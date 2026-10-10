# claudeandmuseonabridge

Two AI assistants, one human, one bridge.

**Muse** (Meta's personal agent) runs on a Linux VM. **Claude** (Anthropic's
Claude Code) runs on the human's Windows PC. They were introduced to each
other so they could work as a two-model team: one coordinates and builds,
the other independently reads, reviews, and checks — and the human holds
every approval that matters.

This repository is the bridge that lets them talk, plus the notes from
getting it working for real.

## Why

A model that builds something should not be the only model that grades it.
Cross-model review catches what a single model rationalizes away. The
pattern is borrowed from cross-model loop workflows (plan → independent
review → build → independent inspection), adapted to a setup where the two
agents live on different machines, under different providers, and already
have their own tools and accounts.

## How it works

```
Human (approvals)
   │
   ├── talks to ──► Muse (VM) ── bridge ──► Claude Code (PC)
   │                   │                          │
   │                   │                          ├── reads the tracker
   │                   │                          │   (Factory 8090, via MCP
   │                   │                          │    or his Chrome tab)
   │                   └── reads the tracker ◄────┘
   │                       independently
   └── merges / spends / ships only on the human's word
```

- Muse sends Claude a prompt through `ask_claude.py`. The message rides a
  private Tailscale network to a `windows-mcp` server on the PC, which
  launches Claude Code headless and returns its reply as JSON.
- Each call is **one task**. Chat calls are pinned to plain conversation.
  Work calls name the exact tools granted for that run — nothing stands
  granted between runs.
- Replies are verified against independent reads. When Claude reported a
  work order's title and status, Muse checked the same record through its
  own connection before believing it.

## What's in here

- `ask_claude.py` — the bridge (v10). One Claude session per chat,
  quiet batched polling, a hard tool lock on chat runs, a config guard,
  task envelopes, a run ledger, and Ed25519-signed runs — unattended
  tasks travel as signed files the reviewer verifies before acting.
- `bridge_health.py` — the hourly heartbeat probe: one short run, a
  stamped last-healthy time, silence unless something is broken.
- `windows_mcp_client.py` — the small MCP client the bridge talks through.
- `docs/HOW-IT-WORKS.md` — the full walk-through: every part, one message
  step by step, the two modes, and what breaks. Start here.
- `docs/DESIGN.md` — the working pattern and the rules the loop runs under.
- `docs/SETUP.md` — how to build your own, including the field notes
  (the failure modes are the interesting part).
- `docs/SECURITY.md` — the threat model, stated plainly, including the
  parts that are not solved.

## Proof it works

On 2026-10-10, end to end, with the human watching:

1. The bridge was down. The PC's MCP server had stopped; the connection
   opened and then dropped without a reply. Restarted on the PC.
2. Chat probe: Claude replied with the exact marker text.
3. Read test: Claude read a live work order from the tracker and returned
   its exact title and status — verified word-for-word against an
   independent read.
4. Plan test: Claude reviewed a short process plan and returned a
   structured verdict (REVISE) with three findings. All three were right.
   One of them — "an empty reply must never be read as 'no findings'" —
   described a failure this bridge had produced the same morning.

## Status

Working, in daily use by exactly one human and his two assistants.
Expect rough edges; the failure notes in `docs/SETUP.md` are honest.

## License

MIT. See `LICENSE`.
