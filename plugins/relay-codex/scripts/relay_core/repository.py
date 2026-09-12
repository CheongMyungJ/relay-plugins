import re
import subprocess
from pathlib import Path
from . import RelayError


def git_raw(cwd, *args):
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RelayError("git", "Git unavailable or timed out.") from exc
    if result.returncode:
        raise RelayError("git", result.stderr.strip())
    return result.stdout


def git(cwd, *args):
    return git_raw(cwd, *args).strip()


def common_dir(root):
    return (Path(root) / git(root, "rev-parse", "--git-common-dir")).resolve()


def worktrees(root):
    raw = git_raw(root, "worktree", "list", "--porcelain", "-z")
    return [Path(field[9:]).resolve() for field in raw.split("\0") if field.startswith("worktree ")]


def branch_name(root, name):
    if not isinstance(name, str) or name.startswith(("-", "refs/")) or re.fullmatch(r"[0-9a-fA-F]{40,64}", name):
        raise RelayError("input", "Use a short branch name, not a revision or SHA.")
    git(root, "check-ref-format", "refs/heads/" + name)
    return name


def local_tip(root, name):
    branch_name(root, name)
    refs = git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/" + name)
    return next((line.split()[1] for line in refs.splitlines() if line.split()[0] == "refs/heads/" + name), None)


def remote_tip(repo, name, fetch=False):
    root, remote = repo["root"], repo["remote"]
    branch_name(root, name)
    ref = "refs/heads/" + name
    lines = git(root, "ls-remote", "--refs", remote, ref).splitlines()
    sha = next((line.split()[0] for line in lines if line.split()[1] == ref), None)
    if sha and fetch:
        import hashlib
        private = "refs/relay/pr/" + hashlib.sha256((remote + "/" + name).encode()).hexdigest()
        git(root, "fetch", "--no-tags", "--no-write-fetch-head", remote, "+" + ref + ":" + private)
        if git(root, "rev-parse", private) != sha:
            raise RelayError("stale", "Remote branch moved during fetch; inspect again.")
    return sha


def ancestor(root, older, newer):
    # rev-list also verifies both commits, unlike treating every Git error as false.
    return git(root, "rev-list", "--count", newer + ".." + older) == "0"


def source(repo, name):
    local, remote = local_tip(repo["root"], name), remote_tip(repo, name, fetch=True)
    if not local and not remote:
        raise RelayError("branch", "Branch does not exist: " + name)
    relation = "remote_only" if not local else "local_only" if not remote else "equal"
    if local and remote and local != remote:
        if ancestor(repo["root"], remote, local):
            relation = "ahead"
        elif ancestor(repo["root"], local, remote):
            relation = "behind"
        else:
            counts = git(repo["root"], "rev-list", "--left-right", "--count", local + "..." + remote)
            raise RelayError("conflict", "Source histories diverged (local/remote counts " + counts + "). Choose how to reconcile them.")
    return {"head_sha": remote if relation in ("remote_only", "behind") else local,
            "local_sha": local, "remote_sha": remote, "relation": relation,
            "push_needed": relation in ("local_only", "ahead")}


def github_remote(url):
    # Enterprise hosts need not contain "github". The selected remote is the
    # authority; gh checks authentication and GitHub API availability later.
    host = r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?"
    match = re.fullmatch(
        rf"(?:https://(?P<https>{host})/|ssh://git@(?P<ssh>{host})(?::[0-9]+)?/|git@(?P<scp>{host}):)"
        r"(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?", url)
    if not match or match["owner"] in (".", "..") or match["name"] in (".", ".."):
        return None
    return {"host": (match["https"] or match["ssh"] or match["scp"]).lower(),
            "repo": f'{match["owner"]}/{match["name"]}'}


def same_repository(left, right):
    # Work created before host support belongs to github.com.
    return (left.get("host", "github.com").lower() == right.get("host", "github.com").lower()
            and left["repo"].lower() == right["repo"].lower())


def inspect(cwd):
    initial = Path(cwd).resolve()
    root = Path(git(initial, "rev-parse", "--show-toplevel")).resolve()
    remotes = git(root, "remote").splitlines()
    candidates = {name: github_remote(git(root, "remote", "get-url", name)) for name in remotes}
    if "origin" in candidates:
        name = "origin"
        if not candidates[name]:
            raise RelayError("repository", "origin must identify the intended GitHub repository.")
    else:
        valid = [key for key, value in candidates.items() if value]
        if len(valid) != 1:
            raise RelayError("repository", "Configure one unambiguous GitHub remote in cwd.")
        name = valid[0]
    return {"root": str(root), "cwd": str(initial), "remote": name, **candidates[name]}


def ensure_identity(expected, cwd):
    actual = inspect(cwd)
    if not same_repository(actual, expected) or actual["remote"] != expected["remote"]:
        raise RelayError("repository", "Repository or remote changed since this work started.")
    return actual
