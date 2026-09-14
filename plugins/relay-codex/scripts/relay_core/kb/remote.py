"""Plan -> act -> record for every external effect of KB work.

Each stage saves its plan before acting and its result after. A retry reads the plan
back: a commit is recovered from the branch when the same parent and content already
exist, an empty commit when the same parent, tree and message already exist, a push is
delivered when the remote already has the commit or a descendant, a PR is recovered by
its unique marker, a close by the PR's state, a comment by marker, body and author.
Nothing is force-pushed, re-posted or re-created automatically.
"""
from .. import RelayError
from ..artifacts import digest
from ..repository import git, git_raw, ancestor
from . import changes as changeset

UNCERTAIN = "uncertain"


def fail(message, code="conflict"):
    raise RelayError(code, message)


class Stages:
    """Stage records live under holder["stages"][name]; `save` persists the holder."""

    def __init__(self, holder, save):
        self.holder, self.save = holder, save
        self.records = holder.setdefault("stages", {})

    def plan(self, name, **planned):
        record = self.records.get(name)
        if record is None:
            record = {**planned, "status": "planned", "result": None}
            self.records[name] = record
            self.save()
        elif {k: record.get(k) for k in planned} != planned:
            fail(f"Stage {name} was planned with different content; reconcile before changing it.")
        return record

    def done(self, name, **result):
        record = self.records[name]
        record.update(status="done", result=result)
        self.save()
        return record

    def mark(self, name, status):
        self.records[name]["status"] = status
        self.save()


def files_digest(root, sha, paths):
    """Digest over the planned KB paths as they exist at a commit (missing paths are absent)."""
    actual = {}
    for path in paths:
        try:
            actual[path] = digest(git_raw(root, "show", f"{sha}:{path}").replace("\r\n", "\n"))
        except RelayError:
            continue
    return changeset.digest_of(actual)


def worktree_digest(root, paths):
    from pathlib import Path
    actual = {}
    for path in paths:
        file = Path(root) / path
        if file.is_file():
            actual[path] = digest(file.read_text(encoding="utf-8").replace("\r\n", "\n"))
    return changeset.digest_of(actual)


def commit(stages, name, root, *, parent, paths, post_tree, message):
    """Commit exactly the planned paths on top of parent; recover a commit that already did so."""
    record = stages.plan(name, parent=parent, paths=sorted(paths), post_tree=post_tree, message=message)
    if record["status"] == "done":
        return record["result"]
    head = git(root, "rev-parse", "HEAD")
    if head != parent:
        parents = git(root, "rev-list", "--parents", "-n", "1", head).split()[1:]
        touched = set(git_raw(root, "diff", "--name-only", "-z", parent, head, "--").split("\0")) - {""}
        if (parents == [parent] and touched <= set(record["paths"]) and not git(root, "status", "--porcelain")
                and files_digest(root, head, record["paths"]) == post_tree):
            return stages.done(name, sha=head, recovered=True)["result"]
        fail("HEAD is neither the planned parent nor the planned commit; inspect the branch.")
    # The worktree content of the planned paths is what the commit would contain: verify it first.
    if worktree_digest(root, record["paths"]) != post_tree:
        fail("Worktree content of the planned paths differs from the approved post-tree; no commit made.", "verification")
    stages.mark(name, "committing")
    git(root, "add", "--all", "--", *record["paths"])
    staged = set(git_raw(root, "diff", "--cached", "--name-only", "-z", "--").split("\0")) - {""}
    if not staged:
        stages.mark(name, "planned")
        fail("Nothing to commit for the planned paths.", "kb")
    if not staged <= set(record["paths"]):
        git(root, "reset", "-q", "--", *sorted(staged - set(record["paths"])))
        fail("Staged paths exceed the knowledge paths; no commit made.")
    git(root, "commit", "-q", "-m", message)
    sha = git(root, "rev-parse", "HEAD")
    if git(root, "status", "--porcelain") or files_digest(root, sha, record["paths"]) != post_tree:
        fail("Committed content differs from the approved post-tree.", "verification")
    return stages.done(name, sha=sha, recovered=False)["result"]


def empty_commit(stages, name, root, *, parent, message):
    """One commit without file changes on top of parent, in a clean worktree; recover one that already did so.

    It is its own stage so the real-change checks of `commit` stay strict: a staged or
    modified file stops it, and a HEAD other than the parent or that exact commit conflicts.
    """
    record = stages.plan(name, parent=parent, message=message)
    if record["status"] == "done":
        return record["result"]
    head = git(root, "rev-parse", "HEAD")
    if head != parent:
        parents = git(root, "rev-list", "--parents", "-n", "1", head).split()[1:]
        if (parents == [parent] and git(root, "rev-parse", head + "^{tree}") == git(root, "rev-parse", parent + "^{tree}")
                and git_raw(root, "log", "-1", "--format=%B", head).strip() == message.strip() and not git(root, "status", "--porcelain")):
            return stages.done(name, sha=head, recovered=True)["result"]
        fail("HEAD is neither the planned parent nor the planned empty commit; inspect the branch.")
    if git(root, "status", "--porcelain"):
        fail("The run worktree has changes; no empty commit made.")
    stages.mark(name, "committing")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    sha = git(root, "rev-parse", "HEAD")
    if git(root, "rev-parse", sha + "^{tree}") != git(root, "rev-parse", parent + "^{tree}") or git(root, "status", "--porcelain"):
        fail("The empty commit changed files.", "verification")
    return stages.done(name, sha=sha, recovered=False)["result"]


def remote_tip(root, url, ref):
    target = "refs/heads/" + ref
    lines = git(root, "ls-remote", "--refs", url, target).splitlines()
    return next((line.split()[0] for line in lines if line.split()[1] == target), None)


def push(stages, name, root, *, url, ref, expected_remote, local_commit):
    """Ordinary push of one commit to one ref; delivered when the remote already has it or a descendant."""
    record = stages.plan(name, url=url, ref=ref, expected_remote=expected_remote, local_commit=local_commit)
    if record["status"] == "done":
        return record["result"]
    current = remote_tip(root, url, ref)
    if current == local_commit:
        return stages.done(name, remote_sha=current, delivered="already")["result"]
    if current is not None and current != expected_remote and ancestor(root, local_commit, current):
        return stages.done(name, remote_sha=current, delivered="descendant")["result"]
    if current != expected_remote:
        fail(f"Remote {ref} is at {current}, not the expected {expected_remote}; no push made.")
    stages.mark(name, "pushing")
    git(root, "push", "--no-follow-tags", url, local_commit + ":" + "refs/heads/" + ref)
    after = remote_tip(root, url, ref)
    if after != local_commit:
        fail("Push result does not match the local commit; resume checks the remote again.", UNCERTAIN)
    return stages.done(name, remote_sha=after, delivered="pushed")["result"]


def matching_pull(pull, head, base, repo):
    return ((pull.get("head", {}).get("repo") or {}).get("full_name", "").lower() == repo.lower()
            and pull.get("head", {}).get("ref") == head and pull.get("base", {}).get("ref") == base)


def create_pull(stages, name, gh, *, repo, head, base, title, body, draft, marker):
    """Create one PR carrying the marker; response loss recovers exactly one PR or stays uncertain."""
    if marker not in body:
        raise RelayError("input", "PR body must carry its identity marker.")
    record = stages.plan(name, repo=repo, head=head, base=base, title=title, body_digest=digest(body), draft=draft, marker=marker)
    if record["status"] == "done":
        return record["result"]

    def recover():
        marked = [p for p in gh.pulls("all") if marker in (p.get("body") or "")]
        matches = [p for p in marked if matching_pull(p, head, base, repo)]
        if len(marked) == 1 and len(matches) == 1:
            pull = gh.pull(matches[0]["number"])
            return stages.done(name, number=pull["number"], url=pull["html_url"], recovered=True)
        if not marked:
            return None
        fail("PR creation cannot be reconciled uniquely; no POST resent.", UNCERTAIN)

    if record["status"] == "creating":
        found = recover()
        if found:
            return found["result"]
    else:
        existing = [p for p in gh.pulls("all") if matching_pull(p, head, base, repo) and marker in (p.get("body") or "")]
        if existing:
            pull = gh.pull(existing[0]["number"])
            return stages.done(name, number=pull["number"], url=pull["html_url"], recovered=True)["result"]
    stages.mark(name, "creating")
    try:
        response = gh.create_pull({"title": title, "body": body, "head": head, "base": base, "draft": draft})
    except RelayError as exc:
        if exc.code == "github_rejected":
            stages.mark(name, "planned")
            raise
        found = recover()
        if found:
            return found["result"]
        fail("PR creation response was lost and no PR carries the marker yet; retry resumes reconciliation.", UNCERTAIN)
    if not isinstance(response, dict) or type(response.get("number")) is not int:
        found = recover()
        if found:
            return found["result"]
        fail("PR creation returned no number.", UNCERTAIN)
    pull = gh.pull(response["number"])
    if marker not in (pull.get("body") or "") or not matching_pull(pull, head, base, repo):
        fail("Created PR does not carry the planned identity.", "verification")
    return stages.done(name, number=pull["number"], url=pull["html_url"], recovered=False)["result"]


def update_pull(stages, name, gh, *, number, previous_digest, title, body):
    """PATCH the body only from the previous known content to the target; external edits stop it."""
    target = digest(body)
    record = stages.plan(name, number=number, previous_digest=previous_digest, target_digest=target, title=title)
    if record["status"] == "done":
        return record["result"]
    pull = gh.pull(number)
    current = digest(pull.get("body") or "")
    if current == target and pull.get("title") == title:
        return stages.done(name, recovered=True)["result"]
    if current != previous_digest:
        fail("PR body changed outside this plan; review before overwriting.")
    stages.mark(name, "updating")
    try:
        gh.update_pull(number, {"title": title, "body": body})
    except RelayError as exc:
        if exc.code == "github_rejected":
            stages.mark(name, "planned")
            raise
    pull = gh.pull(number)
    if digest(pull.get("body") or "") != target:
        fail("PR body after update differs from the target.", UNCERTAIN)
    return stages.done(name, recovered=False)["result"]


def mark_ready(stages, name, gh, *, number):
    record = stages.plan(name, number=number)
    if record["status"] == "done":
        return record["result"]
    pull = gh.pull(number)
    if not pull.get("draft", False):
        return stages.done(name, recovered=True)["result"]
    stages.mark(name, "marking")
    gh.mark_ready(number)
    pull = gh.pull(number)
    if pull.get("draft", False):
        fail("PR is still a draft after marking ready.", UNCERTAIN)
    return stages.done(name, recovered=False)["result"]


def close_pull(stages, name, gh, *, number):
    """Close an unmerged PR without merging; an already closed PR is recovered, a merged one conflicts."""
    record = stages.plan(name, number=number)
    if record["status"] == "done":
        return record["result"]
    pull = gh.pull(number)
    if pull.get("merged") or pull.get("merged_at"):
        fail("PR was merged; it is not closed as unchanged.")
    if pull.get("state") == "closed":
        return stages.done(name, recovered=True)["result"]
    stages.mark(name, "closing")
    try:
        gh.update_pull(number, {"state": "closed"})
    except RelayError as exc:
        if exc.code == "github_rejected":
            stages.mark(name, "planned")
            raise
    pull = gh.pull(number)
    if pull.get("state") != "closed":
        fail("PR is still open after closing.", UNCERTAIN)
    return stages.done(name, recovered=False)["result"]


def comment(stages, name, gh, *, number, body, marker, author):
    """Post one comment identified by its marker; loss recovers by marker, body and author or stays uncertain."""
    if not body.rstrip().endswith(marker):
        raise RelayError("input", "Comment body must end with its identity marker.")
    record = stages.plan(name, number=number, body_digest=digest(body), marker=marker, author=author)
    if record["status"] == "done":
        return record["result"]

    def recover():
        found = [c for c in gh.comments(number) if marker in (c.get("body") or "")]
        if not found:
            return None
        if len(found) != 1 or digest(found[0].get("body") or "") != digest(body) or (found[0].get("user") or {}).get("login") != author:
            fail("Comment cannot be reconciled uniquely; no automatic repost.", UNCERTAIN)
        exact = gh.comment(found[0]["id"])
        if digest(exact.get("body") or "") != digest(body):
            fail("Comment content differs from the plan.", UNCERTAIN)
        return stages.done(name, id=exact["id"], url=exact["html_url"], recovered=True)

    if record["status"] in ("posting", UNCERTAIN):
        found = recover()
        if found:
            return found["result"]
        if record["status"] == UNCERTAIN:
            fail("Comment posting remains unconfirmed; confirm on GitHub before any repost.", UNCERTAIN)
    if gh.viewer() != author:
        fail("Authenticated account differs from the planned author.")
    stages.mark(name, "posting")
    try:
        response = gh.write(number, None, body)
    except RelayError as exc:
        if exc.code == "github_rejected":
            stages.mark(name, "planned")
            raise
        stages.mark(name, UNCERTAIN)
        found = recover()
        if found:
            return found["result"]
        fail("Comment response was lost; retry reconciles by marker without reposting.", UNCERTAIN)
    exact = gh.comment(response["id"])
    if digest(exact.get("body") or "") != digest(body) or (exact.get("user") or {}).get("login") != author:
        fail("Posted comment differs from the plan.", UNCERTAIN)
    return stages.done(name, id=exact["id"], url=exact["html_url"], recovered=False)["result"]
