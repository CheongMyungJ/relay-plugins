import hashlib
import json
import re
from . import RelayError
from . import next_step as steps

BEGIN = "<!-- relay:begin -->"
END = "<!-- relay:end -->"
META = re.compile(r"\n?<!-- relay:metadata (.*?) -->", re.DOTALL)
DOCUMENTS = {"intent", "spec", "plan", "brief", "implementation"}
KNOWN = DOCUMENTS | {"issue", "investigation"}


def normalize(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def digest(text):
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def split(text):
    text = normalize(text)
    if BEGIN not in text and END not in text:
        return "", text, ""
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise RelayError("artifact", "Malformed or duplicate Relay management region.")
    before, _, rest = text.partition(BEGIN)
    body, _, after = rest.partition(END)
    if END in before or BEGIN in after:
        raise RelayError("artifact", "Invalid management region order.")
    return before, body, after


def mentions_request(text, request):
    """Conservatively identify a request before validating an issue's framing."""
    if not isinstance(text, str):
        return False
    if request in text:
        return True
    for raw in META.findall(text):
        try:
            metadata = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(metadata, dict) and metadata.get("request_id") == request:
            return True
    return False


def decode(text, target=None, *, request_id=None):
    """Decode a record, optionally validating only an identifiable publication request."""
    before, region, after = split(text)
    matches = META.findall(region)
    if not matches:
        if "<!-- relay:metadata" in region:
            raise RelayError("artifact", "Malformed Relay metadata.")
        return None
    if len(matches) != 1:
        raise RelayError("artifact", "Multiple metadata records in one artifact.")
    try:
        metadata = json.loads(matches[0])
        if metadata.get("kind") not in KNOWN:
            return None
        if request_id is not None:
            identity = metadata.get("request_id")
            if not isinstance(identity, str) or not identity:
                raise ValueError("missing or invalid request_id")
            if identity != request_id:
                return None
        if metadata["schema"] != 1:
            raise ValueError("unsupported schema")
        content = META.sub("", region)
        if digest(content) != metadata["hash"]:
            raise ValueError("content hash mismatch")
        # relay:next lives inside the hashed content, so a changed suggestion
        # invalidates the earlier approval like any other body change. The hash is
        # already verified here, so detach can read the anchored tail and leave any
        # prose that merely quotes the syntax in the readable body.
        body, status, suggestion = steps.detach(content)
        for field in ("work_id", "request_id", "parents", "version"):
            if field not in metadata:
                raise ValueError("missing " + field)
        if not isinstance(metadata["version"], int) or metadata["version"] < 1:
            raise ValueError("invalid version")
        if not isinstance(metadata["parents"], dict):
            raise ValueError("invalid parents")
        if metadata["kind"] in ("intent", "brief", "issue") and metadata["parents"]:
            raise ValueError("intent, brief and issue cannot have document parents")
        if metadata["kind"] == "issue" and metadata["version"] != 1:
            raise ValueError("issue creation version must be 1")
        if metadata["kind"] == "investigation":
            if metadata["parents"] or not re.fullmatch(r"[a-f0-9]{32}", str(metadata.get("run_id", ""))):
                raise ValueError("investigation requires an execution UUID and no parents")
            if type(metadata["version"]) is not int:
                raise ValueError("invalid investigation version")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RelayError("artifact", "Invalid metadata: " + str(exc)) from exc
    # body is the readable document; content is the exact hashed text the metadata covers.
    return {"body": body, "content": content, "meta": metadata, "target": target, "raw_hash": digest(text),
            "before": before, "after": after, "next_step": suggestion, "next_step_status": status}


def render(body, metadata, previous="", adopt=False, *, next_step):
    body = normalize(body)
    if any(mark in body for mark in (BEGIN, END, "<!-- relay:metadata")):
        raise RelayError("artifact", "Candidate body must not contain generated Relay markers.")
    steps.reserved(body)
    content = body + "\n" + steps.encode(next_step)
    meta = dict(metadata, schema=1, hash=digest(content))
    region = BEGIN + content + "\n<!-- relay:metadata " + json.dumps(meta, ensure_ascii=False, sort_keys=True) + " -->" + END
    if not previous:
        return region
    before, _, after = split(previous)
    if BEGIN not in previous:
        if not adopt:
            raise RelayError("adoption", "Review the complete replacement and explicitly approve adoption.")
        return region
    return before + region + after


def reference(record):
    meta = record["meta"]
    return {"target": record["target"], "version": meta["version"], "hash": meta["hash"]}


def collect(issue, comments, legacy=None):
    records, runs = {}, {}
    # The issue body is source material, never a document dependency or legacy binding.
    for target, text in [(str(c["id"]), c["body"]) for c in comments]:
        record = decode(text, target)
        if not record:
            binding = (legacy or {}).get(target)
            if binding:
                if digest(text) != binding["hash"]:
                    raise RelayError("stale", "Legacy baseline changed; confirm the current document.")
                record = {"body": text, "content": text, "target": target, "raw_hash": digest(text),
                          "meta": dict(binding, parents=binding.get("parents", {}), schema=0),
                          "next_step": None, "next_step_status": steps.UNREADABLE}
            else:
                continue
        kind = record["meta"]["kind"]
        if kind == "investigation":
            continue
        if kind not in KNOWN:
            continue
        if kind == "issue" or not target.isdigit():
            raise RelayError("artifact", "Artifact is in the wrong GitHub location.")
        if kind in ("intent", "brief") and record["meta"]["parents"]:
            raise RelayError("artifact", "Intent and brief cannot have document parents.")
        dest, key = (runs, record["meta"].get("run_id")) if kind == "implementation" else (records, kind)
        if key is None or key in dest:
            raise RelayError("artifact", "Duplicate artifact or missing run ID.")
        dest[key] = record
    for record in records.values():
        record["stale"] = any(name not in records or reference(records[name]) != ref
                              for name, ref in record["meta"]["parents"].items())
    # Transitive staleness matters even when a direct parent's own body has not changed.
    for _ in records:
        for record in records.values():
            record["stale"] |= any(records.get(name, {}).get("stale", False)
                                   for name in record["meta"]["parents"])
    return records, runs


def collect_investigations(comments):
    records = {}
    for item in comments:
        target = str(item["id"])
        record = decode(item["body"], target)
        if not record or record["meta"]["kind"] != "investigation":
            continue
        key = record["meta"]["run_id"]
        if not target.isdigit() or key in records:
            raise RelayError("artifact", "Duplicate investigation or invalid comment target.")
        records[key] = record
    return records
