# Setup

You need: a Windows PC running Claude Code, a second machine (the
coordinator's), and a private network path between them. We use Tailscale
and a `windows-mcp` server on the PC.

## Pieces

1. **windows-mcp server on the PC.** Runs as a per-user scheduled task,
   binds only to the PC's Tailscale address, requires a bearer key, and
   keeps an IP allowlist of the coordinator's machine. The key lives in a
   file with tight permissions — never in this repo, never in chat.
2. **The client** (`windows_mcp_client.py`) on the coordinator's machine.
   Set `WINDOWS_MCP_URL` to the PC's MCP endpoint and place the key file
   where the client expects it.
3. **The bridge** (`ask_claude.py`). It writes a small runner script into
   a bridge folder on the PC (`%USERPROFILE%\.muse-bridge`), which starts
   the native `claude.exe` headless with the prompt on stdin and output
   redirected to files. The bridge polls for a done flag and reads the
   result files back. Set `ASK_CLAUDE_PC_USER` to the Windows username.
4. **Claude's tracker access.** Claude reads the work tracker (Factory
   8090) through an MCP server configured in `~/.claude.json`, and also
   works it through his Chrome tab in interactive sessions. The hosted
   MCP endpoint we use is `https://api.factory.8090.dev/v2/mcp`, type
   `http`, with the project's API key in the entry's headers.

## Field notes — the failures are the documentation

- **"Connected, then dropped."** If the MCP connection opens and closes
  without a reply, the PC-side server task has stopped. Restart the
  scheduled task on the PC; nothing on the coordinator's side can fix it.
- **The backslash URL.** Claude's headless 8090 access failed with a
  config error: in one project scope of `~/.claude.json`, the server's
  `url` was a single `\` character and its headers were missing, while
  other scopes were correct. Claude diagnosed it himself and refused to
  guess the data. Fix: correct the URL, restore the headers from a
  working scope **on the PC** (values never leave the machine), back the
  file up first. Note: an interactive Claude session can overwrite
  `~/.claude.json` from memory when it exits — if the fix vanishes,
  this is why.
- **Headless means no tools.** A headless Claude session grants no tool
  permissions by itself. The bridge passes `--allowedTools` with the
  exact tools for that run (v5: `ASK_CLAUDE_ALLOWED_TOOLS`).
- **Empty replies happen.** Roughly one run in several exits cleanly with
  zero bytes of output — more often on long, multi-part prompts. The
  bridge retries once; split big asks into one task per call.
- **Pin chat calls.** Without a pin, a resumed session once treated a
  chat message as a work order and ran off with it. Chat mode appends a
  system prompt: plain text, no tools, no tasks.

## The MCP transport (the default)

The file bridge above works, but each message pays for the file
hand-off and the polling loop — measured here at ~29 seconds of pure
overhead on a reply Claude produced in ~2 seconds. The variant in
`claude_mcp_server.py` removes that layer:

1. Put `claude_mcp_server.py` on the Windows PC and create a bearer
   key file next to it named `mcp_auth_key` (any long random string;
   treat it like a password). The server refuses to start without it.
2. Start it: `python claude_mcp_server.py` (listens on port 8765;
   run it hidden or as a scheduled task for daily use).
3. On the coordinator, set `CLAUDE_MCP_URL=http://<pc-address>:8765`
   and `CLAUDE_MCP_KEY_FILE` to a file holding the same key, then use
   `claude_mcp_client.py`: `ask [--chat ID] [--tools A,B] "prompt"`.
4. If the coordinator reaches the PC through a proxy, the proxy must
   be one that can route to the PC's private address — a proxy that
   cannot will return empty replies while the server is healthy.

Same trust rules as the file bridge: chat calls are hard-locked to
zero tools, tool calls name their tools per call, sessions persist
per chat id on the server.

## Signing key (unattended mode of the file bridge)

```
mkdir -p ~/.config/claude-bridge && chmod 700 ~/.config/claude-bridge
openssl genpkey -algorithm ed25519 -out ~/.config/claude-bridge/signing_key.pem
chmod 600 ~/.config/claude-bridge/signing_key.pem
openssl pkey -in ~/.config/claude-bridge/signing_key.pem -pubout -out ~/.config/claude-bridge/signing_pub.pem
```

Copy `signing_pub.pem` and `verify_sig.py` (a small pure-Python
Ed25519 verifier) to the PC's `.muse-bridge` folder. The private key
never leaves the coordinator. Signatures cover the LF-normalized
text of a message, because file channels may store CRLF endings.

## The PC agent — do not skip it, and do not mistake it for a replacement

`pc_agent_server.py` is the coordinator's own small agent on the PC
(four tools: ping, exec, read_file, write_file). It is **not** a
windows-mcp replacement and does not compete with it: windows-mcp does
desktop control (mouse, keyboard, screenshots); the PC agent does none
of that and windows-mcp stays installed and in use.

The agent exists because windows-mcp runs as a scheduled task that
sometimes stops — and when it stops, a coordinator whose only channel
was windows-mcp has no hands left on the PC at all. The agent is the
independent second channel: it restarts the other pieces (it has
restarted the MCP server and the windows-mcp task in production), reads
logs, and applies config fixes. One of its four tools is full shell
exec, so a tool count is not a capability comparison. An install that
skips the agent has no self-recovery: the first time windows-mcp stops,
a human has to walk to the PC.

## Autostart (learned the hard way)

Nothing above survives a reboot by itself. What works without admin
rights: shortcuts in the user's Startup folder for the PC agent, the
tray icon, and the MCP server — the MCP server in particular has no
other autostart, so without its shortcut it is simply gone after a
reboot and the coordinator finds a dead port. The windows-mcp task is
the odd one out: it does not autostart reliably, which is one more
reason the PC agent (which can start it) needs to be running.
