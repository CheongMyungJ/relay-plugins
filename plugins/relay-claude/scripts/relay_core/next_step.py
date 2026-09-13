"""The two next-step fields carried by every published artifact.

`next` names one registered Relay skill or is JSON null; `reason` is one line of
plain text. Nothing else belongs here: no number, target, argument, state,
authorization or reference. The object is a suggestion that assumes the current
publication succeeds, never an execution permission, and the comment lives
inside the body the existing candidate/approval hashes already cover.
"""
import json
import re
from pathlib import Path
from . import RelayError

MARKER = "<!-- relay:next"
# Writing puts the comment in exactly one place, so reading is anchored to that
# place instead of searching the body. Prose that quotes this syntax - a spec or
# plan describing the format - stays ordinary text and is never mistaken for the
# artifact's own suggestion.
TAIL = re.compile(r"\n?<!-- relay:next (\{.*?\}) -->\s*\Z")
REGISTRY_PATH = Path(__file__).resolve().parents[2] / "relay.json"
NONE_LABEL = "없음"
RECORDED, UNREADABLE = "recorded", "unreadable"


REGISTERED = None

# Runtime policy. The host-facing decision contract lives in references/next-step.md.
TRANSITIONS = {
    "open": frozenset(("intent", "brief")),
    "intent": frozenset(("design", "brief")),
    "design": frozenset(("plan", "brief")),
    "plan": frozenset(("implement",)),
    "brief": frozenset(("implement",)),
    "implement": frozenset(("pr",)),
    "pr": frozenset(("review",)),
    "review": frozenset(),
    "investigate": frozenset(("intent", "design", "brief")),
    "kb": frozenset(("review",)),
    "kb-sync": frozenset(("kb-sync", "review")),
}
ARTIFACT_STAGES = {"issue": "open", "spec": "design", "implementation": "implement",
                   "investigation": "investigate"}


def stage_name(stage):
    if not isinstance(stage, str):
        raise RelayError("input", "A known source stage is required for transition policy.")
    stage = ARTIFACT_STAGES.get(stage, stage)
    if stage not in TRANSITIONS:
        raise RelayError("input", "Unknown source stage: " + stage)
    return stage


def allowed(stage, choice):
    stage = stage_name(stage)
    return choice is None or (isinstance(choice, str) and choice in TRANSITIONS[stage])


def blocked_reason(stage, choice):
    return f"허용되지 않은 전이 {stage_name(stage)} → {choice}: 자동 진행 종료."


def normalize(stage, value):
    """Normalize new candidates only, after the caller's outcome-specific checks."""
    value = validate(value)
    if allowed(stage, value["next"]):
        return value
    return {"next": None, "reason": blocked_reason(stage, value["next"]) + " 제출 사유: " + value["reason"]}


def require_allowed(stage, value):
    """Guard a new write of frozen content; never rewrite an approved candidate."""
    value = validate(value)
    if not allowed(stage, value["next"]):
        raise RelayError("approval", "Transition policy changed; preserve the original request and prepare, review and approve a new candidate.")


def skills():
    global REGISTERED
    if REGISTERED is None:
        REGISTERED = frozenset(json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["skills"])
    return REGISTERED


def pairs(items):
    names = [name for name, _ in items]
    if len(set(names)) != len(names):
        raise ValueError("duplicate object keys")
    return dict(items)


def validate(value):
    """Accept exactly {next, reason}; a missing selection is explicit null, never omission."""
    if not isinstance(value, dict):
        raise RelayError("input", "next_step must be an object with exactly next and reason.")
    if set(value) != {"next", "reason"}:
        raise RelayError("input", "next_step has exactly the keys next and reason.")
    choice, reason = value["next"], value["reason"]
    if choice is not None and (not isinstance(choice, str) or choice not in skills()):
        raise RelayError("input", "next must be a registered Relay skill name or null.")
    if not isinstance(reason, str) or not reason or reason != reason.strip():
        raise RelayError("input", "reason must be nonempty text without surrounding whitespace.")
    if any(character < " " for character in reason):
        raise RelayError("input", "reason must be a single plain line.")
    return {"next": choice, "reason": reason}


def serialize(value):
    text = json.dumps(validate(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Escaping these three characters keeps a reason from ending the HTML comment.
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def encode(value):
    return MARKER + " " + serialize(value) + " -->"


def detach(text, before=""):
    """Return (remainder, status, value) by rebuilding the tail, never by matching prose.

    Only the anchored comment counts. Whatever fails to parse and validate there is
    somebody's text, not this artifact's suggestion: callers verify the content hash
    first, so a comment the helper actually wrote cannot be malformed at this point.
    `before` names the identifying marker the tail sits in front of - the PR request
    marker or a review unit marker - and defaults to the very end of the text. The
    readable block is removed only when it sits immediately before the comment, so a
    path that renders it elsewhere - the investigation overview - keeps its own copy.
    """
    head, marker, rest = text.rpartition(before) if before else (text, "", "")
    if before and not marker:
        return text, UNREADABLE, None
    found = TAIL.search(head)
    if not found:
        return text, UNREADABLE, None
    try:
        value = validate(json.loads(found.group(1), object_pairs_hook=pairs))
    except (ValueError, RelayError):
        return text, UNREADABLE, None
    remainder = head[:found.start()]
    lines = "\n\n" + block(value)
    if remainder.endswith(lines):
        remainder = remainder[:-len(lines)]
    return remainder + marker + rest, RECORDED, value


def read(text, before=""):
    """Return (status, value). Absence is unreadable, which is not the same as next=null."""
    return detach(text, before)[1:]


def block(value):
    """The human-readable lines rendered from the same object as the comment.

    These labels stay Korean in every working language. detach() rebuilds this exact
    string to cut it back out, and an artifact does not record which language it was
    rendered in, so a locale-dependent block could not be removed again reliably.
    """
    value = validate(value)
    return "다음 단계: " + (value["next"] or NONE_LABEL) + "\n사유: " + value["reason"] + "\n"


def rendered(value):
    return block(value) + "\n" + encode(value)


def reserved(text):
    """Reject a draft that already carries a generated tail; quoting the syntax is fine."""
    if read(text)[0] == RECORDED:
        raise RelayError("input", "The helper adds the relay:next comment; remove it from the draft.")
