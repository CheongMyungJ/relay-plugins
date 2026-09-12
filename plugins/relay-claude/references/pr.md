# PR protocol

## Input and execution

Run `python <plugin>/scripts/relay.py pr --input <absolute-json-file>` with the target repository as cwd. Resolve plugin two directories above skills/<name>/SKILL.md. Write input JSON with the host file API. Git, Python 3 and authenticated gh with repository read, source push and pull-request write permissions are prerequisites; do not install or authenticate automatically.

The helper returns `ok/result` or a nonzero exit with `ok/error/message`. Input options are watch/branch/base; issue is optional. `--watch` is saved on the request at prepare (`watch: true`; pass `watch` on an explicit update) and, once the PR is recorded or found existing, the result carries a `watch` object with `applied`, `assignees`, `warning` and `error`; a marking failure never undoes the PR, and resume only completes an unapplied marking. First prose token ends option parsing. Keep Windows backslashes and quotes intact. A branch selection supplied after clarification goes in head/base with selection_reason; it is a host-resolved user choice, not inferred permission from issue text.

### inspect

```json
{"action":"inspect","raw":"42 --branch feature/login --base develop","cwd":"/absolute/repo"}
```

Returns source/base/default SHAs, actual merge-base diff, dirty flag, local/remote relation, issue context and execution selection evidence. `existing` returns a read-back verified URL/number/branches without writes. `needs_input` returns candidate evidence, template paths or unfinished request IDs. With no pending request it creates no request.json. Optional `head`, `base`, `selection_reason`, `template` carry resolved user/repository choices. Candidate evidence includes exclusions and stale parents; stale history does not establish current plan completion.

When revising a prepared/ready/failed creation request, pass its request_id and the full current raw invocation/choices to inspect the new diff without changing the saved candidate. Then prepare the revised body under that same ID. Uncertain or pushing requests must be resumed first and cannot use inspection to bypass recovery.

### prepare

```json
{"action":"prepare","raw":"42 --branch feature/login --base develop","cwd":"/absolute/repo","inspection_hash":"hash-from-the-inspect-used-to-write-this-body","title":"Explain the resulting behavior","body_file":"/absolute/body.md","next_step":{"next":"review","reason":"게시한 PR을 리뷰로 검토한다."},"draft":false,"no_push":false,"draft_only":false}
```

Always pass the inspection_hash returned by the successful inspect whose diff and template were used to write this body. It binds repository/remote, issue, head/base/default names and SHAs, and the selected template. A missing or changed hash is rejected before saving a candidate. Read the new diff/template and revise the body before retrying; do not simply substitute a new hash into an old draft.

Pass request_id to revise an existing prepared/failed candidate, plus the full invocation and choices. The host resolves natural-language constraints to booleans; false means absent. Body-draft-only and a draft PR are different. SHA or body changes require reinspection and a fresh candidate hash. Preserve the existing request ID; uncertain/pushing requests must be resumed first. Returns request_id/hash/body_file and head/base SHAs. next_step is required on prepare and on explicit update; see [next step](next-step.md). The helper renders its readable lines and `relay:next` comment before the request marker, so a create draft must contain neither. An update's complete replacement still preserves existing request markers, but it may hand back the live body unchanged: the helper cuts the previously generated block at that anchor and renders the new one. The exact frozen body includes `<!-- relay:pr-request <request_id> -->`. Review that concrete artifact as a progress update before creation, without an extra approval when execution is already authorized.

### create

```json
{"action":"create","cwd":"/absolute/repo","request_id":"32-lowercase-hex-id","hash":"returned-sha256","execution_authorized":true}
```

Authorization records the current PR request, not a document approval. The helper rechecks current remote identity, open PRs and SHAs. It pushes only the frozen source SHA to the same named remote branch, normally and without changing checkout/index. Remote-only/behind use the remote tip, equal needs no push, local-only/ahead need push, divergence requires reconciliation. Different push destinations, default-branch push and no_push when push is needed are refused. draft_only returns the prepared body with zero remote writes.

States: prepared → pushing when needed → ready → creating → recorded. Existing PRs return existing. Explicit HTTP rejections are failed after checking for a racing PR; resolve the cause and retry the same candidate only after revalidation. Ambiguous writes become uncertain. Creating/updating process-death states are uncertain for recovery purposes. Changed head/base after successful creation is returned separately as sha_changes, along with actual PR state.

### resume

```json
{"action":"resume","cwd":"/absolute/repo","request_id":"32-lowercase-hex-id"}
```

Read-only. For pushing, query remote SHA first; return ready/prepared or an unexpected-SHA error. For creating/uncertain, find the marker across all pages and all PR states, check exact repository/head/base and then read the exact number. A known number is preserved before read-back. Closed/merged PRs return their actual state. No match, duplicate markers, marker removal, external title/body edits or read failure never trigger another POST. A new request cannot bypass unfinished uncertainty. Multiple pending requests require an explicit ID. For prepared/failed/ready, return the saved hash and state; create still needs current execution authorization. A stale lock must be inspected and its owner's process confirmed ended before removing that exact lock file.

A recorded request is already confirmed. Resuming it reads the exact PR and reports current content/state, content_changes (field names changed since that request), and sha_changes. Later edits, including Relay updates, do not make that write uncertain again. A failed read or identity mismatch returns an error while preserving recorded status. Unconfirmed writes still require exact content verification.

### update

```json
{"action":"update","cwd":"/absolute/repo","number":123,"expected_hash":"sha256-of-title-body","title":"Reviewed replacement title","body_file":"/absolute/replacement.md","execution_authorized":true}
```

Only use for an explicit title/body update. Compute expected_hash with `relay_core.pr.text_hash(current_title, current_body)` from an exact GET (empty body becomes ""). Preserve unrelated user content and existing markers in the full replacement. The helper pins exact number, old content, new content and operation before PATCH, rereads immediately before writing, and rejects external edits. It never patches base/state. Response loss is recovered by GET of that number, never another PATCH. The returned/saved request ID can be resumed.

Explicit HTTP PATCH rejections are saved as failed and retain their original error. After resolving the cause, retry update with the same request_id, exact number, a freshly checked expected_hash, complete replacement and current authorization. Ambiguous PATCH outcomes remain uncertain and must be resumed without resending.

## State, templates and errors

For the specified issue, write `Closes owner/repo#number` in the PR body, using the verified repository and issue number even within the same repository. Omit the section without an issue; reference-only issues and explicit keep-open requests use neutral links. These keywords close issues on merge into the default branch. If base differs, report that this PR merge will not close the issue automatically.

Templates are read without trimming from the remote default SHA (even when base differs), at root/docs/.github, single pull_request_template.md or files directly in PULL_REQUEST_TEMPLATE/, case-insensitively. Explicit template wins; otherwise one default, otherwise one sole candidate, otherwise obtain a choice. With no candidates use Relay's default template.

Error codes include input, issue, repository, branch, git, state, locked, conflict, stale, permission, approval, length, github_rejected, github, verification and uncertain. Preserve requests and actual successful stages on failure. Do not summarize failed or unavailable verification as completed.
