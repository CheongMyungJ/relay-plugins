"""Record and verify execution facts; development commands stay with the host."""
import uuid
from pathlib import Path
from . import RelayError, baselines, workspaces
from .artifacts import reference
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

# A view of the status transitions checkpoint already enforces; never a permission or approval gate.
NEXT_ACTIONS = {"planned": ("prepared",), "implementing": ("verified",), "verified": ("verified", "committed"),
                "committed": ("pushed",), "pushed": ()}
ALWAYS = ("drift", "failure", "hold")
SUMMARY_FIELDS = ("run_id", "status", "basis", "branch", "base", "path", "base_sha", "workspace", "commit", "remote_sha")


def begin(store, state, data, gh, registry):
    if state["stage"] != "implement" or not data.get("execution_authorized"):
        raise RelayError("run", "An explicit implementation request is required.")
    _, _, records, remote_runs = snapshot(state, gh)
    runs = state.setdefault("runs", {})
    active = state.get("active_run")
    if active and not data.get("new_run"):
        run = runs[active]
        baselines.select(baselines.from_records(records), state["options"].get("basis"), run)
        if run.get("workspace"):
            workspaces.verify_run(store, state, run)
        return run
    if remote_runs and not runs and not data.get("new_run"):
        raise RelayError("run", "Remote execution records exist. Confirm Git state and resume with restore before starting another run.")
    documents = baselines.from_records(records)
    basis = baselines.select(documents, state["options"].get("basis"))
    workspace = workspaces.current(store, state)
    if workspace is None:
        raise RelayError("run", "Prepare the issue workspace with workspace ensure before starting a run.")
    record, generation = workspace["record"], workspace["generation"]
    baselines.assessed(basis, documents, record)
    proof = records["plan" if basis["kind"] == "formal" else "brief"]
    # Pin the readable proof text plus its suggestion; together they rebuild the proof hash.
    summary, suggestion = proof["body"], proof["next_step"]
    run_id = uuid.uuid4().hex[:12]
    path = Path(generation["path"])
    ensure_identity(state["repository"], path)
    if git(path, "status", "--porcelain", "--untracked-files=all"):
        raise RelayError("run", "The issue workspace has uncommitted changes; resolve them before a new run.")
    ref = workspace["reference"]
    run = {"run_id": run_id, "status": "implementing", "parents": basis["parents"],
           "basis": basis, "basis_summary": summary, "basis_next_step": suggestion,
           "branch": generation["branch"], "base": f"{state['repository']['remote']}/{generation['default_branch']}",
           # base_sha stays the run's starting HEAD; the workspace keeps its own pinned commits.
           "base_sha": ref["head_sha"], "path": str(path), "repository": state["repository"]["repo"],
           "workspace": {key: ref[key] for key in ("workspace_id", "generation", "initial_base_sha", "base_sha")},
           "drift": [], "tests": [], "holds": []}
    runs[run_id] = run
    state["active_run"] = run_id
    store.save(state)
    record_files(store, run)
    return run


def record_files(store, run):
    """The run's full record on disk; responses point here instead of repeating it."""
    folder = store.path / "runs" / run["run_id"]
    write_json(folder / "execution.json", run)
    (folder / "drift.md").write_text("\n\n".join(str(d) for d in run["drift"]) or "drift 없음\n", encoding="utf-8")
    return files(store, run)


def files(store, run):
    folder = store.path / "runs" / run["run_id"]
    return {"execution": str(folder / "execution.json"), "drift": str(folder / "drift.md"), "state": str(store.path / "state.json")}


def next_actions(run):
    return [*NEXT_ACTIONS.get(run["status"], ()), *ALWAYS]


def counts(run):
    return {"drift": len(run.get("drift", [])), "holds": len(run.get("holds", [])), "tests": len(run.get("tests", []))}


def response(action, run, store):
    """The CLI answer: what this event recorded, the current status and where the full record lives.

    begin/restore add the run summary and the pinned basis text once; an older run's
    stored plan_summary copy stays in state and execution.json and is never repeated here.
    Earlier drift, holds and tests are read from `files`, so responses do not grow per event.
    """
    common = {"counts": counts(run), "failure": run.get("failure"), "next_actions": next_actions(run)}
    if action in ("begin", "restore"):
        if not (store.path / "runs" / run["run_id"] / "execution.json").is_file():
            record_files(store, run)
        summary = {key: run[key] for key in SUMMARY_FIELDS if key in run}
        return {**summary, **common, "basis_summary": run.get("basis_summary"), "basis_next_step": run.get("basis_next_step"),
                "files": files(store, run)}
    recorded = {"drift": lambda: run["drift"][-1], "hold": lambda: run["holds"][-1], "failure": lambda: {"reason": run.get("failure")},
                "verified": lambda: {"tests": run.get("tests"), "verified_tree": run.get("verified_tree")},
                "committed": lambda: {"commit": run.get("commit")}, "pushed": lambda: {"remote_sha": run.get("remote_sha")},
                "prepared": lambda: {}}[action]()
    return {"run_id": run["run_id"], "status": run["status"], "event": action, "recorded": recorded, **common,
            "files": files(store, run)}


def kb_for_run(run, state, paths=None):
    """First lookup from the pinned proof text, or a recheck of actual changed paths; never stored."""
    from .kb import reading
    root = run["path"] if Path(run.get("path", "")).is_dir() else state["repository"]["root"]
    try:
        kb, _ = reading.worktree_kb(root)
        proof = run.get("basis_summary") or ""
        if paths is None:
            return reading.for_document(kb, proof, cwd=root)
        return reading.recheck(kb, paths, reading.document_ids(kb, proof), cwd=root)
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
    baselines.for_run(run, published=True)
    return run


def restore(store, state, data, gh, registry):
    """Recover a run from its recorded evidence after checking the actual local Git state."""
    if state["stage"] != "implement" or not data.get("execution_authorized"):
        raise RelayError("run", "Authorize resuming the existing execution first.")
    _, _, records, remote_runs = snapshot(state, gh, historical=True)
    record = remote_runs.get(data["run_id"])
    if not record:
        raise RelayError("run", "No matching remote execution record.")
    known = state.get("implementation_targets", {}).get(data["run_id"])
    if known and known != record["target"]:
        raise RelayError("conflict", "Recovery report differs from its known comment ID.")
    run = parse_evidence(record, data["run_id"])
    run.pop("plan_summary", None)
    if "basis_summary" not in run:
        # The evidence names its proof by exact reference; only that unrevised comment still holds the pinned text.
        kind = "plan" if run["basis"]["kind"] == "formal" else "brief"
        proof = records.get(kind)
        if not proof or reference(proof) != run["basis"]["proof"]:
            raise RelayError("stale", f"Execution proof {kind} was revised or removed after this run; its pinned text cannot be recovered."
                             " Keep the report as history or request a separate run on the current basis.")
        run["basis_summary"] = proof["body"]
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
    # Recorded branch/base stay on the run; they never become invocation options again.
    kept = {k: v for k, v in state.get("options", {}).items() if k not in ("branch", "base", "worktree")}
    state["options"] = {**kept, "basis": basis["kind"]}
    store.save(state)
    record_files(store, run)
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
        if run.get("workspace"):
            workspaces.verify_run(store, state, run)
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
    record_files(store, run)
    return run


def with_kb(action, result, state, run=None):
    """Add only the KB field to a begin/verified/committed response; it is never stored."""
    run = run or result
    if action == "begin":
        return dict(result, kb=kb_for_run(run, state))
    if action in ("verified", "committed"):
        # A post-hoc lookup of the paths actually changed; it never replaces the pre-edit lookup.
        return dict(result, kb_recheck=kb_for_run(run, state, changed_paths(Path(run["path"]), run["base_sha"])))
    return result


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
