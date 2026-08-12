# /dev auto — one autonomous implement cycle

For cron/scheduled or parallel agent sessions with no human present. One
cycle = at most one shipped implementation (plus any proposals filed along
the way). Multiple agents may run this concurrently against the same board —
the claim step is the mutex.

**Identity**: the assignee is the model, prefixed: `auto/<model-id>` (e.g.
`auto/claude-sonnet-5`). Pass it explicitly via `--assignee`; don't overwrite
a human's product-local `<scope>/.dev/identity`. If this product has no
identity file, write the auto identity there so script defaults stay sane.
Run from the product directory or pass `--scope`.

**Hard limits**: never merge or approve a PR, resolve a `needs: decision`
fork, flip `proposed` to `backlog`, mark a task `not-planned`, delete one, or
otherwise decide for the human — a pointless-looking task gets `needs:
decision` with your reasoning instead. Auto files work and questions; humans
dispose of them via flows/review.md. Never park, discard, or reset the local
integration branch — that is human-only (flows/implement.md preflight).

## Cycle

1. **Gate**: `gh auth status` must succeed, else exit reporting why. Then
   `TASKS preflight` (check only — never `--park` / `--discard` in auto).
   Exit 2 (in-scope ahead): **stop the cycle** — report the script output and
   that a human must park-as-PR or discard via interactive implement; do not
   claim a task. Exit 0 with out-of-scope-only ahead → soft note in the
   report, continue.
2. **Select**: from `TASKS list --json`, candidates are `backlog` tasks,
   unassigned, no `needs` flag, all deps `done`. Exclude on **area grounds**
   (collision rule: SKILL.md *Area stewardship*): skip candidates overlapping
   another contributor's `doing` task or any `review` task — surface the
   overlapping review ids/`pr` in the skip report, board fields only, not a
   GitHub PR sweep — and never select an `all` task (those wait for a
   human-supervised quiet board). **Area sanity**: if the recorded area is
   clearly wrong for what the code now requires, fix it and note why in one
   call (`TASKS update <id> --area "<better>" --append "<why>"`); if the
   right area is genuinely debatable, treat it as a design fork (flag
   `needs: decision`, move on). Then triage for **fork risk**: read the task
   body and skim the code it touches; prefer tasks that are mechanical or
   fully pinned down (clear scope, `Decision:` lines already present,
   established patterns). If nothing suitable exists, report that and stop —
   don't force a risky task.
3. **Claim**: `TASKS claim <id> --assignee auto/<model>`. If the script
   errors (e.g. a concurrent claim race on board push), resync and select
   again. Use the printed `product` dir (workdir is the git worktree root).
4. **Triage scope** (flows/implement.md step 1, including its
   one-layer-at-a-time rule — mid-sized subtasks are fine): if oversized,
   file the subtasks as proposals rather than adding them live — check
   `TASKS related "<title>"` first and skip ones the board already has, then
   `TASKS add ... --status proposed`, each body noting "decomposes T<id>:
   <why>". Then convert the original into an umbrella and un-claim it:
   `TASKS update <id> --kind umbrella --deps <new-ids> --status backlog
   --assignee "" --branch "" --append "Decomposed into proposed T<ids>;"`,
   and return to step 2 (at most once per cycle).
5. **Implement** per flows/implement.md steps 3–6, with one difference —
   **fork handling**: on hitting a genuine design fork, do not pick. Write
   the fork into the task body (question, options, your recommendation), then
   flag and un-claim: `TASKS update <id> --needs decision --status backlog
   --assignee "" --branch "" --append "<the fork write-up>"`, discard the
   branch, and return to step 2. Only flag *genuine* forks — choices repo
   conventions already settle don't count. (Local-ahead was already cleared
   or soft-noted in step 1 — do not re-triage park/discard here.)
6. **Ship** via `TASKS ship <id> --shipped "<what actually shipped>"` (ends at
   `--status review --pr <url>`). The shipped record is required — result,
   not plan; see flows/implement.md step 6.
   When the product versions, pass `--version-intent` (or the line in
   `--body`) per flows/implement.md — the script does not default it. If the
   change is major/breaking, do not ship — flag `needs: decision` like other
   forks. Then stop and summarize what was shipped, proposed, or flagged
   (this lands wherever the invoking automation routes reports). If `whoami`
   is the integrator and something shipped to `review`, add one line: review
   in a **new** session via `/dev review <id>` (same implement-context rule
   as flows/implement.md step 7).
