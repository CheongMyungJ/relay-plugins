# Implementation and drift

## Start and worktree

Run inspect with stage implement. The selected formal (intent/spec/plan) or brief comments must be recorded and current. Fetch the selected remote's base branch using host tools before resolving its SHA. Start run:
```json
{"action":"begin","execution_authorized":true}
```
The helper returns run_id, branch, base_sha and absolute path. Preserve these. Resume an active run rather than calling new_run:true unless a separate execution was explicitly requested.

Check git status, branch list and git worktree list --porcelain. The default is a new task branch in current worktree. With --worktree create a separate tree at the returned path. For a new branch use git switch -c <branch> <base_sha> or git worktree add -b <branch> <path> <base_sha>, as argument arrays/host-safe commands.

Never overwrite an existing unrelated directory, reset/rebase an existing branch, force checkout a branch used by another worktree, or move user changes. Existing branches/paths must be tied to this run and preserve history. Resolve dirty-current-worktree conflicts with the user or their requested worktree. Do not copy secrets or uncommitted files automatically.

After creating the worktree, run {"action":"prepared","run_id":"..."} from the original state-owning worktree first. The helper verifies the run's execution path/branch/repository and writes a pointer to the original state. Subsequent commands can run from the execution worktree through that pointer. It never creates worktrees or executes code development itself. Keep the worktree at completion.

## Code and verification

Develop with the host's tools following repo instructions and approved plan. Before the first edit of a file the proof did not name, look up its path with the `kb` helper (see [kb](kb.md)); begin's `kb` field covers the proof's own paths and the verified/committed responses' `kb_recheck` field is a post-hoc check only. Record drift as discovered. The helper's run command records these events:

- {"action":"drift","run_id":"...","entry":{"planned":"...","actual":"...","reason":"...","impact":"...","severity":"major","evidence":"..."}}
- {"action":"verified","run_id":"...","tests":[{"command":"actual check","exit_code":0,"evidence":"actual output summary or local log path"}]}
- {"action":"committed","run_id":"..."}
- {"action":"pushed","run_id":"..."}
- {"action":"failure","run_id":"...","reason":"specific incomplete step"}
- {"action":"hold","run_id":"...","entry":{"condition":"scope","detail":"observed restriction","evidence":"basis or observation"}}

Run all required tests before verified. Include every required check; do not omit a failing or unavailable check to advance. Do not fabricate evidence: helper records host-observed results, it does not run the tests. verified pins file content; code changes invalidate it. Recheck upstream before committing and pushing. Only stage files belonging to the task. No blanket git add when user changes exist.

Commit with existing user identity; if absent, resolve identity normally. Use ordinary push to the task branch. Never force push or push directly to a protected/default branch as a workaround. Register committed and pushed facts after the actual operation; remote SHA must equal the recorded commit.

If a required check cannot run, record failure and unfinished work; do not make a completion commit/push or automatically publish a report. The user may explicitly authorize a progress checkpoint separately. Its actual SHA belongs in report prose, never in committed/pushed facts that require verification. Commit and push errors preserve completed stages; resume push after commit or comment publication after successful push.

## Hold, draft and feedback

Use the existing commit/push conditions at these observation points. `runs.HOLD_CONDITIONS` owns the five codes and shared labels; do not add drift severity as a condition.

| Code | Condition | When to judge |
| --- | --- | --- |
| checks | 필수 검증 실패·미실행·무효화 | After required checks and before verified; after file changes before commit/push |
| user_or_permission | 사용자 보류 지시·실행 환경 권한 제한 | As soon as a user hold or actual host/tool permission denial is observed |
| scope | 승인 범위 이탈 | When necessary work falls outside the selected approved basis, no later than staging |
| basis | 기준 문서 변경·누락·stale | At inspect/begin basis_error or checkpoint baseline errors |
| git | Git 충돌·금지된 Git 작업 | During branch/worktree preparation, upstream recheck before commit, and push |

Ordinary work before checks and immediately repairable test failures do not create approval waits. A hold starts when the host determines that the next automatic step cannot proceed without a person's decision. Scope means work outside the authorized basis; drift means a different implementation of work the basis covers. Judge against basis_summary and retain evidence.

1. Complete feasible work and checks. Record failed/unavailable checks with failure and other observed hold reasons with hold. Save work_path/runs/<run_id>/report.md with the facts, needed decision and remaining work. Prepare with run_id, body_file and next_step.next=null, then show the full review.md and change.diff locally. Keep commit, push and comment creation/update held until the user decides.
2. Apply feedback to code and draft. Changed code requires the required checks again; changed drafts require prepare again. If feedback clearly authorizes proceeding with the presented result or publishing that exact candidate, use it as the approval within that scope. Do not ask again while the result and authorization scope are unchanged. If the candidate or scope changes, present it and obtain approval covering the change.
3. When existing progress conditions are met and the user authorizes proceeding, continue the remaining verified → commit → committed → push → pushed steps, then prepare and publish the completed report. That decision covers commit, push and completed publication together; no repeated report approval is needed.
4. If conditions remain unmet and the user chooses publication of the current state and an end to follow-up, publish the exact held candidate they approved with next=null. Record their actual decision and remaining work in the draft; a resulting candidate change follows step 2. The same feedback can be the publication approval, without a separate confirmation question. Use the [recording authorization](recording.md) fields to bind that decision to the candidate. Approval never turns failed/unrun checks into passes or overrides host permissions, prohibited Git actions or stale execution bases.

With no feedback, including a dispatcher-opened session, leave the draft, state and worktree intact and end with “보류 중, 게시 없음”, paths and instructions to invoke relay-implement again or use relay-dispatch go --resume. Never substitute an empty comment or automatic publication. A published held record has next=null, so dispatcher closes the tracked session without declaring task success or launching a successor; an unpublished hold must not be described as a remotely observed completion.

Resume the same run with its stored draft, status, failure and holds. Preserve completed stages and original requests: push failure after commit resumes push; publication failure after push resumes publication. A held record later completed updates the same comment at version+1. Do not introduce a second commit cycle for an already pushed run; new work or a changed basis requires an explicitly requested separate execution as applicable. An active user hold still stops the host even if helper state already meets the complete classification; holds is history, not a machine gate.

## Reports

Build the report from templates/implementation.md and run facts. With no hold, implementation → required checks → verified → commit → committed → push → pushed → prepare → publish proceeds under the invocation without an approval wait. Choose the report recommendation using the [common next-step contract](next-step.md); the existing complete/held classification and publication authority above remain unchanged.

[Recording](recording.md) defines complete/held classification and publication inputs. Before publishing, honor active user holds and recheck local files. If files changed after prepare, record failure and reprepare. Keep the original request and authorization when recovering an uncertain publication.

For major drift, immediately record a drift event and report reasons, impact and evidence in the conversation. Include it in the same comment at the completed or explicitly approved held publication point. Drift alone is not an approval gate; leave decision as 사람 판단 전 unless the user actually decides. Do not rewrite the plan.

After local-state loss, inspect returns remote run records. Compare their recorded path/branch/SHA with actual Git state. Use run input {"action":"restore","run_id":"recorded-id","execution_authorized":true,"path":"verified-local-worktree"}. The helper verifies the branch, recorded commit, file signature and remote SHA when applicable before restoring. If the recorded status predates an actual commit, inspect and resolve that gap instead of redoing code. Missing evidence requires clarification of the branch/resume point rather than starting a duplicate run.

Finish with verification, commit, remote SHA, execution URL, path, drift and incomplete checks. PR creation/merge, deployment and worktree deletion are outside this workflow.

## Execution basis

The registry declares baseline_sets formal=[intent,spec,plan], brief=[brief]. The shared baselines resolver serves inspect, begin, checkpoints and the final handoff; empty requires never bypasses execution checks. For a new execution with no option, a brief without a plan selects brief even when intent/spec exist; inspect retains those documents as reference context. Without brief, formal documents select formal and require the complete current intent/spec/plan chain. A present plan and brief require --basis formal|brief even when the plan is stale or missing parents. Never treat an invalid plan as absent to select brief automatically. Malformed metadata and missing/replaced known documents retain their collection/conflict checks. No documents blocks execution. An explicit selection checks that path and never falls back.

New runs pin basis={kind,parents,proof} and basis_summary. Formal proof is plan and keeps plan_summary; brief proof is brief and never populates plan_summary. Mixed parents or conflicting basis/proof are rejected.

Resume keeps its basis even if another path later appears. Conflicting basis options fail before returning an active run or restoring it. begin/prepared/verified/committed/pushed and implement→pr validate selected references and parents. Unrelated valid documents or issue-body edits do not stale the selection. Restore reconciles historical Git evidence; it does not authorize new effects against stale documents. Report drift/failure with historical parents. A changed approved basis requires an explicitly requested separate execution after reviewing the resume point.

Code drift remains in the same authorized run with required revalidation. Changed content cannot reuse verified_tree. Resume only unfinished push/publication after a commit, with the original run/request IDs. Major drift is reported promptly without an automatic reapproval or formal-path conversion gate. Read the entire selected proof, including manual checks and explicit exclusions, and record every required check before completion.

Investigation-derived patches remain experimental until reviewed against the approved formal/brief baseline. Preserve origin and verify chosen portions on current code. Do not automatically copy an investigation worktree or treat its outcome as implementation verification.
