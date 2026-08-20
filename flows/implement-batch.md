# Multi-task implement batch

Loaded only from `flows/implement.md` when the user asked for several tasks
at once. Do **not** read this file for a single-id implement.

## When this applies

- Explicit multi-id: `/dev implement 13, 14, 15` (or space-separated ids).
- **All tasks under an umbrella:** expand that umbrella's `deps` — nested
  umbrellas expand recursively to leaves; the umbrella itself is not a batch
  member unless the user named it as a normal id.
- Multiple freeform description matches, only when the user clearly asked for
  a batch.

`/dev auto` stays one task per cycle — no auto multi-pick.

Run the steps below **before claiming anything**, then run
`flows/implement.md` steps 0–7 for each id in the planned order.

## Steps

1. **Resolve** every id. Drop duplicates. Unknown id → stop and list what is
   missing; do not partially claim the set.
2. **Eligibility (whole set).** Load each task and apply implement.md step
   0's **state preflight** to every member before claiming any. Refuse the
   whole batch if any member is not startable without a special user call —
   `done`, `not-planned`, `later`, `proposed`, open `needs: decision`,
   assigned to someone else, or already `review` (resume is single-task,
   not batch) — listing each ineligible id and why. Soft cases that only
   need confirmation in single-task (`doing`, pointless-looking) still
   require that confirmation up front; without it, refuse the batch.
   Claim nothing until every remaining id is clear to start.
3. **Set-relative blocking.** A dep is unfinished when its status is not
   `done` (a missing id counts as unfinished). An **external blocker** is an
   unfinished dep whose id is **not** in the set. If any selected task has
   one, refuse the whole batch and list, per blocked task, the outside dep
   id(s) and their status. Deps that are already `done`, or that are other
   members of the set, do not block.
4. **Order.** Topological sort on edges *within* the set (A before B when B
   lists A in `deps`); tie-break on lower id. Cycle in the set → refuse and
   report it. State the order once ("implementing as 13 → 15 → 14") and
   proceed.
5. **Shared preflight once:** `gh auth`, then `TASKS preflight` (check only)
   from implement.md step 0. If any member is untagged, recommend an area
   per id (implement.md area sanity) and wait — do not collide or claim
   yet. After every member has an area, `TASKS collisions <id,id,…>` with
   the **whole set** (not one id). Exit 2 → refuse the batch and print
   the output (outside `doing`/`review` occupancy). `set:` overlap lines
   mean members share an area — informational; implement sequentially.
   If any remain untagged, refuse the rest. If any member is `all`, also
   require the quiet-board vouch from implement.md.
6. **Implement sequentially** in that order, running implement.md steps 0–7
   per id (lightweight re-check of state, claim, branch, implement, ship).
   Reuse the shared ahead check if the tree is still clean.
   - **Ship stamp:** every member's ship must carry the full set:
     `TASKS ship <id> --batch <id,id,…> --shipped "<what actually shipped>"`
     (sorted ids of the whole batch; the shipped record is per task, never
     one summary reused across the set).
     That writes `Dev-batch: …` on the PR body and task body so review can
     gate partial review and land the set together
     (`flows/review-batch.md`). Per-PR `Version intent` is a suggestion;
     review applies one bump sized for the whole set.
   - **Within-set deps:** when claim warns about unfinished deps that are
     earlier members of this batch (typically now `review` after their ship),
     proceed. Still stop if an unfinished dep is outside the set (shouldn't
     happen after step 3 unless the board moved).
   - **Area collision** for a later member: re-check with
     `TASKS collisions <id,id,…>` for the **whole set**, not the single
     id — in-set `review` after an earlier ship is expected. Exit 2
     (outside occupancy) → stop; leave already-shipped members in
     `review` and report the rest.
   - If a later member becomes unstartable mid-batch (board moved), stop;
     leave already-shipped members in `review` and report the rest.
7. **Out of scope for this path:** parallel worktrees or parallel agents for
   the batch, unless the user later asks. Each task still gets its own branch
   and PR. Prefer `origin/<integration>` as the base; if a later task truly
   needs unmerged code from an earlier one, stack on that task's branch
   (`TASKS ship --base <parent-branch> --batch … --shipped …`) and note the
   dependency in
   the PR body. Stacking is cheap: `land` merges the parent without
   rewriting it, so a stacked child is only retargeted, never rebased —
   at any depth.
