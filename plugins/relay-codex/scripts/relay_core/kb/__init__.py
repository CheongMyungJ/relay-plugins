"""Path-bound knowledge base: lookup, gates, transactions, PR mode and sync mode.

`dispatch` is the single entry of the `kb` helper command. Each action lives in its
own module; this file only routes and rejects mixed page-reading/new-work input.
"""
from pathlib import Path
from .. import RelayError, repository as gitrepo
from ..github import GitHub

READ = ("lookup", "check", "apply", "fragment", "render")
PR = ("inspect", "prepare", "publish")
SYNC = ("begin", "batch", "checkpoint", "resume", "handoff", "finish")
ACTIONS = READ + PR + SYNC


def reader_for(repo, sha):
    from . import layout
    return layout.TreeReader(repo["root"], sha) if sha else layout.WorktreeReader(repo["root"])


def generic(action, data, repo, gh):
    from . import layout, lookup, fragment as fragments, gates, changes as changeset
    sha = data.get("sha")
    if sha is not None and not isinstance(sha, str):
        raise RelayError("input", "kb: sha — expected a commit SHA string")
    if action == "lookup":
        sha = lookup.cursor_sha(data)  # a cursor alone reads the tree its page was made from
        kb = layout.load(reader_for(repo, sha))
        return lookup.lookup(kb, data, sha=sha, repo=repo["repo"], cwd=repo["root"])
    if action == "fragment":
        sha = fragments.cursor_sha(data)
        if not sha:
            sha = gitrepo.git(repo["root"], "rev-parse", "HEAD")
        kb = layout.load(reader_for(repo, sha))
        return fragments.fragment(kb, data, repo["root"], sha)
    if action == "render":
        root = Path(data.get("worktree") or repo["root"])
        reader = layout.WorktreeReader(root)
        kb = layout.load(reader)
        rendered = layout.render(kb, reader)
        written = []
        for path, text in sorted(rendered["files"].items()):
            target = root / path
            current = reader.read(path)
            if current == text:
                continue
            if text is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")
            written.append(path)
        return {"status": "rendered", "written": written, "warnings": rendered["warnings"]}
    if action == "check":
        if not sha:
            raise RelayError("input", "kb check: sha — required; expected the fixed SHA the change set was written against")
        kb = layout.load(reader_for(repo, sha))
        result = gates.check(kb, data.get("changes"), sha=sha, root=repo["root"], gh=gh, scope_ids=data.get("scope") or [],
                             checked=data.get("checked"), execution_id=data.get("execution_id") or "check", batch_id=data.get("batch_id"),
                             issued=data.get("issued"))
        if result["status"] != "ok":
            return result
        return {"status": "ok", "plan": result["plan"], "verdicts": result["verdicts"], "sources": result["sources"],
                "warnings": result["warnings"], "files": sorted(result["files"]), "checked": result["checked"]}
    if action == "apply":
        plan_value, worktree, record = data.get("plan"), data.get("worktree"), data.get("record")
        if not isinstance(plan_value, dict) or not worktree or not record:
            raise RelayError("input", "kb apply: plan, worktree, record — required; expected the frozen plan object, a worktree path and a record path")
        kb = layout.load(reader_for(repo, plan_value.get("sha")))
        replay, files, _ = changeset.plan(kb, data.get("changes"), plan_value["sha"], reader_for(repo, plan_value["sha"]),
                                          execution_id=plan_value["execution_id"], scope=plan_value["scope"], batch_id=plan_value.get("batch_id"),
                                          label=plan_value.get("label"), issued=plan_value["issued"], verdicts=data.get("verdicts"),
                                          pointer_reader=reader_for(repo, plan_value["sha"]))
        if replay["digest"] != plan_value["digest"] or replay["post_tree"] != plan_value["post_tree"]:
            raise RelayError("conflict", "Change set or KB content differs from the frozen plan.")
        return changeset.apply(replay, files, worktree, Path(record))
    raise RelayError("input", "kb: action — expected one of " + ", ".join(ACTIONS))


def dispatch(data, registry, gh=None, repo=None):
    action = data.get("action")
    if action not in ACTIONS:
        raise RelayError("input", "kb: action — expected one of " + ", ".join(ACTIONS))
    if data.get("cursor") and any(data.get(k) for k in ("changes", "changes_file", "raw")) and action in ("inspect", "batch"):
        raise RelayError("input", "Do not mix a page read with new collection or batch selection.")
    repo = repo or gitrepo.inspect(data.get("cwd", str(Path.cwd())))
    gh = gh or GitHub(repo["repo"], host=repo.get("host", "github.com"))
    if action in READ:
        return generic(action, data, repo, gh)
    if action in PR:
        from .pr_mode import dispatch as pr_dispatch
        return pr_dispatch(data, registry, repo, gh)
    from .sync import dispatch as sync_dispatch
    return sync_dispatch(data, registry, repo, gh)
