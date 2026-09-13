---
name: relay-pr
description: Create a GitHub pull request from an issue's implementation branch, an explicit branch, or the current branch; reconcile prior requests or update an existing PR when requested.
---

# Relay PR

Read [PR protocol](../../references/pr.md) for the helper contract and recovery rules. Use [PR template](../../templates/pr.md) when the repository has no template.

Accept an optional positive issue number, then contiguous `--watch`, `--branch <source>` and `--base <target>` options, then optional natural-language instructions. The repository is the Git repository containing invocation cwd, using origin or its sole GitHub remote. An explicit PR invocation authorizes necessary ordinary source-branch push and PR creation. Honor draft PR, body-draft-only, no-push and later holds. Do not add the document approval workflow to a clear PR request.

Call the dedicated helper `pr` command, never document inspect/prepare/publish or implementation begin/restore. Keep the returned repository and request ID for this session. Resume unfinished requests first. Existing PR results end the creation flow: report the verified URL and branches without changing title, body, issue links or remote refs. Update an existing PR only when explicitly requested.

Resolve source by explicit branch, otherwise verified issue execution evidence, otherwise current branch. Show ambiguous or excluded candidates with branch, run ID/status, recorded commit, actual tip and evidence location. Obtain the missing selection and continue in the same session with head and selection_reason. Do not guess from branch names or replace an issue selection with the current branch. Resolve contradictory options and prose before remote writes.

Read the actual returned diff at the frozen head/base SHA. Write a concrete title and a body explaining the problem, resulting behavior and actual verification. Use the user's language/emphasis, then related document language, then Korean. Attribute old test evidence to its execution commit/time; disclose unrun checks and dirty-worktree changes separately. Never claim a plan item is implemented based only on its plan or commit message.

Use templates from the fetched remote default-branch commit. Preserve required repository fields, checklists, references and their original meaning. Integrate equivalent Relay sections without repetition, keep unsupported checks unchecked, and remove empty optional sections. If selection is ambiguous, show paths and purposes and obtain a selection. Organization-wide inherited templates are not discovered. Link the specified issue with `Closes owner/repo#number`, substituting the verified repository and issue number even for same-repository issues. Omit the related-issue section when no issue is linked. Use neutral links for reference-only issues or an explicit request to keep an issue open. GitHub closes linked issues when the PR is merged into the default branch; if base differs, explain that this merge will not automatically close the issue.

Prepare the title/body and constraints through the helper, passing the inspection_hash from the inspect whose diff/template you used to write the body. If it is stale, read the new diff/template and revise the body before retrying; never attach a fresh hash to an unreviewed old draft. Show the concrete body file and source → target as a progress update, then create with the returned hash and execution_authorized=true. This records the existing request and does not require another approval. A body-only request ends at the prepared artifact. Changed refs require reading the new diff and preparing a new body/hash.

For explicit updates, read the exact PR, preserve unrelated user content and every existing request marker in the complete replacement, then supply its expected title/body hash. Recover ambiguous updates by that exact number. Never resend an uncertain creation or bypass it with a new request.

Explicitly rejected updates can be retried with the same request ID after resolving the cause and rechecking current content and authorization. Resuming an already recorded request reports subsequent content_changes and current PR state without undoing completion; distinguish these later edits from an unconfirmed write.

Finish with the verified PR URL, source → target, actual open/closed/merged/draft state, the `watch` result when requested, and unresolved failures or SHA changes. Code edits, commits, branch rewriting, force/default-branch pushes, forks, merge/auto-merge, deployment and automatic next-stage invocation are outside this skill.

Before choosing `next_step` for any outcome, read [the common next-step contract](../../references/next-step.md) and follow it.
