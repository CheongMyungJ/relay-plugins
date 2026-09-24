"""Declared helper inputs: the fields each command and action reads, checked before any I/O (spec R3).

`check(command, data)` runs in `cli.main` before dispatch, so a missing or malformed field
fails with its path and expected format before any remote call. The table lists only
fields a handler actually reads: required ones fail when absent, optional ones are
checked only when present, and unknown extra fields are accepted as before. Deeper
rules (state, approval, verification) stay with the handlers and their own error codes.
"""
import re
from . import RelayError

STAGES = ("open", "intent", "design", "plan", "brief", "implement", "investigate", "pr", "review")


class Kind:
    """One field format: a predicate plus the words used in the error message."""

    def __init__(self, expected, test, items=None, fields=None):
        self.expected, self.test, self.items, self.fields = expected, test, items, fields


def one_of(*values):
    return Kind("one of " + ", ".join(values), lambda v: isinstance(v, str) and v in values)


def listing(items=None, expected="list"):
    return Kind(expected, lambda v: isinstance(v, list), items=items)


def obj(fields=None, expected="object"):
    return Kind(expected, lambda v: isinstance(v, dict), fields=fields)


def integer(low=None, high=None):
    words = "integer" if low is None else f"integer from {low} to {high}" if high is not None else f"integer ≥ {low}"
    return Kind(words, lambda v: type(v) is int and (low is None or v >= low) and (high is None or v <= high))


ANY = Kind("value", lambda v: True)
TEXT = Kind("string", lambda v: isinstance(v, str))
STR = Kind("nonempty string", lambda v: isinstance(v, str) and bool(v.strip()))
BOOL = Kind("true or false", lambda v: isinstance(v, bool))
TRUE = Kind("true", lambda v: v is True)
HEX32 = Kind("32 lowercase hex characters", lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{32}", v) is not None)
ABS = Kind("absolute path string", lambda v: isinstance(v, str) and re.match(r"([A-Za-z]:[\\/]|[\\/])", v) is not None)
STRINGS = listing(STR, "list of nonempty strings")
NEXT_STEP = obj({"next": ("required", ANY), "reason": ("required", ANY)}, "object with next and reason")
PAGE = {"cursor": STR, "sha": STR}


def fields(required=(), **optional):
    """{name: (presence, kind)}; `required` names (name, kind) pairs."""
    table = {name: ("optional", kind) for name, kind in optional.items()}
    table.update({name: ("required", kind) for name, kind in required})
    return table


# Run events: tests keep their verification error for empty lists or failed checks; the
# table only makes their structure readable.
TEST = obj({"command": ("optional", TEXT), "exit_code": ("optional", integer()), "evidence": ("optional", TEXT)})
DRIFT = obj({key: ("required", ANY) for key in ("planned", "actual", "reason", "impact", "severity", "evidence")})
HOLD = obj({key: ("optional", ANY) for key in ("condition", "detail", "evidence")})
RUN = {
    "begin": fields(execution_authorized=BOOL, new_run=BOOL),
    "restore": fields([("run_id", STR)], execution_authorized=BOOL, path=STR),
    "prepared": fields(run_id=STR),
    "verified": fields(run_id=STR, tests=listing(TEST, "list of test objects")),
    "committed": fields(run_id=STR),
    "pushed": fields(run_id=STR),
    "drift": fields([("entry", DRIFT)], run_id=STR),
    "failure": fields([("reason", STR)], run_id=STR),
    "hold": fields([("entry", HOLD)], run_id=STR),
}

DOCUMENT = {"target": ("required", STR), "version": ("required", integer(1)), "hash": ("required", STR)}
WORKSPACE = {
    "ensure": fields(request_id=HEX32),
    "refresh": fields([("request_id", HEX32), ("user_requested", TRUE)]),
    "assess": fields([("documents", obj(expected="object mapping intent/spec/plan/brief to {target, version, hash}")),
                      ("reason", STR)]),
}

INVESTIGATE = {
    "start": fields([("client_request_id", HEX32), ("request", obj()), ("baseline", obj({"cwd": ("required", STR)}))],
                    execution_authorized=BOOL, new_investigation=BOOL),
    "checkpoint": fields([("investigation_id", HEX32), ("event_id", HEX32), ("expected_revision", integer()),
                          ("changes", obj())]),
    "resume": fields([("investigation_id", HEX32), ("event_id", HEX32), ("expected_revision", integer()),
                      ("reason", STR), ("baseline", obj({"cwd": ("required", STR)}))]),
    "prepare": fields([("investigation_id", HEX32), ("expected_revision", integer()), ("body_file", STR),
                       ("next_step", NEXT_STEP)]),
    "publish": fields([("investigation_id", HEX32), ("request_id", STR), ("hash", STR)], approved=BOOL, user=TEXT),
    "recover": fields([("investigation_id", HEX32), ("target", STR)]),
}

PR = {
    "inspect": fields(raw=TEXT, cwd=ABS, request_id=HEX32, head=STR, base=STR, selection_reason=STR, template=STR),
    "prepare": fields([("title", TEXT), ("body_file", STR), ("next_step", NEXT_STEP)], raw=TEXT, cwd=ABS,
                      request_id=HEX32, inspection_hash=STR, draft=BOOL, no_push=BOOL, draft_only=BOOL,
                      head=STR, base=STR, selection_reason=STR, template=STR),
    "create": fields([("request_id", HEX32), ("hash", STR)], cwd=ABS, execution_authorized=BOOL),
    "resume": fields([("request_id", HEX32)], cwd=ABS),
    "update": fields([("expected_hash", STR), ("title", TEXT), ("body_file", STR), ("next_step", NEXT_STEP)],
                     cwd=ABS, number=integer(1), request_id=HEX32, execution_authorized=BOOL, watch=BOOL),
}

REVIEW = {
    "inspect": fields(raw=TEXT, cwd=ABS, pr=integer(1), selection_reason=STR),
    "prepare": fields([("run_id", HEX32), ("snapshot_hash", STR), ("draft_file", STR), ("next_step", NEXT_STEP)],
                      cwd=ABS, items=listing(), operations=listing(), code_scope=listing(), verification_scope=listing()),
    "execute": fields([("run_id", HEX32)], cwd=ABS, hash=TEXT, decision=obj(), worktree=STR, results_shown=BOOL,
                      tests=listing(), diff_reviewed=BOOL),
    "resume": fields([("run_id", HEX32)], cwd=ABS,
                     reassessment=obj({"current_snapshot_hash": ("required", STR), "impact": ("required", one_of("unrelated", "revise")),
                                       "reason": ("required", STR)})),
}

LOOKUP = fields(paths=listing(), terms=listing(), ids=listing(), detail=one_of("summary", "full"), include_inactive=BOOL,
                limit=integer(1, 50), **PAGE)
KB = {
    "lookup": LOOKUP,
    "fragment": fields(ids=listing(), path=STR, symbols=listing(), since=STR, **PAGE),
    "check": fields([("sha", STR), ("changes", obj())], scope=listing(), checked=obj(), execution_id=STR, batch_id=STR, issued=obj()),
    "render": fields(worktree=STR),
    "apply": fields([("plan", obj()), ("changes", obj()), ("worktree", STR), ("record", STR)], verdicts=obj()),
    "inspect": fields(raw=TEXT, request_id=HEX32, pr=integer(1), section=one_of("summary", "sources", "entries"), cursor=STR),
    "prepare": fields([("request_id", HEX32)], changes_file=STR, next_step=NEXT_STEP),
    "publish": fields([("request_id", HEX32)], hash=STR, approved=BOOL, user=TEXT, publish_only=BOOL, worktree=STR),
    "begin": fields(raw=TEXT, new_run=BOOL),
    "batch": fields([("run_id", STR)], files=listing(), ids=listing(), symbols=obj(), list=BOOL, batch_id=STR, cursor=STR),
    "checkpoint": fields([("run_id", STR)], batch_id=STR, changes_file=STR),
    "resume": fields([("run_id", STR)], limit=ANY, watch=ANY, handoff=ANY),
    "handoff": fields([("run_id", STR)]),
    "finish": fields([("run_id", STR)]),
}

COMMANDS = {
    "inspect": (None, {None: fields([("stage", one_of(*STAGES))], raw=TEXT, cwd=ABS, work_id=STR, investigation_id=STR,
                                    legacy_confirmed=BOOL, legacy=obj())}),
    "prepare": (None, {None: fields([("body_file", STR), ("next_step", NEXT_STEP)], title=TEXT, adopt=BOOL, run_id=STR,
                                    evidence_refs=listing())}),
    "publish": (None, {None: fields([("request_id", HEX32), ("hash", STR)], approved=BOOL, user=TEXT,
                                    execution_authorized=BOOL, run_id=STR)}),
    "run": ("action", RUN),
    "workspace": ("operation", WORKSPACE),
    "investigate": ("operation", INVESTIGATE),
    "pr": ("action", PR),
    "review": ("action", REVIEW),
    "kb": ("action", KB),
}


def label(command, action):
    return command + (" " + action if action else "")


def fail(command, action, path, expected, missing=False):
    raise RelayError("input", f"{label(command, action)}: {path} — {'required; ' if missing else ''}expected {expected}")


def verify(command, action, path, kind, value):
    if not kind.test(value):
        fail(command, action, path, kind.expected)
    if kind.fields is not None:
        walk(command, action, path + ".", kind.fields, value)
    if kind.items is not None:
        for index, item in enumerate(value):
            verify(command, action, f"{path}[{index}]", kind.items, item)


def walk(command, action, prefix, table, data):
    for name, (presence, kind) in table.items():
        if name not in data or (data[name] is None and presence == "optional"):
            if presence == "required":
                fail(command, action, prefix + name, kind.expected, missing=True)
            continue
        verify(command, action, prefix + name, kind, data[name])


def check(command, data):
    """Raise an input error naming the first missing or malformed field; return the action."""
    if not isinstance(data, dict):
        raise RelayError("input", f"{command}: input — expected a JSON object")
    key, actions = COMMANDS[command]
    action = None
    if key:
        allowed = tuple(actions)
        if key not in data:
            fail(command, None, key, "one of " + ", ".join(allowed), missing=True)
        if data[key] not in allowed:
            fail(command, None, key, "one of " + ", ".join(allowed))
        action = data[key]
    walk(command, action, "", actions[action], data)
    return action


def missing_field(command, data, exc):
    """The last-line message for KeyError/TypeError/AttributeError that escaped a handler."""
    key, _ = COMMANDS.get(command, (None, None))
    action = data.get(key) if key and isinstance(data, dict) and isinstance(data.get(key), str) else None
    if isinstance(exc, KeyError) and exc.args:
        return f"{label(command, action)}: input field {exc.args[0]} is missing or has the wrong type"
    return f"{label(command, action)}: an input field is missing or has the wrong type ({type(exc).__name__})"
