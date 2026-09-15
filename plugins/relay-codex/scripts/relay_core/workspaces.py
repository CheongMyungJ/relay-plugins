"""Issue workspaces: one persistent checkout per issue, pinned to a verified remote commit.

The helper prepares, reuses and refreshes the Git worktree in one command. Hosts only read,
develop and test inside the returned path. Storage fields and phases: docs/internals.md.
"""
import copy
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import RelayError, baselines, repository
from .state import IssueIndex, ignore_runtime, read_json, write_json

STAGES = ("intent", "design", "plan", "brief", "implement", "investigate")
KINDS = ("intent", "spec", "plan", "brief")


class Blocked(Exception):
    def __init__(self, reason, message, facts=None, needed=None):
        super().__init__(message)
        self.result = {"status": "blocked", "reason": reason, "message": message,
                       "facts": facts or {}, "needed": needed or []}


def now():
    return datetime.now(timezone.utc).isoformat()


def request_id(value):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise RelayError("input", "request_id must be lowercase 32-character UUID hex.")
    return value


def names(record, number):
    short = record["workspace_id"][:12]
    main = Path(record["main_root"])
    return {"path": str(main.parent / f"{main.name}-relay-{record['issue']}-{short}-g{number}"),
            "branch": f"relay/{record['issue']}-{short}-g{number}",
            "private_ref": f"refs/relay/workspaces/{record['issue']}/{short}-g{number}"}


def generation(record, number):
    return next((g for g in record["generations"] if g["number"] == number), None)


def active(record):
    return generation(record, record["active_generation"]) if record.get("active_generation") else None


def unfinished(record):
    return next((g for g in record["generations"] if g["status"] != "prepared"), None)


def ready(store, record, gen, **extra):
    return {"status": "ready", "path": gen["path"], "work_path": str(store.path),
            "workspace_id": record["workspace_id"], "generation": gen["number"],
            "initial_base_sha": record["initial_base_sha"], "base_sha": gen["base_sha"],
            "head_sha": gen["head_sha"], "branch": gen["branch"], "default_branch": gen["default_branch"], **extra}


def reference(record, gen, head):
    """The code generation a document or run was written against."""
    return {"workspace_id": record["workspace_id"], "generation": gen["number"],
            "initial_base_sha": record["initial_base_sha"], "base_sha": gen["base_sha"], "head_sha": head}


# Entry requirements -------------------------------------------------------------------------

def requirements(store, state, gh, registry):
    """Prior documents first: a workspace never substitutes for an approved document."""
    from .publishing import snapshot
    try:
        _, _, records, remote_runs = snapshot(state, gh)
        stage = state["stage"]
        if stage == "implement":
            run = state.get("runs", {}).get(state.get("active_run"))
            if run:
                # A registered run keeps its pinned basis; checkpoints judge its currency, and a
                # changed basis remains a reportable hold rather than a lost workspace.
                baselines.for_run(run, state.get("options", {}).get("basis"))
            else:
                baselines.select(baselines.from_records(records), state.get("options", {}).get("basis"))
        else:
            missing = [name for name in registry[stage]["requires"] if name not in records or records[name]["stale"]]
            if missing:
                raise Blocked("documents", "Record current prior documents before this stage.",
                              {"missing_or_stale": missing}, [f"Run the stage that records {name} first." for name in missing])
    except RelayError as exc:
        reason = "remote" if exc.code == "github" else "documents"
        raise Blocked(reason, str(exc), {"code": exc.code},
                      ["Retry after GitHub access is restored." if reason == "remote"
                       else "Record or select a current execution basis first."]) from exc
    return records, remote_runs


def legacy_run(state):
    """An implementation run recorded before issue workspaces keeps its own path and branch."""
    run = state.get("runs", {}).get(state.get("active_run"))
    return run if state.get("stage") == "implement" and run and "workspace" not in run else None


# Index identity ------------------------------------------------------------------------------

def open_index(store, state):
    try:
        return IssueIndex(store.root, state["issue"])
    except RelayError as exc:
        raise Blocked("layout", str(exc), {"root": str(store.root)},
                      ["Use a normal (non-bare) main worktree and its connected worktrees."]) from exc


def owned(index, record, store, state):
    repo = state["repository"]
    facts = {"index": str(index.path), "work_id": record.get("work_id"), "owner_root": record.get("owner_root")}
    if (not repository.same_repository(record, repo) or record.get("remote") != repo["remote"]
            or Path(record.get("main_root", "")).resolve() != index.root):
        raise Blocked("identity", "Issue workspace belongs to another repository, remote or main worktree.", facts,
                      ["Inspect the index file and choose how to resolve it; Relay does not move it."])
    if record.get("work_id") != state["work_id"] or Path(record.get("owner_root", "")).resolve() != store.root:
        raise Blocked("owner", "Another local work owns this issue workspace.", facts,
                      ["Inspect again from the owning checkout or select its work_id."])
    return record


def load_or_create(index, store, state):
    if index.exists():
        return owned(index, index.load(), store, state), False
    repo = state["repository"]
    record = {"schema": 1, "host": repo.get("host", "github.com"), "repo": repo["repo"], "remote": repo["remote"],
              "issue": state["issue"], "work_id": state["work_id"], "owner_root": str(store.root),
              "main_root": str(index.root), "workspace_id": uuid.uuid4().hex, "initial_base_sha": None,
              "active_generation": None, "generations": [], "evaluations": [], "created_at": now()}
    return record, True


# Git phases ----------------------------------------------------------------------------------

def failure(index, record, gen, phase, exc):
    gen.setdefault("failures", []).append({"phase": phase, "message": str(exc), "at": now()})
    index.save(record)


def fetch(index, record, gen, gh):
    try:
        default = gen.get("default_branch") or gh.api(gh.prefix)["default_branch"]
        sha = repository.pinned_tip(index.root, record["remote"], default, gen["private_ref"])
    except RelayError as exc:
        failure(index, record, gen, "fetch", exc)
        raise Blocked("remote", "The remote default branch could not be verified; no local ref was used instead.",
                      {"generation": gen["number"], "request_id": gen["request_id"], "code": exc.code},
                      ["Retry the same request after the remote is reachable."]) from exc
    gen.update(status="fetched", default_branch=default, base_sha=sha, fetched_at=now())
    index.save(record)


def create(index, record, gen):
    root, path, branch = index.root, Path(gen["path"]).resolve(), gen["branch"]
    facts = {"path": str(path), "branch": branch, "base_sha": gen["base_sha"], "generation": gen["number"]}
    try:
        kind = repository.git(root, "cat-file", "-t", gen["base_sha"])
    except RelayError:
        kind = None
    if kind != "commit":
        raise Blocked("remote", "The pinned commit is no longer available locally.", facts,
                      ["Inspect the private ref before retrying; Relay does not switch to another commit."])
    entries = repository.worktree_entries(root)
    entry = next((e for e in entries if e["path"] == path), None)
    elsewhere = [str(e["path"]) for e in entries if e["branch"] == branch and e["path"] != path]
    tip = repository.local_tip(root, branch)
    if elsewhere:
        raise Blocked("branch_conflict", "The workspace branch is checked out elsewhere.", dict(facts, checked_out=elsewhere),
                      ["Decide what to do with that checkout; Relay does not move it."])
    if entry:
        if entry["branch"] != branch:
            raise Blocked("path_conflict", "A different checkout occupies the workspace path.", dict(facts, found_branch=entry["branch"]),
                          ["Move or keep that checkout yourself; Relay does not overwrite it."])
    elif path.exists():
        raise Blocked("path_conflict", "An unrelated directory occupies the workspace path.", facts,
                      ["Move or keep that directory yourself; Relay does not overwrite it."])
    elif tip is not None:
        if tip != gen["base_sha"]:
            raise Blocked("branch_conflict", "The workspace branch exists at another commit.", dict(facts, branch_tip=tip),
                          ["Decide what to do with that branch; Relay does not reset it."])
        repository.git(root, "worktree", "add", str(path), branch)
    else:
        repository.git(root, "worktree", "add", "-b", branch, str(path), gen["base_sha"])
    head = verify(index, record, gen, pinned=True)
    pointer = path / ".relay" / "pointer.json"
    owner = Path(record["owner_root"]).resolve()
    if path != owner:
        if pointer.exists() and Path(read_json(pointer).get("root", "")).resolve() != owner:
            raise Blocked("pointer_conflict", "The workspace already points to another state owner.", facts,
                          ["Inspect its .relay/pointer.json; Relay does not replace it."])
        write_json(pointer, {"root": str(owner)})
    if repository.git(path, "status", "--porcelain", "--untracked-files=all"):
        raise Blocked("dirty", "The new workspace is not clean.", facts, ["Inspect the checkout before retrying."])
    gen.update(status="prepared", head_sha=head, prepared_at=now())
    record["active_generation"] = gen["number"]
    record["initial_base_sha"] = record["initial_base_sha"] or gen["base_sha"]
    index.save(record)


def verify(index, record, gen, pinned=False):
    """Path, repository, branch and HEAD of one generation; returns its HEAD."""
    path = Path(gen["path"]).resolve()
    facts = {"path": str(path), "branch": gen["branch"], "base_sha": gen.get("base_sha"),
             "head_sha": gen.get("head_sha"), "generation": gen["number"]}
    if not path.is_dir():
        facts["branch_tip"] = repository.local_tip(index.root, gen["branch"])
        raise Blocked("missing", "The recorded workspace path is missing.", facts,
                      ["Restore the checkout at this path or decide how to recover; Relay does not recreate it."])
    try:
        same = repository.common_dir(path) == repository.common_dir(index.root)
        branch = repository.git(path, "branch", "--show-current") if same else None
        head = repository.git(path, "rev-parse", "HEAD") if same else None
    except RelayError:
        same = False
    if not same or branch != gen["branch"]:
        raise Blocked("mismatch", "The workspace path is not this repository's checkout of the recorded branch.", facts,
                      ["Inspect the checkout; Relay does not switch or overwrite it."])
    if pinned and head != gen["base_sha"]:
        raise Blocked("mismatch", "The unfinished workspace is not at its pinned commit.", dict(facts, actual_head=head),
                      ["Inspect the checkout before retrying."])
    if not pinned and repository.git_raw(path, "rev-list", "--count", head + ".." + gen["base_sha"]).strip() != "0":
        raise Blocked("mismatch", "The workspace HEAD no longer contains its base commit.", dict(facts, actual_head=head),
                      ["Inspect the branch history; Relay does not reset or rebase it."])
    return head


def advance(index, record, gen, gh):
    if gen["status"] == "reserved":
        fetch(index, record, gen, gh)
    if gen["status"] == "fetched":
        create(index, record, gen)


def reserve(index, record, number, request):
    gen = {"number": number, "request_id": request, "status": "reserved", **names(record, number),
           "default_branch": None, "base_sha": None, "head_sha": None, "created_at": now()}
    record["generations"].append(gen)
    index.save(record)
    return gen


# Operations -----------------------------------------------------------------------------------

def ensure(store, state, data, gh, registry):
    request = request_id(data.get("request_id"))
    _, remote_runs = requirements(store, state, gh, registry)
    legacy = legacy_run(state)
    if legacy:
        return {"status": "ready", "legacy_run": True, "path": legacy["path"], "work_path": str(store.path),
                "branch": legacy["branch"], "base_sha": legacy["base_sha"], "run_id": legacy["run_id"],
                "workspace_id": None, "generation": None}
    index = open_index(store, state)
    with index.lock():
        record, new = load_or_create(index, store, state)
        if new and state["stage"] == "implement" and set(remote_runs) - set(state.get("runs", {})):
            facts = {run_id: remote_facts(item, run_id) for run_id, item in remote_runs.items()}
            raise Blocked("restore", "Execution records exist without their local state.", facts,
                          ["Compare the recorded path, branch and SHA with local Git state and restore that run."])
        gen = unfinished(record)
        if gen is None and not record["generations"]:
            gen = reserve(index, record, 1, request)
        if gen is not None and gen["number"] == 1:
            advance(index, record, gen, gh)  # An interrupted first preparation resumes its recorded phase.
        current = active(record)
        current["head_sha"] = verify(index, record, current)
        index.save(record)
        pending = unfinished(record)
    mark(store, state, record)
    extra = {"created": new}
    if pending:
        # A failed refresh never replaces the active generation; only its own request_id retries it.
        extra["unfinished_refresh"] = {"generation": pending["number"], "request_id": pending["request_id"],
                                       "status": pending["status"]}
    return ready(store, record, current, **extra)


def remote_facts(item, run_id):
    from .runs import parse_evidence
    try:
        run = parse_evidence(item, run_id)
        return {key: run.get(key) for key in ("path", "branch", "base_sha", "commit", "status")}
    except (RelayError, ValueError, KeyError, TypeError) as exc:
        return {"error": str(exc)}


def refresh(store, state, data, gh, registry):
    if data.get("user_requested") is not True:
        raise RelayError("input", "Refresh only on the user's explicit request (user_requested:true).")
    if data.get("request_id") is None:
        raise RelayError("input", "Refresh needs a request_id kept across retries.")
    request = request_id(data["request_id"])
    requirements(store, state, gh, registry)
    index = open_index(store, state)
    with index.lock():
        if not index.exists():
            raise Blocked("not_prepared", "No issue workspace exists yet.", {}, ["Run ensure first."])
        record = owned(index, index.load(), store, state)
        current = active(record)
        if current is None:
            raise Blocked("not_prepared", "The issue workspace has no prepared generation.", {}, ["Run ensure first."])
        pending = unfinished(record)
        if pending and pending["request_id"] != request:
            raise Blocked("refresh_pending", "Another refresh request has not finished.",
                          {"generation": pending["number"], "request_id": pending["request_id"]},
                          ["Retry that request_id or keep the active generation."])
        if pending is None:
            from .investigation import unresolved
            run = state.get("runs", {}).get(state.get("active_run"))
            if run and run.get("status") != "pushed":
                raise Blocked("active_run", "An unfinished implementation run uses the active generation.",
                              {"run_id": run["run_id"], "status": run["status"]}, ["Finish or hold that run first."])
            if (state.get("pending") or {}).get("status") == "uncertain" or unresolved(state):
                raise Blocked("uncertain", "A publication must be reconciled before refreshing.", {},
                              ["Retry the original publication first."])
            try:
                tip = repository.git(index.root, "ls-remote", "--refs", record["remote"],
                                     "refs/heads/" + current["default_branch"]).split()
            except RelayError as exc:
                raise Blocked("remote", "The remote default branch could not be read.", {"code": exc.code},
                              ["Retry the same request later."]) from exc
            if tip and tip[0] == current["base_sha"]:
                current["head_sha"] = verify(index, record, current)
                index.save(record)
                mark(store, state, record)
                return ready(store, record, current, refreshed=False)
            pending = reserve(index, record, max(g["number"] for g in record["generations"]) + 1, request)
        previous = record["active_generation"]
        advance(index, record, pending, gh)
        current = active(record)
        current["head_sha"] = verify(index, record, current)
        index.save(record)
    mark(store, state, record)
    return ready(store, record, current, refreshed=True, previous_generation=previous)


def assess(store, state, data, gh, registry):
    """Record the host's judgment that exact documents still apply to the active generation."""
    from .publishing import snapshot
    from .artifacts import reference as document_reference
    documents, reason = data.get("documents"), data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise RelayError("input", "assess needs a nonempty reason.")
    if not isinstance(documents, dict) or not documents or set(documents) - set(KINDS):
        raise RelayError("input", "assess documents map intent/spec/plan/brief to exact references.")
    _, _, records, _ = snapshot(state, gh)
    for kind, ref in documents.items():
        if kind not in records or records[kind]["stale"] or document_reference(records[kind]) != ref:
            raise RelayError("stale", "Assessed document is not the current record: " + kind)
    index = open_index(store, state)
    with index.lock():
        if not index.exists():
            raise Blocked("not_prepared", "No issue workspace exists yet.", {}, ["Run ensure first."])
        record = owned(index, index.load(), store, state)
        current = active(record)
        if current is None:
            raise Blocked("not_prepared", "The issue workspace has no prepared generation.", {}, ["Run ensure first."])
        current["head_sha"] = verify(index, record, current)
        record["evaluations"].append({"workspace_id": record["workspace_id"], "generation": current["number"],
                                      "documents": copy.deepcopy(documents), "reason": reason, "at": now()})
        index.save(record)
    mark(store, state, record)
    return ready(store, record, current, assessed=sorted(documents))


def mark(store, state, record):
    state["workspace"] = {"workspace_id": record["workspace_id"]}
    store.save(state)


def dispatch(store, state, data, gh, registry):
    if state.get("stage") not in STAGES or not state.get("issue"):
        raise RelayError("input", "Issue workspaces serve an inspected issue stage other than open.")
    operation = data.get("operation", "ensure")
    handlers = {"ensure": ensure, "refresh": refresh, "assess": assess}
    if operation not in handlers:
        raise RelayError("input", "Unknown workspace operation.")
    try:
        return handlers[operation](store, state, data, gh, registry)
    except Blocked as exc:
        return exc.result


# Readers used by inspect, publishing, runs and investigation ---------------------------------

def summary(root, state):
    """What inspect reports; it never creates the workspace or its index."""
    if state.get("stage") not in STAGES or not state.get("issue"):
        return None
    try:
        index = IssueIndex(root, state["issue"])
        if not index.exists():
            return {"status": "none", "how": "workspace ensure"}
        record = index.load()
    except (RelayError, OSError, ValueError) as exc:
        return {"status": "unavailable", "message": str(exc)}
    current, pending = active(record), unfinished(record)
    result = {"status": "prepared" if current else "unfinished", "workspace_id": record["workspace_id"],
              "work_id": record["work_id"], "initial_base_sha": record["initial_base_sha"]}
    if current:
        result.update(generation=current["number"], path=current["path"], branch=current["branch"],
                      base_sha=current["base_sha"], head_sha=current["head_sha"])
    if pending:
        result["unfinished"] = {"generation": pending["number"], "status": pending["status"], "request_id": pending["request_id"]}
    return result


def current(store, state):
    """This work's active generation with its present HEAD, or None before any ensure."""
    if not state.get("workspace"):
        return None
    index = IssueIndex(store.root, state["issue"])
    if not index.exists():
        raise RelayError("stale", "The issue workspace record is missing; run workspace ensure.")
    record = index.load()
    gen = active(record)
    if record["workspace_id"] != state["workspace"]["workspace_id"] or record["work_id"] != state["work_id"] or gen is None:
        raise RelayError("stale", "The issue workspace changed owner or has no prepared generation.")
    try:
        head = verify(index, record, gen)
    except Blocked as exc:
        raise RelayError("stale", exc.result["message"]) from exc
    return {"record": record, "generation": gen, "reference": reference(record, gen, head)}


def verify_run(store, state, run):
    """A workspace run stays on its recorded generation's path and branch at every checkpoint."""
    ref = run["workspace"]
    index = IssueIndex(store.root, state["issue"])
    record = index.load() if index.exists() else None
    gen = generation(record, ref["generation"]) if record else None
    if (not record or record["workspace_id"] != ref["workspace_id"] or not gen or gen["status"] != "prepared"
            or Path(gen["path"]).resolve() != Path(run["path"]).resolve() or gen["branch"] != run["branch"]):
        raise RelayError("run", "Run workspace differs from its recorded issue workspace generation.")
    return gen
