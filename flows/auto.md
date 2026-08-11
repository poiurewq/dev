# /dev auto — one autonomous implement cycle

For cron/scheduled or parallel agent sessions with no human present. One
cycle = at most one shipped implementation (plus any proposals filed along
the way). Multiple agents may run this concurrently against the same board —
the claim step is the mutex.

**Identity**: assignee is the model, prefixed: `auto/<model-id>` (e.g.
`auto/claude-sonnet-5`). Pass it explicitly via `--assignee`; don't overwrite
a human's product-local `<scope>/.dev/identity`. If this product has no
identity file, write the auto identity there so script defaults stay sane.
Run from the product directory or pass `--scope`.

**Hard limits**: never merge or approve a PR, never resolve a `needs:
decision` fork, never flip `proposed` to `backlog`, never mark a task
`not-planned` or delete one (a pointless-looking task gets `needs: decision`
with your reasoning instead), never decide for the human. Auto files work and questions; humans dispose of them via
flows/review.md. Never park, discard, or reset the local integration
branch — that is human-only (flows/implement.md local-ahead preflight).

## Cycle

1. **Gate**: `gh auth status` must succeed, else exit reporting why.
   Then `git fetch origin` and run the **local integration ahead** check
   from flows/implement.md (in-scope path rule). If local integration is
   ahead on any **in-scope** path: **stop the cycle** — report commits /
   paths and that a human must park-as-PR or discard via interactive
   implement; do not claim a task. Ahead only out-of-scope → soft note in
   the report, continue.
2. **Select**: from `TASKS list --json`, candidates are `backlog` tasks,
   unassigned, no `needs` flag, all deps `done`. Exclude on **area
   grounds**: skip any candidate whose area list overlaps the areas of
   another contributor's `doing` task **or** any `review` task (same
   collision rule; surface the overlapping review task ids/`pr` in the
   skip report — board fields only, not a GitHub PR sweep), and never
   select a task whose area is `all` (those wait for a human-supervised
   quiet board). **Area sanity**: if the recorded area is clearly wrong
   for what the code now requires, fix it and note why in one call
   (`TASKS update <id> --area "<better>" --append "<why the area changed>"`); if the right area is genuinely debatable, treat
   it as a design fork (flag with `needs: decision`, move on). Then triage
   for **fork risk**:
   read the task body and skim the code it touches; prefer tasks that are
   mechanical or fully pinned down (clear scope, `Decision:` lines already
   present, established patterns to follow). If nothing suitable exists,
   report that and stop — don't force a risky task.
3. **Claim**: `TASKS update <id> --status doing --assignee auto/<model>
   --branch <task-branch>`. If the script errors with a rebase conflict,
   another agent claimed simultaneously — resync and select again.
4. **Triage scope** (flows/implement.md step 1, including its
   one-layer-at-a-time rule — mid-sized subtasks are fine): if oversized,
   file the subtasks as proposals instead of adding them live: check `TASKS
   related "<title>"` first and skip ones the board already has, then `TASKS
   add ... --status proposed`, each body noting "decomposes T<id>: <why>".
   Then convert the original into an umbrella and un-claim it:
   `TASKS update <id> --kind umbrella --deps <new-ids> --status backlog
   --assignee "" --branch "" --append "Decomposed into proposed T<ids>;"`,
   and return to step 2 (at most once per cycle).
5. **Implement** per flows/implement.md steps 3–6, with one difference —
   **fork handling**: on hitting a genuine design fork, do not pick. Write
   the fork into the task body (question, options, your recommendation),
   flag and un-claim:
   `TASKS update <id> --needs decision --status backlog --assignee ""
   --branch "" --append "<the fork write-up>"`, discard the branch, and return to
   step 2. Only flag *genuine* forks — choices repo conventions already
   settle don't count. (Local-ahead was already cleared or soft-noted in
   step 1 — do not re-triage park/discard here.)
6. **Ship** ends at `--status review --pr <url>` as usual (include Version
   intent per flows/implement.md; if the change is major/breaking, do not
   ship — flag `needs: decision` like other forks). Then stop — summarize
   what was shipped, proposed, or flagged (this lands wherever the invoking
   automation routes reports). If `whoami` is the integrator and something
   shipped to `review`, one line in that summary: review in a **new**
   session via `/dev review <id>` (same implement-context rule as
   flows/implement.md step 7).
