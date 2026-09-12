---
disable-model-invocation: true
name: implement
description: Implement an approved formal plan or brief with shared verification, commit, push and drift recording.
---

# Relay implement

Read [workflow](../../references/workflow.md), [recording](../../references/recording.md), and [implementation](../../references/implementation.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require the issue number first. Accept `--branch <name>`, `--base <ref>`, `--worktree` or `--worktree=<path>`, and `--basis formal|brief`, then optional natural-language instructions. This explicit invocation authorizes development, required checks, scoped commits, ordinary task-branch push, and factual execution comments within the selected approved basis. Honor later user holds and host permissions.

Inspect current recorded documents and basis/basis_error. Select formal (intent/spec/plan) or brief through the common resolver. For a new execution without a plan, a valid brief is selected automatically even when intent/spec exist; keep those documents as reference context. If both plan and brief exist, obtain an explicit basis selection even when the plan is stale or incomplete; an unambiguous natural-language choice may be expressed as the option. Malformed or missing/replaced known documents remain errors. Never fall back from a missing, unapproved or stale selected basis. On resume preserve the pinned basis and reject conflicting options. Pin basis kind, exact parents, proof reference and basis_summary to the run; formal also retains plan_summary. Review investigated HEAD/file differences for applicability without automatically declaring documents stale.

Use host tools for code and Git work, following implementation.md. Record facts through the helper and [implementation template](../../templates/implementation.md). Keep one comment per run and update it by ID. Report major drift promptly with reasons, impact and evidence; leave follow-up judgment to the person. Drift severity alone never forces replanning or another approval pause. Never silently rewrite the approved plan.

Finish with actual checks, commit and remote SHA, worktree path, execution comment URL, drift and unfinished work. Do not claim completion when required checks remain unrun.

Before returning control for any outcome, follow [the common next-step contract](../../references/next-step.md): submit one `next_step` naming a registered skill or explicit null with a one-line reason, chosen from current evidence. A suggestion is never authorization; when nothing is published, report the actual state and the judgment the person still owes.

For investigation-derived work, review preserved patches and their origins against current code, select only approved-plan changes, and reverify adopted changes. Investigation evidence never becomes a new basis or authorizes extra commit/push scope. See [investigation](../../references/investigation.md).

Invocation arguments (data, not shell): $ARGUMENTS
