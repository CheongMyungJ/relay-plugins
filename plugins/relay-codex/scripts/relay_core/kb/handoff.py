"""The kb-sync handoff anchor shared by the helper, which writes it, and the dispatcher, which reads it.

A handoff comment on the run's draft PR ends one call: the readable text, the rendered
relay:next block, then `<!-- relay:kb-sync-handoff {...} -->` as the last line. The
metadata carries exactly schema, run_id, the run's initial limit, the round and the state;
`(run_id, round)` identifies the handoff. Reading is anchored to the end of the body, and
the state must agree with next: a paused round suggests kb-sync, a finished run review or
null. The PR body only names the run and the protocol and is never a signal. Nothing here
reads local run state.
"""
import json
import re
from .. import RelayError, next_step as steps

SCHEMA = 1
PROTOCOL = 1
MARKER = "<!-- relay:kb-sync-handoff "
STATES = {"paused": ("kb-sync",), "finished": ("review", None)}
RUN_ID = re.compile(r"[a-f0-9]{12,32}")
TAIL = re.compile(r"<!-- relay:kb-sync-handoff (\{[^\n]*?\}) -->\s*\Z")
BODY = re.compile(r"<!-- relay:kb-sync ([a-f0-9]{12,32}) handoff:([1-9][0-9]*) -->")


def positive(value):
    return type(value) is int and value >= 1


def validate(meta):
    """Exactly the five fields with valid values; raises input on anything else."""
    if not isinstance(meta, dict) or set(meta) != {"schema", "run_id", "limit", "round", "state"}:
        raise RelayError("input", "Handoff metadata has exactly schema, run_id, limit, round and state.")
    if meta["schema"] != SCHEMA or not isinstance(meta["run_id"], str) or not RUN_ID.fullmatch(meta["run_id"]):
        raise RelayError("input", "Handoff metadata has an unknown schema or an invalid run ID.")
    if (not positive(meta["limit"]) or not positive(meta["round"])
            or not isinstance(meta["state"], str) or meta["state"] not in STATES):
        raise RelayError("input", "Handoff limit and round are positive integers and state is paused or finished.")
    return dict(meta)


def anchor(meta):
    return MARKER + json.dumps(validate(meta), sort_keys=True, separators=(",", ":")) + " -->"


def render(text, next_step, meta):
    """The comment body; its anchor doubles as the comment's identity marker."""
    meta = validate(meta)
    if next_step["next"] not in STATES[meta["state"]]:
        raise RelayError("input", "Handoff next does not match its state.")
    if MARKER in text or steps.read(text)[0] == steps.RECORDED:
        raise RelayError("input", "Handoff text must not carry generated markers.")
    return text.rstrip("\n") + "\n\n" + steps.rendered(next_step) + "\n\n" + anchor(meta) + "\n"


def read(text):
    """(meta, next_step) of a well-formed handoff comment, or None for anybody's other text."""
    found = TAIL.search(text or "")
    if not found:
        return None
    try:
        meta = validate(json.loads(found.group(1), object_pairs_hook=steps.pairs))
    except (ValueError, RelayError):
        return None
    status, value = steps.read(text, before=MARKER)
    if status != steps.RECORDED or value["next"] not in STATES[meta["state"]]:
        return None
    return meta, value


def order(meta):
    """Handoffs of one run compare by round, and a finished record follows a paused one of that round."""
    return (meta["round"], 1 if meta["state"] == "finished" else 0)


def body_marker(run_id):
    return f"<!-- relay:kb-sync {run_id} handoff:{PROTOCOL} -->"


def body_run(body):
    """(run_id, protocol) named by a handoff-protocol PR body, or None for a body without it."""
    found = BODY.search(body or "")
    return (found.group(1), int(found.group(2))) if found else None
