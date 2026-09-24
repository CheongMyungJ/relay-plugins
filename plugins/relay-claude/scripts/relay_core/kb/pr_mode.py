"""PR mode: inspect one PR, prepare an exact candidate, publish after approval (spec R6).

State: `.relay/kb/pr/<n>/<request_id>/` with request.json, snapshot.json, candidate.json,
plan.json, files.json, review.md, change.diff, authorization.json, apply.json and
result.json. Publication is apply -> commit -> push -> optional small PR -> comment,
each a recorded stage; an open PR's head is compared with the expected post-head right
before the comment, and a moved head stops with head_advanced.
"""
import difflib
import json
import uuid
from pathlib import Path
from .. import RelayError, invocation, next_step as steps, repository as gitrepo, server_context, watch
from ..artifacts import digest
from ..state import read_json, write_json
from .. import review_snapshot as snapshots, review_git
from . import changes as changeset, gates, layout, lookup, remote, sources as sourcing
from .stores import KbPrStore

MARKER = "<!-- relay:kb "
SMALL_PR_MARKER = "<!-- relay:kb-pr "
COMMENT_LIMIT = 65000
FINAL = ("recorded",)
LOCKED_STATES = ("authorized", "applying", "applied", "committed", "pushed", "pr_created", "posting", "uncertain", "head_advanced")


def fail(message, code="input"):
    raise RelayError(code, message)


def hashed(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def knowledge_path(path):
    return path.startswith(layout.ROOT + "/") or path.rsplit("/", 1)[-1] in layout.INSTRUCTIONS


def diff_paths(code, root, head):
    return review_git.changed_paths(root, code.get("merge_base"), head)


def sources_digest(snapshot):
    comments = [(c["key"], c["body_hash"]) for c in snapshot["comments"]]
    documents = [(issue["number"], kind, doc["reference"]) for issue in snapshot["linked_issues"]
                 for kind, doc in sorted(issue.get("documents", {}).items())]
    reports = [(issue["number"], run_id, run["report"]["target"], digest(run["report"]["body"])) for issue in snapshot["linked_issues"]
               for run_id, run in sorted(issue.get("runs", {}).items()) if run.get("report")]
    return hashed({"comments": comments, "documents": documents, "reports": reports})


def request_hash(request):
    keys = ("repository", "pr", "merged", "source_pr_head", "apply_base_sha", "push", "options", "code_hash", "kb_tree", "sources", "scope")
    return hashed({k: request[k] for k in keys})


def scope_for(kb, paths):
    if not kb["present"] or not paths:
        return []
    query = lookup.normalize_query({"paths": paths, "limit": lookup.MAX_LIMIT})
    found, _, _, _ = lookup.candidates(kb, query)
    return sorted(entry["id"] for entry, _, _ in found if entry["status"] == "active")


def inspect(data, repo, gh, store, registry):
    parsed = invocation.parse("kb", data.get("raw", ""), registry)
    number, options = parsed["pr"], parsed["options"]
    pull = gh.pull(number)
    merged = bool(pull.get("merged") or pull.get("merged_at"))
    if not merged and pull.get("state") != "open":
        fail("Closed, unmerged PRs are not recorded.", "closed")
    if options.get("pr") and not merged:
        fail("--pr applies to merged PRs only; an open PR receives the KB commit on its own branch.")
    default = gh.api(gh.prefix)["default_branch"]
    default_sha = gitrepo.remote_tip(repo, default, fetch=True)
    if not default_sha:
        fail("Remote default branch is missing.", "branch")
    request_id = uuid.uuid4().hex
    snapshot = snapshots.snapshot(repo, gh, number, "kb-" + request_id)
    code = snapshot.get("code") or {}
    if not code.get("complete"):
        fail("PR diff evidence is incomplete: " + "; ".join(code.get("missing", ["unknown"])), "incomplete")
    paths = diff_paths(code, repo["root"], snapshot["pull"]["head"]["sha"])
    source_head = pull["head"]["sha"]
    apply_base = default_sha if merged else source_head
    mode = "open" if not merged else "merged-pr" if options.get("pr") else "merged-direct"
    kb = layout.load(layout.TreeReader(repo["root"], apply_base))
    scope = scope_for(kb, paths)
    verdicts = {}
    if scope:
        from . import reconfirm as reconfirmation
        verdicts = {k: v["verdict"] for k, v in reconfirmation.assess(kb, scope, repo["root"], apply_base).items()}
    push = {"repo": (pull["head"].get("repo") or {}).get("full_name"), "ref": pull["head"]["ref"]} if mode == "open" else \
        {"repo": repo["repo"], "ref": default if mode == "merged-direct" else None}
    request = {"schema": 1, "request_id": request_id, "pr": number, "repository": {"host": repo.get("host", "github.com"), "repo": repo["repo"]},
               "common_dir": str(store.common), "merged": merged, "source_pr_head": source_head, "apply_base_sha": apply_base,
               "default": default, "default_sha": default_sha, "mode": mode, "push": push, "options": {k: options[k] for k in sorted(options)},
               "watch": bool(options.get("watch")), "code_hash": snapshots.hashed(code), "kb_tree": changeset.tree_digest(layout.TreeReader(repo["root"], apply_base)),
               "sources": sources_digest(snapshot), "scope": verdicts, "diff_paths": paths, "pull_url": pull["html_url"],
               "head_ref": pull["head"]["ref"], "base_ref": pull["base"]["ref"], "status": "inspected", "issued": {}}
    request["request_hash"] = request_hash(request)
    store.save(request)
    store.set_current(number, request_id)
    write_json(store.request_path(number, request_id) / "snapshot.json", snapshot)
    summary = lookup.summary(kb, paths=paths, sha=apply_base, cwd=repo["root"])
    return {"status": "inspected", "request_id": request_id, "request_hash": request["request_hash"],
            "work_path": str(store.request_path(number, request_id)), "pr": {"number": number, "url": pull["html_url"], "state": pull["state"],
            "merged": merged, "head": pull["head"]["ref"], "base": pull["base"]["ref"], "title": pull["title"]},
            "mode": mode, "source_pr_head": source_head, "apply_base_sha": apply_base, "diff_paths": paths, "kb": summary,
            "scope": [{"id": k, "verdict": v} for k, v in sorted(verdicts.items())],
            "linked_issues": [{"number": i["number"], "documents": sorted(i.get("documents", {})), "runs": sorted(i.get("runs", {}))}
                              for i in snapshot["linked_issues"]],
            "comments": [{"key": c["key"], "author": c["author"], "chars": len(c["body"])} for c in snapshot["comments"]],
            "sections": ["summary", "sources", "entries"], "warnings": snapshot["warnings"]}


def page_items(request, snapshot, kb, section):
    if section == "summary":
        items = [{"kind": "pull_body", "text": snapshot["pull"].get("body") or ""}]
        for issue in snapshot["linked_issues"]:
            items.append({"kind": "issue_body", "number": issue["number"], "text": issue.get("body", ""), "warning": issue.get("warning")})
            for name, doc in sorted(issue.get("documents", {}).items()):
                items.append({"kind": "document", "number": issue["number"], "document": name, "reference": doc["reference"],
                              "source": f"issue:{issue['number']}/comment:{doc['reference']['target']}", "stale": doc["stale"], "text": doc["body"]})
            for run_id, run in sorted(issue.get("runs", {}).items()):
                if run.get("report"):
                    items.append({"kind": "report", "number": issue["number"], "run_id": run_id, "source": f"issue:{issue['number']}/comment:{run['report']['target']}",
                                  "head_matches": run.get("head_matches"), "warning": run.get("warning"), "text": run["report"]["body"]})
        return items
    if section == "sources":
        items = []
        for c in snapshot["comments"]:
            kind = {"issue": "comment", "inline": "inline", "review": "review"}[c["kind"]]
            source = f"pr:{request['pr']}/{kind}:{c['id']}" if c["body"].strip() else None
            items.append({"kind": c["kind"], "id": c["id"], "author": c["author"], "source": source, "root_id": c.get("root_id"),
                          "path": c.get("path"), "text": c["body"], "review_marker": "<!-- relay:review" in c["body"]})
        items.append({"kind": "diff", "text": (snapshot.get("code") or {}).get("diff", "")})
        return items
    if section == "entries":
        return [{"kind": "entry", "verdict": request["scope"].get(k), **lookup.full_of(kb["entries"][k], [], kb)}
                for k in sorted(request["scope"]) if k in kb["entries"]]
    fail("section is summary, sources or entries.")


def read_page(data, repo, gh, store):
    number = data.get("pr")
    request = store.load(number, data["request_id"])
    snapshot = read_json(store.request_path(number, request["request_id"]) / "snapshot.json")
    kb = layout.load(layout.TreeReader(repo["root"], request["apply_base_sha"]))
    items = page_items(request, snapshot, kb, data.get("section"))
    query = {"request_id": request["request_id"], "pr": number, "section": data.get("section")}
    bind = {"sha": request["apply_base_sha"], "request": request["request_hash"]}
    template = lookup.request_template(repo["root"], "inspect", **query)
    position = (0, 0)
    if data.get("cursor"):
        payload = lookup.read_cursor(data["cursor"], "pr_section")
        if payload["q"] != query:
            raise RelayError("conflict", "Cursor belongs to another request, PR or section; query again.")
        offset = lookup.check_binding(payload, bind)
        position = (offset // 100000, offset % 100000)
    result = {"request_id": request["request_id"], "section": data.get("section"), "items": [], "truncated": False,
              "next_request": lookup.next_request(template, lookup.encode_cursor("pr_section", query, bind, len(items) * 100000))}
    index, offset = position
    while index < len(items):
        item = dict(items[index])
        text = item.get("text", "")
        remainder = text[offset:]
        item["text"], item["char_start"], item["continued"] = remainder, offset, False
        result["items"].append(item)
        if lookup.size(result) <= lookup.PAGE_BUDGET:
            index, offset = index + 1, 0
            continue
        result["items"].pop()
        low, high, best = 1, len(remainder), 0
        while low <= high:
            middle = (low + high) // 2
            item["text"], item["continued"] = remainder[:middle], True
            result["items"].append(item)
            ok = lookup.size(result) <= lookup.PAGE_BUDGET
            result["items"].pop()
            if ok:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        if best == 0:
            if not result["items"]:
                fail("A single item exceeds the page budget.", "length")
            break
        item["text"], item["continued"] = remainder[:best], True
        result["items"].append(item)
        offset += best
        break
    if index < len(items):
        result["truncated"] = True
        result["next_request"] = lookup.next_request(template, lookup.encode_cursor("pr_section", query, bind, index * 100000 + offset))
    else:
        result["next_request"] = None
    return result


def verdict_line(request, no_change):
    if no_change:
        return "판정: KB 파일 변경 없음"
    if request["mode"] == "open":
        return f"판정: KB 커밋 {{{{commit}}}}: 부모 {request['source_pr_head']}, 지식 경로만 포함"
    line = f"판정: KB 커밋 {{{{commit}}}}: 적용 기준 기본 브랜치 {request['apply_base_sha']}, 지식 경로만 포함"
    if request["mode"] == "merged-pr":
        line += "\nKB 변경 PR: {{pr_url}}"
    return line


def comment_template(request, check, changes, no_change):
    lines = [f"# KB 기록: PR #{request['pr']}", "", verdict_line(request, no_change), "", "## 채택"]
    adopted = []
    for op in changes["ops"]:
        if op["op"] in ("create", "update", "supersede"):
            identity = check["plan"]["issued"].get(op["op_id"]) or op.get("id")
            entry = check["post"]["entries"][identity]
            adopted.append(f"- {identity} [{entry['type']}] {entry['rule']}\n  - 코드불가: {entry['not_in_code']}\n  - 유인: {entry['incentive']}")
        elif op["op"] == "absorb":
            adopted.append(f"- {op['id']} → {op['pointer']} (흡수)")
        elif op["op"] == "delete":
            adopted.append(f"- {op['id']} 삭제")
    lines += adopted or ["- 없음"]
    lines += ["", "## 재확인"]
    reconfirm_lines = []
    for identity, verdict in sorted(check["verdicts"].items()):
        handled = next((op["op"] for op in changes["ops"] if op.get("id") == identity or op.get("old") == identity or identity in (op.get("ids") or [])), None)
        reconfirm_lines.append(f"- {identity}: {verdict['verdict']} — {verdict['reason']}" + (f" (처리: {handled})" if handled else ""))
    lines += reconfirm_lines or ["- 없음"]
    lines += ["", "## 기각"]
    lines += [f"- {r['rule']} — {r['reason']}" for r in changes["rejected"]] or ["- 없음"]
    lines += ["", "## 사람 확인 필요"]
    needs = [f"- {op.get('id') or op.get('old')}: {op['op']} 근거 — {op['evidence']}" for op in changes["ops"] if op.get("evidence") and op["op"] != "reconfirm"]
    needs += [f"- {identity}: 재확인 근거 — {text}" for op in changes["ops"] if op["op"] == "reconfirm" for identity, text in (op.get("evidence") or {}).items()]
    lines += needs or ["- 없음"]
    return "\n".join(lines) + "\n"


def render_comment(template, suggestion, request_id):
    return template + "\n" + steps.rendered(suggestion) + "\n\n" + MARKER + request_id + " -->"


def prepare(data, repo, gh, store):
    number = data.get("pr")
    request = store.load(number, data["request_id"])
    if request["status"] in FINAL:
        fail("This request is already recorded.", "conflict")
    if data.get("publish_only"):
        return prepare_publish_only(data, request, store)
    if request["status"] in LOCKED_STATES:
        fail("Publication started; resume publish with the saved authorization instead of preparing again.", "conflict")
    if data.get("request_hash") != request["request_hash"]:
        fail("request_hash differs; inspect again before preparing.", "stale")
    changes = read_json(Path(data["changes_file"]))
    if "next_step" not in data:
        fail("next_step — required; expected object with next and reason (never filled in automatically)")
    suggestion = steps.normalize("kb", steps.validate(data["next_step"]))
    kb = layout.load(layout.TreeReader(repo["root"], request["apply_base_sha"]))
    check = gates.check(kb, changes, sha=request["apply_base_sha"], root=repo["root"], gh=gh, scope_ids=sorted(request["scope"]),
                        execution_id=request["request_id"], label=f"PR #{number}", issued=request.get("issued"))
    if check["status"] == "candidates":
        return {"status": "candidates", "request_id": request["request_id"], "pending": check["pending"]}
    if check["status"] == "capacity_resolution_required":
        return {**check, "request_id": request["request_id"]}
    changes = changeset.validate(changes)
    plan_value, files = check["plan"], check["files"]
    reader = layout.TreeReader(repo["root"], request["apply_base_sha"])
    no_change = all(v["pre"] == v["post"] for v in plan_value["files"].values())
    template = comment_template(request, check, changes, no_change)
    expected_post_head = {"kind": "source_pr_head", "sha": request["source_pr_head"]} if no_change or request["mode"] != "open" else {"kind": "kb_commit"}
    candidate = {"request_id": request["request_id"], "request_hash": request["request_hash"], "plan_digest": plan_value["digest"],
                 "plan": plan_value, "template": template, "next_step": suggestion, "expected_post_head": expected_post_head,
                 "no_change": no_change, "changes": changes}
    candidate["hash"] = hashed({k: candidate[k] for k in ("request_hash", "plan_digest", "template", "next_step", "expected_post_head", "plan")})
    rendered = render_comment(template, suggestion, request["request_id"])
    if len(rendered) > COMMENT_LIMIT:
        fail("Shorten the candidate; the result comment exceeds the supported length.", "length")
    folder = store.request_path(number, request["request_id"])
    diff = []
    for path, text in sorted(files.items()):
        before = reader.read(path) or ""
        diff += difflib.unified_diff(before.splitlines(True), (text or "").splitlines(True), fromfile=f"a/{path}", tofile=f"b/{path}")
    review = rendered + "\n\n---\n\n## 예정 파일\n" + "\n".join(f"- {p}" for p in sorted(files)) + "\n\n## KB diff\n\n```diff\n" + "".join(diff) + "```\n"
    review += f"\nCandidate: `{candidate['hash']}`\n"
    write_json(folder / "candidate.json", candidate)
    write_json(folder / "plan.json", plan_value)
    write_json(folder / "files.json", files)
    (folder / "review.md").write_text(review, encoding="utf-8", newline="\n")
    (folder / "change.diff").write_text("".join(diff), encoding="utf-8", newline="\n")
    request.update(status="prepared", candidate_hash=candidate["hash"], issued=plan_value["issued"], next_step=suggestion, checked=check["checked"])
    store.save(request)
    return {"status": "prepared", "request_id": request["request_id"], "hash": candidate["hash"], "review": str(folder / "review.md"),
            "diff": str(folder / "change.diff"), "next_step": suggestion, "no_change": no_change, "issued": plan_value["issued"],
            "files": sorted(files), "verdicts": check["verdicts"], "warnings": check["warnings"]}


def prepare_publish_only(data, request, store):
    """After head_advanced with a confirmed write and no comment yet: new expected head, new comment candidate."""
    if request["status"] != "head_advanced":
        fail("publish_only applies after head_advanced only.", "conflict")
    stages = request.get("stages", {})
    if stages.get("comment", {}).get("status") not in (None, "planned"):
        fail("Reconcile the pending comment before preparing a publish-only candidate.", "uncertain")
    folder = store.request_path(request["pr"], request["request_id"])
    candidate = read_json(folder / "candidate.json")
    if "next_step" not in data:
        fail("next_step — required; expected object with next and reason (never filled in automatically)")
    suggestion = steps.normalize("kb", steps.validate(data["next_step"]))
    observed = request.get("observed_head")
    if not observed:
        fail("No observed head is recorded.", "conflict")
    candidate.update(next_step=suggestion, expected_post_head={"kind": "observed", "sha": observed})
    candidate["hash"] = hashed({k: candidate[k] for k in ("request_hash", "plan_digest", "template", "next_step", "expected_post_head", "plan")})
    request.pop("stages", {}).pop("comment", None) if False else request.get("stages", {}).pop("comment", None)
    write_json(folder / "candidate.json", candidate)
    rendered = render_comment(candidate["template"], suggestion, request["request_id"])
    (folder / "review.md").write_text(rendered + f"\n\nCandidate: `{candidate['hash']}`\n", encoding="utf-8", newline="\n")
    request.update(status="prepared_publish", candidate_hash=candidate["hash"], next_step=suggestion)
    store.save(request)
    return {"status": "prepared", "request_id": request["request_id"], "hash": candidate["hash"], "review": str(folder / "review.md"),
            "next_step": suggestion, "expected_post_head": observed}


def register(repo, request, path):
    path = Path(path).resolve()
    if path == Path(repo["root"]).resolve():
        fail("KB publication requires a separate connected worktree.", "worktree")
    if gitrepo.common_dir(path) != gitrepo.common_dir(repo["root"]) or path not in gitrepo.worktrees(repo["root"]):
        fail("Worktree is not connected to this repository.", "worktree")
    branch = gitrepo.git(path, "branch", "--show-current")
    if not branch or branch in (request["head_ref"], request["base_ref"], request["default"]):
        fail("Use a separate local branch for the KB commit.", "branch")
    if gitrepo.git(path, "rev-parse", "HEAD") != request["apply_base_sha"] or gitrepo.git(path, "status", "--porcelain"):
        fail("Register a clean worktree at apply_base_sha before publishing.", "worktree")
    return {"path": str(path), "branch": branch}


def recheck(request, repo, gh, store, folder):
    """Before the first application: PR state and tip, then every cited source's digest."""
    pull = gh.pull(request["pr"])
    if request["mode"] == "open":
        if pull.get("state") != "open" or pull.get("merged"):
            fail("PR is no longer open; inspect again.", "stale")
        if pull["head"]["sha"] != request["source_pr_head"]:
            request.update(status="head_advanced", observed_head=pull["head"]["sha"])
            store.save(request)
            fail("PR head moved before the KB commit; inspect again for the new head.", "head_advanced")
    elif not (pull.get("merged") or pull.get("merged_at")):
        fail("PR is no longer merged.", "stale")
    snapshot = read_json(folder / "snapshot.json")
    known = {c["key"]: c["body_hash"] for c in snapshot["comments"]}
    candidate = read_json(folder / "candidate.json")
    checker = sourcing.Sources(gh, repo["root"], request["apply_base_sha"])
    for op in candidate["changes"]["ops"]:
        for value in (op.get("entry") or {}).get("sources", []):
            result = checker.check(value, new=False)
            parsed = sourcing.parse(value)
            if parsed["kind"] in ("pr_comment", "pr_inline", "pr_review") and parsed["number"] == request["pr"]:
                key = {"pr_inline": "inline", "pr_comment": "issue", "pr_review": "review"}[parsed["kind"]] + ":" + str(parsed["id"])
                if key in known and result.get("digest") != known[key]:
                    fail(f"Cited source {value} was revised after inspection; inspect and prepare again.", "stale")
            if not result["valid"] and not result["exists"]:
                fail(f"Cited source {value} is no longer readable.", "source")


def publish(data, repo, gh, store):
    number = data.get("pr")
    request = store.load(number, data["request_id"])
    folder = store.request_path(number, request["request_id"])
    candidate = read_json(folder / "candidate.json") if (folder / "candidate.json").exists() else None
    if not candidate or request.get("candidate_hash") != candidate["hash"]:
        fail("Prepare and review a candidate first.", "approval")
    if data.get("hash") != candidate["hash"] or data.get("approved") is not True or not isinstance(data.get("user"), str) or not data["user"].strip():
        fail("Exact candidate approval with the approving user is required.", "approval")
    if request["status"] in FINAL:
        # A recorded request never writes again; only an unapplied --watch marking is retried.
        result = read_json(folder / "result.json")
        if request["watch"] and not (request.get("watch_result") or {}).get("applied"):
            request["watch_result"] = watch.mark(gh, number, True, request.get("watch_result"))
            result["watch"] = request["watch_result"]
        if request["watch"] and request.get("small_pr") and not (request.get("watch_pr_result") or {}).get("applied"):
            request["watch_pr_result"] = watch.mark(gh, request["small_pr"]["number"], True, request.get("watch_pr_result"))
            result["watch_small_pr"] = request["watch_pr_result"]
        store.save(request)
        write_json(folder / "result.json", result)
        return result
    if request["status"] == "head_advanced":
        fail("Prepare a publish-only candidate for the new head first.", "head_advanced")
    server_context.authorize("kb", request["request_id"], candidate["hash"], user=data["user"], operation=request["mode"])
    if not (folder / "authorization.json").exists():
        write_json(folder / "authorization.json", {"request_id": request["request_id"], "hash": candidate["hash"], "approved": True, "user": data["user"]})
    stages = remote.Stages(request, lambda: store.save(request))
    plan_value, files = candidate["plan"], read_json(folder / "files.json")
    if "application" not in request:
        if not data.get("worktree"):
            request["status"] = "authorized"
            store.save(request)
            return {"status": "authorized", "request_id": request["request_id"], "apply_base_sha": request["apply_base_sha"],
                    "next": "Create a clean connected worktree on a separate branch at apply_base_sha and publish again with worktree."}
        recheck(request, repo, gh, store, folder)
        request["application"] = register(repo, request, data["worktree"])
        request["status"] = "applying"
        store.save(request)
    path = request["application"]["path"]
    if not candidate["no_change"]:
        applied = changeset.apply(plan_value, files, path, folder / "apply.json")
        request["status"] = "applied"
        store.save(request)
        paths = [p for p in applied["paths"] if knowledge_path(p)]
        if len(paths) != len(applied["paths"]):
            fail("Rendered files include a non-knowledge path.", "verification")
        commit = remote.commit(stages, "commit", path, parent=request["apply_base_sha"], paths=paths, post_tree=plan_value["post_tree"],
                               message=f"kb: record PR #{number}")
        request.update(commit=commit["sha"], status="committed")
        store.save(request)
        if request["mode"] == "open":
            url = review_git.destination(repo, {"repo": {"full_name": request["push"]["repo"]}}, push=True)
            pushed = remote.push(stages, "push", path, url=url, ref=request["push"]["ref"], expected_remote=request["source_pr_head"], local_commit=commit["sha"])
        elif request["mode"] == "merged-direct":
            url = gitrepo.git(repo["root"], "remote", "get-url", "--push", repo["remote"])
            pushed = remote.push(stages, "push", path, url=url, ref=request["default"], expected_remote=request["default_sha"], local_commit=commit["sha"])
        else:
            url = gitrepo.git(repo["root"], "remote", "get-url", "--push", repo["remote"])
            branch = request["application"]["branch"]
            pushed = remote.push(stages, "push", path, url=url, ref=branch, expected_remote=None, local_commit=commit["sha"])
        request.update(remote_sha=pushed["remote_sha"], status="pushed")
        store.save(request)
        if request["mode"] == "merged-pr":
            body = (f"KB 기록: PR #{number}의 지식 항목을 기본 브랜치 {request['apply_base_sha'][:12]} 기준으로 반영한다.\n\n"
                    f"KB 커밋 {commit['sha']}: 적용 기준 기본 브랜치 {request['apply_base_sha']}, 지식 경로만 포함\n\n"
                    + SMALL_PR_MARKER + request["request_id"] + " -->\n")
            created = remote.create_pull(stages, "small_pr", gh, repo=repo["repo"], head=request["application"]["branch"], base=request["default"],
                                         title=f"kb: record PR #{number}", body=body, draft=False, marker=SMALL_PR_MARKER + request["request_id"] + " -->")
            request.update(small_pr=created, status="pr_created")
            store.save(request)
    expected = candidate["expected_post_head"]
    if request["mode"] == "open":
        target = request.get("commit") if expected["kind"] == "kb_commit" else expected["sha"]
        pull = gh.pull(number)
        if pull["head"]["sha"] != target:
            request.update(status="head_advanced", observed_head=pull["head"]["sha"])
            store.save(request)
            return {"status": "head_advanced", "request_id": request["request_id"], "expected_head": target, "observed_head": pull["head"]["sha"],
                    "commit": request.get("commit"), "remote_sha": request.get("remote_sha"),
                    "next": "Inspect the new commits; prepare with publish_only and the new head, approve, then publish again."}
    body = render_comment(candidate["template"], candidate["next_step"], request["request_id"])
    body = body.replace("{{commit}}", request.get("commit") or "").replace("{{pr_url}}", (request.get("small_pr") or {}).get("url", ""))
    if "{{" in body.split(MARKER)[0]:
        fail("Unresolved placeholder in the result comment.", "verification")
    steps.require_allowed("kb", candidate["next_step"])
    request["status"] = "posting"
    store.save(request)
    marker = MARKER + request["request_id"] + " -->"
    author = gh.viewer()
    posted = remote.comment(stages, "comment", gh, number=number, body=body, marker=marker, author=author)
    request.update(status="recorded", comment=posted)
    store.save(request)
    result = {"status": "recorded", "request_id": request["request_id"], "pr": number, "url": request["pull_url"], "mode": request["mode"],
              "commit": request.get("commit"), "remote_sha": request.get("remote_sha"), "comment_url": posted["url"], "comment_id": posted["id"],
              "small_pr": request.get("small_pr"), "no_change": candidate["no_change"], "next_step": candidate["next_step"],
              "verdict": verdict_line(request, candidate["no_change"]).replace("{{commit}}", request.get("commit") or "").replace("{{pr_url}}", (request.get("small_pr") or {}).get("url", ""))}
    if request["watch"]:
        request["watch_result"] = watch.mark(gh, number, True, request.get("watch_result"))
        result["watch"] = request["watch_result"]
        if request.get("small_pr"):
            request["watch_pr_result"] = watch.mark(gh, request["small_pr"]["number"], True, request.get("watch_pr_result"))
            result["watch_small_pr"] = request["watch_pr_result"]
    receipt = server_context.publication({
        "kind": "kb", "request_id": request["request_id"], "hash": candidate["hash"], "pr": number,
        "repo": repo.get("repo"), "target": str(posted["id"]), "url": posted["url"], "commit": request.get("commit"),
        "small_pr": (request.get("small_pr") or {}).get("number"), "result": "recorded"})
    if receipt is not None:
        result["server_receipt"] = receipt
    store.save(request)
    write_json(folder / "result.json", result)
    return result


def dispatch(data, registry, repo, gh):
    action = data.get("action")
    store = KbPrStore(repo["root"])
    if action == "inspect":
        if data.get("request_id"):
            if data.get("raw") or data.get("pr") is None:
                fail("A page read names request_id, pr and section without a new invocation.")
            with store.lock(data["pr"]):
                return read_page(data, repo, gh, store)
        parsed = invocation.parse("kb", data.get("raw", ""), registry)
        with store.lock(parsed["pr"]):
            open_requests = [store.load(parsed["pr"], r) for r in store.requests(parsed["pr"])]
            pending = [r for r in open_requests if r["status"] in LOCKED_STATES]
            if pending:
                return {"status": "resume_required", "requests": [{"request_id": r["request_id"], "status": r["status"]} for r in pending]}
            return inspect(data, repo, gh, store, registry)
    if action == "prepare":
        with store.lock(data.get("pr")):
            return prepare(data, repo, gh, store)
    if action == "publish":
        with store.lock(data.get("pr")):
            request = store.load(data.get("pr"), data["request_id"])
            try:
                return publish(data, repo, gh, store)
            except RelayError as exc:
                request = store.load(data.get("pr"), data["request_id"])
                if request["status"] not in FINAL + ("head_advanced",):
                    request["error"] = {"code": exc.code, "message": str(exc)[:2000]}
                    if exc.code in ("uncertain", "conflict"):
                        request["status"] = exc.code
                    store.save(request)
                raise
    fail("Unknown PR-mode action.")
