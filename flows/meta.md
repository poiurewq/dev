# /dev meta [id|area] — pressure the board

`/dev board` shows the plan. `/dev meta` presses it. Not a namespace:
no `meta` subcommands. An optional noun is a **focus** (this task,
umbrella, or area), not a child verb. Freeform "does this board still
make sense?" routes here.

This is not a codebase scan and not `/dev review` (inbox of pending
judgments). Glance at code only when a task's *premise* might be false.

## Sitting

Read the board (`TASKS board`, `--expand` if needed), the later
column, umbrella bodies, `TASKS area list`. `log.md` only if the
current board is thin. If they named a focus, start there.

Follow attention. Do not open with a tour of axes. Open with whatever
looks unstable — a task that only makes sense if an unstated choice is
true, a missing or fake dep, a later item the current cut already
needs, two tasks that contradict, an umbrella whose children don't add
up to its goal, a stale area name.

Topics the sitting may notice (not a checklist, not sibling verbs):
assumptions, later vs now, deps, umbrellas, stale areas.

Speak in their frame. Same posture as review: propose concrete board
edits (retitle, split, merge, flip to `later`, revive, add a dep,
`needs: decision`, not-planned, area rename). They say apply, or keep
talking. Mutations only on explicit go-ahead, via `TASKS`. Auto never
runs this.

## Area validity

When attention lands on areas — they asked "just the areas," focused
an area, or names look wrong — run this pass (it no longer lives on
`/dev area`):

Flag entries that no longer match the repo structure **or** that
should be path-renamed under the naming rule in SKILL.md *Area
stewardship*: drop filename extensions; a residual dumped on a
path-parent should be `…/other`; `/` does not belong on a conceptual
name; an unlisted parent is not assignable. Propose renames/fixes
and, on user OK, apply — `area set` the new name, rewrite **open**
tasks' `area` fields, then `area rm` the old name. Done, not-planned,
and logged tasks keep historical area strings; never bulk-rewrite those.

`/dev area` with no args is list-only (`TASKS area list`).
