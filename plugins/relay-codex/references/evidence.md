# Investigation evidence in documents and implementation

Document prepare accepts optional evidence_refs: each item has host, repo, issue, investigation_id, reference:{target,version,hash}, and nonempty evidence_ids. Selected IDs must exist in the verified published recovery summary.

```json relay:prepare
{"body_file":"<abs-path>","next_step":{"next":"plan","reason":"확정된 설계를 실행 계획으로 정리한다."},"evidence_refs":[{"host":"github.com","repo":"owner/repo","issue":1,"investigation_id":"<investigation-id>","reference":{"target":"123","version":1,"hash":"<hash>"},"evidence_ids":["e1"]}]}
```

A reference change is a reassessment error, never silent replacement. Candidate and visible document preserve the references, and publish rechecks them before a new write. These references are not document parents or execution basis. Investigation revisions do not mark old documents stale. Unpublished local evidence remains explicitly local/unapproved prose until a published record can be verified. Inspect returns current investigation records to support this comparison.

Investigation-derived patches remain experimental until reviewed against the approved formal/brief baseline. Review preserved patches and their origins against current code, select only approved-plan changes, and reverify adopted changes on current code. Do not automatically copy an investigation worktree or treat its outcome as implementation verification. Investigation evidence never becomes a new basis or authorizes extra commit/push scope.
