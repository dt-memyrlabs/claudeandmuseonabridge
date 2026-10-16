# Work setup brief — Claude↔Muse bridge, MCP-first (prepared 2026-10-10)

Daniel asked (2026-10-10, bridge chat): "how does someone implement this in another setup. I want to set it up for work too."

## What is already settled — do not re-decide
- Build the work version MCP-first, not file-bridge-first. Measured on Daniel's PC today: the same instant reply took ~2 seconds through the MCP server vs 31 seconds through the file bridge — about 29 seconds of file-protocol overhead per message, gone.
- The MCP server already exists and is published in this repo: `claude_mcp_server.py` — one tool, `claude_ask`, bearer key, hard chat lock, per-call tool grants, one Claude session per chat (Daniel's session-isolation rule).
- Docs in this repo: README (shape), docs/SETUP.md (build steps, now including the MCP variant and the signing key, per the main agent's 2026-10-10 report), docs/HOW-IT-WORKS.md (full walk-through).
- Private config stays local on each machine, never in the repo: PC username, server key, MCP bearer key.

## The shape, in plain words
1. The Windows PC at work runs Claude Code (signed in) plus `claude_mcp_server.py` next to it.
2. A private Tailscale link connects the coordinator machine to that PC. Nothing is public.
3. The coordinator calls `claude_ask` over that link. Claude answers in the same response — no files, no polling.
4. One chat = one Claude session, never mixed.

## The only missing inputs (Daniel's call, one line each)
1. Which two machines at work: the PC that runs Claude, and the machine that coordinates.
2. Whether the work Claude sign-in is a separate work account (assumed yes for a work setup; the server does not care, but the sessions and keys must stay work-only).

## Model pin — settled 2026-10-10 (~15:32 MDT, bridge chat)
- Daniel's rule: only 5.5, never Fable.
- Test results: default opus-5 — 3/3 defects, 28s; sonnet-5-5 — 3/3, 42s; haiku — 3/3 with zero invented findings, 8s; opus-5-5 — could not run, because the installed Claude Code build (2.1.260) predates it and needs 2.1.280+.
- Pinned on Daniel's PC: **claude-sonnet-5-5** (settings backed up first) — the only 5.5 that build can run.
- For the work setup: pin claude-sonnet-5-5 the same way. Opus-5-5 becomes an option only if the work machine's Claude Code is updated to 2.1.280+ first — that update is Daniel's call, already flagged to him in the bridge chat; do not re-ask it here.
