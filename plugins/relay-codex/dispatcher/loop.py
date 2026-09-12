"""One polling cycle: watch set → artifacts → ledger first → judgment → launch or wait → cursor."""
import datetime
import time
import uuid

from relay_core import RelayError, next_step as steps
from relay_core.watch import LABEL
from . import config as configuration, detect, policy
from .launcher import resume_argv, session_argv, shell_text
from .ledger import artifact_key, iso, item_key, parse

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


def launch(ctx, item, entry):
    """Start the session recorded in entry; a failed launch leaves a pending entry, never a session."""
    ledger, now = ctx.ledger, ctx.clock()
    session_id = str(uuid.uuid4())
    options = ctx.launcher.claude_support() if entry["host"] == "claude" else ()
    argv = session_argv(entry["host"], entry["prompt"], entry["name"], session_id, options)
    if entry["host"] == "claude" and len(options) < 2:
        ctx.log.line(f"{item}: 이 claude는 {set(('--session-id', '--name')) - set(options)} 옵션을 지원하지 않아 뺀다")
    ledger.open_session(item, {"slug": entry["slug"], "number": entry["number"], "stage": entry["stage"], "host": entry["host"],
                               "session_id": session_id, "cwd": entry["cwd"], "name": entry["name"], "argv": argv,
                               "started_at": iso(now)})
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


def resume(ctx, item, session):
    argv = resume_argv(session["host"], session["session_id"])
    return ctx.launcher.launch(session["cwd"], argv, session["name"], session["session_id"])


def judge(ctx, gh, found, item, raw, artifact, settings, head_key=None):
    """Apply D5 to one artifact of one item."""
    ledger, now, slug, number = ctx.ledger, ctx.clock(), found["slug"], raw["number"]
    title, label = raw.get("title"), label_of(artifact)
    session = ledger.session(item)
    if session and session["stage"] == artifact["stage"]:
        ledger.close_session(item, now, f"{artifact['kind']} 산출물 게시")
        ctx.log.event(slug, number, title, label, "세션 완료", f"{session['stage']} 세션이 게시한 산출물")
        session = None
    if artifact["status"] != steps.RECORDED:
        ctx.log.event(slug, number, title, label, "무시", "relay:next 읽을 수 없음")
        return
    next_stage = artifact["next_step"]["next"]
    kind = "pr" if "pull_request" in raw else "issue"
    if next_stage not in (None, "open") and not policy.accepts(ctx.registry, next_stage, kind):
        # A PR item has no issue number to hand to an issue skill, and an issue has no PR to review.
        ctx.log.event(slug, number, title, label, "무시", f"{next_stage}은(는) {'PR' if kind == 'pr' else '이슈'} 번호를 받지 않음")
        return
    action, reason = policy.decide(next_stage, artifact["stage"], session, settings, ctx.config["paused"])
    if action == policy.DONE:
        ledger.pop_pending(item)
        if ledger.close_session(item, now, "next 없음"):
            ctx.log.event(slug, number, title, label, "세션 완료", reason)
        else:
            ctx.log.event(slug, number, title, label, "종료", reason)
        return
    if action == policy.IGNORE:
        ctx.log.event(slug, number, title, label, "무시", reason)
        return
    try:
        prompt = policy.prompt(ctx.registry, settings["host"], next_stage, number, settings, found["root"])
    except RelayError as exc:
        ctx.log.event(slug, number, title, label, "무시", f"프롬프트 조립 실패: {exc}")
        return
    entry = {"slug": slug, "number": number, "stage": next_stage, "host": settings["host"], "cwd": found["root"],
             "prompt": prompt, "name": policy.session_name(slug, number, next_stage), "head_key": head_key,
             "artifact": label, "reason": reason, "gate": reason, "created_at": iso(now), "title": title}
    if action == policy.GATE:
        ledger.set_pending(item, entry)
        ctx.log.event(slug, number, title, label, "게이트 대기", reason,
                      shell_text(session_argv(entry["host"], prompt, entry["name"], "<session-id>")) + " | " + go_command(slug, number))
        return
    launch(ctx, item, entry)


def fresh_artifacts(ctx, slug, candidates):
    return [(artifact_key(slug, a["target"], a["digest"]), a) for a in candidates
            if not ctx.ledger.processed(artifact_key(slug, a["target"], a["digest"]))]


def process_issue(ctx, gh, found, raw, settings, since):
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
        judge(ctx, gh, found, item, raw, artifact, settings, key)
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
    judge(ctx, gh, found, item, raw, head, settings, head_key)


def process_pull(ctx, gh, found, raw, settings, since):
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
    fresh = fresh_artifacts(ctx, slug, candidates)
    if not fresh:
        return
    latest = detect.newest([a for _, a in fresh])
    for key, artifact in fresh:
        ledger.mark_processed(key, now, "판정" if artifact is latest else "같은 주기의 더 최근 산출물에 의해 대체")
    if pull and (pull.get("merged") or pull.get("merged_at")):
        ledger.pop_pending(item)
        ledger.close_session(item, now, "머지됨")
        ctx.log.event(slug, number, raw.get("title"), label_of(latest), "종료", "PR 머지됨")
        return
    judge(ctx, gh, found, item, raw, latest, settings, artifact_key(slug, latest["target"], latest["digest"]))


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


def process_repo(ctx, gh, found, key, settings, start):
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
            process_pull(ctx, gh, found, raw, settings, since)
        else:
            process_issue(ctx, gh, found, raw, settings, since)
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
            process_repo(ctx, gh, found, key, settings, start)
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
                             f"host {config.value['host']}, paused {config.value['paused']}")
        ctx.config = config.value
        try:
            with ctx.ledger.locked():
                cycle(ctx)
                ctx.ledger.save()
        except RelayError as exc:
            ctx.log.line(f"주기 실패 ({exc.code}): {exc}")
        sleep(config.value["poll_seconds"])
