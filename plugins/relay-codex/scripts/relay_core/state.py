import json
import os
import re
import socket
import uuid
from contextlib import contextmanager
from pathlib import Path
from . import RelayError


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def storage_root(root):
    root = Path(root).resolve()
    pointer = root / ".relay" / "pointer.json"
    if pointer.exists():
        data = read_json(pointer)
        from .repository import git
        original = Path(data["root"]).resolve()
        def common(path):
            return (path / git(path, "rev-parse", "--git-common-dir")).resolve()
        if common(root) != common(original):
            raise RelayError("state", "Worktree pointer belongs to a different repository.")
        return original
    return root


class Store:
    def __init__(self, root, work_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", work_id):
            raise RelayError("state", "Invalid work ID.")
        self.root = storage_root(root)
        self.work_id = work_id
        self.path = self.root / ".relay" / "work" / work_id
        if not self.path.resolve().is_relative_to(self.root):
            raise RelayError("state", "Work path escapes its storage root.")
        self.path.mkdir(parents=True, exist_ok=True)

    def load(self):
        return read_json(self.path / "state.json")

    def save(self, state):
        write_json(self.path / "state.json", state)

    @contextmanager
    def lock(self):
        path = self.path / "lock.json"
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "host": socket.gethostname()}, stream)
        except FileExistsError as exc:
            raise RelayError("locked", "Work is locked; inspect lock.json and confirm its process ended before removing it.") from exc
        try:
            yield
        finally:
            path.unlink(missing_ok=True)


def ignore_runtime(root):
    # Repository-local exclusion avoids staging an unrelated .gitignore in user projects.
    from .repository import git
    target = Path(git(root, "rev-parse", "--git-path", "info/exclude"))
    if not target.is_absolute():
        target = Path(root) / target
    target.parent.mkdir(parents=True, exist_ok=True)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    if "/.relay/" not in text.splitlines():
        with target.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(("\n" if text and not text.endswith("\n") else "") + "/.relay/\n")


class PrStore:
    """One PR lock and request namespace shared by all connected worktrees."""
    lock = Store.lock

    def __init__(self, root):
        from .repository import common_dir, worktrees
        self.common = common_dir(root)
        trees = worktrees(root)
        if not trees or not trees[0].is_dir() or common_dir(trees[0]) != self.common or self.common != trees[0] / ".git":
            raise RelayError("state", "PR state requires a normal main worktree and its connected worktrees.")
        self.root = trees[0]
        self.path = self.root / ".relay" / "pr"
        ignore_runtime(self.root)
        self.path.mkdir(parents=True, exist_ok=True)

    def request_path(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise RelayError("input", "Invalid PR request ID.")
        return self.path / request_id

    def load(self, request_id):
        request = read_json(self.request_path(request_id) / "request.json")
        if request.get("request_id") != request_id or request.get("schema") != 1:
            raise RelayError("state", "PR request schema or directory identity differs.")
        return request

    def save(self, request):
        write_json(self.request_path(request["request_id"]) / "request.json", request)

    def pending(self):
        requests = [self.load(p.parent.name) for p in sorted(self.path.glob("*/request.json"))]
        return [request for request in requests if request.get("status") not in ("recorded", "existing")]


class ReviewStore(PrStore):
    """Independent runs, with a shared lock for both modes of each PR."""
    def __init__(self, root):
        from .repository import common_dir, worktrees
        self.common = common_dir(root)
        trees = worktrees(root)
        if not trees or common_dir(trees[0]) != self.common or self.common != trees[0] / ".git":
            raise RelayError("state", "Review state requires a normal main worktree.")
        self.root = trees[0]
        self.path = self.root / ".relay" / "review"
        ignore_runtime(self.root)
        self.path.mkdir(parents=True, exist_ok=True)

    def load(self, run_id):
        request = read_json(self.request_path(run_id) / "request.json")
        if request.get("run_id") != run_id or request.get("schema") != 1:
            raise RelayError("state", "Review schema or run identity differs.")
        return request

    def save(self, request):
        write_json(self.request_path(request["run_id"]) / "request.json", request)

    def records(self, repo, number):
        from .repository import same_repository
        return [r for p in sorted(self.path.glob("*/request.json"))
                if same_repository((r := self.load(p.parent.name))["repository"], repo) and r["pr"] == number]

    @contextmanager
    def pr_lock(self, repo, number):
        import hashlib
        key = hashlib.sha256(f"{repo.get('host', 'github.com').lower()}/{repo['repo'].lower()}/{number}".encode()).hexdigest()
        path = self.path / (key + ".lock.json")
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "host": socket.gethostname()}, stream)
        except FileExistsError as exc:
            raise RelayError("locked", "PR is locked; inspect its lock owner before recovery.") from exc
        try:
            yield
        finally:
            path.unlink(missing_ok=True)
