---
name: relay-design
description: Draft, revise, approve, and record a Relay spec from a recorded intent during one explicitly invoked session.
---

# Relay design

Read [workflow](../../references/workflow.md) and [recording](../../references/recording.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require a positive issue number immediately after the skill name, then optional `--watch`, `--lang <language>` and natural-language context. Read the recorded current intent comment. If it is missing or stale, explain that intent must be reviewed and recorded before design; do not draft from an unapproved issue body.

Use [spec template](../../templates/spec.md) to combine requirements and design. Inspect the target repository and applicable project instructions. Flag conflicting constraints and carry forward unanswered intent questions. Explain assumptions without inventing project policies.

Show the draft, accept feedback, and finalize in this session. After approval publish a spec comment. Return the verified URL and do not invoke plan automatically.

Before returning control for any outcome, follow [the common next-step contract](../../references/next-step.md): submit one `next_step` naming a registered skill or explicit null with a one-line reason, chosen from current evidence. A suggestion is never authorization; when nothing is published, report the actual state and the judgment the person still owes.

When published investigation evidence is relevant, cite and freeze it as described in [investigation evidence](../../references/investigation.md); unpublished local material remains explicitly local/unapproved.
