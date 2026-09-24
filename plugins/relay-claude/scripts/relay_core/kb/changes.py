"""Change sets, frozen operation plans and the file transaction (spec R4).

A change set is validated, applied to an in-memory copy of the KB, rendered, and only
then written. The plan freezes the change-set digest, base SHA, pre-tree, issued IDs and
per-operation input/output digests; a retry with the same plan completes what is missing
and refuses to overwrite anything a third party changed.
"""
import copy
import hashlib
import json
import uuid
from pathlib import Path
from .. import RelayError
from ..artifacts import digest
from . import entries as model
from . import layout, reconfirm

OPS = ("create", "update", "supersede", "absorb", "delete", "reconfirm")
VERDICTS = ("same", "conflict", "unrelated")
REASON_LIMIT = 400


def fail(message, code="kb"):
    raise RelayError(code, message)


def reason(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\n" in value:
        fail(f"{label} — expected one nonempty line", "budget")
    if len(value) > REASON_LIMIT:
        fail(f"{label} — expected at most {REASON_LIMIT} characters", "budget")
    return value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate(changes):
    """Shape only: unique op_id, known ops, one mutation per existing ID, reasons within budget.

    Messages name the field path (`changes.ops[2].op_id — ...`) so a host fixes input without reading code.
    """
    if not isinstance(changes, dict) or set(changes) - {"ops", "rejected", "classified"}:
        fail("changes — expected an object with only ops, rejected and classified")
    ops, rejected, classified = changes.get("ops", []), changes.get("rejected", []), changes.get("classified", [])
    for name, value in (("ops", ops), ("rejected", rejected), ("classified", classified)):
        if not isinstance(value, list):
            fail(f"changes.{name} — expected a list")
    seen, touched = set(), {}
    for index, op in enumerate(ops):
        at = f"changes.ops[{index}]"
        if not isinstance(op, dict) or op.get("op") not in OPS:
            fail(f"{at}.op — expected one of " + ", ".join(OPS))
        key = op.get("op_id")
        if not isinstance(key, str) or not key or key in seen:
            fail(f"{at}.op_id — required unique nonempty string")
        seen.add(key)
        kind = op["op"]
        field = {"update": "id", "supersede": "old", "absorb": "id", "delete": "id", "reconfirm": "ids", "create": None}[kind]
        targets = {"update": [op.get("id")], "supersede": [op.get("old")], "absorb": [op.get("id")], "delete": [op.get("id")],
                   "reconfirm": list(op.get("ids") or []), "create": []}[kind]
        for target in targets:
            if not isinstance(target, str) or not model.ID.match(target):
                fail(f"{at}.{field} — expected full entry IDs for {kind}")
            if target in touched:
                fail(f"{at}.{field} — entry {target} already has an operation ({touched[target]})")
            touched[target] = key
        if kind in ("create", "update", "supersede") and not isinstance(op.get("entry"), dict):
            fail(f"{at}.entry — expected an entry object for {kind}")
        if kind == "absorb" and (not isinstance(op.get("pointer"), str) or not model.POINTER.match(op["pointer"])):
            fail(f"{at}.pointer — expected references/<file>.md#<anchor>")
        if kind == "reconfirm":
            if not targets:
                fail(f"{at}.ids — required nonempty list of entry IDs")
            evidence = op.get("evidence", {})
            if not isinstance(evidence, dict) or any(not isinstance(v, str) for v in evidence.values()):
                fail(f"{at}.evidence — expected an object mapping IDs to text")
            for value in evidence.values():
                reason(value, f"{at}.evidence")
        elif "evidence" in op and op["evidence"] is not None:
            reason(op["evidence"], f"{at}.evidence")
        if "label" in op and not isinstance(op["label"], str):
            fail(f"{at}.label — expected display text")
    for index, item in enumerate(rejected):
        at = f"changes.rejected[{index}]"
        if not isinstance(item, dict) or set(item) != {"rule", "reason"}:
            fail(f"{at} — expected an object with exactly rule and reason")
        reason(item["rule"], at + ".rule")
        reason(item["reason"], at + ".reason")
    for index, item in enumerate(classified):
        at = f"changes.classified[{index}]"
        if not isinstance(item, dict) or set(item) != {"op_id", "existing_id", "verdict", "reason"} or item["verdict"] not in VERDICTS:
            fail(f"{at} — expected op_id, existing_id, verdict (same/conflict/unrelated) and reason")
        if item["op_id"] not in seen or not model.ID.match(str(item["existing_id"])):
            fail(f"{at} — op_id or existing_id names an unknown op or entry")
        reason(item["reason"], at + ".reason")
    return {"ops": ops, "rejected": rejected, "classified": classified}


def normalized_decision(entry):
    return tuple(" ".join((entry.get(k) or "").split()).casefold() for k in ("rule", "why", "rejected_alt"))


def apply_ops(kb, changes, sha, issued=None, verdicts=None, pointer_reader=None):
    """Return (post_kb, issued_ids, checked_updates, events) without touching files."""
    post = copy.deepcopy(kb)
    issued = dict(issued or {})
    checked, events = {}, []
    entries, confirmed = post["entries"], post["state"]["confirmed"]

    def active(identity, kind):
        entry = entries.get(identity)
        if entry is None:
            fail(f"{kind}: unknown entry {identity}")
        if entry["status"] != "active":
            fail(f"{kind}: entry {identity} is not active")
        return entry

    def needs_evidence(entry, op):
        if entry["type"] == "C" and entry["compat"] and not op.get("evidence"):
            fail(f"{op['op']} on compat entry {entry['id']} requires evidence ({op['op_id']}).")

    for op in changes["ops"]:
        kind, key = op["op"], op["op_id"]
        if kind == "create":
            value = model.validate(op["entry"])
            identity = issued.get(key) or value["type"] + "-" + uuid.uuid4().hex
            if identity in entries or identity in post["state"]["deleted"]:
                fail("Issued ID collides with an existing entry: " + identity)
            issued[key] = identity
            entries[identity] = model.validate({**value, "id": identity, "status": "active"}, managed=True)
            post["location"].setdefault(identity, layout.file_for(entries[identity]))
            confirmed[identity] = sha
            events.append({"op_id": key, "op": kind, "id": identity})
        elif kind == "update":
            current = active(op["id"], kind)
            needs_evidence(current, op)
            value = model.validate(op["entry"])
            if value["type"] != current["type"] or value.get("subtype") != current.get("subtype"):
                fail(f"update cannot change the type of {op['id']} ({key}).")
            if current["type"] == "D" and normalized_decision(value) != normalized_decision(current):
                fail(f"update of decision {op['id']} may only supplement fields; use supersede for a changed decision ({key}).")
            entries[op["id"]] = model.validate({**value, "id": op["id"], "status": "active", "supersedes": current.get("supersedes")}, managed=True)
            confirmed[op["id"]] = sha
            events.append({"op_id": key, "op": kind, "id": op["id"]})
        elif kind == "supersede":
            old = active(op["old"], kind)
            needs_evidence(old, op)
            value = model.validate(op["entry"])
            identity = issued.get(key) or value["type"] + "-" + uuid.uuid4().hex
            if identity in entries or identity in post["state"]["deleted"]:
                fail("Issued ID collides with an existing entry: " + identity)
            issued[key] = identity
            entries[identity] = model.validate({**value, "id": identity, "status": "active", "supersedes": op["old"]}, managed=True)
            entries[op["old"]] = model.validate({**old, "status": "superseded", "superseded_by": identity}, managed=True)
            post["location"].setdefault(identity, layout.file_for(entries[identity]))
            confirmed.pop(op["old"], None)
            confirmed[identity] = sha
            events.append({"op_id": key, "op": kind, "id": identity, "old": op["old"]})
        elif kind == "absorb":
            current = active(op["id"], kind)
            needs_evidence(current, op)
            if pointer_reader is not None and not layout.pointer_exists(pointer_reader, op["pointer"]):
                fail(f"absorb pointer does not resolve at the base SHA: {op['pointer']} ({key}).")
            entries[op["id"]] = model.validate({**current, "status": "absorbed", "absorbed_into": op["pointer"]}, managed=True)
            confirmed.pop(op["id"], None)
            events.append({"op_id": key, "op": kind, "id": op["id"]})
        elif kind == "delete":
            current = active(op["id"], kind)
            needs_evidence(current, op)
            if current["type"] == "D":
                fail(f"Decisions are superseded, never deleted ({key}).")
            links = [v for v in (current.get("supersedes"), current.get("superseded_by")) if v]
            links += sorted(k for k, e in entries.items() if e.get("superseded_by") == op["id"] and k not in links)
            entries.pop(op["id"])
            post["location"].pop(op["id"], None)
            confirmed.pop(op["id"], None)
            post["state"]["deleted"][op["id"]] = {"sha": sha, "links": sorted(set(links))}
            events.append({"op_id": key, "op": kind, "id": op["id"]})
        else:
            evidence = op.get("evidence", {})
            for identity in op["ids"]:
                current = active(identity, kind)
                verdict = (verdicts or {}).get(identity, {}).get("verdict")
                if verdict == "broken":
                    fail(f"reconfirm cannot confirm a broken entry {identity}; update, supersede, absorb or delete it ({key}).")
                if current["type"] == "C" and current["compat"] and not evidence.get(identity):
                    fail(f"reconfirm of compat entry {identity} requires evidence ({key}).")
                if evidence.get(identity):
                    confirmed[identity] = sha
                    events.append({"op_id": key, "op": kind, "id": identity, "judged": True})
                elif verdict == "auto":
                    checked[identity] = reconfirm.record(current, confirmed.get(identity), sha)
                    events.append({"op_id": key, "op": kind, "id": identity, "judged": False})
                else:
                    fail(f"reconfirm of {identity} needs evidence unless the verdict is auto ({key}).")
    # The first real entry creates docs/kb; an empty change set on an absent KB leaves it absent.
    post["present"] = kb["present"] or bool(post["entries"])
    return post, issued, checked, events


def tree_digest(reader):
    """Digest of every file the KB owns or manages as it stands in the reader."""
    files = {}
    for path in reader.list(layout.ROOT + "/"):
        files[path] = reader.read(path)
    for path in reader.find(set(layout.INSTRUCTIONS)):
        files[path] = reader.read(path)
    return digest_of({p: digest(t) for p, t in files.items() if t is not None})


def plan(kb, changes, sha, reader, *, execution_id, scope, batch_id=None, label=None, issued=None, verdicts=None, pointer_reader=None):
    """Validate, apply in memory, render, and freeze the operation plan plus the rendered files."""
    changes = validate(changes)
    post, issued, checked, events = apply_ops(kb, changes, sha, issued, verdicts, pointer_reader)
    rendered = layout.render(post, reader)
    files = rendered["files"]
    pre_tree = tree_digest(reader)
    per_file = {}
    for path, text in sorted(files.items()):
        before = reader.read(path)
        per_file[path] = {"pre": digest(before) if before is not None else None, "post": digest(text) if text is not None else None}
    plan_value = {"schema": 1, "digest": digest_of(changes), "sha": sha, "execution_id": execution_id, "batch_id": batch_id,
                  "label": label, "scope": sorted(scope or []), "pre_tree": pre_tree, "issued": dict(sorted(issued.items())),
                  "ops": [{"op_id": op["op_id"], "op": op["op"], "input_digest": digest_of(op),
                           "output_digest": digest_of([e for e in events if e["op_id"] == op["op_id"]])} for op in changes["ops"]],
                  "files": per_file, "deleted": sorted(p for p, t in files.items() if t is None and reader.read(p) is not None),
                  "post_tree": digest_of({p: v["post"] for p, v in per_file.items() if v["post"] is not None}),
                  "checked": checked, "events": events, "warnings": rendered["warnings"]}
    return plan_value, {p: t for p, t in files.items()}, post


def apply(plan_value, files, root, record_path):
    """Write the planned files into root, recording pre/post state first; retries complete or conflict."""
    root, record_path = Path(root), Path(record_path)
    from ..state import read_json, write_json
    record = read_json(record_path) if record_path.exists() else None
    if record is not None and record.get("plan_digest") != plan_value["digest"]:
        fail("Another plan was applied here; the same batch cannot switch content.", "conflict")
    if record is None:
        record = {"schema": 1, "plan_digest": plan_value["digest"], "files": plan_value["files"], "deleted": plan_value["deleted"],
                  "post_tree": plan_value["post_tree"], "status": "planned"}
        write_json(record_path, record)
    written = []
    for path, expected in sorted(plan_value["files"].items()):
        target = root / path
        current = target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.is_file() else None
        current_digest = digest(current) if current is not None else None
        if current_digest == expected["post"]:
            continue
        if current_digest != expected["pre"]:
            record["status"] = "conflict"
            write_json(record_path, record)
            fail(f"{path} changed outside this plan; resolve it before retrying.", "conflict")
        if files[path] is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".relay-kb.tmp")
            temporary.write_text(files[path], encoding="utf-8", newline="\n")
            temporary.replace(target)
        written.append(path)
    actual = {}
    for path, expected in plan_value["files"].items():
        target = root / path
        if target.is_file():
            actual[path] = digest(target.read_text(encoding="utf-8").replace("\r\n", "\n"))
    if digest_of(actual) != plan_value["post_tree"]:
        record["status"] = "conflict"
        write_json(record_path, record)
        fail("Post-tree differs after writing; inspect the worktree.", "conflict")
    record["status"] = "applied"
    write_json(record_path, record)
    return {"status": "applied", "written": written, "paths": sorted(plan_value["files"]), "post_tree": plan_value["post_tree"]}
