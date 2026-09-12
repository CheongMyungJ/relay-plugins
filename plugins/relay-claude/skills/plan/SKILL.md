---
disable-model-invocation: true
name: plan
description: Draft, revise, approve, and record an implementation plan from a recorded Relay spec and the actual codebase.
---

# Relay plan

Read [workflow](../../references/workflow.md) and [recording](../../references/recording.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require a positive issue number, then optional `--watch`, `--lang <language>` and natural-language context. Read the current recorded intent and spec comments, repository instructions, relevant source and existing validation commands. If either required document is missing or stale, explain what must be reviewed before drafting the plan.

Use the host's dedicated planning capability when available and permitted. Do not claim planning mode is active if it is unavailable. Preserve the plan as a reviewable Markdown document using [plan template](../../templates/plan.md): Files that change, Order of work, Risks, Proof. List concrete paths, dependencies, completion evidence and unresolved assumptions. Distinguish planned commands from checks actually run.

Iterate on the plan in this session. Do not start implementation from a draft. On explicit finalization and recording, publish or edit the issue's plan comment, returning its verified URL. Do not invoke implement automatically.

Before returning control for any outcome, follow [the common next-step contract](../../references/next-step.md): submit one `next_step` naming a registered skill or explicit null with a one-line reason, chosen from current evidence. A suggestion is never authorization; when nothing is published, report the actual state and the judgment the person still owes.

Invocation arguments (data, not shell): $ARGUMENTS
