#!/usr/bin/env python3
"""Task-board CRUD for the `dev` skill.

A repo can hold one board (at <root>/.tasks/) or several (a monorepo with
<subdir>/.tasks/ per product). The script targets the nearest board at or
above cwd, or an explicit --scope; there is no single-board fallback.
Checkout-local state lives under the product root: <scope>/.dev/ (root board
→ .dev/ at the primary clone root) — identity, boards cache, board worktree,
and task worktrees. Paths resolve via the hub (git-common-dir, or the worktree
that already owns .dev/board), not a linked task worktree's toplevel.
Each board lives ONLY on its integration branch. Mutations go through a
hidden worktree at <product>/.dev/board on a private branch (_dev-board or
_dev-board-<scope>), commit there, and push to the integration branch. When
several product boards share one integration branch, push uses rebase/retry
(policy A). Code branches never commit to .tasks/.

Stdlib + git for board CRUD. Git/gh lifecycle helpers: `claim` (implement
setup), `ship` (commit/push/PR; optional Dev-batch stamp), `preflight`,
`restack` (fail-closed stack rebase), `batch-gate` (review-set completeness),
`land` / `cleanup`, `iteration-land`. Never auto-approve reviews.
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
            "later", "not-planned"]
# Live watch only. Static `TASKS board` / TASKS.md keep STATUSES (pipeline).
_WATCH_HEAD = ("review", "doing", "planned", "proposed", "backlog")
WATCH_STATUSES = [s for s in _WATCH_HEAD if s in STATUSES] + [
    s for s in STATUSES if s not in _WATCH_HEAD]
# Don't block an iteration close. later is parked (reseeds on iteration-new),
# not finished — but close treats it like not-planned.
TERMINAL = ("done", "later", "not-planned")
# Out of umbrella progress (not done, not this iteration).
OUT_OF_PLAY = ("later", "not-planned")
STATUS_LABEL = {"not-planned": "Not planned", "later": "Later"}
TASKS_DIR = ".tasks"
# Local live viewer dropped by init at <product>/board. Not .dev/board —
# that path is the hidden board worktree.
VIEWER_NAME = "board"
# On-disk board schema. Bump when board.yml / task frontmatter / .tasks layout
# changes in a way future scripts must detect. Missing schema_version on disk
# means 0 (pre-stamp boards). Never downgrade a higher version on write.
# 2: later is a valid task status (additive; older readers ignore unknown).
# 3: iteration is a positive integer identity; iteration_name and
#    iteration_started added. Pre-3 dated names fail closed (no migrate).
SCHEMA_VERSION = 3
CONFIG_KEYS = ["schema_version", "integration_branch", "parent_branch",
               "iteration", "iteration_name", "iteration_started",
               "integrator", "contributors"]
SETTABLE_KEYS = ["parent_branch", "integrator", "iteration",
                 "iteration_name", "iteration_started"]
# kind: optional. "" = normal task; "umbrella" = goal parent whose deps are
# direct children (leaves or nested umbrellas). Hierarchy lives in deps;
# reverse index is computed at board time. Other kind values reserved for
# future (e.g. recurring) — readers ignore unknown kinds.
FIELDS = ["id", "title", "area", "status", "kind", "assignee", "branch", "deps",
          "pr", "needs", "created"]
# Board push races on a shared integration branch: rebase onto origin and
# retry this many times before queueing locally.
PUSH_RETRIES = 5
# Land: merge retries when GitHub reports not-mergeable / recomputing.
# Land never rewrites the task branch; a conflict is merged forward.
LAND_RETRIES = 5
# After a push, GitHub often leaves mergeable=UNKNOWN while recomputing.
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
    """Primary clone / hub worktree — not a linked task worktree.

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
    # Only a .git *directory* is the hub's git dir. --git-common-dir does
    # not return a worktree's .git file; a relative common dir resolved
    # against that worktree's cwd can land on it — its parent is the
    # worktree, not the hub.
    if os.path.basename(common) == ".git" and os.path.isdir(common):
        return os.path.dirname(common)
    hub = hub_from_board_worktrees()
    if hub:
        return hub
    return worktree_root()


def _is_board_worktree_path(path):
    """True if path is a board mutator: …/.dev/board (root or scoped)."""
    norm = os.path.normpath(os.path.realpath(path))
    return (os.path.basename(norm) == "board"
            and os.path.basename(os.path.dirname(norm)) == ".dev")


def hub_from_board_worktrees(cwd=None):
    """Hub checkout that already owns a board mutator worktree.

    Used when git-common-dir is not <hub>/.git (bare repo, separate-git-dir).
    The board path is <hub>/.dev/board or <hub>/<scope>/.dev/board; the hub is
    that product directory's git toplevel. Does not use worktree-list paths for
    the hub itself: a separate-git-dir clone lists the git dir, not the tree.
    Returns None before the first board worktree exists (init).
    """
    try:
        here = worktree_root(cwd)
    except SystemExit:
        return None
    hubs = []
    for t in list_worktrees(here):
        if not _is_board_worktree_path(t["path"]):
            continue
        product = os.path.dirname(os.path.dirname(os.path.realpath(t["path"])))
        try:
            hub = worktree_root(cwd=product)
        except SystemExit:
            continue
        hubs.append(os.path.realpath(hub))
    if not hubs:
        return None
    return min(hubs, key=len)


def git_path(root, rel):
    """Resolve a path inside the git dir (e.g. info/exclude).

    Uses --git-path, not --git-dir: worktree git-dirs do not own shared
    files such as info/exclude.
    """
    r = git("rev-parse", "--path-format=absolute", "--git-path", rel,
            cwd=root, check=False)
    if r.returncode == 0:
        return os.path.normpath(r.stdout.strip())
    p = git("rev-parse", "--git-path", rel, cwd=root).stdout.strip()
    if not os.path.isabs(p):
        p = os.path.join(root, p)
    return os.path.normpath(p)


def has_remote(root):
    return git("remote", "get-url", "origin", cwd=root, check=False).returncode == 0


def ref_exists(root, ref):
    return git("rev-parse", "--verify", "--quiet", ref, cwd=root, check=False).returncode == 0


def is_ancestor(root, maybe_anc, ref):
    """True if maybe_anc is an ancestor of ref (equal counts)."""
    r = git("merge-base", "--is-ancestor", maybe_anc, ref, cwd=root, check=False)
    return r.returncode == 0


def tdir(scope):
    """Board dir relative to repo root ('.tasks' or '<scope>/.tasks')."""
    return os.path.normpath(os.path.join(scope, TASKS_DIR))


def product_root(root, scope):
    """Directory that owns the board: git toplevel for scope '.', else
    <toplevel>/<scope>. `root` is the primary clone (repo_root)."""
    if scope in (".", ""):
        return root
    return os.path.join(root, scope)


def viewer_path(root, scope):
    """Local live-viewer script: <product>/board."""
    return os.path.join(product_root(root, scope), VIEWER_NAME)


def viewer_ignore_line(scope):
    """Gitignore line for the viewer. Anchored so a project `board/` is safe."""
    rel = VIEWER_NAME if scope in (".", "") else os.path.join(scope, VIEWER_NAME)
    return "/" + rel.replace(os.sep, "/")


def append_ignore_line(path, line):
    """Append `line` to a gitignore-style file if missing. True if wrote."""
    existing = open(path).read() if os.path.exists(path) else ""
    if line in existing.splitlines():
        return False
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    return True


# Thin wrapper init writes. __TASKS_PY__ is replaced with repr(path).
BOARD_VIEWER_SCRIPT = '''\
#!/usr/bin/env python3
"""Local board viewer (r/a/e/q; arrows scroll; type id↵). Written by dev init; left alone if you edit it."""
import os, sys
TASKS = __TASKS_PY__
os.chdir(os.path.dirname(os.path.abspath(__file__)))
if not os.path.isfile(TASKS):
    sys.exit("error: dev skill not found at %s — delete ./board and re-run init" % TASKS)
os.execv(sys.executable, [sys.executable, TASKS, "board", "--watch"])
'''

# Code lines a generated ./board may contain (any template generation).
# Opening docstring and blanks are ignored; anything else is a user edit.
_STOCK_VIEWER_LINE = re.compile(
    r"^(?:"
    r"#!/usr/bin/env python3"
    r"|import os, sys"
    r"|TASKS = (?:'[^']*'|\"[^\"]*\")"
    r"|os\.chdir\(os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\)"
    r"|if not os\.path\.isfile\(TASKS\):"
    r"|    sys\.exit\(.+\)"
    r"|os\.execv\(sys\.executable, \[sys\.executable, TASKS, \"board\", \"--watch\"\]\)"
    r")$"
)


def viewer_script():
    """Current ./board contents for this install."""
    return BOARD_VIEWER_SCRIPT.replace(
        "__TASKS_PY__", repr(os.path.abspath(__file__)))


def viewer_is_stock(text):
    """True if text is still an init-generated thin wrapper.

    Docstring and TASKS path may drift; extra statements or a different
    command mean the user edited it.
    """
    text = text.replace("\r\n", "\n")
    text = re.sub(
        r"(?s)\A(#!.*\n)?(?:\n)*(\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?''')[ \t]*\n",
        r"\1",
        text,
        count=1,
    )
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not any('TASKS, "board", "--watch"' in ln for ln in lines):
        return False
    return all(_STOCK_VIEWER_LINE.match(ln) for ln in lines)


def ensure_board_viewer(root, scope):
    """Write <product>/board if missing or still a stock wrapper.

    Returns (path, wrote). Leaves a user-edited file and a directory alone.
    """
    dest = viewer_path(root, scope)
    if os.path.isdir(dest):
        return dest, False
    content = viewer_script()
    if os.path.isfile(dest):
        try:
            existing = open(dest, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            return dest, False
        if existing == content or not viewer_is_stock(existing):
            return dest, False
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(dest, 0o755)
    return dest, True


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
    # Schema 3: identity is a positive integer. No migrate-on-read.
    iteration_index(cfg)
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


def umbrella_rollup(umbrella, tasks):
    """Short status rollup over membership leaves (not ordering-only deps).

    later and not-planned leaves are out of scope: excluded from both the
    done numerator and the denominator, and omitted from open status counts.
    """
    by_id = {t["id"]: t for t in tasks}
    leaves = membership_leaves(umbrella, by_id)
    # Progress is over work still in play — drop later / not-planned.
    active = [t for t in leaves if t["status"] not in OUT_OF_PLAY]
    if not active:
        if leaves:
            parked = {t["status"] for t in leaves if t["status"] in OUT_OF_PLAY}
            if parked == {"later"}:
                return "☂ all later"
            if parked == {"not-planned"}:
                return "☂ all not-planned"
            return "☂ all later/not-planned"
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
    order = [s for s in STATUSES if s != "done" and s not in OUT_OF_PLAY]
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


def occupies_area(t):
    # later still occupies (intended work); not-planned is historical only.
    return t["status"] not in ("done", "not-planned")


# doing + review: the in-flight set implement aborts on / auto skips.
IN_FLIGHT = ("doing", "review")


def areas_overlap(a, b):
    """Segment-prefix overlap (SKILL Area stewardship).

    Split on `/`. `flows` overlaps `flows/implement`; `flows/implement` does
    not overlap `flows/review`. String prefix is the trap (`flow` vs `flows`).
    Reserved `all` overlaps every name.
    """
    if a == "all" or b == "all":
        return True
    sa = [p for p in a.split("/") if p]
    sb = [p for p in b.split("/") if p]
    n = min(len(sa), len(sb))
    return bool(n) and sa[:n] == sb[:n]


def task_areas_overlap(a_areas, b_areas):
    """True if any area on one overlaps any on the other.

    Reserved `all` on either side overlaps every task, including untagged.
    """
    if "all" in a_areas or "all" in b_areas:
        return True
    return any(areas_overlap(x, y) for x in a_areas for y in b_areas)


def in_flight_area_collisions(task, tasks, exclude_ids=None):
    """doing/review tasks whose areas overlap `task`, excluding itself.

    ``exclude_ids`` drops extra ids from the scan (batch peers — they are
    sequential, not outside occupancy).
    """
    skip = {task["id"]}
    if exclude_ids:
        skip.update(exclude_ids)
    mine = split_areas(task.get("area", ""))
    hits = []
    for other in tasks:
        if other["id"] in skip:
            continue
        if other.get("status") not in IN_FLIGHT:
            continue
        if task_areas_overlap(mine, split_areas(other.get("area", ""))):
            hits.append(other)
    return hits


def set_area_overlaps(ids, tasks):
    """Pairs among ``ids`` whose areas overlap, regardless of status."""
    by_id = {t["id"]: t for t in tasks}
    seen = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    pairs = []
    for i, a in enumerate(seen):
        ta = by_id.get(a)
        if ta is None:
            continue
        aa = split_areas(ta.get("area", ""))
        for b in seen[i + 1:]:
            tb = by_id.get(b)
            if tb is None:
                continue
            if task_areas_overlap(aa, split_areas(tb.get("area", ""))):
                pairs.append((ta, tb))
    return pairs


def format_area_collisions(task, blockers, color=False):
    label = task.get("area") or "(untagged)"
    tid = f"T{task['id']}"
    head = (f"{_status_ansi(task['status'], tid)} {_ansi(_DIM, label)}"
            if color else f"{tid} {label}")
    if not blockers:
        clear = _ansi(_OK, "clear") if color else "clear"
        return f"{head} — {clear}"
    bits = []
    for o in blockers:
        oa = o.get("area") or "(untagged)"
        oid, st = f"T{o['id']}", o["status"]
        if color:
            bits.append(f"{_status_ansi(st, oid)} {_status_ansi(st, st)} ({oa})")
        else:
            bits.append(f"{oid} {st} ({oa})")
    blocked = _ansi(_ERR, "blocked") if color else "blocked"
    return f"{head} — {blocked}: {', '.join(bits)}"


def watch_collision_line(tid, tasks, color=False):
    task = next((t for t in tasks if t["id"] == tid), None)
    if task is None:
        msg = f"T{tid} — no such task"
        return _ansi(_ERR, msg) if color else msg
    return format_area_collisions(
        task, in_flight_area_collisions(task, tasks), color=color)


def parse_watch_tid(buf):
    """Digits, optional leading t/T. None if not an id."""
    s = buf.strip()
    if s[:1] in "tT":
        s = s[1:]
    if not s.isdigit():
        return None
    return int(s)


def append_body(body, text):
    body = (body or "").strip()
    text = text.strip()
    return (body + "\n\n" + text).strip() if body else text


def slugify(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or "task"


def parse_positive_int(value, what="value"):
    raw = str(value if value is not None else "").strip()
    if not raw.isdigit() or int(raw) < 1:
        sys.exit(f"error: {what} must be a positive integer (got {value!r})")
    return int(raw)


def parse_iso_date(value, what="date"):
    raw = (value or "").strip()
    try:
        datetime.date.fromisoformat(raw)
    except ValueError:
        sys.exit(f"error: {what} must be YYYY-MM-DD (got {value!r})")
    return raw


def iteration_index(cfg):
    """Live iteration identity. Fail closed if board.yml is pre-schema-3."""
    return parse_positive_int(cfg.get("iteration"), "board.yml iteration")


def iteration_name(cfg):
    return (cfg.get("iteration_name") or "").strip()


def iteration_started(cfg):
    return (cfg.get("iteration_started") or "").strip()


def iteration_label(cfg):
    """Human-facing '1' or '1 — MVP'."""
    idx = iteration_index(cfg)
    name = iteration_name(cfg)
    return f"{idx} — {name}" if name else str(idx)


def title_prefix(idx, tid):
    return f"[{int(idx)}/T{int(tid)}]"


# Schema-3 dirs are `{n}` or `{n}-{slug}`. Pre-3 leftovers are
# `YYYY-MM-DD` or `YYYY-MM-DD-slug` — those must not parse as year N.
ARCHIVE_INDEX_RE = re.compile(r"^(\d+)(?:-.*)?$")
PRE3_ARCHIVE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-.*)?$")


def archive_dir_index(ent):
    """Integer index from a schema-3 archive dir name, or None."""
    if PRE3_ARCHIVE_RE.match(ent):
        return None
    m = ARCHIVE_INDEX_RE.match(ent)
    return int(m.group(1)) if m else None


def iteration_archive_slug(index, name=""):
    """Directory name: '{n}-{slug}' or '{n}' when unnamed."""
    name = (name or "").strip()
    slug = slugify(name) if name else ""
    return f"{int(index)}-{slug}" if slug else str(int(index))


def require_schema3_archive_slug(index, name="", what="iteration"):
    """Fail closed if {n}-{slug} would look like a pre-3 date dir.

    Those names are invisible to archive_dir_index, so a year-like index
    plus an MM-DD display name would skip clash detection and overwrite.
    """
    slug = iteration_archive_slug(index, name)
    if PRE3_ARCHIVE_RE.match(slug):
        shown = (name or "").strip() or "(none)"
        sys.exit(f"error: {what} {int(index)} name {shown!r} would archive "
                 f"as {slug!r}, which looks like a pre-schema-3 date dir "
                 "and would be invisible to the index scan. Pick a "
                 "different number or display name.")
    return slug


def iteration_close_heading(index, name=""):
    name = (name or "").strip()
    return f"## {int(index)} — {name}" if name else f"## {int(index)}"


def log_has_close_section(text, index):
    """True if log.md has a close heading for this index ('## N' or '## N — …').

    Must not treat '## 1' as a match for 10.
    """
    return re.search(rf"(?m)^## {int(index)}(?: — |$)", text) is not None


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


def ff_task_branch(root, branch, start_ref):
    """Fast-forward `branch` to start_ref. Caller must have checked ancestry."""
    checkout = branch_checkout_cwd(root, branch)
    if checkout:
        if worktree_is_dirty(checkout):
            sys.exit(f"error: task branch '{branch}' is behind {start_ref} "
                     f"but worktree is dirty; commit/stash or reset first:"
                     f"\n{checkout}")
        r = git("merge", "--ff-only", start_ref, cwd=checkout, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: could not fast-forward '{branch}' to "
                     f"{start_ref}:\n{err}")
    else:
        r = git("branch", "-f", branch, start_ref, cwd=root, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: could not fast-forward '{branch}' to "
                     f"{start_ref}:\n{err}")
    print(f"note: fast-forwarded leftover '{branch}' to {start_ref}")


def ensure_task_branch(root, branch, start_ref):
    """Create local task branch from start_ref if missing; no checkout.

    An existing branch is reused only when start_ref is already an ancestor
    of its tip (at or ahead of the intended base). An empty leftover (the
    branch is a strict ancestor of start_ref) is fast-forwarded. A leftover
    that has diverged is an error — recovery is in flows/implement.md
    (show unique commits and leftover dirty status, ask keep vs throw;
    do not write branch: first; keep: commit/stash if dirty; refuse
    throw-away while dirty; throw-away: gh pr close then delete).
    """
    if not ref_exists(root, start_ref):
        sys.exit(f"error: start ref '{start_ref}' not found; fetch origin first")

    if not ref_exists(root, f"refs/heads/{branch}"):
        if ref_exists(root, f"origin/{branch}"):
            git("branch", "--track", branch, f"origin/{branch}", cwd=root,
                check=False)
            if not ref_exists(root, f"refs/heads/{branch}"):
                git("branch", branch, f"origin/{branch}", cwd=root)
        else:
            git("branch", branch, start_ref, cwd=root)
            return

    if is_ancestor(root, start_ref, branch):
        return
    if is_ancestor(root, branch, start_ref):
        ff_task_branch(root, branch, start_ref)
        return
    ahead = git("rev-list", "--count", f"{start_ref}..{branch}",
                cwd=root, check=False).stdout.strip() or "?"
    behind = git("rev-list", "--count", f"{branch}..{start_ref}",
                 cwd=root, check=False).stdout.strip() or "?"
    sys.exit(f"error: task branch '{branch}' has diverged from {start_ref} "
             f"({ahead} unique, {behind} behind); show unique commits and "
             f"leftover dirty status and ask keep vs throw before writing "
             f"branch: (flows/implement.md)")


def verified_claim_base(root, branch, start_ref):
    """start_ref if it is an ancestor of branch; else refuse to name a base."""
    if not is_ancestor(root, start_ref, branch):
        sys.exit(f"error: task branch '{branch}' is not based on {start_ref}; "
                 f"refusing to report an unverified base")
    return start_ref


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


def rebase_in_progress(cwd):
    """True if `cwd` has an unfinished rebase (merge or apply)."""
    if not cwd:
        return False
    for name in ("rebase-merge", "rebase-apply"):
        r = git("rev-parse", "--git-path", name, cwd=cwd, check=False)
        path = (r.stdout or "").strip()
        if r.returncode == 0 and path and os.path.isdir(path):
            return True
    return False


def sync_task_branch_from_remote(root, branch):
    """Point local task branch at origin/<branch> when that ref exists.

    Restack and review-attach should start from the PR tip, not a stale
    local ref. Refuses when local is strictly ahead of origin (would
    discard unpushed commits via reset/--force branch move).
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
                 f"{remote}; not discarding unpushed commits "
                 f"(reset to {remote} if this leftover is stale)")
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


def _rebase_argv(upstream, old_base=None):
    """git rebase args: exclude old_base commits when moving a stack child."""
    if old_base:
        return ["rebase", "--onto", upstream, old_base]
    return ["rebase", upstream]


def _rebase_in_existing_checkout(root, branch, upstream, checkout,
                                 old_base=None):
    """Rebase where `branch` is already checked out (task worktree or primary)."""
    dirty = git("status", "--porcelain", cwd=checkout,
                check=False).stdout.strip()
    if dirty:
        sys.exit(f"error: task worktree has uncommitted changes; "
                 f"commit or stash first:\n{checkout}")
    before = git("rev-parse", branch, cwd=root).stdout.strip()
    r = git(*_rebase_argv(upstream, old_base), cwd=checkout, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        onto = f"{upstream} (excluding {old_base})" if old_base else upstream
        git("rebase", "--abort", cwd=checkout, check=False)
        sys.exit(f"error: rebase of '{branch}' onto {onto} failed "
                 f"(conflicts?). Resolve in {checkout}, then re-run "
                 f"restack.\n{err}")
    after = git("rev-parse", branch, cwd=root).stdout.strip()
    return before != after


def _rebase_via_temp_worktree(root, branch, upstream, old_base=None):
    """Rebase without touching the primary clone's checked-out branch.

    Uses a detached temp worktree, then points `branch` at the new tip via
    `git branch -f` (or reset if some other worktree has it). Never runs
    `git rebase <upstream> <branch>`, which would switch the primary checkout.
    """
    before = git("rev-parse", branch, cwd=root).stdout.strip()
    tmp = tempfile.mkdtemp(prefix="dev-restack-rebase-")
    try:
        r = git("worktree", "add", "--detach", tmp, branch, cwd=root,
                check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: could not create temp worktree to rebase "
                     f"'{branch}':\n{err}")
        r = git(*_rebase_argv(upstream, old_base), cwd=tmp, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            onto = f"{upstream} (excluding {old_base})" if old_base else upstream
            git("rebase", "--abort", cwd=tmp, check=False)
            sys.exit(f"error: rebase of '{branch}' onto {onto} failed "
                     f"(conflicts?). Fetch/rebase the task branch, resolve, "
                     f"push, then re-run restack.\n{err}")
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
                         f"rebased tip, then re-run restack:\n"
                         f"{checkout}")
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


def rebase_task_onto_ref(root, branch, upstream, old_base=None):
    """Rebase task branch onto upstream ref. Returns True if rewritten.

    Restack only — land never rewrites a task branch. Force-push is the
    caller's job when True; conflicts abort and exit. Rebases in the
    existing checkout if the branch is already checked out; otherwise uses
    a temp worktree so the primary clone is never switched onto the task
    branch solely for the rebase. Never rewrites integration.

    old_base, when set, is the tip to exclude (`git rebase --onto upstream
    old_base`) when a rewritten stack parent must not be replayed. Do not
    skip when old_base is not an ancestor: the parent may have moved ahead
    of the child. An already-moved child is a no-op (`rebase --onto`
    reports up to date), and behind==0 is not a valid skip there — a
    stacked child usually already contains integration.
    """
    if not ref_exists(root, upstream):
        # Try origin/<name> if bare branch name given
        if not upstream.startswith("origin/") and ref_exists(
                root, f"origin/{upstream}"):
            upstream = f"origin/{upstream}"
        elif not ref_exists(root, upstream):
            sys.exit(f"error: upstream '{upstream}' missing after fetch")
    old_base = resolve_rebase_exclude(root, old_base)
    checkout = branch_checkout_cwd(root, branch)
    if checkout and rebase_in_progress(checkout):
        sys.exit(f"error: rebase of '{branch}' still in progress in "
                 f"{checkout}; finish or abort it, then re-run restack")
    sync_task_branch_from_remote(root, branch)
    if not old_base:
        behind = git("rev-list", "--count", f"{branch}..{upstream}",
                     cwd=root).stdout.strip()
        if behind == "0":
            return False
    checkout = branch_checkout_cwd(root, branch)
    if checkout:
        return _rebase_in_existing_checkout(
            root, branch, upstream, checkout, old_base=old_base)
    return _rebase_via_temp_worktree(root, branch, upstream,
                                     old_base=old_base)


def resolve_rebase_exclude(root, old_base):
    """Resolve a restack exclude ref (SHA, branch, or origin/<branch>)."""
    if not old_base:
        return None
    old_base = str(old_base).strip()
    if not old_base:
        return None
    if ref_exists(root, old_base):
        return git("rev-parse", old_base, cwd=root).stdout.strip()
    if not old_base.startswith("origin/") and ref_exists(
            root, f"origin/{old_base}"):
        return git("rev-parse", f"origin/{old_base}", cwd=root).stdout.strip()
    sys.exit(f"error: restack exclude '{old_base}' missing after fetch")


def ref_short_name(ref):
    """Branch name without origin/ prefix."""
    ref = (ref or "").strip()
    if ref.startswith("origin/"):
        return ref[len("origin/"):]
    return ref


def open_prs_based_on(root, base_branch):
    """Open PRs whose GitHub base is base_branch. Paginates; no hard cap."""
    r = gh("api", "--method", "GET", "--paginate",
           "--jq",
           ".[] | {url: .html_url, headRefName: .head.ref, "
           "baseRefName: .base.ref}",
           "repos/{owner}/{repo}/pulls",
           "-f", "state=open",
           "-f", f"base={base_branch}",
           "-F", "per_page=100",
           cwd=root, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        sys.exit(f"error: could not list PRs based on '{base_branch}':\n{err}")
    out = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            sys.exit(f"error: could not parse PR list for base '{base_branch}'")
        head = (item.get("headRefName") or "").strip()
        url = (item.get("url") or "").strip()
        base = (item.get("baseRefName") or "").strip() or base_branch
        if head and url:
            out.append({"url": url, "head": head, "base": base})
    return out


def retarget_children(root, parent_branch, integration):
    """Point PRs stacked on parent_branch at integration. Fail-closed.

    GitHub does not auto-retarget: deleting a branch that open PRs are
    based on CLOSES them, even when its own PR merged. So this runs after
    the merge and before cleanup deletes the branch. Only the PR base
    moves — the parent's commits are already ancestors of integration
    after a merge commit, so no child branch is rewritten at any depth.
    """
    children = open_prs_based_on(root, parent_branch)
    if not children:
        return
    print(f"  retargeting {len(children)} stacked PR(s) to {integration}")
    for pr in children:
        url, head = pr["url"], pr["head"]
        r = gh("pr", "edit", url, "--base", integration, cwd=root,
               check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: could not retarget {url} ({head}) from "
                     f"'{parent_branch}' to '{integration}'; not deleting "
                     f"'{parent_branch}' (that would close this PR). "
                     f"Retarget it, then re-run land.\n{err}")
        print(f"  retarget {head}: {parent_branch} → {integration}")


def merge_forward_task_branch(root, branch, integration):
    """Merge origin/<integration> into the task branch, in place.

    Absorbs a conflict without rewriting, so PRs stacked on this branch
    stay valid. Only a clean merge is kept: a conflicted one is aborted
    (atomic, unlike a half-applied rebase) and handed to the author, who
    owns the branch and the context to resolve it. Returns False when the
    branch already contains integration — then the CONFLICTING verdict is
    not divergence and merging forward cannot fix it.
    """
    up = f"origin/{integration}"
    git("fetch", "origin", integration, cwd=root, check=False)
    git("fetch", "origin", branch, cwd=root, check=False)
    if not ref_exists(root, up):
        sys.exit(f"error: '{up}' missing after fetch; cannot merge forward")
    # Merge forward from the reviewed tip, not a stale local ref (refuses
    # if local is ahead, so unpushed work is never published by land).
    sync_task_branch_from_remote(root, branch)
    behind = git("rev-list", "--count", f"{branch}..{up}",
                 cwd=root).stdout.strip()
    if behind in ("", "0"):
        return False
    checkout = branch_checkout_cwd(root, branch)
    tmp = None
    if not checkout:
        tmp = tempfile.mkdtemp(prefix="dev-land-merge-")
        r = git("worktree", "add", tmp, branch, cwd=root, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            shutil.rmtree(tmp, ignore_errors=True)
            sys.exit(f"error: could not create temp worktree to merge "
                     f"'{branch}' forward:\n{err}")
        checkout = tmp
    try:
        dirty = git("status", "--porcelain", cwd=checkout,
                    check=False).stdout.strip()
        if dirty:
            sys.exit(f"error: worktree for '{branch}' is dirty at "
                     f"{checkout}; commit or stash, then re-run land")
        r = git("merge", "--no-edit", up, cwd=checkout, check=False)
        if r.returncode != 0:
            git("merge", "--abort", cwd=checkout, check=False)
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: '{branch}' conflicts with {up} and land does "
                     f"not resolve conflicts or rewrite your branch.\n"
                     f"Resolve on the branch (git merge {up} — merge, do "
                     f"not rebase, or any PR stacked on it needs a "
                     f"restack), push, then re-run land.\n{err}")
        print(f"  merged {up} into {branch}; pushing")
        push_task_branch(root, branch, force=False)
        return True
    finally:
        if tmp:
            git("worktree", "remove", "--force", tmp, cwd=root, check=False)
            shutil.rmtree(tmp, ignore_errors=True)
            git("worktree", "prune", cwd=root, check=False)


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
    """True only when a failed gh pr merge is worth another attempt.

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


def merge_error_wants_update(err):
    """True when a failed gh pr merge asks for the branch to catch up.

    Land does not rebase, so the only remedy for these is merging
    origin/<integration> forward into the branch (what GitHub's "Update
    branch" button does). Flake/status-check shapes are excluded — they
    clear on their own and must not mutate the branch.
    """
    e = (err or "").lower()
    return any(n in e for n in (
        "head branch is out of date",
        "base branch was modified",
        "not mergeable",
        "merge conflict",
    ))


def merge_commits_disallowed(err):
    """True when gh pr merge failed because the repo forbids merge commits."""
    e = (err or "").lower()
    return ("merge commits are not allowed" in e
            or ("merge commit" in e and "not allowed" in e))


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
    PR is verified MERGED via gh. Retargets any PR still based on the
    branch to integration first (deleting it would close them) and fails
    closed if that retarget fails. Dirty worktrees are left alone (no
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
            # Deleting a branch open PRs are based on closes them, so the
            # retarget lives with the delete, not only in land.
            retarget_children(root, branch, integration_branch(root, scope))
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
            # -D, not -d: the branch may have been merged into integration
            # rather than the current HEAD — only reached when PR is
            # verified MERGED.
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


def note_version_intent(intent, integration, product):
    """Post-land bump reminder. No-op when intent is missing or none."""
    if not intent or intent.lower() == "none":
        return
    print(f"note: Version intent is '{intent}' — apply one bump "
          f"on '{integration}' when this set is done (per product "
          f"versioning docs)")
    print(f"  product: {product}")


def cmd_land(args):
    """Post-approval land: merge, retarget children, cleanup.

    Merge is integrator-only (whoami == board integrator). Already-merged
    and already-done paths are cleanup and stay allowed for anyone so
    board/status/review refresh can mark done.
    Merges with a merge commit and never rewrites the task branch: the
    branch's commits become ancestors of integration, so PRs stacked on
    it need no rebase at any depth. Divergence is absorbed by the merge;
    a real conflict is merged forward into the branch (clean only) or
    handed back to the author. Immediate children are retargeted to
    integration after the merge and before cleanup deletes the branch —
    deleting it first would close them. Already-done is cleanup only.
    Does not approve the PR. Surfaces Version intent from the PR body only —
    product version file edits stay with the merger / product docs.
    """
    root, scope, integration, bw = ctx()
    meta = find_task(bw, scope, args.id)
    tid = meta["id"]
    pr = (meta.get("pr") or "").strip()

    if meta["status"] in ("not-planned", "later"):
        sys.exit(f"error: T{tid} is {meta['status']}; revive before landing")
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
        print(f"T{tid}: PR already merged — retargeting children, then done")
        retarget_children(root, branch, integration)
        mark_task_done(root, scope, integration, bw, meta)
        # Re-read meta so status=done is visible; cleanup still keys off PR.
        meta = find_task(bw, scope, tid)
        cleanup_task_artifacts(root, scope, meta, branch)
        note_version_intent(intent, integration,
                            os.path.abspath(product_root(root, scope)))
        print(f"T{tid}: done")
        return

    if state == "CLOSED":
        sys.exit(f"error: T{tid} PR is closed without merge: {pr}")

    ident = identity_or_exit(root, scope)
    integrator = (read_board_cfg(bw, scope).get("integrator") or "").strip()
    if not integrator:
        sys.exit("error: no integrator configured; cannot land. "
                 "Set: TASKS config integrator <name>")
    if ident != integrator:
        sys.exit(f"error: only the board integrator can land "
                 f"(whoami='{ident}', integrator='{integrator}')")

    if base and base != integration:
        sys.exit(f"error: T{tid} PR base is '{base}', board integration is "
                 f"'{integration}'. Retarget the PR (gh pr edit --base "
                 f"{integration}) or fix board config before land.")

    if not has_remote(root):
        sys.exit("error: no origin remote; cannot land")

    git("fetch", "origin", integration, cwd=root, check=False)
    git("fetch", "origin", branch, cwd=root, check=False)
    # Land never pushes the task branch: it merges what was reviewed.
    merged = False
    last_err = ""
    for attempt in range(1, LAND_RETRIES + 1):
        # Right after a push, mergeable is often UNKNOWN; wait rather than
        # fail once. A merge commit absorbs a behind-but-clean branch, so
        # only a real content conflict reports CONFLICTING.
        readiness = wait_for_pr_mergeable(root, pr)
        if readiness == "MERGED":
            merged = True
            print(f"  PR became merged while waiting (attempt {attempt})")
            break
        if readiness == "CONFLICTING":
            print(f"  PR mergeable=CONFLICTING (attempt {attempt}); "
                  f"merging origin/{integration} forward…")
            if merge_forward_task_branch(root, branch, integration):
                last_err = (f"PR mergeable=CONFLICTING after merging "
                            f"origin/{integration} forward (attempt "
                            f"{attempt})")
            else:
                last_err = (f"PR mergeable=CONFLICTING but '{branch}' "
                            f"already contains origin/{integration}; "
                            f"GitHub may still be recomputing (attempt "
                            f"{attempt})")
                print(f"  already contains origin/{integration}; waiting")
            if attempt < LAND_RETRIES:
                time.sleep(min(2 * attempt, 8))
            continue

        # Merge commit, never squash: the branch's commits must stay in
        # integration's history so stacked children need no rewrite.
        # cleanup handles branch/worktree deletion explicitly.
        r = gh("pr", "merge", pr, "--merge", cwd=root, check=False)
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
        if merge_commits_disallowed(out):
            sys.exit(
                f"error: this repository does not allow merge commits, which "
                f"land requires — squashing would rewrite '{branch}' and "
                f"strand any PR stacked on it.\nEnable 'Allow merge commits' "
                f"in the repository's settings (Settings → General → Pull "
                f"Requests), then re-run land.\n{out}")
        info = pr_view(root, pr)
        if pr_is_merged(info):
            merged = True
            print(f"  PR became merged externally (attempt {attempt})")
            break
        if not merge_error_transient(out):
            sys.exit(merge_fail_permanent_message(out))
        print(f"  merge attempt {attempt} failed (transient); retry…")
        git("fetch", "origin", integration, cwd=root, check=False)
        # A stale branch cannot fix itself by waiting: land never rebases,
        # so merge integration forward (no-op if already contained).
        if merge_error_wants_update(out):
            merge_forward_task_branch(root, branch, integration)
        if attempt < LAND_RETRIES:
            time.sleep(min(2 * attempt, 8))

    if not merged:
        sys.exit(f"error: could not merge T{tid} PR after {LAND_RETRIES} "
                 f"attempts:\n{last_err}")

    # Before cleanup: deleting this branch while a PR is based on it closes
    # that PR (GitHub does not auto-retarget). Fail-closed.
    retarget_children(root, branch, integration)

    integration, bw = resolve_board(root, scope)
    mark_task_done(root, scope, integration, bw, meta)
    meta = find_task(bw, scope, tid)
    cleanup_task_artifacts(root, scope, meta, branch)
    note_version_intent(intent, integration,
                        os.path.abspath(product_root(root, scope)))
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
    hub), record branch + doing + assignee. Resume reuses a branch that is
    at or ahead of origin/integration; empty leftovers are fast-forwarded;
    diverged leftovers error. Printed base is verified.
    """
    root, scope, integration, bw = ctx()
    meta = find_task(bw, scope, args.id)
    tid = meta["id"]
    status = meta["status"]

    if status in ("done", "later", "not-planned"):
        sys.exit(f"error: T{tid} is {status}; cannot claim")
    if status == "proposed":
        sys.exit(f"error: T{tid} is proposed; approve via review before claim")
    if status == "review":
        sys.exit(f"error: T{tid} is in review; do not claim — resume via "
                 f"diff (handoff: update --assignee first)")
    if (meta.get("needs") or "").strip() == "decision":
        sys.exit(f"error: T{tid} has needs: decision; resolve before claim")
    if not split_areas(meta.get("area", "")):
        sys.exit(f"error: T{tid} has no area; set a real name "
                 f"(reuse or area set) before claim")

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
    base = verified_claim_base(root, branch, start)

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
    print(f"  product: {product_root(ready, scope)}")
    print(f"  base:    {base}")


# ---------- ship (implement: commit, push, open PR) ----------

def path_is_tasks_dir(path):
    """True if path is inside a .tasks board directory (must not ship on code)."""
    norm = path.replace("\\", "/")
    return bool(re.search(r"(^|/)\.tasks(/|$)", norm))


def path_under_task_worktrees(root, scope, path):
    """True if path is under <product>/.dev/worktrees/ (claim policy)."""
    wt_root = os.path.realpath(
        os.path.join(product_root(root, scope), ".dev", "worktrees"))
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    try:
        return os.path.commonpath([wt_root, real]) == wt_root
    except ValueError:
        return False


def find_open_pr_for_branch(root, branch, base):
    """Return PR url for an open PR with this head branch and base, or ''."""
    r = gh("pr", "list", "--head", branch, "--base", base, "--state", "open",
           "--json", "url,number", "--limit", "5", cwd=root, check=False)
    if r.returncode != 0:
        return ""
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return ""
    if not items:
        r = gh("pr", "list", "--head", branch, "--state", "open",
               "--json", "url,baseRefName", "--limit", "5", cwd=root,
               check=False)
        if r.returncode != 0:
            return ""
        try:
            items = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            return ""
        items = [i for i in items if (i.get("baseRefName") or "") == base]
    if not items:
        return ""
    return (items[0].get("url") or "").strip()


def attach_review_branch(root, scope, tid, title, branch, integration):
    """Attach a recorded review branch. Never create from integration."""
    remote = f"origin/{branch}"
    if has_remote(root):
        git("fetch", "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
            cwd=root, check=False)
    if not ref_exists(root, remote):
        sys.exit(f"error: T{tid} is in review but '{branch}' is not "
                 f"checked out and {remote} is missing")
    sync_task_branch_from_remote(root, branch)
    ready = resolve_task_ready_path(
        root, scope, tid, title, branch, integration)
    print(f"note: attached review branch '{branch}'")
    return ready


def ship_work_cwd(root, scope, meta, branch, integration):
    """Directory that has the task branch checked out for committing/pushing.

    Prefer a claim-style linked worktree under .dev/worktrees/; fall back to
    any checkout of the branch. A review task with no checkout attaches
    origin/<branch> (never created from integration). Soft-warn when not
    under .dev/worktrees/.
    """
    work = None
    for path in find_task_worktree_paths(root, scope, meta["id"], branch):
        if current_branch_name(path) != branch:
            continue
        if path_under_task_worktrees(root, scope, path):
            return path
        if work is None:
            work = path
    if work is None:
        work = branch_checkout_cwd(root, branch)
    if not work:
        if (meta.get("status") or "").strip() == "review":
            return attach_review_branch(
                root, scope, meta["id"], meta["title"], branch, integration)
        sys.exit(f"error: task branch '{branch}' is not checked out anywhere; "
                 f"run claim first")
    if not path_under_task_worktrees(root, scope, work):
        print(f"warning: shipping from {work} (not under "
              f"<scope>/.dev/worktrees/); prefer the claim workdir",
              file=sys.stderr)
    return work


def cmd_diff(args):
    """Self-review: location + diff from the task worktree, not session cwd."""
    root, scope, integration, bw = ctx()
    meta = find_task(bw, scope, args.id)
    tid = meta["id"]
    branch = task_branch_name(meta)
    if not branch:
        if meta["status"] == "review":
            sys.exit(f"error: T{tid} is in review with no branch")
        sys.exit(f"error: T{tid} has no branch; run claim first")
    work = ship_work_cwd(root, scope, meta, branch, integration)
    base = f"origin/{integration}"
    if not ref_exists(root, base):
        base = integration
        if not ref_exists(root, base):
            sys.exit(f"error: no {base} to diff against")

    print(f"T{tid}: diff")
    print(f"  workdir: {work}")
    print(f"  product: {product_root(work, scope)}")
    print(f"  branch:  {branch}")
    print(f"  base:    {base}")

    st = git("status", "--porcelain", cwd=work, check=False)
    if st.returncode != 0:
        err = (st.stderr or st.stdout or "").strip()
        sys.exit(f"error: git status failed in {work}:\n{err}")
    porcelain = st.stdout or ""
    if porcelain.strip():
        print("uncommitted:")
        sys.stdout.write(porcelain)
        if not porcelain.endswith("\n"):
            sys.stdout.write("\n")
        tracked = git("diff", "HEAD", cwd=work, check=False)
        if tracked.returncode != 0:
            err = (tracked.stderr or tracked.stdout or "").strip()
            sys.exit(f"error: git diff HEAD failed in {work}:\n{err}")
        if (tracked.stdout or "").strip():
            sys.stdout.write(tracked.stdout)
            if not tracked.stdout.endswith("\n"):
                sys.stdout.write("\n")
    else:
        print("uncommitted: (none)")

    print(f"vs {base}:")
    r = git("diff", f"{base}...HEAD", cwd=work, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        sys.exit(f"error: git diff {base}...HEAD failed:\n{err}")
    out = r.stdout or ""
    if out.strip():
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    else:
        print("(none)")


def commits_ahead(root, branch, base_ref):
    """Commits on branch not in base_ref, or None if rev-list failed."""
    r = git("rev-list", "--count", f"{base_ref}..{branch}", cwd=root,
            check=False)
    if r.returncode != 0:
        return None
    s = (r.stdout or "").strip()
    if not s.isdigit():
        return None
    return int(s)


# ---------- Dev-batch stamp + restack / batch-gate ----------

DEV_BATCH_RE = re.compile(
    r"(?im)^\s*Dev-batch:\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*$")


def parse_id_list(s):
    """Parse '1,2,3' or '1 2 3' into a sorted unique list of ints."""
    if not s:
        return []
    ids = [int(x) for x in re.findall(r"\d+", str(s))]
    return sorted(set(ids))


def format_dev_batch(ids):
    """Machine line: Dev-batch: 19,20,22,23 (sorted)."""
    ids = sorted(set(int(i) for i in ids))
    return "Dev-batch: " + ",".join(str(i) for i in ids)


def parse_dev_batch(text):
    """Extract Dev-batch ids from task body or PR body, or []."""
    if not text:
        return []
    m = DEV_BATCH_RE.search(text)
    if not m:
        return []
    return parse_id_list(m.group(1))


def ensure_dev_batch_in_text(text, ids):
    """Insert or replace Dev-batch line; returns new text."""
    line = format_dev_batch(ids)
    text = text or ""
    if DEV_BATCH_RE.search(text):
        return DEV_BATCH_RE.sub(line, text, count=1)
    return text.rstrip() + ("\n\n" if text.strip() else "") + line + "\n"


# ---------- shipped-state record ----------

# One record per ship, as its own paragraph in the task body:
#   Shipped (2026-08-12): what actually landed, not what was planned.
SHIPPED_RE = re.compile(
    r"(?ims)^Shipped \(\d{4}-\d{2}-\d{2}\):.*?(?=\n[ \t]*\n|\Z)")


def format_shipped(text, date=None):
    """One shipped record. Collapsed to a single paragraph so it parses back."""
    text = " ".join((text or "").split())
    date = date or datetime.date.today().isoformat()
    return f"Shipped ({date}): {text}"


def collect_shipped(text):
    """Every shipped record in a task or PR body, in order."""
    return [m.group(0).strip() for m in SHIPPED_RE.finditer(text or "")]


def ensure_shipped_in_text(text, records):
    """Replace the shipped records in text with `records`, appended at the end.

    Marker-free and idempotent like ensure_dev_batch_in_text: re-shipping
    rewrites the block rather than stacking duplicates onto the PR body.
    """
    text = re.sub(r"\n{3,}", "\n\n", SHIPPED_RE.sub("", text or "")).strip()
    if not records:
        return text + "\n" if text else ""
    block = "\n\n".join(records)
    return (text + "\n\n" + block if text else block) + "\n"


def pr_is_open(info):
    if not info:
        return False
    return (info.get("state") or "").upper() == "OPEN" and not pr_is_merged(info)


def load_task_pr_stack_info(root, bw, scope, tid):
    """Return dict: id, branch, pr, base, head, open, body, status, deps."""
    meta = find_task(bw, scope, tid)
    pr = (meta.get("pr") or "").strip()
    branch = task_branch_name(meta)
    info = pr_view(root, pr, soft=True) if pr else None
    base = ((info or {}).get("baseRefName") or "").strip()
    head = ((info or {}).get("headRefName") or "").strip() or branch
    body = (info or {}).get("body") or ""
    return {
        "id": tid,
        "meta": meta,
        "status": meta.get("status") or "",
        "branch": branch,
        "pr": pr,
        "base": base,
        "head": head,
        "open": pr_is_open(info),
        "body": body,
        "deps": list(meta.get("deps") or []),
        "task_body": meta.get("body") or "",
    }


def discover_batch_ids_for_task(root, bw, scope, tid, integration):
    """Full batch id list for a task: stamp first, else stack component."""
    info = load_task_pr_stack_info(root, bw, scope, tid)
    stamped = parse_dev_batch(info["body"]) or parse_dev_batch(info["task_body"])
    if stamped:
        return stamped
    # Fallback: connected component via PR base↔head among status=review.
    return stack_component_ids(root, bw, scope, tid, integration)


def stack_component_ids(root, bw, scope, seed_tid, integration):
    """Connected component of review tasks linked by PR base == other head."""
    review = [t for t in all_tasks(bw, scope) if t["status"] == "review"]
    infos = {}
    for t in review:
        infos[t["id"]] = load_task_pr_stack_info(root, bw, scope, t["id"])
    if seed_tid not in infos:
        # Seed may still be review-stamped but status moved; include alone.
        return [seed_tid] if find_task(bw, scope, seed_tid) else []
    # Map branch name -> task id for members
    by_branch = {}
    for tid, inf in infos.items():
        b = (inf.get("branch") or inf.get("head") or "").strip()
        if b:
            by_branch[b] = tid
    # Undirected edges via base pointing at another member's branch
    adj = {tid: set() for tid in infos}
    for tid, inf in infos.items():
        base = (inf.get("base") or "").strip()
        if not base or base == integration:
            continue
        parent = by_branch.get(base)
        if parent is not None and parent != tid:
            adj[tid].add(parent)
            adj[parent].add(tid)
    # BFS from seed
    seen = set()
    stack = [seed_tid]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in adj:
            continue
        seen.add(cur)
        stack.extend(adj[cur] - seen)
    return sorted(seen) if seen else [seed_tid]


def open_batch_comembers(root, bw, scope, batch_ids):
    """Subset of batch_ids still in review with an open PR."""
    open_ids = []
    for tid in batch_ids:
        try:
            meta = find_task(bw, scope, tid)
        except SystemExit:
            continue
        if meta.get("status") != "review":
            continue
        pr = (meta.get("pr") or "").strip()
        if not pr:
            continue
        info = pr_view(root, pr, soft=True)
        if pr_is_open(info):
            open_ids.append(tid)
    return open_ids


def cmd_batch_gate(args):
    """Refuse partial review of an open Dev-batch (or stack component).

    Exit 0 if selection covers every still-open co-member of the batch(es)
    touched by the selection. Exit 2 if a proper subset (list missing ids).
    """
    root, scope, integration, bw = ctx()
    selected = parse_id_list(args.ids or "")
    if not selected:
        sys.exit("error: batch-gate requires --ids <id,id,…>")

    require_gh(root)
    # Union of still-open co-members for every selected id's batch
    required = set()
    for tid in selected:
        try:
            find_task(bw, scope, tid)
        except SystemExit:
            sys.exit(f"error: no task with id {tid}")
        batch = discover_batch_ids_for_task(root, bw, scope, tid, integration)
        open_ids = open_batch_comembers(root, bw, scope, batch)
        if not open_ids:
            continue
        required.update(open_ids)

    selected_set = set(selected)
    # If nothing required (no open batch), selection is fine.
    if not required:
        print(f"batch-gate: ok — no open batch co-members for "
              f"{', '.join(f'T{i}' for i in selected)}")
        return

    missing = sorted(required - selected_set)
    if missing:
        print(f"batch-gate: incomplete set — still open co-members missing: "
              f"{', '.join(f'T{i}' for i in missing)}")
        print(f"  selected: {', '.join(f'T{i}' for i in selected)}")
        print(f"  required open set: "
              f"{', '.join(f'T{i}' for i in sorted(required))}")
        print(f"  re-run: /dev review "
              f"{','.join(str(i) for i in sorted(required))}")
        sys.exit(2)
    print(f"batch-gate: ok — "
          f"{', '.join(f'T{i}' for i in sorted(required))}")


def build_stack_parent_map(infos, integration):
    """Map child_id -> parent_id when child's PR base is parent's branch."""
    by_branch = {}
    for tid, inf in infos.items():
        b = (inf.get("branch") or inf.get("head") or "").strip()
        if b:
            by_branch[b] = tid
    parent = {}
    for tid, inf in infos.items():
        base = (inf.get("base") or "").strip()
        if not base or base == integration:
            continue
        p = by_branch.get(base)
        if p is not None and p != tid:
            parent[tid] = p
    return parent


def topo_order_ids(ids, parent_map, dep_edges):
    """Topo order: parents/deps before children. Tie-break lower id."""
    ids = sorted(set(ids))
    # edges: A -> B means A before B (B depends on A)
    preds = {i: set() for i in ids}
    for i in ids:
        p = parent_map.get(i)
        if p in preds:
            preds[i].add(p)
        for d in dep_edges.get(i, []):
            if d in preds:
                preds[i].add(d)
    ordered = []
    ready = sorted(i for i, ps in preds.items() if not ps)
    seen = set()
    while ready:
        n = ready.pop(0)
        if n in seen:
            continue
        seen.add(n)
        ordered.append(n)
        for i, ps in preds.items():
            if n in ps:
                ps.discard(n)
                if not ps and i not in seen:
                    ready.append(i)
        ready.sort()
    if len(ordered) != len(ids):
        cycle = sorted(set(ids) - set(ordered))
        label = ", ".join(f"T{i}" for i in cycle)
        sys.exit(f"error: cycle in restack set among {label}")
    return ordered


def descendants_of(parent_map, root_id, ids):
    """Ids in set that are stack-descendants of root_id (not including root)."""
    children = {i: [] for i in ids}
    for c, p in parent_map.items():
        if c in children and p in children:
            children[p].append(c)
    out = []
    stack = list(children.get(root_id, []))
    seen = set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
        stack.extend(children.get(n, []))
    return out


def cmd_restack(args):
    """Fail-closed stack restack for a Dev-batch / stack set.

    Prints a plan, then applies unless --dry-run. force-with-lease only;
    dirty worktrees refuse; conflicts abort the current rebase and stop
    (earlier plan steps may already be force-pushed — re-run restack after
    resolve; already-up-to-date members no-op). When a member's stack parent
    is outside --ids, rebases onto integration and auto-retargets the PR
    base (land-safe default); --onto / --retarget still force retarget.
    --onto rebases every target onto that ref (does not cascade children
    onto restacked parents); omit it to preserve in-set stack parents.
    Moving onto a different ref uses `rebase --onto` excluding the old PR
    base tip so a rewritten parent's commits are not replayed.
    """
    root, scope, integration, bw = ctx()
    ids = parse_id_list(args.ids or "")
    if not ids:
        sys.exit("error: restack requires --ids <id,id,…>")
    require_gh(root)
    if not has_remote(root):
        sys.exit("error: no origin remote; cannot restack")

    after = args.after
    onto = (args.onto or "").strip()
    dry = bool(args.dry_run)
    retarget = bool(args.retarget)

    # Never rewrite integration as a task branch.
    for tid in ids:
        meta = find_task(bw, scope, tid)
        b = task_branch_name(meta)
        if b and b == integration:
            sys.exit(f"error: T{tid} branch is integration '{integration}'; "
                     "refusing restack")

    git("fetch", "origin", cwd=root, check=False)
    infos = {tid: load_task_pr_stack_info(root, bw, scope, tid) for tid in ids}
    parent_map = build_stack_parent_map(infos, integration)
    dep_edges = {tid: infos[tid]["deps"] for tid in ids}

    if after is not None:
        if after not in ids:
            sys.exit(f"error: --after {after} is not in --ids")
        targets = descendants_of(parent_map, after, ids)
        if not targets:
            print(f"restack: no stack descendants of T{after} in the set")
            return
    else:
        targets = list(ids)

    order = topo_order_ids(targets, parent_map, dep_edges)

    plan = []
    for tid in order:
        inf = infos[tid]
        branch = inf["branch"]
        if not branch:
            sys.exit(f"error: T{tid} has no branch recorded")
        if onto:
            upstream = onto if onto.startswith("origin/") else (
                f"origin/{onto}" if ref_exists(root, f"origin/{onto}") else onto)
            new_base_for_pr = onto[len("origin/"):] if onto.startswith(
                "origin/") else onto
        else:
            p = parent_map.get(tid)
            if p is not None:
                pbranch = infos[p]["branch"]
                upstream = (f"origin/{pbranch}"
                            if ref_exists(root, f"origin/{pbranch}")
                            else pbranch)
                new_base_for_pr = pbranch
            else:
                upstream = f"origin/{integration}"
                new_base_for_pr = integration
        # Retarget when: explicit --retarget / --onto, or land-safe default —
        # stack parent is outside the set so we rebase onto integration while
        # the PR still targets a sibling (or other non-integration) base.
        old_base = (inf.get("base") or "").strip()
        want_retarget = bool(retarget or onto)
        if (not want_retarget
                and new_base_for_pr == integration
                and old_base and old_base != integration):
            want_retarget = True
        planned_new_base = new_base_for_pr if want_retarget else old_base
        exclude = ""
        if old_base and ref_short_name(old_base) != ref_short_name(upstream):
            exclude = old_base
        plan.append({
            "id": tid,
            "branch": branch,
            "upstream": upstream,
            "pr": inf["pr"],
            "old_base": old_base,
            "exclude": exclude,
            "new_base": planned_new_base,
            "retarget": want_retarget and old_base != planned_new_base,
        })

    print("restack plan:")
    for step in plan:
        rflag = f" retarget-base→{step['new_base']}" if step.get("retarget") else ""
        xflag = f" excluding {step['exclude']}" if step.get("exclude") else ""
        print(f"  T{step['id']}: {step['branch']} onto {step['upstream']}"
              f"{xflag}{rflag}")
    if dry:
        print("restack: dry-run only (no changes)")
        return

    for step in plan:
        tid = step["id"]
        branch = step["branch"]
        upstream = step["upstream"]
        # Dirty check on any checkout of the branch
        checkout = branch_checkout_cwd(root, branch)
        if checkout and worktree_is_dirty(checkout):
            sys.exit(f"error: T{tid} worktree dirty at {checkout}; "
                     "commit/stash before restack")
        exclude = step.get("exclude") or None
        onto = f"{upstream} (excluding {exclude})" if exclude else upstream
        print(f"restack T{tid}: rebase {branch} onto {onto}…")
        try:
            rewritten = rebase_task_onto_ref(
                root, branch, upstream, old_base=exclude)
        except SystemExit:
            raise
        if rewritten:
            print(f"  rewritten; push --force-with-lease")
            push_task_branch(root, branch, force=True)
        else:
            print(f"  already up to date with {upstream}")
            push_task_branch(root, branch, force=False)
        if step.get("retarget") and step["pr"] and step["new_base"]:
            if step["old_base"] != step["new_base"]:
                r = gh("pr", "edit", step["pr"], "--base", step["new_base"],
                       cwd=root, check=False)
                if r.returncode != 0:
                    err = (r.stderr or r.stdout or "").strip()
                    sys.exit(f"error: could not retarget T{tid} PR base to "
                             f"{step['new_base']}:\n{err}")
                print(f"  PR base {step['old_base'] or '?'} → {step['new_base']}")
    print("restack: done")


def cmd_ship(args):
    """Ship end of implement: commit if needed, push, open PR, mark review.

    Mirrors hand-rolled implement ship, with stricter guards: never commit
    .tasks/ paths, refuse empty ship, one push via push_task_branch.
    Version intent is agent-owned (optional --version-intent / --body only).
    """
    root, scope, integration, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    idx = iteration_index(cfg)
    meta = find_task(bw, scope, args.id)
    tid = meta["id"]
    prefix = title_prefix(idx, tid)
    branch = task_branch_name(meta)
    if not branch:
        if meta["status"] == "review":
            sys.exit(f"error: T{tid} is in review with no branch")
        sys.exit(f"error: T{tid} has no branch; run claim first")
    if meta["status"] in ("done", "later", "not-planned", "proposed"):
        sys.exit(f"error: T{tid} is {meta['status']}; cannot ship")

    # A ship without a result record is what leaves the PR body a stale copy
    # of pre-implementation intent. Required on every ship, including re-ship
    # after changes-requested — that is when the record drifts most.
    shipped = (args.shipped or "").strip()
    if not shipped:
        sys.exit(f"error: T{tid} needs --shipped \"<what actually shipped>\"; "
                 "one or two sentences on the result, not the plan "
                 "(re-ship: what changed since the last ship)")
    shipped_line = format_shipped(shipped)
    shipped_records = collect_shipped(meta.get("body") or "") + [shipped_line]

    require_gh(root)
    if not has_remote(root):
        sys.exit("error: no origin remote; cannot ship")

    work = ship_work_cwd(root, scope, meta, branch, integration)
    print(f"T{tid}: ship from {work} (branch {branch})")

    if worktree_is_dirty(work):
        msg = (args.message or "").strip() or f"{prefix} {meta['title']}"
        if not msg.startswith(prefix):
            msg = f"{prefix} {msg}"
        r = git("add", "-A", cwd=work, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: git add failed in {work}:\n{err}")
        staged = git("diff", "--cached", "--name-only", cwd=work,
                     check=False).stdout.strip()
        if not staged:
            sys.exit(f"error: worktree dirty but nothing staged in {work}")
        board_paths = [ln for ln in staged.splitlines()
                       if path_is_tasks_dir(ln)]
        if board_paths:
            git("reset", "HEAD", cwd=work, check=False)
            listed = "\n".join(f"  {p}" for p in board_paths[:20])
            more = "" if len(board_paths) <= 20 else f"\n  …+{len(board_paths)-20} more"
            sys.exit(f"error: .tasks/ paths staged in {work}; board state "
                     f"must not ship on a code branch:\n{listed}{more}")
        r = git("commit", "-m", msg, cwd=work, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: commit failed in {work}:\n{err}")
        print(f"  committed: {msg}")

    # PR base defaults to integration; --base stacks on another task branch.
    pr_base = (args.base or "").strip() or integration

    batch_ids = parse_id_list(getattr(args, "batch", None) or "")
    if batch_ids and tid not in batch_ids:
        batch_ids = sorted(set(batch_ids) | {tid})
    batch_line = format_dev_batch(batch_ids) if batch_ids else ""

    pr_url = (meta.get("pr") or "").strip()
    if pr_url:
        info = pr_view(root, pr_url, soft=True)
        if info and pr_is_merged(info):
            sys.exit(f"error: T{tid} PR already merged: {pr_url}")
        if not (info and (info.get("state") or "").upper() == "OPEN"):
            # Closed/unknown recorded URL — look for a live open PR.
            # Open PR is reused as-is; --title/--body/--version-intent/--base
            # apply only when creating below.
            pr_url = find_open_pr_for_branch(root, branch, pr_base)
    else:
        pr_url = find_open_pr_for_branch(root, branch, pr_base)

    git("fetch", "origin", integration, cwd=root, check=False)
    if not pr_url:
        # Nothing to open: clean tree and no commits ahead of integration.
        ahead = commits_ahead(root, branch, f"origin/{integration}")
        if ahead is None:
            # Branch may lack remote tracking yet; try local integration.
            ahead = commits_ahead(root, branch, integration)
        if ahead is None or ahead == 0:
            sys.exit(f"error: nothing to ship on '{branch}' (clean worktree, "
                     f"no commits ahead of origin/{integration}, no open PR)")

    # One push (same helper as land). Explicit refspec; no -u (skill ship is
    # plain "push"; park-as-PR is the only flow that documents git push -u).
    push_task_branch(root, branch, force=False)

    if not pr_url:
        title = (args.title or "").strip() or f"{prefix} {meta['title']}"
        if not title.startswith(prefix):
            title = f"{prefix} {title}"
        body = (args.body or "").strip()
        if not body:
            body = (meta.get("body") or "").strip() or meta["title"]
        intent = (args.version_intent or "").strip()
        if intent and not re.search(r"(?im)^\s*Version intent:", body):
            body = body.rstrip() + f"\n\nVersion intent: {intent}\n"
        if batch_line:
            body = ensure_dev_batch_in_text(body, batch_ids)
        body = ensure_shipped_in_text(body, shipped_records)
        r = gh("pr", "create", "--base", pr_base, "--head", branch,
               "--title", title, "--body", body, cwd=root, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            pr_url = find_open_pr_for_branch(root, branch, pr_base)
            if not pr_url:
                sys.exit(f"error: gh pr create failed:\n{err}")
            print(f"  PR appeared during create: {pr_url}")
        else:
            pr_url = (r.stdout or "").strip().splitlines()[-1].strip()
            print(f"  opened PR: {pr_url}")
    else:
        print(f"  PR: {pr_url}")
        # Re-ship: refresh the shipped records on the open PR (and the
        # Dev-batch stamp when --batch given). The task body is the source of
        # truth; a failed edit only costs the mirror, so it warns.
        info = pr_view(root, pr_url, soft=True) or {}
        body = info.get("body") or ""
        new_body = body
        if batch_ids and parse_dev_batch(new_body) != batch_ids:
            new_body = ensure_dev_batch_in_text(new_body, batch_ids)
        new_body = ensure_shipped_in_text(new_body, shipped_records)
        if new_body.strip() != body.strip():
            r = gh("pr", "edit", pr_url, "--body", new_body, cwd=root,
                   check=False)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip()
                print(f"  warning: could not update PR body: {err}",
                      file=sys.stderr)
            else:
                print(f"  updated PR body: {shipped_line}")

    integration, bw = resolve_board(root, scope)
    meta = find_task(bw, scope, tid)
    changes = []
    if meta["status"] != "review":
        meta["status"] = "review"
        changes.append("status=review")
    if meta.get("pr") != pr_url:
        meta["pr"] = pr_url
        changes.append(f"pr={pr_url}")
    if batch_ids and parse_dev_batch(meta.get("body") or "") != batch_ids:
        meta["body"] = ensure_dev_batch_in_text(meta.get("body") or "",
                                               batch_ids)
        changes.append("batch-stamp")
    meta["body"] = append_body(meta.get("body") or "", shipped_line)
    changes.append("shipped")
    if changes:
        with open(meta["path"], "w") as f:
            f.write(render_task(meta))
        board_commit(root, integration, bw, scope,
                     f"dev: update T{tid} ({', '.join(changes)})")
        print(f"T{tid} updated: {', '.join(changes)}")
    print(f"T{tid}: shipped → {pr_url}")


def scope_path_is_in_scope(scope, path):
    """Whether a repo-relative path is in this board's product scope."""
    norm = path.replace("\\", "/").lstrip("./")
    if scope in (".", ""):
        return True
    prefix = scope.replace("\\", "/").rstrip("/") + "/"
    return (norm == scope.replace("\\", "/").rstrip("/")
            or norm.startswith(prefix))


def local_integration_ahead_state(root, scope, integration):
    """Describe local integration commits ahead of origin.

    Returns dict with ahead, commits, in_scope, out_of_scope path lists.
    Caller should fetch origin first.
    """
    remote_ref = f"origin/{integration}"
    if not ref_exists(root, remote_ref):
        return {"ahead": 0, "commits": [], "in_scope": [], "out_of_scope": [],
                "missing_remote": True}
    if not ref_exists(root, f"refs/heads/{integration}"):
        return {"ahead": 0, "commits": [], "in_scope": [], "out_of_scope": [],
                "missing_local": True}
    n = git("rev-list", "--count", f"{remote_ref}..{integration}",
            cwd=root).stdout.strip()
    ahead = int(n or "0")
    commits = []
    if ahead:
        commits = git(
            "log", "--oneline", f"{remote_ref}..{integration}", cwd=root
        ).stdout.strip().splitlines()
    paths = []
    if ahead:
        paths = git(
            "diff", "--name-only", f"{remote_ref}..{integration}", cwd=root
        ).stdout.strip().splitlines()
    in_scope, out_of_scope = [], []
    for p in paths:
        if not p:
            continue
        if scope_path_is_in_scope(scope, p):
            in_scope.append(p)
        else:
            out_of_scope.append(p)
    return {"ahead": ahead, "commits": commits, "in_scope": in_scope,
            "out_of_scope": out_of_scope}


def cmd_preflight(args):
    """Local integration ahead of origin: check, park-as-PR, or discard.

    Exit 0: clear, or ahead only on out-of-scope paths.
    Exit 2: in-scope ahead in check mode (implement must stop).
    Park/discard require a clean working tree checked out on integration.
    """
    root, scope, integration, bw = ctx()
    mode = "check"
    if args.park:
        mode = "park"
    if args.discard:
        if mode == "park":
            sys.exit("error: pass only one of --park / --discard")
        mode = "discard"

    if not has_remote(root):
        sys.exit("error: no origin remote; cannot preflight")
    git("fetch", "origin", integration, cwd=root, check=False)
    state = local_integration_ahead_state(root, scope, integration)
    if state.get("missing_remote"):
        sys.exit(f"error: origin/{integration} missing after fetch")
    if state.get("missing_local"):
        print(f"preflight: no local branch '{integration}' "
              f"(task branches use origin/{integration}; clear)")
        return

    ahead = state["ahead"]
    if ahead == 0:
        print(f"preflight: local '{integration}' is not ahead of "
              f"origin/{integration}")
        return

    print(f"preflight: local '{integration}' is {ahead} commit(s) ahead of "
          f"origin/{integration}")
    for c in state["commits"]:
        print(f"  {c}")
    if state["in_scope"]:
        print("  in-scope paths:")
        for p in state["in_scope"]:
            print(f"    {p}")
    if state["out_of_scope"]:
        print("  out-of-scope paths:")
        for p in state["out_of_scope"]:
            print(f"    {p}")

    if not state["in_scope"]:
        print("preflight: ahead only on out-of-scope paths — ok to continue")
        return

    if mode == "check":
        print("preflight: in-scope ahead — park-as-PR or discard before "
              "implement (TASKS preflight --park | --discard)")
        sys.exit(2)

    # Park/discard hard-reset the primary clone's integration branch (claim
    # hub). A task worktree on another branch is not enough.
    cur = current_branch_name(root)
    if cur != integration:
        sys.exit(
            f"error: park/discard need primary clone on '{integration}' "
            f"(primary={root}, currently '{cur or 'detached/unknown'}'). "
            f"Checkout '{integration}' there and re-run — task worktrees "
            f"are not enough."
        )
    if worktree_is_dirty(root):
        sys.exit(f"error: working tree on '{integration}' is dirty; "
                 "commit/stash/elsewhere before park or discard")

    if mode == "discard":
        git("reset", "--hard", f"origin/{integration}", cwd=root)
        print(f"preflight: discarded local commits; '{integration}' == "
              f"origin/{integration}")
        return

    require_gh(root)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y%m%d-%H%M%S")
    park_branch = f"park/{integration}-{stamp}"
    git("branch", park_branch, integration, cwd=root)
    r = git("push", "-u", "origin", park_branch, cwd=root, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        # Push failed: nothing durable on origin; drop local park ref only.
        # Ahead commits remain on local integration.
        git("branch", "-D", park_branch, cwd=root, check=False)
        sys.exit(f"error: push of park branch failed:\n{err}")

    # Commits are safe on local + remote park/*. Reset local integration to
    # origin so implement can proceed even if PR create fails next.
    # (origin/<integration> is already the desired tip — ahead was local-only.)
    git("reset", "--hard", f"origin/{integration}", cwd=root)
    print(f"preflight: parked on {park_branch}", flush=True)
    print(f"  local '{integration}' reset to origin/{integration}",
          flush=True)

    title = f"park: local {integration} ahead of origin ({stamp})"
    body = (
        "Parked local integration commits that were ahead of origin "
        "before implement.\n\n"
        f"Scope: {scope}\n"
        f"Commits:\n" + "\n".join(f"- {c}" for c in state["commits"])
    )
    pr_err = ""
    pr_url = ""
    for attempt in range(1, 4):
        r = gh("pr", "create", "--base", integration, "--head", park_branch,
               "--title", title, "--body", body, cwd=root, check=False)
        if r.returncode == 0:
            pr_url = (r.stdout or "").strip().splitlines()[-1].strip()
            break
        pr_err = (r.stderr or r.stdout or "").strip()
        if attempt < 3:
            print(f"preflight: gh pr create failed (attempt {attempt}/3); "
                  f"retrying…", flush=True)
            time.sleep(1)
    if pr_url:
        print(f"  PR: {pr_url}")
        return
    # Park succeeded (remote park/* + local integration reset). PR create is
    # the happy path but must not block implement — exit 0 and yell so the
    # agent surfaces the failure to the user.
    print(
        "WARNING: preflight: gh pr create failed after retries — "
        "SURFACE THIS TO THE USER.\n"
        f"  park branch is safe — do not delete '{park_branch}' "
        f"(local or origin) until reviewed.\n"
        f"  finish PR: gh pr create --base {integration} "
        f"--head {park_branch} --title {title!r}\n"
        f"  local '{integration}' already reset; implement may proceed.\n"
        f"{pr_err}",
        flush=True,
    )


def iteration_close_ready(bw, scope, index):
    """Return an error string if the board is not closed for land, else None.

    Ready means: no live task files, and log.md has a close heading for this
    iteration index (written by iteration-close as ``## {n}`` or
    ``## {n} — {name}``).
    """
    tasks = all_tasks(bw, scope)
    if tasks:
        ids = ", ".join(f"T{t['id']}" for t in tasks)
        return (f"board still has tasks ({ids}); run TASKS iteration-close "
                f"before iteration-land")
    log_path = os.path.join(bw, tdir(scope), "log.md")
    rel = f"{tdir(scope)}/log.md"
    if not os.path.isfile(log_path):
        return (f"no {rel} close entry for iteration {index}; "
                f"run TASKS iteration-close first")
    with open(log_path) as f:
        text = f.read()
    if not log_has_close_section(text, index):
        return (f"no log close section for iteration {index} in "
                f"{rel}; run TASKS iteration-close first")
    return None


def live_iteration_closed(bw, scope, cfg):
    """True when the live iteration is closed in log.md and the board is empty."""
    return iteration_close_ready(bw, scope, iteration_index(cfg)) is None


def cmd_iteration_land(args):
    """Open/merge the iteration PR (merge commit) into parent_branch.

    Idempotent when origin/integration is already contained in
    origin/parent (prior land, manual merge, or empty delta). Does not
    auto-approve. Requires a closed board: no task files and a log.md
    close section for the current iteration.
    """
    root, scope, integration, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    parent = (cfg.get("parent_branch") or "").strip()
    if not parent:
        sys.exit("error: no parent_branch configured; iteration land needs a "
                 "parent to merge into (main-with-no-parent boards never close)")
    if parent == integration:
        sys.exit(f"error: parent_branch equals integration_branch "
                 f"('{integration}')")
    idx = iteration_index(cfg)
    not_ready = iteration_close_ready(bw, scope, idx)
    if not_ready:
        sys.exit(f"error: {not_ready}")
    require_gh(root)
    if not has_remote(root):
        sys.exit("error: no origin remote; cannot iteration-land")

    git("fetch", "origin", parent, integration, cwd=root, check=False)
    if not ref_exists(root, f"origin/{integration}"):
        sys.exit(f"error: origin/{integration} missing; push the integration "
                 "branch before iteration-land")
    if not ref_exists(root, f"origin/{parent}"):
        sys.exit(f"error: origin/{parent} missing after fetch")

    # Prefer an open PR; remember a prior MERGED URL for messaging only.
    # "Already landed" is git ancestry (integration tip in parent), not
    # merely the existence of a past MERGED PR — integration may have
    # advanced after that land and still need a new PR.
    open_url = ""
    merged_url = ""
    r = gh("pr", "list", "--head", integration, "--base", parent,
           "--state", "all", "--json", "url,state,mergedAt,number",
           "--limit", "10", cwd=root, check=False)
    if r.returncode == 0:
        try:
            items = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            items = []
        for it in items:
            st = (it.get("state") or "").upper()
            if st == "OPEN" and not open_url:
                open_url = (it.get("url") or "").strip()
            elif (st == "MERGED" or it.get("mergedAt")) and not merged_url:
                merged_url = (it.get("url") or "").strip()

    anc = git("merge-base", "--is-ancestor",
              f"origin/{integration}", f"origin/{parent}",
              cwd=root, check=False)
    if anc.returncode == 0:
        note = f" ({merged_url})" if merged_url else ""
        print(f"iteration-land: already merged{note}")
        print("next: TASKS iteration-new <branch> when ready")
        return

    pr_url = open_url
    if not pr_url:
        label = iteration_label(cfg)
        title = (args.title or "").strip() or f"iteration {label}"
        body = (args.body or "").strip() or (
            f"Land iteration {label} (`{integration}` → `{parent}`) "
            f"with a merge commit (not squash).\n")
        r = gh("pr", "create", "--base", parent, "--head", integration,
               "--title", title, "--body", body, cwd=root, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            sys.exit(f"error: gh pr create failed:\n{err}")
        pr_url = (r.stdout or "").strip().splitlines()[-1].strip()
        print(f"iteration-land: opened {pr_url}")
    else:
        print(f"iteration-land: existing open PR {pr_url}")

    if args.create_only:
        print("iteration-land: --create-only set; not merging")
        return

    last_err = ""
    for attempt in range(1, LAND_RETRIES + 1):
        readiness = wait_for_pr_mergeable(root, pr_url)
        if readiness == "MERGED":
            print("iteration-land: PR became merged while waiting")
            print("next: TASKS iteration-new <branch>")
            return
        if readiness == "CONFLICTING":
            sys.exit(f"error: iteration PR is CONFLICTING; resolve and "
                     f"re-run iteration-land:\n{pr_url}")
        r = gh("pr", "merge", pr_url, "--merge", cwd=root, check=False)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        if r.returncode == 0:
            print(f"iteration-land: merged (merge commit) attempt {attempt}")
            print("next: TASKS iteration-new <branch>")
            return
        last_err = out
        low = out.lower()
        if "already merged" in low:
            print("iteration-land: already merged")
            print("next: TASKS iteration-new <branch>")
            return
        info = pr_view(root, pr_url, soft=True)
        if info and pr_is_merged(info):
            print("iteration-land: already merged")
            print("next: TASKS iteration-new <branch>")
            return
        if not merge_error_transient(out):
            sys.exit(merge_fail_permanent_message(out))
        print(f"  merge attempt {attempt} failed (transient); retry…")
        if attempt < LAND_RETRIES:
            time.sleep(min(2 * attempt, 8))
    sys.exit(f"error: could not merge iteration PR after {LAND_RETRIES} "
             f"attempts:\n{last_err}")


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
    # match any depth — product-local paths are covered). Viewer ignore is
    # path-anchored and omitted when <product>/board is already a directory.
    dest = viewer_path(root, scope)
    ignore_lines = [".dev/", "TASKS.md"]
    if not os.path.isdir(dest):
        ignore_lines.append(viewer_ignore_line(scope))
    exclude = git_path(root, "info/exclude")
    for line in ignore_lines:
        append_ignore_line(exclude, line)
    branch, bw = resolve_board(root, scope)
    if not os.path.exists(board_yml_path(bw, scope)):
        os.makedirs(os.path.join(bw, tdir(scope)), exist_ok=True)
        idx = parse_positive_int(
            args.iteration if args.iteration is not None else 1, "iteration")
        started = parse_iso_date(
            args.iteration_started or datetime.date.today().isoformat(),
            "iteration_started")
        iname = (args.iteration_name or "").strip()
        require_schema3_archive_slug(idx, iname)
        cfg = {"schema_version": str(SCHEMA_VERSION),
               "integration_branch": branch, "parent_branch": args.parent or "",
               "iteration": str(idx),
               "iteration_name": iname,
               "iteration_started": started,
               "integrator": args.name, "contributors": args.name}
        write_board_cfg(bw, scope, cfg)
        gi = os.path.join(bw, ".gitignore")
        for line in ignore_lines:
            append_ignore_line(gi, line)
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
        gi = os.path.join(bw, ".gitignore")
        if (not os.path.isdir(dest)
                and append_ignore_line(gi, viewer_ignore_line(scope))):
            board_commit(root, branch, bw, scope,
                         "dev: ignore local board viewer")
    dest, _wrote = ensure_board_viewer(root, scope)
    if os.path.isdir(dest):
        print(f"viewer: skipped ({VIEWER_NAME} is a directory)")
    else:
        print(f"viewer: ./{VIEWER_NAME}  (r refresh, a by area, e expand, q quit, arrows scroll, type id↵)")
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
        value = args.value
        if args.key in ("iteration", "iteration_name", "iteration_started"):
            # Land gate matches log.md's "## {n}" heading. Changing identity,
            # display name, or start after close would desync the heading
            # (and, for a renumber, the archive path already written).
            if live_iteration_closed(bw, scope, cfg):
                cur = iteration_index(cfg)
                sys.exit(f"error: iteration {cur} is already closed in "
                         f"{tdir(scope)}/log.md; changing {args.key} now "
                         "would break iteration-land. Land it, then start "
                         "the next one with iteration-new.")
        if args.key == "iteration":
            new = parse_positive_int(value, "iteration")
            cur = iteration_index(cfg)
            name = iteration_name(cfg)
            require_schema3_archive_slug(new, name)
            if new != cur:
                taken = archive_taken(bw, scope, new, name)
                if taken:
                    sys.exit(f"error: iteration {new} already has an archive "
                             f"at {taken}/; pick another number.")
            value = str(new)
        elif args.key == "iteration_name":
            value = value.strip()
            idx = iteration_index(cfg)
            require_schema3_archive_slug(idx, value)
            taken = archive_taken(bw, scope, idx, value)
            if taken:
                sys.exit(f"error: iteration {idx} already has an archive "
                         f"at {taken}/; pick another name.")
        elif args.key == "iteration_started":
            value = parse_iso_date(value, "iteration_started")
        cfg[args.key] = value
        write_board_cfg(bw, scope, cfg)
        board_commit(root, branch, bw, scope, f"dev: config {args.key}={value}")
        print(f"{args.key}: {value}")


def cmd_area(args):
    root, scope, branch, bw = ctx()
    mods = read_areas(bw, scope)
    if args.action == "list":
        open_counts = {}
        for t in all_tasks(bw, scope):
            if occupies_area(t):
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
                if name in split_areas(t.get("area", "")) and occupies_area(t)]
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


def cmd_collisions(args):
    """Area occupancy vs doing/review. Exit 2 if any id is blocked.

    One id matches watch-mode. Several ids: each vs in-flight *outside*
    the set (batch peers are sequential); then ``set:`` lines for
    in-set area overlap (informational, does not fail).
    """
    root, scope, branch, bw = ctx()
    tasks = all_tasks(bw, scope)
    ids = parse_id_list(" ".join(args.ids))
    if not ids:
        sys.exit("error: collisions needs at least one task id")
    by_id = {t["id"]: t for t in tasks}
    missing = [i for i in ids if i not in by_id]
    if missing:
        sys.exit("error: no task with id " +
                 ",".join(str(i) for i in missing))
    exclude = ids if len(ids) > 1 else None
    blocked = False
    for tid in ids:
        task = by_id[tid]
        hits = in_flight_area_collisions(task, tasks, exclude_ids=exclude)
        print(format_area_collisions(task, hits))
        if hits:
            blocked = True
    if len(ids) > 1:
        for a, b in set_area_overlaps(ids, tasks):
            print(f"set: T{a['id']} overlaps T{b['id']}")
    if blocked:
        sys.exit(2)


# 16-color SGR. Paint-time only — never write these into TASKS.md.
_DIM, _OK, _ERR, _NEEDS = "2", "32", "31", "33;1"
_STATUS_COLOR = {
    "proposed": "2",      # dim
    "backlog": "34",      # blue
    "planned": "36",      # cyan
    "doing": "33",        # yellow
    "review": "35",       # magenta
    "done": "32",         # green
    "later": "2",
    "not-planned": "2",
}
_ASSIGNEE_COLORS = (
    "91", "92", "93", "94", "95", "96",
    "31", "32", "33", "34", "35", "36",
)


def _use_color():
    """Color tty stdout unless NO_COLOR is set or TERM is dumb."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def _ansi(code, text):
    return f"\033[{code}m{text}\033[0m"


def _status_ansi(status, text):
    return _ansi(_STATUS_COLOR.get(status, "0"), text)


def _assignee_ansi(name, text):
    n = 0
    for c in name:
        n = (n * 31 + ord(c)) & 0xFFFFFFFF
    return _ansi(_ASSIGNEE_COLORS[n % len(_ASSIGNEE_COLORS)], text)


def fmt_line(t, tasks, color=False):
    by_id = {x["id"]: x for x in tasks}
    blocked = any(by_id.get(d, {}).get("status") != "done" for d in t["deps"])
    tid, st = f"T{t['id']}", f"[{t['status']}]"
    if color:
        tid, st = _status_ansi(t["status"], tid), _status_ansi(t["status"], st)
    parts = [tid, st]
    if t.get("area"):
        area = f"({t['area']})"
        parts.append(_ansi(_DIM, area) if color else area)
    parts.append(t["title"])
    if t.get("assignee"):
        who = f"@{t['assignee']}"
        parts.append(_assignee_ansi(t["assignee"], who) if color else who)
    if t.get("needs"):
        flag = f"⚑needs-{t['needs']}"
        parts.append(_ansi(_NEEDS, flag) if color else flag)
    if is_umbrella(t):
        roll = umbrella_rollup(t, tasks)
        parts.append(_ansi(_DIM, roll) if color else roll)
    elif blocked and t["status"] not in ("review",) + TERMINAL:
        blk = f"⊘blocked-by:{','.join(str(d) for d in t['deps'])}"
        parts.append(_ansi(_ERR, blk) if color else blk)
    return " ".join(parts)


def _status_label(status):
    return STATUS_LABEL.get(status, status.capitalize())


def _fmt_ids(col, color=False):
    if not color:
        return " ".join(f"T{t['id']}" for t in col)
    return " ".join(_status_ansi(t["status"], f"T{t['id']}") for t in col)


def _fmt_count(col):
    return f"({len(col)})"


def _index_block(rows):
    """One line per (label, rhs[, sgr]) row; labels padded to a shared width.

    Optional sgr colors the label only. Width uses the uncolored label so
    ANSI codes do not break the column.
    """
    if not rows:
        return []
    width = max(len(row[0]) for row in rows)
    lines = []
    for row in rows:
        label, rhs = row[0], row[1]
        sgr = row[2] if len(row) > 2 else None
        shown = _ansi(sgr, label) if sgr else label
        lines.append(f"{shown}{' ' * (width - len(label))}  {rhs}".rstrip())
    return lines


def _list_block(tasks, expand=False, color=False, status_order=None):
    """In-play tasks one per line, umbrella children indented under the parent.

    --expand also lists done/later/not-planned. A child whose umbrella is
    itself unlisted renders at top level, so nothing drops off the board.
    """
    order = status_order or STATUSES
    rank = {s: i for i, s in enumerate(order)}
    listed = [t for t in tasks if expand or t["status"] not in TERMINAL]
    by_id = {t["id"]: t for t in listed}
    parent = {}  # child id -> umbrella id; first umbrella by id wins

    def has_ancestor(node, target):
        seen, cur = set(), parent.get(node)
        while cur is not None and cur not in seen:
            if cur == target:
                return True
            seen.add(cur)
            cur = parent.get(cur)
        return False

    for t in sorted(listed, key=lambda t: t["id"]):
        if not is_umbrella(t):
            continue
        for d in t["deps"]:
            if (d in by_id and d not in parent and d != t["id"]
                    and not has_ancestor(t["id"], d)):
                parent[d] = t["id"]
    children = {}
    for cid, pid in parent.items():
        children.setdefault(pid, []).append(cid)

    def in_order(col):
        return sorted(col, key=lambda t: (rank.get(t["status"], 99), t["id"]))

    lines = []

    def emit(t, depth):
        lines.append("  " * depth + fmt_line(t, tasks, color=color))
        for kid in in_order([by_id[c] for c in children.get(t["id"], [])]):
            emit(kid, depth + 1)

    for t in in_order([t for t in listed if t["id"] not in parent]):
        emit(t, 0)
    return lines


def _board_by_status(tasks, expand=False, color=False, status_order=None):
    order = status_order or STATUSES
    rows = []
    for status in order:
        col = [t for t in tasks if t["status"] == status]
        if not col:
            continue
        fold = status in TERMINAL and not expand
        rhs = _fmt_count(col) if fold else _fmt_ids(col, color=color)
        sgr = _STATUS_COLOR.get(status) if color else None
        rows.append((_status_label(status), rhs, sgr))
    lines = _index_block(rows)
    listed = _list_block(tasks, expand=expand, color=color,
                         status_order=order)
    if listed:
        lines.append("")
        lines.extend(listed)
    return lines


def _board_by_area(bw, scope, tasks, expand=False, color=False,
                   status_order=None):
    """Group tasks by area. Multi-area tasks appear under each area.

    Index order: areas.md order, then other named areas (sorted), then
    reserved `all`, then untagged. Empty named areas from areas.md are kept
    so the cut shows the full map; empty ad-hoc / all / untagged are omitted.
    Default keeps done/later/not-planned off area lines (own count rows)
    so open work stays scannable; --expand puts those ids on area lines too.
    """
    indexed = tasks if expand else [
        t for t in tasks if t["status"] not in TERMINAL]
    known = list(read_areas(bw, scope).keys())  # insertion order
    buckets = {name: [] for name in known}
    ad_hoc = {}  # area -> [tasks], excluding known and reserved
    all_col = []
    untagged = []
    for t in indexed:
        areas = split_areas(t.get("area", ""))
        if not areas:
            untagged.append(t)
            continue
        for a in areas:
            if a == "all":
                all_col.append(t)
            elif a in buckets:
                buckets[a].append(t)
            else:
                ad_hoc.setdefault(a, []).append(t)
    rows = [(name, _fmt_ids(buckets[name], color=color)) for name in known]
    rows.extend((name, _fmt_ids(ad_hoc[name], color=color))
                for name in sorted(ad_hoc))
    if all_col:
        rows.append(("all", _fmt_ids(all_col, color=color)))
    if untagged:
        rows.append(("(untagged)", _fmt_ids(untagged, color=color)))
    if not expand:
        for status in TERMINAL:
            col = [t for t in tasks if t["status"] == status]
            if col:
                sgr = _STATUS_COLOR.get(status) if color else None
                rows.append((_status_label(status), _fmt_count(col), sgr))
    lines = _index_block(rows)
    listed = _list_block(tasks, expand=expand, color=color,
                         status_order=status_order)
    if listed:
        lines.append("")
        lines.extend(listed)
    return lines


def _board_text(cfg, scope, bw, tasks, expand=False, by_area=False, color=False,
                status_order=None):
    title = f"# Board — iteration {iteration_label(cfg)}"
    if scope != ".":
        title += f" ({scope})"
    if by_area:
        title += " · by area"
    lines = [title, ""]
    if by_area:
        lines.extend(_board_by_area(
            bw, scope, tasks, expand=expand, color=color,
            status_order=status_order))
    else:
        lines.extend(_board_by_status(
            tasks, expand=expand, color=color,
            status_order=status_order))
    return "\n".join(lines)


def _render_board(args):
    """Fetch, write plain TASKS.md, return (plain, display, tasks).

    display is colorized when stdout is a color-capable tty. TASKS.md is
    always the plain text in STATUSES order. Watch display uses
    WATCH_STATUSES so hottest work sits above the fold.
    """
    root, scope, branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    tasks = all_tasks(bw, scope)
    expand = bool(getattr(args, "expand", False))
    by_area = bool(getattr(args, "by_area", False))
    watch = bool(getattr(args, "watch", False))
    plain = _board_text(cfg, scope, bw, tasks, expand=expand, by_area=by_area)
    with open(os.path.join(root, scope, "TASKS.md"), "w") as f:
        f.write(plain)
    live_order = WATCH_STATUSES if watch else None
    if live_order or _use_color():
        display = _board_text(cfg, scope, bw, tasks, expand=expand,
                              by_area=by_area, color=_use_color(),
                              status_order=live_order)
    else:
        display = plain
    return plain.rstrip(), display.rstrip(), tasks


def _clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


# CSI / SS3 sequences `_read_tty_byte` may return whole. Only up/down
# scroll; anything else stays ESC so it never leaks as a/A/q.
_KEY_SEQS = {
    "\x1b[A": "up", "\x1b[B": "down",
    "\x1bOA": "up", "\x1bOB": "down",
}


def _read_key(cooked=True):
    """One key from a tty; first character of a line otherwise.

    Read the fd directly — sys.stdin's buffer can swallow the key after
    switching the tty out of canonical mode. Escape consumes a following
    CSI/SS3 so arrow keys do not leak as a/A/q (see `_read_tty_byte`).

    Watch mode holds cbreak for the session (`cooked=False`) so digits typed
    during a repaint are not trapped in the line buffer.
    """
    if not sys.stdin.isatty():
        line = sys.stdin.readline()
        return line[:1] if line else "q"
    fd = sys.stdin.fileno()
    if cooked:
        try:
            import termios
            import tty
        except ImportError:
            line = input()
            return line[:1] if line else ""
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = _read_tty_byte(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    else:
        ch = _read_tty_byte(fd)
    if not ch:
        return "q"
    if ch == b"\x1b":
        return "\x1b"
    if ch.startswith(b"\x1b"):
        return _KEY_SEQS.get(ch.decode("ascii", "replace"), "\x1b")
    key = ch.decode("utf-8", "replace")
    if key == "\x04":  # Ctrl-D
        return "q"
    return key


_tty_unread = None  # one leftover byte after a lone ESC (not a CSI)


def _read_tty_byte(fd):
    """One keystroke as bytes. ESC consumes a queued CSI/SS3.

    Lone Escape is b'\\x1b'. A CSI/SS3 is the full sequence so callers can
    map arrows. Do not time-drain the buffer: that eats the next typed
    digit. A short poll lets a CSI split across an SSH packet still attach;
    a non-CSI byte after ESC is unread for the next call.
    """
    global _tty_unread
    if _tty_unread is not None:
        ch = _tty_unread
        _tty_unread = None
        return ch
    ch = os.read(fd, 1)
    if ch != b"\x1b":
        return ch
    import select
    if not select.select([fd], [], [], 0.025)[0]:
        return ch
    nxt = os.read(fd, 1)
    if nxt not in (b"[", b"O"):
        _tty_unread = nxt
        return ch
    seq = ch + nxt
    # CSI / SS3: optional parameter bytes, then a final byte in 0x40–0x7E.
    while True:
        if not select.select([fd], [], [], 0.025)[0]:
            break
        extra = os.read(fd, 1)
        if not extra:
            break
        seq += extra
        if 0x40 <= extra[0] <= 0x7E:
            break
    return seq


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(s):
    return len(_ANSI_RE.sub("", s or ""))


def _term_size():
    sz = shutil.get_terminal_size(fallback=(80, 24))
    return max(1, sz.lines), max(1, sz.columns)


def _line_rows(s, cols):
    n = _visible_len(s)
    if cols <= 0:
        return 1
    return max(1, (n + cols - 1) // cols) if n else 1


def _rows_of(lines, cols):
    return sum(_line_rows(ln, cols) for ln in lines)


def _watch_help(by_area, expand, more_above=0, more_below=0):
    other = "by status" if by_area else "by area"
    fold = "collapse" if expand else "expand"
    help_line = (f"r refresh  a {other}  e {fold}  q quit"
                 "  · type id↵  arrows scroll")
    if more_above or more_below:
        bits = []
        if more_above:
            bits.append(f"↑{more_above}")
        if more_below:
            bits.append(f"↓{more_below}")
        help_line += "  · " + " ".join(bits)
    return help_line


def _watch_footer_lines(by_area, expand, buf, result,
                        more_above=0, more_below=0):
    help_line = _watch_help(by_area, expand, more_above, more_below)
    if _use_color():
        help_line = _ansi(_DIM, help_line)
    lines = ["", help_line]
    if buf:
        lines.append(f"> {buf}")
    if result:
        lines.append(result)
    return lines


def _max_watch_offset(body, body_rows, cols):
    """Largest logical start that still fills `body_rows` from the bottom."""
    if not body:
        return 0
    used = 0
    i = len(body)
    while i > 0:
        need = _line_rows(body[i - 1], cols)
        if used + need > body_rows and used > 0:
            break
        i -= 1
        used += need
    return i


def _window_lines(lines, offset, rows, cols):
    """Logical slice of `lines` starting at `offset` that fits `rows`."""
    if not lines or rows <= 0:
        return [], 0, 0
    offset = max(0, min(offset, _max_watch_offset(lines, rows, cols)))
    shown = []
    used = 0
    i = offset
    while i < len(lines):
        need = _line_rows(lines[i], cols)
        if shown and used + need > rows:
            break
        shown.append(lines[i])
        used += need
        i += 1
        if used >= rows:
            break
    return shown, offset, len(lines) - i


def _paint_watch(out, by_area, expand, buf, result, offset):
    """Paint a terminal-height viewport. Returns the clamped offset."""
    rows, cols = _term_size()
    body = (out or "").splitlines()
    # First pass: footer without counts, so the body budget is stable.
    footer_rows = _rows_of(
        _watch_footer_lines(by_area, expand, buf, result), cols)
    body_rows = max(1, rows - footer_rows)
    shown, offset, more_below = _window_lines(body, offset, body_rows, cols)
    more_above = offset
    if more_above or more_below:
        # Counts on the help line can wrap an extra row; re-fit if so.
        footer_rows = _rows_of(
            _watch_footer_lines(by_area, expand, buf, result,
                                more_above, more_below),
            cols)
        body_rows = max(1, rows - footer_rows)
        shown, offset, more_below = _window_lines(
            body, offset, body_rows, cols)
        more_above = offset
    footer = _watch_footer_lines(by_area, expand, buf, result,
                                 more_above, more_below)
    _clear_screen()
    # No trailing newline: print() on the last row scrolls the first line off.
    frame = shown + footer
    if frame:
        sys.stdout.write("\n".join(frame))
    sys.stdout.flush()
    return offset


def cmd_board(args):
    root, scope, _branch, _bw = ctx()
    _, wrote = ensure_board_viewer(root, scope)
    watch = bool(getattr(args, "watch", False))
    if wrote and not (watch and sys.stdin.isatty()):
        print(f"viewer: refreshed ./{VIEWER_NAME}")
    if watch and sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old_term = None
        try:
            import termios
            import tty
            old_term = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except ImportError:
            pass
        buf = ""
        result = ""
        last_tid = None
        offset = 0
        try:
            while True:
                by_area = bool(getattr(args, "by_area", False))
                expand = bool(getattr(args, "expand", False))
                _plain, display, tasks = _render_board(args)
                if last_tid is not None:
                    result = watch_collision_line(
                        last_tid, tasks, color=_use_color())
                while True:
                    offset = _paint_watch(
                        display, by_area, expand, buf, result, offset)
                    key = _read_key(cooked=old_term is None)
                    if key in ("q", "Q"):
                        print()
                        return
                    if key in ("r", "R"):
                        buf = ""
                        offset = 0
                        break
                    if key in ("a", "A"):
                        buf = ""
                        offset = 0
                        args.by_area = not by_area
                        break
                    if key in ("e", "E"):
                        buf = ""
                        offset = 0
                        args.expand = not expand
                        break
                    if key == "down":
                        offset += 1
                        continue
                    if key == "up":
                        offset = max(0, offset - 1)
                        continue
                    if key == "\x1b":
                        buf = ""
                        result = ""
                        last_tid = None
                        continue
                    if key in ("\x7f", "\x08"):
                        buf = buf[:-1]
                        continue
                    if key in ("\n", "\r"):
                        if not buf:
                            continue
                        tid = parse_watch_tid(buf)
                        buf = ""
                        if tid is None:
                            result = (_ansi(_ERR, "not a task id")
                                      if _use_color() else "not a task id")
                            last_tid = None
                            continue
                        _plain, display, tasks = _render_board(args)
                        last_tid = tid
                        result = watch_collision_line(
                            tid, tasks, color=_use_color())
                        continue
                    if not buf and key in "tT":
                        buf = key
                        continue
                    if key.isdigit() and len(buf) < 8:
                        buf += key
                        continue
        except KeyboardInterrupt:
            print()
        finally:
            if old_term is not None:
                import termios
                termios.tcsetattr(fd, termios.TCSADRAIN, old_term)
        return
    print(_render_board(args)[1])


# ---------- iterations ----------

def cmd_iteration(args):
    root, scope, branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    tasks = all_tasks(bw, scope)
    # later is parked: omit it from the X/Y done denominator.
    in_play = [t for t in tasks if t["status"] != "later"]
    done = sum(1 for t in in_play if t["status"] == "done")
    print(f"scope: {scope}")
    print(f"iteration: {iteration_index(cfg)}")
    print(f"iteration_name: {iteration_name(cfg) or '(none)'}")
    print(f"iteration_started: {iteration_started(cfg) or '(none)'}")
    print(f"integration_branch: {cfg.get('integration_branch', '')}")
    print(f"parent_branch: {cfg.get('parent_branch', '') or '(none)'}")
    print(f"tasks: {done}/{len(in_play)} done")


def archive_dir(scope, index, name=""):
    """Where a closed iteration's task files are kept, relative to repo root.

    A subdirectory of the board dir, so it lands with the board and survives
    into later iterations; task_glob only matches NNN.md at the board root,
    so archived files are never live tasks. Path is archive/{n}-{slug} or
    archive/{n} when unnamed.
    """
    return os.path.join(tdir(scope), "archive",
                        iteration_archive_slug(index, name))


def find_archive_rel(bw, scope, index):
    """Relative archive dir for this index if it exists non-empty, else None.

    Clash is on the integer, not the display-name slug: 3-mvp and 3 are
    the same iteration.
    """
    root = os.path.join(bw, tdir(scope), "archive")
    if not os.path.isdir(root):
        return None
    want = int(index)
    for ent in sorted(os.listdir(root)):
        if archive_dir_index(ent) != want:
            continue
        d = os.path.join(root, ent)
        if os.path.isdir(d) and os.listdir(d):
            return os.path.join(tdir(scope), "archive", ent)
    return None


def archive_taken(bw, scope, index, name=None):
    """Relative archive dir if this index already holds a closed iteration.

    Ids restart each iteration, so archiving onto an existing directory would
    overwrite another iteration's tasks. Checked at numbering time
    (iteration-new, config iteration) and again at close as a last resort.
    The integer scan ignores pre-3 YYYY-MM-DD dirs; when name is given,
    also refuse a non-empty dest at the computed {n}-{slug} path.
    """
    taken = find_archive_rel(bw, scope, index)
    if taken:
        return taken
    if name is None:
        return None
    rel = archive_dir(scope, index, name)
    d = os.path.join(bw, rel)
    if os.path.isdir(d) and os.listdir(d):
        return rel
    return None


def archived_indexes(bw, scope):
    """Set of iteration indexes that already have a non-empty archive dir."""
    root = os.path.join(bw, tdir(scope), "archive")
    if not os.path.isdir(root):
        return set()
    found = set()
    for ent in os.listdir(root):
        idx = archive_dir_index(ent)
        if idx is None:
            continue
        d = os.path.join(root, ent)
        if os.path.isdir(d) and os.listdir(d):
            found.add(idx)
    return found


def next_iteration_index(bw, scope, current):
    taken = archived_indexes(bw, scope)
    taken.add(int(current))
    return max(taken) + 1


def archived_tasks(bw, scope, index):
    """Parse task files from a closed iteration's archive dir (empty if none)."""
    rel = find_archive_rel(bw, scope, index)
    if not rel:
        return []
    d = os.path.join(bw, rel)
    paths = sorted(glob.glob(os.path.join(d, "[0-9][0-9][0-9].md")))
    return [parse_task(p) for p in paths]


def reseed_later_tasks(bw, scope, old_index):
    """Copy archived later tasks onto the live board with fresh ids.

    Only the outgoing iteration's archive (the one just closed). Scanning
    every archive would duplicate a task that stayed later across closes.
    later→later deps are remapped; deps on anything else are dropped.
    Returns [(new_meta, old_id), ...] in new-id order.
    """
    if old_index is None:
        return []
    old_index = int(old_index)
    laters = [t for t in archived_tasks(bw, scope, old_index)
              if t.get("status") == "later"]
    if not laters:
        return []
    nid = max((t["id"] for t in all_tasks(bw, scope)), default=0) + 1
    mapping = {}
    for t in sorted(laters, key=lambda x: x["id"]):
        mapping[t["id"]] = nid
        nid += 1
    written = []
    today = datetime.date.today().isoformat()
    for t in sorted(laters, key=lambda x: x["id"]):
        new_id = mapping[t["id"]]
        new_deps = [mapping[d] for d in t.get("deps", []) if d in mapping]
        body = append_body(t.get("body") or "",
                           f"carried from {old_index}/T{t['id']}")
        meta = {
            "id": new_id,
            "title": t.get("title") or "",
            "area": t.get("area") or "",
            "status": "later",
            "kind": t.get("kind") or "",
            "assignee": "",
            "branch": "",
            "deps": new_deps,
            "pr": "",
            "needs": t.get("needs") or "",
            "created": today,
            "body": body,
        }
        path = os.path.join(bw, tdir(scope), f"{new_id:03d}.md")
        with open(path, "w") as f:
            f.write(render_task(meta))
        meta["path"] = path
        written.append((meta, t["id"]))
    return written


def cmd_iteration_close(args):
    """Archive task files under .tasks/archive/{n}-{slug}/, index them in
    .tasks/log.md, and remove the live files, committing on the integration
    branch. Landing the integration branch in the parent (via PR) happens
    afterwards and is not this script's job."""
    root, scope, branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    tasks = all_tasks(bw, scope)
    if not tasks:
        sys.exit("error: no tasks on this board; nothing to close")
    unfinished = [t for t in tasks if t["status"] not in TERMINAL]
    if unfinished and not args.force:
        ids = ", ".join(f"T{t['id']}" for t in unfinished)
        sys.exit(f"error: unfinished tasks: {ids}. Finish them, mark later, "
                 "delete them, or re-run with --force to close anyway (they "
                 "will be logged as unfinished and removed).")
    idx = iteration_index(cfg)
    name = iteration_name(cfg)
    started = iteration_started(cfg)
    today = datetime.date.today().isoformat()
    parent = cfg.get("parent_branch", "")
    require_schema3_archive_slug(idx, name)
    arel = archive_dir(scope, idx, name)
    adir = os.path.join(bw, arel)
    # Normally caught at numbering time; reachable only if the archive
    # appeared after this iteration was numbered (a merge from the parent,
    # say). Fail closed — the whole point of the archive is that nothing
    # is lost.
    taken = archive_taken(bw, scope, idx, name)
    if taken:
        sys.exit(f"error: iteration {idx} already has an archive at "
                 f"{taken}/; closing would overwrite it. Renumber first: "
                 f"TASKS config iteration <n>")
    os.makedirs(adir, exist_ok=True)
    entry = [iteration_close_heading(idx, name)]
    if started:
        entry.append(f"started: {started}")
    entry.append(f"closed: {today}")
    extra = f" → {parent}" if parent else ""
    entry.append(f"branch: {branch}{extra}")
    entry.append(f"archive: {arel}/")
    for t in tasks:
        line = f"- {idx}/T{t['id']} {t['title']}"
        if t.get("assignee"):
            line += f" — {t['assignee']}"
        if t.get("area"):
            line += f" [{t['area']}]"
        if t["status"] == "not-planned":
            line += " [not planned]"
        elif t["status"] == "later":
            line += " [later]"
        elif t["status"] != "done":
            line += f" [unfinished: {t['status']}]"
        if t.get("pr"):
            line += f" {t['pr']}"
        entry.append(line)
        # The log is the index; the archived file next to it holds everything.
        # Shipped records ride along here so the result is visible at a skim.
        for rec in collect_shipped(t.get("body") or ""):
            entry.append("  - " + " ".join(rec.split()))
    log = os.path.join(bw, tdir(scope), "log.md")
    existing = open(log).read() if os.path.exists(log) else "# Iteration log\n"
    with open(log, "w") as f:
        f.write(existing.rstrip() + "\n\n" + "\n".join(entry) + "\n")
    for t in tasks:
        # Copy verbatim, then remove: frontmatter (area, deps, pr, created)
        # and the full body — Decision:, Shipped:, not-planned reason — are
        # preserved exactly, with no parse/render round-trip to lose them.
        shutil.copyfile(t["path"],
                        os.path.join(adir, os.path.basename(t["path"])))
        os.remove(t["path"])
    board_commit(root, branch, bw, scope, f"dev: close iteration {idx}")
    print(f"iteration {idx} closed: {len(tasks)} tasks archived to "
          f"{arel}/ and indexed in {tdir(scope)}/log.md")
    if parent:
        print(f"next: TASKS iteration-land  # merge-commit PR into '{parent}'")
        print(f"      then after merge: TASKS iteration-new <branch>")


def cmd_iteration_new(args):
    root, scope, old_branch, bw = ctx()
    cfg = read_board_cfg(bw, scope)
    old_idx = iteration_index(cfg)
    parent = args.parent or cfg.get("parent_branch", "")
    if not parent:
        sys.exit("error: no parent branch known; pass --parent <branch>")
    if args.branch in (old_branch, parent):
        sys.exit(f"error: new iteration branch must differ from '{old_branch}' "
                 f"and parent '{parent}'")
    git("fetch", "origin", parent, old_branch, cwd=bw, check=False)
    start = f"origin/{parent}" if ref_exists(root, f"origin/{parent}") else parent
    if not ref_exists(root, start):
        sys.exit(f"error: parent branch '{parent}' not found locally or on origin")
    # The new board starts from the parent, so anything still sitting on the
    # outgoing branch is invisible to it: its archive dir (missed by the
    # collision check below) and its log.md close section (which the new
    # iteration would then re-append around, conflicting at land). Same
    # predicate iteration-land uses for "already landed" — ancestry, not PR
    # state. Fail closed: land first, then start the next iteration.
    old_ref = (f"origin/{old_branch}" if ref_exists(root, f"origin/{old_branch}")
               else old_branch)
    if not ref_exists(root, old_ref):
        sys.exit(f"error: current integration branch '{old_branch}' not found "
                 "locally or on origin")
    landed = git("merge-base", "--is-ancestor", old_ref, start,
                 cwd=root, check=False)
    if landed.returncode != 0:
        sys.exit(f"error: '{old_branch}' is not yet contained in '{parent}'; "
                 "its archive and log.md close section would be missing from "
                 "the new board. Close and land first: TASKS iteration-land")
    git("reset", "--hard", start, cwd=bw)
    # defensively clear any task files inherited from the parent
    for p in task_glob(bw, scope):
        os.remove(p)
    os.makedirs(os.path.join(bw, tdir(scope)), exist_ok=True)
    cfg["integration_branch"] = args.branch
    cfg["parent_branch"] = parent
    if args.iteration is not None:
        new_idx = parse_positive_int(args.iteration, "iteration")
    else:
        new_idx = next_iteration_index(bw, scope, old_idx)
    new_name = (args.name or "").strip()
    require_schema3_archive_slug(new_idx, new_name)
    # The gate above makes the parent carry every closed iteration's archive,
    # so this is the moment a collision is both visible and free to fix —
    # pick another number rather than discovering it at close, with a board
    # full of tasks.
    taken = archive_taken(bw, scope, new_idx, new_name)
    if taken:
        sys.exit(f"error: iteration {new_idx} already has an archive at "
                 f"{taken}/; closing it later would overwrite that "
                 "iteration. Pick another number: iteration-new <branch> "
                 "--iteration <n>")
    started = parse_iso_date(
        args.iteration_started or datetime.date.today().isoformat(),
        "iteration_started")
    cfg["iteration"] = str(new_idx)
    cfg["iteration_name"] = new_name
    cfg["iteration_started"] = started
    write_board_cfg(bw, scope, cfg)
    write_cache(root, scope, args.branch)
    reseeded = reseed_later_tasks(bw, scope, old_idx)
    board_commit(root, args.branch, bw, scope,
                 f"dev: start iteration {new_idx}")
    # leave a pointer on the parent so other contributors' stale checkouts
    # resolve to the new iteration (resolve_board follows it)
    git("reset", "--hard", start, cwd=bw)
    os.makedirs(os.path.join(bw, tdir(scope)), exist_ok=True)
    write_board_cfg(bw, scope, cfg)
    board_commit(root, parent, bw, scope,
                 f"dev: point board at iteration {new_idx}")
    print(f"iteration {new_idx} started on new branch "
          f"'{args.branch}' (parent: {parent})")
    if reseeded:
        bits = ", ".join(f"T{m['id']} ← {old_idx}/T{oid}"
                         for m, oid in reseeded)
        print(f"reseeded {len(reseeded)} later task(s): {bits}")
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
    s.add_argument("--iteration",
                   help="iteration number (default: 1)")
    s.add_argument("--iteration-name",
                   help="optional display name")
    s.add_argument("--iteration-started",
                   help="start date YYYY-MM-DD (default: today)")
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
    s.add_argument("--status",
                   choices=["proposed", "backlog", "planned", "later"],
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

    s = sub.add_parser("collisions",
                       help="area occupancy vs doing/review (exit 2 if blocked)")
    s.add_argument("ids", nargs="+",
                   help="task id(s), e.g. 12 or 12,15,18")
    s.set_defaults(fn=cmd_collisions)

    s = sub.add_parser("board", help="print board view; regenerate TASKS.md")
    s.add_argument("--expand", action="store_true",
                   help="also list done/later/not-planned (default folds "
                        "them to counts)")
    s.add_argument("--by-area", action="store_true",
                   help="index by area instead of status (multi-area tasks "
                        "listed under each area)")
    s.add_argument("--watch", action="store_true",
                   help="interactive: r refresh, a toggle by-area, "
                        "e toggle expand, q quit, arrows scroll, type "
                        "id+Enter for area collisions (used by ./board)")
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
    s.add_argument("--name",
                   help="optional display name (not the identity)")
    s.add_argument("--iteration",
                   help="iteration number (default: one more than max of "
                        "live and archived indexes)")
    s.add_argument("--iteration-started",
                   help="start date YYYY-MM-DD (default: today)")
    s.set_defaults(fn=cmd_iteration_new)

    s = sub.add_parser("iteration-land",
                       help="open/merge iteration PR into parent (merge commit, not squash)")
    s.add_argument("--title", help="PR title (default: iteration <n>)")
    s.add_argument("--body", help="PR body")
    s.add_argument("--create-only", action="store_true",
                   help="open the PR but do not merge")
    s.set_defaults(fn=cmd_iteration_land)

    s = sub.add_parser("claim",
                       help="implement setup: branch + linked worktree, status=doing")
    s.add_argument("id", type=int)
    s.add_argument("--assignee",
                   help="assignee (default: product identity; auto agents pass auto/<model>)")
    s.add_argument("--branch",
                   help="task branch (default: recorded or dev/<scope?>-<id>-<slug>)")
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("diff",
                       help="self-review: location + diff from the task worktree")
    s.add_argument("id", type=int)
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("ship",
                       help="implement ship: commit [n/T<id>], push, open PR, status=review")
    s.add_argument("id", type=int)
    s.add_argument("--message", "-m",
                   help="commit message if worktree dirty "
                        "(default: [n/T<id>] <title>)")
    s.add_argument("--title",
                   help="PR title on create only "
                        "(default: [n/T<id>] <task title>)")
    s.add_argument("--body",
                   help="PR body on create only (default: task body)")
    s.add_argument("--version-intent",
                   help="on create only: append 'Version intent: …' when body lacks "
                        "that line (agent only; no default — omit when product does "
                        "not version)")
    s.add_argument("--base",
                   help="PR base on create only (default: integration; stack with "
                        "another task branch name)")
    s.add_argument("--batch",
                   help="implement-batch stamp: comma-separated task ids "
                        "(writes Dev-batch: … on PR body + task body)")
    s.add_argument("--shipped",
                   help="REQUIRED: what actually shipped, in one or two "
                        "sentences (the result, not the plan). Appended to "
                        "the task body as 'Shipped (<date>): …' and mirrored "
                        "onto the PR body on create and every re-ship")
    s.set_defaults(fn=cmd_ship)

    s = sub.add_parser("batch-gate",
                       help="exit 2 if --ids is a proper subset of an open Dev-batch")
    s.add_argument("--ids", required=True,
                   help="selected task ids, e.g. 19,20 or 19,20,22,23")
    s.set_defaults(fn=cmd_batch_gate)

    s = sub.add_parser("restack",
                       help="fail-closed rebase of a task stack/batch (plan then apply)")
    s.add_argument("--ids", required=True,
                   help="task ids in the set, e.g. 19,20,22,23")
    s.add_argument("--after", type=int,
                   help="only restack stack descendants of this task id")
    s.add_argument("--onto",
                   help="rebase every target onto this ref (e.g. main or "
                        "origin/main) — does not cascade (children do not "
                        "stay based on restacked parents; default without "
                        "--onto preserves in-set stack parents); implies "
                        "PR base retarget unless base already matches")
    s.add_argument("--retarget", action="store_true",
                   help="gh pr edit --base to the new stack parent / --onto")
    s.add_argument("--dry-run", action="store_true",
                   help="print plan only (also land order for the set); "
                        "do not rebase or push")
    s.set_defaults(fn=cmd_restack)

    s = sub.add_parser("preflight",
                       help="local integration ahead of origin: check / park / discard")
    s.add_argument("--park", action="store_true",
                   help="park ahead commits as a PR, reset local integration")
    s.add_argument("--discard", action="store_true",
                   help="reset local integration to origin (drops ahead commits)")
    s.set_defaults(fn=cmd_preflight)

    s = sub.add_parser("land",
                       help="integrator-only: merge task PR (merge commit), cleanup, mark done")
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
