"""Parse only the structured prefix; preserve natural-language input verbatim."""
import re
from . import RelayError


def token_at(text, start):
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    begin = i
    quote, out = None, []
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                quote = None
            else:
                out.append(char)
        elif char in "\"'":
            quote = char
        elif char.isspace():
            break
        else:
            out.append(char)
        i += 1
    if quote:
        raise RelayError("input", "Unclosed quote in an option value.")
    return "".join(out), begin, i


def parse(stage, raw, registry):
    if stage not in registry:
        raise RelayError("input", "Unknown skill.")
    rule = registry[stage]
    first = re.match(r"\s*(\S+)", raw)
    issue, pos = None, 0
    if first and re.fullmatch(r"[1-9][0-9]*", first[1]):
        if rule.get("issue") == "forbidden":
            raise RelayError("input", "open creates a new issue and does not accept an issue number. Ask what the user intended before creating anything or switching skills.")
        issue, pos = int(first[1]), first.end()
    elif rule.get("issue") == "required":
        raise RelayError("input", "Put a positive issue number immediately after the skill name.")
    elif stage == "review" and first and (re.match(r"[-+0-9]", first[1]) and not first[1].startswith("--") or "://" in first[1]):
        raise RelayError("input", "Use a positive PR number, not a URL or invalid number.")
    options = {}
    while True:
        m = re.match(r"\s*", raw[pos:])
        start = pos + m.end()
        if not raw[start:].startswith("--"):
            if stage == "review":
                if options.get("reviewer") and options.get("author"):
                    raise RelayError("input", "--reviewer and --author are mutually exclusive.")
                return {"stage": stage, "pr": issue, "mode": "author" if options.get("author") else "reviewer",
                        "options": options, "description": raw[start:]}
            return {"stage": stage, "issue": issue, "options": options, "description": raw[start:]}
        token, _, pos = token_at(raw, start)
        name, eq, value = token[2:].partition("=")
        mode = rule["options"].get(name)
        if mode is None:
            raise RelayError("input", "Unknown or inapplicable option: --" + name)
        if mode == "flag":
            if eq:
                raise RelayError("input", "Mode flags do not take values.")
            value = True
        elif mode == "optional-equals":
            if eq and not value:
                raise RelayError("input", "Empty worktree path.")
            value = value if eq else True
        elif not eq:
            value, _, pos = token_at(raw, pos)
        if value == "" or (isinstance(value, str) and value.startswith("--")):
            raise RelayError("input", "Missing value for --" + name)
        if name in options and options[name] != value:
            raise RelayError("input", "Conflicting duplicate option: --" + name)
        options[name] = value
