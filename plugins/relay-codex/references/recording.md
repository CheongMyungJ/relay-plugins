# Recording protocol and helper API

## Paths and invocation

The plugin root is two directories above the installed skills/<name>/SKILL.md. Use absolute paths to <plugin>/scripts/relay.py and to JSON input files. Run the helper with the target repository as cwd. Write JSON with the host file API, not string interpolation into a shell command. The helper uses Python standard library and authenticated gh.

Create an ignored .relay directory for request files. inspect adds /.relay/ to the repository-local Git exclusion file, preserving existing rules. The plugin does not modify global ignore settings.

```text
python <plugin>/scripts/relay.py inspect --input <absolute-invocation.json>
python <plugin>/scripts/relay.py prepare --work <work-id> --input <absolute-candidate.json>
python <plugin>/scripts/relay.py publish --work <work-id> --input <absolute-authorization.json>
python <plugin>/scripts/relay.py run --work <work-id> --input <absolute-run-event.json>
```

These are internal helper arguments, not additional options users must type in skill calls. JSON results contain exactly ok/result or ok/error/message; nonzero exit is failure.

inspect input:
```json
{"stage":"design","raw":"1 --lang ko 추가 설명","cwd":"/absolute/target/repo"}
```
Use stage IDs open/intent/design/plan/brief/implement even in Codex's relay-* named skills.
Returned work_id/work_path identify .relay/work/<id>. documents contains verified comment bodies, parent references and stale flags. source_issue contains number, url, title and body as reference material only; unmanaged contains comment source material requiring classification. remote_error means no remote baseline has been verified. Multiple local work copies require choosing a returned/on-disk work_id explicitly rather than guessing.

Resume open with the same stage/raw/cwd and explicit work_id, never with an issue number in raw. inspect retains its stored issue number. After creation open permits only publish reconciliation of its frozen request; new prepare or existing-issue modification is rejected.

prepare input:
```json
{"body_file":"/absolute/draft.md","title":"New issue title","adopt":false,
 "next_step":{"next":"plan","reason":"확정된 설계를 실행 계획으로 정리한다."}}
```
title and nonempty body are required for open (artifact issue). For an implementation report also include run_id. next_step is required on every prepare; omission is never filled with null. Choose and inspect its returned value under the [common next-step contract](next-step.md). prepare reads upstream records, freezes version/parents/body and returns request_id, hash, review and diff paths. open includes the title and entire body in both files. Do not edit candidate.json. Modify the draft/title and prepare again if needed.

Document publish authorization:
```json
{"request_id":"returned-id","hash":"returned-hash","approved":true,"user":"actual approving user"}
```
Completed implementation report authorization uses execution_authorized:true and run_id, with matching request_id/hash. Held reports additionally require approved:true and a nonempty user, recording the user's approval of the exact candidate:

```json
{"request_id":"returned-id","hash":"returned-hash","execution_authorized":true,"run_id":"registered-id"}
{"request_id":"returned-id","hash":"returned-hash","execution_authorized":true,"run_id":"registered-id","approved":true,"user":"actual approving user"}
```

prepare classifies implementation candidates complete only for pushed status, an existing path matching verified_tree, and no failure; all others are held. holds is history and is not a completion criterion. A hold event accepts a known condition and nonempty string detail/evidence, makes no Git/GitHub queries and preserves other run facts. Successful verified/committed/pushed clears only failure and retains holds; there is no release action. candidate.json and pending freeze held, hold_summary={status,failure,holds,tree_matches}, and run_hash; the result includes held. Missing failure is null, missing holds is [], and missing path or comparison signature yields tree_matches=false. Held candidates use next=null and label 실행 기록 (보류); a non-null next is an input error before replacing candidate files or pending.

Execution authority alone cannot publish a held candidate: publish returns approval with no write. A new write also requires unchanged run facts, otherwise run error “Execution facts changed after preparation; prepare the report again.” preserves pending. A local legacy candidate missing the new fields must be prepared again before a new write. Reconciliation of the same already successful request happens before that new-write check, including read-back and watch retries. Preserve the original authorization and request after an uncertain write; never change them to evade recovery. See [implementation](implementation.md) for the draft/feedback flow and how an existing user decision supplies this authorization.

With `--watch` in the inspected invocation, publish marks the target after read-back and adds `watch` to its result: `{"requested":true,"applied":bool,"label":"relay:watch","assignee":login,"assignees":[...],"warning":str|null,"error":str|null}`. `applied:false` carries `error`; rerunning publish with the same authorization repeats no write and only retries the marking.

## Legacy records

Without Relay metadata, comment documents are not automatically assumed approved. inspect exposes raw comment body and numeric target ID. The issue body cannot be bound or adopted as intent. After user confirmation of a comment, send inspect the same input plus legacy_confirmed:true and a legacy mapping:
```json
{"legacy_confirmed":true,"legacy":{"123":{"kind":"intent","version":2,"hash":"SHA256-of-exact-normalized-remote-body","parents":{}}}}
```
For spec/plan use exact numeric target IDs, their confirmed versions/hashes and parents mapping. A parent reference is {"target":"numeric-comment-id","version":1,"hash":"body-hash"}. Compute legacy hashes with Python digest from relay_core.artifacts: UTF-8, CRLF/CR to LF, no trimming. Bind parents to the current confirmed comment records. Supply the normal stage/raw/cwd fields as well.

Never rewrite a remote body merely to introduce metadata. On a later approved revision, pass adopt:true to prepare only after reviewing the full replacement, preserving unrelated material in the draft. Changing a bound legacy body invalidates it until reconfirmed.

## Identity

intent, spec, plan and brief each use one exact numeric comment ID. intent and brief parents are {}; spec depends on intent; plan on intent/spec; implementation parents exactly match its pinned basis and use one comment per run_id. Revisions increment version and preserve URL; parent references include target/version/hash and staleness propagates down the chain. Title or body edits require a new reviewed candidate and authorization. Unknown comment kinds are left untouched; malformed known comment metadata and duplicates are errors.

## Failures and resume

After any ambiguous write, rerun publish with the same authorization. It first reads the existing target, or enumerates all issue/comment pages for the request ID. Do not manufacture a new request to evade uncertainty. If a creation remains unconfirmed, inspect GitHub and resolve it with the user; never automatically create again. An update can be retried only against its unchanged expected baseline.

A deleted/forbidden comment is an error, not grounds to create another. A later prepare also rejects a missing or replaced known document. External edits and older retries cannot overwrite newer content. Oversized documents must be shortened and reviewed, not truncated or split automatically.

One local work copy is serialized by lock.json. If the owner process has ended, inspect actual state and remote result before removing that specific stale lock. New sessions recover remote current documents and runs; missing local drafts are not reconstructable.

Common error codes: input, repository, git, github, artifact, adoption, stale, approval, conflict, uncertain, locked, run, verification, length. Preserve files and report the concrete failed step. For authentication/network failure, use host escalation rules; do not log credentials.

Brief uses the same prepare/review/exact-candidate approval/publish protocol, normalization, same-ID revision, external-text preservation and uncertain-request reconciliation. Never bind the issue body as brief. Missing or replaced known documents remain conflicts. Investigation links do not create brief parents. Implementation reports validate basis/parents/proof consistency and preserve historical references when reporting drift or failure.

Document prepare may freeze evidence_refs against published investigation IDs and target/version/hash; publication revalidates them, and they never enter document parents.
