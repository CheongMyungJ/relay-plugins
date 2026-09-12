"""Read-only, content-addressed PR evidence. Missing evidence stays explicit."""
import copy
import json
import re
from datetime import datetime, timezone
from . import RelayError
from .artifacts import collect, digest, reference
from .runs import parse_evidence
from . import review_git
from .github import GitHub


def hashed(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def comment(value, kind):
    keys = ("id", "html_url", "body", "in_reply_to_id", "pull_request_review_id", "path", "line", "original_line",
            "commit_id", "original_commit_id", "diff_hunk", "state", "submitted_at", "updated_at")
    result = {k: value.get(k) for k in keys if k in value}
    result.update(kind=kind, author=value.get("user", {}).get("login"), body=value.get("body") or "")
    result["key"] = f"{kind}:{result['id']}"
    result["body_hash"] = digest(result["body"])
    return result


def snapshot(repo, gh, number, run_id, include_git=True):
    pull = gh.pull(number)
    expected_url = f"https://{repo.get('host', 'github.com')}/{repo['repo']}/pull/{number}"
    if pull.get("number") != number or pull.get("html_url", "").lower() != expected_url.lower():
        raise RelayError("conflict", "PR identity does not match the requested repository and number.")
    fixed = {key: pull.get(key) for key in ("number", "html_url", "title", "body", "state", "merged")}
    for side in ("head", "base"):
        value = pull[side]
        fixed[side] = {"ref": value["ref"], "sha": value["sha"], "repo": {"full_name": (value.get("repo") or {}).get("full_name")}}
    result = {"repository": {"host": repo.get("host", "github.com"), "repo": repo["repo"]}, "pull": fixed,
              "collected_at": datetime.now(timezone.utc).isoformat(), "comments": [], "completeness": {}, "warnings": [], "linked_issues": []}
    for kind, read in (("issue", gh.comments), ("review", gh.reviews), ("inline", gh.review_comments)):
        try:
            values = [comment(v, kind) for v in read(number) if kind != "review" or v.get("state") != "PENDING"]
            if len({v["key"] for v in values}) != len(values):
                raise RelayError("github", "Duplicate comment IDs in pagination.")
            result["comments"].extend(values)
            result["completeness"][kind] = True
        except RelayError as exc:
            result["completeness"][kind] = False
            result["warnings"].append(f"{kind}: {exc}")
    inline = {c["id"]: c for c in result["comments"] if c["kind"] == "inline"}
    for item in inline.values():
        root, seen = item, set()
        while root.get("in_reply_to_id") and root["id"] not in seen:
            seen.add(root["id"])
            root = inline.get(root["in_reply_to_id"])
            if root is None:
                break
        item["root_id"] = root["id"] if root and not root.get("in_reply_to_id") else None
        if item["root_id"] is None:
            result["completeness"]["inline"] = False
            result["warnings"].append(f"Missing/cyclic inline root for {item['id']}")
    result["comments"].sort(key=lambda c: (c["kind"], c["id"]))
    # Only explicit references; do not recursively crawl conversations.
    body = fixed["body"] or ""
    numbers = {int(n) for n in re.findall(r"(?<![\w/])#([1-9][0-9]*)\b", body)}
    references = {(repo["repo"], n) for n in numbers}
    references.update((name, int(n)) for name, n in re.findall(r"(?<![\w/])([\w.-]+/[\w.-]+)#([1-9][0-9]*)\b", body))
    references.update((name, int(n)) for name, n in re.findall(re.escape(f"https://{repo.get('host', 'github.com')}/") + r"([\w.-]+/[\w.-]+)/issues/([1-9][0-9]*)\b", body))
    for linked_repo, linked in sorted(references):
        url = f"https://{repo.get('host', 'github.com')}/{linked_repo}/issues/"
        linked_gh = gh if linked_repo.lower() == repo["repo"].lower() else GitHub(linked_repo, host=repo.get("host", "github.com"))
        entry = {"repository": linked_repo, "number": linked, "url": url + str(linked), "applicability": "host judgment required"}
        try:
            issue = linked_gh.issue(linked)
            entry["body"] = issue.get("body") or ""
            records, runs = collect({**issue, "body": entry["body"]}, linked_gh.comments(linked))
            entry["documents"] = {k: {"body": r["body"], "reference": reference(r), "stale": r["stale"],
                                      "url": entry["url"] + ("" if r["target"] == "issue" else "#issuecomment-" + r["target"])} for k, r in records.items()}
            entry["runs"] = {}
            for run_id_key, record in runs.items():
                try:
                    evidence = parse_evidence(record, run_id_key)
                    entry["runs"][run_id_key] = {"evidence": evidence, "head_matches": evidence.get("commit") == fixed["head"]["sha"]}
                except (RelayError, ValueError, KeyError, TypeError) as exc:
                    entry["runs"][run_id_key] = {"warning": str(exc)}
        except RelayError as exc:
            entry["warning"] = str(exc)
        result["linked_issues"].append(entry)
    if include_git:
        result["code"] = review_git.evidence(repo, fixed, run_id)
    result["hash"] = comparison_hash(result)
    return result


def comparable(value):
    return {key: copy.deepcopy(value[key]) for key in ("repository", "pull", "comments", "completeness", "linked_issues")}


def comparison_hash(value):
    return hashed(comparable(value))


def check(expected, current, operations=(), application=None):
    before, after = comparable(expected), comparable(current)
    if application and application.get("status") == "pushed":
        before["pull"]["head"]["sha"] = application["remote_sha"]
        for issue in before["linked_issues"]:
            for run in issue.get("runs", {}).values():
                if "evidence" in run:
                    run["head_matches"] = run["evidence"].get("commit") == application["remote_sha"]
    for op in operations:
        if op["status"] != "recorded":
            continue
        found = [c for c in after["comments"] if c["kind"] == op["kind"] and c["id"] == op["id"]]
        if len(found) != 1 or found[0]["body"] != op["body"] or found[0]["author"] != op["author"]:
            raise RelayError("conflict", "Previously published comment changed or disappeared; its success is retained.")
        after["comments"].remove(found[0])
    if before != after:
        raise RelayError("stale", "PR, source, base, comments or linked evidence changed; reassess the snapshot.")
    if current["pull"]["state"] != "open" or current["pull"].get("merged"):
        raise RelayError("closed", "Closed or merged PRs support drafts only.")
    if not all(current["completeness"].values()):
        raise RelayError("incomplete", "Comment collection is incomplete; do not publish.")
