# /dev review [id[, id…]] — the unified inbox

Three kinds of pending human judgment live on the board. You draft; the user
decides. Never approve, merge, resolve a fork, or accept a proposal without
an explicit go-ahead from the user in this conversation.

**Integrator only by comparison.** Treat or address the user as integrator
only when `TASKS whoami` equals `TASKS config integrator`. Do not infer it
from merge rights, from running review, or from a land. Do not tell a
non-match they are the integrator — including in wrap-up.

**Plain language first.** For every PR, fork, or proposal — in the inbox list
and when diving into one item — open with one plain sentence of what the task
is for: the goal from the task body, not file-level nits or a long PR recap.

**Several task ids** (or "all under umbrella …"): read
`flows/review-batch.md` and follow it — two-phase review-all then ordered
land; do not load that file for a single independent PR.

## `/dev review` (no id) — show the inbox

After a PR state refresh (SKILL.md):
1. **Implementation PRs**: tasks in `review` with a `pr` — each with one
   plain sentence of purpose, then whether the current user may land
   (comparison above holds → yes; otherwise review/approve only — do
   not `TASKS land`). Mention integrator only when the comparison
   holds. Ones already at `CHANGES_REQUESTED` are the
   assignee's move, not a review item. When several share a `Dev-batch:`
   line (or stack bases), group them and prefer `/dev review <ids…>` over
   landing one-by-one.
2. **Design forks**: `TASKS list --needs decision` — one plain sentence of
   purpose, then the open question in one line.
3. **Proposed tasks**: `TASKS list --status proposed` — one plain sentence
   of purpose each; group by umbrella/goal if noted in their bodies.

Let the user pick what to handle; batch small decisions in one sitting.

**Several open PRs (no Dev-batch / no stack):** if the comparison above
does not hold, do not land — approve if asked, then stop. Otherwise pick
a land order with the user (deps, area overlap, risk). Do **not** resolve
pairwise conflicts against sibling task branches (`git merge-tree`
between heads, etc.). `TASKS land <id>` one, then the next (land rebases
onto the updated integration branch as needed) — repeat. **When a
Dev-batch or stack applies**, `flows/review-batch.md` supersedes this
path.

## Single-id gate (before reviewing one implementation PR)

Run `TASKS batch-gate --ids <id>`. Exit 2 means open batch co-members are
missing — surface the output and **stop** (hard refuse); tell the user to
re-run with the full required set. Exit 0 → continue below.

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
     `gh pr review --approve` (optional audit trail); if the author **is**
     the current user, **skip** it — GitHub rejects self-approves. If the
     comparison above does not hold, **stop after approve** — do not run
     `TASKS land`; only the integrator can land. Otherwise run
     `TASKS land <id>`, which owns destack of immediate stacked children
     onto integration (before the parent is rewritten), restack of
     deeper descendants onto those rewritten parents, rebase, merge,
     cleanup and the move to `done`; never hand-roll any of it. Land does **not** auto-approve, and refuses if
     the PR base is not the board integration branch. Run it from the
     primary clone or product dir — not from inside the task worktree it
     will remove. Idempotent if the PR is already merged; `TASKS cleanup
     <id>` alone clears leftover local state, deleting the task branch only
     when the PR is verified MERGED and never touching a dirty worktree. If
     land prints a **Version intent** other than `none`, apply one bump
     on the integration branch when this set is done (a single PR is a
     set of one) per the product's versioning docs (not on the task
     branch). Session cwd is already the product on the hub — land
     prints `product: <abs>`; apply those docs there with
     product-relative paths (do not prefix the board scope). A
     Dev-batch or stack: sized for the whole set
     (flows/review-batch.md).
     **Always surface the script's full output**: on non-zero exit or any
     early abort (`error: …`), show the message and stop — do not retry
     with hand-rolled git/gh, and do not claim the task landed.
   - **Request changes**: concise and actionable; the task stays in `review`.
     If the PR author is **not** the current user:
     `gh pr review --request-changes --body <agreed comments>`. If the author
     **is** the current user, GitHub also blocks that — post the same body
     with `gh pr comment` (or `gh pr review --comment`) instead, then fix on
     the branch (resume path in flows/implement.md).

## Deciding a design fork (`needs: decision`)

1. `TASKS show <id>` — the body carries the fork, options, and the filing
   agent's recommendation. Open with one plain sentence of what the task is
   for, then present the fork as a one-question decision.
2. On the user's call, record it and return the task to the pool:
   `TASKS update <id> --needs "" --status backlog --append "Decision: <choice
   + one-line why>"`. If the fork assigned an area, include `--area` on
   that update (`area set` first if the name is new).
3. If the resolution is "don't do this at all", that's a not-planned outcome
   instead: `TASKS update <id> --needs "" --status not-planned --reason
   "<the user's why>"`.

## Adjudicating proposed tasks

1. Walk the user through them (batch by goal): for each, one plain sentence
   of purpose, then keep / modify / drop.
2. Keep → `TASKS update <id> --status backlog` (plus any edits). Intended
   but not this iteration → `--status later`. If the proposal decomposes a
   parent task, wire membership on that parent:
   `TASKS update <parent> --kind umbrella --deps <accepted-ids>` (merge with
   any existing deps) and note the decomposition in its body (`--append`).
3. Drop → offer both: `TASKS update <id> --status not-planned --reason
   "<why>"` (keeps the record, stops auto re-filing the same proposal) or
   `TASKS delete <id>` for noise. The user picks.
