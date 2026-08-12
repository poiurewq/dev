# /dev init — identity, board creation, adoption

## Step 0 — welcome (first contact only)

When the user arrived via bare `/dev` or `/dev help` and this repo has no
board, open with the following **verbatim** — don't paraphrase, don't extend;
only trim a section if the user has made clear they already know it:

---
**Welcome to dev** — a shared task board that lives inside your repo, plus a
workflow around it built for teams of humans *and* AI agents.

**What it is.** Every task is a small file under `.tasks/`, synced through
git — so every contributor, on any machine or branch, sees the same board.
Agents can work the board in parallel without stepping on each other.

**How it works.** You declare tasks (or higher-level goals — dev breaks them
down). Contributors pick tasks up, each on its own branch, and everything
lands through a PR. Reviews, design decisions, and approvals always come
back to a human: agents draft, you decide.

**The philosophy.** Minimal ceremony. A task is a few sentences, not a spec
— details get worked out at implementation time, and real design questions
are surfaced to you instead of silently guessed. The tooling handles the
process (branching, reviews, attribution, board hygiene) so nobody has to
remember it.

Want me to set it up here? It takes about a minute — a name for attribution
and a couple of choices about branches — and you can add your first task
right after.
---

If they accept, continue below. If they decline, point at `/dev help` for
later and stop — don't set anything up unasked.

Works from whatever branch the user happens to be on: board writes go through
the script's hidden worktree, never the checkout.

Prerequisites: at least one commit (the script pins its board worktree to the
integration branch's tip — on a brand-new repo, make an initial commit first)
and a GitHub remote named `origin`. Without a remote, board commits just queue
locally and PRs are impossible, so implement and review can't run; help the
user add one before going further.

1. **Identity**: ask for a short stable handle (suggest one derived from
   `git config user.name`). Identity is product-local
   (`<scope>/.dev/identity`). Skip if that file already exists for the target
   product and the user isn't asking to change it. In a monorepo, each
   product board gets its own identity (same handle is fine — re-run init per
   product).

2. **Scope** (monorepo support): if the repo hosts multiple products and the
   user wants a board for a subdir, confirm which one and pass
   `--scope <subdir>`. Default (no `--scope`) is the nearest board at/above
   cwd, else the repo root (create path only). Day-to-day commands require
   cwd under the product or `--scope`. Don't create scoped boards unprompted
   — one root board is the norm for single-product repos.

3. **Existing board** (`.tasks/board.yml` present for that scope): just run
   `TASKS init --name <handle>` — it joins and registers the contributor.
   Then check gh (step 5). Done.

4. **No board — adoption**:
   - *Integration branch*: propose the current or default branch; if the repo
     already uses iteration branches, ask which. Pass `--integration` (and
     `--parent`, `--iteration` when applicable).
   - Run `TASKS init --name <handle> [--scope s] [--integration b] [--parent p]`.
   - *Areas*: first give the user this primer (verbatim or near):

     > Areas are coarse labels for regions of the codebase. Every task
     > gets tagged with the area(s) it will touch. They keep the board
     > organized — but their real job is coordination: dev steers
     > contributors away from working in the same area at the same time,
     > which is what prevents merge conflicts. More areas enable more
     > concurrent work; there is no fixed upper bound. The natural
     > limiter is multi-area occupancy — propose as many stable areas as
     > enable parallelization without typical tasks needing many areas at
     > once (over-granularity → confusing multi-area tags).
     >
     > Prefer names that mirror the tree when the work splits by path:
     > use a repo-relative path, or just the shortest uniquely
     > distinguishing portion (e.g. `cli` if only one, `billing/api` when
     > several `api` dirs exist). No trailing slashes and no leading
     > `./`. Cross-cutting work that is not a directory still gets a short
     > phrase (`docs`, `ci`). Areas can be added later when tasks start
     > clustering.

     Then: **brownfield** — scan the top-level structure and propose areas
     with that parallelization heuristic and path-preferring names (no hard
     count cap), each `TASKS area set <name> --desc "<one line>"`, user
     trims/confirms first. **Greenfield** — none yet; they appear later per
     the rule above.
   - *Existing task lists* (TODO.md, ROADMAP.md, issues dump): offer
     `/dev absorb <file>` (flows/absorb.md).

5. **gh setup** (PR is the only merge flow):
   - `gh` missing → point at https://cli.github.com (`brew install gh` on
     macOS); `gh auth status` failing → walk them through `gh auth login`.
   - Init still completes without it, but say plainly: implement and review
     stay unavailable until `gh auth status` succeeds.

6. **Happy path for product code** (say once, briefly): land work via a task
   branch and PR into the integration branch — don't pile long-lived feature
   commits only on local integration. Implement starts task branches from
   `origin/<integration>`, so unpushed local-main commits get in the way
   (flows/implement.md will ask to park them as a PR or discard). Board state
   still goes only through `TASKS`, never hand-edited.

7. Report what was set up: scope, integration branch, integrator, areas, gh
   status.
