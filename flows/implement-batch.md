# Multi-task implement batch

Loaded only from `flows/implement.md` when the user asked for several
tasks at once. Do **not** read this file for a single-id implement.

## When this applies

- Explicit multi-id: `/dev implement 13, 14, 15` (or space-separated ids).
- **All tasks under an umbrella:** expand that umbrella's `deps` — nested
  umbrellas expand recursively to leaves; the umbrella itself is not a
  batch member unless they named it as a normal id.
- Multiple freeform description matches only when the user clearly asked
  for a batch.

`/dev auto` stays one task per cycle — no auto multi-pick.

Run the steps below **before claiming anything**, then run
`flows/implement.md` steps 0–7 for each id in the planned order.

## Steps

1. **Resolve** every id. Drop duplicates. Unknown id → stop; list what is
   missing. Do not partially claim the set.
2. **Eligibility (whole set).** Load each task. Apply implement.md step 0's
   **state preflight** to every member before claiming any: refuse the
   whole batch if any member is not startable without a special user call
   — `done`, `not-planned`, `proposed`, open `needs: decision`, assigned
   to someone else, or already `review` (resume is single-task, not
   batch). List each ineligible id and why. Soft cases that only need
   confirmation in single-task (`doing`, pointless-looking) still require
   that confirmation up front for those members; without it, refuse the
   batch. Do not claim or implement any member until every remaining id is
   clear to start.
3. **Set-relative blocking.** A dep is unfinished when its status is not
   `done` (a missing id counts as unfinished). An **external blocker** is
   an unfinished dep whose id is **not** in the set. If any selected task
   has an external blocker, refuse the whole batch: for each blocked task,
   list the outside dep id(s) and their status. Do not claim or implement
   any of them. Deps that are already `done`, or that are other members of
   the set, do not block.
4. **Order.** Topological sort on edges *within* the set (A before B when B
   lists A in `deps`). Tie-break: lower id first. Cycle in the set → refuse
   and report the cycle. State the order once (e.g. "implementing as
   13 → 15 → 14") and proceed.
5. **Shared preflight once:** `gh auth`, then the **local integration ahead**
   check from implement.md step 0. For the union of areas across the set:
   **area collision** (`doing`) and **same-area review** as in step 0.
   Warn; on user go-ahead, continue.
6. **Implement sequentially** in that order. For each id, run implement.md
   steps 0–7 (lightweight re-check of state, claim, branch, implement,
   ship). Reuse the shared ahead check if the tree is still clean.
   **Within-set deps:** when claim warns about unfinished deps that are
   earlier members of this batch (typically now `review` after their ship),
   proceed — do not stop for those. Still stop if an unfinished dep is
   outside the set (should not happen after step 3 unless the board moved).
   **Same-area review** for a later member: ignore other tasks that are
   already members of this batch (their PRs are expected); still warn on
   outside same-area `review` work. If a later member becomes unstartable
   mid-batch (board moved), stop; leave already-shipped members in
   `review` and report the rest.
7. **Out of scope for this path:** parallel worktrees or parallel agents for
   the batch unless the user later asks. Each task still gets its own branch
   and PR. Prefer `origin/<integration>` as the base; if a later task truly
   needs unmerged code from an earlier one in the batch, stack on that
   task's branch and note the dependency in the PR body.
