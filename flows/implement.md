# /dev implement <id[, id…]|goal> — triage, build, ship

**One task:** follow steps 0–7 below.

**Several tasks** (explicit multi-id, or “all under umbrella …”): read
`flows/implement-batch.md` and follow it — do not load that file for a
single id. `/dev auto` stays one task per cycle.

0. **Gate**: `gh auth status` must succeed — otherwise stop and point at
   setup (flows/init.md step 5). Don't start work that can't be shipped.

   **Resolve** the argument to a task id (or a batch — then
   `flows/implement-batch.md`) before preflight:
   - **Explicit id** (digits, or multi-id list): `TASKS show` / load each.
     Unknown id → stop and say so; do **not** treat a bare number as a
     freeform goal to add.
   - **Goal**: `TASKS list`, match on title/body; confirm if not
     obvious. Clear match → that task. Ambiguous → ask. **No match** →
     **file first** (below), then continue this step on the new id(s).

   **File first** (implement goal, nothing on the board matches):
   run the full `/dev add` path on that text as the task-or-goal — same
   triage as SKILL.md *Adding work* (direct add vs plan; `TASKS related`;
   deps judgment; only `TASKS` mutates the board). Mention once that
   implement filed new work.
   - **Direct add** → one new id; continue resolve/preflight on it (no
     second confirmation to start building — the user already asked to
     implement).
   - **Plan flow** → `flows/plan.md`; after the user approves and tasks
     are filed, continue implement on those tasks (several →
     `flows/implement-batch.md`). If plan stops without filing, stop.

   Read the resolved task's body and any `Decision:` lines. Then
   **preflight** its current state:
   - `backlog`/`planned`, unassigned or assigned to this user → proceed.
     Picking first is not required; the claim in step 2 assigns it.
   - Assigned to **someone else** → stop and say who owns it; proceed only
     if the user explicitly reassigns.
   - `doing` → someone (possibly this user, in another session) is on it;
     confirm before touching it.
   - `review` with this user's PR → this is the resume path (bottom).
   - `proposed` → not yet approved; route through flows/review.md first.
   - `needs: decision` → the fork must be decided first (flows/review.md);
     don't implement around an open question.
   - `done` → say so and stop.
   - `not-planned` → show the recorded reason and stop; proceed only if the
     user revives it (`--status backlog`).

   If the task now looks pointless (already solved, superseded, false
   premise), say so before writing code — the user chooses whether to drop
   it.

   Then three pre-claim checks, **always**:
   - **Area sanity**: does the recorded area still fit what this task will
     actually touch? (The codebase may have shifted since declaration.) If
     not, surface it; on the user's approval, `TASKS update <id> --area
     "<better>"` before proceeding.
   - **Area collision**: check in-flight work (`TASKS list --status doing`).
     Two tasks collide when their area lists overlap (`all` overlaps
     everything). On a collision with another contributor's task, warn the
     user — proceed or wait is their call. A task whose area is `all`
     additionally requires an otherwise-quiet board (nothing in `doing`)
     **and** the user's explicit confirmation that other contributors are
     paused — the board can't see teammates' terminals, so the user vouches.
   - **Same-area review**: check `TASKS list --status review`. If any
     review task's area list overlaps the candidate's (same collision rule,
     including `all`), surface those tasks and their `pr` links when set,
     and suggest reviewing/landing them first. Warn — proceed or switch to
     `/dev review` is the user's call. Board fields only (`status`, `area`,
     `pr`); do not enumerate GitHub PRs outside the board.

   - **Local integration ahead** (before claim): `git fetch origin`. Let
     `I` be the board's integration branch (`TASKS config`). If local `I`
     is **ahead of** `origin/I`, list `origin/I..I` (commits + paths).
     **In-scope** = any changed path under the board's scope prefix
     (scoped board `dev` → `dev/…`; root board → any path).
     - **Any in-scope path**: stop. Working tree on `I` must be clean
       first (uncommitted changes: stash/commit/elsewhere — don't mix).
       Offer only: **(1) Park as PR** — put *all* ahead commits on one
       branch (`park/…`), `git push -u`, `gh pr create --base I` (plain
       git + gh; no board task), then reset local `I` to `origin/I` so
       main is no longer ahead; or **(2) Discard** — confirm, then reset
       local `I` to `origin/I`. Re-check; only continue once local `I` is
       not ahead (or ahead only on out-of-scope paths). Do not offer a
       loud "proceed anyway."
     - **Ahead but only out-of-scope paths**: one soft note; continue.
     - Not ahead: continue. (Behind-only is fine — task branches use
       `origin/I`.)

1. **Triage scope** before touching code:
   - **Right-sized** (≈ one focused PR): proceed to step 2.
   - **Oversized**: decompose instead of implementing. **One layer at a
     time**: for very large scopes, prefer a few mid-sized subtasks — which
     may themselves later turn out to be umbrellas and get decomposed in a
     future sitting — over exhaustively enumerating every leaf task now.
     Draft the subtasks (deps where real) and present them; on the user's
     approval, add them (`TASKS add ...`) and convert the original into an
     umbrella: `TASKS update <id> --kind umbrella --deps <new-ids>
     --status backlog --append "Decomposed into T<ids>; this task now
     verifies the overall goal end-to-end."` Then stop, or
     start on the first subtask if the user says so. (Auto mode files the
     subtasks as `proposed` instead — flows/auto.md.)

2. **Claim**: `TASKS update <id> --status doing --assignee <me> --branch
   dev/<id>-<slug>` (slug: kebab-case from the settled title; scoped boards
   prefix the scope with `/`→`-`, e.g. `dev/internal-dev-3-<slug>`). Heed
   the blocked-deps warning: stop and tell the user unless told otherwise.
   (Multi-task batch: within-set unfinished deps — see
   `flows/implement-batch.md`.)

3. **Branch**: create the branch from `origin/<integration>` (already
   fetched in preflight). If the current worktree has unrelated uncommitted
   work or sits on another task's branch, don't disturb it — `git worktree
   add <scope>/.dev/worktrees/<id>-<slug> <branch>` (root board: `.dev/worktrees/…`)
   and work there.

4. **Implement.** Match existing conventions. **Design forks — surface,
   don't decide**: where the task is silent on a choice that repo
   conventions don't settle (data shape, API surface, library, error
   semantics, naming, sync/async), name the fork, the options, and the
   tradeoff you'd weigh, and ask the user before writing code down that
   path. Record the answer with `TASKS update <id> --append "Decision:
   <choice + one-line why>"`. If you hit a
   non-obvious pitfall, add a one-line gotcha to the nearest `AGENTS.md`.

   **Mid-flight scope changes** (high bar for re-triage). If the user
   expands the ask while you are implementing:
   - **In-place (default):** still one focused unit — clarifications,
     adjacent edge cases, small extras that fit this PR. Update the task
     (`--append`, or title/desc if needed) and continue.
   - **Re-triage (drastic only):** clearly multi-PR, a second independent
     goal, or a new product area folded into this task. Pause implement;
     hand the *expanded* ask through `/dev add` triage (sibling tasks vs
     plan flow). Keep or narrow the current task to its original unit —
     do not absorb the expansion. After triage, offer to resume implement
     on the current task so focus returns here (user decides). When unsure
     whether the expansion is drastic, prefer in-place.

5. **Self-review**: re-read the full diff (`git diff <integration>...HEAD`)
   with fresh eyes: crash risks, unintended file touches, scope creep,
   leftover debug code, convention drift. Fix what you find. If a deeper
   review tool exists in this environment, use it.

6. **Ship**: commit(s) prefixed `[T<id>]`, push, `gh pr create --base
   <integration-branch> --title "[T<id>] <title>"`, body a few sentences.
   **Version intent** (when the product versions releases — product docs say
   whether/how; no assumed scheme or file): do **not** edit version files on
   the task branch. Put one line in the PR body:
   `Version intent: none` | a non-major step the product uses (agent picks) |
   `major` (breaking). Non-major: agent decides. Major/breaking: interactive
   asks the user first; auto flags `needs: decision` instead of shipping.
   The actual bump happens at merge into integration (flows/review.md).
   Then `TASKS update <id> --status review --pr <url>`.

7. Report: what changed, anything risky, the PR link. **If `whoami` is the
   board `integrator`**, end with one line: open a **new session** for
   `/dev review <id>` — do not continue review here (implement context
   pollutes judgment). Hint only; never force a session boundary or
   auto-start review. Non-integrator implementers: normal handoff is enough.

**Resuming after changes were requested** on the PR: same flow from step 4 on
the existing branch; address the review comments, push, and note on the PR
what changed (`gh pr comment`).
