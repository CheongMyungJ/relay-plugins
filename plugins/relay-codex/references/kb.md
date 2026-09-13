# Knowledge base contract

The knowledge base (KB) keeps decisions (D), constraints (C), structure facts (S, including glossary terms), verification recipes (V), non-goals (N) and failure classes (F) as path-bound entries under `docs/kb/`. It complements, never replaces, the GitHub records and the `references/` contracts. Every stage reads it through the helper; only kb and kb-sync change `docs/kb/**` and the managed `<!-- relay:kb:begin -->…<!-- relay:kb:end -->` regions of `AGENTS.md`/`CLAUDE.md`, following the [KB write contract](kb-write.md). Use the plugin's absolute `scripts/relay.py` path and an absolute UTF-8 JSON input file with the invocation repository as cwd:

```text
python <plugin>/scripts/relay.py kb --input <absolute-request.json>
```

The input carries `action` and its fields; every result is `ok/result` or `ok/error/message`. Sizes are Unicode characters of the ensure_ascii=false JSON: one page at most 20,000, the `kb` field inside other results at most 8,000, one entry summary at most 600, `limit` 12 by default and 50 at most.

An entry is `### <ID> <rule>` with ID `<type>-<32 hex>`, then `유형`, `상태` (유효, 대체됨, 흡수됨), `경로`, `용어`, `출처` and narrative lines such as `이유`, `기각`, `코드불가` (why code cannot carry it) and `유인` (the wrong change someone would make without it). A C marked `호환: 외부게시` protects externally published data; do not change what it names without compatibility evidence.

## Lookup

- `lookup {paths[], terms[], ids[], sha?, detail: summary|full, include_inactive?, cursor?, limit?}` → `entries`, `redirects` (absorbed pointers and superseded targets), `glossary` (declared aliases only), `tombstones`, `missing_ids`, `omitted` per type, `truncated`, `next_cursor`, `query_hash`, `kb_present`. A file matches its symbols and ancestor directories, a symbol its file and ancestors, a directory its descendants and ancestors; new paths match ancestors without an existence check. Path-less N and D entries are found by terms. Order: direct IDs, compat C, other C, the rest; then specificity, match count, ID. Cursors bind the query, SHA and KB digest; a changed KB is a conflict.
- `fragment {ids[] | path, symbols[], sha?, since?, cursor?}` → symbol sources, change hunks since the confirmed SHA and `<module>` top-level ranges, split by line range with `continued` when a unit exceeds the page.

## What each stage receives

- Document stages (intent, design, plan, brief, investigate): `inspect` returns `kb: {present, counts, how_to_query}`. Look up the work's paths and working terms before drafting.
- implement: `inspect` and `run begin` return `kb` for the proof document's backtick paths and entry IDs. Look up any other file before its first edit; the `kb_recheck` field of `verified` and `committed` responses covers the paths actually changed and is never stored in the run.
- review: `inspect` returns `kb` for the diff paths and `failure_classes` pages. A finding that contradicts an entry names it in `kb_refs`; prepare accepts only active entries or absorbed entries whose pointer resolves.
- Without a KB these fields are null and no section below is required.

## Citing entries

When a KB exists, intent, spec, plan, brief, investigation and implementation bodies need a `## 참조한 KB 항목` section: `- <ID> — 반영 방식`, `- <ID> → references/file.md#anchor — 반영 방식` for absorbed entries, or `- 없음 (조회: 경로 …; 용어 …)`. Prepare rejects unknown, superseded or deleted IDs, absorbed entries without their current pointer, active entries cited with a pointer and a `없음` line that names neither 경로 nor 용어. Implementation reports also fill `## 함께 읽은 파일 묶음` with `- a.py, b.py, c.md — 함께 읽은 이유` for paths looked up together before a first edit; kb reads it as a source of structure facts.
