"""The R5 gate order for a change set.

candidates and complete classification -> operations and final caps -> format -> path
existence at the fixed SHA -> source existence/state -> the two attachment lines ->
path-only reason limit -> scope reconfirmation complete. Failures are errors with the
codes kb / source / budget; an incomplete classification returns the candidate page and
an over-cap result returns `capacity_resolution_required` with the files' active
entries instead of failing, so `check`, `prepare` and `checkpoint` can be iterated.
"""
from .. import RelayError
from . import entries as model
from . import changes as changeset, layout, lookup, reconfirm as reconfirmation, sources as sourcing


def fail(message, code="kb"):
    raise RelayError(code, message)


def candidates_for(kb, value):
    """Active entries overlapping the candidate's paths or terms, excluding glossary terms."""
    query = lookup.normalize_query({"paths": value["paths"], "terms": value["terms"], "limit": lookup.MAX_LIMIT})
    found, _, _, _ = lookup.candidates(kb, query)
    return [(entry, reasons) for entry, reasons, _ in found if entry["status"] == "active"]


def classification(kb, changes):
    """Return (pending page or None, errors) for the create ops' candidate sets."""
    classified = {(c["op_id"], c["existing_id"]): c for c in changes["classified"]}
    pending = []
    for op in changes["ops"]:
        if op["op"] != "create":
            continue
        value = model.validate(op["entry"])
        found = candidates_for(kb, value)
        missing = [(entry, reasons) for entry, reasons in found if (op["op_id"], entry["id"]) not in classified]
        if missing:
            pending.append({"op_id": op["op_id"], "rule": value["rule"],
                            "candidates": [lookup.summary_of(entry, reasons, kb) for entry, reasons in missing]})
            continue
        for entry, _ in found:
            verdict = classified[(op["op_id"], entry["id"])]["verdict"]
            if verdict == "same":
                fail(f"{op['op_id']} duplicates {entry['id']}; submit update (add sources) instead of create.")
            if verdict == "conflict" and not any(o["op"] in ("update", "supersede", "absorb", "delete") and
                                                 (o.get("id") == entry["id"] or o.get("old") == entry["id"]) for o in changes["ops"]):
                fail(f"{op['op_id']} conflicts with {entry['id']}; add an explicit update/supersede/absorb/delete for it.")
    for key in classified:
        if not any(o["op_id"] == key[0] and o["op"] == "create" for o in changes["ops"]):
            fail(f"classified refers to {key[0]}, which is not a create op.")
    return pending or None


def path_exists(engine, value):
    return engine.exists(value)


def check_paths(post, changes, engine):
    warnings = []
    for op in changes["ops"]:
        if op["op"] not in ("create", "update", "supersede"):
            continue
        for value in model.validate(op["entry"])["paths"]:
            base, symbol, _ = model.split_path(value)
            if not path_exists(engine, value):
                fail(f"{op['op_id']}: path does not exist at the fixed SHA: {value}")
            if symbol and not base.endswith(".py"):
                warnings.append(f"{op['op_id']}: {value} is a non-Python symbol; only the file was verified.")
    return warnings


def check_sources(kb, changes, checker):
    results = {}
    for op in changes["ops"]:
        if op["op"] not in ("create", "update", "supersede"):
            continue
        value = model.validate(op["entry"])
        existing = kb["entries"].get(op.get("id") or op.get("old"), {}).get("sources", [])
        new = [s for s in value["sources"] if s not in existing]
        for source, result in checker.check_all(value["sources"], new=new).items():
            results[source] = result
            if source in new and not result["valid"]:
                fail(f"{op['op_id']}: source {source} is not usable ({result['reason']}).", "source")
            if source not in new and not result["exists"]:
                results[source]["warning"] = "historical source is no longer readable; add accessible evidence before new judgments"
    return results


def check_attachments(changes):
    for op in changes["ops"]:
        if op["op"] in ("create", "update", "supersede"):
            entry = op["entry"]
            for key, label in (("not_in_code", "코드불가"), ("incentive", "유인")):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    fail(f"{op['op_id']}: attach {label} ({key}) in one line.")
            if all(str(s).startswith("path:") for s in entry.get("sources", [])) and (entry.get("why") or entry.get("rejected_alt")):
                fail(f"{op['op_id']}: path-only sources cannot carry 이유/기각.")


def check_scope(changes, verdicts):
    handled = set()
    for op in changes["ops"]:
        if op["op"] in ("update", "absorb", "delete"):
            handled.add(op["id"])
        elif op["op"] == "supersede":
            handled.add(op["old"])
        elif op["op"] == "reconfirm":
            handled.update(op["ids"])
    for identity, verdict in verdicts.items():
        if verdict["verdict"] in ("judge", "broken") and identity not in handled:
            fail(f"scope entry {identity} is {verdict['verdict']} ({verdict['reason']}) and has no handling operation.")


def check(kb, changes, *, sha, root, gh, scope_ids=(), checked=None, execution_id, batch_id=None, label=None, issued=None, reader=None):
    """Run every gate; return {status: ok, plan, files, post, verdicts, sources, warnings}, {status: candidates, pending}
    or {status: capacity_resolution_required, paths, capacity, omitted_paths}."""
    changes = changeset.validate(changes)
    pending = classification(kb, changes)
    if pending:
        return {"status": "candidates", "pending": pending}
    engine = reconfirmation.Reconfirm(root, sha)
    verdicts = reconfirmation.assess(kb, list(scope_ids), root, sha, checked)
    reader = reader or layout.TreeReader(root, sha)
    try:
        plan_value, files, post = changeset.plan(kb, changes, sha, reader, execution_id=execution_id, scope=scope_ids,
                                                 batch_id=batch_id, label=label, issued=issued, verdicts=verdicts, pointer_reader=reader)
    except layout.CapacityError as exc:
        return capacity_result(kb, exc)
    warnings = list(plan_value["warnings"])
    warnings += check_paths(post, changes, engine)
    source_results = check_sources(kb, changes, sourcing.Sources(gh, root, sha))
    check_attachments(changes)
    check_scope(changes, verdicts)
    return {"status": "ok", "plan": plan_value, "files": files, "post": post, "verdicts": verdicts,
            "sources": source_results, "warnings": warnings, "checked": plan_value["checked"]}


def capacity_page(kb, path):
    """The active entries of one file, for a capacity error's listing."""
    items = [lookup.summary_of(e, [], kb) for k, e in sorted(kb["entries"].items()) if e["status"] == "active" and layout.file_for(e) == path]
    return {"path": path, "cap": layout.cap_for(path), "active": items}


def capacity_result(kb, error):
    """Over-cap files with their existing active entries, so the same execution can add update/supersede/absorb/delete.

    Listings fill one page budget in path order; files that do not fit are named in `omitted_paths`.
    """
    result = {"status": "capacity_resolution_required", "message": str(error), "paths": error.paths, "capacity": [], "omitted_paths": []}
    for path in error.paths:
        page = capacity_page(kb, path)
        if not result["omitted_paths"] and lookup.size({**result, "capacity": result["capacity"] + [page]}) <= lookup.PAGE_BUDGET:
            result["capacity"].append(page)
        else:
            result["omitted_paths"].append(path)
    return result
