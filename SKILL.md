---
name: dev
description: Multi-contributor task board and dev workflow for a repo, or per-subdir boards in a monorepo. Use for /dev and subcommands (init, add, plan, board, kanban, status, pick, implement, review, auto, absorb, change, delete, show, config, area, iteration, update), and whenever the user asks to add/see/assign/implement/review tasks on the repo's task board, or asks "what should I work on".
---

# dev — coordinated development for humans and agents

One task board per product (a repo, or a subdir of a monorepo), shared by
every contributor — human or agent — across worktrees and machines. You are
the interface: parse intent, run the CRUD script for all board state, apply
judgment yourself. Detailed flows live in `flows/*.md`: when the routing
table names one, read that file and follow it — don't improvise the flow.

`SKILL_DIR` = the directory containing this file.
`TASKS` = `python3 SKILL_DIR/scripts/tasks.py`, run from inside the repo —
the script targets the nearest board at or above cwd, or `--scope <subdir>`.
There is no single-board fallback: monorepo work runs from the product
directory (or passes `--scope`).
`UPDATE` = `python3 SKILL_DIR/scripts/self_update.py` — skill self-update
against https://github.com/poiurewq/dev (see Skill updates below).

## Script synopsis

Use these exact invocation patterns — don't guess flags:

```
TASKS --scope <subdir> <subcommand> ...   # target a board elsewhere in the
                                          # repo (goes BEFORE the subcommand)
TASKS init --name <handle> [--scope <subdir>] [--integration <branch>]
           [--parent <branch>] [--iteration <name>]
TASKS whoami                            # this checkout's identity (exits
                                        # nonzero if not set)
TASKS config [<key> [<value>]]          # settable: integrator, parent_branch
TASKS area list
TASKS area set <name> [--desc "<one-line scope>"]
TASKS area rm <name> [--force]
TASKS add --title "<title>" [--area <m>] [--deps <id,id>]
          [--desc "<1–3 sentences>"] [--assignee <who>]
          [--kind umbrella]                     # optional; empty = normal
          [--status proposed|backlog|planned]      # default backlog
TASKS update <id> [--title "<t>"] [--area <m>] [--status <s>]
          [--kind umbrella|""] [--assignee <who>|""] [--branch <b>|""]
          [--pr <url>] [--needs decision|""] [--deps <id,id>]
          [--append "<paragraph>"]              # add to body, keeping it
          [--desc "<new body>"]                 # REPLACE whole body
          [--status not-planned --reason "<why>"]   # reason is required
TASKS delete <id>
TASKS show <id>
TASKS related "<text>"                  # existing tasks similar to <text>;
                                        # run before every add
TASKS list [--assignee <who>] [--status <s>] [--needs decision] [--json]
TASKS board [--expand]                  # collapse umbrella children by default
TASKS iteration
TASKS iteration-close [--force]
TASKS iteration-new <branch> [--parent <branch>] [--name <name>]
TASKS claim <id> [--assignee <who>] [--branch <b>]
                                        # implement setup: branch from
                                        # origin/integration, always linked
                                        # worktree under .dev/worktrees
                                        # (primary stays hub), status=doing;
                                        # prints workdir
TASKS land <id>                         # post-approval: merge, cleanup, done
TASKS cleanup <id>                      # worktree + branch prune (branch only if PR MERGED)
```

Caveats: adding to a body always uses `--append` (no prior `show`, can't
truncate); `--desc` **replaces** it — only for genuine rewrites. Flags
combine in one call. Empty string (`--assignee ""`, `--needs ""`, `--branch ""`)
clears a field. Multi-word values need quoting. `--area` accepts a
comma-separated list (`--area "cli, docs"`); `all` is reserved (see Area
stewardship). Board discovery: nearest board at or above cwd, or `--scope`;
if none, error listing known boards (cd in, or pass `--scope`). No
single-board fallback. **`claim` / `land` / `cleanup` errors:** when any
command exits non-zero or prints `error: …`, surface that output to the user
verbatim and stop — do not hide, paraphrase away, or paper over with
manual git/gh.

## Ground rules

- **Never edit `.tasks/` by hand and never commit to it from a code branch.**
  All board reads/writes go through `TASKS`. The script serializes every
  mutation through the integration branch on the remote — that is what makes
  the board conflict-free. Bypassing it breaks the model.
- **PR is the only merge flow.** GitHub and an authenticated `gh` are
  assumed; init pushes the user through setup, and implement/review are gated
  on `gh auth status` succeeding. Task branches start from
  `origin/<integration>`; if local integration is ahead on this board's
  scope, implement stops to park-as-PR or discard first (flows/implement.md).
  Auto never parks or discards — it skips and reports.
- **Humans decide; agents draft.** Review verdicts, design-fork resolutions,
  and proposal approvals belong to the user. In interactive flows, surface
  the question and wait; in auto mode, file board-native proposals
  (`proposed` status, `needs: decision`) and never decide them yourself.
- **Vendor-neutral**: repo docs live in `AGENTS.md` (per directory as
  needed); if a repo only has `CLAUDE.md`, make `AGENTS.md` the real file and
  `CLAUDE.md` a symlink to it — not the reverse.
- **Under-specify tasks, and write short.** A task description is a few
  sentences: goal + non-goals if any; details get resolved at implementation
  time. The same brevity applies to everything you write here — appended
  decisions, PR bodies, reports to the user. Review bandwidth is the
  scarcest resource on the board, and verbose text spends it. Never produce
  long specs, plans, or status reports the user didn't ask for.
- Natural-language intents map onto subcommands; fulfill them, and mention
  the keyword once (e.g. "done — btw, `/dev pick 12` does this directly").
  When suggesting a command to the user, always show its argument shape —
  `/dev add <task-or-goal>`, `/dev implement <id[, id…]|goal>` — never a
  bare `/dev add ...`, which leaves the user guessing what goes there.
- **New work goes through `/dev add`.** Explicit `/dev add`, freeform "we
  should…", or a goal after `/dev` all land on the same path. The agent
  triages inside add (direct file vs plan flow) — never ask the user to
  pick "add or plan". `/dev plan` is a legacy alias of `/dev add`.
  `/dev implement <goal>` with no matching board task also files via that
  path first, then builds (flows/implement.md).

## State layout (per board)

- `<scope>/.tasks/` — tracked, lives only on that board's integration
  branch: `NNN.md` (one file per task), `board.yml` (`schema_version`,
  integration_branch, parent_branch, iteration, integrator, contributors),
  `areas.md` (area names + one-line scopes), `log.md` (past-iteration
  summaries). Missing `schema_version` means 0; see product `AGENTS.md` for
  compatibility rules when changing board schema. Task frontmatter may include
  optional `kind` (`umbrella` = goal parent; absent/empty = normal). An
  umbrella's `deps` are its **direct children** (leaves or nested umbrellas);
  hierarchy is the dep graph among `kind: umbrella` nodes — no separate
  parent field. `TASKS board` collapses those children under the umbrella
  with a leaf status rollup; `TASKS board --expand` lists every task flat.
- `<scope>/.dev/` — product-local, gitignored, per-checkout (root board →
  `.dev/` at the git toplevel): `identity`, `boards.json` (scope→branch
  cache), `board/` (hidden worktree on a private branch `_dev-board` or
  `_dev-board-<scope>`), `worktrees/` (task worktrees).
- `<scope>/TASKS.md` — derived kanban view, gitignored, regenerated by `board`.

Task statuses: `proposed` (auto-filed, awaiting human approval) → `backlog` →
`planned` → `doing` → `review` → `done`. Off to the side: `not-planned` —
deliberately decided against (see below). `done` and `not-planned` are the
two terminal statuses; neither blocks an iteration close. A task with
`needs: decision` carries an open design fork awaiting a human call (details
in its body).

## Routing

| Invocation | Action |
|---|---|
| `/dev` (bare) | First contact, below. |
| `/dev help` | Full options table (below). No board/identity → same setup path as bare. |
| `/dev init` | Read `flows/init.md` (identity, board creation/adoption, gh setup, areas). |
| `/dev add <task-or-goal>` | **Adding work**, below — triage, then direct add or plan flow. |
| `/dev plan <goal>` | Alias of `/dev add` (same triage). |
| Freeform new work | Same as `/dev add`. |
| `/dev implement <id[, id…]|goal>` | Read `flows/implement.md` (multi → also `flows/implement-batch.md`; claim, forks, PR). |
| `/dev review [id]` | Read `flows/review.md` (unified inbox: PRs, forks, proposals). |
| `/dev auto` | Read `flows/auto.md` (autonomous implement cycle). |
| `/dev absorb <source>` | Read `flows/absorb.md` (import an external task list). |
| `/dev iteration ...` | Read `flows/iteration.md` (show / close / new). |
| `/dev board` / `kanban` | `TASKS board`; show it; then PR state refresh (below). |
| `/dev status` | Your plate — see "Status" below. |
| `/dev pick <id-or-desc>` | `TASKS update <id> --status planned --assignee <me>`; if already assigned to someone else, confirm the takeover with the user first. |
| `/dev change <id> <what>` / `modify` | Map onto `TASKS update <id> --...`; show the result. |
| `/dev delete <id>` | Confirm, then `TASKS delete <id>`. |
| `/dev drop <id>` / "we're not doing this" | Not planned, below — offer delete as the alternative and let the user pick. |
| `/dev show <id>` | `TASKS show <id>`. |
| `/dev config [key] [value]` | `TASKS config ...` (settable: integrator, parent_branch). |
| `/dev area ...` | Area stewardship, below. |
| `/dev update` | `UPDATE update` — pull latest skill from github.com/poiurewq/dev. |
| `/dev update auto on/off` | `UPDATE auto on/off` — opt-in auto-apply on checks. |

Before any board operation, check identity with `TASKS whoami` — never probe
the filesystem for it yourself. Identity is **product-local**
(`<scope>/.dev/identity`; root board → `<toplevel>/.dev/identity`). Run from
the product directory or pass `--scope` so whoami resolves the right board;
a bare monorepo root with no board there errors (strict discovery). If
`whoami` errors, run the init flow. Given a description instead of an id, run
`TASKS list`, match, confirm if not obvious. **`/dev implement` only:** if
nothing matches, run the `/dev add` path first (Adding work below), then
continue implement on the new task(s) — details in `flows/implement.md`.
Other id-or-desc commands (`pick`, `show`, …) still require a match.

## Skill updates

Public installs are a **git clone** of https://github.com/poiurewq/dev into an
agent skills dir. Prefs live at `~/.config/dev-skill/config.yml` (survives
pulls; `schema_version` like board.yml — see `scripts/self_update.py`).

**Throttled check** — at the start of these entry points, run `UPDATE check`
once (default at most every 24h; network failure is silent; local version
≥ public is silent; missing local `VERSION` counts as `0.0.0`):

- bare `/dev`, `/dev help`, `/dev init`
- `/dev add` (and freeform add / `/dev plan`)
- `/dev board` / `kanban`
- `/dev implement`, `/dev review`

If it prints a line, show it to the user (one quiet line) and continue the
command. Never block board work on check failure. With auto-update on, check
may apply a pull and print one success/failure line.

| User command | Script |
|---|---|
| `/dev update` | `UPDATE update` |
| `/dev update auto on` / `off` | `UPDATE auto on` / `off` |
| (status) `/dev update auto` | `UPDATE auto` |

## First contact (bare `/dev`)

Classify with two script calls — `TASKS board` (board?) and `TASKS whoami`
(identity?):

- **Board exists** (and identity set): show `TASKS board`, then **context
  options only** (below) — never the full options table. Close with one line
  that `/dev help` has the complete menu. Nothing else — no per-command
  commentary.
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

When board and identity exist: emit the options table **verbatim** (below).
Optional one-liner that bare `/dev` is board + next steps — nothing else.
No board, or board without identity: same setup path as bare first contact
(welcome / join) — don't show the table before the board is usable.

### Options table (reproduce as-is)

Every agent shows the same menu, so the user learns one map of the tool.
Emit it whole — don't reorder, trim to "what's relevant", or annotate.

| | |
|---|---|
| **See** | `/dev board` — the whole board · `/dev status` — your plate · `/dev show <id>` — one task |
| **Add work** | `/dev add <task-or-goal>` — file a task, or break a goal into tasks when needed · `/dev absorb <file>` — import an existing list |
| **Do work** | `/dev pick <id>` — claim it · `/dev implement <id[, id…]|goal>` — build it (or a batch) and open PR(s); unknown goal is filed via add triage first · `/dev auto` — hand one to an agent |
| **Decide** | `/dev review` — PRs, design questions, and proposals waiting on you |
| **Adjust** | `/dev change <id> <what>` · `/dev delete <id>` · `/dev area` · `/dev config` |
| **Iterate** | `/dev iteration` — show, close, or start the next one |
| **Skill** | `/dev update` — pull latest dev skill · `/dev update auto on/off` — opt-in auto-apply |

## Adding work (`/dev add`)

Used for `/dev add <task-or-goal>`, freeform new work, and the `/dev plan`
alias. One entry point; the agent triages.

### Triage (always first)

Do not ask the user "add or plan?". Decide:

| Path | When |
|---|---|
| **Direct add** | One right-sized unit (≈ one focused PR): clear outcome, few design forks, or a single "consider X" item. Continue with **Direct add** below. |
| **Plan flow** | Several deliverables already visible, real ordering across pieces, or an iteration-level / multi-PR goal. Read `flows/plan.md`. |

**Grey area → prefer direct add** (under-specify). Implement still
decomposes oversized tasks; plan is for breakdown the user needs *now*, not
every large-sounding phrase. After triage, do the work and mention the path
once (e.g. "filed as one task" / "breaking into tasks").

### Direct add

1. Draft title, area (`TASKS area list`; reuse before inventing — greenfield
   rule: no areas until ~3 tasks cluster), and a 1–3 sentence description.
2. **Check the board first**: `TASKS related "<title + description>"`.
   - Substantially the same task exists → say so and propose amending it
     (`TASKS update <id> ...`) instead of adding a duplicate. The user
     decides; add anyway if they want them separate.
   - Otherwise judge the neighbours it lists for **dependencies in both
     directions** — must something else land first (`--deps`), or does an
     existing task now depend on this one (update *its* deps)? Propose the
     links; don't invent ordering that isn't real.
3. `TASKS add --title "..." [--area m] [--deps 1,2] [--desc "..."]`, then
   show the task as recorded (the script prints it).

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

## Area stewardship (inline)

Areas exist for **coordination**, not just labeling: contributors working in
disjoint areas can't conflict, so dev steers concurrent work apart. A task's
`area` may list several, comma-separated. Two tasks **collide** when their
area lists overlap; on collision with in-flight (`doing`) work **or** same-
area tasks already in `review`, interactive flows warn the user (their call
— proceed, wait, or review/land first), auto agents skip. The reserved area
**`all`** marks a task that may touch everything (wide refactors, some
umbrellas): it collides with every task, so it runs only on an otherwise-
quiet board with the user's explicit confirmation that other contributors
are paused; auto never picks it, and it's never listed in areas.md.

Areas live in `.tasks/areas.md` via `TASKS area list|set|rm`. Names:
short phrases (spaces fine, no `:` or `,`, aim ≤4 words); the one-line `--desc`
carries the rest of the meaning. `/dev area` with no args → `area list`,
flag entries that no longer match the repo structure, propose fixes. Old done
or logged tasks keep historical area names; never rewrite them.

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
--json state,mergedAt,reviewDecision`. Merged → `TASKS land <id>` (marks done
and cleans local worktree/branch; safe if already done). If land/cleanup
aborts, surface the script error to the user — do not mark done by hand.
`reviewDecision: CHANGES_REQUESTED` → the assignee's move; surface it
(resume path in flows/implement.md). `APPROVED` and unmerged → ready to land
via review. Closed unmerged → flag it; only prompt for a decision if the
current user is the assignee or integrator.

## Branch & role conventions

- Branch-per-task: `dev/<id>-<slug>` (scoped boards: `dev/<scope-with-/->-<id>-<slug>`),
  owned by the task's assignee, short-lived, rebased on the integration
  branch, merged promptly via PR.
- Merge rights: anyone except the task's assignee may review and merge; the
  integrator may review anything, including their own work.
- The `integrator` (default: board creator; `/dev config integrator <name>`)
  owns the integration branch: default reviewer, conflict arbitration,
  iteration close.
- Code branches contain code only; the board never rides them — `.tasks/`
  may appear in a feature checkout (inherited from integration); that is fine
  as long as the PR does not modify it.
