"""Server execution context for the helper.

relay-server's session host sets RELAY_SERVER_CONTEXT (execution ID and a helper capability)
for the CLI it runs. Only then does the helper ask the host to record an approval before a
remote write, and report a verified publication after read-back. Without the variable every
function returns None and the helper behaves exactly as a standalone one.

The host decides who approved from its own controller record; nothing about the web user
travels through the helper. Frames follow session-host/PROTOCOL.md (IPC v1).
"""
import json
import os
import re
import struct
import sys
import threading

from . import RelayError

ENV = "RELAY_SERVER_CONTEXT"
SERVER_USER = "relay-server"
PROTOCOL = 1
TIMEOUT = 15.0
MAX_HEADER = 64 * 1024
MAX_PAYLOAD = 1024 * 1024
PREFIX = struct.Struct("<II")
REASONS = {
    "no_controller": "no web terminal holds input for this session; reconnect the terminal and confirm the approval again",
    "terminated": "the server session is ending and accepts no new approval",
    "user": "server sessions record approvals as user " + SERVER_USER,
    "journal": "the session host could not store the approval",
}


def active():
    """The validated server context, or None for a standalone helper."""
    raw = os.environ.get(ENV)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        raise RelayError("approval", ENV + " is not valid JSON; no remote write was made.") from None
    execution = value.get("execution_id") if isinstance(value, dict) else None
    capability = value.get("capability") if isinstance(value, dict) else None
    if (not isinstance(execution, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", execution)
            or not isinstance(capability, str) or not capability):
        raise RelayError("approval", ENV + " lacks a valid execution ID and capability; no remote write was made.")
    return {"execution_id": execution, "capability": capability}


def pipe_name(execution_id):
    return r"\\.\pipe\relay-session-" + execution_id


def encode(header):
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_HEADER:
        raise RelayError("approval", "server context frame too large")
    return PREFIX.pack(len(raw), 0) + raw


def read_exact(stream, size):
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise RelayError("approval", "the session host closed the connection")
        data += chunk
    return data


def receive(stream, request_id):
    """The reply to request_id; unrelated frames are skipped."""
    while True:
        header_len, payload_len = PREFIX.unpack(read_exact(stream, PREFIX.size))
        if not header_len or header_len > MAX_HEADER or payload_len > MAX_PAYLOAD:
            raise RelayError("approval", "the session host sent an invalid frame")
        header = json.loads(read_exact(stream, header_len).decode("utf-8"))
        if payload_len:
            read_exact(stream, payload_len)
        if isinstance(header, dict) and header.get("re") == request_id:
            return header


def exchange(context, request):
    """hello, then one request, on a fresh connection; returns the reply header."""
    if sys.platform != "win32":
        raise RelayError("approval", "the server context needs the Windows session host")
    outcome = {}

    def talk():
        try:
            with open(pipe_name(context["execution_id"]), "r+b", buffering=0) as pipe:
                pipe.write(encode({"type": "hello", "id": "hello", "version": PROTOCOL, "role": "helper",
                                   "execution_id": context["execution_id"], "capability": context["capability"]}))
                hello = receive(pipe, "hello")
                if hello.get("type") != "hello_ok" or hello.get("execution_id") != context["execution_id"]:
                    raise RelayError("approval", "the session host refused the helper: " + str(hello.get("code")))
                pipe.write(encode(dict(request, id="request")))
                outcome["reply"] = receive(pipe, "request")
        except RelayError as exc:
            outcome["error"] = exc
        except (OSError, ValueError) as exc:
            outcome["error"] = RelayError("approval", f"the session host is unavailable: {exc}")

    worker = threading.Thread(target=talk, daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    if worker.is_alive():
        raise RelayError("approval", "the session host did not answer")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["reply"]


def authorize(kind, request_id, digest, *, user=None, execution_authorized=False, operation=None, run_id=None):
    """Record the approval the helper is about to act on; raises before any remote write when refused.

    Human approvals (documents, held reports, investigations, reviews, KB results) must carry the
    fixed user and need a web terminal holding input. Execution-authorized effects (completed
    reports, PR operations) are recorded as such and never shown as a person's approval. A
    repeated request for the same candidate returns the first record.
    """
    context = active()
    if context is None:
        return None
    if (user is not None or not execution_authorized) and user != SERVER_USER:
        raise RelayError("approval", f"Server sessions record approvals as user {SERVER_USER}; no remote write was made.")
    reply = exchange(context, {"type": "authorization_record", "kind": kind, "request_id": request_id, "hash": digest,
                               "execution_authorized": bool(execution_authorized), "user": SERVER_USER,
                               "operation": operation, "run_id": run_id})
    if reply.get("type") == "error":
        raise RelayError("approval", f"The session host rejected the approval ({reply.get('code')}); no remote write was made.")
    if reply.get("accepted") is not True:
        reason = reply.get("reason") or "unknown"
        raise RelayError("approval", f"Server approval not recorded: {REASONS.get(reason, reason)}; no remote write was made.")
    return reply.get("record")


def publication(event):
    """Report a verified remote result. The publication already succeeded, so a failure is returned, not raised."""
    context = active()
    if context is None:
        return None
    try:
        reply = exchange(context, {"type": "publication_event", "event": event})
    except RelayError as exc:
        return {"recorded": False, "error": str(exc)}
    if reply.get("type") != "publication":
        return {"recorded": False, "error": f"{reply.get('code')}: {reply.get('message')}"}
    record = reply.get("record") or {}
    return {"recorded": True, "seq": record.get("seq"), "reused": bool(reply.get("reused"))}
