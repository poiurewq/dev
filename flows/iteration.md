# /dev iteration — show / close / new

An iteration = one integration branch + its board. Numbering resets each
iteration; cross-iteration references are `<iteration>/T<id>` (log.md lines
are already written in that form). Boards whose integration branch is `main`
with no parent simply never close — that's fine for simple repos.

## `/dev iteration` — show

`TASKS iteration`: scope, name, branch, parent, done/total.

## `/dev iteration close`

1. Confirm intent. If unfinished tasks exist, ask per task: finish it,
   **carry over** (note title/body now; it gets re-added in the next
   iteration with a fresh id), or **drop** it (`--status not-planned
   --reason "<why>"`, or delete — user's choice). Dropped tasks don't block
   the close and need no `--force`. Umbrella tasks deserve scrutiny
   here: an unfinished umbrella means the iteration's goal isn't actually
   met — closing anyway should be a deliberate call, not a shrug.
2. `TASKS iteration-close` (add `--force` only after the user accepts the
   carry-over list — genuinely abandoned tasks should be `not-planned`
   first, so `--force` covers only the carried-over ones). This logs every
   task to `.tasks/log.md` (dropped ones marked `[not planned]`) and
   removes the files — git history keeps the full bodies.
3. **Land the iteration**: `gh pr create --base <parent> --head
   <integration-branch> --title "iteration <name>"`; it merges via normal
   review (flows/review.md; the integrator typically owns this). Landing PRs
   for an iteration are merged with a **merge commit** (`gh pr merge
   --merge`), not squash — task-level history should survive in the parent.

## `/dev iteration new <branch>`

1. After the close PR has merged: `TASKS iteration-new <branch> [--parent p]
   [--name n]`. This starts a fresh board on the new branch **and commits a
   pointer to the parent's board.yml** so other contributors' stale
   checkouts auto-resolve to the new iteration. Note: that pointer commit
   pushes to the parent — on a push-protected parent it will warn, and
   contributors then need a one-time `TASKS init --integration <branch>`.
2. Remind the user: `git checkout <branch>`.
3. Re-add any carried-over tasks (fresh ids, same bodies; keep a
   `carried from <old-iteration>/T<n>` note in each).
