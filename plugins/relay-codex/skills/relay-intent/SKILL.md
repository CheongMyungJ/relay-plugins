---
name: relay-intent
description: Draft, revise, approve, and record a detailed Relay intent as one versioned comment on an existing GitHub issue.
---

# Relay intent

Read [workflow](../../references/workflow.md) and [recording](../../references/recording.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require a positive issue number first, then optional `--watch`, `--lang <language>` and natural-language context. Use inspect with stage intent. Read source_issue as reference material and the existing intent comment when present. A plain GitHub issue is sufficient to start; do not create a replacement issue if the number is missing or inaccessible.

Use the [intent template](../../templates/intent.md). Reuse the issue's known facts and clarify the problem, goals, scope and success criteria with the user. Do not invent unknown causes or solutions. Show the complete draft and revision summary, then incorporate feedback in this session.

The first intent is a v1 comment. Preserve surrounding user text and never overwrite the issue body.

Return the verified comment URL. Issue-body edits do not make intent or downstream documents stale. Intent revisions do invalidate downstream parent references.

When the repository has a knowledge base, look up the paths and working terms of this work with the `kb` helper's lookup before drafting and cite the returned entries in the `## 참조한 KB 항목` section as the [knowledge base contract](../../references/kb.md) describes; without a KB the section is not required.

Before choosing `next_step` for any outcome, read [the common next-step contract](../../references/next-step.md) and follow it.

When published investigation evidence is relevant, cite and freeze it as described in [investigation evidence](../../references/investigation.md); unpublished local material remains explicitly local/unapproved.
