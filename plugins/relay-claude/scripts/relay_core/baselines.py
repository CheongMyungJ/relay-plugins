"""Shared selection and validation of approved execution document references."""
import copy
import re
from . import RelayError
from . import next_step as steps
from .artifacts import reference, digest

SETS = {"formal": ["intent", "spec", "plan"], "brief": ["brief"]}


def from_records(records):
    return {name: {"reference": reference(record), "parents": record["meta"]["parents"],
                   "stale": record["stale"], "body": record["body"], "workspace": record["meta"].get("workspace")}
            for name, record in records.items()}


def assessed(basis, documents, workspace):
    """After a refresh, each basis document was written in the active generation or assessed there by exact reference.

    The first generation accepts documents without workspace metadata; their code freshness stays unknown.
    """
    number = workspace["active_generation"]
    if number == 1:
        return basis
    evaluations = [e["documents"] for e in workspace.get("evaluations", [])
                   if e["workspace_id"] == workspace["workspace_id"] and e["generation"] == number]
    missing = []
    for name, ref in basis["parents"].items():
        written = (documents.get(name) or {}).get("workspace") or {}
        if (written.get("workspace_id"), written.get("generation")) == (workspace["workspace_id"], number):
            continue
        if not any(documents_ref.get(name) == ref for documents_ref in evaluations):
            missing.append(name)
    if missing:
        raise RelayError("stale", "Assess these documents against the refreshed workspace before implementing: " + ", ".join(missing))
    return basis


def proof_hashes(summary, suggestion):
    """Rebuild the proof document's hashed content from its readable text.

    The pinned summary stays readable, so an execution report can quote it without
    repeating the generated comment. The two reconstructions are exactly the two
    shapes detach can leave behind: it cuts the readable block with the comment when
    they are adjacent, and only the comment when they are not. A proof published
    before relay:next existed has no suggestion, and its content is the text alone.
    """
    if suggestion is None:
        return (digest(summary),)
    return (digest(summary + "\n\n" + steps.rendered(suggestion)),
            digest(summary + "\n" + steps.encode(suggestion)))


def validate_basis(basis):
    if not isinstance(basis, dict) or set(basis) != {"kind", "parents", "proof"}:
        raise RelayError("run", "Execution basis must contain kind, parents and proof.")
    kind = basis["kind"]
    if not isinstance(kind, str) or kind not in SETS:
        raise RelayError("input", "basis must be formal or brief.")
    parents = basis["parents"]
    if not isinstance(parents, dict) or set(parents) != set(SETS[kind]):
        raise RelayError("run", "Execution basis has an inconsistent parent set.")
    for ref in [*parents.values(), basis["proof"]]:
        if (not isinstance(ref, dict) or set(ref) != {"target", "version", "hash"}
                or not isinstance(ref["target"], str) or not ref["target"].isdigit()
                or type(ref["version"]) is not int or ref["version"] < 1
                or not isinstance(ref["hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", ref["hash"])):
            raise RelayError("run", "Invalid execution document reference.")
    if basis["proof"] != parents["plan" if kind == "formal" else "brief"]:
        raise RelayError("run", "Execution proof differs from the selected basis.")
    return copy.deepcopy(basis)


# The pinned proof text stays in the local run; the proof comment already publishes it.
PRIVATE = ("basis_summary", "plan_summary")


def published(run):
    """The execution evidence a report publishes: the run without its pinned proof text."""
    return {key: copy.deepcopy(value) for key, value in run.items() if key not in PRIVATE}


def for_run(run, requested=None, *, published=False):
    """Validate a run's pinned basis. Published evidence may omit the pinned text; text that is present must match."""
    if run.get("basis") is None:
        raise RelayError("run", "Execution evidence without a pinned basis is no longer supported.")
    basis = validate_basis(run["basis"])
    if basis["parents"] != run.get("parents"):
        raise RelayError("run", "Run parents differ from execution basis.")
    if "plan_summary" in run:
        # Formal runs begun before the pinned text left the evidence still carry this copy.
        if basis["kind"] == "brief":
            raise RelayError("run", "Brief evidence must not masquerade as a plan.")
        if run["plan_summary"] != run.get("basis_summary"):
            raise RelayError("run", "Formal summary differs from the pinned plan.")
    if not (published and "basis_summary" not in run):
        if not isinstance(run.get("basis_summary"), str) or not run["basis_summary"]:
            raise RelayError("run", "Execution basis summary is missing.")
        if basis["proof"]["hash"] not in proof_hashes(run["basis_summary"], run.get("basis_next_step")):
            raise RelayError("run", "Pinned summary hash differs from execution proof.")
    if requested is not None and requested != basis["kind"]:
        raise RelayError("run", "Requested basis differs from the registered execution.")
    return basis


def current(basis, documents):
    basis = validate_basis(basis)
    for name, ref in basis["parents"].items():
        doc = documents.get(name)
        if not doc or doc["stale"] or doc["reference"] != ref:
            raise RelayError("stale", "Execution baseline changed or is missing: " + name)
        expected = {parent: basis["parents"][parent] for parent in
                    {"intent": [], "brief": [], "spec": ["intent"], "plan": ["intent", "spec"]}[name]}
        if doc["parents"] != expected:
            raise RelayError("stale", "Execution document parents are inconsistent: " + name)
    return basis


def select(documents, requested=None, run=None):
    if requested is not None and (not isinstance(requested, str) or requested not in SETS):
        raise RelayError("input", "basis must be formal or brief.")
    if run is not None:
        return current(for_run(run, requested), documents)
    if requested is None:
        formal, brief = any(name in documents for name in SETS["formal"]), "brief" in documents
        # Intent/spec can provide context for a brief without forming a second
        # execution basis. A present plan still needs a choice, even if stale.
        if "plan" in documents and brief:
            raise RelayError("run", "Both paths exist; explicitly select --basis formal or --basis brief.")
        if not formal and not brief:
            raise RelayError("stale", "No recorded execution basis exists.")
        requested = "brief" if brief else "formal"
    parents = {}
    for name in SETS[requested]:
        if name not in documents or documents[name]["stale"]:
            raise RelayError("stale", "A recorded, current " + name + " is required.")
        parents[name] = documents[name]["reference"]
    return current({"kind": requested, "parents": parents,
                    "proof": parents["plan" if requested == "formal" else "brief"]}, documents)
