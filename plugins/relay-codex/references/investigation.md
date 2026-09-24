# Investigation recording contract

## Entry and storage

An investigation preserves evidence without adopting a fix. Its result is reviewed before one issue comment is created or revised. Hosts execute experiments; the helper only validates and records submitted observations. Start with `inspect` stage `investigate`, raw positive issue number followed by optional `--lang` and context. Use `work_id` returned by inspect; `investigations` contains remote records and state_summary.investigations summarizes local runs. Several local runs set investigation_selection_required; inspect again with an exact investigation_id. A cold recover uses the metadata's original work_id, never an arbitrary replacement ID.

Run `python <plugin>/scripts/relay.py investigate --work <work-id> --input <absolute.json>` in the target repository. The response is exactly ok/result or ok/error/message. New investigation IDs and checkpoint event IDs are lowercase 32-character UUID hex. Caller-generated client_request_id/event_id identify logical operations and must be reused on retry. start, checkpoint, resume and recover answer with revision, status, outcome, changed field names, counts and `files`; the full run is `files.investigation`.

## Start and checkpoints

`start` requires operation, client_request_id, execution_authorized:true, request, baseline. request has nonempty text symptom, expected, impact, scope, limits and a nonempty list stop_conditions. Unknown facts are explicitly marked unknown with the needed information. baseline has an absolute cwd, files (repository-relative file paths), and environment (only selected safe string keys/values). After workspace ensure, cwd is the returned issue workspace path; the helper rejects another checkout and records the workspace generation with the baseline. The helper reads actual HEAD and file/deletion/symlink signatures. An existing run requires resume; new_investigation:true represents a user's explicit separate-investigation request.

Same request/hash returns its surviving run; different content conflicts. `checkpoint` requires investigation_id, event_id, expected_revision (integer), changes. `changes` replaces submitted evidence lists as a complete current view; previous views survive in append-only checkpoint files. Allowed fields: observations, hypotheses, experiments, facts, excluded_causes, uncertainties, evidence, changes, outcome, conclusion, failure. Retain evidence IDs that remain relevant; revisions of a hypothesis preserve its earlier checkpoint.

All observation/hypothesis/experiment/fact/exclusion items have a unique nonempty id and summary. Facts and exclusions have nonempty supports referencing observations or performed experiments. Exclusions also have conditions. Experiments have hypothesis ID, input, command, expected, limitations, and status planned/performed. Performed experiments additionally have actual, integer exit_code, sha and selected environment. Helpers cannot prove causal sufficiency from these fields: the host must explain and review the evidence. A failed experiment may be valid evidence of the symptom; an unperformed experiment cannot support a cause claim.

outcome is null/resolved/held/no_change. Null means investigating. Resolved and no_change mean concluded; held means held. conclusion has summary, impact and a common next_step object; see [next step](next-step.md). Resolved additionally requires cause and supports including performed verification. Held requires nonempty needed and resume lists. No_change requires stop_reason. uncertainties is a list of text; failure is separate from the investigation outcome. Never relabel a helper error no_change.

Retry an event with its exact event_id; do not start a different event to bypass an unfinished event. active_investigation is separate from active_run.

## Evidence and isolated edits

Preserve files under investigations/investigation_id/evidence. `evidence` entries provide relative path and description. Files must be available regular files without escaping links. `changes` entries provide worktree, path, signature {path,kind,hash}, purpose, origin, candidate boolean, and patch (an evidence path). The worktree must be a different connected Git worktree from the original baseline cwd. The helper verifies current signatures and preserved patch membership; it does not create, clean or commit worktrees. The host records source dirty/untracked hashes before selective copying and verifies them afterward.

Record the first changed-worktree checkpoint from the original state-owning cwd; subsequent helper calls can use that connected worktree. Recovery never recreates missing worktrees.

`resume` requires investigation_id, event_id, expected_revision, reason and baseline with current cwd/files/environment. It journals the prior conclusion and records current/needs_reassessment/unavailable applicability, then returns to investigating. Compare both HEAD and scope; an outside-scope HEAD change still needs an explanation. After a workspace refresh, resume from the new workspace path; the changed generation makes earlier observations needs_reassessment. Evidence deletion is unavailable, modified evidence is needs_reassessment. The original performed SHA/environment remains historical. A matching environment hash proves equality only for submitted keys. Keep original evidence; re-run experiments as new checkpoint evidence when conditions change.

After reassessing changed conditions, a checkpoint may include assessment:{reason,baseline}. Explain which old evidence remains applicable and which new experiments replace it. The helper journals the old baseline and records actual current signatures/environment; experiment history remains unchanged. Missing or modified preserved evidence still prevents a current applicability result.

## Review, publication and recovery

`prepare` requires investigation_id, expected_revision and body_file inside that investigation directory. Write detailed results first, including revision summary, observed experiment results before commands, limitations, and preserved changes. With a knowledge base present the body also needs the `## 참조한 KB 항목` section of [kb](kb.md). Remove placeholders. The helper generates conclusion/impact/next step, core facts and uncertainties from the same state as the recovery JSON, and freezes the full rendered body including details. prepare requires a next_step equal to the conclusion's after the [common policy](next-step.md); correct the conclusion first when they differ. Return review/diff paths, request_id, hash and version. Inspect the full candidate; the proposed follow-up is not a second executable recommendation.

`publish` requires investigation_id, request_id, hash, approved:true and the actual approving user. With `--watch` in the inspected invocation the recorded result also carries a `watch` object. Exact approval is separate from the investigation invocation. A changed revision, candidate or review invalidates approval. publication is none/review/uncertain/recorded, independent of outcome; publishing is accepted as an interrupted uncertain state. Before a remote write persist uncertain and authorization. Match request IDs across all comment pages; after a response loss retry the same authorization. An absent ambiguous creation is not replayed. Record numeric target before read-back. Same run revisions retain URL and increase version; external managed edits, deletion/replacement, duplicate execution/request or read-back mismatch are errors. Outer unmanaged text is retained. Oversized candidates require a shorter reviewed replacement, never splitting.

`recover` requires exact investigation_id and numeric target. Validate issue/repository/metadata/visible conclusion and restore only into the original work ID; existing local state is not overwritten. Missing evidence/workspace remains unavailable. Recovered summaries do not reproduce experiments.

## Follow-up evidence and the next step

Choose next using the [common next-step contract](next-step.md). Checkpoint and prepare retain their outcome constraints and conclusion/candidate consistency checks; recovery preserves historical summaries without applying new-candidate normalization.

Later documents and implementations cite published results as [evidence](evidence.md) describes.
