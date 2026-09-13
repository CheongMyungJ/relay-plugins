"""Symbol sources, change hunks and module top-level segments under the page budget (spec R3, R5).

Units are read from Git objects at one SHA. A unit larger than the remaining budget is
split by line range and continued on the next page; a response that only says
"truncated" without a way to read further is never produced.
"""
import ast
import re
from .. import RelayError
from ..repository import git_raw
from . import entries as model
from . import layout, lookup

PAGE_BUDGET = lookup.PAGE_BUDGET
MIN_LINES = 8


def python_symbols(text):
    """{name: (start, end)} for top-level defs/classes and their methods as Class.method, 1-based inclusive."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = (node.lineno, node.end_lineno)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        found[node.name + "." + child.name] = (child.lineno, child.end_lineno)
    return found


def top_level_ranges(text):
    """Line ranges outside every top-level def/class: constants, imports, module code."""
    symbols = python_symbols(text)
    total = text.count("\n") + (0 if text.endswith("\n") else 1)
    if symbols is None:
        return [(1, total)] if total else []
    covered = sorted(v for k, v in symbols.items() if "." not in k)
    ranges, cursor = [], 1
    for start, end in covered:
        if start > cursor:
            ranges.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= total:
        ranges.append((cursor, total))
    return [(s, e) for s, e in ranges if any(line.strip() for line in text.split("\n")[s - 1:e])]


class Objects:
    """Per-call memo for file text, symbol tables and diffs at fixed SHAs."""

    def __init__(self, root):
        self.root = root
        self.text, self.symbols, self.diffs, self.changed = {}, {}, {}, {}

    def read(self, sha, path):
        key = (sha, path)
        if key not in self.text:
            try:
                self.text[key] = git_raw(self.root, "show", f"{sha}:{path}")
            except RelayError:
                self.text[key] = None
        return self.text[key]

    def table(self, sha, path):
        key = (sha, path)
        if key not in self.symbols:
            text = self.read(sha, path)
            self.symbols[key] = None if text is None or not path.endswith(".py") else python_symbols(text)
        return self.symbols[key]

    def diff(self, since, sha, path):
        key = (since, sha, path)
        if key not in self.diffs:
            try:
                self.diffs[key] = git_raw(self.root, "diff", "--no-ext-diff", "--no-textconv", "--no-color", since, sha, "--", path)
            except RelayError:
                self.diffs[key] = None
        return self.diffs[key]

    def changed_files(self, since, sha):
        """Every path changed between two commits, computed once per pair."""
        key = (since, sha)
        if key not in self.changed:
            try:
                raw = git_raw(self.root, "diff", "--name-only", "-z", since, sha, "--")
                self.changed[key] = {n for n in raw.split("\0") if n}
            except RelayError:
                self.changed[key] = None
        return self.changed[key]


def hunks(diff_text):
    parts = re.split(r"(?m)^(?=@@ )", diff_text)
    header, rest = parts[0], parts[1:]
    return [(header, h) for h in rest]


def units_for(objects, sha, path, symbols, since, kb_entry=None):
    """The ordered units one path contributes: symbol/file source, hunks since, top-level code."""
    base, symbol, is_dir = model.split_path(path)
    units = []
    label = {"id": kb_entry["id"]} if kb_entry else {}
    if is_dir:
        units.append({**label, "path": path, "kind": "directory", "missing": "directories have no source unit; request files"})
        return units
    text = objects.read(sha, base)
    if text is None:
        units.append({**label, "path": base, "kind": "file", "missing": "path does not exist at " + sha[:12]})
        return units
    lines = text.split("\n")
    if text.endswith("\n"):
        lines = lines[:-1]
    wanted = list(symbols) if symbols else ([symbol] if symbol else [])
    table = objects.table(sha, base)
    if wanted:
        for name in wanted:
            if name == "<module>":
                for start, end in top_level_ranges(text):
                    units.append({**label, "path": base, "kind": "module_top", "line_start": start, "line_end": end,
                                  "lines": lines[start - 1:end]})
                continue
            if table is None or name not in table:
                units.append({**label, "path": base, "symbol": name, "kind": "symbol",
                              "missing": "symbol not found by AST" if table is not None else "not a parsable Python file; read line ranges"})
                continue
            start, end = table[name]
            units.append({**label, "path": base, "symbol": name, "kind": "symbol", "line_start": start, "line_end": end, "lines": lines[start - 1:end]})
    else:
        units.append({**label, "path": base, "kind": "file", "line_start": 1, "line_end": len(lines), "lines": lines})
    if since:
        diff = objects.diff(since, sha, base)
        if diff is None:
            units.append({**label, "path": base, "kind": "hunk", "since": since, "missing": "confirmed commit is unreadable; only current source is provided"})
        else:
            for index, (header, body) in enumerate(hunks(diff)):
                hunk_lines = (header + body).split("\n")
                units.append({**label, "path": base, "kind": "hunk", "since": since, "index": index,
                              "line_start": 1, "line_end": len(hunk_lines), "lines": hunk_lines})
    return units


def plan(kb, data, objects, sha):
    """Resolve the request into ordered units before paging."""
    ids, path, symbols = data.get("ids") or [], data.get("path"), data.get("symbols") or []
    if bool(ids) == bool(path):
        raise RelayError("input", "Request either ids or a path.")
    units, missing = [], []
    if ids:
        for identity in ids:
            if not isinstance(identity, str) or not model.ID.match(identity):
                raise RelayError("input", "ids must be full entry IDs.")
            entry = kb["entries"].get(identity)
            if entry is None:
                missing.append(identity)
                continue
            since = data.get("since") or kb["state"]["confirmed"].get(identity)
            units.append({"id": identity, "kind": "entry", "entry": lookup.summary_of(entry, [], kb), "since": since,
                          "missing": None if since else "no confirmed SHA; only current source is provided"})
            for value in entry["paths"]:
                units.extend(units_for(objects, sha, value, None, since, entry))
    else:
        model.path(path)
        if not isinstance(symbols, list) or any(not isinstance(s, str) for s in symbols):
            raise RelayError("input", "symbols must be a list of names.")
        units.extend(units_for(objects, sha, path, symbols, data.get("since")))
    return units, missing


def fragment(kb, data, root, sha):
    """One page of units; text is joined from `lines`, oversized units continue by line range."""
    objects = Objects(root)
    units, missing = plan(kb, data, objects, sha)
    query = {"ids": data.get("ids"), "path": data.get("path"), "symbols": data.get("symbols"), "since": data.get("since")}
    binding = {"query": lookup.query_hash(query), "sha": sha, "kb": layout.digest_of(kb)}
    position = (0, 0)
    if data.get("cursor"):
        offset = lookup.decode_cursor(data["cursor"], binding)
        position = (offset // 100000, offset % 100000)
    result = {"sha": sha, "items": [], "missing_ids": missing, "truncated": False, "next_cursor": None, "warnings": []}
    result["next_cursor"] = lookup.encode_cursor({**binding, "offset": len(units) * 100000})
    index, line = position

    def fits(item):
        result["items"].append(item)
        ok = lookup.size(result) <= PAGE_BUDGET
        result["items"].pop()
        return ok

    while index < len(units):
        unit = dict(units[index])
        lines = unit.pop("lines", None)
        if lines is None:
            if not fits(unit):
                if not result["items"]:
                    raise RelayError("length", "A single unit exceeds the page budget.")
                break
            result["items"].append(unit)
            index += 1
            continue
        remaining, start = lines[line:], unit["line_start"] + line

        def sliced(count):
            return {**unit, "line_start": start, "line_end": start + count - 1, "continued": count < len(remaining),
                    "text": "\n".join(remaining[:count])}

        if fits(sliced(len(remaining))):
            result["items"].append(sliced(len(remaining)))
            index, line = index + 1, 0
            continue
        # Binary-search the largest line count that still fits; the rest continues on the next page.
        low, high, best = 1, len(remaining), 0
        while low <= high:
            middle = (low + high) // 2
            if fits(sliced(middle)):
                best, low = middle, middle + 1
            else:
                high = middle - 1
        if best == 0:
            if not result["items"]:
                raise RelayError("length", "A single line exceeds the page budget.")
            break
        result["items"].append(sliced(best))
        line += best
        break
    if index < len(units):
        result["truncated"] = True
        result["next_cursor"] = lookup.encode_cursor({**binding, "offset": index * 100000 + line})
    else:
        result["next_cursor"] = None
    return result
