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
