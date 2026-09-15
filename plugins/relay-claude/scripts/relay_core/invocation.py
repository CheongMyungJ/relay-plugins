"""Parse only the structured prefix; preserve natural-language input verbatim."""
import re
from . import RelayError

# Options a skill no longer accepts fail with guidance instead of the generic unknown-option error.
REMOVED = {"implement": ("worktree", "base", "branch")}


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


def repository_path(token):
    """A repository-relative path argument: slashes only, no parent, absolute or empty segments."""
    path = token.replace("\\", "/")
    if not path or path.startswith("/") or re.match(r"[A-Za-z]:", path) or path.startswith("--"):
        raise RelayError("input", "Use repository-relative paths, not absolute paths or options: " + token)
    parts = [part for part in path.split("/") if part]
    if not parts or any(part in (".", "..", ".git") for part in parts):
        raise RelayError("input", "Path arguments cannot leave the repository or name .git: " + token)
    return "/".join(parts)


def parse(stage, raw, registry):
    if stage not in registry:
        raise RelayError("input", "Unknown skill.")
    rule = registry[stage]
    numbered = "pr" if rule.get("pr") else "issue"
    first = re.match(r"\s*(\S+)", raw)
    issue, pos = None, 0
    # Path-argument skills take no leading number: every non-option token is a path.
    if first and re.fullmatch(r"[1-9][0-9]*", first[1]) and not rule.get("arguments"):
        if rule.get("issue") == "forbidden":
            raise RelayError("input", "open creates a new issue and does not accept an issue number. Ask what the user intended before creating anything or switching skills.")
        issue, pos = int(first[1]), first.end()
    elif rule.get("issue") == "required":
        raise RelayError("input", "Put a positive issue number immediately after the skill name.")
    elif rule.get("pr") == "required":
        raise RelayError("input", "Put a positive PR number immediately after the skill name.")
    elif rule.get("pr") and first and (re.match(r"[-+0-9]", first[1]) and not first[1].startswith("--") or "://" in first[1]):
        raise RelayError("input", "Use a positive PR number, not a URL or invalid number.")
    options, paths = {}, []
    while True:
        m = re.match(r"\s*", raw[pos:])
        start = pos + m.end()
        if not raw[start:].startswith("--"):
            if rule.get("arguments") == "paths":
                if start >= len(raw):
                    break
                token, _, pos = token_at(raw, start)
                paths.append(repository_path(token))
                continue
            break
        token, _, pos = token_at(raw, start)
        name, eq, value = token[2:].partition("=")
        mode = rule["options"].get(name)
        if mode is None and name in REMOVED.get(stage, ()):
            raise RelayError("input", f"--{name} was removed from {stage}: Relay selects the issue workspace, base commit "
                             f"and branch from the issue number. Invoke again without it, for example `{stage} {issue} --watch`.")
        if mode is None:
            raise RelayError("input", "Unknown or inapplicable option: --" + name)
        if mode == "flag":
            if eq:
                raise RelayError("input", "Mode flags do not take values.")
            value = True
        elif not eq:
            value, _, pos = token_at(raw, pos)
        if value == "" or (isinstance(value, str) and value.startswith("--")):
            raise RelayError("input", "Missing value for --" + name)
        if name in options and options[name] != value:
            raise RelayError("input", "Conflicting duplicate option: --" + name)
        options[name] = value
    if "limit" in options and not re.fullmatch(r"[1-9][0-9]*", options["limit"]):
        raise RelayError("input", "--limit must be a positive integer.")
    if "resume" in options and (paths or "branch" in options):
        raise RelayError("input", "--resume continues a saved execution and cannot combine with paths or --branch.")
    if "handoff" in options and ("resume" not in options or not re.fullmatch(r"[1-9][0-9]*", options["handoff"])):
        raise RelayError("input", "--handoff takes a positive round and continues a run named by --resume.")
    result = {"stage": stage, numbered: issue, "options": options, "description": raw[start:]}
    if stage == "review":
        if options.get("reviewer") and options.get("author"):
            raise RelayError("input", "--reviewer and --author are mutually exclusive.")
        result["mode"] = "author" if options.get("author") else "reviewer"
    if rule.get("arguments") == "paths":
        result["paths"] = paths
        result["description"] = ""
    return result
