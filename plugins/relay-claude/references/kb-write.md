# KB write contract

kb and kb-sync write the knowledge base with this contract; read the [knowledge base contract](kb.md) first for the helper call, sizes and lookup. Error codes add `kb` (format, operation, invariant), `capacity`, `source`, `budget` and `head_advanced` to the common ones.

## Files

| Path | Role |
| --- | --- |
| `docs/kb/<topic>.md` | D·C·S·V; topic is the first sorted path's directory (two levels, joined by `-`), `root` for root files, `decisions` for path-less D; reserved names get a `topic-` prefix |
| `docs/kb/non-goals.md`, `failure-classes.md`, `glossary.md` | N; F; S term entries |
| `docs/kb/archive/<topic>.md` | superseded/absorbed entries moved out when a file exceeds its active cap |
| `docs/kb/INDEX.md` | generated: active entries with paths and rules, absorbed entries with their pointers |
| `docs/kb/state.json` | `{"schema":1,"confirmed":{id:sha},"deleted":{id:{"sha","links"}}}` |
| `<dir>/AGENTS.md`, `<dir>/CLAUDE.md` | managed region: directory constraints in AGENTS, `@AGENTS.md` in CLAUDE; the root pair is a pointer only; outer text is preserved byte for byte |

Knowledge paths are exactly `docs/kb/**` and every `AGENTS.md`/`CLAUDE.md`; a KB commit stages the helper's exact file list and nothing else. Active caps are 30 per file and 20 for `failure-classes.md`, judged on the whole result of a change set; a same-file supersede does not raise the count.

## Entries

```markdown
### C-51ad4c79f293478da8de5471b8fd7a90 relay:next는 게시 앵커에서 읽고 값에서 재구성해 대조한다
- 유형: 제약 | 상태: 유효 | 호환: 외부게시
- 경로: scripts/relay_core/next_step.py
- 용어: relay:next, 앵커, 재구성
- 출처: issue:24/comment:5643294992
- 이유: 문법 설명 산문을 마커로 오인하면 기존 문서를 읽을 수 없다
- 기각: 본문 전체에서 패턴을 찾아 마커로 취급
- 코드불가: 과거 게시물과의 호환 이유는 현재 구현만으로 복원되지 않는다
- 유인: 범용 검색으로 바꾸면 문법 예제를 실제 마커로 오인할 수 있다
```

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

The change set is `{"ops":[…],"rejected":[{rule,reason}],"classified":[{op_id,existing_id,verdict:same|conflict|unrelated,reason}]}`; ops are `create {entry}`, `update {id, entry, evidence?}` (type kept; a decision may only be supplemented), `supersede {old, entry, evidence?}`, `absorb {id, pointer, evidence?}`, `delete {id, evidence?}` (never a decision) and `reconfirm {ids[], evidence{id:text}?}` (auto or judge; broken is refused). One operation per existing ID; a same candidate becomes an update; every conflict needs an explicit operation; compat entries always need evidence. Reconfirmation: a missing path or Python symbol is broken; compat entries, path-less entries, missing, unreadable or non-ancestor confirmed SHAs and any tracked change under the paths are judge; otherwise auto, which is recorded only in the local `checked` map.

Gate order: candidates and complete classification → operations and final caps → format → path existence at the fixed SHA → source existence and state → the two attachment lines → path-only reason limit → scope reconfirmation complete. Two results are not errors: `candidates` lists the existing entries overlapping each create, to classify and submit again; `capacity_resolution_required` names the over-cap `paths` and returns `capacity` (per file `path`, `cap` and its `active` entry summaries within one page) and `omitted_paths`, to add update, supersede, absorb or delete for listed entries and submit again.

- `check {changes, sha, scope[], checked?, execution_id?, batch_id?, issued?}` → the frozen plan and verdicts without writing, or one of the two results above.
- `render {worktree?}` regenerates INDEX, state and managed regions; ID, link and state errors stop it.
- `apply {plan, changes, worktree, record}` replays the frozen plan into a worktree with a transaction record.

## Recovery

Every external effect is planned before it runs and recorded after: apply (pre/post digests, post-tree), commit (parent, paths, post-tree, message), push (expected remote, local commit; delivered when the remote already has it or a descendant), PR creation (unique marker), PR update (previous digest), comment (marker, body, author). A retry with the same authorization completes the missing step, recovers a matching result, or stops with `conflict`/`uncertain`; nothing is force-pushed, re-created or re-posted automatically. Third-party edits of planned files are conflicts.
