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
     run the full `/dev add` path on that text (SKILL.md *Adding work*), and
     mention once that implement filed new work. Direct add → continue
     resolve/preflight on the new id, with no second confirmation to start
     building (the user already asked to implement). Plan flow → after the
     user approves and tasks are filed, continue implement on them (several →
     `flows/implement-batch.md`); if plan stops without filing, stop.

   Read the resolved task's body and any `Decision:` lines. Then **preflight**
   its current state:
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

   Then four pre-claim checks, **always**:
   - **Area sanity**: does the recorded area still fit what this task will
     actually touch? (The codebase may have shifted since declaration.) If
     not, surface it; on the user's approval, `TASKS update <id> --area
     "<better>"` before proceeding.
   - **Area collision**: `TASKS list --status doing`, applying the collision
     rule from SKILL.md *Area stewardship*. Warn the user on any overlap with
     another contributor's task — proceed or wait is their call. An `all`
     task additionally requires an otherwise-quiet board **and** the user's
     explicit confirmation that other contributors are paused; the board
     can't see teammates' terminals, so the user vouches.
   - **Same-area review**: `TASKS list --status review`, same rule. Surface
     overlapping tasks with their `pr` links and suggest reviewing/landing
     them first — proceed or switch to `/dev review` is the user's call.
     Board fields only; do not enumerate GitHub PRs outside the board.
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
   owns the whole branch/worktree/board setup and prints the `workdir` to
   work in. Idempotent if branch and worktree already exist. Heed
   unfinished-deps warnings: stop and tell the user unless told otherwise.
   (Multi-task batch: within-set deps — see `flows/implement-batch.md`.) Do
   **not** hand-roll `git branch` / `git worktree add` / board `update`.

3. **Branch**: already done by `claim`. Confirm you are in the printed
   `workdir` on the task branch before editing.

4. **Implement.** Match existing conventions. **Design forks — surface,
   don't decide**: where the task is silent on a choice that repo conventions
   don't settle (data shape, API surface, library, error semantics, naming,
   sync/async), name the fork, the options, and the tradeoff you'd weigh, and
   ask the user before writing code down that path. Record the answer with
   `TASKS update <id> --append "Decision: <choice + one-line why>"`. If you
   hit a non-obvious pitfall, add a one-line gotcha to the nearest
   `AGENTS.md`.

   **Mid-flight scope changes** (high bar for re-triage). If the user expands
   the ask while you are implementing:
   - **In-place (default):** still one focused unit — clarifications,
     adjacent edge cases, small extras that fit this PR. Update the task
     (`--append`, or title/desc if needed) and continue.
   - **Re-triage (drastic only):** clearly multi-PR, a second independent
     goal, or a new product area folded into this task. Pause implement; hand
     the *expanded* ask through `/dev add` triage (sibling tasks vs plan
     flow). Keep or narrow the current task to its original unit — do not
     absorb the expansion. After triage, offer to resume implement on the
     current task (user decides). When unsure whether the expansion is
     drastic, prefer in-place.

5. **Self-review**: re-read the full diff (`git diff <integration>...HEAD`)
   with fresh eyes: crash risks, unintended file touches, scope creep,
   leftover debug code, convention drift. Fix what you find. If a deeper
   review tool exists in this environment, use it.

6. **Ship**: `TASKS ship <id>` from the claim workdir (optional `--message`,
   `--title`, `--body`, `--version-intent`, `--base <parent-branch>` when
   stacking). It owns commit/push/PR-create and the move to `status=review`;
   idempotent if the PR is already open, and errors on an empty ship. Do
   **not** hand-roll any of those steps.

   **Version intent** — only when the product versions releases (its docs say
   whether and how; assume no scheme or file). Never edit version files on
   the task branch: the agent owns the line, passing `--version-intent none|…`
   or a `Version intent: …` line in `--body`. Values are `none`, a non-major
   step the product uses (agent decides), or `major` (breaking — interactive
   asks the user first; auto flags `needs: decision` instead of shipping).
   The actual bump happens at merge into integration (flows/review.md).

7. Report: what changed, anything risky, the PR link. **If `whoami` is the
   board `integrator`**, end with one line: open a **new session** for
   `/dev review <id>` — do not continue review here (implement context
   pollutes judgment). Hint only; never force a session boundary or
   auto-start review. Non-integrator implementers: normal handoff is enough.

**Resuming after changes were requested** on the PR: same flow from step 4 on
the existing branch; fix on the branch, `TASKS ship <id>`, then note on the PR
what changed (`gh pr comment`).
