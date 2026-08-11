# /dev review [id] — the unified inbox

Three kinds of pending human judgment live on the board. You draft; the user
decides. Never approve, merge, resolve a fork, or accept a proposal without
an explicit go-ahead from the user in this conversation.

**Plain language first.** For every PR, fork, or proposal (inbox list and
when diving into one item), open with one plain sentence of what the task is
for — goal from the task body, not file-level nits or a long PR recap.

## `/dev review` (no id) — show the inbox

After a PR state refresh (SKILL.md):
1. **Implementation PRs**: tasks in `review` with a `pr` — each with one
   plain sentence of purpose, then note which the current user may merge
   (anyone except the task's assignee; the integrator may merge anything,
   including their own). Ones already at `CHANGES_REQUESTED` are the
   assignee's move, not a review item.
2. **Design forks**: `TASKS list --needs decision` — one plain sentence of
   purpose, then the open question in one line.
3. **Proposed tasks**: `TASKS list --status proposed` — one plain sentence
   of purpose each; group by umbrella/goal if noted in their bodies.

Let the user pick what to handle; batch small decisions in one sitting.

**Several open PRs:** pick a land order with the user (deps, area overlap,
risk). Do **not** resolve pairwise conflicts against sibling task branches
(`git merge-tree` between heads, etc.). `TASKS land <id>` one, then land the
next (land rebases onto the updated integration branch as needed) — repeat.

## Reviewing an implementation PR

1. `gh pr diff <url>` (and `gh pr view` for description/comments). Review
   seriously: correctness, fidelity to the task's scope, convention drift,
   test coverage. Use the environment's review tooling if available.
2. Present: one plain sentence of what the task is for, then findings + a
   recommendation (approve / request changes). The user may ask for another
   round, edit or drop findings, or add their own — the posted review must
   say what the user wants said.
3. On the user's verdict:
   - **Approve & land**: conversational go-ahead in this chat is the human
     decision. If the PR author is **not** the current user, also
     `gh pr review --approve` (optional audit trail). If the author **is**
     the current user, **skip** `gh pr review --approve` — GitHub rejects
     self-approves. After go-ahead, run `TASKS land <id>` (do not hand-roll
     merge/rebase/cleanup). It rebases onto integration if needed (via the
     task worktree or a temp worktree — never by switching the primary
     clone's checkout), waits for GitHub `mergeable` after push, then
     squash-merges with retry on transient not-mergeable / CONFLICTING,
     removes the task worktree/local branch, prunes remote-tracking, and
     marks the task `done`. Idempotent if the PR is already merged
     (`TASKS cleanup <id>` alone for leftover local state; cleanup deletes
     the task branch only when the PR is verified MERGED, and skips dirty
     worktrees). Land does **not** auto-approve. Land refuses if the PR
     base is not the board integration branch. If land prints a **Version
     intent** other than `none`, apply that bump on the integration branch
     after merge per the product's versioning docs (not on the task
     branch). Run land from the primary clone or product dir — not from
     inside the task worktree it will remove. **Always surface the script's
     full output** (stdout and stderr): on non-zero exit or any early abort
     (`error: …`), show the message to the user and stop — do not retry
     with hand-rolled git/gh, and do not claim the task landed.
   - **Request changes**: concise and actionable; task stays in `review`.
     If the PR author is **not** the current user:
     `gh pr review --request-changes --body <agreed comments>`. If the
     author **is** the current user, GitHub also blocks that — post the
     same body with `gh pr comment` (or `gh pr review --comment`) instead,
     then fix on the branch (resume path in flows/implement.md).

## Deciding a design fork (`needs: decision`)

1. `TASKS show <id>` — the body carries the fork, options, and the filing
   agent's recommendation. Open with one plain sentence of what the task is
   for, then present the fork as a one-question decision.
2. On the user's call: record the decision and return the task to the pool:
   `TASKS update <id> --needs "" --status backlog --append "Decision: <choice
   + one-line why>"`.
3. If the fork's resolution is "don't do this at all", that's a
   not-planned outcome instead: `TASKS update <id> --needs "" --status
   not-planned --reason "<the user's why>"`.

## Adjudicating proposed tasks

1. Walk the user through them (batch by goal): for each, one plain sentence
   of purpose, then keep / modify / drop.
2. Keep → `TASKS update <id> --status backlog` (plus any edits). If the
   proposal decomposes a parent task, wire membership on that parent:
   `TASKS update <parent> --kind umbrella --deps <accepted-ids>` (merge with
   any existing deps) and note the decomposition in its body (`--append`).
3. Drop → offer both: `TASKS update <id> --status not-planned --reason
   "<why>"` (keeps the record, stops auto re-filing the same proposal) or
   `TASKS delete <id>` for noise. The user picks.
