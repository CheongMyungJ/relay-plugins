---
name: relay-kb-sync
description: Extract knowledge base entries from repository paths or refresh existing entries against current code in batches, committing to a draft PR without per-item approval.
---

# Relay kb-sync

Read [workflow](../../references/workflow.md), the [knowledge base contract](../../references/kb.md) and the [KB write contract](../../references/kb-write.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd. Use the `kb` helper command from the invocation repository.

Accept repository-relative paths (extraction), `--limit <n>`, `--branch <name>`, `--resume <run_id>` and `--watch`; no paths means refresh of every active entry. `--resume` cannot combine with paths or `--branch`. This invocation authorizes KB reads and writes, commits, pushes and draft PR creation on a task branch; nothing is written to the default branch. Honor later user holds and host permissions. State lives in `.relay/kb/sync/<run_id>/`.

`begin {raw:"scripts/relay_core dispatcher --limit 20 --watch"}` pins the remote default-branch tip as `base_sha`, the branch `relay/kb-<run_id>` (unless `--branch`) and the sibling worktree `<root>-relay-kb-<run_id>`, and returns `targets`, `auto` and the first `listing` page: for extraction the tracked files with sizes, Python symbols, internal imports and the PR/issue numbers from their recent commits; for refresh the judge/broken entries with reasons, while auto entries are recorded locally only. Read further pages with `batch {run_id, list: true, cursor}`.

Choose an order, then `batch {run_id, files[] | ids[], symbols?}` with at most 15 direct files of one directory or 15 entries within the remaining budget; the helper fixes the batch, its scope and starting HEAD and returns symbol fragments (continue with `batch {run_id, batch_id, cursor}`). Write the change set for that batch and `checkpoint {run_id, batch_id, changes_file, label?}`: gates against `base_sha` (a `candidates` or `capacity_resolution_required` result leaves the batch open to fix and checkpoint again), apply, commit on the batch's starting HEAD with `kb-sync: <mode> batch <batch_id> (<run_id>)`, push, then create the draft PR after the first real commit or update its body; a batch without tracked changes records its rejections only. Budgets are 60 files or 80 entries per call unless `--limit`. Extraction candidates cite `path:<p>@<base_sha>` or the classified PR/issue numbers, never reasons inferred from code.

When the budget is exhausted with targets left the run is paused and suggests itself; a new session continues with `--resume <run_id>` or `resume {run_id, limit?}`, which recovers unfinished stages and grants a fresh budget on the same branch and PR. `finish {run_id}` requires no open batch, no remaining target and a handling operation for every judge/broken entry, then finalizes the PR body (ending with `<!-- relay:kb-sync <run_id> -->` after the rendered next step, under 65,000 characters by summarizing counts), marks it ready and suggests review. A run with no PR finishes with null. Subagents may read fragments and draft candidates; batches, checkpoints and remote writes stay in this session.

Report the run ID, branch, worktree, base SHA, processed and remaining counts, commits, PR URL and `watch` result. Never recreate a missing worktree, force-push, or repost an uncertain PR write; resume reconciles it.

Before choosing `next_step` for any outcome, read [the common next-step contract](../../references/next-step.md) and follow it.
