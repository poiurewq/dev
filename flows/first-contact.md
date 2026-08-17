# /dev (bare) and /dev help — first contact

Covers bare `/dev` and `/dev help`. Do not improvise the welcome or the
options table.

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

From board state (and a light PR refresh on `review` tasks if useful; the
refresh lives in SKILL.md), emit only the lines that apply — short command
shapes, ids filled in when known. Skip categories that don't apply; do not
pad toward a full menu.

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
