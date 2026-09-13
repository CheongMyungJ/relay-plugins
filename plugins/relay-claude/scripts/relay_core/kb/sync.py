"""Execution mode: extract from paths or refresh the whole KB in batches on a draft PR (spec R7).

The invocation authorizes KB changes, commits, pushes and PR creation. `begin` pins the
default branch tip, a task branch and a sibling worktree; `batch` fixes one batch of at
most 15 files or entries; `checkpoint` runs the gates, applies, commits, pushes and
creates or updates the draft PR; `resume` recovers unfinished stages with a new budget;
`finish` verifies every target is handled, finalizes the body and marks the PR ready.
"""
import json
import uuid
from pathlib import Path
from .. import RelayError, invocation, next_step as steps, repository as gitrepo, watch
from ..artifacts import digest
from ..state import read_json, write_json
from . import changes as changeset, fragment as fragments, gates, layout, listing, lookup, reconfirm as reconfirmation, remote
from .stores import KbSyncStore

MARKER = "<!-- relay:kb-sync "
BATCH = 15
LIMITS = {"extract": 60, "refresh": 80}
BODY_LIMIT = 65000


def fail(message, code="input"):
    raise RelayError(code, message)


def remaining(run):
    return [t for t in run["targets"] if not t["done"]]


def budget_left(run):
    return run["budget"]["limit"] - run["budget"]["used"]


def next_step_for(run):
    if run["status"] == "finished":
        return {"next": "review", "reason": "KB 변경 PR 검토"} if run.get("pr") else {"next": None, "reason": "변경 없음"}
    if run["status"] == "paused":
        return {"next": "kb-sync", "reason": f"처리 상한 도달; 남은 대상 {len(remaining(run))}개를 --resume {run['run_id']}로 이어간다."}
    return {"next": None, "reason": "실행 중; 배치 처리와 checkpoint가 진행되고 있다."}


def body_for(run):
    """The draft PR body: constraints first, then adoption, reconfirmation, rejection and human checks."""
    done = [t for t in run["targets"] if t["done"]]
    lines = [f"# KB sync: {run['mode']} ({run['run_id']})", "",
             f"- 기준 SHA: {run['base_sha']}", f"- 브랜치: {run['branch']}", f"- 예산: 이번 호출 {run['budget']['limit']} 중 {run['budget']['used']} 사용",
             f"- 처리: {len(done)}개 완료, {len(remaining(run))}개 남음", "", "## 채택"]
    adopted = [a for b in run["batches"].values() for a in b.get("adopted", [])]
    lines += [f"- {a['id']} [{a['type']}] {a['rule']}\n  - 코드불가: {a['not_in_code']}\n  - 유인: {a['incentive']}" for a in adopted] or ["- 없음"]
    lines += ["", "## 재확인"]
    reconfirmed = [r for b in run["batches"].values() for r in b.get("reconfirmed", [])]
    lines += [f"- {r['id']}: {r['verdict']} — {r['reason']}" + (f" (처리: {r['handled']})" if r.get("handled") else "") for r in reconfirmed] or ["- 없음"]
    lines += ["", "## 기각"]
    rejected = [r for b in run["batches"].values() for r in b.get("rejected", [])]
    lines += [f"- {r['rule']} — {r['reason']}" for r in rejected] or ["- 없음"]
    lines += ["", "## 사람 확인 필요"]
    needs = [n for b in run["batches"].values() for n in b.get("needs", [])]
    lines += [f"- {n}" for n in needs] or ["- 없음"]
    text = "\n".join(lines) + "\n"
    if len(text) > BODY_LIMIT - 2000:
        # A large run publishes counts and commit links; the full ledger stays in the run directory.
        commits = [b.get("commit") for b in run["batches"].values() if b.get("commit")]
        text = "\n".join(lines[:7] + ["", f"채택 {len(adopted)}개, 재확인 {len(reconfirmed)}개, 기각 {len(rejected)}개, 사람 확인 {len(needs)}개.",
                                      "전체 기록은 실행 디렉터리의 ledger에 있고 항목은 다음 커밋에서 확인한다:"] + [f"- {c}" for c in commits]) + "\n"
    return text + "\n" + steps.rendered(next_step_for(run)) + "\n\n" + MARKER + run["run_id"] + " -->\n"


def listing_page(run, cursor=None):
    items = run["listing"]
    binding = {"query": "listing", "sha": run["base_sha"], "kb": run["run_id"]}
    offset = lookup.decode_cursor(cursor, binding) if cursor else 0
    result = {"run_id": run["run_id"], "items": [], "truncated": False, "next_cursor": lookup.encode_cursor({**binding, "offset": len(items)})}
    index = offset
    while index < len(items):
        result["items"].append(items[index])
        if lookup.size(result) > lookup.PAGE_BUDGET and len(result["items"]) > 1:
            result["items"].pop()
            break
        index += 1
    result["next_cursor"] = lookup.encode_cursor({**binding, "offset": index}) if index < len(items) else None
    result["truncated"] = index < len(items)
    return result


def begin(data, repo, gh, store, registry):
    parsed = invocation.parse("kb-sync", data.get("raw", ""), registry)
    options = parsed["options"]
    if options.get("resume"):
        return resume({"run_id": options["resume"], "limit": options.get("limit"), "watch": options.get("watch")}, repo, gh, store)
    unfinished = store.unfinished()
    if unfinished and not data.get("new_run"):
        return {"status": "resume_required", "runs": [{"run_id": r["run_id"], "status": r["status"], "mode": r["mode"]} for r in unfinished]}
    default = gh.api(gh.prefix)["default_branch"]
    base_sha = gitrepo.remote_tip(repo, default, fetch=True)
    if not base_sha:
        fail("Remote default branch is missing.", "branch")
    mode = "extract" if parsed["paths"] else "refresh"
    limit = int(options["limit"]) if options.get("limit") else LIMITS[mode]
    run_id = uuid.uuid4().hex[:12]
    branch = gitrepo.branch_name(repo["root"], options.get("branch") or f"relay/kb-{run_id}")
    if branch == default or gitrepo.local_tip(repo["root"], branch) or gitrepo.remote_tip(repo, branch):
        fail("Branch already exists; choose another --branch.", "branch")
    root = Path(repo["root"])
    path = root.parent / f"{root.name}-relay-kb-{run_id}"
    if path.exists():
        fail("Worktree path already exists: " + str(path), "worktree")
    kb = layout.load(layout.TreeReader(repo["root"], base_sha))
    if mode == "refresh" and not kb["present"]:
        fail("No KB exists on the default branch; nothing to refresh.", "kb")
    run = {"schema": 1, "run_id": run_id, "mode": mode, "base_sha": base_sha, "default": default, "branch": branch, "worktree": str(path),
           "paths": parsed["paths"], "repository": {"host": repo.get("host", "github.com"), "repo": repo["repo"]}, "watch": bool(options.get("watch")),
           "targets": [], "checked": {}, "budget": {"limit": limit, "used": 0, "calls": 1}, "current_batch": None, "batches": {},
           "pr": None, "status": "planning", "stages": {}, "listing": [], "pushed_sha": None}
    if mode == "extract":
        run["listing"] = listing.build(repo["root"], base_sha, parsed["paths"], gh)
        run["targets"] = [{"key": f["path"], "directory": f["directory"], "done": False, "batch_id": None} for f in run["listing"]]
    else:
        verdicts = reconfirmation.assess(kb, sorted(k for k, e in kb["entries"].items() if e["status"] == "active"), repo["root"], base_sha)
        for identity, verdict in sorted(verdicts.items()):
            if verdict["verdict"] == "auto":
                run["checked"][identity] = reconfirmation.record(kb["entries"][identity], kb["state"]["confirmed"].get(identity), base_sha)
            else:
                run["targets"].append({"key": identity, "verdict": verdict["verdict"], "reason": verdict["reason"], "changed": verdict["changed"],
                                       "done": False, "batch_id": None})
        run["listing"] = [dict(t, **lookup.summary_of(kb["entries"][t["key"]], [], kb)) for t in run["targets"]]
    store.save(run)  # the plan is recorded before the worktree exists
    try:
        gitrepo.git(repo["root"], "worktree", "add", "-q", "-b", branch, str(path), base_sha)
    except RelayError:
        run["status"] = "failed"
        store.save(run)
        raise
    write_json(path / ".relay" / "pointer.json", {"root": str(store.root)})
    run["status"] = "active"
    store.save(run)
    return {"status": "active", "run_id": run_id, "mode": mode, "base_sha": base_sha, "branch": branch, "worktree": str(path),
            "budget": run["budget"], "targets": len(run["targets"]), "auto": len(run["checked"]),
            "kb": lookup.counts(kb), "listing": listing_page(run)}


def load_active(store, data):
    run = store.load(data["run_id"])
    if run["status"] == "finished":
        fail("This run is finished; start a new run for more work.", "conflict")
    return run


def batch(data, repo, gh, store):
    run = load_active(store, data)
    if data.get("list"):
        return listing_page(run, data.get("cursor"))
    if data.get("cursor") and data.get("batch_id"):
        current = run["batches"].get(data["batch_id"])
        if not current or current["status"] == "done":
            fail("Cursor continues an open batch only.", "conflict")
        return fragments_page(run, current, repo, data["cursor"])
    if run["status"] == "paused":
        fail("Run is paused; resume it with a new budget before another batch.", "conflict")
    if run["current_batch"] and run["batches"][run["current_batch"]]["status"] != "done":
        fail("Finish the current batch with checkpoint before selecting another.", "conflict")
    keys = data.get("files") if run["mode"] == "extract" else data.get("ids")
    if not isinstance(keys, list) or not keys or len(keys) > BATCH:
        fail(f"Select 1-{BATCH} {'files' if run['mode'] == 'extract' else 'ids'}.")
    pending = {t["key"]: t for t in remaining(run)}
    unknown = [k for k in keys if k not in pending]
    if unknown:
        fail("Not remaining targets: " + ", ".join(unknown))
    if run["mode"] == "extract" and len({pending[k]["directory"] for k in keys}) != 1:
        fail("An extraction batch takes direct files of one directory.")
    if len(keys) > budget_left(run):
        fail(f"Only {budget_left(run)} of this call's budget remains; select fewer targets or resume with a new budget.", "budget")
    kb = layout.load(layout.WorktreeReader(run["worktree"]))
    if run["mode"] == "extract":
        scope = sorted({e["id"] for k in keys for e, _ in gates.candidates_for(kb, {"paths": [k], "terms": []})})
    else:
        scope = sorted(keys)
    batch_id = uuid.uuid4().hex[:12]
    head = gitrepo.git(run["worktree"], "rev-parse", "HEAD")
    current = {"batch_id": batch_id, "keys": sorted(keys), "scope": scope, "start_head": head, "status": "open", "symbols": data.get("symbols") or {}}
    run["batches"][batch_id] = current
    run["current_batch"] = batch_id
    for key in keys:
        pending[key]["batch_id"] = batch_id
    store.save(run)
    return {"status": "open", "run_id": run["run_id"], "batch_id": batch_id, "keys": current["keys"], "scope": scope, "start_head": head,
            "fragments": fragments_page(run, current, repo)}


def fragments_page(run, current, repo, cursor=None):
    kb = layout.load(layout.WorktreeReader(run["worktree"]))
    if run["mode"] == "extract":
        files = []
        for key in current["keys"]:
            symbols = current.get("symbols", {}).get(key) or [f for f in run["listing"] if f["path"] == key][0]["symbols"] or []
            files.append({"path": key, "symbols": symbols or None})
        return combined_fragments(kb, {"files": files}, repo, run["base_sha"], cursor)
    return fragments.fragment(kb, {"ids": current["keys"], "cursor": cursor}, repo["root"], run["base_sha"])


def combined_fragments(kb, units, repo, sha, cursor):
    """One page over every batch file's symbol units, paged like fragment.fragment."""
    objects = fragments.Objects(repo["root"])
    all_units = []
    for file in units["files"]:
        all_units.extend(fragments.units_for(objects, sha, file["path"], file["symbols"], None))
    binding = {"query": lookup.query_hash(units), "sha": sha, "kb": layout.digest_of(kb)}
    position = (0, 0)
    if cursor:
        offset = lookup.decode_cursor(cursor, binding)
        position = (offset // 100000, offset % 100000)
    result = {"sha": sha, "items": [], "missing_ids": [], "truncated": False, "next_cursor": lookup.encode_cursor({**binding, "offset": len(all_units) * 100000}), "warnings": []}
    index, line = position
    while index < len(all_units):
        unit = dict(all_units[index])
        lines = unit.pop("lines", None)
        if lines is None:
            result["items"].append(unit)
            if lookup.size(result) > lookup.PAGE_BUDGET and len(result["items"]) > 1:
                result["items"].pop()
                break
            index += 1
            continue
        rest, start = lines[line:], unit["line_start"] + line
        item = {**unit, "line_start": start, "line_end": unit["line_end"], "continued": False, "text": "\n".join(rest)}
        result["items"].append(item)
        if lookup.size(result) <= lookup.PAGE_BUDGET:
            index, line = index + 1, 0
            continue
        result["items"].pop()
        low, high, best = 1, len(rest), 0
        while low <= high:
            middle = (low + high) // 2
            item = {**unit, "line_start": start, "line_end": start + middle - 1, "continued": True, "text": "\n".join(rest[:middle])}
            result["items"].append(item)
            ok = lookup.size(result) <= lookup.PAGE_BUDGET
            result["items"].pop()
            if ok:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        if best == 0:
            if not result["items"]:
                fail("A single line exceeds the page budget.", "length")
            break
        result["items"].append({**unit, "line_start": start, "line_end": start + best - 1, "continued": True, "text": "\n".join(rest[:best])})
        line += best
        break
    if index < len(all_units):
        result["truncated"] = True
        result["next_cursor"] = lookup.encode_cursor({**binding, "offset": index * 100000 + line})
    else:
        result["next_cursor"] = None
    return result


def ledger_entries(check, changes, run, batch_id):
    adopted, needs = [], []
    for op in changes["ops"]:
        if op["op"] in ("create", "update", "supersede"):
            identity = check["plan"]["issued"].get(op["op_id"]) or op.get("id")
            entry = check["post"]["entries"][identity]
            adopted.append({"id": identity, "type": entry["type"], "rule": entry["rule"], "not_in_code": entry["not_in_code"], "incentive": entry["incentive"], "op": op["op"]})
        elif op["op"] == "absorb":
            adopted.append({"id": op["id"], "type": check["post"]["entries"][op["id"]]["type"], "rule": "흡수 → " + op["pointer"], "not_in_code": "-", "incentive": "-", "op": "absorb"})
        elif op["op"] == "delete":
            adopted.append({"id": op["id"], "type": "-", "rule": "삭제", "not_in_code": "-", "incentive": "-", "op": "delete"})
        if op.get("evidence") and op["op"] != "reconfirm":
            needs.append(f"{op.get('id') or op.get('old')}: {op['op']} 근거 — {op['evidence']}")
        if op["op"] == "reconfirm":
            for identity, text in (op.get("evidence") or {}).items():
                needs.append(f"{identity}: 재확인 근거 — {text}")
    reconfirmed = []
    for identity, verdict in sorted(check["verdicts"].items()):
        handled = next((op["op"] for op in changes["ops"] if op.get("id") == identity or op.get("old") == identity or identity in (op.get("ids") or [])), None)
        reconfirmed.append({"id": identity, "verdict": verdict["verdict"], "reason": verdict["reason"], "handled": handled})
    return adopted, reconfirmed, needs


def checkpoint(data, repo, gh, store):
    run = load_active(store, data)
    batch_id = data.get("batch_id")
    current = run["batches"].get(batch_id)
    if not current or run["current_batch"] != batch_id:
        fail("checkpoint names the current open batch.", "conflict")
    if current["status"] == "done":
        return result_for(run, current)
    folder = store.run_path(run["run_id"]) / "batches" / batch_id
    folder.mkdir(parents=True, exist_ok=True)
    if current["status"] == "open":
        changes = read_json(Path(data["changes_file"]))
        kb = layout.load(layout.WorktreeReader(run["worktree"]))
        check = gates.check(kb, changes, sha=run["base_sha"], root=repo["root"], gh=gh, scope_ids=current["scope"], checked=run["checked"],
                            execution_id=run["run_id"], batch_id=batch_id, label=data.get("label"), issued=current.get("issued"),
                            reader=layout.WorktreeReader(run["worktree"]))
        if check["status"] == "candidates":
            return {"status": "candidates", "run_id": run["run_id"], "batch_id": batch_id, "pending": check["pending"]}
        if check["status"] == "capacity_resolution_required":
            return {**check, "run_id": run["run_id"], "batch_id": batch_id}
        changes = changeset.validate(changes)
        adopted, reconfirmed, needs = ledger_entries(check, changes, run, batch_id)
        current.update(issued=check["plan"]["issued"], plan_digest=check["plan"]["digest"], post_tree=check["plan"]["post_tree"],
                       no_change=all(v["pre"] == v["post"] for v in check["plan"]["files"].values()), adopted=adopted, reconfirmed=reconfirmed,
                       rejected=changes["rejected"], needs=needs, checked=check["checked"], status="planned", label=data.get("label"),
                       warnings=check["warnings"])
        write_json(folder / "plan.json", check["plan"])
        write_json(folder / "files.json", check["files"])
        write_json(folder / "changes.json", changes)
        store.save(run)
    plan_value, files = read_json(folder / "plan.json"), read_json(folder / "files.json")
    stages = remote.Stages(current, lambda: store.save(run))
    if not current["no_change"]:
        applied = changeset.apply(plan_value, files, run["worktree"], folder / "apply.json")
        paths = applied["paths"]
        commit = remote.commit(stages, "commit", run["worktree"], parent=current["start_head"], paths=paths, post_tree=plan_value["post_tree"],
                               message=f"kb-sync: {run['mode']} batch {batch_id} ({run['run_id']})")
        current["commit"] = commit["sha"]
        store.save(run)
        url = gitrepo.git(repo["root"], "remote", "get-url", "--push", repo["remote"])
        pushed = remote.push(stages, "push", run["worktree"], url=url, ref=run["branch"], expected_remote=run["pushed_sha"], local_commit=commit["sha"])
        run["pushed_sha"] = pushed["remote_sha"]
        store.save(run)
    run["checked"].update(current.get("checked", {}))
    current["status"] = "recorded" if current["no_change"] and not run.get("pr") else current["status"]
    if not current["no_change"] or run.get("pr"):
        publish_pr(run, current, repo, gh, store, stages)
    for target in run["targets"]:
        if target["batch_id"] == batch_id:
            target["done"] = True
    run["budget"]["used"] += len(current["keys"])
    current["status"] = "done"
    run["current_batch"] = None
    if remaining(run) and budget_left(run) <= 0:
        run["status"] = "paused"
    store.save(run)
    write_json(store.run_path(run["run_id"]) / "ledger.json", {"targets": run["targets"], "batches": run["batches"], "checked": run["checked"]})
    return result_for(run, current)


def publish_pr(run, current, repo, gh, store, stages):
    """Create the draft PR after the first real commit, or update its body for later batches."""
    if run.get("pr") is None:
        body = body_for(run)
        marker = MARKER + run["run_id"] + " -->"
        created = remote.create_pull(stages, "pr", gh, repo=repo["repo"], head=run["branch"], base=run["default"],
                                     title=f"kb-sync: {run['mode']} {run['run_id']}", body=body, draft=True, marker=marker)
        run["pr"] = {"number": created["number"], "url": created["url"], "body_digest": digest(body)}
        store.save(run)
        if run["watch"]:
            run["watch_result"] = watch.mark(gh, created["number"], True, run.get("watch_result"))
            store.save(run)
        return
    body = body_for(run)
    remote.update_pull(stages, "pr_update", gh, number=run["pr"]["number"], previous_digest=run["pr"]["body_digest"],
                       title=f"kb-sync: {run['mode']} {run['run_id']}", body=body)
    run["pr"]["body_digest"] = digest(body)
    store.save(run)


def result_for(run, current=None):
    value = {"status": run["status"], "run_id": run["run_id"], "mode": run["mode"], "branch": run["branch"], "worktree": run["worktree"],
             "pr": run.get("pr"), "budget": run["budget"], "remaining": len(remaining(run)), "done": len([t for t in run["targets"] if t["done"]]),
             "pushed_sha": run.get("pushed_sha"), "next_step": next_step_for(run)}
    if current:
        value["batch"] = {k: current.get(k) for k in ("batch_id", "keys", "status", "commit", "no_change", "adopted", "reconfirmed", "rejected", "needs", "warnings")}
    if run.get("watch"):
        value["watch"] = run.get("watch_result") if run.get("pr") else {"requested": True, "applied": False, "error": None, "warning": "no PR to mark"}
    return value


def resume(data, repo, gh, store):
    run = store.load(data["run_id"])
    if run["status"] == "finished":
        return result_for(run)
    path = Path(run["worktree"])
    if not path.is_dir() or gitrepo.git(path, "branch", "--show-current") != run["branch"]:
        fail("Recorded worktree or branch is missing; recovery never recreates it.", "worktree")
    if gitrepo.common_dir(path) != gitrepo.common_dir(repo["root"]):
        fail("Worktree belongs to another repository.", "worktree")
    url = gitrepo.git(repo["root"], "remote", "get-url", "--push", repo["remote"])
    tip = remote.remote_tip(repo["root"], url, run["branch"])
    if run.get("pushed_sha") and tip != run["pushed_sha"] and not (tip and gitrepo.ancestor(path, run["pushed_sha"], tip)):
        fail("Remote branch no longer holds the pushed commits; inspect before continuing.", "conflict")
    if run.get("pr"):
        pull = gh.pull(run["pr"]["number"])
        if pull.get("head", {}).get("ref") != run["branch"] or pull.get("state") != "open":
            fail("Draft PR identity changed; inspect before continuing.", "conflict")
    current = run["batches"].get(run["current_batch"]) if run["current_batch"] else None
    if current and current["status"] not in ("open", "done"):
        checkpoint({"run_id": run["run_id"], "batch_id": current["batch_id"]}, repo, gh, store)
        run = store.load(run["run_id"])
    limit = int(data["limit"]) if data.get("limit") else LIMITS[run["mode"]]
    run["budget"] = {"limit": limit, "used": 0, "calls": run["budget"]["calls"] + 1}
    if data.get("watch"):
        run["watch"] = True
    run["status"] = "active" if remaining(run) or run["current_batch"] else "active"
    store.save(run)
    return {**result_for(run), "listing": listing_page(run)}


def finish(data, repo, gh, store):
    run = store.load(data["run_id"])
    if run["status"] == "finished":
        return result_for(run)
    if run["current_batch"] and run["batches"][run["current_batch"]]["status"] != "done":
        fail("An open batch remains; checkpoint it first.", "conflict")
    if remaining(run):
        fail(f"{len(remaining(run))} targets remain; batch and checkpoint them or resume with a new budget.", "conflict")
    for batch_value in run["batches"].values():
        for item in batch_value.get("reconfirmed", []):
            if item["verdict"] in ("judge", "broken") and not item.get("handled"):
                fail(f"{item['id']} was {item['verdict']} without a handling operation.", "conflict")
    run["status"] = "finished"
    if run.get("pr"):
        stages = remote.Stages(run, lambda: store.save(run))
        body = body_for(run)
        remote.update_pull(stages, "final_body", gh, number=run["pr"]["number"], previous_digest=run["pr"]["body_digest"],
                           title=f"kb-sync: {run['mode']} {run['run_id']}", body=body)
        run["pr"]["body_digest"] = digest(body)
        store.save(run)
        remote.mark_ready(stages, "ready", gh, number=run["pr"]["number"])
        if run["watch"]:
            run["watch_result"] = watch.mark(gh, run["pr"]["number"], True, run.get("watch_result"))
    store.save(run)
    return result_for(run)


def dispatch(data, registry, repo, gh):
    action = data.get("action")
    store = KbSyncStore(repo["root"])
    if action == "begin":
        parsed = invocation.parse("kb-sync", data.get("raw", ""), registry)
        if parsed["options"].get("resume"):
            with store.lock(parsed["options"]["resume"]):
                return resume({"run_id": parsed["options"]["resume"], "limit": parsed["options"].get("limit"), "watch": parsed["options"].get("watch")}, repo, gh, store)
        return begin(data, repo, gh, store, registry)
    run_id = data.get("run_id")
    if not run_id:
        fail("run_id is required.")
    with store.lock(run_id):
        try:
            if action == "batch":
                return batch(data, repo, gh, store)
            if action == "checkpoint":
                return checkpoint(data, repo, gh, store)
            if action == "resume":
                return resume(data, repo, gh, store)
            return finish(data, repo, gh, store)
        except RelayError as exc:
            try:
                run = store.load(run_id)
            except RelayError:
                raise exc
            if run["status"] != "finished" and exc.code in ("uncertain", "conflict"):
                run["error"] = {"code": exc.code, "message": str(exc)[:2000]}
                store.save(run)
            raise
