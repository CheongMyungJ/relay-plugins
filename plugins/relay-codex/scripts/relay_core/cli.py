import argparse
import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from . import RelayError
from . import invocation, repository, publishing, runs, baselines, workspaces
from .artifacts import collect, collect_investigations, digest, reference
from .github import GitHub
from .state import IssueIndex, Store, read_json, storage_root, ignore_runtime, local_works, locate, work_roots

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "relay.json"


def presented(records):
    """Records as the host reads them; content duplicates body and is not sent."""
    return {key: {field: value for field, value in record.items() if field != "content"}
            for key, record in records.items()}


def inspect(data, registry):
    if data.get("stage") in ("pr", "review"):
        raise RelayError("input", "Use the dedicated pr command; PRs do not use document state.")
    parsed = invocation.parse(data["stage"], data.get("raw", ""), registry)
    repo = repository.inspect(data.get("cwd", str(Path.cwd())))
    root = new_owner_root(repo["root"])
    gh = GitHub(repo["repo"], host=repo["host"])
    found = []
    if parsed["issue"]:
        found = issue_owners(repo, parsed["issue"])
    if data.get("work_id"):
        work_id = data["work_id"]
        if not isinstance(work_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", work_id):
            raise RelayError("state", "Invalid work ID.")
        has_local_state = any(
            (owner / ".relay" / "work" / work_id / "state.json").exists()
            for owner in work_roots(repo["root"])
        )
        if has_local_state:
            store = locate(repo["root"], work_id)
            state = store.load()
        else:
            remote_ids = remote_work_ids_for(parsed["stage"], gh, parsed["issue"])
            if work_id not in remote_ids:
                raise RelayError("selection", "No remote Relay record has the selected work ID.")
            store = Store(repo["root"], work_id)
            state = {"work_id": work_id, "repository": repo, "issue": parsed["issue"], "status": "draft"}
        open_resume = parsed["stage"] == state.get("stage") == "open"
        if not repository.same_repository(state["repository"], repo) or (not open_resume and state["issue"] != parsed["issue"]):
            raise RelayError("state", "Explicit work ID belongs to a different repository or issue.")
    elif len(found) > 1:
        raise RelayError("state", "Multiple local work copies exist; inspect and select work_id.")
    elif found:
        owner, state = found[0]
        store = Store(owner, state["work_id"])
    else:
        remote_ids = remote_work_ids_for(parsed["stage"], gh, parsed["issue"])
        if len(remote_ids) > 1:
            raise RelayError("selection", "Multiple remote Relay work IDs exist; select work_id: "
                             + ", ".join(sorted(remote_ids)))
        work_id = next(iter(remote_ids)) if len(remote_ids) == 1 else None
        if work_id and (not re.fullmatch(r"[a-zA-Z0-9_-]+", work_id) or (root / ".relay" / "work" / work_id).exists()):
            work_id = None  # A remote ID already used by other local work never merges the two.
        state = {"work_id": work_id or uuid.uuid4().hex, "repository": repo, "issue": parsed["issue"], "status": "draft"}
        store = Store(root, state["work_id"])
    with store.lock():
        if (store.path / "state.json").exists():
            state = store.load()
        return inspect_work(data, parsed, repo, root, gh, state, store)


def new_owner_root(root):
    """New work lives with the normal main worktree, so every connected checkout finds it."""
    try:
        return repository.main_worktree(root)
    except RelayError:
        return storage_root(root)


def issue_owners(repo, issue):
    """The unique owner of an issue's work: the workspace index first, then connected worktree stores."""
    try:
        index = IssueIndex(repo["root"], issue)
        record = index.load() if index.exists() else None
    except RelayError:
        record = None
    if record and repository.same_repository(record, repo):
        owner = Path(record["owner_root"]).resolve()
        path = owner / ".relay" / "work" / record["work_id"] / "state.json"
        if path.exists():
            return [(owner, read_json(path))]
        return [(owner, {"work_id": record["work_id"], "repository": repo, "issue": issue, "status": "draft"})]
    found = local_works(repo["root"], repo, issue)
    if len(found) > 1:
        raise RelayError("state", "Multiple local work copies exist; inspect and select work_id: "
                         + ", ".join(sorted(state["work_id"] for _, state in found)))
    return found


def remote_work_ids(gh, issue):
    """Work IDs of Relay records on the issue; an unreadable remote leaves the choice to a new ID."""
    try:
        records, runs = collect({"body": ""}, gh.comments(issue))
    except RelayError:
        return set()
    return {record["meta"].get("work_id") for record in [*records.values(), *runs.values()]} - {None}


def remote_work_ids_for(stage, gh, issue):
    """Remote work identities eligible for an explicit cold-inspect selection."""
    if not issue:
        return set()
    if stage == "investigate":
        return {record["meta"]["work_id"] for record in collect_investigations(gh.comments(issue)).values()}
    return remote_work_ids(gh, issue)


def inspect_work(data, parsed, repo, root, gh, state, store):
    if parsed["stage"] == state.get("stage") == "open":
        parsed = dict(parsed, issue=state["issue"])
    if state.get("pending", {}).get("status") == "uncertain" and state.get("stage") != parsed["stage"]:
        raise RelayError("uncertain", "Reconcile the pending publication before switching stages.")
    from . import investigation
    if investigation.unresolved(state) and state.get("stage") != parsed["stage"]:
        raise RelayError("uncertain", "Reconcile the investigation publication before switching stages.")
    if state.get("stage") != parsed["stage"]:
        state.pop("pending", None)
    if parsed["stage"] == "implement" and state.get("active_run"):
        baselines.for_run(state["runs"][state["active_run"]], parsed["options"].get("basis"))
        # Options removed from implement are never carried forward from older state.
        old = {k: v for k, v in state.get("options", {}).items() if k not in invocation.REMOVED["implement"]}
        for key, value in parsed["options"].items():
            if key in old and old[key] != value:
                raise RelayError("run", "Options differ from the existing run; resolve the intended resume first.")
        parsed["options"] = {**old, **parsed["options"]}
    state.update(parsed)
    ignore_runtime(root)
    missing = [name for name in ("git", "gh") if not shutil.which(name)]
    error = None
    try:
        issue = gh.issue(state["issue"]) if state["issue"] else {"body": ""}
        comments = gh.comments(state["issue"]) if state["issue"] else []
    except RelayError as exc:
        # Local drafts remain usable, but no fabricated remote baseline is returned.
        issue, comments = {"body": ""}, []
        error = {"code": exc.code, "message": str(exc)}
    bindings = data.get("legacy", {})
    if bindings and (not data.get("legacy_confirmed") or error):
        raise RelayError("adoption", "Confirm the exact remote document/version/hash before binding a legacy baseline.")
    if bindings:
        raw = {str(c["id"]): c["body"] for c in comments}
        for target, binding in bindings.items():
            if target not in raw or digest(raw[target]) != binding["hash"]:
                raise RelayError("adoption", "Legacy binding does not match remote content.")
        state.setdefault("legacy", {}).update(bindings)
    records, remote_runs = collect(issue, comments, state.get("legacy")) if not error else ({}, {})
    investigations = collect_investigations(comments) if not error else {}
    known_investigations = state.setdefault("investigation_targets", {})
    for key, record in investigations.items():
        if key in known_investigations and known_investigations[key] != record["target"]:
            raise RelayError("conflict", "Known investigation was replaced.")
        known_investigations[key] = record["target"]
    selected = data.get("investigation_id")
    if parsed["stage"] == "investigate":
        candidates = list(state.get("investigations", {}))
        if selected:
            investigation.load(store, state, selected)
            state["active_investigation"] = selected
        elif len(candidates) == 1:
            state["active_investigation"] = candidates[0]
        elif len(candidates) > 1:
            state["active_investigation"] = None
    known = state.setdefault("document_targets", {})
    for kind, record in records.items():
        if kind in known and known[kind] != record["target"]:
            raise RelayError("conflict", "A known document was replaced at another comment ID.")
        known[kind] = record["target"]
    known_runs = state.setdefault("implementation_targets", {})
    for run_id, record in remote_runs.items():
        if run_id in known_runs and known_runs[run_id] != record["target"]:
            raise RelayError("conflict", "A known execution report was replaced at another comment ID.")
        known_runs[run_id] = record["target"]
    basis, basis_error = None, None
    if parsed["stage"] == "implement" and not error:
        try:
            basis = baselines.select(baselines.from_records(records), parsed["options"].get("basis"),
                                     state.get("runs", {}).get(state.get("active_run")))
            if set(known) - set(records):
                raise RelayError("conflict", "A known document is missing.")
        except RelayError as exc:
            basis_error = {"code": exc.code, "message": str(exc)}
    store.save(state)
    return {"work_id": state["work_id"], "work_path": str(store.path), "repository": repo,
            "input": parsed, "documents": presented(records), "runs": presented(remote_runs), "state": state,
            "kb": kb_field(parsed["stage"], state, repo, records, basis),
            "workspace": workspaces.summary(store.root, state),
            "investigations": investigations, "investigation_selection_required": parsed["stage"] == "investigate" and len(state.get("investigations", {})) > 1 and not state.get("active_investigation"),
            "source_issue": {"number": state["issue"], "url": issue.get("html_url"), "title": issue.get("title"), "body": issue["body"]} if state["issue"] and not error else None,
            "unmanaged": [{"target": str(c["id"]), "body": c["body"]} for c in comments],
            "missing_tools": missing, "remote_error": error, "basis": basis, "basis_error": basis_error}


def kb_field(stage, state, repo, records, basis):
    """implement: entries for the selected proof's paths/IDs; document stages: counts; no KB: null."""
    from .kb import reading, lookup
    try:
        active = state.get("runs", {}).get(state.get("active_run")) if stage == "implement" else None
        root = active["path"] if active and Path(active.get("path", "")).is_dir() else repo["root"]
        kb, _ = reading.worktree_kb(root)
        if stage != "implement":
            return lookup.counts(kb)
        if active:
            proof = active.get("basis_summary") or active.get("plan_summary") or ""
        elif basis:
            proof = records["plan" if basis["kind"] == "formal" else "brief"]["body"]
        else:
            return lookup.counts(kb)
        return reading.for_document(kb, proof)
    except RelayError as exc:
        # A damaged KB is reported with the inspection instead of hiding the documents.
        return {"error": exc.code + ": " + str(exc)}


def main():
    parser = argparse.ArgumentParser(description="Relay internal recording helper")
    parser.add_argument("command", choices=["inspect", "workspace", "prepare", "publish", "run", "pr", "review", "investigate", "kb"])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--work")
    args = parser.parse_args()
    try:
        data = read_json(args.input)
        registry = read_json(REGISTRY_PATH)["skills"]
        if not isinstance(data, dict):
            raise RelayError("input", "Input must be a JSON object.")
        if args.command == "review":
            from .review import dispatch
            result = dispatch(data, registry)
        elif args.command == "pr":
            from .pr import dispatch
            result = dispatch(data, registry)
        elif args.command == "kb":
            from .kb import dispatch
            result = dispatch(data, registry)
        elif data.get("stage") in ("pr", "review"):
            raise RelayError("input", "Use the dedicated pr or review command.")
        elif args.command == "inspect":
            result = inspect(data, registry)
        else:
            if not args.work:
                raise RelayError("input", "--work is required for this internal command.")
            repo = repository.inspect(Path.cwd())
            store = locate(repo["root"], args.work)
            with store.lock():
                state = store.load()
                repository.ensure_identity(state["repository"], Path.cwd())
                gh = GitHub(state["repository"]["repo"], host=state["repository"].get("host", "github.com"))
                if args.command == "workspace":
                    result = workspaces.dispatch(store, state, data, gh, registry)
                elif args.command == "investigate":
                    from .investigation import dispatch
                    result = dispatch(store, state, data, gh)
                elif state["stage"] == "investigate":
                    raise RelayError("input", "Use the dedicated investigate command.")
                elif args.command == "prepare":
                    result = publishing.prepare(store, state, data, gh, registry)
                elif args.command == "publish":
                    result = publishing.publish(store, state, data, gh, registry)
                elif data["action"] == "begin":
                    result = runs.with_kb("begin", runs.begin(store, state, data, gh, registry), state)
                elif data["action"] == "restore":
                    result = runs.restore(store, state, data, gh, registry)
                else:
                    result = runs.with_kb(data["action"], runs.checkpoint(store, state, data, gh, registry), state)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    except (RelayError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": getattr(exc, "code", "input"), "message": str(exc)}, ensure_ascii=False))
        sys.exit(1)
