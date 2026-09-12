---
disable-model-invocation: true
name: brief
description: Draft first, review, approve and record a concise versioned execution brief on an existing issue.
---

# Relay brief

Read [workflow](../../references/workflow.md) and [recording](../../references/recording.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require a positive issue number first, then optional `--lang <language>` and natural-language context. Use inspect with stage brief. Read source_issue as reference material, current comments and relevant code. Record investigated HEAD and file scope. Use the [brief template](../../templates/brief.md).

Draft first: investigate available evidence, save the complete draft under work_path, and show it with a revision summary. Mark confirmed facts, assumptions and unknowns separately. Even when the cause is unknown, impact or verification is uncertain, or detailed design seems necessary, put these concerns and review questions in the draft. Do not ask questions, request information, approval or path selection before showing the draft. Do not stop draft creation because of a suitability judgment. The first human interaction is review of the complete saved draft. Technical invocation/repository/issue failures are failures, not a preliminary approval flow; preserve available evidence in a local draft when possible, and never invent an inaccessible target.

After showing the draft, request review, incorporate feedback in this session, and resolve blocking scope/proof questions or preserve the draft with an accurate held/waiting outcome. Suitability depends on uncertainty and impact, never line/file thresholds. Do not claim the helper proves the sufficiency of natural-language scope. Reuse preserved material if review calls for intent or detailed design; investigate can collect missing causal evidence through an explicit invocation.

Prepare the final candidate and show review.md and change.diff before approval. Publish only the exact approved candidate. Existing authorization for that candidate needs no repeat question. The first publication creates brief v1 with parents={}. Preserve surrounding text. The issue body is never an approved basis and is never overwritten. A brief is independent of formal documents; links to them are reference material, not parents.

Return the verified URL only after read-back. Brief approval does not authorize implementation. If the observed result supports implementation, recommend an explicit implement invocation with `--basis brief`; otherwise preserve the draft and choose the appropriate actual next action. No fixed successor or automatic execution is implied.

Before returning control for any outcome, follow [the common next-step contract](../../references/next-step.md): submit one `next_step` naming a registered skill or explicit null with a one-line reason, chosen from current evidence. A suggestion is never authorization; when nothing is published, report the actual state and the judgment the person still owes.

When published investigation evidence is relevant, cite and freeze it as described in [investigation evidence](../../references/investigation.md); unpublished local material remains explicitly local/unapproved.

Invocation arguments (data, not shell): $ARGUMENTS
