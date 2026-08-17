# /dev iteration — show / close / new

An iteration = one integration branch + its board. Task ids reset each
iteration; the iteration itself is a positive integer. Cross-iteration
references are `<n>/T<id>` (log.md lines are already written that way).
Boards whose integration branch is `main` with no parent simply never
close — fine for simple repos.

**Identity is the integer.** `board.yml` `iteration` is `1`, `2`, `3`…
(default 1 at init; `init --iteration N` to match a team already in
progress). `iteration_name` is an optional display label. Archive dirs
are `{n}-{slug}` (or `{n}` if unnamed); clash is on the number, not the
slug. `iteration_started` is `YYYY-MM-DD`, stamped at init /
iteration-new (today unless given) and settable so it can match an
external calendar. Close writes `started:` / `closed:` lines under
`## {n}` (or `## {n} — {name}`). Ship titles PRs `[n/T<id>] …`.

## `/dev iteration` — show

`TASKS iteration`: scope, index, display name, start date, branch,
parent, done/in-play (later omitted from the denominator).

`TASKS config iteration <n>` renumbers the live iteration (refused if
that number already has an archive). `config iteration_name` /
`config iteration_started` adjust the label and start date. All three
are refused once the iteration is closed but not yet landed, since the
land gate matches log.md's `## {n}` heading.

## `/dev iteration close`

1. Confirm intent. If unfinished tasks exist, ask per task: finish it, **park
   later** (`--status later` — not this iteration; reseeds still-later until
   someone picks it; does not block close), **carry over** (note title/body
   now; re-add next iteration as live work with a fresh id), or **drop** it
   (`--status not-planned --reason "<why>"`, or delete — user's choice).
   Dropped and later tasks don't block the close and need no `--force`.
   Umbrella tasks deserve scrutiny here: an unfinished umbrella means the
   iteration's goal isn't actually met, so closing anyway should be a
   deliberate call, not a shrug.
2. `TASKS iteration-close` (add `--force` only after the user accepts the
   carry-over list — genuinely abandoned tasks should be `not-planned` first,
   and parked work should be `later` first, so `--force` covers only the
   leftover unfinished ones). This copies every task
   file verbatim to `.tasks/archive/<n>-<slug>/NNN.md` — the full record,
   greppable and browsable long after the iteration — and indexes them in
   `.tasks/log.md` (one line per task with area, PR, and `[not planned]` /
   `[later]` / `[unfinished: …]` flags, each task's `Shipped (<date>): …`
   records under it), then removes the live files. Nothing is lost; point
   people at the archive, not at git history of a deleted file.
3. **Land the iteration**: `TASKS iteration-land` (opens the PR if needed,
   then merges with a **merge commit**, not squash). Requires a closed board:
   no live task files and a `log.md` close section for the current iteration
   (what step 2 writes). Use `--create-only` when review should happen first
   via flows/review.md; the integrator typically owns the merge. Do not
   hand-roll `gh pr create` / `gh pr merge --merge` here. Task-level history
   should survive in the parent.

## `/dev iteration new <branch>`

1. After the close PR has merged — **enforced**, not just advised:
   `TASKS iteration-new <branch> [--parent p] [--name n] [--iteration N]
   [--iteration-started D]` refuses while the current integration branch is
   not yet contained in the parent, since the new board starts from the
   parent and would otherwise be missing the last iteration's archive and
   log.md close section. Land it first; there is no override. This starts
   a fresh board on the new branch **and commits a pointer to the parent's
   board.yml** so other contributors' stale checkouts auto-resolve to the
   new iteration. That pointer commit pushes to the parent — on a
   push-protected parent it will warn, and contributors then need a
   one-time `TASKS init --integration <branch>`. The new index defaults to
   one more than the max of the live index and every archived index;
   `--iteration N` sets it explicitly and is refused if that number
   already has an archive.
2. Remind the user: `git checkout <branch>`.
3. Later tasks are reseeded by the script (fresh ids, still later, a
   `carried from <n>/T<id>` note) — do not walk them. Re-add any other
   carried-over (unfinished-then-forced) tasks the same way, reading
   them from `.tasks/archive/<n>-<slug>/NNN.md`.
