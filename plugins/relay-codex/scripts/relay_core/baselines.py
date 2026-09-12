"""Shared selection and validation of approved execution document references."""
import copy
import re
from . import RelayError
from . import next_step as steps
from .artifacts import reference, digest

SETS = {"formal": ["intent", "spec", "plan"], "brief": ["brief"]}


def from_records(records):
    return {name: {"reference": reference(record), "parents": record["meta"]["parents"],
                   "stale": record["stale"], "body": record["body"]}
            for name, record in records.items()}


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


def for_run(run, requested=None):
    basis = run.get("basis")
    if basis is None:
        parents = run.get("parents", {})
        if set(parents) != set(SETS["formal"]) or not isinstance(run.get("plan_summary"), str) or not run["plan_summary"]:
            raise RelayError("run", "Only current formal runs with a pinned plan can omit basis.")
        basis = {"kind": "formal", "parents": parents, "proof": parents["plan"]}
    basis = validate_basis(basis)
    if basis["parents"] != run.get("parents"):
        raise RelayError("run", "Run parents differ from execution basis.")
    if "basis" in run:
        if not isinstance(run.get("basis_summary"), str) or not run["basis_summary"]:
            raise RelayError("run", "Execution basis summary is missing.")
        if basis["kind"] == "formal" and run.get("plan_summary") != run["basis_summary"]:
            raise RelayError("run", "Formal summary differs from the pinned plan.")
        if basis["kind"] == "brief" and "plan_summary" in run:
            raise RelayError("run", "Brief evidence must not masquerade as a plan.")
    if requested is not None and requested != basis["kind"]:
        raise RelayError("run", "Requested basis differs from the registered execution.")
    summary = run.get("basis_summary") if "basis" in run else run["plan_summary"]
    if basis["proof"]["hash"] not in proof_hashes(summary, run.get("basis_next_step")):
        raise RelayError("run", "Pinned summary hash differs from execution proof.")
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
