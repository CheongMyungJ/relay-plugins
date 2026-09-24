"""Execution mode: extract from paths or refresh the whole KB in batches on a draft PR (spec R7).

The invocation authorizes KB changes, commits, pushes and PR creation. `begin` pins the
default branch tip, a task branch, a sibling worktree and the run's initial limit; `batch`
fixes one batch of at most 15 files or entries; `checkpoint` runs the gates, applies,
commits, pushes, records the batch and creates or updates the draft PR, and when the limit
is reached with targets left it ends the call with a handoff comment on the PR (after one
empty commit and a draft PR when the run has none); `resume` recovers unfinished stages and
consumes the latest handoff round once with the same limit; `finish` verifies every target
is handled and either marks a changed PR ready and posts a review handoff, or posts a null
handoff and closes a PR that ended without changes.
"""
import json
import uuid
from pathlib import Path
from .. import RelayError, invocation, repository as gitrepo, server_context, watch
from ..artifacts import digest
from ..state import read_json, write_json
from . import changes as changeset, fragment as fragments, gates, handoff, layout, listing, lookup, reconfirm as reconfirmation, remote
from .stores import KbSyncStore

BATCH = 15
LIMITS = {"extract": 60, "refresh": 80}
BODY_LIMIT = 65000


def fail(message, code="input"):
    raise RelayError(code, message)


def remaining(run):
    return [t for t in run["targets"] if not t["done"]]


def budget_left(run):
    return run["budget"]["limit"] - run["budget"]["used"]


def new_handoff(limit):
    return {"protocol": handoff.PROTOCOL, "limit": limit, "posted": 0, "consumed": 0, "comments": {}}


def protocol(run, store):
    """The run's handoff record. A run from before handoffs still on its first call restores its
    initial limit from that call's budget; a later call's budget is never taken for it."""
    if run.get("handoff") is None:
        if run["budget"]["calls"] != 1:
            fail("This run predates handoffs and its initial limit is unknown; resume it with --limit first.", "conflict")
        run["handoff"] = new_handoff(run["budget"]["limit"])
        store.save(run)
    return run["handoff"]


def next_step_for(run):
    if run["status"] == "finished":
        if run.get("pr") and run.get("changed", True):
            return {"next": "review", "reason": "KB 변경 PR 검토"}
        return {"next": None, "reason": "변경 없음"}
    if run["status"] == "paused":
        return {"next": "kb-sync", "reason": f"처리 상한 도달; 남은 대상 {len(remaining(run))}개를 --resume {run['run_id']}로 이어간다."}
    if run["status"] == "finishing":
        return {"next": None, "reason": "완료 처리 중; finish가 남은 원격 단계를 끝낸다."}
    return {"next": None, "reason": "실행 중; 배치 처리와 checkpoint가 진행되고 있다."}


def title_for(run):
    return f"kb-sync: {run['mode']} {run['run_id']}"


def body_for(run):
    """The draft PR body: constraints first, then adoption, reconfirmation, rejection and human checks.

    It names the run and the handoff protocol but carries no next step: handoff comments
    are the only signal, so editing the body never ends, resumes or reviews anything.
    """
    done = [t for t in run["targets"] if t["done"]]
    lines = [f"# KB sync: {run['mode']} ({run['run_id']})", "",
             f"- 기준 SHA: {run['base_sha']}", f"- 브랜치: {run['branch']}",
             f"- 예산: 호출당 {run['budget']['limit']}, {run['budget']['calls']}번째 호출에서 {run['budget']['used']} 사용",
             f"- 처리: {len(done)}개 완료, {len(remaining(run))}개 남음", "- 진행 인계: 이 PR의 kb-sync 인계 코멘트", "", "## 채택"]
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
        text = "\n".join(lines[:8] + ["", f"채택 {len(adopted)}개, 재확인 {len(reconfirmed)}개, 기각 {len(rejected)}개, 사람 확인 {len(needs)}개.",
                                      "전체 기록은 실행 디렉터리의 ledger에 있고 항목은 다음 커밋에서 확인한다:"] + [f"- {c}" for c in commits]) + "\n"
    return text + "\n" + handoff.body_marker(run["run_id"]) + "\n"


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
        return resume(resume_input(options), repo, gh, store)
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
           "pr": None, "status": "planning", "stages": {}, "listing": [], "pushed_sha": None, "handoff": new_handoff(limit),
           "init_commit": None}
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


def resume_input(options):
    return {"run_id": options["resume"], "limit": options.get("limit"), "watch": options.get("watch"), "handoff": options.get("handoff")}


def load_active(store, data):
    run = store.load(data["run_id"])
    if run["status"] == "finished":
        fail("This run is finished; start a new run for more work.", "conflict")
    if run["status"] == "finishing":
        fail("This run is finishing; call finish again to complete it.", "conflict")
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
    protocol(run, store)
    if run["status"] == "paused":
        fail("Run is paused; its handoff continues it in a new call with the same limit.", "conflict")
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
    protocol(run, store)
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
        # A retry after the push keeps the remote tip it planned against, not the tip it produced.
        planned = current["stages"].get("push")
        expected = planned["expected_remote"] if planned else run["pushed_sha"]
        pushed = remote.push(stages, "push", run["worktree"], url=url, ref=run["branch"], expected_remote=expected, local_commit=commit["sha"])
        run["pushed_sha"] = pushed["remote_sha"]
        store.save(run)
    if current["status"] == "planned":
        # Targets and budget are counted exactly once, before any PR write that a retry might repeat.
        run["checked"].update(current.get("checked", {}))
        for target in run["targets"]:
            if target["batch_id"] == batch_id:
                target["done"] = True
        run["budget"]["used"] += len(current["keys"])
        current["status"] = "recorded"
        store.save(run)
    if not current["no_change"] or run.get("pr"):
        publish_pr(run, current, repo, gh, store, stages)
    current["status"] = "done"
    run["current_batch"] = None
    if remaining(run) and budget_left(run) <= 0:
        run["status"] = "paused"
    store.save(run)
    write_json(store.run_path(run["run_id"]) / "ledger.json", {"targets": run["targets"], "batches": run["batches"], "checked": run["checked"]})
    if run["status"] == "paused":
        post_handoff(run, repo, gh, store)
    return result_for(run, current)


def publish_pr(run, current, repo, gh, store, stages):
    """Create the draft PR after the first real commit, or update its body for later batches."""
    if run.get("pr") is None:
        create_pr(run, repo, gh, store, stages, "pr")
        return
    sync_body(run, gh, store, stages, "pr_update")


def create_pr(run, repo, gh, store, stages, name):
    body = body_for(run)
    created = remote.create_pull(stages, name, gh, repo=repo["repo"], head=run["branch"], base=run["default"],
                                 title=title_for(run), body=body, draft=True, marker=handoff.body_marker(run["run_id"]))
    run["pr"] = {"number": created["number"], "url": created["url"], "body_digest": digest(body)}
    store.save(run)
    receipt = server_context.publication({
        "kind": "kb-sync-pr", "request_id": run["run_id"], "run_id": run["run_id"], "result": "created",
        "repo": repo["repo"], "head": run["branch"], "base": run["default"], "number": created["number"],
        "url": created["url"], "marker": handoff.body_marker(run["run_id"])})
    if receipt is not None:
        run["pr"]["server_receipt"] = receipt
        store.save(run)
    mark_watch(run, gh, store)


def mark_watch(run, gh, store):
    if run["watch"] and run.get("pr"):
        run["watch_result"] = watch.mark(gh, run["pr"]["number"], True, run.get("watch_result"))
        store.save(run)


def sync_body(run, gh, store, stages, name):
    """Bring the PR body to the run's current content; an unchanged body is not written."""
    body = body_for(run)
    if digest(body) == run["pr"]["body_digest"]:
        return
    remote.update_pull(stages, name, gh, number=run["pr"]["number"], previous_digest=run["pr"]["body_digest"], title=title_for(run), body=body)
    run["pr"]["body_digest"] = digest(body)
    store.save(run)


def open_initial_pr(run, repo, gh, store):
    """A paused run without a PR gets one empty commit on base_sha, pushed, and a draft PR to hand off on.

    Its stages live on the run, so a run creates at most one such commit whatever is retried.
    """
    stages = remote.Stages(run, lambda: store.save(run))
    made = remote.empty_commit(stages, "init_commit", run["worktree"], parent=run["base_sha"],
                               message=f"kb-sync: {run['mode']} start ({run['run_id']})")
    run["init_commit"] = made["sha"]
    store.save(run)
    url = gitrepo.git(repo["root"], "remote", "get-url", "--push", repo["remote"])
    pushed = remote.push(stages, "init_push", run["worktree"], url=url, ref=run["branch"], expected_remote=None, local_commit=made["sha"])
    run["pushed_sha"] = pushed["remote_sha"]
    store.save(run)
    create_pr(run, repo, gh, store, stages, "init_pr")


def handoff_text(run, state):
    record, done = run["handoff"], len([t for t in run["targets"] if t["done"]])
    commits = [b["commit"] for b in run["batches"].values() if b.get("commit")]
    lines = [f"kb-sync {run['mode']} {run['run_id']}: {run['budget']['calls']}번째 호출 "
             + ("처리 상한 도달, 인계" if state == "paused" else "완료"), "",
             f"- 호출당 처리 상한: {record['limit']} (이번 호출 {run['budget']['used']} 사용)",
             f"- 처리: {done}개 완료, {len(remaining(run))}개 남음",
             f"- 실제 KB 커밋: {len(commits)}개" + (f", 마지막 {commits[-1]}" if commits else ""),
             f"- 초기화 빈 커밋: {run.get('init_commit') or '없음'}"]
    if state == "finished":
        lines.append("- 결과: " + ("실제 변경 있음, PR ready 전환 확인" if run.get("changed") else "실제 변경 없음, PR을 머지하지 않고 닫음"))
    return "\n".join(lines) + "\n"


def post_record(run, gh, store, state):
    """Post this call's handoff comment once; `(run_id, round)` and the state name it."""
    record, round_ = run["handoff"], run["budget"]["calls"]
    key = str(round_) + ("" if state == "paused" else "-" + state)
    if key in record["comments"]:
        return record["comments"][key]
    stages = remote.Stages(run, lambda: store.save(run))
    meta = {"schema": handoff.SCHEMA, "run_id": run["run_id"], "limit": record["limit"], "round": round_, "state": state}
    body = handoff.render(handoff_text(run, state), next_step_for_state(run, state), meta)
    posted = remote.comment(stages, "handoff_" + key, gh, number=run["pr"]["number"], body=body, marker=handoff.anchor(meta), author=gh.viewer())
    record["comments"][key] = {"id": posted["id"], "url": posted["url"], "round": round_, "state": state}
    if state == "paused":
        record["posted"] = round_
    receipt = server_context.publication({
        "kind": "kb-sync-handoff", "request_id": run["run_id"] + ":" + key, "run_id": run["run_id"], "round": round_,
        "state": state, "limit": record["limit"], "pr": run["pr"]["number"], "target": str(posted["id"]),
        "url": posted["url"], "result": "created"})
    if receipt is not None:
        record["comments"][key]["server_receipt"] = receipt
    store.save(run)
    return record["comments"][key]


def next_step_for_state(run, state):
    return next_step_for(dict(run, status=state))


def post_handoff(run, repo, gh, store):
    """End a paused call: a draft PR exists (created once if needed), its body is current, the handoff is posted."""
    if run["status"] != "paused":
        fail("Only a paused run hands off.", "conflict")
    if run["handoff"]["posted"] >= run["budget"]["calls"]:
        return run["handoff"]["comments"][str(run["budget"]["calls"])]
    if run.get("pr") is None:
        open_initial_pr(run, repo, gh, store)
    sync_body(run, gh, store, remote.Stages(run, lambda: store.save(run)), f"body_{run['budget']['calls']}")
    mark_watch(run, gh, store)
    return post_record(run, gh, store, "paused")


def result_for(run, current=None):
    value = {"status": run["status"], "run_id": run["run_id"], "mode": run["mode"], "branch": run["branch"], "worktree": run["worktree"],
             "pr": run.get("pr"), "budget": run["budget"], "remaining": len(remaining(run)), "done": len([t for t in run["targets"] if t["done"]]),
             "pushed_sha": run.get("pushed_sha"), "init_commit": run.get("init_commit"), "handoff": run.get("handoff"), "next_step": next_step_for(run)}
    if current:
        value["batch"] = {k: current.get(k) for k in ("batch_id", "keys", "status", "commit", "no_change", "adopted", "reconfirmed", "rejected", "needs", "warnings")}
    if run.get("watch"):
        value["watch"] = run.get("watch_result") if run.get("pr") else {"requested": True, "applied": False, "error": None, "warning": "no PR to mark"}
    return value


def resume(data, repo, gh, store):
    """Continue a run in a new call with its initial limit, consuming the latest posted handoff once.

    A handoff resume names its round: an already consumed round returns `handoff_consumed`
    without a budget, and any other round than the latest posted one of a paused run is
    refused. A manual resume first completes a handoff the previous call did not finish
    posting and consumes it, so a later automatic call for that round does nothing.
    """
    run = store.load(data["run_id"])
    if run["status"] == "finished":
        return result_for(run)
    limit = int(data["limit"]) if data.get("limit") else None
    requested = int(data["handoff"]) if data.get("handoff") else None
    record = run.get("handoff")
    if record is None:
        if requested is not None:
            fail("This run predates handoffs; resume it manually with --limit.", "conflict")
        if limit is None and run["budget"]["calls"] != 1:
            fail("This run predates handoffs and its initial limit is unknown; resume it with --limit.", "input")
    elif limit is not None and limit != record["limit"]:
        fail(f"This run keeps its initial limit {record['limit']}; resume it without another --limit.", "input")
    if record is not None and requested is not None and requested <= record["consumed"]:
        return {**result_for(run), "status": "handoff_consumed", "next_step": {"next": None, "reason": f"인계 회차 {requested}는 이미 소비됨; 이 호출은 배치를 시작하지 않는다."}}
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
    if record is None:
        record = run["handoff"] = new_handoff(limit or run["budget"]["limit"])
        store.save(run)
    if requested is not None and (run["status"] != "paused" or requested != run["budget"]["calls"]):
        fail(f"Handoff round {requested} is not the latest round of this paused run.", "conflict")
    if data.get("watch"):
        run["watch"] = True
        store.save(run)
    if run["status"] == "finishing":
        return finish({"run_id": run["run_id"]}, repo, gh, store)
    current = run["batches"].get(run["current_batch"]) if run["current_batch"] else None
    if current and current["status"] not in ("open", "done"):
        checkpoint({"run_id": run["run_id"], "batch_id": current["batch_id"]}, repo, gh, store)
        run = store.load(run["run_id"])
    if run["status"] == "paused":
        post_handoff(run, repo, gh, store)
        run["handoff"]["consumed"] = run["handoff"]["posted"]
    run["budget"] = {"limit": run["handoff"]["limit"], "used": 0, "calls": run["budget"]["calls"] + 1}
    run["status"] = "active"
    store.save(run)
    return {**result_for(run), "listing": listing_page(run)}


def changed(run, repo):
    """Whether the pushed branch differs from base_sha in content; an empty commit alone is no change."""
    if not run.get("pushed_sha"):
        return False
    return gitrepo.git(repo["root"], "rev-parse", run["pushed_sha"] + "^{tree}") != gitrepo.git(repo["root"], "rev-parse", run["base_sha"] + "^{tree}")


def finish(data, repo, gh, store):
    """Validate, then complete every remote step before `finished` is saved; a retry resumes at `finishing`."""
    run = store.load(data["run_id"])
    if run["status"] == "finished":
        return result_for(run)
    if run["status"] != "finishing":
        if run["current_batch"] and run["batches"][run["current_batch"]]["status"] != "done":
            fail("An open batch remains; checkpoint it first.", "conflict")
        if remaining(run):
            fail(f"{len(remaining(run))} targets remain; batch and checkpoint them or resume with a new budget.", "conflict")
        for batch_value in run["batches"].values():
            for item in batch_value.get("reconfirmed", []):
                if item["verdict"] in ("judge", "broken") and not item.get("handled"):
                    fail(f"{item['id']} was {item['verdict']} without a handling operation.", "conflict")
        if run.get("pr"):
            protocol(run, store)
            run["changed"] = changed(run, repo)
        run["status"] = "finishing"
        store.save(run)
    if run.get("pr"):
        stages = remote.Stages(run, lambda: store.save(run))
        sync_body(run, gh, store, stages, "final_body")
        if run["changed"]:
            remote.mark_ready(stages, "ready", gh, number=run["pr"]["number"])
            mark_watch(run, gh, store)
            post_record(run, gh, store, "finished")
        else:
            post_record(run, gh, store, "finished")
            remote.close_pull(stages, "close", gh, number=run["pr"]["number"])
    run["status"] = "finished"
    store.save(run)
    return result_for(run)


def dispatch(data, registry, repo, gh):
    action = data.get("action")
    store = KbSyncStore(repo["root"])
    if action == "begin":
        parsed = invocation.parse("kb-sync", data.get("raw", ""), registry)
        if parsed["options"].get("resume"):
            with store.lock(parsed["options"]["resume"]):
                return resume(resume_input(parsed["options"]), repo, gh, store)
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
            if action == "handoff":
                run = store.load(run_id)
                protocol(run, store)
                post_handoff(run, repo, gh, store)
                return result_for(store.load(run_id))
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
