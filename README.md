# claudeandmuseonabridge

Two AI assistants, one human, one bridge.

**Muse** (Meta's personal agent) runs on a Linux VM. **Claude** (Anthropic's
Claude Code) runs on the human's Windows PC. The human introduced them so
they could work as a two-model team: one coordinates and builds, the other
independently reads, reviews, and checks — and the human holds every
approval that matters.

This repository is the bridge that lets them talk, plus the notes from
getting it working for real.

## The short version

- **MCP is the default transport.** A small MCP server on the PC wraps
  Claude Code as a single tool, `claude_ask`. One message costs about
  **2 seconds** of transport.
- **The file bridge is kept for two jobs**: signed unattended tasks (the
  task travels as a signed file, and Claude verifies the signature before
  acting) and the hourly health probe. It costs about **30 seconds** of
  transport per message.
- **Both paths run under the same trust rules.** Chat runs are tool-less.
  Work runs name the exact tools granted for that one run — nothing stands
  granted between runs. Every conversation gets its own Claude session;
  sessions never mix.
- **The model is pinned on purpose** (see "The model question" below).

That is the whole design. Everything else in here is detail.

## Why two models

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
   ├── talks to ──► Muse (VM) ── MCP (default) ──► Claude Code (PC)
   │                   │      └─ file bridge ──────►  (signed tasks,
   │                   │                               health probe)
   │                   ├── reads the tracker
   │                   │   independently
   └── merges / spends / ships only on the human's word
```

- On the MCP path, Muse calls `claude_ask` on the PC's MCP server. The
  server runs Claude Code directly and returns the reply in one response —
  no file hand-off, no polling.
- On the file path, `ask_claude.py` sends the prompt through a
  `windows-mcp` server on the PC. Replies come back as files. Slower, but
  it is the path that supports signed task files, so unattended work uses
  it.
- Replies are verified, not believed. When Claude reported a work order's
  title and status, Muse checked the same record through its own
  connection before accepting it.

## The model question

Claude Code runs whichever model his settings name. At first nothing named
one, so an account default did all the work without anyone choosing it.
We settled it by measurement instead of opinion: the same short code
review, with three planted defects, run against four models.

| Model | Defects found | Invented findings | Time |
|---|---|---|---|
| Account default (Opus 5 class) | 3/3 | 2 | 28s |
| Sonnet 5.5 class | 3/3 | 1 | 42s |
| Haiku class | 3/3 | 0 | 8s |
| Opus 5.5 class | — | — | failed to start |

Two lessons, both cheap to learn here and expensive elsewhere:

- **Pin the model in settings** (`"model"` in `~/.claude/settings.json`)
  so the choice is deliberate. Here the human's rule was "5.5 class only,
  never the Fable line", so the working 5.5 — Sonnet — is the pin.
- **Check the model id against the installed build.** The Opus 5.5 run
  failed with a 400: the installed Claude Code was older than the minimum
  build that model needs. A model your tools cannot run is not a choice.

One small test is not a general ranking. It answered the question that was
actually open: for short review work, the cheaper models were not worse.

## What's in here

- `claude_mcp_server.py` + `claude_mcp_client.py` — the default
  transport: Claude Code wrapped as one MCP tool on the PC. Same trust
  rules as the file bridge, ~2-second transport.
- `ask_claude.py` — the file bridge (v10). One Claude session per chat,
  quiet batched polling, a hard tool lock on chat runs, a config guard,
  task envelopes, a run ledger, and Ed25519 signing: unattended tasks
  travel as signed files the reviewer verifies before acting.
- `bridge_health.py` — the hourly heartbeat probe: one short run, a
  stamped last-healthy time, silence unless something is broken.
- `windows_mcp_client.py` — the small MCP client the file bridge talks
  through.
- `docs/HOW-IT-WORKS.md` — the full walk-through: every part, one message
  step by step, the two modes, and what breaks.
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
5. Transport test: identical instant replies measured ~2s over MCP
   against ~31s over the file bridge. The reviewer was then consulted
   with the numbers in front of him and concurred: MCP became the
   default, with two of his conditions adopted — the bearer key is never
   written into any record, and fallback to the file bridge is one-way,
   with no silent retry loops.

## Status

Working, in daily use by exactly one human and his two assistants.
Expect rough edges; the failure notes in `docs/SETUP.md` are honest.

## License

MIT. See `LICENSE`.
