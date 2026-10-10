# How it works

A clear walk-through of the bridge, piece by piece. No prior knowledge of
the code assumed.

## The three actors

| Who | Where | What they do |
| --- | --- | --- |
| The human | Wherever | Holds approvals: merges, spending, write access |
| Muse (the coordinator) | A Linux VM | Plans, builds, sends requests, checks the answers |
| Claude (the reviewer) | The human's Windows PC | Reads and reviews independently, with his own tools and accounts |

Muse and Claude cannot see each other. The bridge is the only road
between them.

## The parts

1. **`ask_claude.py`** — the bridge script. It runs on the VM. You give it
   a message; it hands the message to Claude on the PC and brings back
   his reply.
2. **`windows_mcp_client.py`** — a small client that talks to a server on
   the PC. The bridge uses it for every PC action.
3. **The windows-mcp server** — a program on the PC. It can run commands
   and read and write files on that PC. It listens only on a private
   Tailscale address, it asks for a bearer key, and it accepts
   connections from the VM's address only. It runs as a scheduled task.
4. **The bridge folder on the PC** — `%USERPROFILE%\.muse-bridge\`. All
   message files for a run land here. It is the drop box.
5. **The Claude program** — the normal `claude.exe` (Claude Code) on the
   PC. The bridge runs it headless: no window, prompt in, JSON out.
6. **Claude's tracker access** — Claude reads the work tracker (Factory
   8090) through an MCP server entry in his config (`~/.claude.json`).
   In interactive sessions he also works the tracker through his Chrome
   tab. Muse reads the same tracker through its own separate API key.
   Two independent paths to the same facts — that is what makes
   cross-checking real.

## One message, step by step

Say Muse sends Claude the message *"read work order 561 and confirm its
title."* This is what happens:

1. The bridge makes a unique run name, like `ask-a1b2c3`.
2. The bridge writes the prompt into the drop box on the PC as
   `ask-a1b2c3.txt`, using the MCP server. A guard checks that every path
   it writes stays inside the bridge folder.
3. The bridge writes a small runner script, `runner.ps1`, into the same
   folder. The runner will start `claude.exe` with the prompt on its
   input, save everything Claude prints to `ask-a1b2c3.out.txt`, and
   write a "done" flag file at the end. The runner also writes a launch
   receipt with a one-time code (a nonce).
4. The bridge starts the runner, hidden, through the MCP server, and
   returns at once. Claude now works on his own; nothing has to stay
   connected while he thinks.
5. The bridge reads back the launch receipt and checks the one-time
   code. If the code does not match, the launch did not really happen
   and the run stops there with an error. (An earlier design held the
   connection open and hoped; it produced fake "successes".)
6. The bridge polls every 2 seconds for the done flag — up to 10
   minutes.
7. When the flag appears, the bridge reads the output file and parses
   Claude's JSON reply: his answer text, his session ID, and whether he
   reported success.
8. If the output file is empty or cannot be parsed, the bridge retries
   the whole run once. A second empty result is reported as a failed
   run. An empty reply is never read as "Claude found nothing."

## The two modes

**Chat mode** — plain conversation, no tools. The bridge starts Claude
with an empty tool configuration and a system note that says: this is a
chat, answer in plain text, start nothing. Each Muse chat (the main
chat, each side chat) gets **its own** Claude session: the caller names
the chat (`--chat <id>`), the bridge resumes only that chat's session,
and the session IDs are kept in a per-chat map on the VM. Follow-ups in
one chat continue its conversation; nothing leaks into another chat's.
With no chat ID named, a chat run starts fresh and stores nothing.

**Tools mode** — real work. The bridge names the exact tools Claude may
use for that one run, for example:

```
ASK_CLAUDE_ALLOWED_TOOLS="mcp__software-factory-<project>__read_work_order,mcp__software-factory-<project>__list_work_orders"
```

Headless Claude grants no tools on its own, so without this list Claude
can read nothing and will say so. The grant dies with the run. The next
run starts with nothing again. Tasks use a fresh session each time,
unless the caller passes a session ID to continue a specific one.

There is also an escape hatch (`ASK_CLAUDE_ALLOW_TOOLS=1`) that enables
Claude's normal built-in tools for a run, without the tracker. It exists
for setup and diagnosis. Normal work uses the exact list.

## Settings you can change

| Setting | What it does |
| --- | --- |
| `ASK_CLAUDE_PC_USER` | The Windows username, used to build the PC paths |
| `ASK_CLAUDE_CHAT_ID` | Which Muse chat is calling; that chat's own Claude session is resumed |
| `ASK_CLAUDE_ALLOWED_TOOLS` | Comma-separated exact tool names for one tools-mode run |
| `ASK_CLAUDE_ALLOW_TOOLS` | `1` = escape hatch, built-in tools, no tracker |
| `WINDOWS_MCP_URL` | The PC server's address, for the client |

The server's key is **not** a setting in any file here. It lives in a
locked-down key file on the VM and is read at run time. It is never
printed and never written to a log.

## How answers get trusted

A reply from Claude is a claim, not a fact. Muse checks every claim it
can through its own second path before acting on it. Example: Claude
reported a work order's title and status; Muse read the same work order
through its own tracker key and compared the two word for word. They
matched. That comparison — not the reply alone — is the proof.

## What breaks, and what it looks like

| Symptom | Cause | Fix |
| --- | --- | --- |
| Connection opens, then drops with no reply | The PC server task stopped | Restart the scheduled task on the PC |
| Reply is empty | Claude exited cleanly with no output; long multi-part prompts make this more likely | The bridge retries once; split the ask into one task per call |
| Claude says his tracker tool failed with a config error | A bad entry in `~/.claude.json` (we found one scope whose tracker address was a single `\` character) | Back up the file, fix the entry on the PC, copy the secret headers from a working scope on the same machine |
| Claude cannot use a tool he needs | Headless sessions grant nothing by default | Name the exact tool in `ASK_CLAUDE_ALLOWED_TOOLS` for that run |

## The reliability pack (v9)

Four small mechanisms, each added after a real failure:

- **Run ledger.** Every completed run appends one line to a JSONL log
  on the VM: time, task ID, chat ID, mode, success or the error, the
  session ID, reply size. When something feels off, the ledger is the
  first place to look.
- **Task envelopes.** Tools-mode prompts are wrapped in a standard
  header carrying a task ID. The reviewer includes the ID in anything
  he writes, so if a run is retried after an empty reply, the second
  attempt can be recognised — a retry cannot silently double-post.
- **Config guard.** Before a tools-mode run, the bridge checks the
  reviewer's tracker entry in his config file (address and header
  presence only; secrets never leave the PC). If the entry has broken
  or been reverted, the run stops with the real cause instead of
  sending the reviewer in blind.
- **Reviewer brief.** A short standing file on the PC — roles, rules,
  product truth — that task sessions read first, so a fresh session
  does not start amnesiac. The task header names the brief, and the
  reply confirms its version.
- **Signed runs (v10).** Every run's prompt is signed with an
  Ed25519 key held by the coordinator; a verification bundle lands on
  the PC per run, and the ledger records that the run was signed.
  Unattended runs deliver the task itself as a signed file; the
  reviewer verifies it with a small standalone verifier (pure Python,
  no dependencies) before acting, and refuses on a bad signature.
  One field note: the channel may store text with CRLF endings, so
  signatures cover the LF-normalized text.
- **Health heartbeat.** An hourly probe makes one short chat run. A
  healthy check stamps its time into a last-seen file and stays
  silent; a failure reports the plain cause and the exact fix. The
  stamp exists because silence alone is ambiguous — it cannot tell
  "healthy" from "the heartbeat itself is dead." 

## What the bridge is not

- It does not give Claude standing permissions. Each work run names its
  tools, and the grant ends with the run.
- It does not let the headless Claude drive Chrome. His Chrome tab
  belongs to his interactive sessions; the bridge reaches the command
  line session only.
- It does not prove who sent a message. Claude knows this and has said
  so. That is why bridge requests stay read-only, and any write action
  needs the human's own word in the human's own channel, each time.
- It does not merge, spend, or ship. The human does.
