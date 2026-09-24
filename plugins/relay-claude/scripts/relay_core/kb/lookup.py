"""Path, term and ID lookup with ordering, redirects, budgets and cursors (spec R3; #57 R1).

Every response is measured as the Unicode character count of its ensure_ascii=False JSON.
A page never exceeds PAGE_BUDGET; the `kb` field other helper results carry never exceeds
FIELD_BUDGET. A cursor is self-contained: it carries the normalized query, the values it is
bound to (KB digest, SHA, run or request identity) and the offset, so `next_request` alone
reads the next page. A changed KB, SHA or binding is a conflict.
"""
import base64
import json
import posixpath
import zlib
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


CURSOR_VERSION = 2
CONDITIONS = ("paths", "terms", "ids", "detail", "include_inactive", "limit")
OFFSET_WIDTH = 12


def encode_cursor(kind, query, bind, offset):
    """`{"v":2,kind,q,bind}` JSON, zlib-compressed and base64url-encoded without padding, then `.` and
    the offset in 12 fixed digits.

    The offset stays outside the compressed part so a cursor's length never depends on its
    offset: the next_request a page measures before filling is exactly as long as the one it
    ends with, and a page can never exceed its budget by a longer final cursor.
    """
    payload = {"v": CURSOR_VERSION, "kind": kind, "q": query, "bind": bind}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=") + "." + str(offset).zfill(OFFSET_WIDTH)


def read_cursor(text, kind):
    """The cursor payload with its offset; an older plain-base64 cursor is an input error, never reinterpreted."""
    if not isinstance(text, str) or not text:
        raise RelayError("input", "cursor — expected the cursor string from next_request")
    head, dot, digits = text.rpartition(".")
    if not dot:
        padded = text + "=" * (-len(text) % 4)
        try:
            old = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except (ValueError, TypeError, UnicodeError):
            old = None
        if isinstance(old, dict) and "offset" in old:
            raise RelayError("input", "cursor from an older helper; query again")
        raise RelayError("input", "cursor — malformed; query again")
    try:
        if len(digits) != OFFSET_WIDTH or not digits.isdigit():
            raise ValueError("offset")
        padded = head + "=" * (-len(head) % 4)
        payload = json.loads(zlib.decompress(base64.urlsafe_b64decode(padded.encode("ascii"))).decode("utf-8"))
    except (ValueError, TypeError, zlib.error, UnicodeError):
        raise RelayError("input", "cursor — malformed; query again")
    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise RelayError("input", "cursor — malformed; query again")
    payload["offset"] = int(digits)
    if payload.get("kind") != kind:
        raise RelayError("conflict", f"Cursor belongs to a {payload.get('kind')} page, not {kind}; query again.")
    return payload


def check_binding(payload, bind):
    """The KB, SHA and run/request identity a page was made from must still hold."""
    if payload.get("bind") != bind:
        raise RelayError("conflict", "Cursor belongs to another base SHA, KB content or run; query again.")
    return payload["offset"]


def next_request(template, cursor):
    """The executable request for the following page: same cwd/command/input plus the cursor."""
    if cursor is None:
        return None
    return {"cwd": template["cwd"], "command": template["command"], "input": {**template["input"], "cursor": cursor}}


def request_template(cwd, action="lookup", command="kb", **identity):
    return {"cwd": str(cwd) if cwd is not None else None, "command": command, "input": {"action": action, **identity}}


def conditions(query):
    return {key: query[key] for key in CONDITIONS}


def page(kb, query, *, budget, offset=0, sha=None, repo=None, envelope=None, request=None):
    """Fill one page under the character budget; the result is complete for the page it names.

    `request` is the next_request template (cwd, command, input without cursor); the
    largest possible next_request is measured before entries are added.
    """
    items, tombstones, missing, glossary = candidates(kb, query)
    bind = {"kb": layout.digest_of(kb), "sha": sha}
    template = request or request_template(None)
    result = {"kb_present": kb["present"], "repository": repo, "sha": sha, "query_hash": query_hash(conditions(query)),
              "kb_digest": bind["kb"], "entries": [], "redirects": [], "glossary": [], "tombstones": tombstones,
              "missing_ids": missing, "returned_ids": [], "omitted": {}, "truncated": False, "next_request": None,
              "warnings": [], **(envelope or {})}
    # One cursor traverses the prioritized entries, then the glossary explanations.
    # Glossary entries must participate in the same limit and byte-independent budget.
    units = [(entry, reasons, False) for entry, reasons, _ in items]
    units += [(entry, [], True) for entry in glossary]
    if not 0 <= offset <= len(units):
        raise RelayError("input", "Cursor offset is outside the lookup results.")
    # Measure against the largest tail the page can end with: a next_request and every omitted count.
    result["next_request"] = next_request(template, encode_cursor("lookup", conditions(query), bind, len(units)))
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
    result["omitted"], result["next_request"] = {}, None
    for entry, _, _ in remaining:
        result["omitted"][entry["type"]] = result["omitted"].get(entry["type"], 0) + 1
    if remaining:
        result["truncated"] = True
        result["next_request"] = next_request(template, encode_cursor("lookup", conditions(query), bind, offset + taken))
    if size(result) > budget:
        raise RelayError("length", "Lookup response metadata exceed the page budget; narrow the query.")
    return result


def cursor_sha(data):
    """The SHA a cursor-only request was bound to, so the caller reads the same tree."""
    if data.get("cursor") and data.get("sha") is None:
        return read_cursor(data["cursor"], "lookup")["bind"].get("sha")
    return data.get("sha")


def lookup(kb, data, *, sha=None, repo=None, cwd=None):
    """One page. A cursor alone restores the query; condition fields sent with it must match."""
    offset = 0
    if data.get("cursor"):
        payload = read_cursor(data["cursor"], "lookup")
        restored = normalize_query({**payload["q"], "sha": sha})
        given = {key: data[key] for key in CONDITIONS if data.get(key) is not None}
        if given and conditions(normalize_query({**payload["q"], **given, "sha": sha})) != conditions(restored):
            raise RelayError("conflict", "Cursor belongs to another query; send the cursor alone or the same conditions.")
        query = restored
        offset = check_binding(payload, {"kb": layout.digest_of(kb), "sha": sha})
    else:
        query = normalize_query({**data, "sha": sha})
    template = request_template(cwd)
    if not kb["present"]:
        return page(kb, query, budget=PAGE_BUDGET, sha=sha, repo=repo, request=template)
    return page(kb, query, budget=PAGE_BUDGET, offset=offset, sha=sha, repo=repo, request=template)


FIELD_KEYS = ("entries", "redirects", "glossary", "tombstones", "missing_ids", "omitted", "truncated", "next_request",
              "query_hash", "kb_digest", "sha")


def summary(kb, paths=(), terms=(), ids=(), *, budget=FIELD_BUDGET, sha=None, limit=DEFAULT_LIMIT, cwd=None, extra=None):
    """The `kb` field for other helper results: None without a KB, else one bounded page.

    Hidden conditions (a diff's paths, every active F, limit 50) travel inside the cursor of
    `next_request`, so the session continues without knowing them. `extra` fields are part
    of the measured field.
    """
    if not kb["present"]:
        return None
    query = normalize_query({"paths": list(paths), "terms": list(terms), "ids": list(ids), "limit": limit, "sha": sha})
    result = page(kb, query, budget=budget, sha=sha, envelope=dict(extra or {}), request=request_template(cwd))
    return {k: result[k] for k in (*FIELD_KEYS, *(extra or {}))}


def counts(kb):
    """Existence and per-type counts for document-stage inspect results."""
    if not kb["present"]:
        return None
    active = {}
    for entry in kb["entries"].values():
        if entry["status"] == "active":
            active[entry["type"]] = active.get(entry["type"], 0) + 1
    return {"present": True, "counts": active, "absorbed": sum(1 for e in kb["entries"].values() if e["status"] == "absorbed")}


def all_ids(kb, paths=(), terms=(), ids=(), limit=DEFAULT_LIMIT):
    """Every entry ID a query matches across all pages, for comparisons that must not stop at page one."""
    query = normalize_query({"paths": list(paths), "terms": list(terms), "ids": list(ids), "limit": limit})
    items, _, _, _ = candidates(kb, query)
    return [entry["id"] for entry, _, _ in items]


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
