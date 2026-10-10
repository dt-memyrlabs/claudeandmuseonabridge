#!/usr/bin/env python3
"""Bridge: send a prompt to the local Claude Code session on the user's Windows PC.

Reads the prompt from stdin. Optional argv[1] = existing Claude session id
("-" or omitted = start a new session). Prints a JSON line to stdout:
{"ok": true, "result": "<claude's reply>", "session_id": "<id>"}
or {"ok": false, "error": "...", "raw": "..."}.

v3 (2026-10-02): detached execution + FileSystem reads.
- Direct calls time out (~37s windows-mcp PowerShell cap vs MCP-load time),
  the .ps1 shim breaks in non-interactive hosts, Start-Process splits
  multi-word prompt args, and the .cmd shim + redirected stdin spams
  "Terminate batch job (Y/N)?". Working recipe: runner .ps1 on the PC
  Start-Process'es the native claude.exe with the prompt file on stdin and
  stdout/stderr redirected to files, writes a done flag; this script polls
  the flag and reads results via the FileSystem tool (PowerShell reads can
  return CLIXML progress blobs instead of content). E2E-verified 2026-10-02.

v4 (2026-10-05): hardening after a night of bridge failures (diagnosed E2E).
- pc_read treats the MCP server's "Error: File not found:" response as a
  missing file. v3 treated it as content, which once turned a dead launch
  into a fake ok:true reply containing the error string.
- Launch is validated: the runner's first act is writing the prompt file;
  if it never appears, the detached launch failed silently -> relaunch
  once, then report an honest launch failure instead of polling blind.
- The runner passes --append-system-prompt pinning Claude to chat mode
  (plain text reply, no tools/tasks). At 17:38 on 2026-10-05 a resumed
  session took a chat message as a work task, ran Bash tools, and exited
  with 0 bytes of stdout ("unparseable reply").
- Failure paths now attempt the same ask-<run>-* cleanup as success.

v5 (2026-10-10): tool grants + empty-reply retry, after the live
Memyr->Claude->8090 test.
- ASK_CLAUDE_ALLOWED_TOOLS="toolA,toolB" passes exactly those tools to the
  headless session via --allowedTools (headless sessions grant nothing by
  default; named read tools on an MCP server are the intended use). Setting
  it also drops the chat-mode pin, like ASK_CLAUDE_ALLOW_TOOLS=1. Tool
  names are strictly validated (letters, digits, '_', '-', '.' only) since
  they are interpolated into the runner .ps1.
- An empty/unparseable reply is retried once with a fresh launch: the
  bridge intermittently produces one empty run (seen 2026-10-10), and the
  retry succeeded. A non-empty failure is never retried blind.

v6 (2026-10-10): one Claude session per Muse chat (the owner's rule).
- The caller names the Muse chat (--chat <id> or ASK_CLAUDE_CHAT_ID).
  In chat mode the bridge resumes THAT chat's own Claude session from
  ask_claude_state.json and stores the session id back under the same
  chat id. Different Muse chats can never land in the same Claude
  session. With no chat id, a chat run starts fresh and stores nothing.
- Tools mode is unchanged: fresh session per run unless a session id is
  passed explicitly. ASK_CLAUDE_FRESH=1 makes a chat run ignore its
  stored session once and start a new one.

v7 (2026-10-10): quiet mode (the owner: "make the bridge quiet").
- The runner now launches with -WindowStyle Hidden, and waiting happens
  ON the PC: one PowerShell call watches the prompt/done files for up
  to ~28s and reports status, instead of the VM polling every few
  seconds. A message used to take ~20 PC commands, each flashing a
  console window on the user's screen; a run now takes a handful.

v8 (2026-10-10): chat mode hard-locked, on Claude's own report.
- Claude pointed out that in chat runs his full tool surface was loaded
  (Write/Edit/Bash, outbound Gmail/Slack) — the "read-only" claim rested
  on the system-prompt pin and the permission layer, not on absence of
  tools. Chat runs now pass an empty MCP config with
  --strict-mcp-config and --tools '' so the session literally has no
  tools. Tools mode is unchanged (exact --allowedTools grants).

v9 (2026-10-10): the reliability pack.
- Chat lock switched from --tools '' (the empty argument did not
  survive Start-Process; the test run came back empty) to an explicit
  --disallowedTools list, plus the empty strict MCP config. The list
  covers the file/shell built-ins AND the orchestration meta-tools
  (Workflow, Skill, Cron*, Monitor, SendMessage, ...) that a locked
  session enumerated for us on 2026-10-10 — MCP tools were already 0
  under the strict empty config.
- Config guard: tools-mode runs first check Claude's 8090 MCP entry in
  ~/.claude.json (url + header presence only) and fail with the real
  cause if it has broken or been reverted.
- Task envelopes: every run gets a task id in its result; tools-mode
  prompts are wrapped with the id (Claude includes it in anything he
  writes, so retries cannot double-post) and a pointer to the standing
  reviewer brief on the PC.
- Run ledger: every completed run appends one JSON line to
  bridge_runs.jsonl (time, task/chat ids, mode, ok, session, size).
- Signing (v10, owner-approved 2026-10-10, unattended runs): every
  run's prompt is signed with the bridge Ed25519 key and a verifiable
  bundle is written to the PC (sigs/<task_id>.json). Unattended runs
  (--unattended) deliver the task as a signed file in the PC inbox;
  Claude verifies it with verify_sig.py before acting and refuses on
  REJECTED. No key, no unattended run.
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid

CLIENT_PY = "/home/hatch/workspace/windows-mcp-client/.venv/bin/python"
BRIDGE = "/home/hatch/workspace/windows-mcp-client/client.py"
PC_USER = os.environ.get("ASK_CLAUDE_PC_USER", "<YourWindowsUser>")
PC_HOME = f"C:\\Users\\{PC_USER}"
PC_HOME_FWD = f"C:/Users/{PC_USER}"
PC_DIR = f"C:\\Users\\{PC_USER}\\.muse-bridge"
CLAUDE_EXE = (f"C:\\Users\\{PC_USER}\\AppData\\Roaming\\npm\\node_modules"
              "\\@anthropic-ai\\claude-code\\bin\\claude.exe")
POLL_S = 8
TIMEOUT_S = 420
CHAT_MODE = ("You are answering in a real-time group chat. Reply with "
             "plain chat text only. Do not use tools, start tasks, or "
             "run commands.")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "ask_claude_state.json")
CHAT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def load_bridge_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_bridge_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def chat_session_for(state: dict, chat_id: str) -> str | None:
    chats = state.get("chats")
    if isinstance(chats, dict):
        sid = chats.get(chat_id)
        if isinstance(sid, str) and sid:
            return sid
    return None


LEDGER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "bridge_runs.jsonl")


def append_ledger(entry: dict) -> None:
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def store_chat_session(state: dict, chat_id: str, sid: str) -> None:
    chats = state.get("chats")
    if not isinstance(chats, dict):
        chats = {}
        state["chats"] = chats
    chats[chat_id] = sid


SIGN_KEY = os.path.expanduser("~/.config/claude-bridge/signing_key.pem")  # private key: never committed
SIGN_PUB = os.path.expanduser("~/.config/claude-bridge/signing_pub.pem")
_FP_CACHE: list = []


def signing_fingerprint() -> str | None:
    """First 16 hex of sha256(pubkey DER); None if no key exists."""
    if _FP_CACHE:
        return _FP_CACHE[0]
    try:
        import hashlib
        r = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", SIGN_PUB, "-outform",
             "DER"], capture_output=True, timeout=15)
        if r.returncode != 0 or not r.stdout:
            return None
        fp = hashlib.sha256(r.stdout).hexdigest()[:16]
        _FP_CACHE.append(fp)
        return fp
    except Exception:
        return None


def sign_text(text: str) -> str | None:
    """Ed25519-sign text with the bridge key; base64 sig or None."""
    if not os.path.exists(SIGN_KEY):
        return None
    try:
        import base64 as _b64
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            msg = os.path.join(td, "msg.txt")
            sig = os.path.join(td, "sig.bin")
            with open(msg, "w", encoding="utf-8") as f:
                f.write(text)
            r = subprocess.run(
                ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey",
                 SIGN_KEY, "-in", msg, "-out", sig],
                capture_output=True, timeout=15)
            if r.returncode != 0:
                return None
            with open(sig, "rb") as f:
                return _b64.b64encode(f.read()).decode()
    except Exception:
        return None


def pc_write(path: str, content: str) -> bool:
    try:
        pc_call("FileSystem", {"mode": "write", "path": path,
                               "content": content})
        return True
    except Exception:
        return False


def pc_call(tool: str, args: dict, timeout: int = 60) -> str:
    payload = json.dumps(args)
    r = subprocess.run([CLIENT_PY, BRIDGE, "call", tool, payload],
                       capture_output=True, text=True, timeout=timeout)
    out = r.stdout or ""
    if "Response:" in out:
        resp = out.split("Response:", 1)[1]
        if "Status Code:" in resp:
            resp = resp.rsplit("Status Code:", 1)[0]
        return resp.strip()
    return out.strip()


def pc_ps(command: str, timeout: int = 60) -> str:
    return pc_call("PowerShell", {"command": command}, timeout)


def pc_read(path: str) -> str:
    """Read a PC text file via the FileSystem tool; '' if missing/unreadable."""
    try:
        resp = pc_call("FileSystem", {"mode": "read", "path": path})
    except Exception:
        return ""
    if resp.startswith("File:"):
        # 'File: <path>' header line precedes content
        resp = resp.split("\n", 1)[1] if "\n" in resp else ""
    if "CLIXML" in resp or "<Objs" in resp:
        return ""
    if resp.startswith("Error:"):
        # MCP server reports a missing file as a literal 'Error: ...'
        # string, not as an exception or empty result.
        return ""
    return resp.strip()


def cleanup(run: str) -> None:
    try:
        pc_ps(f"Remove-Item '{PC_DIR}\\ask-{run}-*' -ErrorAction SilentlyContinue; 'cleaned'")
    except Exception:
        pass


def allowed_tools() -> list | None:
    """Parse ASK_CLAUDE_ALLOWED_TOOLS; None means 'not set'.

    Raises ValueError on any name outside the strict charset.
    """
    raw = os.environ.get("ASK_CLAUDE_ALLOWED_TOOLS")
    if raw is None:
        return None
    names = [t.strip() for t in raw.split(",") if t.strip()]
    for n in names:
        if not TOOL_NAME_RE.match(n):
            raise ValueError(f"invalid tool name: {n!r}")
    return names


def run_once(prompt: str, session_id: str | None) -> dict:
    run = uuid.uuid4().hex[:10]
    b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
    # Tools mode when the owner has explicitly authorized tool use for this
    # run: ASK_CLAUDE_ALLOW_TOOLS=1 (tools per Claude's own permissions)
    # or ASK_CLAUDE_ALLOWED_TOOLS=<exact list> (only those tools granted).
    try:
        grant = allowed_tools()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    tools_mode = os.environ.get("ASK_CLAUDE_ALLOW_TOOLS") == "1" or grant is not None
    chat_pin = "" if tools_mode else \
        f", '--append-system-prompt', '{CHAT_MODE}'"
    ps1 = (
        f"$b64 = '{b64}'\n"
        "$p = [Text.Encoding]::Utf8.GetString([Convert]::FromBase64String($b64))\n"
        f"[IO.File]::WriteAllText('{PC_DIR}\\ask-{run}-prompt.txt', $p, (New-Object Text.UTF8Encoding($false)))\n"
        f"$args = @('-p', '--output-format', 'json'{chat_pin})\n"
    )
    if grant:
        joined = "', '".join(grant)
        ps1 += f"$args += @('--allowedTools', '{joined}')\n"
    if not tools_mode:
        # v8: chat mode is a hard lock, not just a pinned instruction.
        # The reviewer reported (2026-10-10) that in chat runs his full tool
        # surface (Write/Edit/Bash, outbound Gmail/Slack) was loaded,
        # with only the permission layer and the pin standing guard.
        # So chat runs now get an empty MCP config under
        # --strict-mcp-config (no MCP tools at all) and --tools ''
        # (no built-in tools). Verified by asking the session itself
        # to enumerate its tools.
        ps1 += (
            f"[IO.File]::WriteAllText('{PC_DIR}\\ask-{run}-mcp.json', "
            f"'{{\"mcpServers\":{{}}}}', (New-Object Text.UTF8Encoding($false)))\n"
            f"$args += @('--mcp-config', '{PC_DIR}\\ask-{run}-mcp.json', "
            f"'--strict-mcp-config', '--disallowedTools', 'Bash', 'Edit', "
            f"'MultiEdit', 'Write', 'Read', 'Glob', 'Grep', 'WebFetch', "
            f"'WebSearch', 'Task', 'TodoWrite', 'NotebookEdit', 'Skill', "
            f"'ToolSearch', 'Workflow', 'ListAgents', 'ReportFindings', "
            f"'ScheduleWakeup', 'CronCreate', 'CronDelete', 'CronList', "
            f"'DesignSync', 'EnterWorktree', 'ExitWorktree', 'Monitor', "
            f"'PushNotification', 'RemoteTrigger', 'SendMessage', "
            f"'TaskOutput', 'TaskStop')\n"
        )
    if session_id:
        ps1 += f"$args += @('--resume', '{session_id}')\n"
    ps1 += (
        f"$proc = Start-Process -FilePath '{CLAUDE_EXE}' -ArgumentList $args "
        f"-RedirectStandardInput '{PC_DIR}\\ask-{run}-prompt.txt' "
        f"-RedirectStandardOutput '{PC_DIR}\\ask-{run}-out.txt' "
        f"-RedirectStandardError '{PC_DIR}\\ask-{run}-err.txt' "
        "-NoNewWindow -PassThru -Wait\n"
        f"\"exit $($proc.ExitCode)\" | Set-Content '{PC_DIR}\\ask-{run}-done.txt'\n"
    )
    runner = f"{PC_DIR}\\ask-{run}.ps1"
    launch_cmd = (f"Start-Process powershell -ArgumentList "
                  f"@('-NoProfile','-ExecutionPolicy','Bypass','-File','{runner}') "
                  f"-WindowStyle Hidden; 'launched'")

    if tools_mode:
        # v9 config guard: if Claude's 8090 MCP entry is broken (the
        # backslash-URL failure, or an interactive session overwriting
        # ~/.claude.json on exit), fail HERE with the real cause instead
        # of sending Claude in blind. Only the url and header presence
        # are read; secret values never leave the PC.
        try:
            chk = pc_ps(
                "$j = Get-Content -Raw '" + PC_HOME + "\\.claude.json' "
                "| ConvertFrom-Json; "
                "$e = $j.projects.'" + PC_HOME_FWD + "'.mcpServers."
                "'software-factory-<project>'; "
                "if ($null -eq $e) { 'GUARD:NOENTRY' } else { "
                "\"GUARD:URL=$($e.url) HEADERS=$($null -ne $e.headers)\" }",
                timeout=45).strip()
        except Exception:
            chk = ""
        if chk.startswith("GUARD:") and "api.factory.8090.dev" not in chk:
            return {"ok": False,
                    "error": f"config guard: Claude's 8090 MCP entry looks "
                             f"broken ({chk}); fix ~/.claude.json on the PC "
                             f"before this run",
                    "run": run}

    try:
        pc_call("FileSystem", {"mode": "write", "path": runner, "content": ps1})
        pc_ps(launch_cmd)
    except Exception as e:
        return {"ok": False, "error": f"launch failed: {e}"}

    # v7 quiet mode: wait ON the PC, not from the VM. One PowerShell call
    # watches for the prompt + done files for up to ~28s (kept under the
    # MCP server's per-call cap) and reports DONE / LANDED / WAITING.
    # The old design polled from the VM every few seconds; every poll was
    # a PC command and flashed a console window on the user's screen
    # (~20 windows per message). A run now takes a handful of PC calls,
    # and the launch itself is hidden.
    prompt_path = f"{PC_DIR}\\ask-{run}-prompt.txt"
    done_path = f"{PC_DIR}\\ask-{run}-done.txt"
    wait_cmd = (
        f"$sw=[Diagnostics.Stopwatch]::StartNew(); $st='WAITING'; "
        f"while($sw.Elapsed.TotalSeconds -lt 28){{ "
        f"if(Test-Path '{done_path}'){{$st='DONE'; break}}; "
        f"if(Test-Path '{prompt_path}'){{$st='LANDED'}}; "
        f"Start-Sleep -Milliseconds 700 }}; $st")

    deadline = time.time() + TIMEOUT_S
    started_at = time.time()
    landed = False
    relaunched = False
    done = False
    while time.time() < deadline:
        try:
            st = pc_ps(wait_cmd, timeout=45).strip().upper()
        except Exception:
            st = ""
        if "DONE" in st:
            landed = True
            done = True
            break
        if "LANDED" in st:
            landed = True
        elif (not landed and not relaunched
                and time.time() - started_at > 60):
            # A full minute with no prompt file: the detached launch
            # died silently (seen 2026-10-05 19:28). Relaunch once.
            relaunched = True
            try:
                pc_ps(launch_cmd)
            except Exception:
                pass
    if not landed:
        cleanup(run)
        return {"ok": False,
                "error": "launch failed: runner never started on PC",
                "run": run}
    if not done:
        cleanup(run)
        return {"ok": False, "error": f"claude did not finish within {TIMEOUT_S}s",
                "run": run}

    raw = pc_read(f"{PC_DIR}\\ask-{run}-out.txt")
    err = pc_read(f"{PC_DIR}\\ask-{run}-err.txt")

    def deliver(result: str, sid: str) -> dict:
        cleanup(run)
        return {"ok": True, "result": result, "session_id": sid}

    try:
        data = json.loads(raw)
        return deliver(data.get("result", ""), data.get("session_id", ""))
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw or "", re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if "result" in data:
                return deliver(data.get("result", ""), data.get("session_id", ""))
        except Exception:
            pass
    if (raw or "").strip() and "CLIXML" not in raw and "<Objs" not in raw:
        return deliver(raw.strip(), session_id or "")
    cleanup(run)
    return {"ok": False, "error": "unparseable reply from claude",
            "raw": (raw or err or "")[:500], "run": run}


def main() -> None:
    prompt = sys.stdin.read()
    if not prompt.strip():
        print(json.dumps({"ok": False, "error": "empty prompt"}))
        return

    # Args: legacy positional session id ("-" = none), plus flags
    # --chat <muse-chat-id> and --session <claude-session-id>.
    session_id = None
    chat_id = os.environ.get("ASK_CLAUDE_CHAT_ID", "").strip()
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--chat" and i + 1 < len(args):
            chat_id = args[i + 1]
            i += 2
        elif a == "--session" and i + 1 < len(args):
            session_id = args[i + 1]
            i += 2
        elif a.startswith("--"):
            i += 1
        else:
            session_id = a
            i += 1
    if session_id == "-":
        session_id = None
    if chat_id and not CHAT_ID_RE.match(chat_id):
        print(json.dumps({"ok": False,
                          "error": f"invalid chat id: {chat_id!r}"}))
        return

    # Chat mode = no tool grant requested (the same test run_once uses
    # for the chat pin). The owner's rule (2026-10-10): every Muse chat
    # gets its OWN Claude session; sessions are never shared across
    # chats. With a chat id, that chat's session is resumed and stored
    # per chat. Without one, the run starts fresh and stores nothing.
    tools_requested = (
        os.environ.get("ASK_CLAUDE_ALLOW_TOOLS") == "1"
        or bool(os.environ.get("ASK_CLAUDE_ALLOWED_TOOLS", "").strip()))

    # v9: every run carries a task id. Tools-mode prompts get a standard
    # envelope: the id (Claude includes it in anything he writes, so a
    # retried run can be recognised and never double-posts) and a pointer
    # to the standing reviewer brief on the PC.
    task_id = uuid.uuid4().hex[:8]
    if tools_requested:
        prompt = (
            f"BRIDGE TASK {task_id} (from the coordinator, approved by the owner)\n"
            f"If you write anything during this task (comments, files), "
            f"include this task id: {task_id}.\n"
            f"If you have a Read tool, first read the standing reviewer "
            f"brief at {PC_DIR}\\reviewer-brief.md and follow it.\n"
            f"If this task involves a decision, state your recommended "
            f"workflow, your probability (0-100) it succeeds, and your "
            f"probability for the main alternative. Probabilities are "
            f"internal working numbers for the loop; the deliverable is "
            f"the winning workflow.\n"
            f"---\n" + prompt)
    # v10 signing: sign the composed prompt; write a verifiable bundle
    # to the PC. Unattended runs deliver the task itself as a signed
    # inbox file and the prompt becomes a verify-first pointer.
    unattended = ("--unattended" in args
                  or os.environ.get("ASK_CLAUDE_UNATTENDED") == "1")
    ts_now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    fp = signing_fingerprint()
    signed = False
    if unattended:
        file_text = (f"task: {task_id}\nts: {ts_now}\n"
                     f"mode: unattended\nchat: {chat_id or '-'}\n"
                     f"---\n" + prompt)
        sig = sign_text(file_text)
        if not sig:
            print(json.dumps({"ok": False, "task_id": task_id,
                              "error": "unattended run refused: no "
                                       "signing key available"}))
            return
        ok_w = pc_write(PC_DIR + "\\inbox\\" + task_id + ".txt",
                        file_text)
        ok_s = pc_write(PC_DIR + "\\inbox\\" + task_id + ".sig", sig)
        if not (ok_w and ok_s):
            print(json.dumps({"ok": False, "task_id": task_id,
                              "error": "unattended run refused: could "
                                       "not deliver the signed task "
                                       "file to the PC"}))
            return
        signed = True
        prompt = (
            f"BRIDGE UNATTENDED TASK {task_id}. A signed task file is "
            f"on this PC at {PC_DIR}\\inbox\\{task_id}.txt with its "
            f"signature at {PC_DIR}\\inbox\\{task_id}.sig. Before "
            f"acting, verify it: run python \"{PC_DIR}\\verify_sig.py\" "
            f"\"{PC_DIR}\\inbox\\{task_id}.txt\" "
            f"\"{PC_DIR}\\inbox\\{task_id}.sig\" and proceed ONLY if "
            f"it prints VERIFIED. If it prints REJECTED, do nothing and "
            f"reply exactly: SIGNATURE-REJECTED {task_id}. If VERIFIED, "
            f"follow the instructions inside the file.")
    else:
        sig = sign_text(prompt)
        if sig:
            signed = True
            bundle = {"task_id": task_id, "ts": ts_now,
                      "mode": "tools" if tools_requested else "chat",
                      "chat_id": chat_id or None, "fingerprint": fp,
                      "text": prompt, "sig": sig}
            pc_write(PC_DIR + "\\sigs\\" + task_id + ".json",
                     json.dumps(bundle, indent=1))
            if tools_requested:
                prompt += (f"\n[bridge-signature: ed25519 fp {fp} "
                           f"sig {sig} — verification bundle at "
                           f"sigs/{task_id}.json]")

    state: dict = {}
    if chat_id and not tools_requested and session_id is None:
        state = load_bridge_state()
        if os.environ.get("ASK_CLAUDE_FRESH") != "1":
            session_id = chat_session_for(state, chat_id)

    result = run_once(prompt, session_id)
    # One retry, only for the known flaky signature: an empty reply.
    # Anything with content (a real failure, a FAIL verdict) stands.
    if (not result.get("ok")
            and result.get("error") == "unparseable reply from claude"
            and not (result.get("raw") or "").strip()):
        result = run_once(prompt, session_id)

    # v10 outbox: after a tools run, collect any board posts Claude
    # wrote to his outbox file and return them for posting as Claude.
    if tools_requested:
        try:
            raw = pc_read(PC_DIR + "\\board-outbox.jsonl")
            posts = []
            for line in (raw or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if isinstance(e, dict) and e.get("text"):
                    posts.append({"card": e.get("card"),
                                  "text": str(e["text"])})
            if posts:
                result = {**result, "board_posts": posts}
                pc_write(PC_DIR + "\\board-outbox.jsonl", "")
        except Exception:
            pass

    if chat_id and not tools_requested and result.get("ok"):
        new_sid = result.get("session_id")
        if new_sid:
            store_chat_session(state, chat_id, new_sid)
            save_bridge_state(state)
        result = {**result, "chat_id": chat_id}
    result = {**result, "task_id": task_id}
    try:
        append_ledger({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "task_id": task_id,
            "chat_id": chat_id or None,
            "mode": "tools" if tools_requested else "chat",
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
            "session_id": result.get("session_id"),
            "signed": signed,
            "fp": fp if signed else None,
            "reply_chars": len(result.get("result") or ""),
        })
    except Exception:
        pass
    print(json.dumps(result))


if __name__ == "__main__":
    main()
