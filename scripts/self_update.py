#!/usr/bin/env python3
"""Skill self-update for the public dev skill (github.com/poiurewq/dev).

Install (public consumers): git clone into an agent skills directory, e.g.
  git clone https://github.com/poiurewq/dev.git ~/.grok/skills/dev

User prefs live outside the skill tree so they survive updates:
  ~/.config/dev-skill/config.yml

Config schema (schema_version integer, like board.yml):
  schema_version: 1
  auto_update: false          # opt-in apply on check
  check_interval_hours: 24    # throttle for check
  last_check_at: ""           # ISO-8601 UTC; empty = never

SCHEMA_VERSION in this file is what we write. Missing schema_version → 0.
Never downgrade a higher schema_version stamp; preserve unknown keys.

Commands:
  check   Throttled version check vs origin/main VERSION. Quiet if local >=
          public or network fails. One line if behind. If auto_update, apply.
  update  Ignore throttle; git pull --ff-only origin main (requires clone).
  auto    Show or set auto_update (on|off|true|false).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- constants ---

CONFIG_SCHEMA_VERSION = 1
CANONICAL_REPO = "https://github.com/poiurewq/dev"
VERSION_URL = "https://raw.githubusercontent.com/poiurewq/dev/main/VERSION"
DEFAULT_INTERVAL_HOURS = 24
CONFIG_DIR = Path.home() / ".config" / "dev-skill"
CONFIG_PATH = CONFIG_DIR / "config.yml"

# Ordered keys we always write; unknown keys preserved after these.
CONFIG_KEYS = (
    "schema_version",
    "auto_update",
    "check_interval_hours",
    "last_check_at",
)

SKILL_DIR = Path(__file__).resolve().parent.parent


# --- small YAML subset (key: value lines; no deps) ---

def parse_kv(text: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([\w][\w-]*)\s*:\s*(.*)$", line)
        if m:
            d[m.group(1)] = m.group(2).strip()
    return d


def config_schema_version(cfg: dict[str, str]) -> int:
    raw = cfg.get("schema_version", "")
    if raw == "" or raw is None:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def default_config() -> dict[str, str]:
    return {
        "schema_version": str(CONFIG_SCHEMA_VERSION),
        "auto_update": "false",
        "check_interval_hours": str(DEFAULT_INTERVAL_HOURS),
        "last_check_at": "",
    }


def read_config() -> dict[str, str]:
    if not CONFIG_PATH.is_file():
        return default_config()
    cfg = default_config()
    on_disk = parse_kv(CONFIG_PATH.read_text(encoding="utf-8"))
    # Preserve unknown keys; overlay known defaults then file values.
    for k, v in on_disk.items():
        cfg[k] = v
    v = config_schema_version(cfg)
    if v > CONFIG_SCHEMA_VERSION:
        print(
            f"warning: dev-skill config schema_version {v} is newer than "
            f"this self_update.py (supports {CONFIG_SCHEMA_VERSION}); "
            f"unknown fields may be ignored",
            file=sys.stderr,
        )
    elif v < CONFIG_SCHEMA_VERSION:
        cfg["schema_version"] = str(CONFIG_SCHEMA_VERSION)
    return cfg


def write_config(cfg: dict[str, str]) -> None:
    v = config_schema_version(cfg)
    if v < CONFIG_SCHEMA_VERSION:
        cfg["schema_version"] = str(CONFIG_SCHEMA_VERSION)
    # Never downgrade a newer stamp from a newer script.
    if v > CONFIG_SCHEMA_VERSION:
        pass  # keep their schema_version
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    known = set(CONFIG_KEYS)
    for k in CONFIG_KEYS:
        lines.append(f"{k}: {cfg.get(k, '')}")
    for k, val in cfg.items():
        if k not in known:
            lines.append(f"{k}: {val}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_bool(s: str) -> bool:
    return str(s).strip().lower() in ("1", "true", "yes", "on")


# --- versions ---

def parse_semver(s: str) -> tuple[int, int, int] | None:
    s = (s or "").strip()
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", s)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def local_version() -> str:
    path = SKILL_DIR / "VERSION"
    if not path.is_file():
        return "0.0.0"
    ver = path.read_text(encoding="utf-8").strip()
    return ver if ver else "0.0.0"


def fetch_public_version(timeout: float = 5.0) -> str | None:
    try:
        req = urllib.request.Request(
            VERSION_URL,
            headers={"User-Agent": "dev-skill-self-update"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
        return body if body else None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def cmp_semver(a: str, b: str) -> int:
    """Return -1 if a<b, 0 if equal/unparseable-equal treatment, 1 if a>b.

    Unparseable versions compare as less than parseable ones when the other
    side parses; two unparseable strings compare equal only if identical.
    """
    pa, pb = parse_semver(a), parse_semver(b)
    if pa is None and pb is None:
        if a == b:
            return 0
        return -1 if a < b else 1
    if pa is None:
        return -1
    if pb is None:
        return 1
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


# --- throttle ---

def parse_iso(ts: str) -> datetime | None:
    ts = (ts or "").strip()
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def should_skip_check(cfg: dict[str, str]) -> bool:
    last = parse_iso(cfg.get("last_check_at", ""))
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    try:
        hours = float(cfg.get("check_interval_hours") or DEFAULT_INTERVAL_HOURS)
    except ValueError:
        hours = DEFAULT_INTERVAL_HOURS
    if hours < 0:
        hours = 0
    elapsed = (now_utc() - last).total_seconds()
    return elapsed < hours * 3600


def stamp_last_check(cfg: dict[str, str]) -> None:
    cfg["last_check_at"] = now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    write_config(cfg)


# --- git apply ---

def is_skill_git_root(path: Path) -> bool:
    """True only when path itself is a git worktree root (not a subdir of another repo).

    qskill-synced installs have no .git. Public installs are a clone whose
    toplevel *is* SKILL_DIR. Developing the skill inside skills-internal must
    never be treated as an updatable install (pull would hit the monorepo).
    """
    try:
        inside = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return False
        top = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if top.returncode != 0:
            return False
        return Path(top.stdout.strip()).resolve() == path.resolve()
    except OSError:
        return False


def origin_looks_canonical(url: str) -> bool:
    """True only for github.com/poiurewq/dev (https/ssh/scp forms, optional .git).

    Rejects substring traps (poiurewq/devtools) and other owners' dev repos.
    """
    u = (url or "").strip().lower().rstrip("/")
    if u.endswith(".git"):
        u = u[: -len(".git")]
    # https://token@github.com/... or ssh://git@github.com/...
    u = re.sub(r"^(https?|ssh)://[^/@]+@", r"\1://", u)
    # After stripping userinfo, ssh URLs may be ssh://github.com/...
    if u.startswith("ssh://"):
        u = u[len("ssh://") :]
    return u in (
        "https://github.com/poiurewq/dev",
        "http://github.com/poiurewq/dev",
        "git@github.com:poiurewq/dev",
        "github.com/poiurewq/dev",
        "github.com:poiurewq/dev",
    )


def git_pull_ff(path: Path) -> tuple[bool, str]:
    """Fast-forward pull origin main. Returns (ok, message)."""
    if not is_skill_git_root(path):
        return (
            False,
            "skill dir is not a standalone git clone of poiurewq/dev; install with:\n"
            f"  git clone {CANONICAL_REPO}.git <agent-skills>/dev",
        )
    rem = subprocess.run(
        ["git", "-C", str(path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if rem.returncode != 0:
        return False, "no git remote 'origin'; set origin to poiurewq/dev and retry"
    origin_url = (rem.stdout or "").strip()
    if not origin_looks_canonical(origin_url):
        return (
            False,
            f"origin is {origin_url!r}, expected poiurewq/dev; "
            "refusing to pull a different remote",
        )
    r = subprocess.run(
        ["git", "-C", str(path), "pull", "--ff-only", "origin", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    detail = out if out else err
    if r.returncode != 0:
        return False, detail or "git pull --ff-only failed"
    return True, detail or "already up to date"


# --- commands ---

def cmd_check(*, force: bool = False) -> int:
    cfg = read_config()
    if not force and should_skip_check(cfg):
        return 0

    local = local_version()
    public = fetch_public_version()
    # Always stamp after an attempted network check (or force) so we throttle
    # even when public is unreachable — avoids hammering on offline days.
    stamp_last_check(cfg)

    if public is None:
        return 0  # network fail: silent

    if cmp_semver(local, public) >= 0:
        return 0  # local == or ahead: silent

    # Behind.
    if parse_bool(cfg.get("auto_update", "false")):
        ok, msg = git_pull_ff(SKILL_DIR)
        if ok:
            new_local = local_version()
            print(f"dev skill updated: {local} → {new_local}")
            return 0
        print(
            f"dev skill {public} available (you have {local}); "
            f"auto-update failed: {msg}",
            file=sys.stderr,
        )
        return 1

    print(
        f"dev skill {public} available (you have {local}) — "
        f"/dev update, or /dev update auto on"
    )
    return 0


def cmd_update() -> int:
    local = local_version()
    ok, msg = git_pull_ff(SKILL_DIR)
    if not ok:
        print(f"error: {msg}", file=sys.stderr)
        return 1
    new_local = local_version()
    if new_local != local:
        print(f"dev skill updated: {local} → {new_local}")
    else:
        # pull may have moved commits without VERSION change, or already current
        print(f"dev skill up to date ({new_local})")
    # Refresh throttle stamp so a following check stays quiet.
    cfg = read_config()
    stamp_last_check(cfg)
    return 0


def cmd_auto(value: str | None) -> int:
    cfg = read_config()
    if value is None or value == "":
        on = parse_bool(cfg.get("auto_update", "false"))
        print(f"auto_update: {'on' if on else 'off'}")
        return 0
    v = value.strip().lower()
    if v in ("on", "true", "yes", "1"):
        cfg["auto_update"] = "true"
    elif v in ("off", "false", "no", "0"):
        cfg["auto_update"] = "false"
    else:
        print("error: expected on|off", file=sys.stderr)
        return 1
    write_config(cfg)
    print(f"auto_update: {'on' if parse_bool(cfg['auto_update']) else 'off'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="self_update.py",
        description="Update the local dev skill from github.com/poiurewq/dev",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="throttled version check / optional auto-apply")
    c.add_argument(
        "--force",
        action="store_true",
        help="ignore check_interval_hours throttle",
    )

    sub.add_parser("update", help="git pull --ff-only origin main")

    a = sub.add_parser("auto", help="show or set auto_update")
    a.add_argument(
        "value",
        nargs="?",
        help="on|off (omit to show current)",
    )

    args = p.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(force=bool(getattr(args, "force", False)))
    if args.cmd == "update":
        return cmd_update()
    if args.cmd == "auto":
        return cmd_auto(getattr(args, "value", None))
    p.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
