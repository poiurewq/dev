# /dev skill ... — installed skill, not the board

Everything under `/dev skill` acts on the **installed skill**, never on the
user's board or repo. Keep the two apart when you speak: `/dev review` is
feedback on *their* work; `/dev skill feedback` is feedback on *this tool*.

Public installs are a **git clone** of https://github.com/poiurewq/dev into an
agent skills dir. Prefs live at `~/.config/dev-skill/config.yml` (survives
pulls; `schema_version` like board.yml — see `scripts/skill.py`).

| Invocation | Script |
|---|---|
| `/dev skill` | `SKILL_CMD status` |
| `/dev skill update` | `SKILL_CMD update` |
| `/dev skill update auto` | `SKILL_CMD auto` (report current setting) |
| `/dev skill update auto on/off` | `SKILL_CMD auto on/off` |
| `/dev skill feedback <text>` | `SKILL_CMD feedback --title "<t>" [--body "<b>"]` |

The throttled `SKILL_CMD check` when-list stays in SKILL.md — it runs on
common board commands, not only here.

## Feedback to the maintainers (`/dev skill feedback`)

Opens an issue on poiurewq/dev — the maintainer inbox for the skill itself.
Not for the user's own repo, and not a way to file board tasks (that is
`/dev add <task-or-goal>`); if the user seems to mean their own work, ask
before filing.

Draft a title (one line) and body (the behaviour they saw and what they
expected) from what the user said. **Show the draft and get their OK before
running the command**, and say plainly what it does: files a GitHub issue on
the public poiurewq/dev repo, which cannot be quietly undone. The script
appends skill version, install kind, python, and OS. Never put repo names,
paths, branch names, or task content in the title or body — this is a public
repo. Report the issue URL the script prints.
