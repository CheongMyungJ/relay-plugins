"""File placement, state.json, INDEX and managed instruction regions (spec R1, R4 invariants).

`load` reads a whole KB from any reader (worktree, git tree or memory) into one
structure; `render` computes every file the KB owns from that structure; `verify`
checks the invariants and that the on-disk generated files equal `render`. Rendering
never assigns IDs or repairs state: an inconsistent KB is an error, not a fix-up.
"""
import json
import posixpath
import re
import subprocess
from pathlib import Path
from .. import RelayError
from ..repository import git, git_raw
from . import entries as model

ROOT = "docs/kb"
ARCHIVE = ROOT + "/archive"
INDEX = ROOT + "/INDEX.md"
STATE = ROOT + "/state.json"
RESERVED = {"INDEX", "glossary", "non-goals", "failure-classes", "state", "archive", "README"}
CAPS = {"glossary": 30, "non-goals": 30, "failure-classes": 20}
DEFAULT_CAP = 30
BEGIN, END = "<!-- relay:kb:begin -->", "<!-- relay:kb:end -->"
REGION_LIMIT = 8000
LINE_WARNING = 200
INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md")
LOOKUP_HINT = "조회: `python <plugin>/scripts/relay.py kb --input <json>`의 lookup(paths·terms·ids)"


def fail(message, code="kb"):
    raise RelayError(code, message)


def topic(entry):
    """The file stem an entry lives in: first sorted path's directory (two levels), or the type file."""
    if entry["type"] == "N":
        return "non-goals"
    if entry["type"] == "F":
        return "failure-classes"
    if entry["type"] == "S" and entry.get("subtype") == "term":
        return "glossary"
    if not entry["paths"]:
        return "decisions"
    base, _, is_dir = model.split_path(entry["paths"][0])
    parts = base.rstrip("/").split("/") if is_dir else base.split("/")[:-1]
    stem = "-".join(parts[:2]) if parts else "root"
    return "topic-" + stem if stem in RESERVED or stem.startswith("topic-") else stem


def file_for(entry, archived=False):
    return f"{ARCHIVE if archived else ROOT}/{topic(entry)}.md"


def stem_of(path):
    return posixpath.basename(path)[:-3]


def cap_for(path):
    return CAPS.get(stem_of(path), DEFAULT_CAP)


def anchor_of(heading):
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text).strip("-")


def pointer_exists(reader, pointer):
    file, _, anchor = pointer.partition("#")
    text = reader.read(file)
    if text is None:
        return False
    return any(anchor_of(line.lstrip("#")) == anchor for line in text.splitlines() if line.startswith("#"))


class MemoryReader:
    """A {path: text} snapshot; None values mark deletions in an overlay."""

    def __init__(self, files=None, base=None):
        self.files = dict(files or {})
        self.base = base

    def list(self, prefix):
        seen = set(self.files)
        if self.base is not None:
            seen |= set(self.base.list(prefix))
        return sorted(p for p in seen if p.startswith(prefix) and self.read(p) is not None)

    def read(self, path):
        if path in self.files:
            return self.files[path]
        return self.base.read(path) if self.base is not None else None

    def find(self, names):
        return [p for p in self.list("") if posixpath.basename(p) in names]


class WorktreeReader:
    def __init__(self, root):
        self.root = Path(root)

    def list(self, prefix):
        raw = subprocess.run(["git", "-C", str(self.root), "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", prefix or "."],
                             check=True, capture_output=True).stdout
        names = sorted({n.decode("utf-8") for n in raw.split(b"\0") if n})
        return [n for n in names if (self.root / n).is_file()]

    def read(self, path):
        file = self.root / path
        if not file.is_file():
            return None
        return file.read_text(encoding="utf-8").replace("\r\n", "\n")

    def find(self, names):
        return [p for p in self.list("") if posixpath.basename(p) in names]


class TreeReader:
    def __init__(self, root, sha):
        self.root, self.sha = Path(root), sha
        self.cache = {}

    def list(self, prefix):
        raw = git_raw(self.root, "ls-tree", "-r", "--name-only", "-z", self.sha, "--", prefix or ".")
        return sorted(n for n in raw.split("\0") if n)

    def read(self, path):
        if path not in self.cache:
            try:
                self.cache[path] = git_raw(self.root, "show", f"{self.sha}:{path}").replace("\r\n", "\n")
            except RelayError:
                self.cache[path] = None
        return self.cache[path]

    def find(self, names):
        return [p for p in self.list("") if posixpath.basename(p) in names]


def validate_state(state):
    if not isinstance(state, dict) or set(state) != {"schema", "confirmed", "deleted"} or state["schema"] != 1:
        fail("state.json must contain schema 1, confirmed and deleted.")
    if not isinstance(state["confirmed"], dict) or not isinstance(state["deleted"], dict):
        fail("state.json confirmed and deleted must be objects.")
    for key, sha in state["confirmed"].items():
        if not model.ID.match(key) or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            fail("state.json confirmed maps entry IDs to commit SHAs.")
    for key, tomb in state["deleted"].items():
        if (not model.ID.match(key) or not isinstance(tomb, dict) or set(tomb) != {"sha", "links"}
                or not isinstance(tomb["sha"], str) or not re.fullmatch(r"[0-9a-f]{40,64}", tomb["sha"])
                or not isinstance(tomb["links"], list) or any(not model.ID.match(l) for l in tomb["links"])):
            fail("state.json tombstones carry sha and linked entry IDs.")
    return {"schema": 1, "confirmed": dict(sorted(state["confirmed"].items())),
            "deleted": {k: {"sha": v["sha"], "links": sorted(v["links"])} for k, v in sorted(state["deleted"].items())}}


def empty():
    return {"present": False, "files": {}, "entries": {}, "location": {},
            "state": {"schema": 1, "confirmed": {}, "deleted": {}}}


def load(reader):
    """Every entry file, archive file and state.json; duplicate IDs and broken state stop here."""
    paths = reader.list(ROOT + "/")
    kb = empty()
    if not paths:
        return kb
    kb["present"] = True
    raw = reader.read(STATE)
    if raw is None:
        fail("docs/kb exists without state.json.")
    try:
        kb["state"] = validate_state(json.loads(raw))
    except ValueError as exc:
        fail("state.json is not valid JSON: " + str(exc))
    for path in paths:
        if not path.endswith(".md") or path == INDEX:
            continue
        parent = posixpath.dirname(path)
        if parent not in (ROOT, ARCHIVE):
            continue
        try:
            parsed = model.parse_file(reader.read(path))
        except RelayError as exc:
            fail(f"{path}: {exc}")
        kb["files"][path] = parsed
        for entry in parsed["entries"]:
            if entry["id"] in kb["entries"]:
                fail("Duplicate entry ID across the KB: " + entry["id"])
            kb["entries"][entry["id"]] = entry
            kb["location"][entry["id"]] = path
    for key in kb["state"]["deleted"]:
        if key in kb["entries"]:
            fail("Tombstoned entry still exists: " + key)
    return kb


def check_state(kb):
    """Active entries have confirmed SHAs; nothing else does; links are consistent and acyclic."""
    entries, confirmed = kb["entries"], kb["state"]["confirmed"]
    for key, entry in entries.items():
        if entry["status"] == "active" and key not in confirmed:
            fail("Active entry has no confirmed SHA: " + key)
        if entry["status"] != "active" and key in confirmed:
            fail("Inactive entry still has a confirmed SHA: " + key)
    for key in confirmed:
        if key not in entries:
            fail("Confirmed SHA for an unknown entry: " + key)
        if key in kb["state"]["deleted"]:
            fail("Deleted entry still confirmed: " + key)
    deleted = kb["state"]["deleted"]
    for key, entry in entries.items():
        new = entry.get("superseded_by")
        if new is not None:
            # The newer entry may have been deleted later; its tombstone keeps the link.
            if new in deleted and key in deleted[new]["links"]:
                continue
            if new not in entries or entries[new].get("supersedes") != key:
                fail(f"Supersession link is not bidirectional: {key} -> {new}")
        old = entry.get("supersedes")
        if old is not None:
            if old not in entries or entries[old]["status"] != "superseded" or entries[old].get("superseded_by") != key:
                fail(f"Supersession link is not bidirectional: {key} <- {old}")
        seen, cursor = set(), key
        while entries.get(cursor, {}).get("superseded_by"):
            if cursor in seen:
                fail("Supersession cycle at " + key)
            seen.add(cursor)
            cursor = entries[cursor]["superseded_by"]


def placement(kb):
    """Which file each entry renders into: archived entries stay archived; overflow moves inactive ones."""
    where = {}
    for key, entry in kb["entries"].items():
        archived = kb["location"].get(key, "").startswith(ARCHIVE + "/")
        where[key] = file_for(entry, archived=archived)
    counts = {}
    for path in where.values():
        counts[path] = counts.get(path, 0) + 1
    for path, count in counts.items():
        if path.startswith(ARCHIVE + "/") or count <= cap_for(path):
            continue
        for key, home in list(where.items()):
            if home == path and kb["entries"][key]["status"] != "active":
                where[key] = file_for(kb["entries"][key], archived=True)
    return where


def active_counts(kb, where=None):
    where = where or placement(kb)
    counts = {}
    for key, entry in kb["entries"].items():
        if entry["status"] == "active":
            counts[where[key]] = counts.get(where[key], 0) + 1
    return counts


class CapacityError(RelayError):
    """A capacity failure that names every over-cap file, so callers can list its entries."""

    def __init__(self, over):
        super().__init__("capacity", "; ".join(f"{path} exceeds its active cap of {cap_for(path)} ({count})." for path, count in over))
        self.paths = [path for path, _ in over]


def check_caps(kb, where=None):
    over = sorted((path, count) for path, count in active_counts(kb, where).items() if count > cap_for(path))
    if over:
        raise CapacityError(over)


def index_text(kb, where):
    active = sorted((where[k], k) for k, e in kb["entries"].items() if e["status"] == "active")
    absorbed = sorted(k for k, e in kb["entries"].items() if e["status"] == "absorbed")
    lines = ["# KB INDEX", "", "Relay가 생성하는 색인이다. 전체를 기본 컨텍스트로 보내지 않고 lookup으로 필요한 항목만 읽는다.", "", "## 활성 항목", ""]
    for path, key in active:
        entry = kb["entries"][key]
        paths = ", ".join(entry["paths"]) if entry["paths"] else "경로 없음"
        lines.append(f"- `{key}` [{stem_of(path)}]({posixpath.relpath(path, ROOT)}) {paths} — {entry['rule']}")
    if not active:
        lines.append("- 없음")
    lines += ["", "## 흡수된 항목", ""]
    for key in absorbed:
        entry = kb["entries"][key]
        lines.append(f"- `{key}` → {entry['absorbed_into']} — {entry['rule']}")
    if not absorbed:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def region_dirs(kb):
    """Directory -> sorted active constraint IDs; root-level files are pointer-only and skipped."""
    dirs = {}
    for key, entry in kb["entries"].items():
        if entry["type"] != "C" or entry["status"] != "active":
            continue
        for value in entry["paths"]:
            base, _, is_dir = model.split_path(value)
            directory = base.rstrip("/") if is_dir else posixpath.dirname(base)
            if directory:
                dirs.setdefault(directory, set()).add(key)
    return {d: sorted(ids) for d, ids in dirs.items()}


def agents_region(kb, directory, ids):
    lines = [BEGIN, f"Relay KB: `{directory}/`에 묶인 제약이다. 전체는 {INDEX}, {LOOKUP_HINT}."]
    for key in ids:
        entry = kb["entries"][key]
        lines.append(f"- {key}: {entry['rule']} ({', '.join(entry['paths'])})")
    lines.append(END)
    text = "\n".join(lines)
    if len(text) > REGION_LIMIT:
        text = "\n".join([BEGIN, f"Relay KB: `{directory}/`에 묶인 제약 {len(ids)}개가 {REGION_LIMIT:,}자를 넘어 여기에 싣지 않는다. "
                          f"paths=[\"{directory}/\"]로 lookup해 읽는다. 전체는 {INDEX}.", END])
    return text


def root_agents_region():
    return "\n".join([BEGIN, f"Relay KB: {INDEX}에 경로별 결정·제약·구조 사실·검증 레시피·비목표·실패 부류가 있다. "
                      f"파일을 편집하기 전에 lookup에 경로·용어를 넣어 관련 항목만 읽는다({LOOKUP_HINT}). 루트 파일의 제약도 lookup으로 받는다.", END])


def claude_region():
    return "\n".join([BEGIN, "@AGENTS.md", END])


def split_region(text):
    """(before, region_or_None, after) with byte-exact outer text; unpaired or duplicate markers fail."""
    if text.count(BEGIN) == 0 and text.count(END) == 0:
        return text, None, ""
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        fail("Duplicate or unpaired relay:kb markers.")
    start, stop = text.index(BEGIN), text.index(END)
    if stop < start:
        fail("relay:kb markers are out of order.")
    return text[:start], text[start:stop + len(END)], text[stop + len(END):]


def splice(existing, region):
    """Insert or replace the managed region; None region removes it and returns None for an empty file."""
    if existing is None:
        return None if region is None else region + "\n"
    before, current, after = split_region(existing)
    if region is None:
        if current is None:
            return existing
        rest = before + (after[1:] if after.startswith("\n") else after)
        return rest if rest.strip() else None
    if current is None:
        return existing.rstrip("\n") + "\n\n" + region + "\n" if existing.strip() else region + "\n"
    return before + region + after


def render(kb, reader):
    """Every generated file as {path: text or None}; unchanged files are included for comparison."""
    if not kb["present"] and not kb["entries"]:
        return {"files": {}, "warnings": []}
    check_state(kb)
    where = placement(kb)
    check_caps(kb, where)
    files, warnings = {}, []
    grouped = {}
    for key, path in where.items():
        grouped.setdefault(path, []).append(kb["entries"][key])
    for path, items in grouped.items():
        items.sort(key=lambda e: e["id"])
        preamble = kb["files"].get(path, {}).get("preamble") or (
            f"# archive: {stem_of(path)}\n" if path.startswith(ARCHIVE + "/") else f"# {stem_of(path)}\n")
        files[path] = model.render_file(items, preamble)
    for path in kb["files"]:
        if path not in files:
            files[path] = None
    files[INDEX] = index_text(kb, where)
    files[STATE] = json.dumps(kb["state"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    regions = {"": (root_agents_region(), claude_region())} if kb["present"] or kb["entries"] else {}
    for directory, ids in region_dirs(kb).items():
        regions[directory] = (agents_region(kb, directory, ids), claude_region())
    managed = set()
    for directory, (agents, claude) in regions.items():
        for name, region in ((INSTRUCTIONS[0], agents), (INSTRUCTIONS[1], claude)):
            path = posixpath.join(directory, name) if directory else name
            managed.add(path)
            files[path] = splice(reader.read(path), region)
    for path in reader.find(set(INSTRUCTIONS)):
        if path in managed:
            continue
        text = reader.read(path)
        if text is not None and BEGIN in text:
            files[path] = splice(text, None)
    for path, text in files.items():
        if text is not None and text.count("\n") > LINE_WARNING:
            warnings.append(f"{path} exceeds {LINE_WARNING} lines; shorten user text or split entries.")
    return {"files": files, "warnings": warnings}


def verify(kb, reader):
    """Invariants plus generated files equal to render; returns warnings."""
    rendered = render(kb, reader)
    for key, entry in kb["entries"].items():
        if entry["status"] == "absorbed" and not pointer_exists(reader, entry["absorbed_into"]):
            fail(f"Absorbed pointer does not resolve: {entry['absorbed_into']} ({key})")
    for path, text in rendered["files"].items():
        if reader.read(path) != text:
            fail(f"Generated file differs from render: {path}")
    return rendered["warnings"]


def digest_of(kb):
    """A content digest over entries and state, for cursors and change plans."""
    import hashlib
    payload = json.dumps({"entries": kb["entries"], "state": kb["state"], "location": kb["location"]},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
