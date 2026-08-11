# /dev — coordinated development for humans and agents

A shared task board that lives inside your repo, plus a workflow around it
built for teams of humans *and* AI agents. Every task is a small file under
`.tasks/`, synced through git — so every contributor, on any machine or
branch, sees the same board.

Public source: https://github.com/poiurewq/dev

## Install

Clone into the skills directory of the agent product you use (examples):

```bash
git clone https://github.com/poiurewq/dev.git ~/.grok/skills/dev
# or
git clone https://github.com/poiurewq/dev.git ~/.claude/skills/dev
```

If you use more than one agent, clone once per skills root (or only into the
one you care about — updates apply to the install that is running).

Requires: `git`, `python3`, and for board workflows `gh` (GitHub CLI)
authenticated against the target repo.

## Getting started

In a repo (with at least one commit and a GitHub `origin`):

```text
/dev
```

That walks you through identity + board setup. Then `/dev help` for the
full command map.

## Keep the skill updated

The skill tracks `main` on this repo. A throttled check runs on common `/dev`
entry points and prints one quiet line when you are behind.

| Command | Effect |
|---|---|
| `/dev update` | `git pull --ff-only` in the skill install |
| `/dev update auto on` | opt-in: checks may apply updates |
| `/dev update auto off` | default — notify only |

Prefs (survive pulls): `~/.config/dev-skill/config.yml`.

## Layout (this repo)

```
SKILL.md           # agent-facing router
VERSION            # semver (one line)
flows/             # multi-step procedures
scripts/tasks.py   # board state API
scripts/self_update.py
```
