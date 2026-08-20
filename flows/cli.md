# TASKS flag wall

Full invocation patterns. SKILL.md keeps the short index and the append /
error caveats; load this file when a flag is not already spelled in
SKILL.md or the flow you are following. Do not guess flags.

```
TASKS --scope <subdir> <subcommand> ...   # target another board in the repo
                                          # (goes BEFORE the subcommand)
TASKS init --name <handle> [--scope <subdir>] [--integration <branch>]
           [--parent <branch>] [--iteration N] [--iteration-name <name>]
           [--iteration-started YYYY-MM-DD]
TASKS whoami                            # this checkout's identity (exits
                                        # nonzero if not set)
TASKS config [<key> [<value>]]          # settable: integrator, parent_branch,
                                        # iteration (renumber live index;
                                        # refused if that n is archived),
                                        # iteration_name, iteration_started
                                        # (all three refused once
                                        # closed-not-landed)
TASKS area list
TASKS area set <name> [--desc "<one-line scope>"]
TASKS area rm <name> [--force]
TASKS add --title "<title>" [--area <m>] [--deps <id,id>]
          [--desc "<1–3 sentences>"] [--assignee <who>]
          [--kind umbrella]                     # empty = normal
          [--status proposed|backlog|planned|later]  # default backlog
TASKS update <id> [--title "<t>"] [--area <m>] [--status <s>]
          [--kind umbrella|""] [--assignee <who>|""] [--branch <b>|""]
          [--pr <url>] [--needs decision|""] [--deps <id,id>]
          [--append "<paragraph>"]              # add to body, keeping it
          [--desc "<new body>"]                 # REPLACE whole body
          [--status later]                      # park for a later iteration
          [--status not-planned --reason "<why>"]   # reason is required
TASKS delete <id>
TASKS show <id>
TASKS collisions <id[,id…]>             # area occupancy vs doing/review;
                                        # multi-id also prints in-set overlap;
                                        # exit 2 if any id is in-flight-blocked
                                        # (batch peers excluded from that check)
TASKS related "<text>"                  # existing tasks similar to <text>;
                                        # run before every add
TASKS list [--assignee <who>] [--status <s>] [--needs decision] [--json]
TASKS board [--expand] [--by-area] [--watch]
                                        # index: one line per status (or
                                        # area) of task ids; then in-play
                                        # tasks one per line, umbrella
                                        # children indented under the
                                        # parent; done/later/not-planned
                                        # fold to a count, --expand lists
                                        # those three; --watch: r/a/e/q, arrows
                                        # scroll, type id↵ for area
                                        # collisions (./board)
TASKS iteration
TASKS iteration-close [--force]
TASKS iteration-new <branch> [--parent <branch>] [--name <name>]
                    [--iteration N] [--iteration-started YYYY-MM-DD]
TASKS iteration-land [--create-only] [--title T] [--body B]
                                        # open/merge iteration PR into parent
                                        # with merge commit (not squash)
TASKS claim <id> [--assignee <who>] [--branch <b>]
                                        # branch from origin/integration
                                        # (ff empty leftover; refuse if
                                        # diverged or untagged), always a linked
                                        # worktree under .dev/worktrees
                                        # (primary stays hub), status=doing;
                                        # prints workdir + product + verified
                                        # base (edit + compile/test/run in product)
TASKS diff <id>                         # location + review diff from the
                                        # task worktree (not session cwd);
                                        # implement self-review / resume
                                        # (review: attaches origin/<branch>
                                        # if this clone has no checkout)
TASKS ship <id> --shipped "<what actually shipped>"
                [--message M] [--title T] [--body B]
                [--version-intent <intent>] [--base <branch>]
                [--batch <id,id,…>]     # commit if dirty ([n/T<id>] prefix),
                                        # push, gh pr create if none open,
                                        # status=review + pr URL. Re-ship
                                        # reuses the open PR, so --title /
                                        # --body / --version-intent / --base
                                        # apply on create only.
                                        # --shipped is REQUIRED on every ship
                                        # (result, not plan): appended to the
                                        # task body as Shipped (<date>): … and
                                        # mirrored onto the PR body each ship.
                                        # --version-intent has no default;
                                        # --base stacks; --batch stamps
                                        # Dev-batch on PR + task body
TASKS batch-gate --ids <id,id,…>        # exit 2 if selection omits open
                                        # co-members of a Dev-batch/stack
TASKS restack --ids <id,id,…> [--after N] [--onto <ref>]
              [--retarget] [--dry-run]  # fail-closed stack rebase (plan,
                                        # then apply unless --dry-run);
                                        # auto-retargets PR base to
                                        # integration when the stack parent
                                        # is outside the set; --onto rebases
                                        # every target onto that ref (no
                                        # cascade — the default keeps in-set
                                        # stack parents); moving onto a
                                        # new base excludes the old parent
                                        # tip (no duplicate replay)
TASKS preflight [--park|--discard]      # local integration ahead of origin
                                        # (check exits 2 if in-scope ahead;
                                        # --park / --discard are interactive)
TASKS land <id>                         # integrator-only merge commit (never
                                        # squash, never rewrites the branch):
                                        # merge, retarget stacked children to
                                        # integration, cleanup, done
TASKS cleanup <id>                      # worktree + branch prune (branch only if PR MERGED)
```
