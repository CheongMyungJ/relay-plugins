"""Independent reviewer/author workflow; host reasoning and edits stay outside."""
import copy
import re
import uuid
from pathlib import Path
from . import RelayError, invocation, next_step as steps, repository
from .github import GitHub
from .state import ReviewStore, write_json
from . import review_snapshot as snapshots, review_operations as posts, review_git

SEVERITIES = ("blocking", "major", "minor", "info")
ANSWER = ("필요", "부분", "완료", "불확실", "불필요")
CODE = ("필요", "부분", "반영됨", "불확실", "불필요")


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise RelayError("input", "Missing " + label)
    return value


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value):
        raise RelayError("input", "Invalid local item/unit ID.")
    return value


def select(data, parsed, repo, gh):
    number = parsed["pr"]
    if data.get("pr") is not None:
        if number is not None and data["pr"] != number:
            raise RelayError("input", "Conflicting PR selection.")
        require_text(data.get("selection_reason"), "selection_reason")
        number = data["pr"]
    if number is not None:
        if type(number) is not int or number <= 0:
            raise RelayError("input", "PR must be a positive integer.")
        return number, data.get("selection_reason", "explicit PR number"), []
    branch = repository.git(repo["root"], "branch", "--show-current")
    if not branch:
        raise RelayError("selection", "Detached HEAD requires an explicit PR number.")
    candidates = [p for p in gh.pulls() if p["head"]["ref"] == branch and
                  (p["head"].get("repo") or {}).get("full_name", "").lower() == repo["repo"].lower()]
    if len(candidates) != 1:
        return None, "select an explicit PR", [{"pr": p["number"], "url": p["html_url"]} for p in candidates]
    return candidates[0]["number"], "unique open PR matching current source repository/branch", []


def validate_items(request, data):
    items = copy.deepcopy(data.get("items"))
    if not isinstance(items, list):
        raise RelayError("input", "items must be a list.")
    sources = {c["key"]: c for c in request["snapshot"]["comments"]}
    previous = {i["id"]: i for candidate in request.get("candidate_history", []) if candidate for i in candidate["items"]}
    previous.update({i["id"]: i for i in request.get("candidate", {}).get("items", [])})
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise RelayError("input", "Each item must be an object.")
        key = identifier(item.get("id"))
        if key in seen:
            raise RelayError("input", "Duplicate item ID.")
        seen.add(key)
        require_text(item.get("evidence"), "item evidence")
        require_text(item.get("resolution"), "resolution separate from original severity")
        if request["mode"] == "reviewer":
            if item.get("severity") not in SEVERITIES:
                raise RelayError("input", "Unknown severity; use blocking/major/minor/info.")
            for field in ("condition", "impact", "location", "suggestion", "severity_reason"):
                require_text(item.get(field), field)
            item["original_severity"] = previous.get(key, {}).get("original_severity", item["severity"])
        else:
            refs = item.get("sources")
            if not isinstance(refs, list) or not refs or any(s not in sources or not sources[s]["body"].strip() for s in refs):
                raise RelayError("input", "Author items require collected nonempty sources.")
            if not item.get("categories") or not set(item["categories"]) <= {"question", "suggestion", "change_request"}:
                raise RelayError("input", "Invalid author categories.")
            if item.get("answer_status") not in ANSWER or item.get("code_status") not in CODE:
                raise RelayError("input", "Invalid independent answer/code status.")
            for field in ("answer_evidence", "code_evidence", "decision_reason"):
                require_text(item.get(field), field)
            if item.get("acceptance") not in ("accept", "decline", "explain", "defer"):
                raise RelayError("input", "Invalid response decision.")
            if item.get("excluded"):
                if item["answer_status"] not in ("완료", "불필요") or item["code_status"] not in ("반영됨", "불필요"):
                    raise RelayError("input", "Exclude only when neither an answer nor a code change remains.")
                require_text(item.get("exclusion_reason"), "exclusion reason")
    if request["mode"] == "reviewer":
        items.sort(key=lambda i: (SEVERITIES.index(i["severity"]), i["id"]))
    return items


def validate_operations(request, data, items):
    units = copy.deepcopy(data.get("operations"))
    if not isinstance(units, list):
        raise RelayError("input", "operations must be a list.")
    by_id = {i["id"]: i for i in items}
    sources = {c["key"]: c for c in request["snapshot"]["comments"]}
    seen, targets = set(), set()
    used_ids = {u["unit_id"] for u in request.get("completed_operations", []) + request.get("retired_operations", [])}
    for unit in units:
        if not isinstance(unit, dict):
            raise RelayError("input", "Each operation must be an object.")
        key = identifier(unit.get("unit_id"))
        if key in seen or key in used_ids:
            raise RelayError("input", "Duplicate unit ID.")
        seen.add(key)
        body = require_text(unit.get("body"), "exact posting body")
        if len(body) > 60000 or "<!-- relay:review" in body:
            raise RelayError("input", "Body too long or contains reserved markers.")
        # The unit body is what actually gets posted, so it needs the same guard the draft has.
        steps.reserved(body)
        ids = unit.get("item_ids")
        if not isinstance(ids, list) or len(ids) != len(set(ids)) or any(i not in by_id or by_id[i].get("excluded") for i in ids):
            raise RelayError("input", "Invalid posting item scope.")
        kind = unit.get("kind")
        if kind not in ("issue", "inline") or (kind == "issue" and unit.get("root_id") is not None):
            raise RelayError("input", "Only general comments and root replies are supported.")
        target = (kind, unit.get("root_id"))
        if target in targets:
            raise RelayError("input", "Use one unit per root and one general comment.")
        targets.add(target)
        if request["mode"] == "reviewer":
            if kind != "issue" or len(units) != 1:
                raise RelayError("input", "Reviewer publishes one general PR comment.")
        else:
            if not ids:
                raise RelayError("input", "Do not post an empty author completion comment.")
            refs = [sources[s] for i in ids for s in by_id[i]["sources"]]
            relevant = [s for s in refs if (s["kind"] == "inline" and kind == "inline" and s.get("root_id") == unit.get("root_id")) or
                        (s["kind"] != "inline" and kind == "issue")]
            if not relevant or any(s["html_url"] not in body for s in relevant):
                raise RelayError("input", "Response must link collected sources at the correct destination.")
            if kind == "inline":
                root = sources.get(f"inline:{unit.get('root_id')}")
                if not root or root.get("in_reply_to_id") or root.get("root_id") != root["id"]:
                    raise RelayError("input", "Reply target must be a collected top-level root.")
    if request["mode"] == "author":
        for item in items:
            if item.get("excluded") or item.get("acceptance") == "defer":
                continue
            for source_key in item["sources"]:
                source = sources[source_key]
                if not any(item["id"] in u["item_ids"] and source["html_url"] in u["body"] and
                           ((source["kind"] == "inline" and u["kind"] == "inline" and u.get("root_id") == source.get("root_id")) or
                            (source["kind"] != "inline" and u["kind"] == "issue")) for u in units):
                    raise RelayError("input", "Active author source lacks an exact response at its destination.")
    return units


def prepare(store, request, data):
    if request.get("decision") or any(u["status"] != "recorded" for u in posts.operations(store, request)):
        raise RelayError("conflict", "Execution already selected; resume its recorded stages before a new candidate.")
    if data.get("snapshot_hash") != request["snapshot"]["hash"]:
        raise RelayError("stale", "Prepare against the current snapshot hash.")
    draft = Path(data["draft_file"]).read_text(encoding="utf-8-sig")
    require_text(draft, "complete draft")
    steps.reserved(draft)
    if "next_step" not in data:
        raise RelayError("input", "Submit the candidate's next_step; it is never filled in automatically.")
    suggestion = steps.validate(data["next_step"])
    items = validate_items(request, data)
    units = validate_operations(request, data, items)
    scope = copy.deepcopy(data.get("code_scope", []))
    if not isinstance(scope, list):
        raise RelayError("input", "code_scope must be a list.")
    for entry in scope:
        if not isinstance(entry, dict) or not isinstance(entry.get("paths"), list):
            raise RelayError("input", "Code scope entries require a list of exact paths.")
        if entry.get("item_id") not in {i["id"] for i in items if not i.get("excluded")} or not entry.get("paths"):
            raise RelayError("input", "Code scope must name active items and exact paths.")
        for path in entry["paths"]:
            if not isinstance(path, str) or not path or path.startswith(("/", "\\")) or "\\" in path or ":" in path or ".." in path.split("/") or ".git" in path.split("/"):
                raise RelayError("input", "Use repository-relative exact code paths.")
    checks = data.get("verification_scope", [])
    if not isinstance(checks, list) or any(not isinstance(c, str) or not c.strip() for c in checks):
        raise RelayError("input", "verification_scope must list actual required commands.")
    candidate = {"run_id": request["run_id"], "repository": request["repository"], "pr": request["pr"], "mode": request["mode"],
                 "code_evidence_hash": snapshots.hashed(request["snapshot"].get("code")),
                 "snapshot_hash": data["snapshot_hash"], "revision": request.get("candidate", {}).get("revision", 0) + 1,
                 "draft": draft, "items": items, "operations": units, "code_scope": scope, "verification_scope": checks,
                 "next_step": suggestion}
    candidate["hash"] = snapshots.hashed(candidate)
    request.update(candidate=candidate, status="prepared")
    store.save(request)
    path = store.request_path(request["run_id"])
    # Include exact structured units even when the host draft omitted one.
    review = draft + "\n\n## Frozen execution candidate\n\n" + "\n".join(
        f"- {i['id']}: {i.get('severity', i.get('answer_status'))}; {i['resolution']}" for i in items)
    for unit in units:
        review += f"\n\n### {unit['unit_id']} ({unit['kind']}, root {unit.get('root_id')})\n\n" + unit["body"]
    review += "\n\n" + steps.block(suggestion)
    review += f"\nCandidate: `{candidate['hash']}`\n"
    (path / "draft.md").write_text(review, encoding="utf-8")
    return {"run_id": request["run_id"], "hash": candidate["hash"], "revision": candidate["revision"],
            "draft_file": str(path / "draft.md"), "candidate": candidate, "next_step": suggestion}


def fresh(repo, request, gh, units=()):
    current = snapshots.snapshot(repo, gh, request["pr"], request["run_id"], include_git=False)
    snapshots.check(request["snapshot"], current, units, request.get("application"))
    return current


def reassess(store, request, data, repo, gh):
    """A host-recorded impact judgment, never an automatic stale bypass."""
    current = snapshots.snapshot(repo, gh, request["pr"], request["run_id"])
    if data.get("current_snapshot_hash") != current["hash"]:
        raise RelayError("stale", "Read the current snapshot and bind its exact hash before reassessment.")
    require_text(data.get("reason"), "reassessment evidence and impact")
    units = posts.recover(store, request, gh)
    # Published units are expected changes, but edits/deletions remain conflicts.
    for unit in units:
        if unit["status"] == "recorded":
            current["comments"] = [c for c in current["comments"] if not (c["kind"] == unit["kind"] and c["id"] == unit["id"])]
    previous = request["snapshot"]
    if data.get("impact") == "unrelated":
        # Code changes always require renewed code evidence, not an unrelated assertion.
        expected_pull = copy.deepcopy(previous["pull"])
        app = request.get("application")
        if app and app.get("status") == "pushed":
            expected_pull["head"]["sha"] = app["remote_sha"]
        if any(current["pull"][key] != expected_pull[key] for key in ("head", "base", "state", "merged")):
            raise RelayError("stale", "Code or PR state changed; refresh the candidate and verification.")
        current["pull"]["head"]["sha"] = previous["pull"]["head"]["sha"]
    elif data.get("impact") == "revise":
        app = request.get("application")
        if app and app["status"] != "pushed":
            raise RelayError("conflict", "Reconcile local application/push before revising its scope.")
        if any(u["status"] not in ("pending", "failed", "recorded") for u in units):
            raise RelayError("uncertain", "Reconcile uncertain operations before revising remaining work.")
        if app:
            request.setdefault("application_history", []).append(request.pop("application"))
        request.setdefault("candidate_history", []).append(request.get("candidate"))
        request.setdefault("decision_history", []).append(request.get("decision"))
        request["completed_operations"] = [u for u in units if u["status"] == "recorded"]
        request.setdefault("retired_operations", []).extend(u for u in units if u["status"] != "recorded")
        request.pop("decision", None)
        request.pop("candidate", None)
        request.pop("results_shown", None)
        posts.save(store, request, request["completed_operations"])
        request["status"] = "draft"
    else:
        raise RelayError("input", "Reassessment impact must be unrelated or revise.")
    current["hash"] = snapshots.comparison_hash(current)
    request.setdefault("reassessments", []).append({"previous_hash": previous["hash"], "current_hash": current["hash"],
                                                  "impact": data["impact"], "reason": data["reason"]})
    request["snapshot"] = current
    store.save(request)
    write_json(store.request_path(request["run_id"]) / "snapshot.json", current)


def result(store, request):
    units = posts.operations(store, request)
    items = request.get("candidate", {}).get("items", [])
    value = {"run_id": request["run_id"], "status": request["status"], "pr": request["pr"], "mode": request["mode"],
             "url": request["snapshot"]["pull"]["html_url"], "baseline_sha": request["snapshot"]["pull"]["head"]["sha"],
             "application": request.get("application"), "operations": units,
             "application_history": request.get("application_history", []), "candidate_history": request.get("candidate_history", []),
             "excluded": [i for i in items if i.get("excluded")], "unresolved": [i for i in items if i.get("resolution") != "resolved"],
             "warnings": request["snapshot"]["warnings"], "error": request.get("error")}
    write_json(store.request_path(request["run_id"]) / "result.json", value)
    return value


def execution_complete(request, units):
    decision = request.get("decision")
    if not decision or decision["action"] == "hold":
        return False
    expected = {u["unit_id"] for u in request["candidate"]["operations"]
                if set(u["item_ids"]) <= set(decision["selected"])}
    recorded = {u["unit_id"] for u in units if u["status"] == "recorded"}
    # Retained successes from an older candidate do not complete new work.
    if not expected <= recorded or any(u["status"] != "recorded" for u in units):
        return False
    if decision["action"] == "apply":
        app = request.get("application") or {}
        return bool(app.get("commit") and app.get("status") == "pushed" and app.get("remote_sha") == app["commit"])
    return True


def execute(store, request, data, repo, gh):
    candidate = request.get("candidate", {})
    if not candidate or data.get("hash") != candidate["hash"]:
        raise RelayError("approval", "Select the exact prepared candidate hash.")
    if snapshots.hashed({k: v for k, v in candidate.items() if k != "hash"}) != candidate["hash"]:
        raise RelayError("conflict", "Candidate content changed after preparation.")
    decision = data.get("decision")
    if not isinstance(decision, dict) or decision.get("action") not in ("post", "apply", "hold"):
        raise RelayError("approval", "Record the user's post/apply/hold decision.")
    require_text(decision.get("user"), "decision user")
    require_text(decision.get("record"), "user decision scope")
    selected = decision.get("selected")
    if not isinstance(selected, list) or len(set(selected)) != len(selected) or not set(selected) <= {i["id"] for i in candidate["items"] if not i.get("excluded")}:
        raise RelayError("approval", "Select only candidate item IDs.")
    if request.get("decision") and request["decision"] != decision:
        raise RelayError("approval", "Decision changed; reconcile the existing execution.")
    if decision["action"] == "hold":
        request["status"] = "held"
        store.save(request)
        return result(store, request)
    units = posts.recover(store, request, gh)
    fresh(repo, request, gh, units)
    if request["status"] == "recorded" and execution_complete(request, units):
        return result(store, request)
    # Mixed units must be rejected before code, push, or any POST.
    for unit in candidate["operations"]:
        if set(unit["item_ids"]) & set(selected) and not set(unit["item_ids"]) <= set(selected):
            raise RelayError("approval", "Prepare exact posting units for the selected subset.")
    request.update(decision=decision, status="authorized")
    store.save(request)
    if decision["action"] == "apply":
        if not selected or not candidate["verification_scope"] or not set(selected) <= {s["item_id"] for s in candidate["code_scope"]}:
            raise RelayError("verification", "Selected code items need explicit paths and required checks.")
        if "application" not in request:
            if not data.get("worktree"):
                request["status"] = "applying"
                store.save(request)
                return {**result(store, request), "head_sha": request["snapshot"]["pull"]["head"]["sha"], "next": "Create and register a separate worktree at head_sha before editing."}
            request["application"] = review_git.register(repo, request, data["worktree"])
            store.save(request)
            return result(store, request)
        if request["application"]["status"] == "applying":
            if "tests" not in data:
                return result(store, request)
            request["application"] = review_git.verify(repo, request, data)
            store.save(request)
        fresh(repo, request, gh, units)
        review_git.push(repo, request, gh, lambda: store.save(request))
    units = posts.freeze(store, request, gh)
    # Result substitution is deterministic and remains bound to the user's scope.
    if decision["action"] == "apply" and not request.get("results_shown"):
        request["status"] = "verified"
        store.save(request)
        return {**result(store, request), "next": "Show exact rendered operation bodies, then execute with results_shown=true."}
    if data.get("results_shown") is True:
        request["results_shown"] = True
        store.save(request)
    request["status"] = "publishing"
    store.save(request)
    posts.publish(store, request, gh, lambda ops: fresh(repo, request, gh, ops))
    fresh(repo, request, gh, posts.operations(store, request))
    request["status"] = "recorded"
    request.pop("error", None)
    store.save(request)
    return result(store, request)


def dispatch(data, registry, gh=None, repo=None):
    action = data.get("action")
    if action not in ("inspect", "prepare", "execute", "resume"):
        raise RelayError("input", "Unknown review action.")
    parsed = invocation.parse("review", data.get("raw", ""), registry) if action == "inspect" else None
    repo = repo or repository.inspect(data.get("cwd", str(Path.cwd())))
    gh = gh or GitHub(repo["repo"], host=repo.get("host", "github.com"))
    store = ReviewStore(repo["root"])
    if action == "inspect":
        number, reason, candidates = select(data, parsed, repo, gh)
        if number is None:
            return {"status": "selection_required", "candidates": candidates, "reason": reason}
    else:
        request = store.load(data["run_id"])
        if not repository.same_repository(request["repository"], repo) or data.get("pr", request["pr"]) != request["pr"] or data.get("mode", request["mode"]) != request["mode"]:
            raise RelayError("state", "Run belongs to another repository, PR or mode.")
        number = request["pr"]
    with store.pr_lock(repo, number):
        if action == "inspect":
            pending = [r for r in store.records(repo, number) if r["status"] not in ("recorded", "held")]
            if pending:
                return {"status": "resume_required", "runs": [{"run_id": r["run_id"], "mode": r["mode"], "status": r["status"]} for r in pending]}
            run_id = uuid.uuid4().hex
            snapshot = snapshots.snapshot(repo, gh, number, run_id)
            request = {"schema": 1, "run_id": run_id, "repository": repo, "pr": number, "mode": parsed["mode"], "input": parsed,
                       "initial_checkout": repo["root"], "selection_reason": reason, "snapshot": snapshot, "status": "draft"}
            store.save(request)
            write_json(store.request_path(run_id) / "snapshot.json", snapshot)
            return {"run_id": run_id, "work_path": str(store.request_path(run_id)), "mode": request["mode"], "snapshot": snapshot, "selection_reason": reason}
        request = store.load(data["run_id"])
        try:
            if action == "prepare":
                current = snapshots.snapshot(repo, gh, number, request["run_id"], include_git=False)
                for unit in posts.operations(store, request):
                    if unit["status"] == "recorded":
                        current["comments"] = [c for c in current["comments"] if not (c["kind"] == unit["kind"] and c["id"] == unit["id"])]
                current["hash"] = snapshots.comparison_hash(current)
                if current["hash"] != request["snapshot"]["hash"]:
                    raise RelayError("stale", "Evidence changed; resume and reassess before preparing.")
                return prepare(store, request, data)
            if action == "execute":
                # Re-entry after showing rendered results uses the same decision and hash.
                if data.get("results_shown") is True and posts.operations(store, request) and request.get("application", {}).get("status") == "pushed":
                    request["results_shown"] = True
                return execute(store, request, data, repo, gh)
            if data.get("reassessment"):
                reassess(store, request, data["reassessment"], repo, gh)
            units = posts.recover(store, request, gh)
            app = request.get("application")
            if app:
                app["observed_head"] = repository.git(app["path"], "rev-parse", "HEAD")
                app["observed_status"] = repository.git(app["path"], "status", "--porcelain")
                source = request["snapshot"]["pull"]["head"]
                remote = review_git.tip(repo["root"], review_git.destination(repo, source, push=True), source["ref"])
                if app.get("commit") == remote:
                    app.update(status="pushed", remote_sha=remote)
                    store.save(request)
            current = snapshots.snapshot(repo, gh, number, request["run_id"], include_git=False)
            try:
                snapshots.check(request["snapshot"], current, units, request.get("application"))
            except RelayError as exc:
                request["error"] = {"code": exc.code, "message": str(exc)}
                if request["status"] != "recorded":
                    request["status"] = "stale" if exc.code == "stale" else request["status"]
                store.save(request)
                return {**result(store, request), "current_snapshot": current, "candidate": request.get("candidate"), "snapshot": request["snapshot"]}
            if execution_complete(request, units):
                request["status"] = "recorded"
            store.save(request)
            return {**result(store, request), "candidate": request.get("candidate"), "snapshot": request["snapshot"]}
        except RelayError as exc:
            request["error"] = {"code": exc.code, "message": str(exc)}
            if request["status"] != "recorded":
                request["status"] = exc.code if exc.code in ("stale", "uncertain", "conflict") else "failed"
            store.save(request)
            result(store, request)
            raise
