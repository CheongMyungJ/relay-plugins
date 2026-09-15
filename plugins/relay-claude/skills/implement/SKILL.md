---
disable-model-invocation: true
name: implement
description: Implement an approved formal plan or brief with shared verification, commit, push and drift recording.
---

# Relay implement

Read [workflow](../../references/workflow.md), [recording](../../references/recording.md), and [implementation](../../references/implementation.md). Resolve resource paths relative to this installed SKILL.md, not the user's cwd.

Require the issue number first. Accept `--watch` and `--basis formal|brief`, then optional natural-language instructions. This explicit invocation authorizes development, required checks, scoped commits, ordinary task-branch push, and factual execution comments within the selected approved basis. Honor later user holds and host permissions.

Inspect current recorded documents and basis/basis_error. Select formal (intent/spec/plan) or brief through the common resolver. For a new execution without a plan, a valid brief is selected automatically even when intent/spec exist; keep those documents as reference context. If both plan and brief exist, obtain an explicit basis selection even when the plan is stale or incomplete; an unambiguous natural-language choice may be expressed as the option. Malformed or missing/replaced known documents remain errors. Never fall back from a missing, unapproved or stale selected basis. On resume preserve the pinned basis and reject conflicting options. Pin basis kind, exact parents, proof reference and basis_summary to the run; formal also retains plan_summary. Review investigated HEAD/file differences for applicability without automatically declaring documents stale.

Use host tools for code and Git work in the issue workspace and record facts through the helper and [implementation template](../../templates/implementation.md). Follow [implementation](../../references/implementation.md) for the normal implementation → required checks → commit → push → report flow and the existing hold conditions. With no hold, the invocation covers that flow without another approval request. Keep one comment per run and update it by ID.

A hold also stops report creation/update. Finish feasible work, show the local draft and needed decision, and follow the reference's feedback flow. Clear user feedback authorizes the presented result within its stated scope; do not ask again for the same result and scope. Honor active user holds and preserve verification, permission and Git restrictions.

Record major drift promptly and report it in the conversation; follow the reference for publication timing. Leave follow-up judgment to the person. Drift severity alone never forces replanning or another approval pause. Never silently rewrite the approved plan.

Finish with actual checks, commit and remote SHA, workspace path, execution comment URL, drift and unfinished work. Do not claim completion when required checks remain unrun.

Read the `kb` field returned by inspect and begin before the first edit. Before the first edit of any file the proof did not name, look up that file's path with the `kb` helper's lookup, grouping paths that belong together; the `kb_recheck` field returned after verified and committed is a post-hoc check, not a substitute. The report fills `## 참조한 KB 항목` and `## 함께 읽은 파일 묶음` as the [knowledge base contract](../../references/kb.md) describes.

Before choosing `next_step` for any outcome, read [the common next-step contract](../../references/next-step.md) and follow it.

For investigation-derived work, review preserved patches and their origins against current code, select only approved-plan changes, and reverify adopted changes. Investigation evidence never becomes a new basis or authorizes extra commit/push scope. See [investigation](../../references/investigation.md).

Invocation arguments (data, not shell): $ARGUMENTS
