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

With more than one agent, clone once per skills root, or just the one you use
— updates apply to the install that is running.

Requires: `git`, `python3`, and for board workflows `gh` (GitHub CLI)
authenticated against the target repo.

## Getting started

In a repo (with at least one commit and a GitHub `origin`):

```text
/dev
```

That walks you through identity + board setup. Then `/dev help` for the
full command map. After init, `./board` (from the product directory) is a
live view — `r` refresh, `a` toggle by-area, `q` quit, arrows scroll, type a task id + Enter
to see whether it is area-blocked by current doing/review work.

## The skill itself (`/dev skill`)

`/dev skill ...` acts on the installed skill; every other command acts on
your board. The skill tracks `main` on this repo, and a throttled check on
common `/dev` entry points prints one quiet line when you are behind.

| Command | Effect |
|---|---|
| `/dev skill` | version, auto-update state, these commands |
| `/dev skill update` | `git pull --ff-only` in the skill install |
| `/dev skill update auto on` | opt-in: checks may apply updates |
| `/dev skill update auto off` | default — notify only |
| `/dev skill feedback <text>` | file a GitHub issue on this repo — bugs and ideas about the skill |

Prefs (survive pulls): `~/.config/dev-skill/config.yml`.

`/dev skill feedback` opens a public GitHub issue on this repo via `gh`; your
agent shows you the draft first, and only skill version and platform are
attached — no repo names, paths, or task content.

## Layout (this repo)

```
SKILL.md           # agent-facing router
VERSION            # semver (one line)
flows/             # multi-step procedures
scripts/tasks.py   # board state API
scripts/skill.py   # status / update / feedback
```
