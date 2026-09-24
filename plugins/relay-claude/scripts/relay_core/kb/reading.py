"""The reading layer: KB fields for inspect/begin/checkpoint/review and the reference-section check (spec R8).

Nothing here writes state. `inspect` results carry a bounded `kb` field, execution
checkpoints return a `kb_recheck` field without touching the run, and document prepare
verifies the `## 참조한 KB 항목` section against the KB when one exists.
"""
import re
from pathlib import Path
from .. import RelayError
from . import entries as model
from . import layout, lookup

SECTION = "## 참조한 KB 항목"
LINE_ID = re.compile(r"^- ([DCSVNF]-[0-9a-f]{32}) — (.+)$")
LINE_POINTER = re.compile(r"^- ([DCSVNF]-[0-9a-f]{32}) → (references/[A-Za-z0-9_.-]+\.md#[A-Za-z0-9_.-]+) — (.+)$")
LINE_NONE = re.compile(r"^- 없음 \(조회: (.+)\)$")


def worktree_kb(root):
    """The KB of a checkout, or an absent KB when the path is not a usable worktree."""
    if not root:
        return layout.empty(), None
    path = Path(root)
    if not path.is_dir() or not (path / ".git").exists():
        return layout.empty(), None
    reader = layout.WorktreeReader(path)
    try:
        return layout.load(reader), reader
    except RelayError:
        raise


def tree_kb(root, sha):
    reader = layout.TreeReader(root, sha)
    try:
        return layout.load(reader), reader
    except RelayError:
        raise


def for_document(kb, proof_body, *, sha=None, limit=lookup.DEFAULT_LIMIT, cwd=None):
    """The implement-stage field: entries for the paths and IDs the proof document names.

    The queried paths and IDs travel in `next_request`; they are not repeated in the field.
    """
    paths, ids = lookup.extract_refs(proof_body or "")
    return lookup.summary(kb, paths=paths, ids=ids, sha=sha, limit=limit, cwd=cwd)


def document_ids(kb, proof_body):
    """Every entry the proof lookup matches on any page."""
    if not kb["present"]:
        return []
    paths, ids = lookup.extract_refs(proof_body or "")
    return lookup.all_ids(kb, paths=paths, ids=ids)


def recheck(kb, paths, initial_ids=(), *, sha=None, cwd=None):
    """After verified/committed: the actual changed paths, and which entries the first lookup missed.

    `new_ids` compares every entry the changed paths match, not only the first page; the
    changed paths themselves travel in `next_request`.
    """
    if not kb["present"] or not paths:
        return None
    clean = []
    for value in paths:
        try:
            clean.append(model.path(value))
        except RelayError:
            continue
    new_ids = sorted(set(lookup.all_ids(kb, paths=clean)) - set(initial_ids))
    return lookup.summary(kb, paths=clean, sha=sha, limit=lookup.MAX_LIMIT, cwd=cwd, extra={"new_ids": new_ids})


def failure_classes(kb, *, sha=None, cwd=None):
    """Every active F entry, paged like any `kb` field; no F entries keeps the same shape."""
    if not kb["present"]:
        return None
    ids = sorted(k for k, e in kb["entries"].items() if e["type"] == "F" and e["status"] == "active")
    return lookup.summary(kb, ids=ids, sha=sha, limit=lookup.MAX_LIMIT, cwd=cwd)


def references(body):
    """Parse the reference section; returns (found, items) where items carry kind/id/pointer/how/keys."""
    text = body.replace("\r\n", "\n")
    if SECTION not in text:
        return False, []
    section = text.split(SECTION, 1)[1]
    lines = []
    for raw in section.split("\n")[1:]:
        if raw.startswith("## ") or raw.startswith("# "):
            break
        if raw.strip():
            lines.append(raw.rstrip())
    items = []
    for raw in lines:
        found = LINE_POINTER.match(raw)
        if found:
            items.append({"kind": "pointer", "id": found.group(1), "pointer": found.group(2), "how": found.group(3)})
            continue
        found = LINE_ID.match(raw)
        if found:
            items.append({"kind": "id", "id": found.group(1), "how": found.group(2)})
            continue
        found = LINE_NONE.match(raw)
        if found:
            items.append({"kind": "none", "keys": found.group(1)})
            continue
        items.append({"kind": "invalid", "line": raw})
    return True, items


def check_references(kb, reader, body, label="document"):
    """Enforce the section when a KB exists; absent KB means the section is not required."""
    if not kb["present"]:
        return None
    found, items = references(body)
    if not found:
        raise RelayError("kb", f"{label} lacks the '{SECTION}' section required while a KB exists.")
    if not items:
        raise RelayError("kb", f"'{SECTION}' must list entry IDs or a '- 없음 (조회: ...)' line.")
    for item in items:
        if item["kind"] == "invalid":
            raise RelayError("kb", f"Unrecognized reference line: {item['line'][:120]}")
        if item["kind"] == "none":
            if "경로" not in item["keys"] and "용어" not in item["keys"]:
                raise RelayError("kb", "The '없음' line must name the queried 경로 or 용어.")
            continue
        entry = kb["entries"].get(item["id"])
        if entry is None:
            raise RelayError("kb", "Referenced entry does not exist: " + item["id"] + (" (deleted)" if item["id"] in kb["state"]["deleted"] else ""))
        if entry["status"] == "superseded":
            raise RelayError("kb", f"Referenced entry {item['id']} is superseded by {entry['superseded_by']}; cite the current entry.")
        if entry["status"] == "absorbed":
            if item["kind"] != "pointer" or item["pointer"] != entry["absorbed_into"]:
                raise RelayError("kb", f"Absorbed entry {item['id']} must be cited as '- <ID> → {entry['absorbed_into']} — 반영 방식'.")
            if not layout.pointer_exists(reader, item["pointer"]):
                raise RelayError("kb", f"Absorbed pointer does not resolve: {item['pointer']}")
        elif item["kind"] == "pointer":
            raise RelayError("kb", f"Active entry {item['id']} is cited with a pointer; cite it by ID.")
    return items


def validate_refs(kb, reader, refs):
    """Review items may name entries; each must be active or a resolvable absorbed pointer."""
    if refs is None:
        return []
    if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
        raise RelayError("input", "kb_refs must be a list of entry IDs.")
    if refs and not kb["present"]:
        raise RelayError("input", "kb_refs given but the PR head has no KB.")
    for identity in refs:
        if not model.ID.match(identity):
            raise RelayError("input", "kb_refs must be full entry IDs.")
        entry = kb["entries"].get(identity)
        if entry is None or entry["status"] == "superseded":
            raise RelayError("input", "kb_refs names a missing or superseded entry: " + identity)
        if entry["status"] == "absorbed" and not layout.pointer_exists(reader, entry["absorbed_into"]):
            raise RelayError("input", "kb_refs names an absorbed entry whose pointer does not resolve: " + identity)
    return sorted(set(refs))
