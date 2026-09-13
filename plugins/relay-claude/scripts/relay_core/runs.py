"""Record and verify execution facts; development commands stay with the host."""
import uuid
from pathlib import Path
from . import RelayError, baselines
from .repository import git, ensure_identity
from .state import write_json
from .publishing import snapshot


HOLD_CONDITIONS = {
    "checks": "필수 검증 실패·미실행·무효화",
    "user_or_permission": "사용자 보류 지시·실행 환경 권한 제한",
    "scope": "승인 범위 이탈",
    "basis": "기준 문서 변경·누락·stale",
    "git": "Git 충돌·금지된 Git 작업",
}


def begin(store, state, data, gh, registry):
    if state["stage"] != "implement" or not data.get("execution_authorized"):
        raise RelayError("run", "An explicit implementation request is required.")
    _, _, records, remote_runs = snapshot(state, gh)
    runs = state.setdefault("runs", {})
    active = state.get("active_run")
    if active and not data.get("new_run"):
        run = runs[active]
        baselines.select(baselines.from_records(records), state["options"].get("basis"), run)
        options = state["options"]
        if options.get("branch", run["branch"]) != run["branch"] or options.get("base", run["base"]) != run["base"]:
            raise RelayError("run", "Requested branch/base differs from the registered execution.")
        return run
    if remote_runs and not runs and not data.get("new_run"):
        raise RelayError("run", "Remote execution records exist. Confirm Git state and resume with restore before starting another run.")
    basis = baselines.select(baselines.from_records(records), state["options"].get("basis"))
    proof = records["plan" if basis["kind"] == "formal" else "brief"]
    # Pin the readable proof text plus its suggestion; together they rebuild the proof hash.
    summary, suggestion = proof["body"], proof["next_step"]
    run_id = uuid.uuid4().hex[:12]
    options = state["options"]
    root = Path(state["repository"]["root"])
    remote = state["repository"]["remote"]
    base = options.get("base")
    default = gh.api(gh.prefix)["default_branch"]
    if not base:
        base = f"{remote}/{default}"
    sha = git(root, "rev-parse", "--verify", base + "^{commit}")
    branch = options.get("branch", f"relay/{state['issue']}-{run_id}")
    if branch == default:
        raise RelayError("run", "Use a task branch, not the repository default branch.")
    git(root, "check-ref-format", "--branch", branch)
    wt = options.get("worktree")
    path = root.parent / f"{root.name}-relay-{state['issue']}-{run_id}" if wt is True else (Path(state["repository"]["cwd"]) / wt).resolve() if wt else root
    run = {"run_id": run_id, "status": "planned", "parents": basis["parents"],
           "basis": basis, "basis_summary": summary, "basis_next_step": suggestion,
           "branch": branch, "base": base, "base_sha": sha,
           "path": str(path), "repository": state["repository"]["repo"], "drift": [], "tests": [], "holds": []}
    if basis["kind"] == "formal":
        run["plan_summary"] = summary
    runs[run_id] = run
    state["active_run"] = run_id
    store.save(state)
    return run


def kb_for_run(run, state, paths=None):
    """First lookup from the pinned proof text, or a recheck of actual changed paths; never stored."""
    from .kb import reading
    root = run["path"] if Path(run.get("path", "")).is_dir() else state["repository"]["root"]
    try:
        kb, _ = reading.worktree_kb(root)
        first = reading.for_document(kb, run.get("basis_summary") or run.get("plan_summary") or "")
        if paths is None:
            return first
        initial = ([e["id"] for e in first["entries"]] + [r["id"] for r in first["redirects"]]) if first else []
        return reading.recheck(kb, paths, initial)
    except RelayError as exc:
        return {"error": exc.code + ": " + str(exc)}


def changed_paths(path, base_sha):
    """Paths changed since the base: committed differences plus the current worktree status."""
    from .repository import git_raw
    names = set(git(path, "diff", "--name-only", base_sha, "HEAD", "--").splitlines())
    items = git_raw(path, "status", "--porcelain", "-z", "--untracked-files=all").split("\0")
    skip = False
    for item in items:
        if skip:  # the original name of a rename/copy follows its entry
            skip = False
            continue
        if not item:
            continue
        names.add(item[3:])
        skip = item[0] in "RC"
    return sorted(n for n in names if n)


def parse_evidence(record, run_id):
    """Parse verified artifact evidence without restoring state or requiring current parents."""
    import json
    import re
    blocks = re.findall(r"실행 근거:\n```json\n(.*?)\n```", record["body"], re.DOTALL)
    if len(blocks) != 1:
        raise RelayError("run", "Execution evidence is missing or ambiguous; inspect manually.")
    run = json.loads(blocks[0])
    if run["run_id"] != run_id or run_id != record["meta"].get("run_id") or run["parents"] != record["meta"]["parents"]:
        raise RelayError("run", "Execution identity does not match the recorded artifact.")
    baselines.for_run(run)
    return run


def restore(store, state, data, gh, registry):
    """Recover a run from its recorded evidence after checking the actual local Git state."""
    if state["stage"] != "implement" or not data.get("execution_authorized"):
        raise RelayError("run", "Authorize resuming the existing execution first.")
    _, _, _, remote_runs = snapshot(state, gh, historical=True)
    record = remote_runs.get(data["run_id"])
    if not record:
        raise RelayError("run", "No matching remote execution record.")
    known = state.get("implementation_targets", {}).get(data["run_id"])
    if known and known != record["target"]:
        raise RelayError("conflict", "Recovery report differs from its known comment ID.")
    run = parse_evidence(record, data["run_id"])
    basis = baselines.for_run(run, state.get("options", {}).get("basis"))
    path = Path(data.get("path", run["path"])).resolve()
    ensure_identity(state["repository"], path)
    if git(path, "branch", "--show-current") != run["branch"]:
        raise RelayError("run", "Recovery branch does not match the execution record.")
    if run.get("commit") and git(path, "rev-parse", "HEAD") != run["commit"]:
        raise RelayError("run", "Recovery HEAD differs from the recorded commit; inspect the resume point.")
    if run["status"] in ("verified", "committed", "pushed") and tree_signature(path) != run.get("verified_tree"):
        raise RelayError("verification", "Recovered file content differs from the verified execution.")
    if run["status"] == "pushed":
        remote = git(path, "ls-remote", state["repository"]["remote"], "refs/heads/" + run["branch"]).split()
        if not remote or remote[0] != run["commit"] or run.get("remote_sha") != run["commit"]:
            raise RelayError("run", "Recovered remote SHA differs from the execution record.")
    run["path"] = str(path)
    if path != store.root:
        expected_common = (store.root / git(store.root, "rev-parse", "--git-common-dir")).resolve()
        actual_common = (path / git(path, "rev-parse", "--git-common-dir")).resolve()
        if expected_common != actual_common:
            raise RelayError("run", "Recovery path is not a worktree of this local repository.")
        write_json(path / ".relay" / "pointer.json", {"root": str(store.root)})
    state.setdefault("runs", {})[run["run_id"]] = run
    state["active_run"] = run["run_id"]
    state.setdefault("implementation_targets", {})[run["run_id"]] = record["target"]
    state["options"] = {"branch": run["branch"], "base": run["base"], "basis": basis["kind"]}
    store.save(state)
    return run


def checkpoint(store, state, data, gh, registry):
    run_id = data.get("run_id", state.get("active_run"))
    run = state.get("runs", {}).get(run_id)
    if not run:
        raise RelayError("run", "No matching implementation run.")
    basis = baselines.for_run(run, state.get("options", {}).get("basis"))
    action = data["action"]
    if action == "drift":
        entry = data["entry"]
        required = {"planned", "actual", "reason", "impact", "severity", "evidence"}
        if not required <= entry.keys():
            raise RelayError("run", "Drift entry lacks required evidence fields.")
        run["drift"].append(dict(entry, decision=entry.get("decision", "사람 판단 전")))
    elif action == "failure":
        run["failure"] = data["reason"]
    elif action == "hold":
        entry = data.get("entry")
        if (not isinstance(entry, dict)
                or not all(isinstance(entry.get(key), str) and entry[key].strip()
                           for key in ("condition", "detail", "evidence"))
                or entry["condition"] not in HOLD_CONDITIONS):
            raise RelayError("run", "Hold requires a known condition and nonempty detail/evidence strings.")
        run.setdefault("holds", []).append({key: entry[key] for key in ("condition", "detail", "evidence")})
    else:
        _, _, records, _ = snapshot(state, gh)
        baselines.current(basis, baselines.from_records(records))
        path = Path(run["path"])
        ensure_identity(state["repository"], path)
        branch = git(path, "branch", "--show-current")
        if branch != run["branch"]:
            raise RelayError("run", "Actual branch differs from this run.")
        if action == "prepared":
            if run["status"] != "planned":
                return run
            if git(path, "status", "--porcelain"):
                raise RelayError("run", "Prepare a clean task worktree before registering implementation.")
            git(path, "merge-base", "--is-ancestor", run["base_sha"], "HEAD")
            write_json(path / ".relay" / "pointer.json", {"root": str(store.root)}) if path.resolve() != store.root else None
            run["status"] = "implementing"
        elif action == "verified":
            if run["status"] not in ("implementing", "verified"):
                raise RelayError("run", "Prepare this execution before registering verification.")
            tests = data.get("tests", [])
            if not tests or any(type(t.get("exit_code")) is not int or t["exit_code"] != 0 or not t.get("command") or not t.get("evidence") for t in tests):
                raise RelayError("verification", "All required checks must pass and have recorded evidence.")
            run["tests"] = tests
            run["verified_tree"] = tree_signature(path)
            run["status"] = "verified"
        elif action == "committed":
            if run["status"] != "verified" or tree_signature(path) != run["verified_tree"]:
                raise RelayError("verification", "Verify the exact working tree before recording the commit.")
            if git(path, "status", "--porcelain"):
                raise RelayError("run", "Task worktree must be clean after the scoped commit.")
            run["commit"] = git(path, "rev-parse", "HEAD")
            if run["commit"] == run["base_sha"]:
                raise RelayError("run", "No implementation commit exists.")
            run["status"] = "committed"
        elif action == "pushed":
            if run["status"] not in ("committed", "pushed"):
                raise RelayError("run", "Record the verified commit before push.")
            if tree_signature(path) != run.get("verified_tree"):
                raise RelayError("verification", "Working tree changed after the verified commit.")
            actual = git(path, "ls-remote", state["repository"]["remote"], "refs/heads/" + branch).split()
            if not actual or actual[0] != run["commit"] or git(path, "rev-parse", "HEAD") != run["commit"]:
                raise RelayError("run", "Local and remote SHA do not match the implementation commit.")
            run["remote_sha"] = actual[0]
            run["status"] = "pushed"
        else:
            raise RelayError("run", "Unknown checkpoint action.")
        if action in ("verified", "committed", "pushed"):
            # A successful recheck resolves the active failure. Older execution
            # reports retain history; a later handoff must not mistake it for a
            # still-failing run after recovery.
            run.pop("failure", None)
    store.save(state)
    write_json(store.path / "runs" / run_id / "execution.json", run)
    (store.path / "runs" / run_id / "drift.md").write_text("\n\n".join(str(d) for d in run["drift"]) or "drift 없음\n", encoding="utf-8")
    return run


def with_kb(action, run, state):
    """The helper response for begin/verified/committed: the run plus a KB field that is never stored."""
    if action == "begin":
        return dict(run, kb=kb_for_run(run, state))
    if action in ("verified", "committed"):
        # A post-hoc lookup of the paths actually changed; it never replaces the pre-edit lookup.
        return dict(run, kb_recheck=kb_for_run(run, state, changed_paths(Path(run["path"]), run["base_sha"])))
    return run


def tree_signature(path):
    """Hash tracked and untracked nonignored file content, independent of staging/commit."""
    import hashlib
    import subprocess
    raw = subprocess.run(["git", "-C", str(path), "ls-files", "-z", "--cached", "--others", "--exclude-standard"], check=True, capture_output=True).stdout
    import json
    import stat
    entries = []
    for name in sorted(set(raw.split(b"\0")) - {b""}):
        file = Path(path) / name.decode("utf-8")
        if file.is_symlink():
            value = ["symlink", str(file.readlink())]
        elif file.is_file():
            value = ["file", hashlib.sha256(file.read_bytes()).hexdigest(), bool(file.stat().st_mode & stat.S_IXUSR)]
        elif file.is_dir():
            value = ["submodule", git(file, "rev-parse", "HEAD")]
        else:
            continue  # A deleted tracked path must match its absence after commit.
        entries.append([name.decode("utf-8"), value])
    return hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode("utf-8")).hexdigest()
