"""Judgment and prompt assembly. Nothing read from GitHub ever enters a prompt."""
import re

from relay_core import RelayError, next_step as steps
from relay_core.kb import handoff as handoffs
from .config import option_value

# Adding a host is one entry: how a skill is addressed, and which registry key names it.
HOSTS = {"claude": {"prefix": "/relay:", "name": "claude"},
         "codex": {"prefix": "$", "name": "codex"},
         "opencode": {"prefix": "/", "name": "opencode", "fallback": "codex"}}

AUTO, GATE, IGNORE, DONE = "auto", "gate", "ignore", "done"
BLOCKED = "blocked"


def skill_name(registry, host, stage):
    if stage not in registry:
        raise RelayError("input", "unknown skill: " + str(stage))
    table = HOSTS[host]
    return registry[stage].get(table["name"]) or registry[stage][table["fallback"]]


def prompt(registry, host, stage, number, settings, root=None):
    """`<prefix><skill> <number> [--reviewer] --watch [--lang x]`.

    Numbers, registered skill names and validated configuration values only. The skill itself
    prepares the issue workspace, so no path, base or branch travels in the prompt.
    """
    if type(number) is not int or number < 1:
        raise RelayError("input", "issue or PR number must be a positive integer")
    parts = [HOSTS[host]["prefix"] + skill_name(registry, host, stage), str(number)]
    if stage == "review":
        parts.append("--reviewer")
    parts.append("--watch")
    lang = settings.get("defaults", {}).get("lang")
    if lang and "lang" in registry[stage]["options"]:
        parts += ["--lang", option_value(lang, "lang")]
    return " ".join(parts)


def legacy_implement_prompt(entry):
    """A stored implement command that still carries an option implement no longer accepts."""
    return entry.get("stage") == "implement" and bool(re.search(r"(?:^|\s)--(?:worktree|base|branch)(?:[=\s]|$)",
                                                                   str(entry.get("prompt", ""))))


def handoff_prompt(registry, host, meta):
    """`<prefix><kb-sync> --resume <run_id> --limit <n> --handoff <round> --watch` from validated metadata only."""
    meta = handoffs.validate(meta)
    if meta["state"] != "paused":
        raise RelayError("input", "only a paused handoff resumes kb-sync")
    return " ".join([HOSTS[host]["prefix"] + skill_name(registry, host, "kb-sync"), "--resume", meta["run_id"],
                     "--limit", str(meta["limit"]), "--handoff", str(meta["round"]), "--watch"])


def resumes_run(artifact, next_stage):
    """A validated paused kb-sync handoff continues its run without a number (the PR check's one exception)."""
    meta = artifact.get("handoff")
    return bool(meta) and artifact["stage"] == "kb-sync" and next_stage == "kb-sync" and meta["state"] == "paused"


def session_name(slug, number, stage):
    return f"relay {slug}#{number} {stage}"


def accepts(registry, stage, item_kind):
    """Whether the skill takes this item's number: relay.json `targets` names issue or pr (D6)."""
    return item_kind in registry.get(stage, {}).get("targets", [])


def decide(next_stage, artifact_stage, session, settings, paused):
    """One artifact's judgment after the chain and session bookkeeping are settled.

    Returns (action, reason). GATE reasons name why a person decides instead.
    """
    if next_stage is None:
        return DONE, "명시적 next 없음: 자동 진행 종료"
    if not steps.allowed(artifact_stage, next_stage):
        return BLOCKED, steps.blocked_reason(artifact_stage, next_stage)
    if session:
        return GATE, "열린 세션 있음"
    if next_stage in settings["gated"]:
        return GATE, "게이트 대기"
    if next_stage in settings["auto"]:
        return (GATE, "일시정지") if paused else (AUTO, "자동 시작")
    return IGNORE, "auto·gated에 없음"
