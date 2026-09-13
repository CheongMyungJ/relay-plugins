"""Extraction listing: tracked files under the requested paths at one SHA (spec R7).

Per file: size, line count, Python symbols, internal import edges and the issue/PR
numbers named in its last 20 commit messages. Numbers are classified through the
GitHub issues endpoint so a candidate can cite `pr:n` or `issue:n/...` correctly.
"""
import ast
import posixpath
import re
from ..repository import git, git_raw
from .. import RelayError
from .fragment import python_symbols

LOG_DEPTH = 20
NUMBER = re.compile(r"(?<![\w/])#([1-9][0-9]*)\b")


def tracked(root, sha, paths):
    """Tracked files at sha under each requested path; a path with nothing under it is an input error."""
    found = []
    for value in paths:
        base = value.rstrip("/")
        raw = git_raw(root, "ls-tree", "-r", "--name-only", "-z", sha, "--", base)
        names = [n for n in raw.split("\0") if n]
        if not names:
            raise RelayError("input", "No tracked file exists under " + value + " at " + sha[:12])
        found.extend(names)
    return sorted(set(found))


def module_index(files):
    """Repository-relative module names for Python files, so imports can be resolved to files."""
    index = {}
    for name in files:
        if name.endswith(".py"):
            module = name[:-3].replace("/", ".")
            index[module] = name
            if module.endswith(".__init__"):
                index[module[:-9]] = name
    return index


def imports_of(text, own_module, index):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    package = own_module.rpartition(".")[0]
    edges = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in index:
                    edges.add(index[alias.name])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[:len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base in index:
                edges.add(index[base])
            for alias in node.names:
                candidate = base + "." + alias.name if base else alias.name
                if candidate in index:
                    edges.add(index[candidate])
    return sorted(edges)


def numbers_of(root, sha, path):
    log = git_raw(root, "log", "-n", str(LOG_DEPTH), "--format=%s%n%b", sha, "--", path)
    return sorted({int(n) for n in NUMBER.findall(log)})


def classify(gh, numbers, memo):
    result = {}
    for number in numbers:
        if number not in memo:
            try:
                item = gh.item(number)
                memo[number] = "pr" if "pull_request" in item else "issue"
            except RelayError:
                memo[number] = "unknown"
        result[number] = memo[number]
    return result


def build(root, sha, paths, gh, memo=None):
    """Every file with its facts, grouped by directory for batching."""
    memo = {} if memo is None else memo
    files = tracked(root, sha, paths)
    index = module_index(files)
    entries = []
    for name in files:
        text = git_raw(root, "show", f"{sha}:{name}")
        lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        symbols = python_symbols(text) if name.endswith(".py") else None
        numbers = numbers_of(root, sha, name)
        entries.append({"path": name, "directory": posixpath.dirname(name), "size": len(text.encode("utf-8")), "lines": lines,
                        "symbols": sorted(symbols) if symbols else [], "parsable": symbols is not None if name.endswith(".py") else None,
                        "imports": imports_of(text, name[:-3].replace("/", "."), index) if name.endswith(".py") else [],
                        "numbers": classify(gh, numbers, memo)})
    return entries
