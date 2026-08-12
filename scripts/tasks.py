#!/usr/bin/env python3
"""Task-board CRUD for the `dev` skill.

A repo can hold one board (at <root>/.tasks/) or several (a monorepo with
<subdir>/.tasks/ per product). The script targets the nearest board at or
above cwd, or an explicit --scope; there is no single-board fallback.
Checkout-local state lives under the product root: <scope>/.dev/ (root board
→ .dev/ at the primary clone root) — identity, boards cache, board worktree,
and task worktrees. Paths resolve via the primary clone (git-common-dir), not
a linked task worktree's toplevel, so board mutations work from task trees.
Each board lives ONLY on its integration branch. Mutations go through a
hidden worktree at <product>/.dev/board on a private branch (_dev-board or
_dev-board-<scope>), commit there, and push to the integration branch. When
several product boards share one integration branch, push uses rebase/retry
(policy A). Code branches never commit to .tasks/.

Stdlib + git for board CRUD. Git/gh lifecycle helpers: `claim` (implement
setup), `land` / `cleanup` (post-approval merge). Never auto-approve reviews.
"""
import argparse
import datetime
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

STATUSES = ["proposed", "backlog", "planned", "doing", "review", "done",
            "not-planned"]
# Statuses a task can rest in forever: they don't block an iteration close.
TERMINAL = ("done", "not-planned")
STATUS_LABEL = {"not-planned": "Not planned"}
TASKS_DIR = ".tasks"
# On-disk board schema. Bump when board.yml / task frontmatter / .tasks layout
# changes in a way future scripts must detect. Missing schema_version on disk
# means 0 (pre-stamp boards). Never downgrade a higher version on write.
SCHEMA_VERSION = 1
CONFIG_KEYS = ["schema_version", "integration_branch", "parent_branch",
               "iteration", "integrator", "contributors"]
SETTABLE_KEYS = ["parent_branch", "integrator"]
# kind: optional. "" = normal task; "umbrella" = goal parent whose deps are
# direct children (leaves or nested umbrellas). Hierarchy lives in deps;
# reverse index is computed at board time. Other kind values reserved for
# future (e.g. recurring) — readers ignore unknown kinds.
FIELDS = ["id", "title", "area", "status", "kind", "assignee", "branch", "deps",
          "pr", "needs", "created"]
# Board push races on a shared integration branch: rebase onto origin and
# retry this many times before queueing locally.
PUSH_RETRIES = 5
# Land: rebase+push+merge retries when GitHub reports not-mergeable / behind.
LAND_RETRIES = 5
# After push/rebase, GitHub often leaves mergeable=UNKNOWN while recomputing.
# Poll before gh pr merge so the first attempt is not a guaranteed failure.
LAND_MERGEABLE_TIMEOUT_S = 30
LAND_MERGEABLE_POLL_S = 1.0


def sh(args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
    if check and r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        sys.exit(f"error running {' '.join(args)}:\n{err}")
    return r


def git(*args, cwd=None, check=True):
    return sh(["git", *args], cwd=cwd, check=check)


def gh(*args, cwd=None, check=True):
    return sh(["gh", *args], cwd=cwd, check=check)


def worktree_root(cwd=None):
    """Root of the current git worktree (where the user is standing)."""
    return git("rev-parse", "--show-toplevel", cwd=cwd).stdout.strip()


def repo_root():
    """Primary clone / main worktree root — not a linked task worktree.

    Board mutator paths, product .dev/, and git worktree add must use this so
    board commands work when cwd is inside a task worktree. Scope discovery
    still walks the *current* worktree (see find_scope / nearest_scope).
    """
    r = git("rev-parse", "--path-format=absolute", "--git-common-dir",
            check=False)
    if r.returncode == 0:
        common = r.stdout.strip()
    else:
        # Git < 2.31: no --path-format
        common = git("rev-parse", "--git-common-dir").stdout.strip()
        if not os.path.isabs(common):
            common = os.path.abspath(common)
    common = os.path.normpath(common)
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    # Bare or unusual layout: fall back to this worktree's toplevel.
    return worktree_root()


def has_remote(root):
    return git("remote", "get-url", "origin", cwd=root, check=False).returncode == 0


def ref_exists(root, ref):
    return git("rev-parse", "--verify", "--quiet", ref, cwd=root, check=False).returncode == 0


def tdir(scope):
    """Board dir relative to repo root ('.tasks' or '<scope>/.tasks')."""
    return os.path.normpath(os.path.join(scope, TASKS_DIR))


def product_root(root, scope):
    """Directory that owns the board: git toplevel for scope '.', else
    <toplevel>/<scope>. `root` is the primary clone (repo_root)."""
    if scope in (".", ""):
        return root
    return os.path.join(root, scope)


def board_branch_name(scope):
    """Private worktree branch for this product's board mutator. Distinct per
    scope so multiple product boards can each hold a worktree in one clone."""
    if scope in (".", ""):
        return "_dev-board"
    return "_dev-board-" + scope.replace("/", "-").replace("\\", "-")


def board_worktree_rel(scope):
    """Path of the board worktree relative to the git toplevel."""
    if scope in (".", ""):
        return os.path.join(".dev", "board")
    return os.path.join(scope, ".dev", "board")


# ---------- local (per-product, per-checkout, gitignored) state ----------

def dev_dir(root, scope):
    d = os.path.join(product_root(root, scope), ".dev")
    os.makedirs(d, exist_ok=True)
    return d


def read_local(root, scope, name):
    p = os.path.join(product_root(root, scope), ".dev", name)
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip()
    return None


def write_local(root, scope, name, value):
    with open(os.path.join(dev_dir(root, scope), name), "w") as f:
        f.write(value + "\n")


def read_cache(root, scope):
    p = os.path.join(product_root(root, scope), ".dev", "boards.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


def write_cache(root, scope, branch):
    c = read_cache(root, scope)
    c[scope] = branch
    with open(os.path.join(dev_dir(root, scope), "boards.json"), "w") as f:
        json.dump(c, f, indent=1)


SCOPE_OVERRIDE = None  # set from --scope in main()


def discover_boards(root):
    """All board scopes visible in the checkout (shallow scan for board.yml)."""
    scopes = set()
    for pat in (".tasks", "*/.tasks", "*/*/.tasks", "*/*/*/.tasks"):
        for p in glob.glob(os.path.join(root, pat, "board.yml")):
            d = os.path.dirname(os.path.dirname(p))
            rel = os.path.relpath(d, root)
            scopes.add(rel)
    return sorted(scopes)


def find_scope(root):
    """Board to target: --scope override, else nearest board at/above cwd.
    No single-board fallback — cwd under the product or --scope is required.

    Walk the *current* worktree so a task worktree still resolves scope
    `dev` (not a path under .dev/worktrees/…). Board paths use primary
    `root` (repo_root)."""
    if SCOPE_OVERRIDE:
        scope = os.path.normpath(SCOPE_OVERRIDE)
        if scope != "." and (os.path.isabs(scope) or scope.startswith("..")):
            sys.exit("error: --scope must be a subdir path relative to the repo root")
        return scope
    # Prefer the worktree containing cwd (task worktrees are linked trees).
    try:
        wt = worktree_root()
    except SystemExit:
        wt = root
    s = nearest_scope(wt)
    if s is not None:
        return s
    # cwd outside any worktree but under primary: walk primary
    if os.path.realpath(wt) != os.path.realpath(root):
        s = nearest_scope(root)
        if s is not None:
            return s
    boards = discover_boards(root)
    if boards:
        sys.exit("error: no board at or above cwd; boards exist at: "
                 f"{', '.join(boards)}. cd into one or pass --scope <subdir>")
    return None


def nearest_scope(walk_root):
    """Nearest board at or above cwd within walk_root (checkout board.yml)."""
    root_abs = os.path.realpath(walk_root)
    cur = os.path.realpath(os.getcwd())
    if not (cur == root_abs or cur.startswith(root_abs + os.sep)):
        cur = root_abs
    probe = cur
    while True:
        if os.path.exists(os.path.join(probe, TASKS_DIR, "board.yml")):
            return "." if probe == root_abs else os.path.relpath(probe, root_abs)
        if probe == root_abs:
            break
        probe = os.path.dirname(probe)
    return None


def integration_branch(root, scope):
    """From the checkout's board.yml for this scope, else the product cache."""
    p = os.path.join(root, tdir(scope), "board.yml")
    if os.path.exists(p):
        with open(p) as f:
            cfg = parse_kv(f.read())
        if cfg.get("integration_branch"):
            return cfg["integration_branch"]
    b = read_cache(root, scope).get(scope)
    if b:
        return b
    sys.exit(f"error: no board found ({tdir(scope)}/board.yml). "
             "Run: tasks.py init --name <you>")


# ---------- board worktree ----------

def ensure_worktree(root, scope, branch):
    rel = board_worktree_rel(scope)
    bw = os.path.join(root, rel)
    if os.path.exists(os.path.join(bw, ".git")):
        return bw
    git("worktree", "prune", cwd=root, check=False)
    os.makedirs(os.path.dirname(bw), exist_ok=True)
    git("fetch", "origin", branch, cwd=root, check=False)
    start = f"origin/{branch}" if ref_exists(root, f"origin/{branch}") else branch
    if not ref_exists(root, start):
        sys.exit(f"error: integration branch '{branch}' not found locally or on origin")
    private = board_branch_name(scope)
    git("worktree", "add", "-B", private, rel, start, cwd=root)
    return bw


def sync_board(root, scope, branch):
    """Bring the product board worktree up to date with the freshest board state."""
    bw = ensure_worktree(root, scope, branch)
    git("fetch", "origin", branch, cwd=bw, check=False)
    upstream = f"origin/{branch}" if ref_exists(root, f"origin/{branch}") else branch
    if not ref_exists(root, upstream):
        sys.exit(f"error: integration branch '{branch}' not found locally or on origin")
    ahead = git("rev-list", "--count", f"{upstream}..HEAD", cwd=bw).stdout.strip()
    if ahead != "0":
        # queued offline commits: replay them on top of the fresh upstream
        r = git("rebase", upstream, cwd=bw, check=False)
        if r.returncode != 0:
            git("rebase", "--abort", cwd=bw, check=False)
            sys.exit(f"error: queued board commits conflict with {upstream}; "
                     f"resolve manually in {board_worktree_rel(scope)}")
    else:
        git("reset", "--hard", upstream, cwd=bw)
    return bw


def resolve_board(root, scope):
    """Sync the board worktree, following integration_branch pointers: a
    branch whose board.yml names another branch (e.g. the parent after an
    iteration switch) redirects there. Returns (branch, bw); caches the
    resolved branch."""
    branch = integration_branch(root, scope)
    bw = sync_board(root, scope, branch)
    for _ in range(3):
        if not os.path.exists(board_yml_path(bw, scope)):
            break
        target = read_board_cfg(bw, scope).get("integration_branch") or branch
        if target == branch:
            break
        branch = target
        bw = sync_board(root, scope, branch)
    if read_cache(root, scope).get(scope) != branch:
        write_cache(root, scope, branch)
    return branch, bw


def ctx():
    root = repo_root()
    scope = find_scope(root)
    if scope is None:
        sys.exit(f"error: no board found (no {TASKS_DIR}/board.yml from cwd up "
                 "to repo root). Run: tasks.py init --name <you>")
    branch, bw = resolve_board(root, scope)
    return root, scope, branch, bw


def board_commit(root, branch, bw, scope, message):
    """Commit this board's changes, push to its integration branch (rebase/
    retry when the tip moved — shared-main policy A), fast-forward local
    checkouts best-effort."""
    paths = [tdir(scope)]
    if os.path.exists(os.path.join(bw, ".gitignore")):
        paths.append(".gitignore")
    git("add", "-A", "--", *paths, cwd=bw)
    if git("diff", "--cached", "--quiet", cwd=bw, check=False).returncode == 0:
        print("no changes")
        return
    git("commit", "-m", message, cwd=bw)
    sha = git("rev-parse", "HEAD", cwd=bw).stdout.strip()
    private = board_branch_name(scope)
    if has_remote(root):
        pushed = False
        for attempt in range(PUSH_RETRIES):
            r = git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=bw,
                    check=False)
            if r.returncode == 0:
                pushed = True
                break
            # Non-ff or race: fetch, rebase onto origin, retry (policy A).
            git("fetch", "origin", branch, cwd=bw, check=False)
            upstream = f"origin/{branch}"
            if not ref_exists(root, upstream):
                break
            rr = git("rebase", upstream, cwd=bw, check=False)
            if rr.returncode != 0:
                git("rebase", "--abort", cwd=bw, check=False)
                sys.exit(f"error: board push rebase conflict with {upstream}; "
                         f"resolve manually in {board_worktree_rel(scope)}")
            sha = git("rev-parse", "HEAD", cwd=bw).stdout.strip()
        if not pushed:
            print(f"warning: push failed; board commit queued locally on "
                  f"{private} and will be pushed on the next board operation",
                  file=sys.stderr)
    update_local_branch(root, scope, branch, sha)


def update_local_branch(root, scope, branch, sha):
    """Best-effort: fast-forward the local branch / checkout to include sha."""
    bw_path = os.path.realpath(os.path.join(root, board_worktree_rel(scope)))
    out = git("worktree", "list", "--porcelain", cwd=root).stdout
    checkout = None
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line.split(" ", 1)[1]
        elif line == f"branch refs/heads/{branch}":
            if os.path.realpath(path) != bw_path:
                checkout = path
    if checkout:
        clean = git("status", "--porcelain", cwd=checkout, check=False).stdout.strip() == ""
        if clean:
            git("merge", "--ff-only", sha, cwd=checkout, check=False)
    else:
        git("branch", "-f", branch, sha, cwd=root, check=False)


# ---------- board.yml / areas.md ----------

def board_yml_path(bw, scope):
    return os.path.join(bw, tdir(scope), "board.yml")


def board_schema_version(cfg):
    """Integer schema version from board.yml; missing or invalid → 0."""
    raw = (cfg.get("schema_version") or "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def ensure_schema_version(cfg):
    """Stamp schema_version for write; never downgrade a newer board."""
    if board_schema_version(cfg) < SCHEMA_VERSION:
        cfg["schema_version"] = str(SCHEMA_VERSION)
    return cfg


def read_board_cfg(bw, scope):
    with open(board_yml_path(bw, scope)) as f:
        cfg = parse_kv(f.read())
    v = board_schema_version(cfg)
    if v > SCHEMA_VERSION:
        print(f"warning: board schema_version {v} is newer than this tasks.py "
              f"(supports {SCHEMA_VERSION}); unknown fields may be ignored",
              file=sys.stderr)
    return cfg


def write_board_cfg(bw, scope, cfg):
    ensure_schema_version(cfg)
    lines = [f"{k}: {cfg.get(k, '')}" for k in CONFIG_KEYS]
    known = set(CONFIG_KEYS)
    # Preserve unknown keys so an older script rewriting a newer board does
    # not strip future fields (best-effort forward compat on write).
    for k, v in cfg.items():
        if k not in known:
            lines.append(f"{k}: {v}")
    with open(board_yml_path(bw, scope), "w") as f:
        f.write("\n".join(lines) + "\n")


def areas_path(bw, scope):
    return os.path.join(bw, tdir(scope), "areas.md")


def read_areas(bw, scope):
    mods = {}
    p = areas_path(bw, scope)
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                m = re.match(r"^- ([^:]+):\s*(.*)$", line)
                if m:
                    mods[m.group(1).strip()] = m.group(2).strip()
    return mods


def write_areas(bw, scope, mods):
    with open(areas_path(bw, scope), "w") as f:
        f.write("# Areas\n\n")
        for name, desc in mods.items():
            f.write(f"- {name}: {desc}\n")


# ---------- task file format ----------
# <scope>/.tasks/NNN.md :
#   ---
#   id: 12
#   title: Fix token refresh
#   area: auth
#   status: backlog
#   kind:                    (optional; "umbrella" = goal parent)
#   assignee: qz
#   branch: dev/12-fix-token-refresh
#   deps: [3, 7]
#   pr: https://github.com/...
#   needs: decision        (set while a design fork awaits a human call)
#   created: 2026-08-09
#   ---
#   freeform description

def parse_kv(text):
    d = {}
    for line in text.splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            d[m.group(1)] = m.group(2).strip()
    return d


def parse_task(path):
    with open(path) as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        sys.exit(f"error: malformed task file {path}")
    meta = parse_kv(m.group(1))
    meta["id"] = int(meta["id"])
    deps = meta.get("deps", "")
    meta["deps"] = [int(x) for x in re.findall(r"\d+", deps)]
    meta["kind"] = meta.get("kind", "") or ""
    meta["body"] = m.group(2).strip()
    meta["path"] = path
    return meta


def render_task(meta):
    lines = ["---"]
    for k in FIELDS:
        v = meta.get(k, "")
        if k == "deps":
            v = "[" + ", ".join(str(d) for d in meta.get("deps", [])) + "]"
        lines.append(f"{k}: {v}")
    lines.append("---")
    body = meta.get("body", "").strip()
    return "\n".join(lines) + ("\n\n" + body + "\n" if body else "\n")


def is_umbrella(t):
    return (t.get("kind") or "").strip() == "umbrella"


def membership_leaves(umbrella, by_id, _seen=None):
    """Leaf tasks under an umbrella via membership edges only.

    An umbrella's deps are direct children. A child that is itself an
    umbrella contributes its leaves recursively; a non-umbrella child is a
    leaf (its own deps are ordering, not membership). Nested-ready without
    a separate parent field.
    """
    if _seen is None:
        _seen = set()
    leaves = []
    for d in umbrella.get("deps", []):
        if d in _seen:
            continue
        _seen.add(d)
        child = by_id.get(d)
        if not child:
            continue
        if is_umbrella(child):
            leaves.extend(membership_leaves(child, by_id, _seen))
        else:
            leaves.append(child)
    return leaves


def covered_by_umbrellas(tasks):
    """Task ids that sit under any umbrella (direct or nested membership)."""
    by_id = {t["id"]: t for t in tasks}
    covered = set()
    for t in tasks:
        if not is_umbrella(t):
            continue
        for d in t.get("deps", []):
            covered.add(d)
        for leaf in membership_leaves(t, by_id):
            covered.add(leaf["id"])
    return covered


def umbrella_rollup(umbrella, tasks):
    """Short status rollup over membership leaves (not ordering-only deps).

    not-planned leaves are out of scope: excluded from both the done
    numerator and the denominator, and omitted from open status counts.
    """
    by_id = {t["id"]: t for t in tasks}
    leaves = membership_leaves(umbrella, by_id)
    # Progress is over work still in play — drop not-planned entirely.
    active = [t for t in leaves if t["status"] != "not-planned"]
    if not active:
        if leaves:
            return "☂ all not-planned"
        return "☂ no children"
    n = len(active)
    done = sum(1 for t in active if t["status"] == "done")
    blocked = 0
    for t in active:
        if t["status"] in TERMINAL or t["status"] == "review":
            continue
        if any(by_id.get(d, {}).get("status") != "done" for d in t.get("deps", [])):
            blocked += 1
    # Compact status counts for non-done active leaves (omit zeros).
    counts = {}
    for t in active:
        if t["status"] == "done":
            continue
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    order = [s for s in STATUSES if s != "done" and s != "not-planned"]
    parts = [f"☂ {done}/{n} done"]
    for s in order:
        if counts.get(s):
            parts.append(f"{counts[s]} {s}")
    if blocked:
        parts.append(f"{blocked} blocked")
    return " · ".join(parts)


def task_glob(bw, scope):
    return glob.glob(os.path.join(bw, tdir(scope), "[0-9][0-9][0-9].md"))


def all_tasks(bw, scope):
    return sorted((parse_task(p) for p in task_glob(bw, scope)),
                  key=lambda t: t["id"])


def find_task(bw, scope, tid):
    p = os.path.join(bw, tdir(scope), f"{int(tid):03d}.md")
    if not os.path.exists(p):
        sys.exit(f"error: no task with id {tid}")
    return parse_task(p)


def split_areas(s):
    return [a.strip() for a in s.split(",") if a.strip()]


def append_body(body, text):
    body = (body or "").strip()
    text = text.strip()
    return (body + "\n\n" + text).strip() if body else text


def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or "task"


def default_task_branch(scope, tid, title):
    """Branch name for a task: dev/<id>-<slug> or dev/<scope>-<id>-<slug>."""
    slug = slugify(title)
    if scope in (".", ""):
        return f"dev/{int(tid)}-{slug}"
    scope_slug = scope.replace("/", "-").replace("\\", "-")
    return f"dev/{scope_slug}-{int(tid)}-{slug}"


def current_branch_name(cwd):
    r = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False)
    if r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


def task_worktree_path(root, scope, tid, title):
    """Canonical path for a task worktree under product .dev/worktrees/."""
    name = f"{int(tid)}-{slugify(title)}"
    return os.path.join(product_root(root, scope), ".dev", "worktrees", name)


def ensure_task_branch(root, branch, start_ref):
    """Create local task branch from start_ref if missing; no checkout."""
    if ref_exists(root, f"refs/heads/{branch}"):
        return
    if ref_exists(root, f"origin/{branch}"):
        git("branch", "--track", branch, f"origin/{branch}", cwd=root,
            check=False)
        if ref_exists(root, f"refs/heads/{branch}"):
            return
        git("branch", branch, f"origin/{branch}", cwd=root)
        return
    if not ref_exists(root, start_ref):
        sys.exit(f"error: start ref '{start_ref}' not found; fetch origin first")
    git("branch", branch, start_ref, cwd=root)


def free_task_branch_from_primary(root, branch, integration):
    """If primary has `branch` checked out, move it to integration (hub).

    Required before `git worktree add` can attach the same branch elsewhere.
    Refuses when primary is dirty so we never discard uncommitted work.
    """
    checkout = branch_checkout_cwd(root, branch)
    if not checkout:
        return
    if os.path.realpath(checkout) != os.path.realpath(root):
        return  # already on a linked worktree — caller reuses it
    if worktree_is_dirty(root):
        sys.exit(f"error: primary clone is on task branch '{branch}' and dirty; "
                 f"commit/stash or move primary to '{integration}' before claim")
    r = git("checkout", integration, cwd=root, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        sys.exit(f"error: could not move primary to '{integration}' to free "
                 f"'{branch}' for a task worktree:\n{err}")


def ensure_task_worktree(root, scope, tid, title, branch):
    """Create or reuse the canonical linked worktree for this task branch."""
    wt_path = task_worktree_path(root, scope, tid, title)
    os.makedirs(os.path.dirname(wt_path), exist_ok=True)
    if os.path.exists(wt_path):
        if os.path.exists(os.path.join(wt_path, ".git")):
            git("worktree", "prune", cwd=root, check=False)
            cur = current_branch_name(wt_path)
            if cur == branch:
                return wt_path
            sys.exit(f"error: path exists but is not a live worktree for "
                     f"'{branch}': {wt_path}")
        sys.exit(f"error: task worktree path exists without .git "
                 f"(remove by hand): {wt_path}")
    r = git("worktree", "add", wt_path, branch, cwd=root, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        sys.exit(f"error: git worktree add failed for '{branch}':\n{err}")
    return wt_path


def resolve_task_ready_path(root, scope, tid, title, branch, integration):
    """Return the path where the agent should work — always a linked worktree.

    Policy: task implement work lives under `<scope>/.dev/worktrees/`; the
    primary clone stays on integration as a hub for parallel agents. Never
    checks out the task branch on primary. Idempotent resume reuses an
    existing task worktree for this id/branch.
    """
    root_rp = os.path.realpath(root)

    # Resume: existing linked worktree for this task (never primary).
    for path in find_task_worktree_paths(root, scope, tid, branch):
        if os.path.realpath(path) == root_rp:
            continue
        if not os.path.isdir(path):
            continue
        if not os.path.exists(os.path.join(path, ".git")):
            continue
        cur = current_branch_name(path)
        if cur != branch:
            if worktree_is_dirty(path):
                sys.exit(f"error: task worktree {path} is dirty and not on "
                         f"'{branch}' (on '{cur}'); commit/stash or remove it")
            r = git("checkout", branch, cwd=path, check=False)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip()
                sys.exit(f"error: could not checkout '{branch}' in "
                         f"{path}:\n{err}")
        return path

    # Branch already on a non-primary worktree (odd path / renamed title).
    checkout = branch_checkout_cwd(root, branch)
    if checkout and os.path.realpath(checkout) != root_rp:
        return checkout

    # Free the branch if a prior claim left it on primary, then always add
    # a linked worktree under .dev/worktrees/.
    free_task_branch_from_primary(root, branch, integration)
    return ensure_task_worktree(root, scope, tid, title, branch)


# ---------- land / cleanup (task PR merge path) ----------

def require_gh(root):
    """Exit unless gh is available and authenticated."""
    r = sh(["gh", "auth", "status"], cwd=root, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        sys.exit("error: gh not authenticated (needed for land). "
                 f"Run: gh auth login\n{err}")


def path_is_inside(inner, outer):
    inner = os.path.realpath(inner)
    outer = os.path.realpath(outer)
    return inner == outer or inner.startswith(outer + os.sep)


def list_worktrees(root):
    """Parse `git worktree list --porcelain` into path/branch/head dicts."""
    out = git("worktree", "list", "--porcelain", cwd=root).stdout
    trees, cur = [], {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                trees.append(cur)
            cur = {"path": line.split(" ", 1)[1], "branch": None, "head": None}
        elif line.startswith("HEAD "):
            cur["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            ref = line.split(" ", 1)[1]
            if ref.startswith("refs/heads/"):
                cur["branch"] = ref[len("refs/heads/"):]
            else:
                cur["branch"] = ref
        elif line == "detached":
            cur["branch"] = None
    if cur:
        trees.append(cur)
    return trees


def parse_version_intent(body):
    """Pull `Version intent: <token>` from a PR body, if present."""
    m = re.search(r"(?im)^\s*Version intent:\s*(\S+)", body or "")
    return m.group(1) if m else None


def task_branch_name(meta, pr_head=None):
    """Prefer board-recorded branch; fall back to PR head ref."""
    b = (meta.get("branch") or "").strip()
    if b:
        return b
    if pr_head:
        return pr_head.strip()
    return ""


def find_task_worktree_paths(root, scope, tid, branch):
    """Local paths that hold this task's checkout (by branch and convention)."""
    found = []
    seen = set()
    if branch:
        for t in list_worktrees(root):
            if t.get("branch") == branch:
                path = t["path"]
                rp = os.path.realpath(path)
                if rp not in seen:
                    seen.add(rp)
                    found.append(path)
    wt_dir = os.path.join(product_root(root, scope), ".dev", "worktrees")
    if os.path.isdir(wt_dir):
        prefix = f"{int(tid)}-"
        tid_s = str(int(tid))
        for name in sorted(os.listdir(wt_dir)):
            if name == tid_s or name.startswith(prefix):
                path = os.path.join(wt_dir, name)
                if not os.path.isdir(path):
                    continue
                rp = os.path.realpath(path)
                if rp not in seen:
                    seen.add(rp)
                    found.append(path)
    return found


def branch_checkout_cwd(root, branch):
    """Worktree path that has `branch` checked out, or None."""
    if not branch:
        return None
    for t in list_worktrees(root):
        if t.get("branch") == branch:
            return t["path"]
    return None


def ensure_local_branch(root, branch, start_ref=None):
    """Create local branch tracking start_ref if missing. No checkout."""
    if not branch:
        return
    if ref_exists(root, f"refs/heads/{branch}"):
        return
    start = start_ref
    if not start:
        if ref_exists(root, f"origin/{branch}"):
            start = f"origin/{branch}"
        else:
            sys.exit(f"error: task branch '{branch}' not found locally or on origin")
    git("branch", "--track", branch, start, cwd=root, check=False)
    if not ref_exists(root, f"refs/heads/{branch}"):
        git("branch", branch, start, cwd=root)


def sync_task_branch_from_remote(root, branch):
    """Point local task branch at origin/<branch> when that ref exists.

    Land should start from the PR tip GitHub has, not a stale local ref.
    Refuses when local is strictly ahead of origin (would discard unpushed
    commits via reset/--force branch move).
    """
    remote = f"origin/{branch}"
    if not ref_exists(root, remote):
        ensure_local_branch(root, branch, None)
        return
    ensure_local_branch(root, branch, remote)
    ahead = git("rev-list", "--count", f"{remote}..{branch}",
                cwd=root).stdout.strip()
    if ahead not in ("", "0"):
        sys.exit(f"error: local '{branch}' is {ahead} commit(s) ahead of "
                 f"{remote}; push (or reset to origin) before land so "
                 f"unpushed commits are not discarded")
    checkout = branch_checkout_cwd(root, branch)
    if checkout:
        dirty = git("status", "--porcelain", cwd=checkout,
                    check=False).stdout.strip()
        if dirty:
            sys.exit(f"error: task worktree has uncommitted changes; "
                     f"commit or stash first:\n{checkout}")
        git("reset", "--hard", remote, cwd=checkout)
    else:
        git("branch", "-f", branch, remote, cwd=root)


def _rebase_in_existing_checkout(root, branch, upstream, checkout):
    """Rebase where `branch` is already checked out (task worktree or primary)."""
    dirty = git("status", "--porcelain", cwd=checkout,
                check=False).stdout.strip()
    if dirty:
        sys.exit(f"error: task worktree has uncommitted changes; "
                 f"commit or stash first:\n{checkout}")
    before = git("rev-parse", branch, cwd=root).stdout.strip()
    r = git("rebase", upstream, cwd=checkout, check=False)
    if r.returncode != 0:
        git("rebase", "--abort", cwd=checkout, check=False)
        err = (r.stderr or r.stdout or "").strip()
        sys.exit(f"error: rebase of '{branch}' onto {upstream} failed "
                 f"(conflicts?). Resolve in {checkout}, then re-run land.\n"
                 f"{err}")
    after = git("rev-parse", branch, cwd=root).stdout.strip()
    return before != after


def _rebase_via_temp_worktree(root, branch, upstream):
    """Rebase without touching the primary clone's checked-out branch.

    Uses a detached temp worktree, then points `branch` at the new tip via
    `git branch -f` (or reset if some other worktree has it). Never runs
    `git rebase <upstream> <branch>`, which would switch the primary checkout.
    """
    before = git("rev-parse", branch, cwd=root).stdout.strip()
    tmp = tempfile.mkdtemp(prefix="dev-land-rebase-")
    try:
        r = git("worktree", "add", "--detach", tmp, branch, cwd=root,
                check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: could not create temp worktree to rebase "
                     f"'{branch}':\n{err}")
        r = git("rebase", upstream, cwd=tmp, check=False)
        if r.returncode != 0:
            git("rebase", "--abort", cwd=tmp, check=False)
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: rebase of '{branch}' onto {upstream} failed "
                     f"(conflicts?). Fetch/rebase the task branch, resolve, "
                     f"push, then re-run land.\n{err}")
        new = git("rev-parse", "HEAD", cwd=tmp).stdout.strip()
        # Temp worktree still holds detached HEAD at `new`; drop it before
        # moving the branch ref (and before any other worktree reset).
        git("worktree", "remove", "--force", tmp, cwd=root, check=False)
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        tmp = None

        checkout = branch_checkout_cwd(root, branch)
        if checkout:
            dirty = git("status", "--porcelain", cwd=checkout,
                        check=False).stdout.strip()
            if dirty:
                sys.exit(f"error: rebased tip is {new[:12]} but worktree has "
                         f"uncommitted changes; commit/stash, reset to the "
                         f"rebased tip, then re-run land:\n{checkout}")
            git("reset", "--hard", new, cwd=checkout)
        else:
            git("branch", "-f", branch, new, cwd=root)
        after = git("rev-parse", branch, cwd=root).stdout.strip()
        return before != after
    finally:
        if tmp and os.path.isdir(tmp):
            git("worktree", "remove", "--force", tmp, cwd=root, check=False)
            shutil.rmtree(tmp, ignore_errors=True)
        git("worktree", "prune", cwd=root, check=False)


def rebase_task_onto_integration(root, branch, integration):
    """Rebase task branch onto origin/<integration>. Returns True if rewritten.

    Force-push is the caller's job when True. Conflicts abort and exit.
    Rebases in the existing checkout if the branch is already checked out;
    otherwise uses a temp worktree so the primary clone is never switched
    onto the task branch solely for the rebase.
    """
    upstream = f"origin/{integration}"
    if not ref_exists(root, upstream):
        sys.exit(f"error: {upstream} missing after fetch")
    sync_task_branch_from_remote(root, branch)
    behind = git("rev-list", "--count", f"{branch}..{upstream}",
                 cwd=root).stdout.strip()
    if behind == "0":
        return False
    checkout = branch_checkout_cwd(root, branch)
    if checkout:
        # Already checked out (task worktree or primary) — rebase in place.
        # Does not switch any other worktree onto the branch.
        return _rebase_in_existing_checkout(root, branch, upstream, checkout)
    # Not checked out anywhere: temp detached worktree + move branch ref.
    # Never `git rebase <upstream> <branch>` (that form checks the branch out
    # on the primary clone).
    return _rebase_via_temp_worktree(root, branch, upstream)


def push_task_branch(root, branch, force=False):
    """Push task branch to origin; force-with-lease only after a rebase."""
    if not branch:
        return
    args = ["push", "origin", f"refs/heads/{branch}:refs/heads/{branch}"]
    if force:
        args.insert(1, "--force-with-lease")
    r = git(*args, cwd=root, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        sys.exit(f"error: push of '{branch}' failed:\n{err}")


def wait_for_pr_mergeable(root, pr, *, timeout_s=None, poll_s=None):
    """Poll until GitHub settles mergeable, or timeout.

    Returns one of: MERGEABLE, CONFLICTING, UNKNOWN, MERGED.
    UNKNOWN means still computing after timeout — caller may try merge anyway.
    """
    if timeout_s is None:
        timeout_s = LAND_MERGEABLE_TIMEOUT_S
    if poll_s is None:
        poll_s = LAND_MERGEABLE_POLL_S
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    last = "UNKNOWN"
    announced = False
    while True:
        info = pr_view(root, pr, soft=True)
        if info and pr_is_merged(info):
            return "MERGED"
        raw = (info or {}).get("mergeable")
        # gh usually returns MERGEABLE|CONFLICTING|UNKNOWN; REST may use bool.
        if raw is True:
            m = "MERGEABLE"
        elif raw is False:
            m = "CONFLICTING"
        elif raw is None or raw == "":
            m = "UNKNOWN"
        else:
            m = str(raw).upper()
        if m in ("MERGEABLE", "CONFLICTING"):
            if announced:
                print(f"  PR mergeable={m}")
            return m
        last = m or "UNKNOWN"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"  PR mergeable still {last} after {timeout_s}s; trying merge")
            return "UNKNOWN"
        if not announced:
            print(f"  waiting for GitHub mergeable (currently {last})…")
            announced = True
        time.sleep(min(float(poll_s), remaining))


def merge_error_transient(err):
    """True only when a failed gh pr merge is worth rebase+retry.

    Permanent policy/review/auth failures return False so land fails fast.
    Default is permanent: only known "branch behind / conflict / flake"
    shapes retry.
    """
    e = (err or "").lower()
    if not e:
        return False
    # Permanent — never retry.
    permanent = (
        "pull request not found",
        "could not resolve",
        "no pull requests found",
        "authentication",
        "permission denied",
        "not allowed on this repository",
        "merge commits are not allowed",
        "squash merges are not allowed",
        "rebase merges are not allowed",
        "protected branch",
        "resource not accessible",
        "policy",
        "prohibits the merge",
    )
    if any(p in e for p in permanent):
        return False
    # Review / approval / draft: land never auto-approves or un-drafts.
    if "draft" in e:
        return False
    if "review" in e and ("required" in e or "approv" in e or "pending" in e):
        return False
    if "approv" in e and ("required" in e or "needed" in e or "pending" in e
                          or "missing" in e):
        return False
    # Status checks: pending/waiting may clear; hard failures must not spin.
    if "status check" in e:
        if any(w in e for w in ("fail", "unsuccessful", "errored")):
            return False
        if any(w in e for w in (
                "pending", "waiting", "in progress", "expected",
                "not complete", "incomplete")):
            return True
        # Unknown status-check wording — fail fast rather than retry-loop.
        return False
    # Transient — rebase onto updated base and/or wait may help.
    needles = (
        "not mergeable",
        "merge conflict",
        "head branch is out of date",
        "base branch was modified",
        "temporarily unavailable",
        "try again",
    )
    return any(n in e for n in needles)


def merge_fail_permanent_message(err):
    """Human-facing exit text for a non-retryable merge failure."""
    low = (err or "").lower()
    if "draft" in low:
        return (f"error: PR is draft; mark ready for review before land:\n"
                f"{err}")
    if "approv" in low or ("review" in low and (
            "required" in low or "pending" in low or "approv" in low)):
        return (f"error: merge blocked by review rules "
                f"(land does not auto-approve):\n{err}")
    if "status check" in low and any(
            w in low for w in ("fail", "unsuccessful", "errored")):
        return (f"error: merge blocked by failed status check(s); "
                f"fix CI before land:\n{err}")
    return f"error: merge failed (not retryable):\n{err}"


def pr_view(root, pr, *, soft=False):
    """JSON fields for land/cleanup. soft=True returns None on failure."""
    r = gh("pr", "view", pr,
           "--json", "state,mergedAt,mergeable,headRefName,baseRefName,body,url,title",
           cwd=root, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if soft:
            return None
        sys.exit(f"error: gh pr view failed for {pr}:\n{err}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        if soft:
            return None
        sys.exit(f"error: could not parse gh pr view output for {pr}")


def pr_is_merged(info):
    if not info:
        return False
    return bool((info.get("state") or "").upper() == "MERGED"
                or info.get("mergedAt"))


def task_branch_safe_to_drop(root, meta, branch):
    """Whether remote/local task branch may be deleted without losing work.

    Requires evidence the PR landed (MERGED). Refuses when gh cannot verify.
    Returns (ok: bool, reason: str).
    """
    if not branch:
        return False, "no branch recorded"
    pr = (meta.get("pr") or "").strip()
    if not pr:
        return False, "no PR URL to verify merge; refusing to delete branch"
    # Prefer soft view so cleanup never aborts the whole command on gh flake.
    r = sh(["gh", "auth", "status"], cwd=root, check=False)
    if r.returncode != 0:
        return False, "gh not authenticated; refusing to delete branch"
    info = pr_view(root, pr, soft=True)
    if info is None:
        return False, f"could not verify PR state for {pr}; refusing to delete branch"
    if pr_is_merged(info):
        return True, "PR merged"
    state = (info.get("state") or "?").upper()
    return False, f"PR is {state} (not merged); refusing to delete branch"


def worktree_is_dirty(path):
    r = git("status", "--porcelain", cwd=path, check=False)
    if r.returncode != 0:
        return True  # treat unknown as dirty — do not force-remove
    return bool((r.stdout or "").strip())


def mark_task_done(root, scope, branch, bw, meta):
    """Set status=done on the board if not already. Returns True if changed."""
    meta = find_task(bw, scope, meta["id"])
    if meta["status"] == "done":
        return False
    meta["status"] = "done"
    with open(meta["path"], "w") as f:
        f.write(render_task(meta))
    board_commit(root, branch, bw, scope,
                 f"dev: update T{meta['id']} (status=done)")
    return True


def _switch_primary_off_task_branch(root, scope, branch, actions):
    """If primary clone has the task branch checked out, move it to integration.

    Needed so local branch delete can succeed after land when someone had the
    task branch checked out on the primary tree (not only in a task worktree).
    """
    still = branch_checkout_cwd(root, branch)
    if not still or os.path.realpath(still) != os.path.realpath(root):
        return
    if worktree_is_dirty(root):
        actions.append(f"primary clone still on '{branch}' (dirty; not switching)")
        return
    ibranch = integration_branch(root, scope)
    target = f"origin/{ibranch}" if ref_exists(root, f"origin/{ibranch}") else ibranch
    if not ref_exists(root, target) and not ref_exists(root, ibranch):
        actions.append(f"primary clone still on '{branch}' "
                       f"(no {ibranch} ref to switch to)")
        return
    # Prefer a real local branch checkout so primary is not left detached.
    if ref_exists(root, f"refs/heads/{ibranch}"):
        r = git("checkout", ibranch, cwd=root, check=False)
    elif ref_exists(root, f"origin/{ibranch}"):
        r = git("checkout", "-B", ibranch, f"origin/{ibranch}", cwd=root,
                check=False)
    else:
        r = git("checkout", ibranch, cwd=root, check=False)
    if r.returncode == 0:
        actions.append(f"switched primary clone to '{ibranch}'")
    else:
        err = (r.stderr or r.stdout or "").strip()
        actions.append(f"could not switch primary off '{branch}': {err}")


def cleanup_task_artifacts(root, scope, meta, branch=None, *, verbose=True):
    """Idempotent cleanup: clean worktrees; branches only after PR merge proof.

    Never deletes origin/<branch> or the local task branch unless the task's
    PR is verified MERGED via gh. Dirty worktrees are left alone (no
    --force wipe of uncommitted work). Safe to re-run; safe on open PRs.
    """
    tid = meta["id"]
    branch = branch if branch is not None else task_branch_name(meta)
    actions = []
    cwd = os.getcwd()

    for path in find_task_worktree_paths(root, scope, tid, branch):
        if path_is_inside(cwd, path):
            sys.exit(f"error: cwd is inside task worktree {path}; "
                     "cd to the primary clone (or product dir) and re-run")
        if os.path.realpath(path) == os.path.realpath(root):
            continue
        board_wt = os.path.realpath(os.path.join(root, board_worktree_rel(scope)))
        if os.path.realpath(path) == board_wt:
            continue
        if not os.path.exists(path):
            continue
        # Orphan dir left after a prior prune — no .git link, nothing to lose.
        if os.path.isdir(path) and not os.path.exists(os.path.join(path, ".git")):
            actions.append(f"left orphan dir {path} (no .git; remove by hand)")
            continue
        if worktree_is_dirty(path):
            actions.append(f"skipped dirty worktree {path} "
                           f"(uncommitted changes; commit/stash or remove by hand)")
            continue
        r = git("worktree", "remove", path, cwd=root, check=False)
        if r.returncode != 0:
            git("worktree", "prune", cwd=root, check=False)
            # Force only after clean check — handles locked admin files, not dirt.
            r = git("worktree", "remove", "--force", path, cwd=root, check=False)
        if r.returncode == 0:
            actions.append(f"removed worktree {path}")
        else:
            err = (r.stderr or r.stdout or "").strip()
            actions.append(f"worktree remove failed for {path}: {err}")

    git("worktree", "prune", cwd=root, check=False)

    drop_ok, drop_reason = task_branch_safe_to_drop(root, meta, branch)
    if branch and not drop_ok:
        actions.append(f"kept branch '{branch}': {drop_reason}")

    if branch and drop_ok:
        if has_remote(root):
            git("fetch", "origin", "--prune", cwd=root, check=False)
            if ref_exists(root, f"origin/{branch}"):
                r = git("push", "origin", "--delete", branch, cwd=root,
                        check=False)
                if r.returncode == 0:
                    actions.append(f"deleted origin/{branch}")
                else:
                    err = (r.stderr or r.stdout or "").strip()
                    if "remote ref does not exist" in err.lower() or \
                       "does not exist" in err.lower():
                        actions.append(f"origin/{branch} already gone")
                    else:
                        actions.append(f"could not delete origin/{branch}: {err}")
            else:
                actions.append(f"origin/{branch} already absent")

        # Move primary off the task branch before deleting it.
        _switch_primary_off_task_branch(root, scope, branch, actions)

        still = branch_checkout_cwd(root, branch)
        if still:
            actions.append(f"local branch '{branch}' still checked out at "
                           f"{still}; not deleted")
        elif ref_exists(root, f"refs/heads/{branch}"):
            # Squash merges do not make the branch an ancestor of main, so -D
            # is required — only reached when PR is verified MERGED.
            r = git("branch", "-D", branch, cwd=root, check=False)
            if r.returncode == 0:
                actions.append(f"deleted local branch {branch}")
            else:
                err = (r.stderr or r.stdout or "").strip()
                actions.append(f"could not delete local {branch}: {err}")
        else:
            actions.append(f"local branch {branch} already absent")

        if ref_exists(root, f"refs/remotes/origin/{branch}"):
            git("branch", "-d", "-r", f"origin/{branch}", cwd=root, check=False)
            actions.append(f"pruned origin/{branch} remote-tracking ref")

    if not actions:
        actions.append("already clean")
    if verbose:
        for a in actions:
            print(f"cleanup: {a}")
    return actions


def cmd_cleanup(args):
    """Idempotent cleanup; refuses to delete branches unless PR is MERGED."""
    root, scope, _ibranch, bw = ctx()
    meta = find_task(bw, scope, args.id)
    branch = task_branch_name(meta)
    if not branch and not find_task_worktree_paths(root, scope, meta["id"], ""):
        print(f"T{meta['id']}: nothing to clean (no branch / worktree recorded)")
        return
    print(f"T{meta['id']}: cleaning branch={branch or '(none)'} …")
    cleanup_task_artifacts(root, scope, meta, branch)


def cmd_land(args):
    """Post-approval land: rebase if needed, merge PR, cleanup, mark done.

    Does not approve the PR. Surfaces Version intent from the PR body only —
    product version file edits stay with the merger / product docs.
    """
    root, scope, integration, bw = ctx()
    meta = find_task(bw, scope, args.id)
    tid = meta["id"]
    pr = (meta.get("pr") or "").strip()

    if meta["status"] == "not-planned":
        sys.exit(f"error: T{tid} is not-planned; revive before landing")
    if meta["status"] == "done":
        print(f"T{tid}: already done — running cleanup only")
        branch = task_branch_name(meta)
        cleanup_task_artifacts(root, scope, meta, branch)
        return

    if not pr:
        sys.exit(f"error: T{tid} has no pr URL; open a PR before land")

    require_gh(root)
    info = pr_view(root, pr)
    state = (info.get("state") or "").upper()
    head = info.get("headRefName") or ""
    base = (info.get("baseRefName") or "").strip()
    body = info.get("body") or ""
    intent = parse_version_intent(body)
    branch = task_branch_name(meta, head)
    if not branch:
        sys.exit(f"error: T{tid} has no branch and PR head is empty")

    print(f"T{tid}: land {pr}")
    print(f"  branch: {branch}")
    print(f"  base:   {base or integration}")
    print(f"  state:  {state}")
    print(f"  Version intent: {intent if intent is not None else '(not stated)'}")

    if pr_is_merged(info):
        print(f"T{tid}: PR already merged — marking done and cleaning up")
        mark_task_done(root, scope, integration, bw, meta)
        # Re-read meta so status=done is visible; cleanup still keys off PR.
        meta = find_task(bw, scope, tid)
        cleanup_task_artifacts(root, scope, meta, branch)
        if intent and intent.lower() != "none":
            print(f"note: Version intent is '{intent}' — apply the bump on "
                  f"'{integration}' per product versioning docs")
        print(f"T{tid}: done")
        return

    if state == "CLOSED":
        sys.exit(f"error: T{tid} PR is closed without merge: {pr}")

    if base and base != integration:
        sys.exit(f"error: T{tid} PR base is '{base}', board integration is "
                 f"'{integration}'. Retarget the PR (gh pr edit --base "
                 f"{integration}) or fix board config before land.")

    if not has_remote(root):
        sys.exit("error: no origin remote; cannot land")

    git("fetch", "origin", integration, cwd=root, check=False)
    git("fetch", "origin", branch, cwd=root, check=False)

    rewritten = rebase_task_onto_integration(root, branch, integration)
    if rewritten:
        print(f"  rebased {branch} onto origin/{integration}; pushing")
        push_task_branch(root, branch, force=True)
    else:
        push_task_branch(root, branch, force=False)

    last_err = ""
    merged = False
    for attempt in range(1, LAND_RETRIES + 1):
        # After push, mergeable is often UNKNOWN; wait rather than fail once.
        readiness = wait_for_pr_mergeable(root, pr)
        if readiness == "MERGED":
            merged = True
            print(f"  PR became merged while waiting (attempt {attempt})")
            break
        if readiness == "CONFLICTING":
            last_err = (f"PR mergeable=CONFLICTING after wait "
                        f"(attempt {attempt})")
            print(f"  {last_err}; rebase and retry…")
            git("fetch", "origin", integration, cwd=root, check=False)
            rewritten = rebase_task_onto_integration(root, branch, integration)
            push_task_branch(root, branch, force=True if rewritten else False)
            if attempt < LAND_RETRIES:
                time.sleep(min(2 * attempt, 8))
            continue

        # Squash only; cleanup handles branch/worktree deletion explicitly.
        r = gh("pr", "merge", pr, "--squash", cwd=root, check=False)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode == 0:
            merged = True
            print(f"  merged (attempt {attempt})")
            break
        last_err = out
        low = out.lower()
        if "already merged" in low or "pull request is already merged" in low:
            merged = True
            print(f"  already merged (attempt {attempt})")
            break
        info = pr_view(root, pr)
        if pr_is_merged(info):
            merged = True
            print(f"  PR became merged externally (attempt {attempt})")
            break
        if not merge_error_transient(out):
            sys.exit(merge_fail_permanent_message(out))
        print(f"  merge attempt {attempt} failed (transient); rebase and retry…")
        git("fetch", "origin", integration, cwd=root, check=False)
        rewritten = rebase_task_onto_integration(root, branch, integration)
        push_task_branch(root, branch, force=True if rewritten else False)
        if attempt < LAND_RETRIES:
            time.sleep(min(2 * attempt, 8))

    if not merged:
        sys.exit(f"error: could not merge T{tid} PR after {LAND_RETRIES} "
                 f"attempts:\n{last_err}")

    integration, bw = resolve_board(root, scope)
    mark_task_done(root, scope, integration, bw, meta)
    meta = find_task(bw, scope, tid)
    cleanup_task_artifacts(root, scope, meta, branch)
    if intent and intent.lower() != "none":
        print(f"note: Version intent is '{intent}' — apply the bump on "
              f"'{integration}' per product versioning docs")
    print(f"T{tid}: landed and done")


# ---------- claim (implement setup) ----------

def identity_or_exit(root, scope):
    ident = read_local(root, scope, "identity")
    if not ident:
        id_path = os.path.join(product_root(root, scope), ".dev", "identity")
        sys.exit(f"error: no identity set for this product ({id_path}). "
                 "Run: init --name <handle> (or pass --assignee)")
    return ident


def cmd_claim(args):
    """Implement claim/setup: branch from origin/integration, always a
    linked worktree under .dev/worktrees (primary stays on integration as
    hub), record branch + doing + assignee. Idempotent resume.
    """
    root, scope, integration, bw = ctx()
    meta = find_task(bw, scope, args.id)
    tid = meta["id"]
    status = meta["status"]

    if status in ("done", "not-planned"):
        sys.exit(f"error: T{tid} is {status}; cannot claim")
    if status == "proposed":
        sys.exit(f"error: T{tid} is proposed; approve via review before claim")
    if status == "review":
        sys.exit(f"error: T{tid} is in review; do not re-claim — resume on "
                 f"the existing branch/PR (address review comments there), "
                 f"or land/cleanup first")
    if (meta.get("needs") or "").strip() == "decision":
        sys.exit(f"error: T{tid} has needs: decision; resolve before claim")

    assignee = (args.assignee if args.assignee is not None else "").strip()
    if not assignee:
        assignee = identity_or_exit(root, scope)

    branch = (args.branch or "").strip() or (meta.get("branch") or "").strip()
    if not branch:
        branch = default_task_branch(scope, tid, meta["title"])

    undone = [d for d in meta.get("deps", [])
              if find_task(bw, scope, d)["status"] != "done"]
    if undone:
        print(f"warning: T{tid} has unfinished deps: {undone}", file=sys.stderr)

    if not has_remote(root):
        sys.exit("error: no origin remote; cannot claim from origin/integration")
    git("fetch", "origin", integration, cwd=root, check=False)
    start = f"origin/{integration}"
    if not ref_exists(root, start):
        sys.exit(f"error: {start} missing after fetch")

    ensure_task_branch(root, branch, start)
    ready = resolve_task_ready_path(
        root, scope, tid, meta["title"], branch, integration)

    # Refresh meta after git work (board may have moved on shared integration).
    integration, bw = resolve_board(root, scope)
    meta = find_task(bw, scope, tid)
    changes = []
    if meta["status"] != "doing":
        meta["status"] = "doing"
        changes.append("status=doing")
    if meta.get("assignee") != assignee:
        meta["assignee"] = assignee
        changes.append(f"assignee={assignee}")
    if meta.get("branch") != branch:
        meta["branch"] = branch
        changes.append(f"branch={branch}")
    if changes:
        with open(meta["path"], "w") as f:
            f.write(render_task(meta))
        board_commit(root, integration, bw, scope,
                     f"dev: update T{tid} ({', '.join(changes)})")
        print(f"T{tid} updated: {', '.join(changes)}")
    else:
        print(f"T{tid}: already claimed (doing, {assignee}, {branch})")

    print(f"T{tid}: ready")
    print(f"  branch:  {branch}")
    print(f"  workdir: {ready}")
    print(f"  base:    origin/{integration}")


# ---------- commands ----------

def cmd_init(args):
    root = repo_root()
    if args.scope:
        scope = os.path.normpath(args.scope)
        if scope != "." and (os.path.isabs(scope) or scope.startswith("..")):
            sys.exit("error: --scope must be a subdir path relative to the repo root")
    else:
        # Same walk as find_scope (current worktree first).
        try:
            wt = worktree_root()
        except SystemExit:
            wt = root
        scope = nearest_scope(wt) or nearest_scope(root) or "."
    write_local(root, scope, "identity", args.name)
    p = os.path.join(root, tdir(scope), "board.yml")
    if args.integration:
        branch = args.integration
    elif os.path.exists(p):
        branch = parse_kv(open(p).read()).get("integration_branch")
    else:
        branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()
    write_cache(root, scope, branch)
    # immediate local ignore, without dirtying any branch (.dev/ and TASKS.md
    # match any depth — product-local paths are covered)
    exclude = os.path.join(root, ".git", "info", "exclude")
    existing = open(exclude).read() if os.path.exists(exclude) else ""
    with open(exclude, "a") as f:
        for line in (".dev/", "TASKS.md"):
            if line not in existing.splitlines():
                f.write(line + "\n")
    branch, bw = resolve_board(root, scope)
    if not os.path.exists(board_yml_path(bw, scope)):
        os.makedirs(os.path.join(bw, tdir(scope)), exist_ok=True)
        cfg = {"schema_version": str(SCHEMA_VERSION),
               "integration_branch": branch, "parent_branch": args.parent or "",
               "iteration": args.iteration or branch,
               "integrator": args.name, "contributors": args.name}
        write_board_cfg(bw, scope, cfg)
        gi = os.path.join(bw, ".gitignore")
        gi_existing = open(gi).read() if os.path.exists(gi) else ""
        with open(gi, "a") as f:
            if gi_existing and not gi_existing.endswith("\n"):
                f.write("\n")
            for line in (".dev/", "TASKS.md"):
                if line not in gi_existing.splitlines():
                    f.write(line + "\n")
        board_commit(root, branch, bw, scope, f"dev: init task board ({scope})")
        print(f"initialized board '{scope}' on '{branch}' (integrator: {args.name})")
    else:
        cfg = read_board_cfg(bw, scope)
        roster = [c.strip() for c in cfg.get("contributors", "").split(",") if c.strip()]
        if args.name not in roster:
            cfg["contributors"] = ", ".join(roster + [args.name])
            write_board_cfg(bw, scope, cfg)
            board_commit(root, branch, bw, scope,
                         f"dev: register contributor {args.name}")
        print(f"joined board '{scope}' on '{branch}'")
    print(f"identity: {args.name}")
    if scope != ".":
        print(f"note: commands target this board from inside '{scope}/' "
              f"(or pass --scope {scope})")


def cmd_whoami(args):
    root = repo_root()
    scope = find_scope(root)
    if scope is None:
        sys.exit(f"error: no board found (no {TASKS_DIR}/board.yml from cwd up "
                 "to repo root). Run: tasks.py init --name <you>")
    ident = read_local(root, scope, "identity")
    if ident is None:
        id_path = os.path.join(product_root(root, scope), ".dev", "identity")
        sys.exit(f"error: no identity set for this product "
                 f"({id_path}). Run: init --name <handle>")
    print(ident)


def cmd_config(args):
    root, scope, branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    if args.key is None:
        print(f"scope: {scope}")
        for k in CONFIG_KEYS:
            print(f"{k}: {cfg.get(k, '')}")
    elif args.value is None:
        if args.key not in CONFIG_KEYS:
            sys.exit(f"error: unknown key '{args.key}' (known: {', '.join(CONFIG_KEYS)})")
        print(cfg.get(args.key, ""))
    else:
        if args.key not in SETTABLE_KEYS:
            sys.exit(f"error: '{args.key}' is not settable via config "
                     f"(settable: {', '.join(SETTABLE_KEYS)})")
        cfg[args.key] = args.value
        write_board_cfg(bw, scope, cfg)
        board_commit(root, branch, bw, scope, f"dev: config {args.key}={args.value}")
        print(f"{args.key}: {args.value}")


def cmd_area(args):
    root, scope, branch, bw = ctx()
    mods = read_areas(bw, scope)
    if args.action == "list":
        open_counts = {}
        for t in all_tasks(bw, scope):
            if t["status"] != "done":
                for a in split_areas(t.get("area", "")):
                    open_counts[a] = open_counts.get(a, 0) + 1
        for name, desc in mods.items():
            print(f"- {name}: {desc} ({open_counts.pop(name, 0)} open)")
        for name, n in open_counts.items():
            print(f"- {name}: (no entry in areas.md) ({n} open)")
    elif args.action == "set":
        name = args.area_name.strip()
        if ":" in name or "," in name or not name:
            sys.exit("error: area names must be non-empty, no ':' or ','")
        if name.lower() == "all":
            sys.exit("error: 'all' is reserved (a task areaed 'all' touches "
                     "everything; it is never listed in areas.md)")
        mods[name] = args.desc or mods.get(name, "")
        write_areas(bw, scope, mods)
        board_commit(root, branch, bw, scope, f"dev: area set {name}")
        print(f"- {name}: {mods[name]}")
    elif args.action == "rm":
        name = args.area_name.strip()
        if name not in mods:
            sys.exit(f"error: no area '{name}'")
        refs = [t["id"] for t in all_tasks(bw, scope)
                if name in split_areas(t.get("area", "")) and t["status"] != "done"]
        if refs and not args.force:
            sys.exit(f"error: open tasks still reference '{name}': "
                     f"{', '.join(f'T{i}' for i in refs)} (use --force to remove anyway)")
        del mods[name]
        write_areas(bw, scope, mods)
        board_commit(root, branch, bw, scope, f"dev: area rm {name}")
        print(f"area '{name}' removed")


def cmd_add(args):
    root, scope, branch, bw = ctx()
    tasks = all_tasks(bw, scope)
    tid = max((t["id"] for t in tasks), default=0) + 1
    kind = (args.kind or "").strip()
    meta = {
        "id": tid, "title": args.title, "area": args.area or "",
        "status": args.status, "kind": kind,
        "assignee": args.assignee or "", "branch": "",
        "deps": [int(x) for x in re.findall(r"\d+", args.deps or "")], "pr": "",
        "needs": "", "created": datetime.date.today().isoformat(),
        "body": args.desc or "",
    }
    known = {t["id"] for t in tasks}
    for d in meta["deps"]:
        if d not in known:
            print(f"warning: dep {d} does not exist", file=sys.stderr)
    known_areas = read_areas(bw, scope)
    for a in split_areas(args.area or ""):
        if a != "all" and a not in known_areas:
            print(f"warning: area '{a}' has no entry in areas.md",
                  file=sys.stderr)
    path = os.path.join(bw, tdir(scope), f"{tid:03d}.md")
    with open(path, "w") as f:
        f.write(render_task(meta))
    board_commit(root, branch, bw, scope, f"dev: add T{tid} {args.title}")
    print(render_task(meta).rstrip())


def cmd_update(args):
    root, scope, branch, bw = ctx()
    meta = find_task(bw, scope, args.id)
    changes = []
    for field in ("title", "area", "status", "kind", "assignee", "branch", "pr",
                  "needs", "desc"):
        v = getattr(args, field, None)
        if v is not None:
            key = "body" if field == "desc" else field
            meta[key] = v.strip() if field == "kind" and isinstance(v, str) else v
            changes.append(f"{field}={v}" if field != "desc" else "desc")
    if args.deps is not None:
        meta["deps"] = [int(x) for x in re.findall(r"\d+", args.deps)]
        changes.append(f"deps={meta['deps']}")
    if args.reason is not None and args.status != "not-planned":
        sys.exit("error: --reason only applies with --status not-planned")
    if args.status == "not-planned" and not (args.reason or "").strip():
        sys.exit("error: --status not-planned requires --reason \"<why this "
                 "is not being pursued>\" (recorded in the task body)")
    if args.append is not None:
        meta["body"] = append_body(meta["body"], args.append)
        changes.append("append")
    if args.reason is not None:
        today = datetime.date.today().isoformat()
        meta["body"] = append_body(meta["body"],
                                   f"Not planned ({today}): {args.reason}")
    if not changes:
        sys.exit("error: nothing to update")
    if meta["status"] not in STATUSES:
        sys.exit(f"error: status must be one of {STATUSES}")
    if meta.get("needs") not in ("", "decision"):
        sys.exit("error: needs must be 'decision' or empty")
    if meta["status"] == "doing":
        undone = [d for d in meta["deps"]
                  if find_task(bw, scope, d)["status"] != "done"]
        if undone:
            print(f"warning: T{meta['id']} has unfinished deps: {undone}",
                  file=sys.stderr)
    with open(meta["path"], "w") as f:
        f.write(render_task(meta))
    board_commit(root, branch, bw, scope,
                 f"dev: update T{meta['id']} ({', '.join(changes)})")
    print(f"T{meta['id']} updated: {', '.join(changes)}")


STOPWORDS = {"the", "and", "for", "with", "that", "this", "add", "use",
             "into", "from", "when", "make", "support", "task", "new"}


def terms(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) > 2 and w not in STOPWORDS}


def cmd_related(args):
    """Rank existing tasks against a proposed title/description, so the agent
    can spot duplicates and dependency neighbours before adding."""
    root, scope, branch, bw = ctx()
    tasks = all_tasks(bw, scope)
    qt = terms(args.text)
    hits = []
    for t in tasks:
        overlap = len(qt & terms(t["title"] + " " + t.get("body", ""))) / len(qt) if qt else 0
        # Character similarity is noisy on short strings — only let it speak
        # when the titles are near-identical; otherwise shared terms decide.
        ratio = difflib.SequenceMatcher(None, args.text.lower(),
                                        t["title"].lower()).ratio()
        score = max(overlap, ratio if ratio >= 0.55 else 0)
        if score >= 0.3:
            hits.append((score, t))
    if not hits:
        print("(nothing related)")
        return
    for score, t in sorted(hits, key=lambda h: -h[0])[:8]:
        print(f"{score:.2f} {fmt_line(t, tasks)}")


def cmd_delete(args):
    root, scope, branch, bw = ctx()
    meta = find_task(bw, scope, args.id)
    os.remove(meta["path"])
    board_commit(root, branch, bw, scope,
                 f"dev: delete T{meta['id']} {meta['title']}")
    print(f"T{meta['id']} deleted")


def cmd_show(args):
    root, scope, branch, bw = ctx()
    meta = find_task(bw, scope, args.id)
    with open(meta["path"]) as f:
        print(f.read().rstrip())


def cmd_list(args):
    root, scope, branch, bw = ctx()
    tasks = all_tasks(bw, scope)
    sel = [t for t in tasks
           if (not args.assignee or t.get("assignee") == args.assignee)
           and (not args.status or t.get("status") == args.status)
           and (not args.needs or t.get("needs") == args.needs)]
    if args.json:
        out = [{k: t.get(k, "") for k in FIELDS + ["body"]} for t in sel]
        print(json.dumps(out, indent=1))
    elif not sel:
        print("(no matching tasks)")
    else:
        for t in sel:
            print(fmt_line(t, tasks))


def fmt_line(t, tasks):
    by_id = {x["id"]: x for x in tasks}
    blocked = any(by_id.get(d, {}).get("status") != "done" for d in t["deps"])
    parts = [f"T{t['id']}", f"[{t['status']}]"]
    if t.get("area"):
        parts.append(f"({t['area']})")
    parts.append(t["title"])
    if t.get("assignee"):
        parts.append(f"@{t['assignee']}")
    if t.get("needs"):
        parts.append(f"⚑needs-{t['needs']}")
    if is_umbrella(t):
        parts.append(umbrella_rollup(t, tasks))
    elif blocked and t["status"] not in ("review",) + TERMINAL:
        parts.append(f"⊘blocked-by:{','.join(str(d) for d in t['deps'])}")
    return " ".join(parts)


def cmd_board(args):
    root, scope, branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    tasks = all_tasks(bw, scope)
    expand = bool(getattr(args, "expand", False))
    covered = set() if expand else covered_by_umbrellas(tasks)
    title = f"# Board — iteration {cfg.get('iteration', '')}"
    if scope != ".":
        title += f" ({scope})"
    lines = [title, ""]
    for status in STATUSES:
        col = [t for t in tasks if t["status"] == status
               and t["id"] not in covered]
        # Column count: visible rows (collapsed view hides umbrella children).
        if status == "not-planned" and not col:
            continue  # no empty column for the exceptional case
        label = STATUS_LABEL.get(status, status.capitalize())
        lines.append(f"## {label} ({len(col)})")
        for t in col:
            mark = {"done": "x", "not-planned": "~"}.get(status, " ")
            lines.append(f"- [{mark}] {fmt_line(t, tasks)}")
        lines.append("")
    out = "\n".join(lines)
    with open(os.path.join(root, scope, "TASKS.md"), "w") as f:
        f.write(out)
    print(out.rstrip())


# ---------- iterations ----------

def cmd_iteration(args):
    root, scope, branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    tasks = all_tasks(bw, scope)
    done = sum(1 for t in tasks if t["status"] == "done")
    print(f"scope: {scope}")
    print(f"iteration: {cfg.get('iteration', '')}")
    print(f"integration_branch: {cfg.get('integration_branch', '')}")
    print(f"parent_branch: {cfg.get('parent_branch', '') or '(none)'}")
    print(f"tasks: {done}/{len(tasks)} done")


def cmd_iteration_close(args):
    """Log tasks to .tasks/log.md and delete task files, committing on the
    integration branch. Landing the integration branch in the parent (via PR)
    happens afterwards and is not this script's job."""
    root, scope, branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    tasks = all_tasks(bw, scope)
    if not tasks:
        sys.exit("error: no tasks on this board; nothing to close")
    unfinished = [t for t in tasks if t["status"] not in TERMINAL]
    if unfinished and not args.force:
        ids = ", ".join(f"T{t['id']}" for t in unfinished)
        sys.exit(f"error: unfinished tasks: {ids}. Finish them, delete them, "
                 "or re-run with --force to close anyway (they will be logged "
                 "as unfinished and removed).")
    name = cfg.get("iteration", branch)
    today = datetime.date.today().isoformat()
    parent = cfg.get("parent_branch", "")
    header = f"## {name} — closed {today} (branch {branch}"
    header += f" → {parent})" if parent else ")"
    entry = [header]
    for t in tasks:
        line = f"- {name}/T{t['id']} {t['title']}"
        if t.get("assignee"):
            line += f" — {t['assignee']}"
        if t["status"] == "not-planned":
            line += " [not planned]"
        elif t["status"] != "done":
            line += f" [unfinished: {t['status']}]"
        entry.append(line)
    log = os.path.join(bw, tdir(scope), "log.md")
    existing = open(log).read() if os.path.exists(log) else "# Iteration log\n"
    with open(log, "w") as f:
        f.write(existing.rstrip() + "\n\n" + "\n".join(entry) + "\n")
    for t in tasks:
        os.remove(t["path"])
    board_commit(root, branch, bw, scope, f"dev: close iteration {name}")
    print(f"iteration '{name}' closed: {len(tasks)} tasks logged to "
          f"{tdir(scope)}/log.md and removed")
    if parent:
        print(f"next: open a PR landing '{branch}' into '{parent}', then "
              f"after it merges: tasks.py iteration-new <branch>")


def cmd_iteration_new(args):
    root, scope, old_branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    parent = args.parent or cfg.get("parent_branch", "")
    if not parent:
        sys.exit("error: no parent branch known; pass --parent <branch>")
    if args.branch in (old_branch, parent):
        sys.exit(f"error: new iteration branch must differ from '{old_branch}' "
                 f"and parent '{parent}'")
    git("fetch", "origin", parent, cwd=bw, check=False)
    start = f"origin/{parent}" if ref_exists(root, f"origin/{parent}") else parent
    if not ref_exists(root, start):
        sys.exit(f"error: parent branch '{parent}' not found locally or on origin")
    git("reset", "--hard", start, cwd=bw)
    # defensively clear any task files inherited from the parent
    for p in task_glob(bw, scope):
        os.remove(p)
    os.makedirs(os.path.join(bw, tdir(scope)), exist_ok=True)
    cfg["integration_branch"] = args.branch
    cfg["parent_branch"] = parent
    cfg["iteration"] = args.name or args.branch
    write_board_cfg(bw, scope, cfg)
    write_cache(root, scope, args.branch)
    board_commit(root, args.branch, bw, scope,
                 f"dev: start iteration {cfg['iteration']}")
    # leave a pointer on the parent so other contributors' stale checkouts
    # resolve to the new iteration (resolve_board follows it)
    git("reset", "--hard", start, cwd=bw)
    os.makedirs(os.path.join(bw, tdir(scope)), exist_ok=True)
    write_board_cfg(bw, scope, cfg)
    board_commit(root, parent, bw, scope,
                 f"dev: point board at iteration {cfg['iteration']}")
    print(f"iteration '{cfg['iteration']}' started on new branch "
          f"'{args.branch}' (parent: {parent})")
    print(f"note: switch your checkout when ready: git checkout {args.branch}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scope", default=None,
                   help="target the board at this subdir (relative to repo root)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="set identity; create or join a board")
    s.add_argument("--name", required=True)
    s.add_argument("--scope", default=argparse.SUPPRESS,
                   help="subdir (relative to repo root) whose board "
                   "to create/join; default: nearest board at/above cwd, else root")
    s.add_argument("--integration", help="integration branch (default: existing board's, else current)")
    s.add_argument("--parent", help="parent branch this integration branch lands in")
    s.add_argument("--iteration", help="iteration name (default: branch name)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("whoami", help="print this checkout's identity")
    s.set_defaults(fn=cmd_whoami)

    s = sub.add_parser("config", help="show or set board settings")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("area", help="manage the board's area list")
    msub = s.add_subparsers(dest="action", required=True)
    m = msub.add_parser("list", help="areas with descriptions and open counts")
    m = msub.add_parser("set", help="add or update a area")
    m.add_argument("area_name")
    m.add_argument("--desc", help="one-line scope description")
    m = msub.add_parser("rm", help="remove a area")
    m.add_argument("area_name")
    m.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_area)

    s = sub.add_parser("add", help="add a task")
    s.add_argument("--title", required=True)
    s.add_argument("--area")
    s.add_argument("--deps", help="comma-separated task ids")
    s.add_argument("--desc")
    s.add_argument("--assignee")
    s.add_argument("--kind", default="",
                   help="optional kind (e.g. umbrella); empty = normal task")
    s.add_argument("--status", choices=["proposed", "backlog", "planned"],
                   default="backlog")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("update", help="update task fields")
    s.add_argument("id", type=int)
    for f in ("title", "area", "status", "kind", "assignee", "branch", "pr",
              "needs", "deps", "desc"):
        s.add_argument(f"--{f}")
    s.add_argument("--append", help="append a paragraph to the body "
                                    "(leaves existing text untouched)")
    s.add_argument("--reason", help="why this task is not being pursued; "
                                    "required with --status not-planned")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("related", help="existing tasks similar to some text")
    s.add_argument("text", help="proposed title (+ description)")
    s.set_defaults(fn=cmd_related)

    s = sub.add_parser("delete", help="delete a task")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_delete)

    s = sub.add_parser("show", help="print one task file")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("list", help="one-line-per-task, filterable")
    s.add_argument("--assignee")
    s.add_argument("--status")
    s.add_argument("--needs")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("board", help="print kanban view; regenerate TASKS.md")
    s.add_argument("--expand", action="store_true",
                   help="list all tasks flat (do not collapse umbrella children)")
    s.set_defaults(fn=cmd_board)

    s = sub.add_parser("iteration", help="show current iteration")
    s.set_defaults(fn=cmd_iteration)

    s = sub.add_parser("iteration-close", help="log + remove all tasks on the board")
    s.add_argument("--force", action="store_true",
                   help="close even with unfinished tasks")
    s.set_defaults(fn=cmd_iteration_close)

    s = sub.add_parser("iteration-new", help="start a fresh board on a new integration branch")
    s.add_argument("branch")
    s.add_argument("--parent")
    s.add_argument("--name", help="iteration name (default: branch name)")
    s.set_defaults(fn=cmd_iteration_new)

    s = sub.add_parser("claim",
                       help="implement setup: branch + linked worktree, status=doing")
    s.add_argument("id", type=int)
    s.add_argument("--assignee",
                   help="assignee (default: product identity; auto agents pass auto/<model>)")
    s.add_argument("--branch",
                   help="task branch (default: recorded or dev/<scope?>-<id>-<slug>)")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("land",
                       help="merge task PR (squash), cleanup branches/worktree, mark done")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_land)

    s = sub.add_parser("cleanup",
                       help="idempotent: remove task worktree/local branch/remote-tracking")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_cleanup)

    args = p.parse_args()
    global SCOPE_OVERRIDE
    SCOPE_OVERRIDE = getattr(args, "scope", None)
    args.fn(args)


if __name__ == "__main__":
    main()
