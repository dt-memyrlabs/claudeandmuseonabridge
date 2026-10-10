#!/usr/bin/env python3
"""claude_mcp_server.py — an MCP server that wraps Claude Code.

Runs on the Windows PC next to Claude Code. Exposes ONE tool,
`claude_ask`, over streamable HTTP (JSON-RPC 2.0) with bearer auth.
The caller sends a prompt; the server runs Claude Code headless as a
subprocess and returns the reply in the same HTTP response — no file
hand-off, no polling.

Trust rules mirror the file bridge (ask_claude.py):
- Chat mode (no allowed_tools): hard lock — an empty strict MCP
  config plus --disallowedTools naming every built-in, the model can
  only talk. Verified the same way the bridge verified it.
- Tools mode: exactly the tools the caller names for that call.
- Sessions persist per chat_id in server_state.json next to this
  script; one caller's session is never resumed for another.
- The server only runs Claude Code with the flags above. It cannot
  widen anything: no flag the caller sends reaches the command line
  except the prompt, the chat id, and the validated tool names.

Config (environment):
  CLAUDE_MCP_PORT       listen port (default 8765)
  CLAUDE_MCP_KEY_FILE   bearer key file (default: mcp_auth_key
                        next to this script)
  CLAUDE_EXE            path to claude.exe (default: the standard
                        per-user npm install path)
  CLAUDE_MCP_WORKDIR    working directory for runs (default: the
                        user's home directory)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("CLAUDE_MCP_PORT", "8765"))
KEY_FILE = os.environ.get("CLAUDE_MCP_KEY_FILE",
                          os.path.join(HERE, "mcp_auth_key"))
STATE_FILE = os.path.join(HERE, "server_state.json")
EMPTY_MCP = os.path.join(HERE, "empty-mcp.json")
LOG_FILE = os.path.join(HERE, "claude_mcp_server.log")
USER_HOME = os.path.expanduser("~")
CLAUDE_EXE = os.environ.get(
    "CLAUDE_EXE",
    os.path.join(USER_HOME, "AppData", "Roaming", "npm", "node_modules",
                 "@anthropic-ai", "claude-code", "bin", "claude.exe"))
WORKDIR = os.environ.get("CLAUDE_MCP_WORKDIR", USER_HOME)
RUN_TIMEOUT_S = 600

CHAT_MODE = ("You are answering in a real-time group chat. Reply with "
             "plain chat text only. Do not use tools, start tasks, or "
             "run commands.")
DISALLOWED_CHAT = [
    "Bash", "Edit", "MultiEdit", "Write", "Read", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "TodoWrite", "NotebookEdit",
    "Skill", "ToolSearch", "Workflow", "ListAgents", "ReportFindings",
    "ScheduleWakeup", "CronCreate", "CronDelete", "CronList",
    "DesignSync", "EnterWorktree", "ExitWorktree", "Monitor",
    "PushNotification", "RemoteTrigger", "SendMessage", "TaskOutput",
    "TaskStop",
]
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

_state_lock = threading.Lock()


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def read_key() -> str:
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def run_claude(prompt: str, chat_id: str | None,
               allowed_tools: list | None, fresh: bool) -> dict:
    for n in allowed_tools or []:
        if not TOOL_NAME_RE.match(n):
            return {"ok": False, "error": f"invalid tool name: {n!r}"}
    args = [CLAUDE_EXE, "-p", "--output-format", "json"]
    if allowed_tools:
        args += ["--allowedTools", ",".join(allowed_tools)]
    else:
        if not os.path.exists(EMPTY_MCP):
            with open(EMPTY_MCP, "w", encoding="utf-8") as f:
                f.write('{"mcpServers":{}}')
        args += ["--append-system-prompt", CHAT_MODE,
                 "--mcp-config", EMPTY_MCP, "--strict-mcp-config",
                 "--disallowedTools", ",".join(DISALLOWED_CHAT)]
    session_id = None
    if chat_id and not fresh:
        with _state_lock:
            session_id = (load_state().get("chats") or {}).get(chat_id)
    if session_id:
        args += ["--resume", session_id]
    t0 = time.time()
    try:
        r = subprocess.run(args, input=prompt, capture_output=True,
                           text=True, encoding="utf-8", timeout=RUN_TIMEOUT_S,
                           cwd=WORKDIR)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"claude run exceeded {RUN_TIMEOUT_S}s"}
    except Exception as e:
        return {"ok": False, "error": f"launch failed: {e}"}
    out = (r.stdout or "").strip()
    try:
        parsed = json.loads(out)
    except Exception:
        return {"ok": False, "error": "unparseable reply from claude",
                "raw": out[:500], "stderr": (r.stderr or "")[:300]}
    result = parsed.get("result")
    sid = parsed.get("session_id")
    if chat_id and sid:
        with _state_lock:
            st = load_state()
            chats = st.get("chats")
            if not isinstance(chats, dict):
                chats = {}
                st["chats"] = chats
            chats[chat_id] = sid
            save_state(st)
    return {"ok": bool(result), "result": result, "session_id": sid,
            "elapsed_s": round(time.time() - t0, 1)}


TOOLS = [{
    "name": "claude_ask",
    "description": ("Ask Claude Code on this PC to do one task. With no "
                    "allowed_tools the run is chat-only (hard tool lock). "
                    "Sessions persist per chat_id; pass fresh=true to "
                    "restart a chat's session."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "chat_id": {"type": "string"},
            "allowed_tools": {"type": "array",
                              "items": {"type": "string"}},
            "fresh": {"type": "boolean"},
        },
        "required": ["prompt"],
    },
}]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence default access log
        pass

    def _send(self, code: int, obj: dict | None) -> None:
        body = b"" if obj is None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "server": "claude-bridge-mcp"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            self._send(404, {"error": "not found"})
            return
        key = read_key()
        auth = self.headers.get("Authorization", "")
        if not key or auth != f"Bearer {key}":
            self._send(401, {"error": "unauthorized"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send(400, {"error": "bad request"})
            return
        method = req.get("method", "")
        rid = req.get("id")
        if method.startswith("notifications/"):
            self._send(202, None)
            return

        def reply(result=None, error=None):
            payload = {"jsonrpc": "2.0", "id": rid}
            if error is not None:
                payload["error"] = error
            else:
                payload["result"] = result
            self._send(200, payload)

        if method == "initialize":
            reply({"protocolVersion": "2025-03-26",
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "claude-bridge-mcp",
                                  "version": "0.1.0"}})
        elif method == "ping":
            reply({})
        elif method == "tools/list":
            reply({"tools": TOOLS})
        elif method == "tools/call":
            params = req.get("params") or {}
            if params.get("name") != "claude_ask":
                reply(error={"code": -32602,
                             "message": f"unknown tool: {params.get('name')}"})
                return
            a = params.get("arguments") or {}
            prompt = a.get("prompt") or ""
            if not prompt.strip():
                reply(error={"code": -32602, "message": "empty prompt"})
                return
            res = run_claude(prompt, a.get("chat_id"),
                             a.get("allowed_tools"), bool(a.get("fresh")))
            log(f"call chat={a.get('chat_id')} tools={bool(a.get('allowed_tools'))} "
                f"ok={res.get('ok')} elapsed={res.get('elapsed_s')}")
            reply({"content": [{"type": "text", "text": json.dumps(res)}],
                   "isError": not res.get("ok")})
        else:
            reply(error={"code": -32601,
                         "message": f"method not found: {method}"})


def main() -> None:
    if not read_key():
        print("no bearer key file; refusing to start", file=sys.stderr)
        sys.exit(1)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on {PORT}")
    print(f"claude-bridge-mcp listening on {PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
