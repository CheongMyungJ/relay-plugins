"""auto / judge / broken verdicts for existing entries against a target SHA (spec R5).

broken: a current path or Python symbol no longer exists at the target.
judge: no confirmed SHA, unreadable or non-ancestor confirmed commit, compat entries,
       entries without paths, or any tracked change under the entry's paths.
auto: every path is unchanged between confirmed and target. Auto verdicts are recorded
      locally only; the helper never rewrites state.json for them.
"""
import hashlib
import json
from ..repository import git
from .. import RelayError
from . import entries as model
from .fragment import Objects


def entry_digest(entry):
    return hashlib.sha256(json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class Reconfirm:
    """One target SHA, one memo: trees per SHA, ASTs per (sha, path), diffs per confirmed SHA."""

    def __init__(self, root, target_sha):
        self.root, self.target = root, target_sha
        self.objects = Objects(root)
        self.trees, self.ancestors = {}, {}

    def tree(self, sha):
        if sha not in self.trees:
            try:
                from ..repository import git_raw
                raw = git_raw(self.root, "ls-tree", "-r", "--name-only", "-z", sha)
                self.trees[sha] = {n for n in raw.split("\0") if n}
            except RelayError:
                self.trees[sha] = None
        return self.trees[sha]

    def ancestor(self, sha):
        if sha not in self.ancestors:
            try:
                git(self.root, "merge-base", "--is-ancestor", sha, self.target)
                self.ancestors[sha] = True
            except RelayError:
                self.ancestors[sha] = False
        return self.ancestors[sha]

    def exists(self, value):
        """Whether an entry path (dir, file or file:symbol) exists at the target."""
        base, symbol, is_dir = model.split_path(value)
        tree = self.tree(self.target)
        if tree is None:
            raise RelayError("git", "Target commit is unreadable: " + self.target)
        if is_dir:
            prefix = base if base.endswith("/") else base + "/"
            return any(n.startswith(prefix) for n in tree)
        if base not in tree:
            return False
        if symbol is None:
            return True
        table = self.objects.table(self.target, base)
        # Non-Python symbols get file-level checks only, with a warning from the caller.
        return True if table is None else symbol in table

    def verdict(self, entry, confirmed):
        """{verdict, reason, changed: [paths], warnings: [...]} for one entry."""
        warnings = []
        for value in entry["paths"]:
            base, symbol, _ = model.split_path(value)
            if symbol and not base.endswith(".py"):
                warnings.append(f"{value}: non-Python symbol checked at file level only")
            if not self.exists(value):
                return {"verdict": "broken", "reason": f"missing at target: {value}", "changed": [], "warnings": warnings}
        if entry["type"] == "C" and entry["compat"]:
            return {"verdict": "judge", "reason": "compat entries always need evidence", "changed": [], "warnings": warnings}
        if not entry["paths"]:
            return {"verdict": "judge", "reason": "entries without paths are document-based", "changed": [], "warnings": warnings}
        if not confirmed:
            return {"verdict": "judge", "reason": "no confirmed SHA", "changed": [], "warnings": warnings}
        if self.tree(confirmed) is None:
            return {"verdict": "judge", "reason": "confirmed commit is unreadable", "changed": [], "warnings": warnings}
        if not self.ancestor(confirmed):
            return {"verdict": "judge", "reason": "confirmed commit is not an ancestor of the target", "changed": [], "warnings": warnings}
        changed_files = self.objects.changed_files(confirmed, self.target)
        if changed_files is None:
            return {"verdict": "judge", "reason": "diff is unavailable", "changed": [], "warnings": warnings}
        touched = []
        for value in entry["paths"]:
            base, _, is_dir = model.split_path(value)
            prefix = (base if base.endswith("/") else base + "/") if is_dir else None
            for name in sorted(changed_files):
                if (prefix and name.startswith(prefix)) or name == base:
                    touched.append(name)
        if touched:
            return {"verdict": "judge", "reason": "tracked changes under the entry's paths", "changed": sorted(set(touched)), "warnings": warnings}
        return {"verdict": "auto", "reason": "no tracked change since confirmed", "changed": [], "warnings": warnings}


def reusable(checked, entry, confirmed, target):
    """A previous local auto record applies only to the same entry text, confirmed and target."""
    record = (checked or {}).get(entry["id"])
    return bool(record and record.get("verdict") == "auto" and record.get("entry_digest") == entry_digest(entry)
                and record.get("confirmed") == confirmed and record.get("target_sha") == target)


def record(entry, confirmed, target):
    return {"entry_digest": entry_digest(entry), "confirmed": confirmed, "target_sha": target, "verdict": "auto"}


def assess(kb, ids, root, target, checked=None):
    """Verdicts for the given IDs at target; reused auto records skip Git entirely."""
    engine = Reconfirm(root, target)
    results = {}
    for identity in ids:
        entry = kb["entries"][identity]
        confirmed = kb["state"]["confirmed"].get(identity)
        if reusable(checked, entry, confirmed, target):
            results[identity] = {"verdict": "auto", "reason": "reused local checked record", "changed": [], "warnings": [], "reused": True}
            continue
        results[identity] = dict(engine.verdict(entry, confirmed), reused=False)
    return results
