"""One state file per user, written atomically under an exclusive lock file."""
import datetime
import json
import os
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from relay_core import RelayError
from relay_core.state import read_json, write_json

EMPTY = {"schema": 1, "processed": {}, "sessions": {}, "pending": {}, "settling": {}, "cursors": {},
         "watched": {}, "last_cycle": None}


def pid_alive(pid):
    """Whether a process with this ID exists here. It only asks; it never signals or terminates."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def iso(when):
    return when.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse(text):
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def item_key(slug, number):
    return f"{slug}#{number}"


def artifact_key(slug, target, digest):
    return f"{slug}#{target}@{digest}"


class Ledger:
    def __init__(self, home):
        self.home = Path(home)
        self.file = self.home / "state.json"
        self.lock_file = self.home / "state.lock"
        self.data = None

    def load(self):
        self.data = read_json(self.file) if self.file.exists() else json.loads(json.dumps(EMPTY))
        for key, value in EMPTY.items():
            self.data.setdefault(key, json.loads(json.dumps(value)))
        return self.data

    def save(self):
        write_json(self.file, self.data)

    @contextmanager
    def locked(self, timeout=5.0):
        self.home.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(self.lock_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise RelayError("locked", self.holder_message())
                time.sleep(0.1)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "host": socket.gethostname()}, stream)
        try:
            yield self.load()
        finally:
            self.lock_file.unlink(missing_ok=True)

    def holder_message(self):
        """Tell a busy `run` apart from a lock left behind by a process that died."""
        try:
            holder = json.loads(self.lock_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            holder = {}
        pid, host = holder.get("pid"), holder.get("host")
        if host == socket.gethostname() and pid_alive(pid):
            return (f"{self.lock_file} is held by relay-dispatch pid {pid}, which is still running "
                    "(probably `run` in the middle of a cycle); try again in a moment and do not remove the lock")
        return (f"{self.lock_file} is held by relay-dispatch pid {pid} on {host}, which is not running here; "
                "if no relay-dispatch process is alive, remove the lock file")

    # processed artifacts
    def processed(self, key):
        return key in self.data["processed"]

    def mark_processed(self, key, when, reason):
        self.data["processed"][key] = {"at": iso(when), "reason": reason}

    # sessions
    def session(self, item):
        entry = self.data["sessions"].get(item)
        return entry if entry and entry.get("status") == "open" else None

    def open_session(self, item, info):
        self.data["sessions"][item] = dict(info, status="open")

    def close_session(self, item, when, reason):
        entry = self.data["sessions"].get(item)
        if entry and entry.get("status") == "open":
            entry.update(status="done", done_at=iso(when), done_reason=reason)
            return True
        return False

    def open_sessions(self):
        return {k: v for k, v in self.data["sessions"].items() if v.get("status") == "open"}

    # pending gate entries
    def pending(self, item):
        return self.data["pending"].get(item)

    def set_pending(self, item, entry):
        self.data["pending"][item] = entry

    def pop_pending(self, item):
        return self.data["pending"].pop(item, None)

    # settling wait
    def settling(self, item):
        return self.data["settling"].get(item)

    def set_settling(self, item, when):
        self.data["settling"][item] = iso(when)

    def clear_settling(self, item):
        self.data["settling"].pop(item, None)

    # cursors
    def cursor(self, key):
        return self.data["cursors"].get(key)

    def set_cursor(self, key, when):
        self.data["cursors"][key] = iso(when)
