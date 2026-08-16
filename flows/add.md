# /dev add <task-or-goal> — sit with the ask, then file

Covers `/dev add <task-or-goal>`, freeform new work, and the `/dev plan`
alias. One entry point; sit with the ask, then triage.

## Premise (before filing)

Sit with the ask before choosing a path (SKILL.md *User specs are one
guess*). What is the underlying problem? Does the proposed shape solve it?
Any deeper consequences? If you need context to judge that, gather it: the
codebase first; online when the ask would add or change the stack or you
cannot see enough locally. If you see a better approach, say so — the
product beats the wording. If the problem is unclear, ask. Real concerns
only; do not invent nits. Push-back is conversation; if you raised one,
wait for a direction before triaging. The task body is only what the user
accepted. Then triage.

## Triage

Do not ask the user which path. Decide:

| Path | When |
|---|---|
| **Direct add** | One right-sized unit (≈ one focused PR): clear outcome, few design forks, or a single "consider X" item. Continue with **Direct add** below. |
| **Plan flow** | Several deliverables already visible, real ordering across pieces, or an iteration-level / multi-PR goal. Read `flows/plan.md`. |
| **Shape** | Many threads must be cut *together* before tasks are knowable — new product, first iteration, or a large new subsystem. Read `flows/shape.md`. |

**Grey area → prefer direct add** (under-specify). Implement still decomposes
oversized tasks; plan is for breakdown the user needs *now*, not every
large-sounding phrase. Shape is rarer than plan: only when the cut itself
is not yet knowable. After triage, do the work and mention the path once
("filed as one task" / "breaking into tasks" / "shaping this onto the board").

## Direct add

1. Draft title, area (`TASKS area list`; reuse before inventing — greenfield
   rule: no areas until ~3 tasks cluster), and a 1–3 sentence description
   from what the user said. Do not add non-goals, constraints, or design
   choices they did not state (ground rule *Do not manufacture
   specifications*).
2. **Check the board first**: `TASKS related "<title + description>"`.
   - Substantially the same task exists → say so and propose amending it
     (`TASKS update <id> ...`) instead of adding a duplicate. The user
     decides; add anyway if they want them separate.
   - Otherwise judge the neighbours it lists for **dependencies in both
     directions** — must something else land first (`--deps`), or does an
     existing task now depend on this one (update *its* deps)? Propose the
     links; don't invent ordering that isn't real.
3. `TASKS add --title "..." [--area m] [--deps 1,2] [--desc "..."]`, then
   show the task as recorded (the script prints it).
