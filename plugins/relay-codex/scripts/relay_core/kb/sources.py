"""Source strings: parsing plus existence and state checks with a per-call memo (spec R5).

`issue:n/comment:id` must decode as a Relay document or execution report, `pr:n` must
be merged, `pr:n/comment:id`, `pr:n/inline:id` and `pr:n/review:id` must belong to that
PR with a nonempty body (state `review` when it carries the relay:review marker,
otherwise `comment`), and `path:p@sha` must exist at that commit. PR mode approves every
candidate by hand, so a marker adds no trust and would only exclude human review
reasoning. Each (endpoint, id) is read once per call.
"""
import re
from .. import RelayError
from ..artifacts import decode, digest
from ..repository import git
from . import entries as model

REVIEW_MARKER = "<!-- relay:review"


def parse(value):
    model.source(value)
    if value.startswith("issue:"):
        issue, comment = re.match(r"issue:(\d+)/comment:(\d+)$", value).groups()
        return {"kind": "issue_comment", "number": int(issue), "id": int(comment)}
    if value.startswith("path:"):
        path, sha = value[5:].rsplit("@", 1)
        return {"kind": "path", "path": path, "sha": sha}
    found = re.match(r"pr:(\d+)(?:/(comment|inline|review):(\d+))?$", value)
    number, kind, ident = found.groups()
    if kind is None:
        return {"kind": "pr", "number": int(number)}
    return {"kind": "pr_" + kind, "number": int(number), "id": int(ident)}


class Sources:
    """Check sources against GitHub and Git, remembering every read for the life of one call."""

    def __init__(self, gh, root, base_sha=None):
        self.gh, self.root, self.base_sha = gh, root, base_sha
        self.memo = {}

    def remember(self, key, read):
        if key not in self.memo:
            try:
                self.memo[key] = ("ok", read())
            except RelayError as exc:
                self.memo[key] = ("error", exc)
        status, value = self.memo[key]
        if status == "error":
            raise value
        return value

    def check(self, value, *, new=True):
        """{source, kind, exists, valid, state, reason, digest, meta}; never raises for a missing object."""
        parsed = parse(value)
        result = {"source": value, "kind": parsed["kind"], "exists": False, "valid": False, "state": None,
                  "reason": None, "digest": None, "meta": None}
        try:
            if parsed["kind"] == "issue_comment":
                obj = self.remember(("comment", parsed["id"]), lambda: self.gh.comment(parsed["id"]))
                result["exists"] = True
                if not str(obj.get("issue_url", "")).endswith("/issues/" + str(parsed["number"])):
                    result["reason"] = "comment belongs to another issue"
                    return result
                record = decode(obj.get("body") or "", str(parsed["id"]))
                if not record:
                    result["reason"] = "comment carries no Relay metadata"
                    return result
                result.update(valid=True, state=record["meta"]["kind"], digest=digest(obj.get("body") or ""),
                              meta={"kind": record["meta"]["kind"], "version": record["meta"]["version"], "hash": record["meta"]["hash"]})
            elif parsed["kind"] == "pr":
                pull = self.remember(("pull", parsed["number"]), lambda: self.gh.pull(parsed["number"]))
                result["exists"] = True
                merged = bool(pull.get("merged") or pull.get("merged_at"))
                result.update(state="merged" if merged else pull.get("state"), digest=digest(pull.get("body") or ""))
                if not merged:
                    result["reason"] = "PR is not merged"
                    return result
                result["valid"] = True
            elif parsed["kind"] in ("pr_comment", "pr_inline"):
                inline = parsed["kind"] == "pr_inline"
                key = ("review_comment" if inline else "comment", parsed["id"])
                obj = self.remember(key, lambda: self.gh.review_comment(parsed["id"]) if inline else self.gh.comment(parsed["id"]))
                result["exists"] = True
                owner = str(obj.get("pull_request_url" if inline else "issue_url", ""))
                if not owner.endswith(("/pulls/" if inline else "/issues/") + str(parsed["number"])):
                    result["reason"] = "comment belongs to another pull request"
                    return result
                body = obj.get("body") or ""
                if not body.strip():
                    result["reason"] = "comment body is empty"
                    return result
                result.update(valid=True, state="review" if REVIEW_MARKER in body else "comment", digest=digest(body))
            elif parsed["kind"] == "pr_review":
                reviews = self.remember(("reviews", parsed["number"]), lambda: self.gh.reviews(parsed["number"]))
                found = [r for r in reviews if r.get("id") == parsed["id"]]
                if not found:
                    result["reason"] = "review is not on that pull request"
                    return result
                result["exists"] = True
                body = found[0].get("body") or ""
                if not body.strip():
                    result["reason"] = "review body is empty"
                    return result
                result.update(valid=True, state="review" if REVIEW_MARKER in body else "comment", digest=digest(body))
            else:
                base = model.split_path(parsed["path"])[0]
                exists = self.remember(("object", parsed["sha"], base), lambda: self.object_exists(parsed["sha"], base))
                result["exists"] = exists
                if not exists:
                    result["reason"] = "path does not exist at that commit or the commit is unreadable"
                    return result
                if new and self.base_sha and not self.remember(("ancestor", parsed["sha"]), lambda: self.is_ancestor(parsed["sha"])):
                    result.update(state="not-ancestor", reason="new path sources must cite the base SHA or one of its ancestors")
                    return result
                result.update(valid=True, state="present")
        except RelayError as exc:
            result["reason"] = exc.code + ": " + str(exc)[:200]
        return result

    def object_exists(self, sha, path):
        try:
            git(self.root, "cat-file", "-e", f"{sha}:{path.rstrip('/')}")
            return True
        except RelayError:
            return False

    def is_ancestor(self, sha):
        try:
            git(self.root, "merge-base", "--is-ancestor", sha, self.base_sha)
            return True
        except RelayError:
            return False

    def check_all(self, values, *, new=None):
        """Results per source; `new` names the subset that must exist at the base SHA or an ancestor."""
        new = set(values) if new is None else set(new)
        return {value: self.check(value, new=value in new) for value in values}
