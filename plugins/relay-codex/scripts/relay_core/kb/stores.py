"""Local state for PR mode (`.relay/kb/pr/<n>/<request_id>/`) and sync mode (`.relay/kb/sync/<run_id>/`).

Both live in the normal main worktree, found through the Git common directory like the
PR and review stores, and reuse the same O_EXCL lock files. PR mode locks per PR and
keeps a current-request pointer; sync mode locks per run.
"""
import json
import os
import re
import socket
from contextlib import contextmanager
from .. import RelayError
from ..repository import common_dir, worktrees
from ..state import read_json, write_json, ignore_runtime


def main_worktree(root, label):
    common = common_dir(root)
    trees = worktrees(root)
    if not trees or not trees[0].is_dir() or common_dir(trees[0]) != common or common != trees[0] / ".git":
        raise RelayError("state", label + " state requires a normal main worktree and its connected worktrees.")
    return trees[0], common


@contextmanager
def exclusive(path, message):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "host": socket.gethostname()}, stream)
    except FileExistsError as exc:
        raise RelayError("locked", message) from exc
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{12,32}", value):
        raise RelayError("input", "Invalid " + label + ".")
    return value


class KbPrStore:
    def __init__(self, root):
        self.root, self.common = main_worktree(root, "KB PR")
        self.path = self.root / ".relay" / "kb" / "pr"
        ignore_runtime(self.root)
        self.path.mkdir(parents=True, exist_ok=True)

    def pr_path(self, number):
        if type(number) is not int or number < 1:
            raise RelayError("input", "PR number must be a positive integer.")
        return self.path / str(number)

    def request_path(self, number, request_id):
        return self.pr_path(number) / identifier(request_id, "KB request ID")

    def lock(self, number):
        return exclusive(self.pr_path(number) / "lock.json", "PR is locked for KB work; inspect lock.json before recovery.")

    def current(self, number):
        pointer = self.pr_path(number) / "current.json"
        return read_json(pointer)["request_id"] if pointer.exists() else None

    def set_current(self, number, request_id):
        write_json(self.pr_path(number) / "current.json", {"request_id": request_id})

    def load(self, number, request_id):
        path = self.request_path(number, request_id) / "request.json"
        if not path.exists():
            raise RelayError("state", "No KB request " + request_id + " exists for PR " + str(number) + ".")
        request = read_json(path)
        if request.get("request_id") != request_id or request.get("pr") != number or request.get("schema") != 1:
            raise RelayError("state", "KB request identity differs from its directory.")
        return request

    def save(self, request):
        write_json(self.request_path(request["pr"], request["request_id"]) / "request.json", request)

    def requests(self, number):
        folder = self.pr_path(number)
        return sorted(p.parent.name for p in folder.glob("*/request.json")) if folder.exists() else []


class KbSyncStore:
    def __init__(self, root):
        self.root, self.common = main_worktree(root, "KB sync")
        self.path = self.root / ".relay" / "kb" / "sync"
        ignore_runtime(self.root)
        self.path.mkdir(parents=True, exist_ok=True)

    def run_path(self, run_id):
        return self.path / identifier(run_id, "KB run ID")

    def lock(self, run_id):
        return exclusive(self.run_path(run_id) / "lock.json", "KB run is locked; inspect lock.json before recovery.")

    def load(self, run_id):
        path = self.run_path(run_id) / "run.json"
        if not path.exists():
            raise RelayError("state", "No KB run " + run_id + " exists.")
        run = read_json(path)
        if run.get("run_id") != run_id or run.get("schema") != 1:
            raise RelayError("state", "KB run identity differs from its directory.")
        return run

    def save(self, run):
        write_json(self.run_path(run["run_id"]) / "run.json", run)

    def runs(self):
        return sorted(p.parent.name for p in self.path.glob("*/run.json"))

    def unfinished(self):
        return [r for r in (self.load(i) for i in self.runs()) if r.get("status") not in ("finished", "failed")]
