# Plan flow — break a goal into board tasks

Entry: **only** when `/dev add` triage picks the plan path (SKILL.md
**Adding work**); `/dev plan <goal>` is a legacy alias using the same triage.
The user states a higher-level goal; you tease out just enough shape to break
it into board tasks. Lighter than a spec workflow: a handful of questions,
not a cross-examination, and no standing spec document — the umbrella task's
body carries the goal.

1. **Short interview** — at most ~5 questions, and only those the goal leaves
   genuinely open. Candidates: what does "done" look like for this iteration?
   What's explicitly out of scope? Any constraints (compat, deadline,
   dependencies on other work)? Which areas of the code does the user expect
   this to touch? Stop asking as soon as you can draft a sensible breakdown —
   under-specified is the preferred failure mode, and implementation-time
   fork-surfacing catches the rest.

2. **Draft the breakdown**: a handful of tasks with few-sentence descriptions
   and deps where ordering is real. Right-sized (≈ one focused PR) when the
   goal permits; for very large goals, a few **mid-sized** tasks are fine —
   each gets decomposed in its own future sitting rather than fleshing out
   fifty leaves now. Plus one **umbrella task** — titled after the user's
   goal, body = the goal in a few sentences + interview takeaways, deps on
   all the subtasks, `kind: umbrella`, scope = "verify the goal is met
   end-to-end". Area-tag each task; a task may list several comma-separated
   areas, and umbrellas often span areas — tag them with the union (or `all`
   only if verification genuinely touches everything, since `all` waits for a
   fully quiet board). Nested goals later can be umbrellas whose children
   include other umbrellas; set `kind: umbrella` on each parent layer.

3. **Check the board**: `TASKS related "<title + desc>"` per drafted task.
   Fold anything already on the board into the existing task rather than
   duplicating it, and wire up deps the neighbours imply (either direction).

4. **Propose, don't commit**: show the full breakdown in-conversation; the
   user edits/approves. Only then add: subtasks first (`TASKS add ...`, ids
   come back sequentially), umbrella last with
   `--kind umbrella --deps <subtask-ids>`.

5. Show the resulting board section (`TASKS board`).

The umbrella is what makes coverage checkable: iteration close blocks on it
like any unfinished task, so "the board eventually covers the goal" is
enforced by the normal mechanics, not by a document audit.
