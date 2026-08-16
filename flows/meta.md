# /dev meta [id|area] — pressure the board

`/dev board` shows the plan. `/dev meta` presses it. Not a namespace:
no `meta` subcommands. An optional noun is a **focus** (this task,
umbrella, or area), not a child verb. Freeform "does this board still
make sense?" routes here.

**Reserved:** `area` and `areas` are not a board-area focus. `/dev
meta area` (or `areas`) skips the sitting and runs only **Area
validity** — cheaper, so it can run more often. A real area name
(`flows`, …) is still a sitting focus.

This is not a codebase scan and not `/dev review` (inbox of pending
judgments). Glance at code only when a task's *premise* might be false.

## Sitting

Read the board (`TASKS board`, `--expand` if needed), umbrella
bodies, `TASKS area list`. `log.md` only if the current board is
thin. If they named a focus, start there.

Do not park work as `later` or pull later work into the current cut.
That is an operational team call — the iteration name does not tell
you what belongs now.

Follow attention. Do not open with a tour of axes. Open with whatever
looks unstable — a task that only makes sense if an unstated choice is
true, a missing or fake dep, two tasks that contradict, an umbrella
whose children don't add up to its goal, a stale area name.

Topics the sitting may notice (not a checklist, not sibling verbs):
assumptions, deps, umbrellas, stale areas.

Speak in their frame. Same posture as review: propose concrete board
edits (retitle, split, merge, add a dep, `needs: decision`,
not-planned, area rename). They say apply, or keep talking.
Mutations only on explicit go-ahead, via `TASKS`. Auto never
runs this.

## Area validity

`/dev meta area` (or `areas`) is this pass alone. A sitting also
runs it when attention lands on areas — they asked "just the
areas," focused a named area, or names look wrong. It no longer
lives on `/dev area`.

Flag entries that no longer match the repo structure **or** that
should be path-renamed under the naming rule in SKILL.md *Area
stewardship*: drop filename extensions; a residual dumped on a
path-parent should be `…/other`; `/` does not belong on a conceptual
name; an unlisted parent is not assignable. Propose renames/fixes
and, on user OK, apply — `area set` the new name, rewrite **open**
tasks' `area` fields, then `area rm` the old name. Done, not-planned,
and logged tasks keep historical area strings; never bulk-rewrite those.

`/dev area` with no args is list-only (`TASKS area list`).
