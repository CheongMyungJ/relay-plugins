import json
import os
import re
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from . import RelayError


class GitHub:
    def __init__(self, repo, host="github.com"):
        self.repo = repo
        self.host = host
        self.prefix = "repos/" + repo

    def api(self, endpoint, method="GET", payload=None, pages=False):
        args = ["gh", "api", endpoint, "--hostname", self.host, "--method", method]
        if pages:
            args += ["--paginate", "--slurp"]
        filename = None
        try:
            if payload is not None:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as stream:
                    json.dump(payload, stream, ensure_ascii=False)
                    filename = stream.name
                args += ["--input", filename]
            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
            if proc.returncode:
                # gh stderr can contain user-controlled response text: return a bounded message, never env.
                rejected = re.search(r"HTTP (400|401|403|404|405|413|415|422|429)\b", proc.stderr)
                raise RelayError("github_rejected" if rejected else "github", proc.stderr.strip()[:2000])
            data = json.loads(proc.stdout)
            if pages and (not isinstance(data, list) or any(not isinstance(page, list) for page in data)):
                raise RelayError("github", "gh returned an invalid paginated response.")
            return [item for page in data for item in page] if pages else data
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise RelayError("github", "gh unavailable, timed out, or returned an invalid response.") from exc
        finally:
            if filename:
                Path(filename).unlink(missing_ok=True)

    def issue(self, number):
        result = self.api(f"{self.prefix}/issues/{number}")
        if "pull_request" in result:
            raise RelayError("issue", "The supplied number identifies a pull request, not an issue.")
        return result

    def comments(self, number):
        return self.api(f"{self.prefix}/issues/{number}/comments?per_page=100", pages=True)

    def issues(self):
        return [item for item in self.api(f"{self.prefix}/issues?state=all&per_page=100", pages=True) if "pull_request" not in item]

    def pulls(self, state="open"):
        return self.api(f"{self.prefix}/pulls?state={state}&per_page=100", pages=True)

    def pull(self, number):
        return self.api(f"{self.prefix}/pulls/{number}")

    def reviews(self, number):
        return [r for r in self.api(f"{self.prefix}/pulls/{number}/reviews?per_page=100", pages=True)
                if r.get("state") != "PENDING"]

    def review_comments(self, number):
        return self.api(f"{self.prefix}/pulls/{number}/comments?per_page=100", pages=True)

    def review_comment(self, comment_id):
        return self.api(f"{self.prefix}/pulls/comments/{comment_id}")

    def comment(self, comment_id):
        return self.api(f"{self.prefix}/issues/comments/{comment_id}")

    def reply(self, number, root_id, body):
        return self.api(f"{self.prefix}/pulls/{number}/comments/{root_id}/replies", "POST", {"body": body})

    def viewer(self):
        return self.api("user")["login"]

    def item(self, number):
        """An issue or pull request as the issues endpoint returns it, without the PR check."""
        return self.api(f"{self.prefix}/issues/{number}")

    def label(self, name):
        try:
            return self.api(f"{self.prefix}/labels/{urllib.parse.quote(name, safe='')}")
        except RelayError as exc:
            if exc.code == "github_rejected" and "HTTP 404" in str(exc):
                return None
            raise

    def create_label(self, payload):
        return self.api(f"{self.prefix}/labels", "POST", payload)

    def add_labels(self, number, names):
        return self.api(f"{self.prefix}/issues/{number}/labels", "POST", {"labels": list(names)})

    def remove_label(self, number, name):
        return self.api(f"{self.prefix}/issues/{number}/labels/{urllib.parse.quote(name, safe='')}", "DELETE")

    def add_assignees(self, number, logins):
        return self.api(f"{self.prefix}/issues/{number}/assignees", "POST", {"assignees": list(logins)})

    def watched(self, login, label):
        """Open issues and pull requests carrying the label and assigned to login; PRs keep pull_request."""
        query = urllib.parse.urlencode({"state": "open", "labels": label, "assignee": login, "per_page": 100})
        return self.api(f"{self.prefix}/issues?{query}", pages=True)

    def comments_since(self, number, since):
        query = urllib.parse.urlencode({"per_page": 100, "since": since})
        return self.api(f"{self.prefix}/issues/{number}/comments?{query}", pages=True)

    def review_comments_since(self, number, since):
        query = urllib.parse.urlencode({"per_page": 100, "since": since})
        return self.api(f"{self.prefix}/pulls/{number}/comments?{query}", pages=True)

    def create_pull(self, payload):
        return self.api(f"{self.prefix}/pulls", "POST", payload)

    def update_pull(self, number, payload):
        return self.api(f"{self.prefix}/pulls/{number}", "PATCH", payload)

    def mark_ready(self, number):
        """Turn a draft PR into a ready one; REST cannot, so this goes through the gh CLI."""
        args = ["gh", "pr", "ready", str(number), "--repo", f"{self.host}/{self.repo}"]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RelayError("github", "gh unavailable or timed out.") from exc
        if proc.returncode:
            rejected = re.search(r"HTTP (400|401|403|404|405|413|415|422|429)\b", proc.stderr)
            raise RelayError("github_rejected" if rejected else "github", proc.stderr.strip()[:2000])
        return self.pull(number)

    def get_target(self, number, target):
        return self.issue(number) if target == "issue" else self.api(f"{self.prefix}/issues/comments/{target}")

    def write(self, number, target, body, title=None):
        if target == "issue" and number is None:
            return self.api(f"{self.prefix}/issues", "POST", {"title": title, "body": body})
        if target == "issue":
            return self.api(f"{self.prefix}/issues/{number}", "PATCH", {"body": body})
        if target is None:
            return self.api(f"{self.prefix}/issues/{number}/comments", "POST", {"body": body})
        return self.api(f"{self.prefix}/issues/comments/{target}", "PATCH", {"body": body})
