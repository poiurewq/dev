# Multi-task review batch

Loaded only from `flows/review.md` when the user asked for several task ids
at once (or "all under umbrella …"). Do **not** load this file for a single
id — a lone PR stays on the single-id path even when open batch co-members
exist (the gate only advises); a missing stack parent refuses there rather
than expanding into this file. Never auto-expand a selection the gate
refused or advised on — surface `batch-gate` output and let the user re-run
with the set they want.

## When this applies

- Explicit multi-id: `/dev review 19, 20, 22` (or space-separated).
- **All tasks under an umbrella** in `review`: expand that umbrella's `deps`
  recursively to leaves; keep only those currently in `review` with a `pr`.
- Single-id whose open **stack parent** is not selected: `batch-gate` exit
  2 — **hard refuse**, list the missing parents; do not review or expand.
- Single-id that is part of a still-open Dev-batch: `batch-gate` **advises**
  (exit 0). Relay it and offer the full set; the user decides.

Non-goals: auto multi-review; auto-expand after a gate refuse or advisory;
pairwise sibling `merge-tree`.

## Philosophy (two phases)

1. **Review all first** — no mid-batch land. Keep the stack/batch alive.
2. **Land only after every member is approved** — then land in stack/topo
   order via `TASKS land` only. Land merges the parent with a merge commit
   and retargets its stacked children to integration; no sibling branch is
   rewritten, so landing one member is inert to the rest.

When a Dev-batch (or stack component) applies, this **supersedes** the
single-review "land one then next" multi-PR path in `flows/review.md`. That
path remains for inbox items with **no** shared `Dev-batch:` and no stack
edges among them.

## Steps

### 0. Resolve + gate

1. Resolve every id (unknown → stop; no partial batch). Drop duplicates.
2. `TASKS batch-gate --ids <all selected>`. Exit 2 → surface the script
   output (missing stack parents) and **stop**. A partial-batch advisory on
   exit 0 is relayed, not enforced — the user picks the set.
3. Load each task: must be `review` with a `pr` (or already `done`/merged —
   skip with a note). Refuse `proposed` / open `needs: decision` in the set
   for this path (handle those via the normal inbox).

### 1. Phase 1 — Review all (no land)

For each member (any order for reading; prefer stack bottom→top for context):

1. Plain sentence of purpose, then `gh pr diff` / review seriously.
2. Present findings + recommendation (approve / request changes) **for the
   whole set together** when possible (one sitting).
3. On **request changes** for member Tᵢ: do **not** land anyone. The author
   fixes on the branch and re-pushes with `TASKS ship <i> --shipped "<what
   changed>"`. Then propagate:
   `TASKS restack --ids <full open set> --after <i>` (fail-closed — a
   conflict aborts that rebase and stops). Earlier plan steps may already be
   force-pushed; that is expected, not a half-open rebase. Resolve the failed
   member (fix conflicts, push), then **re-run the same restack**
   (already-up-to-date members no-op). Do not hand-roll multi-branch rebase
   or try to roll back successful steps. Surface restack output verbatim.
4. On **approve** for a member: record the conversational go-ahead for that
   id; **do not** `TASKS land` yet. The self-approve skip for
   `gh pr review --approve` still applies (flows/review.md).

Repeat until every open member is approved or the user abandons the batch.

### 2. Phase 2 — Ordered land

If `whoami` is not the board integrator, do not enter this phase. Report
approvals and say only the integrator can land.

Order edges = **union of**:

- within-set board `deps` (A before B when B lists A), and
- PR **base→head** edges among the set (child's `baseRefName` is parent's
  branch).

Stack edges often dominate (implement-batch stacks may share only done deps).
Topo-sort; lower id breaks ties. Cycle → stop and report. Same order as
`TASKS restack --ids <full open set> --dry-run` prints.

For each id in order:

1. `TASKS land <id>` only — never hand-roll merge. Land merges the
   parent (merge commit) and retargets its immediate children to
   integration; their branches are untouched, at any stack depth.
   Surface full output; on `error:` stop the land wave (the rest stay
   in `review`).
2. Version intent: for a **Dev-batch**, after the last member of the set
   lands, apply **one** bump sized for the whole set (greatest of the
   stated intents) — not one per PR. If the wave stops early, wait — do
   not bump twice later. A set that is **only** a stack (PR base edges,
   no shared `Dev-batch:`) is not one change set: those tasks merely
   share a branch lineage, so each keeps its own intent and gets its own
   bump as it lands.

### 3. Report

What was approved, restacked, landed, or left open. If `whoami` equals
`TASKS config integrator` and work remains, note it — but don't auto-start
another review cycle in the same breath as implement (implement wrap-up
still prefers a new session; review-batch itself is already the review
session). Do not tell a non-match they are the integrator.
