---
name: dev
description: Multi-contributor task board and dev workflow for a repo, or per-subdir boards in a monorepo. Use for /dev and subcommands (init, add, plan, board, kanban, status, pick, implement, review, auto, absorb, change, delete, show, config, area, meta, iteration, update), and whenever the user asks to add/see/assign/implement/review tasks on the repo's task board, or asks "what should I work on".
---

# dev — coordinated development for humans and agents

One task board per product (a repo, or a subdir of a monorepo), shared by
every contributor — human or agent — across worktrees and machines. You are
the interface: parse intent, run the script for all board state, apply
judgment yourself. When the routing table names a `flows/*.md`, read that
file and follow it — don't improvise the flow.

`SKILL_DIR` = the directory containing this file.
`TASKS` = `python3 SKILL_DIR/scripts/tasks.py`, run from inside the repo. It
targets the nearest board at or above cwd, or `--scope <subdir>`; there is no
single-board fallback, so monorepo work runs from the product directory.
`SKILL_CMD` = `python3 SKILL_DIR/scripts/skill.py` — acts on the installed
skill, not the board (see Skill commands).

## Script synopsis

Use these exact invocation patterns — don't guess flags:

```
TASKS --scope <subdir> <subcommand> ...   # target another board in the repo
                                          # (goes BEFORE the subcommand)
TASKS init --name <handle> [--scope <subdir>] [--integration <branch>]
           [--parent <branch>] [--iteration <name>]
TASKS whoami                            # this checkout's identity (exits
                                        # nonzero if not set)
TASKS config [<key> [<value>]]          # settable: integrator, parent_branch,
                                        # iteration (rename; date-prefix like
                                        # init, keeping current date; refused
                                        # once closed-not-landed)
TASKS area list
TASKS area set <name> [--desc "<one-line scope>"]
TASKS area rm <name> [--force]
TASKS add --title "<title>" [--area <m>] [--deps <id,id>]
          [--desc "<1–3 sentences>"] [--assignee <who>]
          [--kind umbrella]                     # empty = normal
          [--status proposed|backlog|planned|later]  # default backlog
TASKS update <id> [--title "<t>"] [--area <m>] [--status <s>]
          [--kind umbrella|""] [--assignee <who>|""] [--branch <b>|""]
          [--pr <url>] [--needs decision|""] [--deps <id,id>]
          [--append "<paragraph>"]              # add to body, keeping it
          [--desc "<new body>"]                 # REPLACE whole body
          [--status later]                      # park for a later iteration
          [--status not-planned --reason "<why>"]   # reason is required
TASKS delete <id>
TASKS show <id>
TASKS collisions <id[,id…]>             # area occupancy vs doing/review;
                                        # multi-id also prints in-set overlap;
                                        # exit 2 if any id is in-flight-blocked
                                        # (batch peers excluded from that check)
TASKS related "<text>"                  # existing tasks similar to <text>;
                                        # run before every add
TASKS list [--assignee <who>] [--status <s>] [--needs decision] [--json]
TASKS board [--expand] [--by-area] [--watch]
                                        # index: one line per status (or
                                        # area) of task ids; then in-play
                                        # tasks one per line. collapses
                                        # umbrella children; done/later/
                                        # not-planned fold to a count;
                                        # --expand lists children and those
                                        # three; --watch: r/a/q, type id↵
                                        # for area collisions (./board)
TASKS iteration
TASKS iteration-close [--force]
TASKS iteration-new <branch> [--parent <branch>] [--name <name>]
TASKS iteration-land [--create-only] [--title T] [--body B]
                                        # open/merge iteration PR into parent
                                        # with merge commit (not squash)
TASKS claim <id> [--assignee <who>] [--branch <b>]
                                        # branch from origin/integration
                                        # (ff empty leftover; refuse if
                                        # diverged), always a linked
                                        # worktree under .dev/worktrees
                                        # (primary stays hub), status=doing;
                                        # prints workdir + product + verified
                                        # base (edit + compile/test/run in product)
TASKS diff <id>                         # location + review diff from the
                                        # task worktree (not session cwd);
                                        # implement self-review / resume
TASKS ship <id> --shipped "<what actually shipped>"
                [--message M] [--title T] [--body B]
                [--version-intent <intent>] [--base <branch>]
                [--batch <id,id,…>]     # commit if dirty ([T<id>] prefix),
                                        # push, gh pr create if none open,
                                        # status=review + pr URL. Re-ship
                                        # reuses the open PR, so --title /
                                        # --body / --version-intent / --base
                                        # apply on create only.
                                        # --shipped is REQUIRED on every ship
                                        # (result, not plan): appended to the
                                        # task body as Shipped (<date>): … and
                                        # mirrored onto the PR body each ship.
                                        # --version-intent has no default;
                                        # --base stacks; --batch stamps
                                        # Dev-batch on PR + task body
TASKS batch-gate --ids <id,id,…>        # exit 2 if selection omits open
                                        # co-members of a Dev-batch/stack
TASKS restack --ids <id,id,…> [--after N] [--onto <ref>]
              [--retarget] [--dry-run]  # fail-closed stack rebase (plan,
                                        # then apply unless --dry-run);
                                        # auto-retargets PR base to
                                        # integration when the stack parent
                                        # is outside the set; --onto rebases
                                        # every target onto that ref (no
                                        # cascade — the default keeps in-set
                                        # stack parents); destack onto a
                                        # new base excludes the old parent
                                        # tip (no squash replay)
TASKS preflight [--park|--discard]      # local integration ahead of origin
                                        # (check exits 2 if in-scope ahead;
                                        # --park / --discard are interactive)
TASKS land <id>                         # post-approval: destack children
                                        # first, then merge, cleanup, done
TASKS cleanup <id>                      # worktree + branch prune (branch only if PR MERGED)
```

Caveats: `--append` adds to a body (no prior `show` needed, can't truncate);
`--desc` **replaces** it — only for genuine rewrites. Flags combine in one
call; an empty string (`--assignee ""`, `--needs ""`, `--branch ""`) clears a
field; multi-word values need quoting. `--area` takes a comma-separated list
(`--area "cli, docs"`); `all` is reserved (see Area stewardship). When no
board is found the error lists known boards — cd in, or pass `--scope`.
**Whenever a `TASKS` command exits non-zero or prints `error: …`, surface
that output to the user verbatim and stop** — never hide it, paraphrase it
away, or paper over it with manual git/gh.

## Ground rules

- **Never edit `.tasks/` by hand and never commit to it from a code branch.**
  All board reads/writes go through `TASKS`, which serializes every mutation
  through the integration branch on the remote — that is what makes the board
  conflict-free. Bypassing it breaks the model.
- **Scripted git lifecycle.** Never improvise multi-step git/gh for task
  work: use `claim`, `diff` (self-review from the task worktree), `ship`,
  `land`/`cleanup`, `preflight` (park/discard), `iteration-land`, and for
  stacks `restack`/`batch-gate`. Flows decide and call; they don't spell
  out raw sequences to re-invent. Read-only observation (`gh pr view` /
  `gh pr diff`) and human review actions stay outside those helpers.
- **PR is the only merge flow.** GitHub and an authenticated `gh` are
  assumed; init walks the user through setup, and implement/review are gated
  on `gh auth status` succeeding. Task branches start from
  `origin/<integration>`; if local integration is ahead on this board's
  scope, implement stops to park-as-PR or discard first (flows/implement.md).
  Auto never parks or discards — it skips and reports.
- **Humans decide; agents draft.** Review verdicts, design-fork resolutions,
  and proposal approvals belong to the user. Interactive flows surface the
  question and wait; auto files board-native proposals (`proposed` status,
  `needs: decision`) and never decides them.
- **User specs are one guess.** Specifications the user lays out — on add, or
  when recommending changes during implement or review — are one guess at an
  implementation and may not be the best. When any part doesn't make sense or
  could be clearly improved, say so; when something is unclear, ask. The
  commitment is to a good product, not to the user's words.
- **Vendor-neutral**: product docs live in `AGENTS.md` at the directory
  that owns the code (create or update the nearest one that covers what
  you touched — a nested package's rule does not go in the repo root).
  Record non-obvious invariants there: conventions and decided
  architectural, procedural, or domain rules, not only implementer
  gotchas. Keep it lean; skip anything obvious from the code. If
  `CLAUDE.md` is present, it is a symlink to that `AGENTS.md` — not
  the reverse (promote a lone `CLAUDE.md`; if both are regular files,
  fold unique content into `AGENTS.md`, then replace `CLAUDE.md` with
  a symlink).
- **Under-specify tasks, and write short.** A task description is a few
  sentences: the goal. Details get resolved at implementation time. The
  same brevity applies to everything else you write — appended decisions,
  PR bodies, reports to the user. Review bandwidth is the scarcest
  resource on the board, and verbose text spends it. Never produce long
  specs, plans, or status reports the user didn't ask for.
- **Do not manufacture specifications.** When adding or modifying a task,
  never write constraints, non-goals, interfaces, or other requirements
  the user did not explicitly state or approve. Future agents treat the
  task file as human-approved. Non-goals belong on the task only when the
  user said them.
- Natural-language intents map onto subcommands; fulfill them and mention the
  keyword once ("done — btw, `/dev pick 12` does this directly"). Always show
  a suggested command's argument shape — `/dev add <task-or-goal>`,
  `/dev implement <id[, id…]|goal>`, `/dev meta [id-or-area]` — never a
  bare `/dev add ...`, which
  leaves the user guessing what goes there.
- **New work goes through `/dev add`.** Explicit `/dev add`, freeform "we
  should…", or a goal after `/dev` all land on the same path; the agent
  triages inside add (direct file, plan, or shape) — never ask the user to
  pick a path. `/dev plan` is a legacy alias. `/dev implement <goal>` with
  no matching board task also files via add first, then builds
  (flows/implement.md).

## State layout (per board)

- `<scope>/.tasks/` — tracked, lives only on that board's integration
  branch: `NNN.md` (one file per task), `board.yml` (`schema_version`,
  integration_branch, parent_branch, iteration, integrator, contributors),
  `areas.md` (area names + one-line scopes), `log.md` (past-iteration
  index), `archive/<iteration>/NNN.md` (every closed task, verbatim — the
  full record the log only indexes). Iteration names are stored as
  `<YYYY-MM-DD>-<name>` (start date, applied at every setter), so
  archive dirs and log sections stay in the order they happened. Missing
  `schema_version` means 0; see product `AGENTS.md` for compatibility rules
  when changing board schema.
  Task frontmatter may carry `kind` (`umbrella` = goal parent; absent/empty
  = normal). An umbrella's `deps` are its **direct children** (leaves or
  nested umbrellas) — hierarchy is the dep graph among `kind: umbrella`
  nodes, with no separate parent field.
  `TASKS board` is an index (one line per status of task ids) then
  in-play tasks one per line. It collapses umbrella children under the
  parent with a leaf status rollup; done, later, and not-planned fold
  to a count. `--expand` lists children and those three in the index
  and the list; `--by-area` indexes by area instead of status (same
  collapse; folded statuses stay counts on last index lines unless
  `--expand`).
- `<scope>/.dev/` — product-local, gitignored, per-checkout (root board →
  `.dev/` at the git toplevel): `identity`, `boards.json` (scope→branch
  cache), `board/` (hidden worktree on a private branch `_dev-board` or
  `_dev-board-<scope>`), `worktrees/` (task worktrees).
- `<scope>/TASKS.md` — derived kanban view, gitignored, regenerated by `board`.
- `<scope>/board` — local live viewer (`r` refresh, `a` toggle by-area, `q` quit, type a task id + Enter for area collisions), gitignored,
  written by `init` if missing and refreshed by `init` / `board` while still
  a stock wrapper. Not `.dev/board` (that is the board worktree).

Statuses: `proposed` (auto-filed, awaiting human approval) → `backlog` →
`planned` → `doing` → `review` → `done`. Off to the side: `later` — intended,
not this iteration (reseeds on iteration-new; see below); `not-planned` —
deliberately decided against (see below). `done`, `later`, and `not-planned`
do not block an iteration close. `needs: decision` marks an open design fork
awaiting a human call, detailed in the task body.

## Routing

| Invocation | Action |
|---|---|
| `/dev` (bare) | First contact, below. |
| `/dev help` | Full options table (below). No board/identity → same setup path as bare. |
| `/dev init` | Read `flows/init.md` (identity, board creation/adoption, gh setup, areas). |
| `/dev add <task-or-goal>` | Read `flows/add.md` (triage, then direct add, plan, or shape). |
| `/dev plan <goal>` | Alias of `/dev add` (same triage). |
| Freeform new work | Same as `/dev add`. |
| `/dev implement <id[, id…]|goal>` | Read `flows/implement.md` (multi → also `flows/implement-batch.md`; claim, forks, PR). |
| `/dev review [id[, id…]]` | Read `flows/review.md` (inbox; multi-id → `flows/review-batch.md`). |
| `/dev auto` | Read `flows/auto.md` (autonomous implement cycle). |
| `/dev absorb <source>` | Read `flows/absorb.md` (import an external task list). |
| `/dev iteration ...` | Read `flows/iteration.md` (show / close / new). |
| `/dev board` / `kanban` | `TASKS board` (status index + in-play list); `TASKS board --by-area` for the area cut; then PR state refresh (below). |
| `/dev meta [id-or-area]` | Read `flows/meta.md` (pressure the board; optional focus). `/dev meta area` (or `areas`) is reserved: area-validity pass only. Freeform "does this board still make sense?" too. |
| `/dev status` | Your plate — see "Status" below. |
| `/dev pick <id-or-desc>` | `TASKS update <id> --status planned --assignee <me>`; if already assigned to someone else, confirm the takeover with the user first. |
| `/dev change <id> <what>` / `modify` | Map onto `TASKS update <id> --...`; show the result. |
| `/dev delete <id>` | Confirm, then `TASKS delete <id>`. |
| `/dev drop <id>` / "we're not doing this" | Not planned, below — offer delete as the alternative and let the user pick. |
| `/dev show <id>` | `TASKS show <id>`. |
| `/dev config [key] [value]` | `TASKS config ...` (settable: integrator, parent_branch, iteration). |
| `/dev area ...` | Area stewardship, below. |
| `/dev skill` | `SKILL_CMD status` — version, auto-update state, skill commands. |
| `/dev skill update` | `SKILL_CMD update` — pull latest skill from github.com/poiurewq/dev. |
| `/dev skill update auto on/off` | `SKILL_CMD auto on/off` — opt-in auto-apply on checks. |
| `/dev skill feedback <text>` | Read **Skill commands** below — send a bug or idea **about the dev skill** to its maintainers as a public GitHub issue. |

Before any board operation, check identity with `TASKS whoami` — never probe
the filesystem for it yourself. Identity is **product-local**
(`<scope>/.dev/identity`), so run from the product directory or pass
`--scope`; a bare monorepo root with no board there errors. If `whoami`
errors, run the init flow. Given a description instead of an id, run
`TASKS list`, match, confirm if not obvious. **`/dev implement` only:** if
nothing matches, run the `/dev add` path first (`flows/add.md`), then
continue implement on the new task(s) — details in `flows/implement.md`.
Other id-or-desc commands (`pick`, `show`, …) still require a match.

## Skill commands (`/dev skill ...`)

Everything under `/dev skill` acts on the **installed skill**, never on the
user's board or repo. Keep the two apart when you speak: `/dev review` is
feedback on *their* work; `/dev skill feedback` is feedback on *this tool*.

Public installs are a **git clone** of https://github.com/poiurewq/dev into an
agent skills dir. Prefs live at `~/.config/dev-skill/config.yml` (survives
pulls; `schema_version` like board.yml — see `scripts/skill.py`).

**Throttled check** — run `SKILL_CMD check` once at the start of bare `/dev`,
`/dev help`, `/dev init`, `/dev add` (including freeform add and `/dev
plan`), `/dev board` / `kanban`, `/dev implement`, `/dev review`, and
`/dev meta`. It runs
at most every 24h and stays silent when the local version is current or the
network fails; a missing local `VERSION` counts as `0.0.0`. Show any line it
prints (one quiet line) and continue the command — never block board work on
it. With auto-update on, the check may apply a pull and print one
success/failure line.

Command→script mapping is in the routing table above, plus two forms it
omits: bare `/dev skill update auto` → `SKILL_CMD auto` (reports the current
setting), and `/dev skill feedback <text>` →
`SKILL_CMD feedback --title "<t>" [--body "<b>"]`.

### Feedback to the maintainers (`/dev skill feedback`)

Opens an issue on poiurewq/dev — the maintainer inbox for the skill itself.
Not for the user's own repo, and not a way to file board tasks (that is
`/dev add <task-or-goal>`); if the user seems to mean their own work, ask
before filing.

Draft a title (one line) and body (the behaviour they saw and what they
expected) from what the user said. **Show the draft and get their OK before
running the command**, and say plainly what it does: files a GitHub issue on
the public poiurewq/dev repo, which cannot be quietly undone. The script
appends skill version, install kind, python, and OS. Never put repo names,
paths, branch names, or task content in the title or body — this is a public
repo. Report the issue URL the script prints.

## First contact (bare `/dev`)

Classify with two script calls — `TASKS board` (board?) and `TASKS whoami`
(identity?):

- **Board exists** (and identity set): show `TASKS board`, then **context
  options only** (below) — never the full options table, and no per-command
  commentary. Close with one line that `/dev help` has the complete menu.
- **No board** — assume the user knows nothing beyond the name. Read
  `flows/init.md` and deliver its step-0 welcome **verbatim** (it exists so
  every agent gives the same first impression — don't improvise your own),
  then run the init flow if they accept. Don't dump the full command
  reference on them; they'll learn verbs as they need them.
- **Board exists but no identity** (new contributor): skip the sales pitch —
  one line ("this repo runs its task board with dev; let's get you on it"),
  then the init flow's identity/join steps.

### Context options (bare only; after identity)

From board state (and a light PR refresh on `review` tasks if useful), emit
only the lines that apply — short command shapes, ids filled in when known.
Skip categories that don't apply; do not pad toward a full menu.

| When | Offer |
|---|---|
| Open forks (`needs: decision`), `proposed` tasks, or open PRs you may merge | `/dev review` |
| Your `doing` / `planned` / `review` work | `/dev status` · `/dev implement <id>` (or resume note for `review`) |
| Unassigned backlog and a quiet plate | 1–2 unblocked candidates via `/dev pick <id>` or `/dev implement <id>` |
| No open work on the board | `/dev add <task-or-goal>` |

Prefer the highest-signal row(s); one or two lines is enough. Always end with
the `/dev help` hint.

## `/dev help`

When board and identity exist: emit the options table **verbatim** (below),
plus an optional one-liner that bare `/dev` is board + next steps — nothing
else. No board, or board without identity: same setup path as bare first
contact (welcome / join) — don't show the table before the board is usable.

### Options table (reproduce as-is)

Every agent shows the same menu, so the user learns one map of the tool.
Emit it whole — don't reorder, trim to "what's relevant", or annotate.

| | |
|---|---|
| **See** | `/dev board` — the whole board (status) · `/dev board --by-area` — cut by area · `/dev status` — your plate · `/dev show <id>` — one task |
| **Add work** | `/dev add <task-or-goal>` — file a task, or work a goal or pile of threads onto the board · `/dev absorb <file>` — import an existing list |
| **Do work** | `/dev pick <id>` — claim it · `/dev implement <id[, id…]|goal>` — build it (or a batch) and open PR(s); unknown goal is filed via add triage first · `/dev auto` — hand one to an agent |
| **Decide** | `/dev review` — PRs, design questions, and proposals waiting on you · `/dev meta` — pressure the board · `/dev meta area` — area-validity only |
| **Adjust** | `/dev change <id> <what>` · `/dev delete <id>` · `/dev area` · `/dev config` |
| **Iterate** | `/dev iteration` — show, close, or start the next one |
| **Skill** | `/dev skill` — version and update state · `/dev skill update` — pull latest dev skill · `/dev skill update auto on/off` — opt-in auto-apply · `/dev skill feedback <text>` — file a bug or idea about the skill as an issue on its public repo |

## Not planned (inline)

`not-planned` = decided against, kept with its reason so the idea isn't
re-litigated. `delete` = erase (filed in error, duplicate, noise).

`TASKS update <id> --status not-planned --reason "<why>"` — the script
requires the reason and appends it to the body as `Not planned (<date>):`.
Ask the user for it in their own words; if the answer is thin, ask once what
changed. Reversible: `--status backlog` revives, the reason stays as history.

A human decision — agents propose it, auto never uses it. When the user wants
a task gone, ask which they mean (delete or not-planned) rather than choosing
for them.

## Later (inline)

`later` = intended, not this iteration. Not a new verb: `/dev change <id>
later` → `TASKS update <id> --status later` (or `add --status later`). No
`--reason`. Claim, ship, land, and auto refuse it. Iteration close treats it like
not-planned (logged `[later]`, not unfinished). `iteration-new` re-adds
archived later tasks with fresh ids, still later, plus a
`carried from <old-iteration>/T<n>` note — no walk. Revive with
`--status backlog` or `/dev pick <id>`.

## Area stewardship (inline)

Areas exist for **coordination**, not just labeling: contributors working in
disjoint areas can't conflict, so dev steers concurrent work apart. A task's
`area` may list several, comma-separated. Two tasks **collide** when any name
on one **segment-prefix-overlaps** any name on the other: split on `/`;
`flows` overlaps `flows/implement` and `flows/other`; `flows/implement` does
not overlap `flows/review`; `flow` does not overlap `flows` (string prefix
is the trap). The script owns this check (`areas_overlap` /
`in_flight_area_collisions` in tasks.py); `TASKS collisions <id[,id…]>`
and watch-mode type-id query it. On collision with in-flight (`doing`)
work **or** `review` tasks — any assignee, including the current user —
implement **aborts** (print the collisions output and stop; do not claim).
Auto skips. A multi-id call excludes the other ids in the set from that
fail check (batch members are sequential) and prints `set:` overlap
lines as information only. Resume of one batch member uses that same
set (`flows/implement.md`), not the single id. The reserved area
**`all`** marks a task that may touch everything (wide refactors, some
umbrellas): it collides with every task, so it runs only on an otherwise-
quiet board (nothing in `doing` or `review`) with the user's explicit
confirmation that other contributors are paused; auto never picks it, and
it's never listed in areas.md.

A name with `/` is a path: parents are implied by segments, with no parent
field. A parent is a subtree-wide lock (`flows` = any/all `flows/*`) and is
only assignable if it is listed in areas.md — do not invent it because a
directory exists. Tag the parent **or** the specific children, not both.
Prefer the children so locks stay narrow; do not tag the parent because the
work *might* go wide — widen at implement if a fork actually crosses them.
Residuals that are not a named child go to `…/other` (`flows/other`), never
the parent. A listed path-parent may itself be slash-free (`flows`) and
still own `flows/*`. Conceptual names (`docs`, `ci`) stay slash-free and
do not grow children — `docs`, not `docs/readme`.

There is no fixed upper bound on how many areas a board should have: more
areas enable more concurrent work. The natural limiter is multi-area
occupancy — prefer as many stable areas as enable parallelization without
typical tasks needing many areas at once (over-granularity → confusing
multi-area tags). Init proposes under that heuristic (flows/init.md).

Areas live in `.tasks/areas.md` via `TASKS area list|set|rm`. Names: when the
work splits by path, prefer a repo-relative path or the shortest uniquely
distinguishing portion (`cli`, `billing/api`) — no filename extensions
(`flows/iteration`, not `flows/iteration.md`). Cross-cutting non-directory
work still uses a short slash-free phrase (spaces fine). Form: no trailing
`/`, no leading `./`, no `:` or `,`. The one-line `--desc` carries the rest.

`/dev area` with no args → `area list` only. Validity (stale names,
path-renames, parent-used-as-residual, leftover extensions) lives under
`/dev meta area` (flows/meta.md). `area list` open counts and `area rm`
(without `--force`) match **exact names only** — a parent is not
responsible for its children's tasks; later still occupies; not-planned
does not. `TASKS board --by-area` indexes along the area axis: multi-area
tasks appear on each listed area's id line, umbrellas collapse as on
the status board, and done/later/not-planned fold to counts on last
index lines.

## Status (`/dev status`)

`TASKS list --assignee <me>`, then the PR refresh below. Report, in order:
your in-flight tasks and their PR state (call out **changes requested** —
that's your move to make), then anything waiting on you (`TASKS list --needs
decision`, `--status proposed`).

**If your list is empty** (`(no matching tasks)`), don't stop at "nothing
assigned" — that's a dead end when the board has work. Add: the unassigned
backlog count, one or two candidates worth picking (`TASKS list --status
backlog`, prefer unblocked ones), and `/dev pick <id>`. If the board is
genuinely empty too, say so and point at `/dev add <task-or-goal>`.

## PR state refresh (during board/status/review)

For tasks in `review` with a `pr`, when gh is available: `gh pr view <url>
--json state,mergedAt,reviewDecision`.

- Merged → `TASKS land <id>` (marks done, cleans local worktree/branch; safe
  if already done). If land/cleanup aborts, surface the script error — do not
  mark done by hand.
- `CHANGES_REQUESTED` → the assignee's move; surface it (resume path in
  flows/implement.md).
- `APPROVED` and unmerged → ready to land via review.
- Closed unmerged → flag it; only prompt for a decision if the current user
  is the assignee or integrator.

## Branch & role conventions

- Branch-per-task: `dev/<id>-<slug>` (scoped boards:
  `dev/<scope-with-/->-<id>-<slug>`), owned by the task's assignee,
  short-lived, rebased on the integration branch, merged promptly via PR.
- Merge rights: anyone except the task's assignee may review and merge; the
  integrator may review anything, including their own work.
- The `integrator` (default: board creator; `/dev config integrator <name>`)
  owns the integration branch: default reviewer, conflict arbitration,
  iteration close.
- Code branches contain code only; the board never rides them — `.tasks/`
  may appear in a feature checkout (inherited from integration), which is
  fine as long as the PR does not modify it.
