"""Entry model: Markdown blocks <-> JSON, identity, limits and per-type requirements (spec R2).

An entry is one `### <ID> <rule>` heading followed by `- <field>: <value>` lines. The
meta fields come first in a fixed order, then the narrative fields in a fixed order;
absent optional fields are omitted. The writer and parser round-trip exactly, so a
rendered file re-parses to the same JSON and renders to the same bytes again.
"""
import re
import unicodedata
from .. import RelayError

ID = re.compile(r"^[DCSVNF]-[0-9a-f]{32}$")
TYPES = {"D": "결정", "C": "제약", "S": "구조 사실", "V": "검증 레시피", "N": "비목표", "F": "실패 부류"}
LABELS = {label: letter for letter, label in TYPES.items()}
TERM_LABEL = "구조 사실(용어)"
STATUS = {"active": "유효", "superseded": "대체됨", "absorbed": "흡수됨"}
STATUS_LABELS = {label: key for key, label in STATUS.items()}
COMPAT_LABEL = "외부게시"
# Meta fields in rendering order, then narrative fields in rendering order.
META_ORDER = ("유형", "상태", "호환", "blocking", "경로", "용어", "출처", "대체", "대체함", "흡수")
NARRATIVE = (("이유", "why"), ("기각", "rejected_alt"), ("코드불가", "not_in_code"), ("유인", "incentive"),
             ("명령", "command"), ("정의", "definition"), ("비고", "note"))
NARRATIVE_KEYS = dict(NARRATIVE)
NARRATIVE_LABELS = {key: label for label, key in NARRATIVE}
LIMITS = {"rule": 200, "narrative": 400, "entry": 4000, "lines": 5, "paths": 12, "path": 240,
          "terms": (2, 5), "term": 40, "sources": (1, 8)}
SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")
SOURCE = re.compile(r"^(?:issue:[1-9][0-9]*/comment:[1-9][0-9]*"
                    r"|pr:[1-9][0-9]*(?:/(?:comment|inline|review):[1-9][0-9]*)?"
                    r"|path:[^@\s,]+@[0-9a-f]{40,64})$")
POINTER = re.compile(r"^references/[A-Za-z0-9_.-]+\.md#[A-Za-z0-9_.-]+$")
HEADING = re.compile(r"^### ([DCSVNF]-[0-9a-f]{32}) (.+)$")
FIELD = re.compile(r"^- ([^:]+): (.*)$")
HOST_KEYS = {"type", "subtype", "rule", "paths", "terms", "sources", "why", "rejected_alt", "not_in_code",
             "incentive", "compat", "command", "definition", "blocking", "note"}
MANAGED_KEYS = {"id", "status", "superseded_by", "supersedes", "absorbed_into"}


def fail(message):
    raise RelayError("kb", message)


def line(value, label, limit):
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} must be nonempty text.")
    if value != value.strip() or any(c < " " or c == "\x7f" for c in value):
        fail(f"{label} must be one trimmed line without control characters.")
    if len(value) > limit:
        fail(f"{label} exceeds {limit} characters.")
    return value


def term(value):
    if not isinstance(value, str):
        fail("Terms must be strings.")
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not normalized or "," in normalized or len(normalized) > LIMITS["term"]:
        fail("Each term is 1-40 characters without commas: " + repr(value))
    return normalized


def path(value):
    """`dir/`, `dir/file.ext` or `dir/file.py:Class.method`; slashes only, inside the repository."""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > LIMITS["path"]:
        fail("Path must be trimmed text up to 240 characters: " + repr(value))
    if "\\" in value or "," in value or any(c < " " for c in value) or value.startswith("/") or re.match(r"[A-Za-z]:", value):
        fail("Paths use forward slashes, no commas, and are repository-relative: " + value)
    base, sep, symbol = value.rpartition(":")
    if sep:
        if base.endswith("/") or not SYMBOL.match(symbol):
            fail("A symbol path is dir/file.ext:Symbol or :Class.method: " + value)
    else:
        base = value
    parts = base.split("/")
    directory = base.endswith("/")
    if directory:
        parts = parts[:-1]
    if not parts or any(not part or part in (".", "..", ".git") or part != part.strip() for part in parts):
        fail("Path segments must be nonempty and cannot be ., .. or .git: " + value)
    return value


def split_path(value):
    """(file_or_dir, symbol_or_None, is_directory) for a validated path."""
    base, sep, symbol = value.rpartition(":")
    if not sep:
        return value, None, value.endswith("/")
    return base, symbol, False


def source(value):
    if not isinstance(value, str) or not SOURCE.match(value):
        fail("Source must be issue:n/comment:id, pr:n, pr:n/comment:id, pr:n/inline:id, pr:n/review:id or path:p@sha: " + repr(value))
    if value.startswith("path:"):
        path(value[5:].rsplit("@", 1)[0])
    return value


def unique_sorted(values, label, check):
    if not isinstance(values, list):
        fail(label + " must be a list.")
    cleaned = [check(v) for v in values]
    if len(set(cleaned)) != len(cleaned):
        fail(label + " contains duplicates.")
    return sorted(cleaned)


def validate(entry, *, managed=False):
    """Return the canonical entry. Host input omits id/status/links; managed entries carry them."""
    if not isinstance(entry, dict):
        fail("Entry must be an object.")
    unknown = set(entry) - HOST_KEYS - (MANAGED_KEYS if managed else set())
    if unknown:
        fail("Unknown entry fields: " + ", ".join(sorted(unknown)))
    kind = entry.get("type")
    if kind not in TYPES:
        fail("type must be one of D, C, S, V, N, F.")
    subtype = entry.get("subtype")
    if subtype not in (None, "term") or (subtype == "term" and kind != "S"):
        fail("subtype is only S term.")
    result = {"type": kind, "subtype": subtype, "rule": line(entry.get("rule"), "rule", LIMITS["rule"])}
    result["paths"] = unique_sorted(entry.get("paths", []), "paths", path)
    if len(result["paths"]) > LIMITS["paths"]:
        fail("At most 12 paths per entry.")
    result["terms"] = unique_sorted(entry.get("terms", []), "terms", term)
    if not LIMITS["terms"][0] <= len(result["terms"]) <= LIMITS["terms"][1]:
        fail("An entry declares 2-5 terms.")
    result["sources"] = unique_sorted(entry.get("sources", []), "sources", source)
    if not LIMITS["sources"][0] <= len(result["sources"]) <= LIMITS["sources"][1]:
        fail("An entry cites 1-8 sources.")
    lines = 1
    for label, key in NARRATIVE:
        value = entry.get(key)
        if value is None:
            result[key] = None
            continue
        result[key] = line(value, label, LIMITS["narrative"])
        lines += 1
    if lines > LIMITS["lines"]:
        fail("Rule plus narrative lines exceed five lines; split the entry or use references.")
    for flag in ("compat", "blocking"):
        value = entry.get(flag, False)
        if type(value) is not bool:
            fail(flag + " must be true or false.")
        result[flag] = value
    if result["compat"] and kind != "C":
        fail("Only a C entry can be marked compat.")
    if result["blocking"] and kind != "F":
        fail("Only an F entry can be blocking.")
    for key in ("not_in_code", "incentive"):
        if not result[key]:
            fail("Every entry needs " + NARRATIVE_LABELS[key] + " (" + key + ").")
    if kind == "D" and not (result["why"] and result["rejected_alt"]):
        fail("A decision needs 이유 and 기각.")
    if kind in ("C", "V") and not result["paths"]:
        fail("C and V entries need at least one path.")
    if kind == "S" and subtype != "term" and len(result["paths"]) < 2:
        fail("A structure fact needs two or more paths.")
    if subtype == "term" and (not result["definition"] or result["paths"]):
        fail("A glossary term needs 정의 and no paths.")
    if kind == "V" and not result["command"]:
        fail("A recipe needs 명령.")
    if kind == "N" and result["paths"]:
        fail("A non-goal has no paths.")
    if kind == "F" and len(result["sources"]) < 2 and not result["blocking"]:
        fail("A failure class needs two sources or blocking true.")
    if all(s.startswith("path:") for s in result["sources"]) and (result["why"] or result["rejected_alt"]):
        fail("Path-only sources cannot justify 이유/기각; cite a document or leave them empty.")
    if managed:
        identity = entry.get("id")
        if not isinstance(identity, str) or not ID.match(identity) or identity[0] != kind:
            fail("Entry ID must be <type>-<32 hex> and match its type.")
        status = entry.get("status", "active")
        if status not in STATUS:
            fail("status must be active, superseded or absorbed.")
        links = {key: entry.get(key) for key in ("superseded_by", "supersedes", "absorbed_into")}
        for key in ("superseded_by", "supersedes"):
            if links[key] is not None and (not isinstance(links[key], str) or not ID.match(links[key]) or links[key] == identity):
                fail(key + " must be another entry ID.")
        if links["absorbed_into"] is not None and (not isinstance(links["absorbed_into"], str) or not POINTER.match(links["absorbed_into"])):
            fail("absorbed_into must be references/<file>.md#<anchor>.")
        if (status == "superseded") != (links["superseded_by"] is not None):
            fail("A superseded entry carries 대체 and nothing else does.")
        if (status == "absorbed") != (links["absorbed_into"] is not None):
            fail("An absorbed entry carries 흡수 and nothing else does.")
        result.update(id=identity, status=status, **links)
    return result


def block(entry):
    """Render one validated managed entry as its Markdown block (without trailing blank line)."""
    entry = validate(entry, managed=True)
    label = TERM_LABEL if entry["subtype"] == "term" else TYPES[entry["type"]]
    head = f"- 유형: {label} | 상태: {STATUS[entry['status']]}"
    if entry["compat"]:
        head += " | 호환: " + COMPAT_LABEL
    lines = [f"### {entry['id']} {entry['rule']}", head]
    if entry["blocking"]:
        lines.append("- blocking: true")
    for label, key in (("경로", "paths"), ("용어", "terms"), ("출처", "sources")):
        if entry[key]:
            lines.append(f"- {label}: " + ", ".join(entry[key]))
    for label, key in (("대체", "superseded_by"), ("대체함", "supersedes"), ("흡수", "absorbed_into")):
        if entry[key]:
            lines.append(f"- {label}: {entry[key]}")
    for label, key in NARRATIVE:
        if entry[key]:
            lines.append(f"- {label}: {entry[key]}")
    text = "\n".join(lines) + "\n"
    if len(text) > LIMITS["entry"]:
        fail("Entry exceeds 4000 characters: " + entry["id"])
    return text


def parse_block(lines, where=""):
    """One heading plus field lines -> managed entry JSON; damaged or duplicate fields are errors."""
    found = HEADING.match(lines[0])
    if not found:
        fail("Damaged entry heading" + where + ": " + lines[0][:80])
    identity, rule = found.group(1), found.group(2)
    fields = {}
    for raw in lines[1:]:
        if not raw.strip():
            continue
        match = FIELD.match(raw)
        if not match:
            fail(f"Damaged entry line in {identity}: {raw[:80]}")
        fields.setdefault("_order", []).append(match.group(1))
        if match.group(1) in fields:
            fail(f"Duplicate field {match.group(1)} in {identity}")
        fields[match.group(1)] = match.group(2)
    order = fields.pop("_order", [])
    if not order or order[0] != "유형":
        fail(f"Entry {identity} must start with the 유형 line.")
    # The captured value of the 유형 line is `<label> | 상태: <status>[ | 호환: 외부게시]`.
    parts = fields.pop("유형").split(" | ")
    label, head = parts[0], {}
    for part in parts[1:]:
        key, sep, value = part.partition(": ")
        if not sep or key in head or key not in ("상태", "호환") or not value:
            fail(f"Damaged type line in {identity}")
        head[key] = value
    if list(head) != ["상태"] + (["호환"] if "호환" in head else []):
        fail(f"Damaged type line order in {identity}")
    if label == TERM_LABEL:
        kind, subtype = "S", "term"
    elif label in LABELS:
        kind, subtype = LABELS[label], None
    else:
        fail(f"Unknown type label {label!r} in {identity}")
    if head["상태"] not in STATUS_LABELS:
        fail(f"Unknown status {head['상태']!r} in {identity}")
    if "호환" in head and head["호환"] != COMPAT_LABEL:
        fail(f"Unknown compat value in {identity}")
    known = set(META_ORDER) | set(NARRATIVE_KEYS)
    unknown = set(fields) - known
    if unknown:
        fail(f"Unknown fields in {identity}: " + ", ".join(sorted(unknown)))
    expected = [name for name in META_ORDER[3:] if name in fields] + [label for label, _ in NARRATIVE if label in fields]
    if order[1:] != expected:
        fail(f"Fields out of order in {identity}; the writer's order is required for round-trip.")
    entry = {"id": identity, "type": kind, "subtype": subtype, "rule": rule,
             "status": STATUS_LABELS[head["상태"]], "compat": "호환" in head, "blocking": False}
    if "blocking" in fields:
        if fields["blocking"] != "true":
            fail(f"blocking is only recorded as true in {identity}")
        entry["blocking"] = True
    for label, key in (("경로", "paths"), ("용어", "terms"), ("출처", "sources")):
        entry[key] = fields[label].split(", ") if label in fields else []
    for label, key in (("대체", "superseded_by"), ("대체함", "supersedes"), ("흡수", "absorbed_into")):
        entry[key] = fields.get(label)
    for label, key in NARRATIVE:
        entry[key] = fields.get(label)
    return validate(entry, managed=True)


def parse_file(text):
    """Split a KB Markdown file into its preamble and entries; blocks start at `### `."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    starts = [i for i, raw in enumerate(lines) if raw.startswith("### ")]
    preamble = "\n".join(lines[:starts[0]]) if starts else text
    entries = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        chunk = lines[start:end]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        entries.append(parse_block(chunk))
    return {"preamble": preamble.rstrip("\n") + ("\n" if preamble.strip() else ""), "entries": entries}


def render_file(entries, preamble=""):
    body = "\n".join(block(entry) for entry in entries)
    preamble = preamble.rstrip("\n")
    return (preamble + "\n\n" if preamble else "") + body


def to_host(entry):
    """The host-visible JSON of a managed entry without None narrative fields."""
    return {key: value for key, value in entry.items() if value not in (None, [], False) or key in ("paths", "terms", "sources", "status")}
