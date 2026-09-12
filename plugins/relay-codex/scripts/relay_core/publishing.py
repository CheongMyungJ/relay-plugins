"""Freeze, compare and publish. Ambiguous writes are never blindly replayed."""
import datetime
import difflib
import json
import uuid
from pathlib import Path
from . import RelayError, baselines, watch
from . import next_step as steps
from .artifacts import collect, decode, digest, mentions_request, normalize, reference, render
from .state import read_json, write_json


def snapshot(state, gh, *, historical=False):
    if not state["issue"]:
        return {"body": ""}, [], {}, {}
    issue = gh.issue(state["issue"])
    comments = gh.comments(state["issue"])
    records, runs = collect(issue, comments, state.get("legacy"))
    for kind, target in ({} if historical else state.get("document_targets", {})).items():
        if kind not in records or records[kind]["target"] != target:
            raise RelayError("conflict", "A known document is missing or replaced: " + kind)
    return issue, comments, records, runs


def parents_for(stage, registry, records):
    if stage == "implement":
        return baselines.select(baselines.from_records(records))["parents"]
    result = {}
    for name in registry[stage]["requires"]:
        if name not in records or records[name]["stale"]:
            raise RelayError("stale", "A recorded, current " + name + " is required.")
        result[name] = reference(records[name])
    return result


def approval_hash(kind, title, body):
    if kind == "issue":
        return digest(json.dumps({"title": normalize(title), "body": normalize(body)},
                                 sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return digest(body)


def review_text(frozen):
    return ("# Issue title\n\n" + frozen["title"] + "\n\n# Issue body\n\n" + frozen["body"]
            if frozen["kind"] == "issue" else frozen["body"])


def prepare(store, state, candidate, gh, registry):
    pending = state.get("pending")
    if pending and pending["status"] == "uncertain":
        raise RelayError("uncertain", "Reconcile the pending publication before preparing another body.")
    issue, comments, records, runs = snapshot(state, gh, historical=state["stage"] == "implement")
    stage = state["stage"]
    kind = registry[stage]["artifact"]
    if kind == "issue" and state["issue"] is not None:
        raise RelayError("input", "This open work already created an issue; only reconcile its existing publication.")
    if kind != "issue" and state["issue"] is None:
        raise RelayError("input", "Document publication requires an existing issue.")
    run_id = candidate.get("run_id")
    if kind == "implementation":
        run = state.get("runs", {}).get(run_id)
        if not run:
            raise RelayError("run", "Register this authorized implementation run first.")
        basis = baselines.for_run(run)
        parents = run["parents"]  # Never rewrite the historical baseline as the issue evolves.
        previous = runs.get(run_id)
        known = state.get("implementation_targets", {}).get(run_id)
        if known and (not previous or previous["target"] != known):
            raise RelayError("conflict", "The known execution report is missing or replaced; reconcile its exact comment ID.")
    else:
        parents = parents_for(stage, registry, records)
        previous = records.get(kind)
        known = state.get("document_targets", {}).get(kind)
        if known and (not previous or previous["target"] != known):
            raise RelayError("conflict", "The known document is missing or replaced; reconcile its exact comment ID.")
    target = "issue" if kind == "issue" else previous["target"] if previous else None
    # Adoption of a legacy spec/plan requires an exact, user-confirmed binding from inspect.
    old = issue["body"] if target == "issue" else next((c["body"] for c in comments if str(c["id"]) == target), "")
    body = Path(candidate["body_file"]).read_text(encoding="utf-8-sig")
    title = normalize(candidate.get("title") or "") if kind == "issue" else None
    if kind == "issue" and (not title.strip() or not body.strip()):
        raise RelayError("input", "A new issue requires a reviewed, nonempty title and body.")
    from .investigation import evidence_refs
    evidence = evidence_refs(state, candidate.get("evidence_refs", []), gh)
    if "next_step" not in candidate:
        raise RelayError("input", "Submit the candidate's next_step; it is never filled in automatically.")
    suggestion = steps.validate(candidate["next_step"])
    steps.reserved(body)
    request = uuid.uuid4().hex
    version = previous["meta"]["version"] + 1 if previous else 1
    metadata = {"kind": kind, "work_id": state["work_id"], "version": version,
                "request_id": request, "parents": parents,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if run_id:
        metadata["run_id"] = run_id
    label = "실행 기록" if run_id else "확정"
    baseline = "\n".join(f"- {name}: {ref['target']} · v{ref['version']} · SHA-256 `{ref['hash']}`" for name, ref in parents.items())
    visible = body if kind == "issue" else f"{kind.title()} v{version} — {label}\n\n" + body
    if run_id:
        proof = basis["proof"]
        visible += f"\n\n실행 경로: {basis['kind']}\n검증 기준: {proof['target']} · v{proof['version']} · SHA-256 `{proof['hash']}`\n"
    if baseline:
        visible += "\n\n기준 문서:\n" + baseline + "\n"
    if run_id:
        # Include evidence from registered state instead of relying only on prose.
        visible += "\n\n실행 근거:\n```json\n" + json.dumps(state["runs"][run_id], ensure_ascii=False, indent=2) + "\n```\n"
    if evidence:
        visible += "\n\n조사 참고 근거 (문서 부모 아님):\n```json\n" + json.dumps(evidence, ensure_ascii=False, indent=2) + "\n```\n"
    visible += "\n\n" + steps.block(suggestion)
    rendered = render(visible, metadata, old, adopt=candidate.get("adopt", False), next_step=suggestion)
    if len(rendered) > 65000:
        raise RelayError("length", "Shorten and review the candidate; do not split it across comments.")
    frozen = {"request_id": request, "body": rendered, "hash": approval_hash(kind, title, rendered), "body_hash": digest(rendered), "kind": kind,
              "title": title, "target": target, "expected": digest(old) if old else None,
              "parents": parents, "run_id": run_id, "status": "review", "version": version, "evidence_refs": evidence,
              "next_step": suggestion}
    write_json(store.path / "candidate.json", frozen)
    review = review_text(frozen)
    (store.path / "review.md").write_text(review, encoding="utf-8", newline="\n")
    (store.path / "change.diff").write_text("".join(difflib.unified_diff(old.splitlines(True), review.splitlines(True), fromfile="recorded", tofile="candidate")), encoding="utf-8")
    state["pending"] = frozen
    state.setdefault("document_targets", {}).update({name: record["target"] for name, record in records.items()})
    state.setdefault("implementation_targets", {}).update({name: record["target"] for name, record in runs.items()})
    state["status"] = "review"
    store.save(state)
    return {"request_id": request, "hash": frozen["hash"], "review": str(store.path / "review.md"),
            "diff": str(store.path / "change.diff"), "version": version, "next_step": suggestion}


def matching_request(items, request, expected, title=None, *, issue_search=False):
    found = []
    for item in items:
        if issue_search and not mentions_request(item["body"], request):
            continue
        record = decode(item["body"], request_id=request)
        if issue_search and record is None:
            raise RelayError("artifact", "Issue mentions this request but has no matching valid record.")
        if record and record["meta"]["request_id"] == request:
            if digest(item["body"]) != expected or (title is not None and normalize(item.get("title", "")) != title):
                raise RelayError("conflict", "Request ID exists with different content.")
            found.append(item)
    if len(found) > 1:
        raise RelayError("conflict", "Duplicate publication request found.")
    return found[0] if found else None


def publish(store, state, authorization, gh, registry):
    frozen = state.get("pending")
    if not frozen:
        raise RelayError("approval", "Prepare and review a candidate first.")
    if authorization.get("request_id") != frozen["request_id"] or authorization.get("hash") != frozen["hash"]:
        raise RelayError("approval", "Approval does not match the current candidate.")
    if frozen["run_id"]:
        if authorization.get("run_id") != frozen["run_id"] or not authorization.get("execution_authorized"):
            raise RelayError("approval", "Implementation record must match the authorized execution.")
    elif authorization.get("approved") is not True or not authorization.get("user"):
        raise RelayError("approval", "Explicit document approval is required.")
    # Detect edits after review. Never publish from a newly edited file using old approval.
    if (normalize((store.path / "review.md").read_text(encoding="utf-8")) != review_text(frozen)
            or approval_hash(frozen["kind"], frozen["title"], frozen["body"]) != frozen["hash"]
            or digest(frozen["body"]) != frozen["body_hash"]):
        raise RelayError("approval", "Review file changed; prepare and review again.")
    if read_json(store.path / "candidate.json") != frozen:
        # Only the status stored in state changes during retries.
        disk = read_json(store.path / "candidate.json")
        if {k: v for k, v in disk.items() if k != "status"} != {k: v for k, v in frozen.items() if k != "status"}:
            raise RelayError("approval", "Frozen candidate changed.")
    number, target = state["issue"], frozen["target"]
    uncertain = frozen["status"] == "uncertain"
    # Reconcile before checking parents: a write may already have succeeded.
    if target is not None and number is not None:
        current = gh.get_target(number, target)  # Deleted/forbidden targets remain errors.
        result = matching_request([current], frozen["request_id"], frozen["body_hash"], frozen["title"])
    else:
        items = gh.issues() if number is None else gh.comments(number)
        result = matching_request(items, frozen["request_id"], frozen["body_hash"], frozen["title"],
                                  issue_search=number is None)
        current = None
    if result is None:
        if frozen["kind"] == "issue" and number is not None:
            raise RelayError("conflict", "The created issue no longer matches this request; open never edits or replaces it.")
        if uncertain and (target is None or number is None):
            raise RelayError("uncertain", "No result found yet; creation is not replayed after an ambiguous response.")
        _, _, records, remote_runs = snapshot(state, gh, historical=bool(frozen["run_id"]))
        if target is None and frozen["run_id"] in remote_runs:
            raise RelayError("conflict", "An execution report was created after preparation; reconcile its existing comment.")
        if target is None and frozen["kind"] in records:
            raise RelayError("conflict", "A document of this kind was created after preparation; review its existing comment before publishing.")
        if not frozen["run_id"] and parents_for(state["stage"], registry, records) != frozen["parents"]:
            raise RelayError("stale", "Parent documents changed after review.")
        if current is not None and digest(current["body"]) != frozen["expected"]:
            raise RelayError("conflict", "The remote document changed after preparation.")
        from .investigation import evidence_refs
        evidence_refs(state, frozen.get("evidence_refs", []), gh)
        state["status"] = "publication_uncertain"
        frozen["status"] = "uncertain"
        state["authorization"] = {k: authorization[k] for k in ("user", "approved", "execution_authorized", "run_id", "hash", "request_id") if k in authorization}
        store.save(state)
        try:
            result = gh.write(number, target, frozen["body"], frozen["title"])
        except RelayError as exc:
            if exc.code == "github_rejected":
                frozen["status"] = "review"
                state["status"] = "record_pending"
                store.save(state)
            raise
    if target == "issue" and number is None:
        number = result["number"]
        state["issue"] = number
    actual_target = "issue" if target == "issue" else str(result["id"])
    # Persist identity before read-back, so its failure can recover the exact object.
    frozen["target"] = actual_target
    write_json(store.path / "candidate.json", frozen)
    store.save(state)
    verified = gh.get_target(number, actual_target)
    if (digest(verified["body"]) != frozen["body_hash"]
            or (frozen["kind"] == "issue" and normalize(verified.get("title", "")) != frozen["title"])):
        raise RelayError("conflict", "Read-back differs; publication requires inspection.")
    published = decode(verified["body"], actual_target, request_id=frozen["request_id"])
    if not published or published["next_step"] != frozen["next_step"]:
        raise RelayError("conflict", "Published next step differs from the approved candidate.")
    frozen["status"] = "recorded"
    state["status"] = "recorded"
    state["last_record"] = {"url": verified["html_url"], "target": actual_target, "hash": frozen["hash"],
                            "version": frozen["version"], "next_step": published["next_step"]}
    if frozen["kind"] in ("intent", "spec", "plan", "brief"):
        state.setdefault("document_targets", {})[frozen["kind"]] = actual_target
    if frozen["run_id"]:
        state.setdefault("implementation_targets", {})[frozen["run_id"]] = actual_target
    state.get("legacy", {}).pop(actual_target, None)
    # Marking follows the verified publication and never undoes it. The record lives on
    # the frozen request, so a retry of the same request only completes an unapplied mark.
    record = watch.mark(gh, number, state.get("options", {}).get("watch"), frozen.get("watch"))
    if record is not None:
        frozen["watch"] = state["watch"] = state["last_record"]["watch"] = record
    write_json(store.path / "candidate.json", frozen)
    store.save(state)
    return state["last_record"]
