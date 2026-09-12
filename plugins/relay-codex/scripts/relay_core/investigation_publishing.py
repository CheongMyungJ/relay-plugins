"""Exact-candidate approval and one remote comment per investigation."""
import copy
import difflib
import json
import re
import uuid
from pathlib import Path

from . import RelayError, watch
from . import next_step as steps
from .artifacts import decode, digest, normalize, render
from .publishing import matching_request
from .state import read_json, write_json
from .investigation import (applicability, bounded, directory, identifier, load, now,
                            remote_records, save, text, validate_result)

SUMMARY = re.compile(r"\n<details>\n<summary>Relay investigation recovery</summary>\n\n```json\n(.*?)\n```\n</details>\n", re.DOTALL)


def frozen_summary(run):
    # Exclude recursive publication payloads and history that is already journaled.
    return {k: copy.deepcopy(v) for k, v in run.items() if k not in ("pending", "history", "applied_events", "publication", "target", "last_record", "watch")}


def overview(run):
    result = run["conclusion"]
    lines = ["# Investigation\n", "## 결론과 다음 행동\n", result["summary"],
             "\n영향: " + result["impact"], "\n" + steps.block(result["next_step"]).rstrip("\n")]
    if run["outcome"] == "held":
        lines += ["\n필요한 정보: " + "; ".join(result["needed"]), "\n재개 조건: " + "; ".join(result["resume"])]
    elif run["outcome"] == "no_change":
        lines += ["\n종료 이유: " + result["stop_reason"]]
    else:
        lines += ["\n확인한 원인: " + result["cause"]]
    lines += ["\n## 핵심 근거\n"]
    for group, label in (("facts", "사실"), ("observations", "관찰"), ("excluded_causes", "조건부 배제")):
        for item in run[group]:
            lines.append(f"- {label} {item['id']}: {item['summary']}" + (" (근거: " + ", ".join(item["supports"]) + ")" if "supports" in item else ""))
    lines += ["\n## 남은 불확실성과 한계\n", "\n".join("- " + s for s in run["uncertainties"]) or "기록된 추가 불확실성 없음."]
    return "\n".join(lines) + "\n"


def restored_summary(record, state):
    matches = SUMMARY.findall(record["body"])
    if len(matches) != 1:
        raise RelayError("artifact", "Investigation has no unique recovery summary.")
    run = json.loads(matches[0])
    if (run.get("schema") != 1 or run.get("investigation_id") != record["meta"]["run_id"]
            or run.get("work_id") != record["meta"]["work_id"] or run.get("issue") != state["issue"]):
        raise RelayError("artifact", "Recovery summary differs from metadata/issue.")
    from .repository import same_repository
    if not same_repository(run["repository"], state["repository"]):
        raise RelayError("artifact", "Recovery summary belongs to another repository.")
    validate_result(run)
    if not record["body"].startswith(overview(run)):
        raise RelayError("artifact", "Visible conclusion and structured summary disagree.")
    return run


def prepare(store, state, data, gh):
    run = load(store, state, identifier(data.get("investigation_id")))
    if run["publication"] in ("publishing", "uncertain"):
        raise RelayError("uncertain", "Reconcile the original candidate before preparing another.")
    if type(data.get("expected_revision")) is not int or data["expected_revision"] != run["revision"]:
        raise RelayError("conflict", "Candidate revision differs from investigation.")
    validate_result(run)
    if run["outcome"] is None:
        raise RelayError("input", "Choose an evidence-supported outcome before preparing publication.")
    if "next_step" not in data:
        raise RelayError("input", "Submit the candidate's next_step; it is never filled in automatically.")
    suggestion = steps.validate(data["next_step"])
    if suggestion != run["conclusion"]["next_step"]:
        raise RelayError("input", "Candidate next_step differs from the conclusion; correct the conclusion first.")
    path = directory(store, run["investigation_id"])
    body_path = bounded(Path(data["body_file"]), path)
    body = normalize(body_path.read_text(encoding="utf-8-sig"))
    text(body, "reviewed detail body")
    if "Relay investigation recovery" in body or re.search(r"<[^>]*(?:placeholder|작성|제목)[^>]*>", body, re.I):
        raise RelayError("input", "Remove placeholders and generated recovery sections from the draft.")
    steps.reserved(body)
    records = remote_records(state, gh)
    prior = records.get(run["investigation_id"])
    if run.get("target") and (not prior or prior["target"] != run["target"]):
        raise RelayError("conflict", "The investigation target is missing or replaced.")
    old = gh.get_target(state["issue"], prior["target"])["body"] if prior else ""
    if prior and run.get("last_record") and prior["raw_hash"] != run["last_record"]["hash"]:
        # Outer user content is allowed; managed content must remain identical.
        if prior["meta"]["hash"] != run["last_record"]["content_hash"]:
            raise RelayError("conflict", "Investigation managed content changed externally.")
    request, version = uuid.uuid4().hex, prior["meta"]["version"] + 1 if prior else 1
    summary = frozen_summary(run)
    visible = overview(run) + "\n## 상세 재현과 실험\n\n" + body + "\n\n## 변경과 기록 정보\n\n"
    visible += f"조사 결과: {run['outcome']} · 개정 {run['revision']} · 게시 버전 {version}\n"
    visible += "\n<details>\n<summary>Relay investigation recovery</summary>\n\n```json\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\n```\n</details>\n"
    rendered = render(visible, {"kind": "investigation", "work_id": state["work_id"], "run_id": run["investigation_id"],
                               "parents": {}, "version": version, "request_id": request, "updated_at": now()}, old,
                      next_step=suggestion)
    if len(rendered) > 65000:
        raise RelayError("length", "Shorten and review the candidate; never truncate or split it.")
    frozen = {"request_id": request, "hash": digest(rendered), "body": rendered, "revision": run["revision"],
              "version": version, "target": prior["target"] if prior else None, "expected": digest(old) if old else None,
              "summary": summary, "next_step": suggestion, "status": "review"}
    write_json(path / "candidate.json", frozen)
    (path / "review.md").write_text(rendered, encoding="utf-8", newline="\n")
    (path / "change.diff").write_text("".join(difflib.unified_diff(old.splitlines(True), rendered.splitlines(True), fromfile="recorded", tofile="candidate")), encoding="utf-8")
    run.update(pending=frozen, publication="review")
    save(store, state, run)
    return {"request_id": request, "hash": frozen["hash"], "version": version, "next_step": suggestion,
            "review": str(path / "review.md"), "diff": str(path / "change.diff")}


def publish(store, state, data, gh):
    key = identifier(data.get("investigation_id"))
    run = load(store, state, key)
    path = directory(store, key)
    candidate = run["pending"]
    if (not candidate or data.get("approved") is not True or not isinstance(data.get("user"), str) or not data["user"].strip()
            or data.get("request_id") != candidate["request_id"] or data.get("hash") != candidate["hash"]):
        raise RelayError("approval", "Exact investigation candidate approval is required.")
    if (candidate["revision"] != run["revision"] or candidate["summary"] != frozen_summary(run)
            or digest(candidate["body"]) != candidate["hash"]
            or normalize((path / "review.md").read_text(encoding="utf-8")) != candidate["body"]
            or read_json(path / "candidate.json") != candidate):
        raise RelayError("approval", "Frozen evidence or review changed; prepare and review again.")
    target = candidate["target"]
    current = gh.get_target(state["issue"], target) if target else None
    found = matching_request([current] if current else gh.comments(state["issue"]), candidate["request_id"], candidate["hash"])
    if found is None:
        if run["publication"] in ("publishing", "uncertain") and target is None:
            raise RelayError("uncertain", "Creation result is absent; do not replay an ambiguous creation.")
        records = remote_records(state, gh)
        if not target and key in records:
            raise RelayError("conflict", "Investigation was published after preparation.")
        if current and digest(current["body"]) != candidate["expected"]:
            raise RelayError("conflict", "Remote investigation changed after review.")
        write_json(path / "authorization.json", {k: data[k] for k in ("request_id", "hash", "approved", "user")})
        run["publication"] = "uncertain"
        save(store, state, run)
        try:
            found = gh.write(state["issue"], target, candidate["body"])
        except RelayError as exc:
            if exc.code == "github_rejected":
                run["publication"] = "review"
                save(store, state, run)
            raise
    target = str(found["id"])
    run["target"] = candidate["target"] = target
    state.setdefault("investigation_targets", {})[key] = target
    write_json(path / "candidate.json", candidate)
    save(store, state, run)
    verified = gh.get_target(state["issue"], target)
    if digest(verified["body"]) != candidate["hash"]:
        raise RelayError("conflict", "Read-back differs; reconcile the original comment.")
    published = decode(verified["body"], target)
    if published["next_step"] != candidate["next_step"]:
        raise RelayError("conflict", "Published next step differs from the approved candidate.")
    run["publication"] = "recorded"
    run["last_record"] = {"target": target, "url": verified["html_url"], "hash": candidate["hash"],
                          "content_hash": published["meta"]["hash"], "version": candidate["version"],
                          "next_step": published["next_step"]}
    record = watch.mark(gh, state["issue"], state.get("options", {}).get("watch"), candidate.get("watch"))
    if record is not None:
        candidate["watch"] = run["watch"] = run["last_record"]["watch"] = record
        write_json(path / "candidate.json", candidate)
    save(store, state, run)
    return run["last_record"]


def recover(store, state, data, gh):
    key = identifier(data.get("investigation_id"))
    if (directory(store, key) / "investigation.json").exists():
        raise RelayError("conflict", "Local investigation survives; resume it instead of overwriting.")
    target = text(data.get("target"), "target")
    if not target.isdigit():
        raise RelayError("input", "Recovery requires an exact numeric comment ID.")
    record = remote_records(state, gh).get(key)
    if not record or record["target"] != target:
        raise RelayError("conflict", "Comment is not the selected investigation on this issue.")
    run = restored_summary(record, state)
    if run["work_id"] != state["work_id"]:
        raise RelayError("state", "Recover into the original work ID.")
    run.update(pending=None, publication="recorded", target=target, history=[], applied_events=[])
    run["applicability"] = applicability(store, run)
    run["last_record"] = {"target": target, "url": gh.get_target(state["issue"], target)["html_url"],
                          "hash": record["raw_hash"], "content_hash": record["meta"]["hash"], "version": record["meta"]["version"]}
    state.setdefault("investigation_targets", {})[key] = target
    save(store, state, run)
    return run
