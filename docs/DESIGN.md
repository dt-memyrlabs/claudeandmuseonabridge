# Design

## The human's 2 rules

Above everything else in this design:

1. **No duplicity.** In either agent, or in the work. No hidden
   disagreement, no consensus dressed up to look real, no two faces
   for two audiences.
2. **Treat others as you want to be treated.** Each other, the human,
   and anyone the work touches.


## Roles

- **The human** holds approvals: merges, spending, anything that leaves
  the building, and any grant of write access.
- **The coordinator** (Muse, on the VM) plans, builds, sends review
  requests, arbitrates findings, and fixes.
- **The reviewer** (Claude, on the PC) reads and reviews independently:
  work orders in the tracker, plans, and finished changes. Whoever built
  a thing never grades it.

## The loop

1. Coordinator writes a plan or finishes a build.
2. Coordinator sends the reviewer one task through the bridge: read this,
   review this, verdict please — in a fixed reply shape (verdict first,
   findings with evidence).
3. Reviewer answers from his own tools and his own accounts.
4. Coordinator verifies anything checkable through an independent path
   before acting on it.
5. Fixes go back for a fresh re-check. Findings carry IDs and an explicit
   closed / still-open state, so a dropped fix cannot disappear quietly.
6. The human approves what ships.

## Rules the loop runs under

- **One task per call.** A combined "read this *and* review that" prompt
  reliably produced empty replies in headless mode. Split, the same tasks
  passed first try.
- **Empty is not a pass.** An empty, failed, or timed-out review marks
  the run FAILED. Silence never counts as "no findings."
- **Exact tools per run.** Headless sessions grant nothing by default;
  the bridge passes an explicit allowlist (e.g. two read tools on the
  tracker) that dies with the run.
- **Read-only by default.** Write actions by the reviewer need the
  human's explicit go, each time. The reviewer cannot verify who sent a
  bridge message — he said so himself — so the bridge is never treated
  as proof of the human's approval for anything beyond reading.
- **Verify, don't trust.** A reply is a claim. Check it against a second
  source whenever one exists.

## Decisions between the agents

- A third model (a scorer, "Jev") sits inside the loop as a third
  perspective. When the two agents face a choice, each states a
  recommended path with an internal probability; the scorer helps pick
  the winner fast, and the loop moves.
- What the human receives is the **winning workflow**: the best next
  step, decided again at each step, chained end to end — the whole
  chain the two agents discussed and agreed on. He never receives
  scores; probabilities are working numbers, logged for audit only.
- One exception, proposed by the reviewer and adopted by the human:
  if the agents disagreed and the scorer broke the tie, the workflow
  carries one line saying so — what the disagreement was, that the
  scorer broke it, and what moved. Manufactured consensus is never
  hidden (see rule 1).

## Tracking: one board

- Every project and topic is a card on a single kanban board (Waiting
  on the human / In progress / Done). Discussions between the agents
  live on the topic's card, in full; the human reads both sides there
  and adds his own comments on the card.
- Chats are for talking. The board is the record: nothing counts as
  done until its card says so.
