# KB write contract

kb and kb-sync write the knowledge base with this contract; read the [knowledge base contract](kb.md) first for the helper call, sizes and lookup. Error codes add `kb` (format, operation, invariant), `capacity`, `source`, `budget` and `head_advanced` to the common ones.

## Files

Entries live in `docs/kb/<topic>.md`, with N, F and S term entries in `non-goals.md`, `failure-classes.md` and `glossary.md`; the helper places, archives and renders them together with `docs/kb/INDEX.md`, `docs/kb/state.json` and the managed regions of `AGENTS.md`/`CLAUDE.md`. Knowledge paths are exactly `docs/kb/**` and every `AGENTS.md`/`CLAUDE.md`; a KB commit stages the helper's exact file list and nothing else. Active caps are 30 per file and 20 for `failure-classes.md`, judged on the whole result of a change set; a same-file supersede does not raise the count.

## Entries

Host JSON for a candidate: `type`, optional `subtype: "term"`, `rule`, `paths[]`, `terms[]`, `sources[]`, optional `why`, `rejected_alt`, `command`, `definition`, `note`, required `not_in_code` and `incentive`, optional `compat` (C only) and `blocking` (F only). The helper assigns `id` (`<type>-<uuid4 hex>`), `status` and links. Limits: rule 200 characters, each narrative field 400, rule plus narrative lines five, entry 4,000, 12 paths of 240 characters (`dir/`, `dir/file.ext`, `dir/file.py:Class.method`; no `..`, backslashes or absolute paths), 2-5 terms of 40 characters (NFKC, casefold, whitespace-normalized). D needs 이유 and 기각, C paths, S two paths (term: 정의), V paths and 명령, N no paths, F two sources or blocking.

Sources are 1-8 of: `issue:n/comment:id`, a Relay document or implementation report on that issue; `pr:n`, a merged PR; `pr:n/comment:id`, `pr:n/inline:id` and `pr:n/review:id`, a nonempty general comment, inline comment or submitted review body on that PR, with or without the relay:review marker; `path:p@sha`, a path at the base SHA or an ancestor. Path-only sources cannot carry 이유/기각, and reasons are never inferred from code.

| Type | Adopt | Reject |
| --- | --- | --- |
| D | why the decision holds and the alternative actually rejected (이유·기각) | explanation of a code branch, general coding principles |
| C | a path-bound rule whose violation does not show where the change is made, backed by its source | what the code at the path already enforces, general coding principles |
| C compat (`호환: 외부게시`) | why externally published data keeps its format, citing that data; every reconfirmation needs compatibility evidence | internal-only formats |
| S | a non-local relation whose producer, consumer and compatibility test must be read together, with the reason | a plain import edge or file list |
| S term | a repository-specific meaning whose other reading leads to a wrong design | a dictionary definition |
| V | a non-obvious rule for choosing checks or an environment precondition | a copied test or script |
| N | the reason a tempting scope expansion was excluded | a prohibition without evidence |
| F | a recurring finding or blocking defect pattern | a generic possibility without evidence |

## Change sets

The change set (the `changes_file` content) is `{"ops":[…],"rejected":[{rule,reason}],"classified":[{op_id,existing_id,verdict:same|conflict|unrelated,reason}]}`. Every op carries a unique `op_id` and its `op`: `{op_id, op:"create", entry}`, `{op_id, op:"update", id, entry, evidence?}` (type kept; a decision may only be supplemented), `{op_id, op:"supersede", old, entry, evidence?}`, `{op_id, op:"absorb", id, pointer, evidence?}`, `{op_id, op:"delete", id, evidence?}` (never a decision) and `{op_id, op:"reconfirm", ids[], evidence{id:text}?}` (auto or judge; broken is refused). One operation per existing ID; a same candidate becomes an update; every conflict needs an explicit operation; compat entries always need evidence. The helper classifies each reconfirmation as auto, judge or broken.

```json relay:changes
{"ops":[{"op_id":"c1","op":"create","entry":{"type":"C","rule":"규칙 한 줄","paths":["scripts/app.py"],"terms":["app","rule"],"sources":["pr:5"],"not_in_code":"코드에 둘 수 없는 이유","incentive":"모르면 할 법한 잘못된 개선"}}],"rejected":[{"rule":"약한 후보","reason":"출처 없음"}],"classified":[]}
```

Two results are not errors: `candidates` lists the existing entries overlapping each create, to classify and submit again; `capacity_resolution_required` names the over-cap `paths` and returns `capacity` (per file `path`, `cap` and its `active` entry summaries within one page) and `omitted_paths`, to add update, supersede, absorb or delete for listed entries and submit again.

Every external effect (apply, commit, push, PR, comment) is planned before it runs and recorded after; a failed or interrupted write follows the helper's recovery message.
