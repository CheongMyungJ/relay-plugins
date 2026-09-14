"""Artifacts and their next step, read only where relay_core writes them.

An issue body or comment is an artifact when relay_core.artifacts.decode returns a
record; a PR body or review posting is one when next_step.read finds the anchored
comment before its marker. Anything else is somebody's text. Malformed metadata is
not an error here: the dispatcher opens sessions, it never repairs records.
"""
from relay_core import RelayError, artifacts, next_step as steps
from relay_core.artifacts import reference
from relay_core.kb import handoff as handoffs

KIND_STAGE = {"issue": "open", "intent": "intent", "spec": "design", "plan": "plan", "brief": "brief",
              "implementation": "implement", "investigation": "investigate", "pr": "pr", "review": "review",
              "kb": "kb", "kb-sync": "kb-sync"}
PR_MARKER = "<!-- relay:pr-request"
REVIEW_MARKER = "<!-- relay:review"
KB_MARKER = "<!-- relay:kb "
KB_SYNC_MARKER = "<!-- relay:kb-sync"
FORMAL = ("intent", "spec", "plan")  # upstream to downstream


def artifact(kind, target, text, next_step, status, *, version=None, updated=None, stale=False, source):
    return {"kind": kind, "stage": KIND_STAGE[kind], "target": str(target), "digest": artifacts.digest(text or ""),
            "next_step": next_step, "status": status, "version": version, "updated": updated, "stale": stale,
            "source": source}


def from_record(text, target, updated, source):
    """A metadata-bearing issue body or comment, or None."""
    try:
        record = artifacts.decode(text or "", str(target))
    except RelayError:
        return None
    if not record or record["next_step_status"] != steps.RECORDED:
        return None
    return artifact(record["meta"]["kind"], target, text, record["next_step"], record["next_step_status"],
                    version=record["meta"]["version"], updated=updated, source=source)


def from_anchor(text, target, updated, kind, marker, source):
    status, value = steps.read(text or "", before=marker)
    if status != steps.RECORDED:
        return None
    return artifact(kind, target, text, value, status, updated=updated, source=source)


def from_issue_body(issue):
    return from_record(issue.get("body") or "", issue["number"], issue.get("updated_at"), "issue_body")


def from_pull_body(pull):
    """A PR body is a pr artifact at its request marker, otherwise a kb-sync draft PR at its run marker.

    A body of the handoff protocol only names its run: its handoff comments are the signal,
    so the body never ends, resumes or reviews anything.
    """
    body = pull.get("body") or ""
    found = from_anchor(body, pull["number"], pull.get("updated_at"), "pr", PR_MARKER, "pr_body")
    if found or handoffs.body_run(body):
        return found
    return from_anchor(body, pull["number"], pull.get("updated_at"), "kb-sync", KB_SYNC_MARKER, "pr_body")


def from_review(comment, source):
    stamp = comment.get("updated_at") or comment.get("submitted_at")
    return from_anchor(comment.get("body") or "", comment["id"], stamp, "review", REVIEW_MARKER, source)


def from_kb(comment, source):
    return from_anchor(comment.get("body") or "", comment["id"], comment.get("updated_at"), "kb", KB_MARKER, source)


def from_handoff(comment, source):
    """A kb-sync handoff comment: its validated run/limit/round/state travel with the artifact."""
    found = handoffs.read(comment.get("body") or "")
    if not found:
        return None
    meta, value = found
    return dict(artifact("kb-sync", comment["id"], comment.get("body"), value, steps.RECORDED,
                         updated=comment.get("updated_at"), source=source), handoff=meta)


def from_comment(comment, source="comment"):
    """A general comment is a Relay document first, then a review posting unit, a kb result, a kb-sync handoff."""
    return (from_record(comment.get("body") or "", comment["id"], comment.get("updated_at"), source)
            or from_review(comment, source) or from_kb(comment, source) or from_handoff(comment, source))


def newest(entries):
    return max(entries, key=lambda e: (e.get("updated") or "", e["target"])) if entries else None


def chain(issue, comments):
    """The issue's document chain: head, whether anything downstream of it is stale, and every document.

    The head is the most downstream current document: a current implementation report,
    otherwise plan > spec > intent on the formal path or the brief on the brief path.
    With both paths present the newest report's basis decides; without a report, the
    more recently updated side does. The issue body is the head only when no document
    exists at all. Investigations never join the chain.
    """
    records, runs = artifacts.collect(issue, comments)
    stamps = {str(c["id"]): c.get("updated_at") for c in comments}

    def entry(kind, record, stale):
        return {"kind": kind, "stage": KIND_STAGE[kind], "target": record["target"], "digest": record["raw_hash"],
                "next_step": record["next_step"], "status": record["next_step_status"],
                "version": record["meta"]["version"], "updated": stamps.get(record["target"]) or record["meta"].get("updated_at"),
                "stale": stale, "source": "comment", "parents": sorted(record["meta"]["parents"])}

    documents = {kind: entry(kind, record, record["stale"]) for kind, record in records.items()}
    reports = []
    for run_id, record in runs.items():
        parents = record["meta"]["parents"]
        stale = any(name not in records or records[name]["stale"] or reference(records[name]) != ref
                    for name, ref in parents.items())
        reports.append(dict(entry("implementation", record, stale), run_id=run_id))
    if not documents:
        head = from_issue_body(issue)
        return {"head": head, "downstream_stale": False, "documents": documents, "reports": reports}
    current_reports = [r for r in reports if not r["stale"]]
    if current_reports:
        return {"head": newest(current_reports), "downstream_stale": False, "documents": documents, "reports": reports}
    formal = [documents[k] for k in FORMAL if k in documents]
    brief = documents.get("brief")
    if brief and formal:
        latest = newest(reports)
        if latest:
            path = "brief" if "brief" in latest["parents"] else "formal"
        else:
            path = "brief" if (brief.get("updated") or "") >= (newest(formal).get("updated") or "") else "formal"
    else:
        path = "brief" if brief else "formal"
    if path == "brief":
        head, downstream = brief, []
    else:
        head, downstream = None, []
        for kind in FORMAL:
            doc = documents.get(kind)
            if doc is None:
                continue
            if head is not None and doc["stale"]:
                downstream.append(doc)
            elif not doc["stale"]:
                head, downstream = doc, []
            else:
                downstream.append(doc)
    stale_below = any(d["stale"] for d in downstream) or any(r["stale"] for r in reports)
    return {"head": head, "downstream_stale": stale_below, "documents": documents, "reports": reports}
