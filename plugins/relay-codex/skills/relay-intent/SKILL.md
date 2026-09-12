---
name: relay-intent
description: Draft, revise, approve, and record a detailed Relay intent as one versioned comment on an existing GitHub issue.
---

# Relay intent

Read [workflow](../../references/workflow.md) and [recording](../../references/recording.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require a positive issue number first, then optional `--watch`, `--lang <language>` and natural-language context. Use inspect with stage intent. Read source_issue as reference material and the existing intent comment when present. A plain GitHub issue is sufficient to start; do not create a replacement issue if the number is missing or inaccessible.

Use the [intent template](../../templates/intent.md). Reuse the issue's known facts and clarify the problem, goals, scope and success criteria with the user. Do not invent unknown causes or solutions. Show the complete draft and revision summary, then incorporate feedback in this session.

The first intent is a v1 comment. Preserve surrounding user text and never overwrite the issue body.

Return the verified comment URL. Explain that design can use this current intent after the user explicitly invokes it; do not invoke design automatically. Issue-body edits do not make intent or downstream documents stale. Intent revisions do invalidate downstream parent references.

Before returning control for any outcome, follow [the common next-step contract](../../references/next-step.md): submit one `next_step` naming a registered skill or explicit null with a one-line reason, chosen from current evidence. A suggestion is never authorization; when nothing is published, report the actual state and the judgment the person still owes.

When published investigation evidence is relevant, cite and freeze it as described in [investigation evidence](../../references/investigation.md); unpublished local material remains explicitly local/unapproved.
