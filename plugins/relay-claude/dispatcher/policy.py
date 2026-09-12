"""Judgment and prompt assembly. Nothing read from GitHub ever enters a prompt."""
import re
from pathlib import Path

from relay_core import RelayError
from .config import option_value

# Adding a host is one entry: how a skill is addressed, and which registry key names it.
HOSTS = {"claude": {"prefix": "/relay:", "name": "claude"},
         "codex": {"prefix": "$", "name": "codex"},
         "opencode": {"prefix": "/", "name": "opencode", "fallback": "codex"}}

AUTO, GATE, IGNORE, DONE = "auto", "gate", "ignore", "done"


def skill_name(registry, host, stage):
    if stage not in registry:
        raise RelayError("input", "unknown skill: " + str(stage))
    table = HOSTS[host]
    return registry[stage].get(table["name"]) or registry[stage][table["fallback"]]


def worktree_path(template, root, issue):
    root = Path(root)
    return str(Path(template.format(parent=str(root.parent), name=root.name, issue=issue)))


def prompt(registry, host, stage, number, settings, root):
    """`<prefix><skill> <number> [--reviewer] --watch [--worktree="…"] [--lang x]`.

    Numbers, registered skill names and validated configuration values only.
    """
    if type(number) is not int or number < 1:
        raise RelayError("input", "issue or PR number must be a positive integer")
    parts = [HOSTS[host]["prefix"] + skill_name(registry, host, stage), str(number)]
    if stage == "review":
        parts.append("--reviewer")
    parts.append("--watch")
    if stage == "implement":
        path = worktree_path(settings["worktree_template"], root, number)
        if re.search(r"[\"'\r\n]", path):
            raise RelayError("input", "worktree path must not contain quotes or line breaks")
        parts.append('--worktree="' + path + '"')
    lang = settings.get("defaults", {}).get("lang")
    if lang and "lang" in registry[stage]["options"]:
        parts += ["--lang", option_value(lang, "lang")]
    return " ".join(parts)


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
        return DONE, "next 없음"
    if next_stage == "open":
        return IGNORE, "open은 이슈 번호를 받지 않음"
    if next_stage == artifact_stage:
        return GATE, "자기 재추천"
    if session:
        return GATE, "열린 세션 있음"
    if next_stage in settings["gated"]:
        return GATE, "게이트 대기"
    if next_stage in settings["auto"]:
        return (GATE, "일시정지") if paused else (AUTO, "자동 시작")
    return IGNORE, "auto·gated에 없음"
