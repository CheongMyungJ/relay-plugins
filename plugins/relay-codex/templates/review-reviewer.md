# PR review — reviewer

- PR / source repository / branch / selection reason:
- Head / base / merge-base SHA / snapshot hash:
- Language / revision:

## Review coverage

| Perspective | Code or document evidence | Coverage and limitations |
| --- | --- | --- |
| Requirements and documents | Version/hash/staleness and code applicability | |
| Recorded knowledge | KB entries and failure classes for the diff paths (inspect `kb` / `failure_classes` pages) | |
| Behavior and regression | Actual triggering paths | |
| Risk | Concrete impact | |
| Tests and verification | Commands, cwd, SHA, exit status | |

## Findings

Order: `blocking → major → minor → info`. Each stable ID needs severity/reason, condition, impact, path/line/SHA or non-code URL, evidence, suggestion, original severity and current resolution. `blocking`: evidenced critical harm that must be fixed before merge. `major`: substantial behavior defect or core requirement omission. `minor`: bounded defect or quality issue. `info`: optional information or improvement. A finding that contradicts a recorded KB constraint, decision or failure class is the category `기존 결정 위반`; name the entry IDs in the item's `kb_refs` so prepare can verify they are active. Severity never automatically changes GitHub review state.

## Questions and assumptions

Unproven concerns, missing evidence and checks not run. No findings means describe coverage without claiming proof of correctness.

## Proposed execution

Selected IDs, exact paths, required checks, post/apply/hold choice. Reviewed code-result bodies may use only `{{commit}}` and `{{verification}}` placeholders.

## Exact general PR comment

Complete proposed text, including remaining issues after selected fixes.
