"""Independent investigation journal. Hosts run experiments; this module records facts.

An event is committed before its materialized state. Retrying that same event
finishes interrupted writes without replaying an experiment or incrementing twice.
"""
import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import RelayError, repository
from . import next_step as steps
from .artifacts import collect_investigations, digest, reference
from .state import read_json, write_json, ignore_runtime


def now():
    return datetime.now(timezone.utc).isoformat()


def hashed(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise RelayError("input", "Investigation and event IDs must be lowercase UUID hex.")
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise RelayError("input", name + " must be nonempty text.")
    return value


def strings(value, name):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise RelayError("input", name + " must be a list of nonempty strings.")
    return value


def bounded(path, root):
    path, root = Path(path), Path(root).resolve()
    if not path.resolve().is_relative_to(root):
        raise RelayError("state", "Investigation path escapes its storage root.")
    current = path
    while current != root and current.is_relative_to(root):
        if current.is_symlink():
            raise RelayError("state", "Investigation storage cannot traverse a symlink.")
        current = current.parent
    return path


def directory(store, key):
    path = bounded(store.path / "investigations" / identifier(key), store.root)
    if path.exists() and any(p.is_symlink() for p in path.rglob("*")):
        raise RelayError("state", "Investigation storage cannot contain symlinks.")
    return path


def relative(value):
    text(value, "relative path")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or ":" in value or not path.parts:
        raise RelayError("input", "Use a repository-relative file path without traversal.")
    return path.as_posix()


def signatures(root, files):
    root = Path(root).resolve()
    result = []
    for name in strings(files, "scope files"):
        name = relative(name)
        path = root / name
        # Do not follow links, including links in any parent directory.
        bounded(path.parent, root)
        if path.is_symlink():
            value = {"path": name, "kind": "symlink", "hash": digest(str(path.readlink()))}
        elif not path.exists():
            value = {"path": name, "kind": "missing", "hash": None}
        elif path.is_file():
            value = {"path": name, "kind": "file", "hash": hashlib.sha256(path.read_bytes()).hexdigest()}
        else:
            raise RelayError("input", "Scope must name files, not directories.")
        result.append(value)
    return result


def baseline(store, request):
    if not isinstance(request, dict):
        raise RelayError("input", "baseline must be an object.")
    cwd = Path(text(request.get("cwd"), "cwd")).resolve()
    if repository.common_dir(cwd) != repository.common_dir(store.root):
        raise RelayError("repository", "Investigation workspace belongs to another repository.")
    env = request.get("environment")
    if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
        raise RelayError("input", "environment must contain only selected safe text values.")
    return {"cwd": str(cwd), "base_sha": repository.git(cwd, "rev-parse", "HEAD"),
            "files": signatures(cwd, request.get("files")), "environment": env,
            "environment_hash": hashed(env)}


def manifest(store, key, entries):
    if not isinstance(entries, list):
        raise RelayError("input", "evidence must be a list.")
    root = directory(store, key) / "evidence"
    result = []
    for item in entries:
        name = relative(item["path"])
        path = bounded(root / name, root)
        if path.is_symlink() or not path.is_file():
            raise RelayError("input", "Evidence must be an available regular file.")
        data = path.read_bytes()
        result.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                       "description": text(item.get("description"), "evidence description")})
    return result


def applicability(store, run, current=None):
    old = run["baseline"]
    reasons, unavailable = [], []
    try:
        submitted = (run.get("applicability") or {}).get("current") or old
        current = current or baseline(store, {"cwd": submitted["cwd"], "files": [f["path"] for f in old["files"]],
                                               "environment": submitted["environment"]})
        for field in ("base_sha", "files", "environment_hash"):
            if current[field] != old[field]:
                reasons.append(field + " changed; reassess recorded evidence")
    except (RelayError, OSError) as exc:
        unavailable.append(str(exc))
    root = directory(store, run["investigation_id"]) / "evidence"
    for entry in run["evidence"]:
        try:
            path = bounded(root / relative(entry["path"]), root)
            if not path.is_file() or path.is_symlink():
                unavailable.append(entry["path"] + " unavailable")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                reasons.append(entry["path"] + " content changed")
        except (OSError, RelayError) as exc:
            unavailable.append(str(exc))
    for change in run["changes"]:
        try:
            workspace = Path(change["worktree"])
            if not workspace.is_dir() or repository.common_dir(workspace) != repository.common_dir(store.root):
                unavailable.append("Experimental worktree unavailable: " + str(workspace))
            elif signatures(workspace, [change["path"]])[0] != change["signature"]:
                reasons.append("Experimental file changed: " + change["path"])
        except (OSError, RelayError) as exc:
            unavailable.append(str(exc))
    return {"status": "unavailable" if unavailable else "needs_reassessment" if reasons else "current",
            "reasons": unavailable + reasons or ["Recorded code and evidence match; environment comparison covers only submitted keys."],
            "current": current}


def load(store, state, key):
    path = directory(store, key) / "investigation.json"
    run = read_json(path)
    if (run.get("schema") != 1 or run.get("investigation_id") != key or run.get("work_id") != state["work_id"]
            or run.get("issue") != state["issue"] or not repository.same_repository(run["repository"], state["repository"])):
        raise RelayError("state", "Investigation identity differs from work/repository/issue.")
    return run


def save(store, state, run):
    for change in run["changes"]:
        workspace = Path(change["worktree"]).resolve()
        if not workspace.is_dir():
            continue  # Remote recovery does not recreate missing experiment trees.
        if repository.common_dir(workspace) != repository.common_dir(store.root):
            raise RelayError("repository", "Experimental workspace identity changed.")
        pointer = bounded(workspace / ".relay/pointer.json", workspace)
        if pointer.exists() and Path(read_json(pointer)["root"]).resolve() != store.root:
            raise RelayError("state", "Experimental worktree already points to another state owner.")
        ignore_runtime(workspace)
        write_json(pointer, {"root": str(store.root)})
    write_json(directory(store, run["investigation_id"]) / "investigation.json", run)
    state.setdefault("investigations", {})[run["investigation_id"]] = copy.deepcopy(run)
    state["active_investigation"] = run["investigation_id"]
    store.save(state)


def unresolved(state):
    return any(r.get("publication") in ("publishing", "uncertain") for r in state.get("investigations", {}).values())


def journal_ready(store, state, data):
    """Do not bypass an interrupted materialization with a new logical effect."""
    for path in (store.path / "investigations").glob("*/investigation.json"):
        key = path.parent.name
        run = load(store, state, key)
        if run != state.get("investigations", {}).get(key):
            event = data.get("event_id")
            retry = data.get("operation") in ("checkpoint", "resume") and data.get("investigation_id") == key and event in run.get("applied_events", [])
            retry = retry or data.get("operation") == "start" and (store.path / "investigations/starts" / (str(data.get("client_request_id")) + ".json")).is_file()
            pending = run.get("pending") or {}
            retry = retry or (data.get("operation") == "publish" and data.get("investigation_id") == key
                              and data.get("request_id") == pending.get("request_id") and data.get("hash") == pending.get("hash"))
            if not retry:
                raise RelayError("conflict", "Investigation index needs the original operation retry.")
        for event_path in (path.parent / "checkpoints").glob("*.json"):
            if event_path.stem not in run["applied_events"] and not (data.get("investigation_id") == key and data.get("event_id") == event_path.stem):
                raise RelayError("conflict", "Retry the surviving unmaterialized checkpoint before another operation.")


def start(store, state, data, gh):
    request = identifier(data.get("client_request_id"))
    path = bounded(store.path / "investigations" / "starts" / (request + ".json"), store.root)
    payload_hash = hashed(data)
    if path.exists():
        event = read_json(path)
        if event["payload_hash"] != payload_hash:
            raise RelayError("conflict", "Start request ID was used with different content.")
        runpath = directory(store, event["run"]["investigation_id"]) / "investigation.json"
        run = load(store, state, event["run"]["investigation_id"]) if runpath.exists() else event["run"]
        save(store, state, run)
        return run
    if unresolved(state):
        raise RelayError("uncertain", "Reconcile the original investigation publication first.")
    if state.get("investigations") and data.get("new_investigation") is not True:
        raise RelayError("selection", "Select and resume an existing investigation, or explicitly request a new one.")
    if data.get("execution_authorized") is not True:
        raise RelayError("approval", "Explicit investigation invocation is required.")
    gh.issue(state["issue"])
    scope = data.get("request", {})
    if not isinstance(scope, dict):
        raise RelayError("input", "request must be an object.")
    for field in ("symptom", "expected", "impact", "scope", "limits"):
        text(scope.get(field), field)
    if not strings(scope.get("stop_conditions"), "stop_conditions"):
        raise RelayError("input", "At least one stop condition is required.")
    key = uuid.uuid4().hex
    run = {"schema": 1, "work_id": state["work_id"], "investigation_id": key,
           "repository": {k: state["repository"].get(k, "github.com") for k in ("host", "repo")},
           "issue": state["issue"], "created_at": now(), "updated_at": now(), "revision": 0,
           "status": "investigating", "publication": "none", "pending": None, "target": None,
           "request": scope, "baseline": baseline(store, data["baseline"]), "outcome": None,
           "conclusion": None, "observations": [], "hypotheses": [], "experiments": [], "facts": [],
           "excluded_causes": [], "uncertainties": [], "evidence": [], "changes": [], "failure": None,
           "active_checkpoint": None, "applied_events": [], "history": [], "applicability": None}
    write_json(path, {"payload_hash": payload_hash, "run": run})
    save(store, state, run)
    return run


def validate_result(run):
    ids = {}
    for group in ("observations", "hypotheses", "experiments", "facts", "excluded_causes"):
        if not isinstance(run[group], list):
            raise RelayError("input", group + " must be a list.")
        for item in run[group]:
            if not isinstance(item, dict):
                raise RelayError("input", group + " entries must be objects.")
            key = text(item.get("id"), group + " id")
            if key in ids:
                raise RelayError("input", "Evidence IDs must be unique.")
            ids[key] = group
            text(item.get("summary"), group + " summary")
    evidence = {item["id"] for item in run["observations"]}
    evidence.update(item["id"] for item in run["experiments"] if item.get("status") == "performed")
    def supports(item):
        refs = strings(item.get("supports"), "supports")
        if not refs or not set(refs) <= evidence:
            raise RelayError("input", "A claim needs existing observation/experiment evidence IDs.")
    for group in ("facts", "excluded_causes"):
        for item in run[group]:
            supports(item)
            if group == "excluded_causes":
                text(item.get("conditions"), "exclusion conditions")
    for item in run["experiments"]:
        if ids.get(item.get("hypothesis")) != "hypotheses":
            raise RelayError("input", "Experiment must reference a hypothesis.")
        for field in ("input", "command", "expected", "limitations"):
            text(item.get(field), field)
        if item.get("status") not in ("planned", "performed"):
            raise RelayError("input", "Experiment status must be planned or performed.")
        if item["status"] == "performed":
            text(item.get("actual"), "actual")
            if type(item.get("exit_code")) is not int or not re.fullmatch(r"[a-f0-9]{40,64}", str(item.get("sha", ""))):
                raise RelayError("input", "Performed experiments require observed exit code and SHA.")
            if not isinstance(item.get("environment"), dict):
                raise RelayError("input", "Performed experiments require their environment.")
    strings(run["uncertainties"], "uncertainties")
    if run["failure"] is not None:
        text(run["failure"], "failure")
    outcome = run["outcome"]
    if outcome not in (None, "resolved", "held", "no_change"):
        raise RelayError("input", "Unknown investigation outcome.")
    if outcome is not None:
        result = run["conclusion"]
        if not isinstance(result, dict):
            raise RelayError("input", "Outcome requires a structured conclusion.")
        for field in ("summary", "impact"):
            text(result.get(field), field)
        suggestion = steps.validate(result.get("next_step"))
        # An investigation hands evidence to a document stage; it never becomes a
        # basis for implementation or a PR prerequisite.
        if suggestion["next"] not in (None, "brief", "intent", "design", "investigate"):
            raise RelayError("input", "An investigation can only suggest brief, intent, design, investigate or null.")
        if outcome in ("held", "no_change") and suggestion["next"] is not None:
            raise RelayError("input", "A held or unchanged investigation records next null and waits for the person.")
        if outcome == "resolved":
            text(result.get("cause"), "cause")
            supports(result)
            if not any(e["id"] in result["supports"] and e["status"] == "performed" for e in run["experiments"]):
                raise RelayError("input", "Resolved requires performed supporting verification.")
        elif outcome == "held":
            if not strings(result.get("needed"), "needed") or not strings(result.get("resume"), "resume"):
                raise RelayError("input", "Held requires missing information and resume conditions.")
        else:
            text(result.get("stop_reason"), "stop_reason")


def checkpoint(store, state, data, resume=False):
    key, event_id = identifier(data.get("investigation_id")), identifier(data.get("event_id"))
    run = load(store, state, key)
    path = directory(store, key) / "checkpoints" / (event_id + ".json")
    payload_hash = hashed(data)
    if path.exists():
        event = read_json(path)
        if event["payload_hash"] != payload_hash:
            raise RelayError("conflict", "Event ID has different content.")
        if (event.get("after_hash") != hashed(event["after"]) or event["after"].get("active_checkpoint") != event_id
                or event["after"].get("revision") != event["expected_revision"] + 1):
            raise RelayError("conflict", "Checkpoint record is inconsistent; preserve it for inspection.")
        if event_id in run["applied_events"]:
            save(store, state, run)
            return run
        if run["revision"] != event["expected_revision"] or hashed(run) != event.get("before_hash"):
            raise RelayError("conflict", "Journal and state revisions disagree; preserve and inspect them.")
        save(store, state, event["after"])
        return event["after"]
    for pending in (directory(store, key) / "checkpoints").glob("*.json"):
        if pending.stem not in run["applied_events"]:
            raise RelayError("conflict", "An unapplied event survives; retry its exact event ID first.")
    if run["publication"] in ("uncertain", "publishing"):
        raise RelayError("uncertain", "Reconcile the original publication before changing evidence.")
    if type(data.get("expected_revision")) is not int or data["expected_revision"] != run["revision"]:
        raise RelayError("conflict", "Checkpoint expected_revision differs.")
    after = copy.deepcopy(run)
    if resume:
        text(data.get("reason"), "resume reason")
        current = baseline(store, data["baseline"])
        after["applicability"] = applicability(store, run, current)
        after["history"].append({"revision": run["revision"], "outcome": run["outcome"], "conclusion": run["conclusion"],
                                 "baseline": run["baseline"], "reason": data["reason"]})
        after.update(outcome=None, conclusion=None, status="investigating", failure=None)
    else:
        changes = data.get("changes")
        allowed = {"observations", "hypotheses", "experiments", "facts", "excluded_causes", "uncertainties", "outcome", "conclusion", "failure", "evidence", "changes", "assessment"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise RelayError("input", "Unknown checkpoint field.")
        after.update(copy.deepcopy(changes))
        if "evidence" in changes:
            after["evidence"] = manifest(store, key, changes["evidence"])
        if "assessment" in changes:
            assessment = changes["assessment"]
            if not isinstance(assessment, dict):
                raise RelayError("input", "assessment must be an object.")
            text(assessment.get("reason"), "assessment reason")
            current = baseline(store, assessment["baseline"])
            after["history"].append({"revision": run["revision"], "baseline": run["baseline"], "assessment": assessment})
            after["baseline"] = current
            after["applicability"] = applicability(store, after, current)
        if after["changes"]:
            validate_changes(store, after)
        validate_result(after)
        after["status"] = "held" if after["outcome"] == "held" else "concluded" if after["outcome"] else "investigating"
    after.update(revision=run["revision"] + 1, active_checkpoint=event_id, updated_at=now(), pending=None,
                 publication="recorded" if run["target"] else "none")
    after["applied_events"].append(event_id)
    write_json(path, {"payload_hash": payload_hash, "expected_revision": run["revision"],
                      "before_hash": hashed(run), "after_hash": hashed(after), "after": after})
    save(store, state, after)
    return after


def validate_changes(store, run):
    if not isinstance(run["changes"], list):
        raise RelayError("input", "changes must be a list.")
    for change in run["changes"]:
        cwd = Path(text(change.get("worktree"), "worktree")).resolve()
        source = Path(run["baseline"]["cwd"]).resolve()
        if cwd == source or cwd not in repository.worktrees(store.root) or repository.common_dir(cwd) != repository.common_dir(store.root):
            raise RelayError("input", "File experiments require a separate connected worktree.")
        for field in ("purpose", "origin"):
            text(change.get(field), field)
        if type(change.get("candidate")) is not bool:
            raise RelayError("input", "candidate must explicitly identify a possible later fix.")
        actual = signatures(cwd, [change["path"]])[0]
        if change.get("signature") != actual:
            raise RelayError("input", "Experimental file signature differs.")
        if change.get("patch") not in {e["path"] for e in run["evidence"]}:
            raise RelayError("input", "Preserve the experimental patch in evidence first.")


def remote_records(state, gh):
    gh.issue(state["issue"])
    records = collect_investigations(gh.comments(state["issue"]))
    for key, target in state.get("investigation_targets", {}).items():
        if key not in records or records[key]["target"] != target:
            raise RelayError("conflict", "A known investigation comment is missing or replaced.")
    return records


def evidence_refs(state, values, gh):
    if not isinstance(values, list):
        raise RelayError("input", "evidence_refs must be a list.")
    records = remote_records(state, gh) if values else {}
    for value in values:
        if not isinstance(value, dict):
            raise RelayError("input", "evidence_refs entries must be objects.")
        if not repository.same_repository(value, state["repository"]) or value.get("issue") != state["issue"]:
            raise RelayError("stale", "Investigation evidence belongs to another repository/issue.")
        record = records.get(value.get("investigation_id"))
        if not record or reference(record) != value.get("reference"):
            raise RelayError("stale", "Investigation evidence reference changed; reassess it.")
        from .investigation_publishing import restored_summary
        run = restored_summary(record, state)
        ids = {i["id"] for name in ("observations", "experiments", "facts", "excluded_causes") for i in run[name]}
        chosen = strings(value.get("evidence_ids"), "evidence_ids")
        if not chosen or not set(chosen) <= ids:
            raise RelayError("input", "Select existing investigation evidence IDs.")
    return copy.deepcopy(values)


def dispatch(store, state, data, gh):
    if state.get("stage") != "investigate":
        raise RelayError("input", "Inspect the investigate stage first.")
    op = data.get("operation")
    journal_ready(store, state, data)
    if op != "start" and data.get("investigation_id"):
        identifier(data["investigation_id"])
        selected = state.get("active_investigation")
        if selected != data["investigation_id"] and op != "recover":
            raise RelayError("selection", "Inspect and select this investigation before mutating it.")
    if op == "start":
        return start(store, state, data, gh)
    if op in ("checkpoint", "resume"):
        return checkpoint(store, state, data, resume=op == "resume")
    if op in ("prepare", "publish", "recover"):
        from . import investigation_publishing
        return getattr(investigation_publishing, op)(store, state, data, gh)
    raise RelayError("input", "Unknown investigation operation.")
