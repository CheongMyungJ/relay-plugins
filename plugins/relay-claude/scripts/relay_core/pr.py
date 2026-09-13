"""PR orchestration. The host writes prose; this module verifies and records effects."""
import json
import re
import uuid
from pathlib import Path, PurePosixPath
from . import RelayError, invocation, next_step as steps, repository as gitrepo, watch
from .artifacts import collect, digest, reference
from .github import GitHub
from .runs import parse_evidence
from .state import PrStore, read_json, write_json

MARKER = re.compile(r"<!-- relay:pr-request ([a-f0-9]{32}) -->")
UNCERTAIN = {"creating", "updating", "uncertain"}


def text_hash(title, body):
    return digest(json.dumps([title, body], ensure_ascii=False))


def candidate_hash(request):
    fields = ("repository", "common_dir", "remote_url", "issue", "head", "base", "head_sha", "base_sha",
              "default", "default_sha", "local_sha", "remote_sha", "push_needed", "title", "body", "draft", "constraints", "operation", "expected_hash")
    frozen = {key: request.get(key) for key in fields}
    if request.get("operation") == "update":
        frozen["number"] = request["number"]
    return digest(json.dumps(frozen, ensure_ascii=False, sort_keys=True))


def inspection_hash(inspected):
    """Bind the host's prose to the exact source material it inspected."""
    fields = ("repository", "remote_url", "issue", "head", "base", "head_sha", "base_sha",
              "default", "default_sha")
    frozen = {key: inspected[key] for key in fields}
    frozen["template"] = inspected["templates"]["selected"]
    return digest(json.dumps(frozen, ensure_ascii=False, sort_keys=True))


def matching(pull, repo, head, base):
    return (pull.get("head", {}).get("repo") or {}).get("full_name", "").lower() == repo["repo"].lower() and (
        pull.get("base", {}).get("repo") or {}).get("full_name", "").lower() == repo["repo"].lower() and (
        pull.get("head", {}).get("ref") == head and pull.get("base", {}).get("ref") == base
    )


def existing(gh, repo, head, base):
    matches = [p for p in gh.pulls() if matching(p, repo, head, base)]
    if len(matches) > 1:
        raise RelayError("conflict", "Multiple open PRs match: " + ", ".join(str(p["number"]) for p in matches))
    if matches:
        pull = gh.pull(matches[0]["number"])
        if pull.get("number") != matches[0]["number"] or not matching(pull, repo, head, base) or pull.get("state") != "open":
            raise RelayError("stale", "Existing PR changed during lookup; inspect again.")
        return pull


def result_for(pull, status, host="github.com"):
    number = pull.get("number")
    repo = (pull.get("base", {}).get("repo") or {}).get("full_name")
    if type(number) is not int or number < 1 or pull.get("html_url") != f"https://{host}/{repo}/pull/{number}":
        raise RelayError("verification", "PR identity or URL could not be verified.")
    return {"status": status, "number": number, "url": pull["html_url"], "head": pull["head"]["ref"],
            "base": pull["base"]["ref"], "state": pull["state"], "merged": bool(pull.get("merged_at") or pull.get("merged")),
            "title": pull["title"], "body": pull.get("body") or "", "draft": pull.get("draft", False)}


def templates(repo, sha, selected=None):
    root = repo["root"]
    paths = gitrepo.git_raw(root, "ls-tree", "-r", "--name-only", "-z", sha).split("\0")
    candidates = []
    for name in paths:
        if not name:
            continue
        p = PurePosixPath(name)
        parts = p.parts
        parent = "/".join(parts[:-1]).lower()
        default = p.name.lower() == "pull_request_template.md" and parent in ("", "docs", ".github")
        optional = parent in ("pull_request_template", "docs/pull_request_template", ".github/pull_request_template")
        if default or optional:
            candidates.append({"path": name, "default": default, "sha": sha,
                               "body": gitrepo.git_raw(root, "show", sha + ":" + name)})
    defaults = [p for p in candidates if p["default"]]
    chosen = next((p for p in candidates if p["path"] == selected), None) if selected else (
        defaults[0] if len(defaults) == 1 else candidates[0] if len(candidates) == 1 else None)
    if selected and chosen is None:
        raise RelayError("input", "Selected template does not exist at the remote default commit.")
    return {"candidates": candidates, "selected": chosen, "needs_input": bool(candidates and not chosen), "default_sha": sha}


def evidence(repo, issue, gh, base_sha, base):
    records, remote_runs = collect(gh.issue(issue), gh.comments(issue))
    entries = []
    for run_id, record in remote_runs.items():
        run = parse_evidence(record, run_id)
        entries.append(dict(run, evidence=f"https://{repo.get('host', 'github.com')}/{repo['repo']}/issues/{issue}#issuecomment-{record['target']}",
                            stale=any(name not in records or records[name].get("stale") or reference(records[name]) != ref
                                      for name, ref in run["parents"].items())))
    common = gitrepo.common_dir(repo["root"])
    for tree in gitrepo.worktrees(repo["root"]):
        if not tree.is_dir() or gitrepo.common_dir(tree) != common:
            continue
        pointer = tree / ".relay" / "pointer.json"
        if pointer.exists():
            target = Path(read_json(pointer)["root"]).resolve()
            if target not in gitrepo.worktrees(repo["root"]) or gitrepo.common_dir(target) != common:
                raise RelayError("state", "Implementation pointer is outside this repository's worktrees.")
        for path in (tree / ".relay" / "work").glob("*/state.json"):
            state = read_json(path)
            if state.get("issue") != issue or not gitrepo.same_repository(state.get("repository", {"repo": ""}), repo):
                continue
            for key, run in state.get("runs", {}).items():
                if run.get("run_id") != key:
                    raise RelayError("run", "Local execution key and run ID disagree.")
                entries.append(dict(run, evidence=str(path), stale=None))
    seen, conflicts = {}, set()
    for entry in entries:
        key = entry["run_id"]
        identity = tuple(entry.get(field) for field in ("repository", "branch", "commit")) + (json.dumps(entry.get("parents"), sort_keys=True),)
        if key in seen and seen[key] != identity:
            conflicts.add(key)
        seen[key] = identity
    candidates = {}
    for entry in entries:
        entry["tip"] = None
        try:
            if entry.get("repository", "").lower() != repo["repo"].lower():
                raise RelayError("run", "Execution repository differs.")
            if entry["run_id"] in conflicts:
                raise RelayError("conflict", "Local and remote evidence contradict one another.")
            if not entry.get("commit"):
                raise RelayError("run", "No recorded implementation commit.")
            source = gitrepo.source(repo, entry["branch"])
            entry["tip"] = source["head_sha"]
            if not re.fullmatch(r"[0-9a-f]{40,64}", entry["commit"]) or not gitrepo.ancestor(repo["root"], entry["commit"], entry["tip"]):
                raise RelayError("run", "Recorded commit is not in source history.")
            merge = gitrepo.git(repo["root"], "merge-base", base_sha, entry["tip"])
            if not gitrepo.git(repo["root"], "diff", "--name-only", merge, entry["tip"]) and not existing(gh, repo, entry["branch"], base):
                raise RelayError("branch", "No file changes against base.")
            candidates.setdefault(entry["branch"], []).append(entry)
        except RelayError as exc:
            entry["excluded"] = str(exc)
    return {"candidates": candidates, "evidence": entries, "conflicts": sorted(conflicts)}


def inspect_request(data, repo, gh, registry):
    parsed = invocation.parse("pr", data.get("raw", ""), registry)
    options = parsed["options"]
    issue = gh.issue(parsed["issue"]) if parsed["issue"] else None
    default = gh.api(gh.prefix)["default_branch"]
    base = gitrepo.branch_name(repo["root"], data.get("base", options.get("base", default)))
    base_sha = gitrepo.remote_tip(repo, base, fetch=True)
    if not base_sha:
        raise RelayError("branch", "Base branch does not exist on the selected remote.")
    head = data.get("head", options.get("branch"))
    selection = {"reason": data.get("selection_reason", "explicit branch" if head else "current branch")}
    if not head and parsed["issue"]:
        selection = evidence(repo, parsed["issue"], gh, base_sha, base)
        if len(selection["candidates"]) != 1 or selection["conflicts"]:
            return {"status": "needs_input", "selection": selection, "base": base}
        head = next(iter(selection["candidates"]))
    if not head:
        head = gitrepo.git(repo["cwd"], "branch", "--show-current")
    if not head:
        raise RelayError("branch", "Detached HEAD needs an explicit branch or unique issue evidence.")
    gitrepo.branch_name(repo["root"], head)
    found = existing(gh, repo, head, base)
    if found:
        return result_for(found, "existing", repo.get("host", "github.com"))
    if head == base:
        raise RelayError("branch", "Head and base are the same branch.")
    source = gitrepo.source(repo, head)
    merge = gitrepo.git(repo["root"], "merge-base", base_sha, source["head_sha"])
    diff = gitrepo.git_raw(repo["root"], "diff", "--no-ext-diff", "--no-textconv", merge, source["head_sha"], "--")
    if not diff:
        raise RelayError("branch", "No file changes to propose.")
    default_sha = base_sha if default == base else gitrepo.remote_tip(repo, default, fetch=True)
    if not default_sha:
        raise RelayError("branch", "Remote default branch is missing.")
    template = templates(repo, default_sha, data.get("template"))
    result = {"status": "needs_input" if template["needs_input"] else "inspected", "repository": repo,
            "remote_url": gitrepo.git(repo["root"], "remote", "get-url", repo["remote"]),
            "issue": parsed["issue"], "issue_data": issue, "description": parsed["description"], "head": head, "base": base,
            "base_sha": base_sha, "default": default, "default_sha": default_sha, **source,
            "merge_base": merge, "diff": diff, "templates": template, "selection": selection,
            "dirty": bool(gitrepo.git(repo["cwd"], "status", "--porcelain")), "watch": bool(options.get("watch"))}
    if result["status"] == "inspected":
        result["inspection_hash"] = inspection_hash(result)
    return result


def check_identity(request, repo, store):
    saved = request["repository"]
    if not gitrepo.same_repository(saved, repo) or (saved["remote"], request["common_dir"]) != (repo["remote"], str(store.common)):
        raise RelayError("repository", "PR request belongs to another repository or remote.")
    if request["remote_url"] != gitrepo.git(repo["root"], "remote", "get-url", repo["remote"]):
        raise RelayError("repository", "Remote address changed; inspect and prepare again.")


def save_result(store, request, result, gh=None):
    # Marking follows the confirmed PR and never changes the request hash or the PR itself.
    if request.get("watch") and gh is not None and result.get("status") in ("recorded", "existing"):
        request["watch_result"] = watch.mark(gh, result["number"], True, request.get("watch_result"))
        result["watch"] = request["watch_result"]
    request["status"] = result["status"]
    request["result"] = result
    store.save(request)
    write_json(store.request_path(request["request_id"]) / "result.json", result)
    return dict(result, request_id=request["request_id"], hash=request.get("hash"))


def prepare_request(data, repo, gh, store, registry):
    old = store.load(data["request_id"]) if data.get("request_id") else None
    if old:
        check_identity(old, repo, store)
        if old["status"] in UNCERTAIN or old["status"] == "pushing":
            raise RelayError("uncertain", "Resume the saved request before preparing another candidate.")
        if old["status"] in ("recorded", "existing"):
            raise RelayError("input", "This request already has a PR; use explicit update.")
    inspected = inspect_request(data, repo, gh, registry)
    if inspected["status"] != "inspected":
        return inspected
    if data.get("inspection_hash") != inspected["inspection_hash"]:
        raise RelayError("stale", "Inspection hash is missing or changed; read the current diff/templates and rewrite the body before preparing.")
    title = data["title"]
    body = Path(data["body_file"]).read_text(encoding="utf-8-sig")
    if not isinstance(title, str) or not title.strip() or len(title) > 256:
        raise RelayError("input", "Provide a nonempty PR title up to 256 characters.")
    if MARKER.search(body) or "<!-- relay:pr-request" in body:
        raise RelayError("input", "The helper adds the request marker; remove it from the draft.")
    steps.reserved(body)
    if "next_step" not in data:
        raise RelayError("input", "Submit the candidate's next_step; it is never filled in automatically.")
    suggestion = steps.normalize("pr", data["next_step"])
    constraints = {key: data.get(key, False) for key in ("no_push", "draft_only")}
    draft = data.get("draft", False)
    if type(draft) is not bool or any(type(v) is not bool for v in constraints.values()):
        raise RelayError("input", "draft, no_push and draft_only must be booleans.")
    request_id = old["request_id"] if old else uuid.uuid4().hex
    # The suggestion sits before the request marker, inside the existing candidate hash.
    body += "\n\n" + steps.rendered(suggestion) + "\n\n<!-- relay:pr-request " + request_id + " -->\n"
    if len(body) > 65000:
        raise RelayError("length", "Shorten and review the PR body; it exceeds the supported limit.")
    keys = ("repository", "remote_url", "issue", "head", "base", "head_sha", "base_sha", "default", "default_sha",
            "local_sha", "remote_sha", "relation", "push_needed", "selection", "dirty", "watch")
    request = {key: inspected[key] for key in keys}
    request.update(schema=1, request_id=request_id, common_dir=str(store.common), operation="create", status="prepared",
                   title=title, body=body, draft=draft, constraints=constraints, next_step=suggestion,
                   template=inspected["templates"]["selected"])
    request["hash"] = candidate_hash(request)
    store.save(request)
    path = store.request_path(request_id) / "body.md"
    path.write_text(body, encoding="utf-8", newline="\n")
    return {"status": "prepared", "request_id": request_id, "hash": request["hash"], "body_file": str(path),
            "head": request["head"], "base": request["base"], "head_sha": request["head_sha"], "base_sha": request["base_sha"],
            "push_needed": request["push_needed"], "constraints": constraints, "next_step": suggestion}


def verify_pull(store, request, pull, gh=None, completed=False):
    if request.get("number") and pull.get("number") != request["number"]:
        raise RelayError("verification", "Read-back returned another PR number.")
    if not matching(pull, request["repository"], request["head"], request["base"]):
        raise RelayError("verification", "PR repository or branches differ from the saved request.")
    if not completed and (pull.get("title") != request["title"] or (pull.get("body") or "") != request["body"]):
        raise RelayError("conflict", "PR exists but its title/body differs; inspect external edits without overwriting them.")
    if not completed and request["operation"] == "create" and pull.get("draft", False) != request["draft"]:
        raise RelayError("conflict", "PR exists but draft state differs.")
    result = result_for(pull, "recorded", request["repository"].get("host", "github.com"))
    if completed:
        fields = ("title", "body", "draft") if request["operation"] == "create" else ("title", "body")
        result["content_changes"] = [key for key in fields if result[key] != request[key]]
    result["sha_changes"] = {side: {"prepared": request[side + "_sha"], "observed": pull[side].get("sha")}
                             for side in ("head", "base") if pull[side].get("sha") != request[side + "_sha"]}
    return save_result(store, request, result, gh)


def recover(store, request, gh):
    """Read only, including after process death between remote write and local save."""
    # Completed writes remain completed even if later edits or a failed GET are observed.
    if request["status"] == "recorded":
        return verify_pull(store, request, gh.pull(request["number"]), gh, completed=True)
    try:
        if request["operation"] == "update" or request.get("number"):
            pull = gh.pull(request["number"])
        else:
            pulls = gh.pulls("all")
            marker = "<!-- relay:pr-request " + request["request_id"] + " -->"
            marked = [p for p in pulls if marker in (p.get("body") or "")]
            matches = [p for p in marked if matching(p, request["repository"], request["head"], request["base"])]
            if len(marked) != 1 or len(matches) != 1:
                candidates = [p["number"] for p in pulls if matching(p, request["repository"], request["head"], request["base"])]
                raise RelayError("uncertain", "Creation cannot be reconciled uniquely; no POST resent. Candidate PRs: " + str(candidates))
            request["number"] = matches[0]["number"]
            store.save(request)
            pull = gh.pull(request["number"])
        return verify_pull(store, request, pull, gh)
    except RelayError as exc:
        request.update(status="uncertain", failure={"stage": "reconcile", "code": exc.code, "message": str(exc)[:2000]})
        store.save(request)
        raise


def check_refs(request, repo, gh, allow_pushed=False):
    if gh.api(gh.prefix)["default_branch"] != request["default"]:
        raise RelayError("stale", "Repository default branch changed; prepare again.")
    for name, sha in ((request["base"], request["base_sha"]), (request["default"], request["default_sha"])):
        if gitrepo.remote_tip(repo, name) != sha:
            raise RelayError("stale", "Base/default SHA changed; inspect diff and prepare again.")
    local = gitrepo.local_tip(repo["root"], request["head"])
    if local != request["local_sha"]:
        raise RelayError("stale", "Local source moved; inspect diff and prepare again.")
    remote = gitrepo.remote_tip(repo, request["head"])
    expected = request["head_sha"] if allow_pushed else request["remote_sha"]
    if remote != expected:
        raise RelayError("stale", "Remote source moved; inspect diff and prepare again.")


def push_source(store, request, repo, gh):
    if not request["push_needed"]:
        return
    if request["constraints"]["no_push"] or request["head"] == request["default"]:
        raise RelayError("permission", "Required source push is prohibited; candidate remains prepared.")
    root, remote = repo["root"], repo["remote"]
    urls = gitrepo.git(root, "remote", "get-url", "--push", "--all", remote).splitlines()
    if not gitrepo.github_remote(request["remote_url"]) or not urls or any(gitrepo.github_remote(url) != gitrepo.github_remote(request["remote_url"]) for url in urls):
        raise RelayError("repository", "Push URLs do not all identify the selected GitHub repository.")
    mirror = gitrepo.git(root, "config", "--type=bool", "--default=false", "--get", "remote." + remote + ".mirror")
    if mirror == "true":
        raise RelayError("repository", "Mirror remotes cannot push a single PR source ref.")
    check_refs(request, repo, gh)
    request["status"] = "pushing"
    store.save(request)
    try:
        gitrepo.git(root, "push", "--no-follow-tags", "--recurse-submodules=no", remote,
                    request["head_sha"] + ":refs/heads/" + request["head"])
    except RelayError:
        if gitrepo.remote_tip(repo, request["head"]) != request["head_sha"]:
            raise RelayError("uncertain", "Push was not confirmed; resume checks the remote SHA first.")
    if gitrepo.remote_tip(repo, request["head"]) != request["head_sha"]:
        raise RelayError("stale", "Source SHA differs after push.")
    request.update(status="ready", pushed_sha=request["head_sha"])
    store.save(request)


def create_request(data, repo, gh, store):
    request = store.load(data["request_id"])
    check_identity(request, repo, store)
    if request["status"] in UNCERTAIN or request["status"] in ("recorded", "existing"):
        return recover(store, request, gh) if request["status"] != "existing" else request["result"]
    if request["operation"] != "create" or data.get("execution_authorized") is not True:
        raise RelayError("approval", "An explicit PR execution request is required.")
    if data.get("hash") != request["hash"] or candidate_hash(request) != request["hash"]:
        raise RelayError("stale", "Candidate hash differs; prepare the reviewed content again.")
    if request["constraints"]["draft_only"]:
        return {"status": "prepared", "request_id": request["request_id"], "message": "Body draft only; no remote write."}
    found = existing(gh, repo, request["head"], request["base"])
    if found:
        return save_result(store, request, result_for(found, "existing", repo.get("host", "github.com")), gh)
    steps.require_allowed("pr", request["next_step"])
    if request["status"] == "pushing":
        actual = gitrepo.remote_tip(repo, request["head"])
        if actual == request["head_sha"]:
            request.update(status="ready", pushed_sha=request["head_sha"])
            store.save(request)
        elif actual == request["remote_sha"]:
            request["status"] = "prepared"
            store.save(request)
        else:
            raise RelayError("stale", "Push recovery found an unexpected remote SHA.")
    pushed = request.get("pushed_sha") == request["head_sha"]
    check_refs(request, repo, gh, allow_pushed=request["status"] == "ready" or pushed)
    if request["status"] != "ready" and not pushed:
        push_source(store, request, repo, gh)
    found = existing(gh, repo, request["head"], request["base"])
    if found:
        return save_result(store, request, result_for(found, "existing", repo.get("host", "github.com")), gh)
    check_refs(request, repo, gh, allow_pushed=True)
    request.update(status="creating", payload={key: request[key] for key in ("title", "body", "head", "base", "draft")})
    store.save(request)
    try:
        response = gh.create_pull(request["payload"])
    except RelayError as exc:
        request.update(status="failed" if exc.code == "github_rejected" else "uncertain",
                       failure={"stage": "create", "code": exc.code, "message": str(exc)[:2000]})
        store.save(request)
        if exc.code == "github_rejected":
            found = existing(gh, repo, request["head"], request["base"])
            if found:
                return save_result(store, request, result_for(found, "existing", repo.get("host", "github.com")), gh)
            raise
        return recover(store, request, gh)
    if isinstance(response, dict) and type(response.get("number")) is int:
        request["number"] = response["number"]
        store.save(request)
    return recover(store, request, gh)


def update_request(data, repo, gh, store):
    if data.get("execution_authorized") is not True or type(data.get("number")) is not int or data["number"] < 1:
        raise RelayError("approval", "Explicit update authorization and an exact PR number are required.")
    old = store.load(data["request_id"]) if data.get("request_id") else None
    if old:
        check_identity(old, repo, store)
        if old["operation"] != "update" or old["number"] != data["number"]:
            raise RelayError("conflict", "Selected request belongs to another operation or PR.")
        if old["status"] in UNCERTAIN or old["status"] == "recorded":
            return recover(store, old, gh)
        steps.require_allowed("pr", old["next_step"])
    pull = gh.pull(data["number"])
    if not matching(pull, repo, pull["head"]["ref"], pull["base"]["ref"]):
        raise RelayError("repository", "Only same-repository PR updates are supported.")
    old_body = pull.get("body") or ""
    if data["expected_hash"] != text_hash(pull["title"], old_body):
        raise RelayError("conflict", "PR title/body changed externally; review the complete replacement.")
    body = Path(data["body_file"]).read_text(encoding="utf-8-sig")
    if MARKER.findall(old_body) != MARKER.findall(body):
        raise RelayError("conflict", "Preserve existing request markers in the complete replacement.")
    if "next_step" not in data:
        raise RelayError("input", "Submit the candidate's next_step; it is never filled in automatically.")
    suggestion = steps.normalize("pr", data["next_step"])
    # A complete replacement starts from the live body, which already carries the
    # previously generated block. Rebuild and cut exactly that instead of asking the
    # host to edit the generated region by hand, then keep the new suggestion
    # immediately before the first preserved request marker.
    found = MARKER.search(body)
    body = steps.detach(body, found.group(0) if found else "")[0]
    found = MARKER.search(body)
    head, kept = (body[:found.start()], body[found.start():]) if found else (body, "")
    steps.reserved(head)
    body = head + steps.rendered(suggestion) + "\n\n" + kept if found else head.rstrip("\n") + "\n\n" + steps.rendered(suggestion) + "\n"
    if not isinstance(data["title"], str) or not data["title"].strip() or len(data["title"]) > 256 or len(body) > 65000:
        raise RelayError("input", "Invalid replacement title/body length.")
    request = {"schema": 1, "request_id": old["request_id"] if old else uuid.uuid4().hex, "repository": repo, "common_dir": str(store.common),
               "remote_url": gitrepo.git(repo["root"], "remote", "get-url", repo["remote"]), "operation": "update",
               "number": data["number"], "expected_hash": data["expected_hash"], "title": data["title"], "body": body,
               "head": pull["head"]["ref"], "base": pull["base"]["ref"], "head_sha": pull["head"]["sha"],
               "base_sha": pull["base"]["sha"], "status": "prepared", "next_step": suggestion,
               "previous_title": pull["title"], "previous_body": old_body, "watch": data.get("watch") is True}
    request["hash"] = candidate_hash(request)
    store.save(request)
    (store.request_path(request["request_id"]) / "body.md").write_text(body, encoding="utf-8", newline="\n")
    current = gh.pull(data["number"])
    if text_hash(current["title"], current.get("body") or "") != data["expected_hash"]:
        request["status"] = "failed"
        store.save(request)
        raise RelayError("conflict", "PR changed immediately before update; no PATCH sent.")
    request.update(status="updating", payload={"title": request["title"], "body": body})
    store.save(request)
    try:
        gh.update_pull(data["number"], request["payload"])
    except RelayError as exc:
        request.update(status="failed" if exc.code == "github_rejected" else "uncertain",
                       failure={"stage": "update", "code": exc.code, "message": str(exc)[:2000]})
        store.save(request)
        if exc.code == "github_rejected":
            raise
    return recover(store, request, gh)


def dispatch(data, registry, gh=None, repo=None):
    action = data.get("action")
    if action not in ("inspect", "prepare", "create", "resume", "update"):
        raise RelayError("input", "Unknown PR action.")
    # Parse before creating state directories, even for read-only inspect.
    if action in ("inspect", "prepare"):
        invocation.parse("pr", data.get("raw", ""), registry)
    repo = repo or gitrepo.inspect(data.get("cwd", str(Path.cwd())))
    gh = gh or GitHub(repo["repo"], host=repo.get("host", "github.com"))
    store = PrStore(repo["root"])
    with store.lock():
        pending = store.pending()
        selected = data.get("request_id")
        others = [p for p in pending if p["request_id"] != selected]
        if any(p["status"] in UNCERTAIN or p["status"] == "pushing" for p in others) or (len(pending) > 1 and not selected):
            return {"status": "needs_input", "message": "Select and resume an unfinished request before new work.",
                    "requests": [{k: p.get(k) for k in ("request_id", "status", "head", "base", "operation")} for p in pending]}
        if action == "inspect":
            if pending:
                if selected:
                    request = store.load(selected)
                    check_identity(request, repo, store)
                    if request["operation"] == "create" and request["status"] in ("prepared", "ready", "failed"):
                        return inspect_request(data, repo, gh, registry)
                return {"status": "needs_input", "requests": [{k: p.get(k) for k in ("request_id", "status", "head", "base")} for p in pending]}
            return inspect_request(data, repo, gh, registry)
        if action == "prepare":
            if pending and not selected:
                return {"status": "needs_input", "requests": [{"request_id": p["request_id"], "status": p["status"]} for p in pending]}
            return prepare_request(data, repo, gh, store, registry)
        if action == "update":
            if pending and not selected:
                return {"status": "needs_input", "requests": [{"request_id": p["request_id"], "status": p["status"]} for p in pending]}
            return update_request(data, repo, gh, store)
        if action == "create":
            return create_request(data, repo, gh, store)
        request = store.load(data["request_id"])
        check_identity(request, repo, store)
        if request["status"] == "existing":
            pull = gh.pull(request["result"]["number"])
            if not matching(pull, repo, request["head"], request["base"]):
                raise RelayError("conflict", "Existing PR branches changed.")
            return save_result(store, request, result_for(pull, "existing", repo.get("host", "github.com")), gh)
        if request["status"] in UNCERTAIN or request["status"] == "recorded":
            return recover(store, request, gh)
        if request["status"] == "pushing":
            actual = gitrepo.remote_tip(repo, request["head"])
            if actual not in (request["head_sha"], request["remote_sha"]):
                raise RelayError("stale", "Push recovery found an unexpected SHA.")
            request["status"] = "ready" if actual == request["head_sha"] else "prepared"
            if request["status"] == "ready":
                request["pushed_sha"] = actual
            store.save(request)
        return {"status": request["status"], "request_id": request["request_id"], "hash": request["hash"],
                "message": "No remote write. Use create with the saved hash and current execution authorization."}
