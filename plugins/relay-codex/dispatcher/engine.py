"""The dispatch judgment shared by relay-dispatch and relay-server, free of side effects.

The engine reads a collected GitHub snapshot (through `Pages`, whose pages the adapter's
reader fetches), the validated configuration, the registry and a read-only view of the
processed/tracking history, and yields `Decision`s in order. Each decision carries the
bookkeeping `Effect`s the adapter applies before the engine continues, so later judgments of
the same item read the updated history exactly as the single-process loop did.

The engine never queries GitHub, starts processes, writes files or answers web requests.
`close-tracking` ends the tracking of a stage; it is not a process termination.
"""
import datetime
from dataclasses import dataclass, field

from relay_core import RelayError, next_step as steps
from relay_core.kb import handoff as handoffs
from . import config as configuration, detect, policy
from .ledger import artifact_key, item_key, parse

START, GATE, CLOSE, IGNORE, RECONCILE = "start", "gate", "close-tracking", "ignore", "reconcile"
SOURCE_UNKNOWN = "전이 출처 불명: 원본 단계를 확인하고 사람이 재판정해야 함"


@dataclass(frozen=True)
class Effect:
    """A bookkeeping step: processed, close, drop_pending, set_pending, settle, unsettle, handoff, event, line."""
    kind: str
    data: dict


def effect(kind, **data):
    return Effect(kind, data)


def event(slug, number, title, label, action, reason, command=None):
    return effect("event", slug=slug, number=number, title=title, label=label, action=action, reason=reason,
                  command=command)


@dataclass
class Decision:
    action: str
    reason: str
    item: str = None
    entry: dict = None
    effects: list = field(default_factory=list)
    verified: bool = False
    slug: str = None
    number: int = None
    title: str = None
    label: str = None

    def summary(self):
        """The comparable core of a decision, independent of the adapter's rendering."""
        entry = self.entry or {}
        return {"action": self.action, "reason": self.reason, "item": self.item, "stage": entry.get("stage"),
                "host": entry.get("host"), "model": entry.get("model"), "prompt": entry.get("prompt"),
                "source_stage": entry.get("source_stage"), "source_target": entry.get("source_target"),
                "source_digest": entry.get("source_digest"), "handoff": entry.get("handoff"),
                "effects": [(e.kind, {k: v for k, v in e.data.items() if k != "command"}) for e in self.effects]}


def label_of(artifact):
    return artifact["kind"] + (f" v{artifact['version']}" if artifact.get("version") else "")


class Pages:
    """The collection contract: the only GitHub pages a judgment may read, fetched on first use
    through the adapter's reader and kept for the rest of the item's judgment."""

    def __init__(self, reader, number, since):
        self.reader = reader
        self.number = number
        self.since = since
        self.cache = {}

    def _get(self, name, fetch):
        if name not in self.cache:
            self.cache[name] = fetch()
        return self.cache[name]

    def comments_since(self):
        return self._get("comments_since", lambda: self.reader.comments_since(self.number, self.since))

    def comments(self):
        return self._get("comments", lambda: self.reader.comments(self.number))

    def pull(self):
        return self._get("pull", lambda: self.reader.pull(self.number))

    def review_comments_since(self):
        return self._get("review_comments_since", lambda: self.reader.review_comments_since(self.number, self.since))

    def reviews(self):
        return self._get("reviews", lambda: self.reader.reviews(self.number))


class Cycle:
    """Per-cycle inputs: validated configuration, the clock reading and the poll interval."""

    def __init__(self, config, now):
        self.config = config
        self.now = now


class DispatchEngine:
    def __init__(self, registry, *, watch):
        if type(watch) is not bool:
            raise RelayError("input", "the engine needs an explicit watch policy")
        self.registry = registry
        self.watch = watch

    # ---- prompts ---------------------------------------------------------------------------

    def prompt(self, host, stage, number, settings, root=None):
        return policy.prompt(self.registry, host, stage, number, settings, root, watch=self.watch)

    def handoff_prompt(self, host, meta):
        return policy.handoff_prompt(self.registry, host, meta, watch=self.watch)

    # ---- one artifact ----------------------------------------------------------------------

    def judge(self, view, cycle, found, item, raw, artifact, settings, head_key=None, repo_entry=None):
        """D5 for one artifact of one item."""
        slug, number = found["slug"], raw["number"]
        title, label = raw.get("title"), label_of(artifact)
        tag = dict(item=item, slug=slug, number=number, title=title, label=label)
        session = view.session(item)
        if session and session["stage"] == artifact["stage"]:
            yield Decision(CLOSE, f"{artifact['kind']} 산출물 게시", effects=[
                effect("close", item=item, reason=f"{artifact['kind']} 산출물 게시"),
                event(slug, number, title, label, "산출물 게시", f"{session['stage']} 추적 세션 닫힘; 작업 성공 판정 아님")], **tag)
            session = None
        if artifact["status"] != steps.RECORDED:
            yield Decision(IGNORE, "relay:next 읽을 수 없음",
                           effects=[event(slug, number, title, label, "무시", "relay:next 읽을 수 없음")], **tag)
            return
        next_stage = artifact["next_step"]["next"]
        action, reason = policy.decide(next_stage, artifact["stage"], session, settings, cycle.config["paused"])
        if action in (policy.DONE, policy.BLOCKED):
            yield Decision(CLOSE, reason, effects=[
                effect("drop_pending", item=item), effect("close", item=item, reason=reason),
                event(slug, number, title, label, "자동 진행 종료", reason)], **tag)
            return
        kind = "pr" if "pull_request" in raw else "issue"
        resumes = kind == "pr" and policy.resumes_run(artifact, next_stage)
        if not resumes and next_stage not in (None, "open") and not policy.accepts(self.registry, next_stage, kind):
            # A PR item has no issue number to hand to an issue skill, and an issue has no PR to review.
            why = f"{next_stage}은(는) {'PR' if kind == 'pr' else '이슈'} 번호를 받지 않음"
            yield Decision(IGNORE, why, effects=[event(slug, number, title, label, "무시", why)], **tag)
            return
        if action == policy.IGNORE:
            yield Decision(IGNORE, reason, effects=[event(slug, number, title, label, "무시", reason)], **tag)
            return
        selected = configuration.session_settings(cycle.config, repo_entry or {}, next_stage)
        try:
            if resumes:
                prompt = self.handoff_prompt(selected["host"], artifact["handoff"])
            else:
                prompt = self.prompt(selected["host"], next_stage, number, settings, found["root"])
        except RelayError as exc:
            why = f"프롬프트 조립 실패: {exc}"
            yield Decision(IGNORE, why, effects=[event(slug, number, title, label, "무시", why)], **tag)
            return
        effects = []
        selection = selected["selection"]
        if selection["model_state"] == "host-mismatch":
            effects.append(effect("line", text=f"{slug}#{number} {next_stage}: 모델 {selection['requested_model']} 무시 "
                                               f"(출처 {selection['model_scope']}, host {selection['model_host']} "
                                               f"!= 최종 host {selected['host']})"))
        entry = {"slug": slug, "number": number, "stage": next_stage, **selected, "cwd": found["root"],
                 "source_stage": artifact["stage"], "source_target": artifact["target"], "source_digest": artifact["digest"],
                 "prompt": prompt, "name": policy.session_name(slug, number, next_stage), "head_key": head_key,
                 "artifact": label, "reason": reason, "gate": reason, "created_at": iso_now(cycle), "title": title}
        if resumes:
            entry["handoff"] = dict(artifact["handoff"])
        if action == policy.GATE:
            yield Decision(GATE, reason, entry=entry, effects=effects, **tag)
            return
        yield Decision(START, reason, entry=entry, effects=effects, verified=True, **tag)

    # ---- items -----------------------------------------------------------------------------

    @staticmethod
    def fresh(view, slug, candidates):
        return [(artifact_key(slug, a["target"], a["digest"]), a) for a in candidates
                if not view.processed(artifact_key(slug, a["target"], a["digest"]))]

    def issue(self, view, cycle, found, raw, settings, pages, repo_entry=None):
        slug, number, title = found["slug"], raw["number"], raw.get("title")
        item = item_key(slug, number)
        tag = dict(item=item, slug=slug, number=number, title=title)
        candidates = []
        body = detect.from_issue_body(raw)
        if body and (raw.get("updated_at") or "") >= pages.since:
            candidates.append(body)
        for comment in pages.comments_since():
            artifact = detect.from_comment(comment)
            if artifact:
                candidates.append(artifact)
        fresh = self.fresh(view, slug, candidates)
        settling = view.settling(item)
        if not fresh and not settling:
            return
        for key, artifact in [(k, a) for k, a in fresh if a["kind"] == "investigation"]:
            yield Decision(RECONCILE, "판정", effects=[effect("processed", key=key, reason="판정")], **tag)
            yield from self.judge(view, cycle, found, item, raw, artifact, settings, key, repo_entry)
        fresh = [(k, a) for k, a in fresh if a["kind"] != "investigation"]
        if not fresh and not settling:
            return
        try:
            result = detect.chain(raw, pages.comments())
        except RelayError as exc:
            yield Decision(IGNORE, f"문서 체인 오류: {exc}", effects=(
                [effect("processed", key=key, reason="체인 오류") for key, _ in fresh]
                + [event(slug, number, title, "-", "무시", f"문서 체인 오류: {exc}")]), **tag)
            return
        head = result["head"]
        head_key = artifact_key(slug, head["target"], head["digest"]) if head else None
        effects = []
        pending = view.pending(item)
        if pending and pending.get("head_key") != head_key:
            effects += [effect("drop_pending", item=item),
                        event(slug, number, title, pending.get("artifact", "-"), "대체됨", "체인 머리가 바뀜")]
        fresh_keys = {k for k, _ in fresh}
        for key, artifact in fresh:
            if key != head_key:
                if head is None:
                    reason = "머리 없음"
                elif head_key in fresh_keys:
                    reason = "하류 current 문서에 의해 대체"
                else:
                    reason = "머리가 이번 주기 산출물이 아님"  # e.g. watch added after the chain was published
                effects.append(effect("processed", key=key, reason=reason))
        if head is None or (head_key not in fresh_keys and not settling):
            if effects:
                yield Decision(RECONCILE, "체인 정리", effects=effects, **tag)
            return
        if result["downstream_stale"]:
            if fresh:
                effects += [effect("processed", key=head_key, reason="정착 대기"), effect("settle", item=item),
                            event(slug, number, title, label_of(head), "정착 대기(하류 stale)", "상위 개정 진행 중일 수 있음")]
                yield Decision(RECONCILE, "정착 대기", effects=effects, **tag)
                return
            if cycle.now - parse(settling) < datetime.timedelta(seconds=2 * cycle.config["poll_seconds"]):
                if effects:
                    yield Decision(RECONCILE, "체인 정리", effects=effects, **tag)
                return
        effects.append(effect("unsettle", item=item))
        if head_key in fresh_keys:
            effects.append(effect("processed", key=head_key, reason="판정"))
        yield Decision(RECONCILE, "판정", effects=effects, **tag)
        yield from self.judge(view, cycle, found, item, raw, head, settings, head_key, repo_entry)

    def pull(self, view, cycle, found, raw, settings, pages, repo_entry=None):
        slug, number, title = found["slug"], raw["number"], raw.get("title")
        item = item_key(slug, number)
        tag = dict(item=item, slug=slug, number=number, title=title)
        candidates, pull = [], None
        if (raw.get("updated_at") or "") >= pages.since:
            pull = pages.pull()
            artifact = detect.from_pull_body(pull)
            if artifact:
                candidates.append(artifact)
        for comment in pages.comments_since():
            artifact = detect.from_comment(comment, "pr_comment")
            if artifact:
                candidates.append(artifact)
        for comment in pages.review_comments_since():
            artifact = detect.from_review(comment, "inline")
            if artifact:
                candidates.append(artifact)
        for review in pages.reviews():
            if (review.get("submitted_at") or "") >= pages.since:
                artifact = detect.from_review(review, "review")
                if artifact:
                    candidates.append(artifact)
        fresh = yield from self.drop_stale_handoffs(view, found, raw, self.fresh(view, slug, candidates))
        if not fresh:
            return
        latest = detect.newest([a for _, a in fresh])
        label = label_of(latest)
        yield Decision(RECONCILE, "판정", label=label, effects=[
            effect("processed", key=key, reason="판정" if artifact is latest else "같은 주기의 더 최근 산출물에 의해 대체")
            for key, artifact in fresh], **tag)
        if latest.get("handoff"):
            pull = pull or pages.pull()
            if handoffs.body_run(pull.get("body")) != (latest["handoff"]["run_id"], handoffs.PROTOCOL):
                why = "PR 본문의 kb-sync run 마커와 인계 run이 다름"
                yield Decision(IGNORE, why, label=label, effects=[event(slug, number, title, label, "무시", why)], **tag)
                return
        if pull and (pull.get("merged") or pull.get("merged_at")):
            yield Decision(CLOSE, "머지됨", label=label, effects=[
                effect("drop_pending", item=item), effect("close", item=item, reason="머지됨"),
                event(slug, number, title, label, "종료", "PR 머지됨")], **tag)
            return
        if latest.get("handoff"):
            yield Decision(RECONCILE, "인계 기록", label=label, effects=[
                effect("handoff", slug=slug, run_id=latest["handoff"]["run_id"], meta=latest["handoff"],
                       target=latest["target"])], **tag)
        key = artifact_key(slug, latest["target"], latest["digest"])
        yield from self.judge(view, cycle, found, item, raw, latest, settings, key, repo_entry)

    def drop_stale_handoffs(self, view, found, raw, fresh):
        """Keep one handoff per new (run, round, state); repeats, edits and older rounds never reach judgment.

        They are recorded as processed without touching sessions or pending commands, so a
        re-posted or edited comment, a restart or a re-poll cannot hand a round out twice.
        """
        slug, number, title = found["slug"], raw["number"], raw.get("title")
        kept, taken = [], set()
        ordered = sorted(fresh, key=lambda ka: handoffs.order(ka[1]["handoff"]) if ka[1].get("handoff") else (0, 0),
                         reverse=True)
        for key, artifact in ordered:
            meta = artifact.get("handoff")
            if not meta:
                kept.append((key, artifact))
                continue
            seen = view.handoff(slug, meta["run_id"])
            if meta["run_id"] in taken or (seen and handoffs.order(meta) <= handoffs.order(seen)):
                why = f"kb-sync {meta['run_id']} 회차 {meta['round']} {meta['state']}는 이미 판정된 인계보다 새롭지 않음"
                yield Decision(RECONCILE, "중복·오래된 인계 회차", item=item_key(slug, number), slug=slug, number=number,
                               title=title, label=label_of(artifact), effects=[
                                   effect("processed", key=key, reason="중복·오래된 인계 회차"),
                                   event(slug, number, title, label_of(artifact), "무시", why)])
                continue
            taken.add(meta["run_id"])
            kept.append((key, artifact))
        return kept

    # ---- stored commands -------------------------------------------------------------------

    def pending_transition(self, item, entry):
        """None when a stored command's provenance still allows it; otherwise the decision that holds or ends it."""
        tag = dict(item=item, slug=entry.get("slug"), number=entry.get("number"), title=entry.get("title"),
                   label=entry.get("artifact", "-"))
        try:
            source = steps.stage_name(entry.get("source_stage"))
        except RelayError:
            effects = []
            if entry.get("gate") != SOURCE_UNKNOWN:
                effects = [effect("set_pending", item=item, entry=dict(entry, gate=SOURCE_UNKNOWN)),
                           event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "보류",
                                 SOURCE_UNKNOWN)]
            return Decision(RECONCILE, SOURCE_UNKNOWN, effects=effects, **tag)
        if steps.allowed(source, entry.get("stage")) and entry.get("stage") is not None:
            return None
        reason = (steps.blocked_reason(source, entry["stage"]) if entry.get("stage") is not None
                  else "명시적 next 없음: 자동 진행 종료")
        return Decision(CLOSE, reason, effects=[
            effect("drop_pending", item=item),
            event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "자동 진행 종료", reason)],
            **tag)

    @staticmethod
    def handoff_replaced(view, entry):
        seen = view.handoff(entry["slug"], entry["handoff"]["run_id"])
        return not seen or handoffs.order(seen) != handoffs.order(entry["handoff"])

    @staticmethod
    def handoff_remote_reason(entry, pull, comments):
        """Why a stored kb-sync resume no longer matches its PR, or None when it is still current."""
        meta = entry["handoff"]
        found = [handoffs.read(c.get("body") or "") for c in comments]
        found = [f[0] for f in found if f and f[0]["run_id"] == meta["run_id"]]
        latest = max(found, key=handoffs.order, default=None)
        if pull.get("state") != "open" or pull.get("merged") or pull.get("merged_at"):
            return "PR이 닫혔거나 머지됨"
        if handoffs.body_run(pull.get("body")) != (meta["run_id"], handoffs.PROTOCOL):
            return "PR 본문의 kb-sync run 마커와 인계 run이 다름"
        if latest != meta:
            return "PR의 최신 인계가 아님"
        return None

    @staticmethod
    def handoff_unverified(item, entry, code, detail):
        gate = "인계 재확인 실패: " + code
        effects = []
        if entry.get("gate") != gate:
            effects = [effect("set_pending", item=item, entry=dict(entry, gate=gate)),
                       event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "보류",
                             f"{gate} ({detail})")]
        return Decision(RECONCILE, gate, item=item, effects=effects)

    @staticmethod
    def handoff_stale(view, item, entry, reason):
        effects = []
        if (view.pending(item) or {}).get("handoff") == entry["handoff"]:
            effects.append(effect("drop_pending", item=item))
        effects.append(event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"),
                             "자동 진행 종료", reason))
        return Decision(CLOSE, reason, item=item, effects=effects)

    def rebuilt_prompt(self, view, item, entry, config, repo_entry):
        """A stored implement command rebuilt once from its number, host and validated configuration.

        Only the prompt changes; host, model, provenance and the gate stay as queued.
        Returns (entry or None, decision).
        """
        try:
            text = self.prompt(entry["host"], "implement", entry["number"], configuration.settings(config, repo_entry),
                               entry["cwd"])
        except RelayError as exc:
            gate = "프롬프트 재생성 실패: " + str(exc)
            effects = []
            if entry.get("gate") != gate:
                effects = [effect("set_pending", item=item, entry=dict(entry, gate=gate)),
                           event(entry["slug"], entry["number"], entry.get("title"), entry.get("artifact", "-"), "보류", gate)]
            return None, Decision(RECONCILE, gate, item=item, effects=effects)
        effects = []
        stored = view.pending(item)
        if stored is not None:
            effects.append(effect("set_pending", item=item, entry=dict(stored, prompt=text)))
        effects.append(effect("line", text=f"{item}: 제거된 implement 옵션이 든 대기 명령을 다시 만들었다: {text}"))
        return dict(entry, prompt=text), Decision(RECONCILE, "프롬프트 재생성", item=item, effects=effects)

    @staticmethod
    def paused_queue(pending, paused):
        """Entries queued only because of pause, in their queued order, once unpaused."""
        if paused:
            return []
        return sorted(((k, v) for k, v in pending.items() if v.get("gate") == "일시정지"),
                      key=lambda kv: kv[1].get("created_at", ""))

    @staticmethod
    def vanished(item, slug, number, current):
        """An item that left the watch set ends its tracking only when it is closed or merged."""
        if current.get("state") == "open" and not current.get("merged"):
            return None
        return Decision(CLOSE, "닫힘", item=item, slug=slug, number=number, title=current.get("title"), effects=[
            effect("drop_pending", item=item), effect("close", item=item, reason="닫힘"),
            event(slug, number, current.get("title"), "-", "종료", "이슈·PR이 닫힘")])


def iso_now(cycle):
    from .ledger import iso
    return iso(cycle.now)
