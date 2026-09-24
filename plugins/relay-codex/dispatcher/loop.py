"""The local adapter of the dispatch engine. One polling cycle: watch set → engine decisions →
ledger and log → launch or wait → cursor.

Judgment lives in `engine`; this module owns the caller's assignee filter, the ledger writes,
the launcher, the stored-command rechecks and the log.
"""
import datetime
import time
import uuid

from relay_core import RelayError
from relay_core.watch import LABEL
from . import config as configuration, engine as engines, policy
from .launcher import resume_argv, shell_text
from .ledger import iso, item_key, model_fields, parse

OVERLAP = datetime.timedelta(minutes=5)


class Context:
    def __init__(self, *, config, ledger, launcher, log, clock, registry, login, remote_for, observer=None):
        self.config = config
        self.ledger = ledger
        self.launcher = launcher
        self.log = log
        self.clock = clock
        self.registry = registry
        self.login = login
        self.remote_for = remote_for
        self.observer = observer
        self._engine = None

    @property
    def engine(self):
        if self._engine is None or self._engine.registry is not self.registry:
            self._engine = engines.DispatchEngine(self.registry, watch=True)
        return self._engine

    def cycle_input(self):
        return engines.Cycle(self.config, self.clock())


label_of = engines.label_of


def go_command(slug, number):
    return f"relay-dispatch go {slug}#{number}"


def apply(ctx, effects):
    """Write one decision's bookkeeping to the ledger and the log, in order."""
    ledger, now = ctx.ledger, ctx.clock()
    for step in effects:
        data = step.data
        if step.kind == "processed":
            ledger.mark_processed(data["key"], now, data["reason"])
        elif step.kind == "close":
            ledger.close_session(data["item"], now, data["reason"])
        elif step.kind == "drop_pending":
            ledger.pop_pending(data["item"])
        elif step.kind == "set_pending":
            ledger.set_pending(data["item"], data["entry"])
        elif step.kind == "settle":
            ledger.set_settling(data["item"], now)
        elif step.kind == "unsettle":
            ledger.clear_settling(data["item"])
        elif step.kind == "handoff":
            ledger.record_handoff(data["slug"], data["run_id"], data["meta"], data["target"], now)
        elif step.kind == "event":
            ctx.log.event(data["slug"], data["number"], data["title"], data["label"], data["action"], data["reason"],
                          data.get("command"))
        elif step.kind == "line":
            ctx.log.line(data["text"])
        else:
            raise RelayError("run", "unknown engine effect: " + step.kind)


def carry_out(ctx, decisions):
    """Apply each decision before the engine judges further, so it reads the updated ledger."""
    for decision in decisions:
        apply(ctx, decision.effects)
        if ctx.observer is not None:
            ctx.observer(decision)
        if decision.action == engines.GATE:
            entry = decision.entry
            ctx.ledger.set_pending(decision.item, entry)
            ctx.log.event(entry["slug"], entry["number"], entry.get("title"), entry["artifact"], "게이트 대기", decision.reason,
                          shell_text(ctx.launcher.session_argv(entry)) + " | " + go_command(entry["slug"], entry["number"]))
        elif decision.action == engines.START:
            launch(ctx, decision.item, decision.entry, verified=decision.verified)


def launch(ctx, item, entry, verified=False):
    """Start the session recorded in entry; a failed launch leaves a pending entry, never a session.

    A kb-sync resume that did not come straight from this cycle's judgment is checked
    against the PR first, so go, unpausing and launcher retries never run a replaced round.
    """
    if not check_pending_transition(ctx, item, entry):
        return False
    if entry.get("handoff") and not verified and not handoff_current(ctx, item, entry):
        return False
    if policy.legacy_implement_prompt(entry):
        entry = regenerate_prompt(ctx, item, entry)
        if entry is None:
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
    engine = ctx.engine
    if engine.handoff_replaced(ctx.ledger, entry):
        reason = "새 인계 회차로 대체됨"
    else:
        try:
            gh = ctx.remote_for(configuration.identity(entry["cwd"]))
            pull = gh.pull(entry["number"])
            comments = gh.comments(entry["number"])
        except RelayError as exc:
            apply(ctx, engine.handoff_unverified(item, entry, exc.code, exc).effects)
            return False
        reason = engine.handoff_remote_reason(entry, pull, comments)
    if reason is None:
        return True
    apply(ctx, engine.handoff_stale(ctx.ledger, item, entry, reason).effects)
    return False


def resume(ctx, item, session):
    argv = resume_argv(session["host"], session["session_id"], model=model_fields(session)["model"])
    return ctx.launcher.launch(session["cwd"], argv, session["name"], session["session_id"])


def check_pending_transition(ctx, item, entry):
    """Check stored provenance without reconstructing prompts or model selection."""
    decision = ctx.engine.pending_transition(item, entry)
    if decision is None:
        return True
    apply(ctx, decision.effects)
    return False


def regenerate_prompt(ctx, item, entry):
    """Rebuild a stored implement command once; returns the updated entry, or None (it then waits with a reason)."""
    repo_entry = {"path": entry["cwd"]}
    for candidate in ctx.config["repos"]:
        try:
            if configuration.identity(candidate["path"])["root"] == entry["cwd"]:
                repo_entry = candidate
                break
        except RelayError:
            continue
    rebuilt, decision = ctx.engine.rebuilt_prompt(ctx.ledger, item, entry, ctx.config, repo_entry)
    apply(ctx, decision.effects)
    return rebuilt


def reconcile_pending(ctx):
    # Even processed artifacts and advanced cursors cannot grandfather old commands.
    for item, entry in list(ctx.ledger.data["pending"].items()):
        if check_pending_transition(ctx, item, entry) and policy.legacy_implement_prompt(entry):
            regenerate_prompt(ctx, item, entry)


def judge(ctx, gh, found, item, raw, artifact, settings, head_key=None, repo_entry=None):
    """Apply D5 to one artifact of one item."""
    carry_out(ctx, ctx.engine.judge(ctx.ledger, ctx.cycle_input(), found, item, raw, artifact, settings, head_key,
                                    repo_entry))


def process_issue(ctx, gh, found, raw, settings, since, repo_entry=None):
    pages = engines.Pages(gh, raw["number"], since)
    carry_out(ctx, ctx.engine.issue(ctx.ledger, ctx.cycle_input(), found, raw, settings, pages, repo_entry))


def process_pull(ctx, gh, found, raw, settings, since, repo_entry=None):
    pages = engines.Pages(gh, raw["number"], since)
    carry_out(ctx, ctx.engine.pull(ctx.ledger, ctx.cycle_input(), found, raw, settings, pages, repo_entry))


def reconcile_vanished(ctx, gh, found, present):
    """Sessions and gates for items that left the watch set end only when the item is closed."""
    ledger, slug = ctx.ledger, found["slug"]
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
        decision = ctx.engine.vanished(item, slug, number, current)
        if decision is not None:
            carry_out(ctx, [decision])


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
    for item, entry in ctx.engine.paused_queue(ctx.ledger.data["pending"], ctx.config["paused"]):
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
