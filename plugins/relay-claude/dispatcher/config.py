"""User configuration: validation, mtime-based rereads, per-repository overrides."""
import copy
import os
import unicodedata
from pathlib import Path

from relay_core import RelayError, repository
from relay_core.state import read_json, write_json

HOSTS = ("claude", "codex", "opencode")
LAUNCHERS = ("wt", "tmux", "dry-run")
DEFAULT_AUTO = ["intent", "design", "plan", "brief", "investigate", "implement", "pr", "review"]
DEFAULTS = {"poll_seconds": 30, "launcher": "wt", "paused": False,
            "auto": DEFAULT_AUTO, "gated": [], "defaults": {"lang": "ko"},
            "worktree_template": "{parent}/{name}-wt-{issue}", "repos": []}
OVERRIDES = ("host", "auto", "gated", "defaults", "worktree_template")
SESSION_KEYS = {"host", "model", "skills"}
PLACEHOLDERS = ("{parent}", "{name}", "{issue}")


def home():
    return Path(os.environ.get("RELAY_DISPATCH_HOME") or Path.home() / ".relay-dispatch").resolve()


def path():
    return home() / "config.json"


def option_value(value, name):
    """An option value that can travel inside a prompt: no whitespace, quotes or leading --."""
    if not isinstance(value, str) or not value or value.startswith("--"):
        raise RelayError("input", f"defaults.{name} must be plain text that does not start with --")
    if any(c.isspace() or c in "\"'" for c in value):
        raise RelayError("input", f"defaults.{name} must not contain whitespace or quotes")
    return value


def validate_stages(value, label, registry):
    if not isinstance(value, list) or any(not isinstance(s, str) for s in value):
        raise RelayError("input", label + " must be a list of skill names")
    unknown = [s for s in value if s not in registry]
    if unknown:
        raise RelayError("input", label + " names unknown skills: " + ", ".join(unknown))
    return list(dict.fromkeys(value))


def validate_session(scope, label, registry):
    if "host" in scope and scope["host"] not in HOSTS:
        raise RelayError("input", label + ".host must be one of " + ", ".join(HOSTS))
    if "model" in scope and scope["model"] is not None:
        value = scope["model"]
        if (not isinstance(value, str) or not value or value.startswith("-")
                or any(c.isspace() or c in "\"'" or unicodedata.category(c).startswith("C") for c in value)):
            raise RelayError("input", label + ".model must be nonempty text without whitespace, quotes, control characters or a leading -")
    if "skills" in scope:
        if not isinstance(scope["skills"], dict):
            raise RelayError("input", label + ".skills must be an object")
        for stage, values in scope["skills"].items():
            field = label + ".skills." + str(stage)
            if stage not in registry:
                raise RelayError("input", field + " is an unknown skill")
            if not isinstance(values, dict):
                raise RelayError("input", field + " must be an object")
            extra = set(values) - {"host", "model"}
            if extra:
                raise RelayError("input", field + " has unknown keys: " + ", ".join(sorted(extra)))
            validate_session(values, field, registry)


def validate_scope(scope, label, registry, explicit=None):
    """Validate the fields shared by the top level and repository entries.

    `explicit` holds the keys the user actually wrote; an overlap between auto and
    gated is an error only when both were written, otherwise the gate simply wins.
    """
    explicit = scope if explicit is None else explicit
    validate_session(scope, label, registry)
    for key in ("auto", "gated"):
        if key in scope:
            scope[key] = validate_stages(scope[key], label + "." + key, registry)
    if "auto" in explicit and "gated" in explicit and set(scope["auto"]) & set(scope["gated"]):
        raise RelayError("input", label + ": a stage cannot be both auto and gated")
    if "defaults" in scope:
        if not isinstance(scope["defaults"], dict):
            raise RelayError("input", label + ".defaults must be an object")
        for name, value in scope["defaults"].items():
            if name != "lang":
                raise RelayError("input", label + ".defaults supports only lang")
            option_value(value, name)
    if "worktree_template" in scope:
        template = scope["worktree_template"]
        if not isinstance(template, str) or not template.strip():
            raise RelayError("input", label + ".worktree_template must be a nonempty string")
        stripped = template
        for placeholder in PLACEHOLDERS:
            stripped = stripped.replace(placeholder, "")
        if "{" in stripped or "}" in stripped:
            raise RelayError("input", label + ".worktree_template allows only {parent}, {name} and {issue}")
        if "{issue}" not in template:
            raise RelayError("input", label + ".worktree_template must contain {issue}")
    return scope


def validate(config, registry):
    if not isinstance(config, dict):
        raise RelayError("input", "config must be a JSON object")
    merged = copy.deepcopy(DEFAULTS)
    merged.update(copy.deepcopy(config))
    unknown = set(merged) - set(DEFAULTS) - SESSION_KEYS
    if unknown:
        raise RelayError("input", "unknown config keys: " + ", ".join(sorted(unknown)))
    if type(merged["poll_seconds"]) is not int or merged["poll_seconds"] < 10:
        raise RelayError("input", "poll_seconds must be an integer of at least 10")
    if merged["launcher"] not in LAUNCHERS:
        raise RelayError("input", "launcher must be one of " + ", ".join(LAUNCHERS))
    if type(merged["paused"]) is not bool:
        raise RelayError("input", "paused must be true or false")
    validate_scope(merged, "config", registry, explicit=config)
    if not isinstance(merged["repos"], list):
        raise RelayError("input", "repos must be a list")
    for index, entry in enumerate(merged["repos"]):
        label = f"repos[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"].strip():
            raise RelayError("input", label + " needs a path string")
        extra = set(entry) - set(OVERRIDES) - SESSION_KEYS - {"path"}
        if extra:
            raise RelayError("input", label + " has unknown keys: " + ", ".join(sorted(extra)))
        validate_scope(entry, label, registry)
    return merged


def settings(config, entry):
    """Effective host/auto/gated/defaults/template for one repository entry."""
    result = {key: copy.deepcopy(config[key]) for key in OVERRIDES if key in config}
    result.setdefault("host", "claude")
    for key in OVERRIDES:
        if key in entry:
            result[key] = copy.deepcopy(entry[key])
    # A repository's gate list always wins over the auto list it inherits.
    result["auto"] = [s for s in result["auto"] if s not in result["gated"]]
    result["path"] = entry["path"]
    return result


def session_settings(config, entry, stage):
    """Select fields independently, retaining the model's host until the final comparison."""
    host, model = "claude", None
    selection = {"host_scope": "default", "model_scope": None, "requested_model": None,
                 "model_host": None, "model_state": "unspecified"}
    layers = (("global", config), ("repo", entry),
              ("global-skill", config.get("skills", {}).get(stage, {})),
              ("repo-skill", entry.get("skills", {}).get(stage, {})))
    for scope, values in layers:
        if "host" in values:
            host = values["host"]
            selection["host_scope"] = scope
        if "model" in values:
            model = values["model"]
            selection.update(model_scope=scope, requested_model=model, model_host=host,
                             model_state="cleared" if model is None else "selected")
    if model is not None and selection["model_host"] != host:
        model = None
        selection["model_state"] = "host-mismatch"
    return {"host": host, "model": model, "selection": selection}


def identity(entry_path):
    """Slug, host and root of the clone at entry_path, derived exactly as Relay does."""
    root = Path(entry_path)
    if not root.is_dir():
        raise RelayError("repository", "clone path does not exist: " + str(entry_path))
    repo = repository.inspect(root)
    return {"slug": repo["repo"], "host": repo["host"], "root": repo["root"], "remote": repo["remote"]}


def slug_key(host, slug):
    return (host + "/" + slug).lower()


class Config:
    """The live configuration `run` polls: reread only when the file's mtime changes."""

    def __init__(self, registry, file=None):
        self.registry = registry
        self.file = Path(file) if file else path()
        self.mtime = None
        self.value = validate({}, registry)
        self.error = None

    def load(self):
        """Return True when the file was (re)read; a parse error keeps the previous value."""
        if not self.file.exists():
            changed = self.mtime is not None
            self.value, self.mtime, self.error = validate({}, self.registry), None, None
            return changed
        stamp = self.file.stat().st_mtime_ns
        if stamp == self.mtime:
            return False
        try:
            self.value = validate(read_json(self.file), self.registry)
            self.error = None
        except (RelayError, ValueError, OSError) as exc:
            self.error = str(exc)
        self.mtime = stamp
        return True


def read(registry, file=None):
    file = Path(file) if file else path()
    return validate(read_json(file) if file.exists() else {}, registry)


def save(config, registry, file=None):
    file = Path(file) if file else path()
    validate(config, registry)
    write_json(file, config)


def add_repo(config, entry_path, registry):
    found = identity(entry_path)
    for existing in config.get("repos", []):
        known = identity(existing["path"])
        if slug_key(known["host"], known["slug"]) == slug_key(found["host"], found["slug"]):
            raise RelayError("conflict", f"{found['slug']} is already registered at {existing['path']}; keep one clone per repository")
    config.setdefault("repos", []).append({"path": str(Path(entry_path).resolve())})
    validate(config, registry)
    return found


def remove_repo(config, selector):
    repos = config.get("repos", [])
    wanted = str(Path(selector).resolve()) if Path(selector).exists() else None
    for index, entry in enumerate(repos):
        if entry["path"] == selector or (wanted and str(Path(entry["path"]).resolve()) == wanted):
            return repos.pop(index)
    for index, entry in enumerate(repos):
        try:
            if identity(entry["path"])["slug"].lower() == selector.lower():
                return repos.pop(index)
        except RelayError:
            continue
    raise RelayError("input", "no registered repository matches " + selector)
