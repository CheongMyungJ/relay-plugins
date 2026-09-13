"""Git evidence and ordinary pushes to the frozen PR source; never edit code."""
from pathlib import Path
import re
from . import RelayError, repository as repo_git


def destination(repo, side, push=False):
    identity = {"host": repo.get("host", "github.com"), "repo": (side.get("repo") or {}).get("full_name", "")}
    if not identity["repo"] or len(identity["repo"].split("/")) != 2:
        raise RelayError("repository", "PR source repository is missing.")
    root = repo["root"]
    for remote in repo_git.git(root, "remote").splitlines():
        url = repo_git.git(root, "remote", "get-url", remote)
        parsed = repo_git.github_remote(url)
        if parsed and repo_git.same_repository(parsed, identity):
            if push:
                urls = repo_git.git(root, "remote", "get-url", "--push", "--all", remote).splitlines()
                if len(urls) != 1 or not (parsed := repo_git.github_remote(urls[0])) or not repo_git.same_repository(parsed, identity):
                    raise RelayError("repository", "Source remote has a different or multiple push destinations.")
                return urls[0]
            return url
    url = f"https://{identity['host']}/{identity['repo']}.git"
    if not repo_git.github_remote(url):
        raise RelayError("repository", "Invalid PR repository identity.")
    return url


def tip(root, url, ref):
    repo_git.branch_name(root, ref)
    target = "refs/heads/" + ref
    return next((line.split()[0] for line in repo_git.git(root, "ls-remote", "--refs", url, target).splitlines()
                 if line.split()[1] == target), None)


def changed_paths(root, base, head):
    """Literal changed paths, including both sides of a rename, at the recorded commits."""
    if not base:
        raise RelayError("git", "The recorded merge base is missing; collect Git evidence before querying changed paths.")
    raw = repo_git.git_raw(root, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", base, head, "--")
    return sorted({path for path in raw.split("\0") if path})


def evidence(repo, pull, run_id):
    root = repo["root"]
    result = {"complete": False, "missing": [], "merge_base": None}
    try:
        for name in ("head", "base"):
            side = pull[name]
            if not re.fullmatch(r"[a-fA-F0-9]{40,64}", side["sha"]):
                raise RelayError("input", "Invalid PR commit SHA.")
            ref = f"refs/relay/review/{run_id}/{name}"
            repo_git.git(root, "fetch", "--no-tags", "--no-write-fetch-head", destination(repo, side), "+" + side["sha"] + ":" + ref)
            if repo_git.git(root, "rev-parse", ref + "^{commit}") != side["sha"]:
                raise RelayError("stale", "Fetched object differs from PR SHA.")
        base = repo_git.git(root, "merge-base", pull["base"]["sha"], pull["head"]["sha"])
        diff = repo_git.git_raw(root, "diff", "--no-ext-diff", "--no-textconv", base, pull["head"]["sha"], "--")
        stats = repo_git.git_raw(root, "diff", "--no-ext-diff", "--no-textconv", "--numstat", base, pull["head"]["sha"], "--")
        result.update(merge_base=base, diff=diff[:500000], numstat=stats)
        if len(diff) > 500000:
            result["missing"].append("Diff exceeds 500000 characters; read remaining Git objects directly.")
        if any(line.startswith("-\t-\t") for line in stats.splitlines()):
            result["missing"].append("Binary content needs separate inspection.")
        result["complete"] = not result["missing"]
    except RelayError as exc:
        result["missing"].append(str(exc))
    return result


def register(repo, request, path):
    path = Path(path).resolve()
    if path == Path(repo["root"]).resolve() or path == Path(request["initial_checkout"]).resolve():
        raise RelayError("worktree", "Code application requires a separate worktree.")
    if repo_git.common_dir(path) != repo_git.common_dir(repo["root"]) or path not in repo_git.worktrees(repo["root"]):
        raise RelayError("worktree", "Application path is not a connected worktree.")
    branch = repo_git.git(path, "branch", "--show-current")
    if not branch or branch in (request["snapshot"]["pull"]["head"]["ref"], request["snapshot"]["pull"]["base"]["ref"]):
        raise RelayError("branch", "Use a separate local application branch.")
    if repo_git.git(path, "rev-parse", "HEAD") != request["snapshot"]["pull"]["head"]["sha"] or repo_git.git(path, "status", "--porcelain"):
        raise RelayError("worktree", "Register a clean worktree at the frozen head before editing.")
    return {"path": str(path), "branch": branch, "status": "applying"}


def verify(repo, request, data):
    app = request["application"]
    path = app["path"]
    if repo_git.common_dir(path) != repo_git.common_dir(repo["root"]) or repo_git.git(path, "branch", "--show-current") != app["branch"]:
        raise RelayError("worktree", "Registered worktree identity changed.")
    head = request["snapshot"]["pull"]["head"]["sha"]
    commit = repo_git.git(path, "rev-parse", "HEAD")
    if commit == head or not repo_git.ancestor(path, head, commit) or repo_git.git(path, "status", "--porcelain"):
        raise RelayError("verification", "Provide a clean, nonempty descendant commit.")
    paths = set(repo_git.git_raw(path, "diff", "--no-renames", "--name-only", "-z", head, commit, "--").rstrip("\0").split("\0"))
    allowed = {p for item in request["candidate"]["code_scope"] if item["item_id"] in request["decision"]["selected"] for p in item["paths"]}
    if not paths or not paths <= allowed:
        raise RelayError("verification", "Actual changed paths exceed the selected code scope.")
    tree = repo_git.git(path, "rev-parse", "HEAD^{tree}")
    tests = data.get("tests", [])
    required = request["candidate"]["verification_scope"]
    if not required or not tests or data.get("diff_reviewed") is not True:
        raise RelayError("verification", "Record required checks and review the actual scoped diff.")
    for test in tests:
        if (test.get("exit_code") != 0 or not test.get("evidence") or test.get("tree") != tree or
                test.get("commit") != commit or Path(test.get("cwd", "")).resolve() != Path(path).resolve()):
            raise RelayError("verification", "Every check must pass on the final commit/tree in the registered worktree.")
    if not set(required) <= {t.get("command") for t in tests}:
        raise RelayError("verification", "A required check is missing.")
    return {**app, "commit": commit, "tree": tree, "tests": tests, "paths": sorted(paths), "status": "verified"}


def push(repo, request, gh, save):
    app, pull = request["application"], request["snapshot"]["pull"]
    source = pull["head"]
    url = destination(repo, source, push=True)
    default = gh.api("repos/" + source["repo"]["full_name"])["default_branch"]
    if source["ref"] in (default, pull["base"]["ref"]):
        raise RelayError("branch", "Refusing to push a base/default branch.")
    current = tip(repo["root"], url, source["ref"])
    if current == app["commit"]:
        app.update(status="pushed", remote_sha=current)
        save()
        return
    if current != source["sha"]:
        raise RelayError("stale", "Source was deleted or moved; review before pushing.")
    if repo_git.git(app["path"], "rev-parse", "HEAD") != app["commit"] or repo_git.git(app["path"], "status", "--porcelain"):
        raise RelayError("verification", "Application changed after verification.")
    app["status"] = "pushing"
    save()
    # No force, lease, configured refspec, or branch-name inference.
    repo_git.git(app["path"], "push", url, app["commit"] + ":refs/heads/" + source["ref"])
    if tip(repo["root"], url, source["ref"]) != app["commit"]:
        raise RelayError("uncertain", "Push result does not match the verified commit.")
    app.update(status="pushed", remote_sha=app["commit"])
    save()
