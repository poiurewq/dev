# /dev implement <id[, id…]|goal> — triage, build, ship

**One task:** follow steps 0–7 below.

**Several tasks** (explicit multi-id, or "all under umbrella …"): read
`flows/implement-batch.md` and follow it — do not load that file for a
single id. `/dev auto` stays one task per cycle.

0. **Gate**: `gh auth status` must succeed — otherwise stop and point at
   setup (flows/init.md step 5). Don't start work that can't be shipped.

   **Resolve** the argument to a task id (or a batch — then
   `flows/implement-batch.md`) before preflight:
   - **Explicit id** (digits, or multi-id list): `TASKS show` / load each.
     Unknown id → stop and say so; do **not** treat a bare number as a
     freeform goal to add.
   - **Goal**: `TASKS list`, match on title/body; confirm if not obvious.
     Clear match → that task. Ambiguous → ask. **No match** → **file first**:
     run the full `/dev add` path on that text (`flows/add.md`), and
     mention once that implement filed new work. Direct add → continue
     resolve/preflight on the new id, with no second confirmation to start
     building (the user already asked to implement). Plan or shape flow →
     after the user approves and tasks are filed, continue implement on them
     (several → `flows/implement-batch.md`); if that path stops without
     filing, stop.

   Read the resolved task's body and any `Decision:` lines. Then **preflight**
   its current state:
   - `backlog`/`planned`, unassigned or assigned to this user → proceed.
     Picking first is not required; the claim in step 2 assigns it.
   - Assigned to **someone else** → stop and say who owns it; proceed only
     if the user explicitly reassigns (`TASKS update <id> --assignee <who>`).
     A review handoff stays `review` — do not claim.
   - `doing` → someone (possibly this user, in another session) is on it;
     confirm before touching it.
   - `review` assigned to this user → this is the resume path (bottom).
   - `proposed` → not yet approved; route through flows/review.md first.
   - `needs: decision` → the fork must be decided first (flows/review.md);
     don't implement around an open question.
   - `done` → say so and stop.
   - `not-planned` → show the recorded reason and stop; proceed only if the
     user revives it (`--status backlog`).
   - `later` → parked for a future iteration; stop. Proceed only if the user
     revives it (`--status backlog` or `/dev pick <id>`).

   If the task now looks pointless (already solved, superseded, false
   premise), say so before writing code — the user chooses whether to drop
   it.

   Then three pre-claim checks, **always**:
   - **Area sanity**: empty area is a hard stop — do not claim, and do
     not run collisions yet (`claim` refuses; untagged tasks skip
     collisions). From `TASKS area list`, recommend a reuse and/or a
     new name (`area set`); no placeholder. Wait for the user. On
     their call, `area set` if new, then `TASKS update <id> --area
     "..."` and `Decision:`. Only then continue to collisions. If
     they decline to name it, stop. If the recorded area is set but
     no longer fits what this task will actually touch (the codebase
     may have shifted since declaration), surface it; on the user's
     approval, update `--area` before collisions.
   - **Area collision**: `TASKS collisions <id>` (SKILL.md *Area
     stewardship*). Exit 2 → print the output and **stop** — do not claim.
     Any assignee's `doing` or `review` in an overlapping area counts,
     including this user's other work. Resume of the same id is clear
     (self is excluded). Resume of a `Dev-batch:` member must pass the
     whole set (resume path below) — a single id false-aborts on
     same-area siblings still in review. An `all` task additionally
     requires an otherwise-quiet board **and** the user's explicit
     confirmation that other contributors are paused; the board can't
     see teammates' terminals, so the user vouches. Multi-id / batch:
     use `TASKS collisions <id,id,…>` for the whole set (see
     `flows/implement-batch.md`) — never the single id, or in-set
     review after an earlier ship would false-abort.
   - **Local integration ahead**: `TASKS preflight` (fetches and reports).
     Exit 0 = clear, or ahead only on out-of-scope paths (soft note already
     printed). Exit 2 = in-scope ahead: stop. The working tree on integration
     must be clean before acting. Offer only: **(1) Park as PR** —
     `TASKS preflight --park`; **(2) Discard** — confirm first, then
     `TASKS preflight --discard`. Re-run `TASKS preflight`; continue only
     once it exits 0. If `--park` prints a WARNING that `gh pr create`
     failed, surface it (exit is still 0; local integration is already
     clear). Do not hand-roll fetch/log/reset/park git, and do not offer a
     loud "proceed anyway." Behind-only is fine — task branches use
     `origin/<integration>`.

1. **Triage scope** before touching code:
   - **Right-sized** (≈ one focused PR): proceed to step 2.
   - **Oversized**: decompose instead of implementing. **One layer at a
     time**: for very large scopes, prefer a few mid-sized subtasks — which
     may themselves later turn out to be umbrellas — over exhaustively
     enumerating every leaf now. Draft the subtasks (deps where real) and
     present them; on the user's approval, add them and convert the original
     into an umbrella: `TASKS update <id> --kind umbrella --deps <new-ids>
     --status backlog --append "Decomposed into T<ids>; this task now
     verifies the overall goal end-to-end."` Then stop, or start on the first
     subtask if the user says so. (Auto files the subtasks as `proposed`
     instead — flows/auto.md.)

2. **Claim**: `TASKS claim <id>` (optional `--assignee`, `--branch`), which
   owns the whole branch/worktree/board setup and prints `workdir` (git
   worktree root) and `product` (where to edit — same as workdir on a root
   board, `<workdir>/<scope>` on a scoped board). An existing branch is
   reused if it is at or ahead of origin/<integration>; an empty leftover
   (no unique commits) is fast-forwarded. A leftover that has diverged is
   a stop: surface the claim error, then show what is unique on that
   leftover (`git log --oneline origin/<integration>..<name>` and
   `git diff --stat origin/<integration>...<name>`) plus leftover
   worktree dirty status (`git status --porcelain` in that worktree, if
   any) and ask keep vs throw away (throw-away closes any open PR
   with `gh pr close`, then deletes the leftover). Do not write
   `branch:` before the user chooses.
   **Keep:** if the leftover worktree is dirty, commit or stash first
   (`TASKS restack` refuses dirty). Then `TASKS update <id> --branch
   <name>` only if the task has no `branch:` (needed after un-claim
   cleared it; restack only reads the task file), then `TASKS restack
   --ids <id> --onto origin/<integration>`, then `TASKS claim <id>`
   again.
   **Throw away:** refuse if the leftover worktree is dirty (same as
   `TASKS cleanup` — do not force-wipe uncommitted work). Otherwise
   `gh pr close` any open PR on that head (`gh pr list --head <name>
   --state open` — `git push origin --delete` does not close it, and
   the next claim recreates the same name so the old PR would
   reattach), then delete the leftover (worktree if any, local branch,
   and `git push origin --delete <name>` if the remote branch exists —
   otherwise the next claim recreates it and refuses again) and re-claim.
   `TASKS cleanup` will not drop the branch without a merged PR.
   Heed unfinished-deps warnings: stop and tell the user unless told
   otherwise. (Multi-task batch: within-set deps — see
   `flows/implement-batch.md`.) Do **not** hand-roll `git branch` /
   `git worktree add` / board `update`. The one exception is the
   throw-away leftover delete above (`gh pr close`, `git worktree
   remove`, local branch delete, `git push origin --delete <name>`).

3. **Branch**: already done by `claim`. Confirm the printed `base:` is the
   origin/integration ref you expect (claim verifies it; do not assume).
   Session cwd stays on the hub. Edit, compile, test, and run product
   files from the printed `product` dir (`cd` or the command's
   working-directory flag) — a green result on the hub is integration,
   not the branch. Do not wrap those commands in TASKS.

4. **Implement.** Match existing conventions. **Design forks — surface,
   don't decide**: where the task is silent on a choice that repo conventions
   don't settle (data shape, API surface, library, error semantics, naming,
   sync/async), name the fork, the options, and the tradeoff you'd weigh, and
   ask the user before writing code down that path. Record the answer with
   `TASKS update <id> --append "Decision: <choice + one-line why>"`. If you
   hit a non-obvious pitfall, or discover a convention or decided rule
   that isn't obvious from the code, add a one-line invariant to the
   nearest `AGENTS.md` (SKILL.md *Vendor-neutral*).

   **Leaving the stated areas**: Before touching a file the task's
   area(s) do not cover (`TASKS area list` descriptions + SKILL.md
   *Area stewardship* path convention), pause. Name the file(s) and
   the area they belong to. Do not write those files yet. A
   `Decision:` already on the body for this widen is prior approval
   — say you are retrying it; do not re-ask. Otherwise ask (auto:
   leaving-areas fork, flows/auto.md). Decline → stay inside; no
   Decision. On approval: `area set` if the name is new, then
   `TASKS update <id> --area "…"` (add the specific child, not the
   parent) and `Decision:` (files + areas) if this is the first
   approval, then `TASKS collisions <id>` (batch: the whole set).
   Exit 0 → touch those files. Exit 2 → revert `--area` to the
   previous set, keep the `Decision:` (`widen to X for <files>;
   last blocked by T…`), stop — do not touch those files, do not
   set `needs: decision`. In-area work may continue. Next
   implement retries that Decision (still blocked → revert if you
   updated, leave the Decision, stop). Widening to `all` still
   needs the quiet-board vouch.

   **Mid-flight scope changes** (high bar for re-triage). If the user expands
   the ask while you are implementing:
   - **In-place (default):** still one focused unit — clarifications,
     adjacent edge cases, small extras that fit this PR. Update the task
     (`--append`, or title/desc if needed) and continue.
   - **Re-triage (drastic only):** clearly multi-PR, a second independent
     goal, or a new product area folded into this task. Pause implement; hand
     the *expanded* ask through `/dev add` triage (sibling tasks vs plan or
     shape). Keep or narrow the current task to its original unit — do not
     absorb the expansion. After triage, offer to resume implement on the
     current task (user decides). When unsure whether the expansion is
     drastic, prefer in-place.

5. **Self-review**: `TASKS diff <id>` — it resolves the task worktree
   (session cwd stays on the hub) and prints location plus the review
   diff. Never ambient `git diff` / `git status`; an empty hub tree is
   not a clean self-review. Re-read that output with fresh eyes: crash
   risks, unintended file touches, scope creep, leftover debug code,
   convention drift. If the diff left the stated areas, route
   through **Leaving the stated areas** above (files already
   written: same path; decline or collisions exit 2 → revert those
   files and `--area` if updated). Fix what you find. If a deeper review tool exists
   in this environment, use it.

6. **Ship**: `TASKS ship <id> --shipped "<what actually shipped>"` from the
   printed `product` dir (optional `--message`, `--title`, `--body`,
   `--version-intent`, `--base <parent-branch>` when stacking). It owns
   commit/push/PR-create and the move to `status=review`; idempotent if the
   PR is already open, and errors on an empty ship. Do **not** hand-roll any
   of those steps.

   **Shipped record** — `--shipped` is required; ship refuses without it. One
   or two sentences on **the result, not the plan**: what actually landed,
   including where it diverged from the task's intent and anything knowingly
   left out. It is appended to the task body as `Shipped (<date>): …`
   (durable in `.tasks/`, visible to `TASKS show`, survives iteration-close)
   and mirrored onto the PR body on create and on every re-ship. Don't
   restate the title or paraphrase the description back — a record that only
   echoes the intent is the thing this exists to prevent.

   **Version intent** — only when the product versions releases (its docs say
   whether and how; assume no scheme or file). Never edit version files on
   the task branch: the agent owns the line, passing `--version-intent none|…`
   or a `Version intent: …` line in `--body`. Values are `none`, a non-major
   step the product uses (agent decides), or `major` (breaking — interactive
   asks the user first; auto flags `needs: decision` instead of shipping).
   The actual bump happens at merge into integration (flows/review.md).
   A Dev-batch or stack: one bump sized for the whole set after the last
   member lands (flows/review-batch.md), not one bump per PR.

7. Report: what changed, anything risky, the PR link. **If `whoami` is the
   board `integrator`**, end with one line: open a **new session** for
   `/dev review <id>` — do not continue review here (implement context
   pollutes judgment). Hint only; never force a session boundary or
   auto-start review. Non-integrator implementers: normal handoff is enough.

**Resuming after changes were requested** on the PR: `TASKS diff <id>` first
(re-orients to the worktree; a review branch with no local checkout
attaches `origin/<branch>`). Then `TASKS collisions` with the task's
`Dev-batch:` ids (the single id if none) — exit 2 is outside occupancy,
stop; batch peers in review must not false-abort. Then the same flow
from step 4 on the existing branch; fix on the branch, `TASKS ship <id>
--shipped "<what changed since the last ship>"`, then note on the PR
what changed (`gh pr comment`). The re-ship record covers the fixes,
not the whole task again — the earlier records stay.
