---
disable-model-invocation: true
name: design
description: Draft, revise, approve, and record a Relay spec from a recorded intent during one explicitly invoked session.
---

# Relay design

Read [workflow](../../references/workflow.md) and [recording](../../references/recording.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require a positive issue number immediately after the skill name, then optional `--watch`, `--lang <language>` and natural-language context. Read the recorded current intent comment. If it is missing or stale, explain that intent must be reviewed and recorded before design; do not draft from an unapproved issue body.

Use [spec template](../../templates/spec.md) to combine requirements and design. Inspect the target repository and applicable project instructions. Flag conflicting constraints and carry forward unanswered intent questions. Explain assumptions without inventing project policies.

Show the draft, accept feedback, and finalize in this session. After approval publish a spec comment. Return the verified URL and do not invoke plan automatically.

When the repository has a knowledge base, look up the paths and working terms of this work with the `kb` helper's lookup before drafting and cite the returned entries in the `## 참조한 KB 항목` section as the [knowledge base contract](../../references/kb.md) describes; without a KB the section is not required.

Before choosing `next_step` for any outcome, read [the common next-step contract](../../references/next-step.md) and follow it.

When published investigation evidence is relevant, cite and freeze it as described in [investigation evidence](../../references/evidence.md); unpublished local material remains explicitly local/unapproved.

Invocation arguments (data, not shell): $ARGUMENTS
