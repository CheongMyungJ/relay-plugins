"""Path, term and ID lookup with ordering, redirects, budgets and cursors (spec R3).

Every response is measured as the Unicode character count of its ensure_ascii=False JSON.
A page never exceeds PAGE_BUDGET; the `kb` field other helper results carry never exceeds
FIELD_BUDGET. Cursors are bound to the query, the base SHA and the KB content digest.
"""
import base64
import json
import posixpath
from .. import RelayError
from ..artifacts import digest
from . import entries as model
from . import layout

PAGE_BUDGET = 20000
FIELD_BUDGET = 8000
SUMMARY_BUDGET = 600
DEFAULT_LIMIT, MAX_LIMIT = 12, 50
GROUPS = {"id": 0, "compat": 1, "constraint": 2, "other": 3}


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


def query_hash(query):
    return digest(json.dumps(query, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def normalize_query(data):
    paths = data.get("paths", []) or []
    terms = data.get("terms", []) or []
    ids = data.get("ids", []) or []
    if not all(isinstance(v, list) for v in (paths, terms, ids)):
        raise RelayError("input", "paths, terms and ids must be lists.")
    detail = data.get("detail", "summary")
    if detail not in ("summary", "full"):
        raise RelayError("input", "detail is summary or full.")
    limit = data.get("limit", DEFAULT_LIMIT)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise RelayError("input", f"limit is an integer from 1 to {MAX_LIMIT}.")
    for identity in ids:
        if not isinstance(identity, str) or not model.ID.match(identity):
            raise RelayError("input", "ids must be full entry IDs.")
    return {"paths": sorted({model.path(p) for p in paths}), "terms": sorted({model.term(t) for t in terms}),
            "ids": sorted(set(ids)), "detail": detail, "include_inactive": bool(data.get("include_inactive")),
            "limit": limit, "sha": data.get("sha")}


def relation(query, target):
    """(level, specificity) of a query path against an entry path, or None."""
    qbase, qsym, qdir = model.split_path(query)
    tbase, tsym, tdir = model.split_path(target)
    if tdir:
        prefix = tbase if tbase.endswith("/") else tbase + "/"
        if qdir:
            qprefix = qbase if qbase.endswith("/") else qbase + "/"
            if qprefix == prefix or qprefix.startswith(prefix):
                return "directory", 1
            if prefix.startswith(qprefix):
                return "descendant", 2
            return None
        return ("directory", 1) if qbase.startswith(prefix) else None
    if qdir:
        qprefix = qbase if qbase.endswith("/") else qbase + "/"
        return ("descendant", 2) if tbase.startswith(qprefix) else None
    if tbase != qbase:
        return None
    if qsym and tsym == qsym:
        return "symbol", 3
    return "file", 2


def expand_terms(kb, terms):
    """Declared glossary aliases only: a matching glossary entry adds all its terms."""
    expanded, glossary = set(terms), []
    for entry in kb["entries"].values():
        if entry["type"] == "S" and entry.get("subtype") == "term" and entry["status"] == "active":
            if set(entry["terms"]) & set(terms):
                expanded |= set(entry["terms"])
                glossary.append(entry)
    return expanded, sorted(glossary, key=lambda e: e["id"])


def candidates(kb, query):
    """Ordered (entry, reasons, group) tuples plus tombstones and missing IDs."""
    wide = query["include_inactive"] or bool(query["ids"])
    expanded, glossary = expand_terms(kb, query["terms"])
    found = {}
    for identity in query["ids"]:
        entry = kb["entries"].get(identity)
        if entry is not None:
            found[identity] = ([{"id": identity}], 3, 1)
    for key, entry in kb["entries"].items():
        if entry["type"] == "S" and entry.get("subtype") == "term":
            continue
        visible = entry["status"] == "active" or entry["status"] == "absorbed" or wide
        if not visible:
            continue
        reasons, best, count = [], 0, 0
        for q in query["paths"]:
            for target in entry["paths"]:
                rel = relation(q, target)
                if rel:
                    reasons.append({"path": q, "matched": target, "level": rel[0]})
                    best, count = max(best, rel[1]), count + 1
        hits = sorted(set(entry["terms"]) & expanded)
        for t in hits:
            reasons.append({"term": t})
        count += len(hits)
        if reasons and key not in found:
            found[key] = (reasons, best, count)
        elif reasons:
            found[key] = (found[key][0] + reasons, max(found[key][1], best), found[key][2] + count)
    ordered = []
    for key, (reasons, best, count) in found.items():
        entry = kb["entries"][key]
        if key in query["ids"]:
            group = GROUPS["id"]
        elif entry["type"] == "C" and entry["compat"]:
            group = GROUPS["compat"]
        elif entry["type"] == "C":
            group = GROUPS["constraint"]
        else:
            group = GROUPS["other"]
        ordered.append((group, -best, -count, key, entry, reasons))
    ordered.sort(key=lambda item: item[:4])
    tombstones = [{"id": i, "status": "deleted", **kb["state"]["deleted"][i]} for i in query["ids"] if i in kb["state"]["deleted"]]
    missing = [i for i in query["ids"] if i not in kb["entries"] and i not in kb["state"]["deleted"]]
    return [(e, r, g) for g, _, _, _, e, r in ordered], tombstones, missing, glossary


def summary_of(entry, reasons, kb):
    """At most SUMMARY_BUDGET characters; long optional fields are marked and left to a full lookup."""
    core = entry.get("why") or entry.get("definition") or entry.get("not_in_code")
    value = {"id": entry["id"], "type": entry["type"], "status": entry["status"], "rule": entry["rule"],
             "paths": list(entry["paths"]), "terms": list(entry["terms"]), "compat": entry["compat"],
             "sources": len(entry["sources"]), "evidence": core, "match_reason": reasons, "omitted_fields": []}
    if entry["type"] == "F" and entry["blocking"]:
        value["blocking"] = True
    if entry["status"] == "superseded":
        value["superseded_by"] = entry["superseded_by"]
    if entry["status"] == "absorbed":
        value["pointer"] = entry["absorbed_into"]
    value["file"] = kb["location"].get(entry["id"])
    if size(value) > SUMMARY_BUDGET:
        value["evidence"] = None
        value["omitted_fields"].append("evidence")
    if size(value) > SUMMARY_BUDGET:
        value["match_reason"] = value["match_reason"][:3]
        value["omitted_fields"].append("match_reason")
    while size(value) > SUMMARY_BUDGET and len(value["paths"]) > 1:
        value["paths"] = value["paths"][:-1]
        value["omitted_fields"] = sorted(set(value["omitted_fields"]) | {"paths"})
    if size(value) > SUMMARY_BUDGET:
        value["rule"] = value["rule"][:200]
    return value


def full_of(entry, reasons, kb):
    return {**model.to_host(entry), "match_reason": reasons, "file": kb["location"].get(entry["id"])}


def encode_cursor(payload):
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decode_cursor(text, expected):
    try:
        payload = json.loads(base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        raise RelayError("input", "Malformed cursor.")
    if not isinstance(payload, dict) or {k: payload.get(k) for k in expected} != expected or type(payload.get("offset")) is not int:
        raise RelayError("conflict", "Cursor belongs to another query, base SHA or KB content; query again.")
    return payload["offset"]


def page(kb, query, *, budget, offset=0, sha=None, repo=None, envelope=None):
    """Fill one page under the character budget; the result is complete for the page it names."""
    items, tombstones, missing, glossary = candidates(kb, query)
    binding = {"query": query_hash(query), "kb": layout.digest_of(kb), "sha": sha}
    result = {"kb_present": kb["present"], "repository": repo, "sha": sha, "query_hash": binding["query"],
              "kb_digest": binding["kb"], "entries": [], "redirects": [], "glossary": [], "tombstones": tombstones,
              "missing_ids": missing, "returned_ids": [], "omitted": {}, "truncated": False, "next_cursor": None,
              "warnings": [], **(envelope or {})}
    # One cursor traverses the prioritized entries, then the glossary explanations.
    # Glossary entries must participate in the same limit and byte-independent budget.
    units = [(entry, reasons, False) for entry, reasons, _ in items]
    units += [(entry, [], True) for entry in glossary]
    if not 0 <= offset <= len(units):
        raise RelayError("input", "Cursor offset is outside the lookup results.")
    # Measure against the largest tail the page can end with: a cursor and every omitted count.
    result["next_cursor"] = encode_cursor({**binding, "offset": len(units)})
    for entry, _, _ in units[offset:]:
        result["omitted"][entry["type"]] = result["omitted"].get(entry["type"], 0) + 1
    taken = 0
    for entry, reasons, is_glossary in units[offset:]:
        redirect = None
        if is_glossary:
            item = {"id": entry["id"], "terms": entry["terms"], "definition": entry["definition"], "rule": entry["rule"]}
            slot = "glossary"
        elif entry["status"] == "absorbed" and entry["id"] not in query["ids"]:
            item = {"id": entry["id"], "rule": entry["rule"], "pointer": entry["absorbed_into"], "match_reason": reasons}
            slot = "redirects"
        else:
            item = full_of(entry, reasons, kb) if query["detail"] == "full" else summary_of(entry, reasons, kb)
            slot = "entries"
            if entry["status"] == "superseded":
                redirect = {"id": entry["id"], "rule": entry["rule"], "pointer": entry["superseded_by"],
                            "match_reason": [{"superseded": True}]}
                result["redirects"].append(redirect)
        result[slot].append(item)
        result["returned_ids"].append(entry["id"])
        if size(result) > budget:
            result[slot].pop()
            result["returned_ids"].pop()
            if redirect is not None:
                result["redirects"].pop()
            if not taken:
                raise RelayError("length", "A lookup item and its response metadata exceed the page budget; narrow the query.")
            break
        taken += 1
        if taken >= query["limit"]:
            break
    remaining = units[offset + taken:]
    result["omitted"], result["next_cursor"] = {}, None
    for entry, _, _ in remaining:
        result["omitted"][entry["type"]] = result["omitted"].get(entry["type"], 0) + 1
    if remaining:
        result["truncated"] = True
        result["next_cursor"] = encode_cursor({**binding, "offset": offset + taken})
    if size(result) > budget:
        raise RelayError("length", "Lookup response metadata exceed the page budget; narrow the query.")
    return result


def lookup(kb, data, *, sha=None, repo=None):
    query = normalize_query({**data, "sha": sha})
    offset = 0
    if data.get("cursor"):
        offset = decode_cursor(data["cursor"], {"query": query_hash(query), "kb": layout.digest_of(kb), "sha": sha})
    if not kb["present"]:
        return page(kb, query, budget=PAGE_BUDGET, sha=sha, repo=repo)
    return page(kb, query, budget=PAGE_BUDGET, offset=offset, sha=sha, repo=repo)


def summary(kb, paths=(), terms=(), ids=(), *, budget=FIELD_BUDGET, sha=None, limit=DEFAULT_LIMIT):
    """The `kb` field for other helper results: None without a KB, else one bounded page."""
    if not kb["present"]:
        return None
    query = normalize_query({"paths": list(paths), "terms": list(terms), "ids": list(ids), "limit": limit, "sha": sha})
    hint = "relay.py kb lookup with paths/terms/ids and this cursor"
    result = page(kb, query, budget=budget, sha=sha, envelope={"how_to_query": hint})
    keys = ("entries", "redirects", "glossary", "tombstones", "missing_ids", "omitted", "truncated", "next_cursor", "query_hash", "kb_digest", "sha")
    trimmed = {k: result[k] for k in keys}
    trimmed["how_to_query"] = hint
    return trimmed


def counts(kb):
    """Existence and per-type counts for document-stage inspect results."""
    if not kb["present"]:
        return None
    active = {}
    for entry in kb["entries"].values():
        if entry["status"] == "active":
            active[entry["type"]] = active.get(entry["type"], 0) + 1
    return {"present": True, "counts": active, "absorbed": sum(1 for e in kb["entries"].values() if e["status"] == "absorbed"),
            "how_to_query": "relay.py kb lookup with paths=[...] and terms=[...] before drafting"}


def extract_refs(text):
    """Backtick-wrapped repository-relative path candidates and entry IDs mentioned in a document."""
    import re
    paths, ids = set(), set(re.findall(r"\b[DCSVNF]-[0-9a-f]{32}\b", text))
    for raw in re.findall(r"`([^`\n]{1,240})`", text):
        candidate = raw.strip()
        if candidate.startswith(("http", "-", "{", "<", "$", "python ", "git ")) or " " in candidate:
            continue
        if "/" not in candidate and not re.search(r"\.[A-Za-z0-9]+$", candidate):
            continue
        try:
            paths.add(model.path(candidate))
        except RelayError:
            continue
    return sorted(paths), sorted(ids)
