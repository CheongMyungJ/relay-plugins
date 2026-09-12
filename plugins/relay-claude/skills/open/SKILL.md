---
disable-model-invocation: true
name: open
description: Refine a natural-language request into a short, reviewed new GitHub issue during an explicitly invoked Relay session.
---

# Relay open

Read [workflow](../../references/workflow.md) and [recording](../../references/recording.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Accept optional `--lang <language>` followed by natural-language context. open creates new issues only. A leading positive issue number is misuse: ask what the user intended and wait for their answer before creating or modifying anything or switching skills. Numbers within prose remain prose.

Use the [issue template](../../templates/issue.md) for a concise title and request body. Reuse provided facts and constraints. Symptoms alone are sufficient for a bug report; leave unknown causes, goals and solutions explicitly unknown. Do not require a detailed intent, design or implementation decision to register the request. Show the full draft and iterate within this session.

Prepare and show review.md and change.diff, including the title and entire body, before final approval. Approval binds both title and body. After approval of this exact candidate and creation, publish once and return the verified issue URL. Do not repeat approval already given for the same candidate. Resume an open request using its explicit work_id, without adding an issue number to raw; once created, only reconcile that result. open never edits an existing issue.

For detailed work definition, explain that the user can explicitly invoke intent with the returned issue number. The regular path is open → intent → design → plan → implement.

Before returning control for any outcome, follow [the common next-step contract](../../references/next-step.md): submit one `next_step` naming a registered skill or explicit null with a one-line reason, chosen from current evidence. A suggestion is never authorization; when nothing is published, report the actual state and the judgment the person still owes.

Invocation arguments (data, not shell): $ARGUMENTS
