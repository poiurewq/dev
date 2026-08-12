# /dev absorb <source> — import an external task list

For repos with pre-dev task lists (TODO.md, ROADMAP.md, an issues export, a
scratch file the user points at).

1. Read the source in full. Extract candidate items, running `TASKS related
   "<item>"` on each to catch ones the board already has.
2. Ask which mode the user wants (they can mix, per batch):
   - **Adjudicate now**: walk them through candidates in batches (~10 at a
     time), each with your proposed disposition — **keep** (with proposed
     title, area, deps, 1–3 sentence description), **drop** (done already /
     stale / duplicate — say which), or **modify** (needs the user's
     rewording or a scope call). The user adjudicates; add the keeps via
     `TASKS add ...` in dependency order. Dropped candidates are simply never
     added — don't add them and then mark them `not-planned`; that status is
     for tasks the board already carried.
   - **Funnel to proposed**: for users without time to review everything in
     one sitting — add each plausible item as `TASKS add ... --status
     proposed` with your best-guess title/area/deps, pausing only for items
     too unclear to record faithfully. They queue in the review inbox
     (flows/review.md) for adjudication later; obvious junk (duplicates,
     clearly-done items) may be skipped, but say what was skipped and why.
3. Offer to retire the source file (delete it, or replace its contents with a
   one-line pointer to the board) so the board becomes the single source of
   truth — that edit is a normal code change on a branch, not a board write.
   The user decides.
