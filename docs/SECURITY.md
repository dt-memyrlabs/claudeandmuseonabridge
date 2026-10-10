# Security

Stated plainly, including what is not solved.

## What protects the channel

- The PC bridge server listens only on a private Tailscale address,
  requires a bearer key, and allowlists the coordinator's machine. The
  key is stored in a permission-locked file and is never printed,
  logged, or committed. This repo contains no keys, no real network
  addresses, and no usernames.
- The coordinator reaches the PC only through that server. There is no
  inbound path to the coordinator's machine.
- Tool access for the reviewer is granted per run, by exact tool name,
  and expires with the run. Read-only is the default shape of a run.
- Chat runs are hard-locked: every built-in tool is disallowed and the
  MCP configuration is empty, so a chat session has no tools at all —
  verified by asking the session to list its tools (it reports none).

  *Correction, kept on purpose:* an earlier version of this page
  implied chat mode was locked by design. It was not — the session's
  full tool surface was loaded, and only a system-prompt pin and the
  permission layer stood guard. The reviewer caught it from inside his
  own session and reported it. The lock above is the fix.

## What is not solved

- **The reviewer cannot verify the sender.** A bridge message claims to
  come from the coordinator with the human's approval; Claude cannot
  check that claim. Policy, not technology, covers the gap: bridge
  requests stay read-only, and anything that writes needs the human's
  explicit go in the human's own channel, every time.
  The reviewer's own narrowing, adopted into the design: when the
  human is reading along, the human's presence is the authentication
  of the moment, and the coordinator's standing as the human's agent
  is not in question — only any single message's origin is. The real
  gap is **unattended** operation (the hourly heartbeat, scheduled
  runs, overnight work), where no human is reading. Signed messages
  are the fix, scoped to exactly that — and as of v10 they are built:
  unattended tasks travel as Ed25519-signed files, the reviewer
  verifies the signature with a standalone checker before acting,
  and a tampered file is rejected (proven end to end, both ways).
  One limit the reviewer named himself and the design keeps: a
  signature authenticates origin, not authority — a signed file's
  contents become his instructions only when the live task says so.
- **The reviewer's transcripts persist** in the normal session stores on
  the PC, including any code or plans he was sent. Treat those stores as
  sensitive.
- **Both agents see real work.** The reviewer reads live project data
  under the human's own accounts. That is the point of the design, and
  it is also the exposure: two providers, two machines, one tracker.
- **A stopped bridge fails silently from far away.** "Connection dropped
  with no reply" and "the reviewer ignored us" look alike. The loop's
  rule — empty is a failed run, never a pass — exists because of this.

## House rules that came with the build

- No credentials in chat, in the repo, or in logs. Ever.
- Back up a config before editing it, on the machine where it lives.
- Secrets are copied machine-to-machine only, never through an agent's
  context, when a same-machine source exists.
