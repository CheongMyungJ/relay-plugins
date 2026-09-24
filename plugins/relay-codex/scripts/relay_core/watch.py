"""Mark an issue or pull request for the local dispatcher: one label, the caller as assignee.

Marking runs after a publication has been verified and never undoes it. Every call
only adds; nothing is removed here. A failure is a result field, not an exception, so
the publication's own record stays intact and a later retry of the same request can
complete just the marking.
"""
from . import RelayError

LABEL = "relay:watch"
COLOR = "0e8a16"
DESCRIPTION = "Relay dispatcher follows this issue or PR"


def ensure_label(gh):
    if gh.label(LABEL) is None:
        gh.create_label({"name": LABEL, "color": COLOR, "description": DESCRIPTION})


def attach_label(gh, number):
    """Ensure the label exists and attach it; relay-server marks a new PR this way without --watch."""
    ensure_label(gh)
    return gh.add_labels(number, [LABEL])


def add_assignees(gh, number, logins):
    """Add logins and return the resulting sorted assignee list; every login must have been accepted."""
    response = gh.add_assignees(number, list(logins))
    assignees = sorted(a.get("login") for a in (response or {}).get("assignees", []) if a.get("login"))
    if not set(logins) <= set(assignees):
        # GitHub silently drops assignees who cannot be assigned; the issue keeps its old list.
        raise RelayError("github_rejected", "GitHub did not accept the assignee; repository access is required.")
    return assignees


def apply(gh, number, login):
    """Ensure the label exists, attach it, and add login as an assignee."""
    attach_label(gh, number)
    assignees = add_assignees(gh, number, [login])
    result = {"label": LABEL, "assignee": login, "assignees": assignees, "warning": None}
    if len(assignees) > 1:
        result["warning"] = "multiple assignees"
    return result


def mark(gh, number, requested, previous=None):
    """Return the watch record for a completed publication, or None when it was not requested.

    A previous record that already applied is returned unchanged, so a retried
    publication does not touch GitHub again; an unapplied one is attempted anew.
    """
    if not requested:
        return None
    if previous and previous.get("applied"):
        return previous
    record = {"requested": True, "applied": False, "label": LABEL, "assignee": None, "assignees": [], "warning": None, "error": None}
    try:
        login = gh.viewer()
        record.update(apply(gh, number, login), applied=True)
    except RelayError as exc:
        record["error"] = exc.code + ": " + str(exc)[:500]
    return record
