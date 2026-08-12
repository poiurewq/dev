# /dev iteration — show / close / new

An iteration = one integration branch + its board. Numbering resets each
iteration; cross-iteration references are `<iteration>/T<id>` (log.md lines
are already written that way). Boards whose integration branch is `main` with
no parent simply never close — fine for simple repos.

**Names carry their start date.** Every setter stores `<YYYY-MM-DD>-<name>`.
`init` / `iteration-new` prefix today (or the branch name) unless the value
already starts with an ISO date; `config iteration` keeps the current start
date on rename, or prefixes today if the current name is undated. A value
that already starts with an ISO date is stored as-is. That is what makes
archive dirs and log.md sections reconstruct the order the project was built
in — don't strip the date when referring to an iteration.

## `/dev iteration` — show

`TASKS iteration`: scope, name, branch, parent, done/total.

To rename the current iteration: `TASKS config iteration <new-name>` (same
normalization as above). Refused once the iteration is closed
but not yet landed, since the land gate matches log.md's close heading.

## `/dev iteration close`

1. Confirm intent. If unfinished tasks exist, ask per task: finish it, **carry
   over** (note title/body now; it gets re-added next iteration with a fresh
   id), or **drop** it (`--status not-planned --reason "<why>"`, or delete —
   user's choice). Dropped tasks don't block the close and need no `--force`.
   Umbrella tasks deserve scrutiny here: an unfinished umbrella means the
   iteration's goal isn't actually met, so closing anyway should be a
   deliberate call, not a shrug.
2. `TASKS iteration-close` (add `--force` only after the user accepts the
   carry-over list — genuinely abandoned tasks should be `not-planned` first,
   so `--force` covers only the carried-over ones). This copies every task
   file verbatim to `.tasks/archive/<iteration>/NNN.md` — the full record,
   greppable and browsable long after the iteration — and indexes them in
   `.tasks/log.md` (one line per task with area, PR, and `[not planned]` /
   `[unfinished: …]` flags, each task's `Shipped (<date>): …` records under
   it), then removes the live files. Nothing is lost; point people at the
   archive, not at git history of a deleted file.
3. **Land the iteration**: `TASKS iteration-land` (opens the PR if needed,
   then merges with a **merge commit**, not squash). Requires a closed board:
   no live task files and a `log.md` close section for the current iteration
   (what step 2 writes). Use `--create-only` when review should happen first
   via flows/review.md; the integrator typically owns the merge. Do not
   hand-roll `gh pr create` / `gh pr merge --merge` here. Task-level history
   should survive in the parent.

## `/dev iteration new <branch>`

1. After the close PR has merged — **enforced**, not just advised:
   `TASKS iteration-new <branch> [--parent p] [--name n]` refuses while the
   current integration branch is not yet contained in the parent, since the
   new board starts from the parent and would otherwise be missing the last
   iteration's archive and log.md close section. Land it first; there is no
   override. This starts a fresh board on the new branch **and commits a
   pointer to the parent's board.yml** so other contributors' stale checkouts
   auto-resolve to the new iteration. That pointer commit pushes to the
   parent — on a push-protected parent it will warn, and contributors then
   need a one-time `TASKS init --integration <branch>`. It also refuses a name
   whose archive dir already exists (a reused iteration name would overwrite
   that iteration at close) — pick another `--name`; renaming is free here.
2. Remind the user: `git checkout <branch>`.
3. Re-add any carried-over tasks (fresh ids, same bodies — read them from
   `.tasks/archive/<old-iteration>/NNN.md`; keep a
   `carried from <old-iteration>/T<n>` note in each).
