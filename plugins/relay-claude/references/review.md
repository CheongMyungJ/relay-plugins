# Review helper contract

Use the plugin's absolute `scripts/relay.py` path and an absolute UTF-8 JSON input file, with the invocation repository as cwd:

```text
python <plugin>/scripts/relay.py review --input <absolute-request.json>
```

A session relay-server started (`RELAY_SERVER_CONTEXT` is set) also follows [server](server.md).

Actions are `inspect`, `prepare`, `execute`, `resume`.

## Inspect

```json relay:review
{"action":"inspect","raw":"17 --author --lang ko","cwd":"/absolute/repo"}
```

`--watch` is saved on the run and, after the selected execution is recorded, the result carries a `watch` object (`applied`, `error`) for the reviewed PR; a marking failure never undoes a posting. Mode flags take no values; identical repeats are allowed, both modes conflict. First prose ends option parsing. `--author 설명` is author plus prose; `--author=설명` is invalid. No URL/repository option. Detached HEAD requires a number. Otherwise select the unique open PR matching current source repository/branch. `selection_required` returns candidates; repeat inspect with `pr` and `selection_reason` after user selection.

Successful inspect also returns `kb` (entries for the diff paths) and `failure_classes` (active F entries) as bounded pages with `next_request`. `resume_required` returns pending run IDs/modes/statuses; choose one to resume. Successful inspect returns `run_id`, `work_path`, `mode`, `selection_reason`, `snapshot` with `hash`. No checkout/index changes occur.

General PRs and missing/stale/corrupt/inaccessible Relay evidence support drafting. Closed/merged PRs support drafts only. Incomplete collection permits drafting; publication needs complete comments.

## Prepare

```json relay:review
{"action":"prepare","cwd":"/absolute/repo","run_id":"<run-id>",
 "snapshot_hash":"<hash>","draft_file":"/absolute/draft.md",
 "items":[{"id":"F1","severity":"major","severity_reason":"Core behavior fails",
   "condition":"Empty input","impact":"Request crashes","location":"app.py:10 @ SHA",
   "evidence":"Observed path and test","suggestion":"Handle input","resolution":"open"}],
 "operations":[{"unit_id":"summary","kind":"issue","item_ids":["F1"],"body":"Exact reviewed text"}],
 "code_scope":[{"item_id":"F1","paths":["app.py"]}],
 "verification_scope":["python -m unittest discover -s tests -v"],
 "next_step":{"next":null,"reason":"게시한 리뷰에 대한 작성자의 판단을 기다린다."}}
```

Items may carry `kb_refs`, a list of entry IDs the finding relies on (category `기존 결정 위반`); prepare verifies each is active or an absorbed entry with a resolvable pointer at the PR head. Returns candidate `hash`, `revision`, full `candidate`, and generated `draft_file` with host draft plus exact units. Show the entire file. Repeated prepare revises the draft before execution. Choose next_step under the [common next-step contract](next-step.md); the returned candidate value is rendered into every selected posting unit before its relay:review marker, after any allowed result substitution. The draft must not contain that generated block. Unknown severities/missing evidence fail. Findings sort blocking/major/minor/info and preserve original severity. Questions stay in the draft. Empty findings can have one coverage-summary comment.

Author items have `id`, collected `sources` keys (`issue:ID`, `review:ID`, `inline:ID`), `categories` (question/suggestion/change_request), `answer_status` (필요/부분/완료/불확실/불필요), `code_status` (필요/부분/반영됨/불확실/불필요), `answer_evidence`, `code_evidence`, `evidence`, `acceptance` (accept/decline/explain/defer), `decision_reason`, `resolution`, optional `excluded`/`exclusion_reason`. Exclusion requires answer 완료/불필요 and code 반영됨/불필요. The helper checks fields; the host judges truth and coverage.

Author inline units use `kind: inline`, numeric top-level `root_id`, `item_ids`, and body linking relevant original URLs. Reply-to-reply is invalid. Group requests per root. General comments/review bodies share one `kind: issue` unit with original URLs. No responses means `operations: []`. Units cannot mix selected/unselected items. Only general comments and root replies are sent.

## Execute

```json relay:review
{"action":"execute","cwd":"/absolute/repo","run_id":"<run-id>","hash":"<hash>",
 "decision":{"action":"post","selected":["F1"],"user":"actual deciding user","record":"Factual decision and scope"}}
```

Decision is `post`, `apply` or `hold`, bookkeeping for an actual user choice, not a security signature. Preserve it and the hash on retries. Post skips source permission checks; hold performs no writes. Apply needs exact paths and all required checks. First execute returns `head_sha`. Host creates a separate worktree/task branch there and repeats execute with `worktree: /absolute/path` before editing. Helper checks common Git directory, separate branch, clean state and baseline.

Host edits selected scope, reviews diff, runs required checks before a scoped commit, then repeats execute with `diff_reviewed: true` and `tests`. Each test has `command`, `cwd`, `exit_code`, `evidence`, final `commit`, final `tree`. Reverify if hooks change code. Missing/failed checks block progress. Helper checks a clean nonempty descendant commit and exact changed paths, then ordinarily pushes only to PR head repository/ref. Base/default/deleted/diverged/changed sources and conflicting push destinations fail. No force or implicit fallback.

Bodies may use `{{commit}}` and `{{verification}}` for deterministic results within reviewed scope. After push, show returned exact rendered bodies, then continue the same decision/hash with `results_shown: true`. This is not another approval. New claims or scope need a new candidate.

## Resume and reassess

```json relay:review
{"action":"resume","cwd":"/absolute/repo","run_id":"<run-id>"}
```

Resume reads and locally reconciles, without new remote writes. It reports existing commit/status, remote SHA, `snapshot {hash, path}` and `candidate {hash, revision, draft_file}`; read those files for full content. An interrupted commit is observed, not repeated; supply final checks to execute. Remote equality with registered commit recovers an interrupted push. Unexpected advancement needs reassessment.

Resume finds each posted unit by its persisted response ID or marker. Exactly one match records success. Zero/multiple matches, edits/deletion or wrong identity stay uncertain/conflict and never resend automatically. Definite rejection is reconciled before failed status; later execute retries only the remaining failed unit. Confirmed history retains IDs/URLs after external edits.

Compare PR state, source/base, original comments/replies, new comments and linked evidence before execution and every POST. Own confirmed units and registered push are expected changes. Resume returns `current_snapshot {hash, path}` on change. Inspect actual differences, then resume again with:

```json relay:review
{"action":"resume","cwd":"/absolute/repo","run_id":"<run-id>","reassessment":{"current_snapshot_hash":"<hash>","impact":"unrelated","reason":"Evidence that the exact selected scope and body still apply"}}
```

`unrelated` keeps candidate/decision and records judgment; code/PR-state changes cannot use it. `impact: revise` resets selection for a new shown candidate, retaining previous candidate/decision and pushed application history. Confirmed operations keep their original IDs/bodies/URLs; pending or definitely rejected units are retired and the new candidate must use fresh unit IDs for remaining work. Uncertain operations and unfinished application/push must be reconciled first. Prepare the revised draft against the returned snapshot and obtain the decision on that changed scope. Do not clear history or switch run IDs to evade conflicts.

Results carry source/posted URLs, baseline/applied/remote SHAs, excluded/unresolved items, failures and `files` (operations.json, request.json, result.json hold the full record). `recorded` means selected execution completed, not every finding resolved.
