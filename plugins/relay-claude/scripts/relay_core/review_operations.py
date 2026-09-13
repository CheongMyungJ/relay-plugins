"""Write-ahead posting units. Resume reconciles; uncertain POSTs never repeat."""
from . import RelayError
from . import next_step as steps
from .artifacts import digest
from .state import write_json, read_json


def operations(store, request):
    path = store.request_path(request["run_id"]) / "operations.json"
    return read_json(path) if path.exists() else []


def save(store, request, units):
    write_json(store.request_path(request["run_id"]) / "operations.json", units)


def freeze(store, request, gh):
    existing = operations(store, request)
    units = existing
    existing_ids = {u["unit_id"] for u in units}
    for unit in request["candidate"]["operations"]:
        if unit["unit_id"] in existing_ids:
            continue
        if not set(unit["item_ids"]) <= set(request["decision"]["selected"]):
            if set(unit["item_ids"]) & set(request["decision"]["selected"]):
                raise RelayError("approval", "A posting unit mixes selected and unselected items; prepare a scoped candidate.")
            continue
        marker = f"<!-- relay:review {request['run_id']} {unit['unit_id']} -->"
        body = unit["body"]
        app = request.get("application")
        if app:
            body = body.replace("{{commit}}", app["commit"]).replace("{{verification}}", "\n".join(
                f"- `{t['command']}`: exit {t['exit_code']} — {t['evidence']}" for t in app["tests"]))
        if "{{commit}}" in body or "{{verification}}" in body:
            raise RelayError("verification", "Result placeholders need verified code application.")
        # Render the candidate's suggestion after substitution, so no result text reaches it.
        body += "\n\n" + steps.rendered(request["candidate"]["next_step"])
        units.append({**unit, "body": body + "\n\n" + marker, "marker": marker, "author": gh.viewer(),
                      "expected_head": app["commit"] if app else request["snapshot"]["pull"]["head"]["sha"], "status": "pending"})
        units[-1]["body_hash"] = digest(units[-1]["body"])
    save(store, request, units)
    return units


def validate_remote(request, unit, obj):
    url = obj.get("html_url", "")
    if (obj.get("body") != unit["body"] or obj.get("user", {}).get("login") != unit["author"] or
            not url.startswith(request["snapshot"]["pull"]["html_url"] + "#")):
        raise RelayError("conflict", "Published body, author, URL or target differs.")
    if unit["kind"] == "inline" and obj.get("in_reply_to_id") != unit["root_id"]:
        raise RelayError("conflict", "Reply is not attached to the frozen root.")
    if not isinstance(obj.get("id"), int) or obj["id"] <= 0:
        raise RelayError("conflict", "Invalid response comment ID.")


def recover(store, request, gh):
    units = operations(store, request)
    for unit in units:
        if (digest(unit["body"]) != unit["body_hash"] or
                unit["marker"] != f"<!-- relay:review {request['run_id']} {unit['unit_id']} -->" or
                not unit["body"].endswith(unit["marker"])):
            raise RelayError("conflict", "Frozen operation content or identity changed.")
        if unit["status"] in ("pending", "failed"):
            continue
        try:
            if unit.get("id"):
                obj = gh.review_comment(unit["id"]) if unit["kind"] == "inline" else gh.comment(unit["id"])
                validate_remote(request, unit, obj)
            else:
                items = gh.review_comments(request["pr"]) if unit["kind"] == "inline" else gh.comments(request["pr"])
                matches = [c for c in items if unit["marker"] in (c.get("body") or "")]
                if not matches and unit["status"] == "rejected":
                    unit["status"] = "failed"
                    save(store, request, units)
                    continue
                if len(matches) != 1:
                    raise RelayError("uncertain" if not matches else "conflict", "POST marker has zero or multiple matches; no automatic resend.")
                if unit["status"] == "rejected":
                    # Remote evidence of a POST overrides the earlier rejection.
                    unit["status"] = "uncertain"
                    save(store, request, units)
                obj = matches[0]
                validate_remote(request, unit, obj)
                # Verify exact ID, rather than accepting enumeration alone.
                obj = gh.review_comment(obj["id"]) if unit["kind"] == "inline" else gh.comment(obj["id"])
                validate_remote(request, unit, obj)
            unit.update(status="recorded", id=obj["id"], url=obj["html_url"])
            unit.pop("error", None)
            save(store, request, units)
        except RelayError as exc:
            # Confirmed history is immutable, even when the remote comment later changes.
            # A failed read cannot turn a definite POST rejection into response loss.
            # Conflicting remote evidence still needs reconciliation before retrying.
            if unit["status"] != "recorded" and (unit["status"] != "rejected" or exc.code == "conflict"):
                unit["status"] = "conflict" if exc.code == "conflict" else "uncertain"
            unit["error"] = str(exc)
            save(store, request, units)
            raise
    return units


def publish(store, request, gh, before_each):
    units = recover(store, request, gh)
    for unit in units:
        if unit["status"] == "recorded":
            continue
        status, suggestion = steps.read(unit["body"], unit["marker"])
        if status != steps.RECORDED:
            raise RelayError("approval", "Legacy posting unit; prepare and review a new candidate.")
        steps.require_allowed("review", suggestion)
        before_each(units)
        if gh.viewer() != unit["author"]:
            raise RelayError("conflict", "Authenticated author changed.")
        if unit["kind"] == "inline":
            root = gh.review_comment(unit["root_id"])
            if root.get("in_reply_to_id") or not root.get("html_url", "").startswith(request["snapshot"]["pull"]["html_url"] + "#"):
                raise RelayError("conflict", "Reply target is not the original top-level comment.")
        unit["status"] = "writing"
        save(store, request, units)
        try:
            obj = gh.reply(request["pr"], unit["root_id"], unit["body"]) if unit["kind"] == "inline" else gh.write(request["pr"], None, unit["body"])
            # Persist response ID before verification, including malformed responses.
            if isinstance(obj.get("id"), int):
                unit["id"] = obj["id"]
            save(store, request, units)
            validate_remote(request, unit, obj)
            exact = gh.review_comment(obj["id"]) if unit["kind"] == "inline" else gh.comment(obj["id"])
            validate_remote(request, unit, exact)
            unit.update(status="recorded", id=exact["id"], url=exact["html_url"])
            save(store, request, units)
        except RelayError as exc:
            unit["status"] = "rejected" if exc.code == "github_rejected" and not unit.get("id") else "uncertain"
            unit["error"] = str(exc)
            save(store, request, units)
            # Reconcile even definite rejection before declaring a retryable failure.
            recovered = recover(store, request, gh)
            if next(u for u in recovered if u["unit_id"] == unit["unit_id"])["status"] != "recorded":
                raise
            # Keep the current iteration's unit objects synchronized after recovery.
            for old, new in zip(units, recovered):
                old.update(new)
    return units
