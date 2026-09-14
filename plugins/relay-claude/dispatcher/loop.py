"""One polling cycle: watch set → artifacts → ledger first → judgment → launch or wait → cursor."""
import datetime
import time
import uuid

from relay_core import RelayError, next_step as steps
from relay_core.kb import handoff as handoffs
from relay_core.watch import LABEL
from . import config as configuration, detect, policy
from .launcher import resume_argv, shell_text
from .ledger import artifact_key, iso, item_key, model_fields, parse

OVERLAP = datetime.timedelta(minutes=5)


class Context:
    def __init__(self, *, config, ledger, launcher, log, clock, registry, login, remote_for):
        self.config = config
        self.ledger = ledger
        self.launcher = launcher
        self.log = log
        self.clock = clock
        self.registry = registry
        self.login = login
        self.remote_for = remote_for


def label_of(artifact):
    return artifact["kind"] + (f" v{artifact['version']}" if artifact.get("version") else "")


def go_command(slug, number):
    return f"relay-dispatch go {slug}#{number}"


def launch(ctx, item, entry, verified=False):
    """Start the session recorded in entry; a failed launch leaves a pending entry, never a session.

    A kb-sync resume that did not come straight from this cycle's judgment is checked
    against the PR first, so go, unpausing and launcher retries never run a replaced round.
    """
    if not check_pending_transition(ctx, item, entry):
        return False
    if entry.get("handoff") and not verified and not handoff_current(ctx, item, entry):
        return False
    ledger, now = ctx.ledger, ctx.clock()
    session_id = str(uuid.uuid4())
    options = ctx.launcher.claude_support() if entry["host"] == "claude" else ()
    argv = ctx.launcher.session_argv(entry, session_id)
    if entry["host"] == "claude" and len(options) < 2:
        ctx.log.line(f"{item}: 이 claude는 {set(('--session-id', '--name')) - set(options)} 옵션을 지원하지 않아 뺀다")
    ledger.open_session(item, {"slug": entry["slug"], "number": entry["number"], "stage": entry["stage"], "host": entry["host"],
                               "session_id": session_id, "cwd": entry["cwd"], "name": entry["name"], "argv": argv,
                               "started_at": iso(now), **model_fields(entry),
                               **({"handoff": dict(entry["handoff"])} if entry.get("handoff") else {})})
    ledger.save()
    outcome = ctx.launcher.launch(entry["cwd"], argv, entry["name"], session_id)
    if outcome["ok"]:
        ledger.pop_pending(item)
        ctx.log.event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "자동 시작",
                      entry.get("reason", "자동 시작"), outcome["command"])
        return True
    ledger.data["sessions"].pop(item, None)
    ledger.set_pending(item, dict(entry, gate="런처 실패", error=outcome["error"], created_at=iso(now)))
    ctx.log.event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "런처 실패",
                  outcome["error"], outcome["command"] + " | " + go_command(entry["slug"], entry["number"]))
    return False


def handoff_current(ctx, item, entry):
    """Whether a stored kb-sync resume is still its run's latest handoff on the open PR carrying that run."""
    meta, slug, number = entry["handoff"], entry["slug"], entry["number"]
    seen = ctx.ledger.handoff(slug, meta["run_id"])
    reason = None
    if not seen or handoffs.order(seen) != handoffs.order(meta):
        reason = "새 인계 회차로 대체됨"
    else:
        try:
            gh = ctx.remote_for(configuration.identity(entry["cwd"]))
            pull = gh.pull(number)
            comments = gh.comments(number)
        except RelayError as exc:
            gate = "인계 재확인 실패: " + exc.code
            if entry.get("gate") != gate:
                ctx.ledger.set_pending(item, dict(entry, gate=gate))
                ctx.log.event(slug, number, entry.get("title"), entry.get("artifact", "-"), "보류", f"{gate} ({exc})")
            return False
        found = [handoffs.read(c.get("body") or "") for c in comments]
        found = [f[0] for f in found if f and f[0]["run_id"] == meta["run_id"]]
        latest = max(found, key=handoffs.order, default=None)
        if pull.get("state") != "open" or pull.get("merged") or pull.get("merged_at"):
            reason = "PR이 닫혔거나 머지됨"
        elif handoffs.body_run(pull.get("body")) != (meta["run_id"], handoffs.PROTOCOL):
            reason = "PR 본문의 kb-sync run 마커와 인계 run이 다름"
        elif latest != meta:
            reason = "PR의 최신 인계가 아님"
    if reason is None:
        return True
    if (ctx.ledger.pending(item) or {}).get("handoff") == meta:
        ctx.ledger.pop_pending(item)
    ctx.log.event(slug, number, entry.get("title"), entry.get("artifact", "-"), "자동 진행 종료", reason)
    return False


def resume(ctx, item, session):
    argv = resume_argv(session["host"], session["session_id"], model=model_fields(session)["model"])
    return ctx.launcher.launch(session["cwd"], argv, session["name"], session["session_id"])


def check_pending_transition(ctx, item, entry):
    """Check stored provenance without reconstructing prompts or model selection."""
    source = entry.get("source_stage")
    try:
        source = steps.stage_name(source)
    except RelayError:
        reason = "전이 출처 불명: 원본 단계를 확인하고 사람이 재판정해야 함"
        if entry.get("gate") != reason:
            ctx.ledger.set_pending(item, dict(entry, gate=reason))
            ctx.log.event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "보류", reason)
        return False
    if steps.allowed(source, entry.get("stage")) and entry.get("stage") is not None:
        return True
    reason = (steps.blocked_reason(source, entry["stage"]) if entry.get("stage") is not None
              else "명시적 next 없음: 자동 진행 종료")
    ctx.ledger.pop_pending(item)
    ctx.log.event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "자동 진행 종료", reason)
    return False


def reconcile_pending(ctx):
    # Even processed artifacts and advanced cursors cannot grandfather old commands.
    for item, entry in list(ctx.ledger.data["pending"].items()):
        check_pending_transition(ctx, item, entry)


def judge(ctx, gh, found, item, raw, artifact, settings, head_key=None, repo_entry=None):
    """Apply D5 to one artifact of one item."""
    ledger, now, slug, number = ctx.ledger, ctx.clock(), found["slug"], raw["number"]
    title, label = raw.get("title"), label_of(artifact)
    session = ledger.session(item)
    if session and session["stage"] == artifact["stage"]:
        ledger.close_session(item, now, f"{artifact['kind']} 산출물 게시")
        ctx.log.event(slug, number, title, label, "산출물 게시", f"{session['stage']} 추적 세션 닫힘; 작업 성공 판정 아님")
        session = None
    if artifact["status"] != steps.RECORDED:
        ctx.log.event(slug, number, title, label, "무시", "relay:next 읽을 수 없음")
        return
    next_stage = artifact["next_step"]["next"]
    action, reason = policy.decide(next_stage, artifact["stage"], session, settings, ctx.config["paused"])
    if action in (policy.DONE, policy.BLOCKED):
        ledger.pop_pending(item)
        ledger.close_session(item, now, reason)
        ctx.log.event(slug, number, title, label, "자동 진행 종료", reason)
        return
    kind = "pr" if "pull_request" in raw else "issue"
    resumes = kind == "pr" and policy.resumes_run(artifact, next_stage)
    if not resumes and next_stage not in (None, "open") and not policy.accepts(ctx.registry, next_stage, kind):
        # A PR item has no issue number to hand to an issue skill, and an issue has no PR to review.
        ctx.log.event(slug, number, title, label, "무시", f"{next_stage}은(는) {'PR' if kind == 'pr' else '이슈'} 번호를 받지 않음")
        return
    if action == policy.IGNORE:
        ctx.log.event(slug, number, title, label, "무시", reason)
        return
    selected = configuration.session_settings(ctx.config, repo_entry or {}, next_stage)
    try:
        if resumes:
            prompt = policy.handoff_prompt(ctx.registry, selected["host"], artifact["handoff"])
        else:
            prompt = policy.prompt(ctx.registry, selected["host"], next_stage, number, settings, found["root"])
    except RelayError as exc:
        ctx.log.event(slug, number, title, label, "무시", f"프롬프트 조립 실패: {exc}")
        return
    selection = selected["selection"]
    if selection["model_state"] == "host-mismatch":
        ctx.log.line(f"{slug}#{number} {next_stage}: 모델 {selection['requested_model']} 무시 "
                     f"(출처 {selection['model_scope']}, host {selection['model_host']} != 최종 host {selected['host']})")
    entry = {"slug": slug, "number": number, "stage": next_stage, **selected, "cwd": found["root"],
             "source_stage": artifact["stage"], "source_target": artifact["target"], "source_digest": artifact["digest"],
             "prompt": prompt, "name": policy.session_name(slug, number, next_stage), "head_key": head_key,
             "artifact": label, "reason": reason, "gate": reason, "created_at": iso(now), "title": title}
    if resumes:
        entry["handoff"] = dict(artifact["handoff"])
    if action == policy.GATE:
        ledger.set_pending(item, entry)
        ctx.log.event(slug, number, title, label, "게이트 대기", reason,
                      shell_text(ctx.launcher.session_argv(entry)) + " | " + go_command(slug, number))
        return
    launch(ctx, item, entry, verified=True)


def fresh_artifacts(ctx, slug, candidates):
    return [(artifact_key(slug, a["target"], a["digest"]), a) for a in candidates
            if not ctx.ledger.processed(artifact_key(slug, a["target"], a["digest"]))]


def process_issue(ctx, gh, found, raw, settings, since, repo_entry=None):
    ledger, now, slug, number = ctx.ledger, ctx.clock(), found["slug"], raw["number"]
    item = item_key(slug, number)
    candidates = []
    body = detect.from_issue_body(raw)
    if body and (raw.get("updated_at") or "") >= since:
        candidates.append(body)
    for comment in gh.comments_since(number, since):
        artifact = detect.from_comment(comment)
        if artifact:
            candidates.append(artifact)
    fresh = fresh_artifacts(ctx, slug, candidates)
    settling = ledger.settling(item)
    if not fresh and not settling:
        return
    for key, artifact in [(k, a) for k, a in fresh if a["kind"] == "investigation"]:
        ledger.mark_processed(key, now, "판정")
        judge(ctx, gh, found, item, raw, artifact, settings, key, repo_entry)
    fresh = [(k, a) for k, a in fresh if a["kind"] != "investigation"]
    if not fresh and not settling:
        return
    try:
        result = detect.chain(raw, gh.comments(number))
    except RelayError as exc:
        for key, _ in fresh:
            ledger.mark_processed(key, now, "체인 오류")
        ctx.log.event(slug, number, raw.get("title"), "-", "무시", f"문서 체인 오류: {exc}")
        return
    head = result["head"]
    head_key = artifact_key(slug, head["target"], head["digest"]) if head else None
    pending = ledger.pending(item)
    if pending and pending.get("head_key") != head_key:
        ledger.pop_pending(item)
        ctx.log.event(slug, number, raw.get("title"), pending.get("artifact", "-"), "대체됨", "체인 머리가 바뀜")
    fresh_keys = {k for k, _ in fresh}
    for key, artifact in fresh:
        if key != head_key:
            if head is None:
                reason = "머리 없음"
            elif head_key in fresh_keys:
                reason = "하류 current 문서에 의해 대체"
            else:
                reason = "머리가 이번 주기 산출물이 아님"  # e.g. watch added after the chain was published
            ledger.mark_processed(key, now, reason)
    if head is None:
        return
    if head_key not in fresh_keys and not settling:
        return
    if result["downstream_stale"]:
        if fresh:
            ledger.mark_processed(head_key, now, "정착 대기")
            ledger.set_settling(item, now)
            ctx.log.event(slug, number, raw.get("title"), label_of(head), "정착 대기(하류 stale)", "상위 개정 진행 중일 수 있음")
            return
        if now - parse(settling) < datetime.timedelta(seconds=2 * ctx.config["poll_seconds"]):
            return
    ledger.clear_settling(item)
    if head_key in fresh_keys:
        ledger.mark_processed(head_key, now, "판정")
    judge(ctx, gh, found, item, raw, head, settings, head_key, repo_entry)


def process_pull(ctx, gh, found, raw, settings, since, repo_entry=None):
    ledger, now, slug, number = ctx.ledger, ctx.clock(), found["slug"], raw["number"]
    item = item_key(slug, number)
    candidates, pull = [], None
    if (raw.get("updated_at") or "") >= since:
        pull = gh.pull(number)
        artifact = detect.from_pull_body(pull)
        if artifact:
            candidates.append(artifact)
    for comment in gh.comments_since(number, since):
        artifact = detect.from_comment(comment, "pr_comment")
        if artifact:
            candidates.append(artifact)
    for comment in gh.review_comments_since(number, since):
        artifact = detect.from_review(comment, "inline")
        if artifact:
            candidates.append(artifact)
    for review in gh.reviews(number):
        if (review.get("submitted_at") or "") >= since:
            artifact = detect.from_review(review, "review")
            if artifact:
                candidates.append(artifact)
    fresh = drop_stale_handoffs(ctx, found, raw, fresh_artifacts(ctx, slug, candidates))
    if not fresh:
        return
    latest = detect.newest([a for _, a in fresh])
    for key, artifact in fresh:
        ledger.mark_processed(key, now, "판정" if artifact is latest else "같은 주기의 더 최근 산출물에 의해 대체")
    if latest.get("handoff"):
        pull = pull or gh.pull(number)
        if handoffs.body_run(pull.get("body")) != (latest["handoff"]["run_id"], handoffs.PROTOCOL):
            ctx.log.event(slug, number, raw.get("title"), label_of(latest), "무시", "PR 본문의 kb-sync run 마커와 인계 run이 다름")
            return
    if pull and (pull.get("merged") or pull.get("merged_at")):
        ledger.pop_pending(item)
        ledger.close_session(item, now, "머지됨")
        ctx.log.event(slug, number, raw.get("title"), label_of(latest), "종료", "PR 머지됨")
        return
    if latest.get("handoff"):
        ledger.record_handoff(slug, latest["handoff"]["run_id"], latest["handoff"], latest["target"], now)
    judge(ctx, gh, found, item, raw, latest, settings, artifact_key(slug, latest["target"], latest["digest"]), repo_entry)


def drop_stale_handoffs(ctx, found, raw, fresh):
    """Keep one handoff per new (run, round, state); repeats, edits and older rounds never reach judgment.

    They are recorded as processed without touching sessions or pending commands, so a
    re-posted or edited comment, a restart or a re-poll cannot hand a round out twice.
    """
    ledger, now, slug = ctx.ledger, ctx.clock(), found["slug"]
    kept, taken = [], set()
    for key, artifact in sorted(fresh, key=lambda ka: handoffs.order(ka[1]["handoff"]) if ka[1].get("handoff") else (0, 0), reverse=True):
        meta = artifact.get("handoff")
        if not meta:
            kept.append((key, artifact))
            continue
        seen = ledger.handoff(slug, meta["run_id"])
        if meta["run_id"] in taken or (seen and handoffs.order(meta) <= handoffs.order(seen)):
            ledger.mark_processed(key, now, "중복·오래된 인계 회차")
            ctx.log.event(slug, raw["number"], raw.get("title"), label_of(artifact), "무시",
                          f"kb-sync {meta['run_id']} 회차 {meta['round']} {meta['state']}는 이미 판정된 인계보다 새롭지 않음")
            continue
        taken.add(meta["run_id"])
        kept.append((key, artifact))
    return kept


def reconcile_vanished(ctx, gh, found, present):
    """Sessions and gates for items that left the watch set end only when the item is closed."""
    ledger, now, slug = ctx.ledger, ctx.clock(), found["slug"]
    prefix = slug + "#"
    items = {k for k in list(ledger.open_sessions()) + list(ledger.data["pending"]) if k.startswith(prefix)}
    for item in items:
        number = int(item[len(prefix):])
        if number in present:
            continue
        try:
            current = gh.item(number)
        except RelayError:
            continue
        if current.get("state") == "open" and not current.get("merged"):
            continue
        ledger.pop_pending(item)
        ledger.close_session(item, now, "닫힘")
        ctx.log.event(slug, number, current.get("title"), "-", "종료", "이슈·PR이 닫힘")


def process_repo(ctx, gh, found, key, settings, start, repo_entry=None):
    ledger, slug = ctx.ledger, found["slug"]
    cursor = ledger.cursor(key)
    since = iso(parse(cursor) - OVERLAP if cursor else start)
    watched, present = [], set()
    previous = {w["number"]: w for w in ledger.data["watched"].get(key, [])}
    for raw in gh.watched(ctx.login, LABEL):
        number = raw["number"]
        assignees = [a.get("login") for a in raw.get("assignees", [])]
        kind = "pr" if "pull_request" in raw else "issue"
        if len(assignees) != 1:
            watched.append({"number": number, "kind": kind, "title": raw.get("title"), "status": "excluded"})
            if previous.get(number, {}).get("status") != "excluded":
                ctx.log.event(slug, number, raw.get("title"), kind, "감시 제외", f"assignee {len(assignees)}명")
            continue
        present.add(number)
        watched.append({"number": number, "kind": kind, "title": raw.get("title"), "status": "watched"})
        if kind == "pr":
            process_pull(ctx, gh, found, raw, settings, since, repo_entry)
        else:
            process_issue(ctx, gh, found, raw, settings, since, repo_entry)
    reconcile_vanished(ctx, gh, found, present)
    ledger.data["watched"][key] = watched


def release_paused(ctx):
    """Entries queued only because of pause start in their queued order once unpaused."""
    if ctx.config["paused"]:
        return
    queued = sorted(((k, v) for k, v in ctx.ledger.data["pending"].items() if v.get("gate") == "일시정지"),
                    key=lambda kv: kv[1].get("created_at", ""))
    for item, entry in queued:
        launch(ctx, item, dict(entry, reason="일시정지 해제"))


def cycle(ctx):
    """One cycle under the ledger lock; the caller persists the ledger afterwards."""
    start = ctx.clock()
    errors = []
    reconcile_pending(ctx)
    release_paused(ctx)
    for entry in ctx.config["repos"]:
        try:
            found = configuration.identity(entry["path"])
        except RelayError as exc:
            errors.append({"path": entry["path"], "error": str(exc)})
            if ctx.ledger.data.get("skipped", {}).get(entry["path"]) != str(exc):
                ctx.log.line(f"{entry['path']}: 저장소 건너뜀 ({exc})")
            ctx.ledger.data.setdefault("skipped", {})[entry["path"]] = str(exc)
            continue
        ctx.ledger.data.get("skipped", {}).pop(entry["path"], None)
        key = configuration.slug_key(found["host"], found["slug"])
        settings = configuration.settings(ctx.config, entry)
        try:
            gh = ctx.remote_for(found)
            process_repo(ctx, gh, found, key, settings, start, entry)
        except RelayError as exc:
            errors.append({"repo": found["slug"], "error": f"{exc.code}: {exc}"})
            ctx.log.line(f"{found['slug']}: 이번 주기 건너뜀 ({exc.code}: {exc})")
            continue
        ctx.ledger.set_cursor(key, start)
    sessions = ctx.ledger.open_sessions()
    watched = sum(1 for items in ctx.ledger.data["watched"].values() for w in items if w["status"] == "watched")
    ctx.log.elapsed(sessions)
    ctx.log.heartbeat(watched, len(sessions))
    ctx.ledger.data["last_cycle"] = {"at": iso(start), "errors": errors, "watched": watched, "sessions": len(sessions)}
    return {"at": iso(start), "errors": errors, "watched": watched, "sessions": len(sessions)}


def run(ctx, config, sleep=time.sleep, stop=lambda: False):
    """The resident loop: reread configuration, cycle under the lock, wait poll_seconds."""
    while not stop():
        if config.load():
            if config.error:
                ctx.log.line(f"설정 파일 오류, 직전 설정 유지: {config.error}")
            else:
                ctx.log.line(f"설정 적용: 저장소 {len(config.value['repos'])}개, launcher {config.value['launcher']}, "
                             f"host {config.value.get('host', 'claude')}, paused {config.value['paused']}")
        ctx.config = config.value
        try:
            with ctx.ledger.locked():
                cycle(ctx)
                ctx.ledger.save()
        except RelayError as exc:
            ctx.log.line(f"주기 실패 ({exc.code}): {exc}")
        sleep(config.value["poll_seconds"])
