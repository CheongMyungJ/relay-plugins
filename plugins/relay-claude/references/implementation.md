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

Develop with the host's tools following repo instructions and approved plan. Record drift as discovered. The helper's run command records these events:

- {"action":"drift","run_id":"...","entry":{"planned":"...","actual":"...","reason":"...","impact":"...","severity":"major","evidence":"..."}}
- {"action":"verified","run_id":"...","tests":[{"command":"actual check","exit_code":0,"evidence":"actual output summary or local log path"}]}
- {"action":"committed","run_id":"..."}
- {"action":"pushed","run_id":"..."}
- {"action":"failure","run_id":"...","reason":"specific incomplete step"}

Run all required tests before verified. Include every required check; do not omit a failing or unavailable check to advance. Do not fabricate evidence: helper records host-observed results, it does not run the tests. verified pins file content; code changes invalidate it. Recheck upstream before committing and pushing. Only stage files belonging to the task. No blanket git add when user changes exist.

Commit with existing user identity; if absent, resolve identity normally. Use ordinary push to the task branch. Never force push or push directly to a protected/default branch as a workaround. Register committed and pushed facts after the actual operation; remote SHA must equal the recorded commit.

If a required check cannot run, record failure and unfinished work; do not make a completion commit/push. The user may explicitly authorize a progress checkpoint separately. Commit and push errors preserve completed stages; resume push after commit or comment publication after successful push.

## Reports

Build an implementation report from templates/implementation.md and run facts. prepare with run_id, body_file and the required next_step, then publish with matching execution_authorized/run_id/request_id/hash. The published record returns the verified next step; see [next step](next-step.md). For major drift, publish promptly during the run and keep updating that same comment. Drift alone is not an approval gate; leave decision as 사람 판단 전 unless the user actually decides. Do not rewrite the plan.

After local-state loss, inspect returns remote run records. Compare their recorded path/branch/SHA with actual Git state. Use run input {"action":"restore","run_id":"recorded-id","execution_authorized":true,"path":"verified-local-worktree"}. The helper verifies the branch, recorded commit, file signature and remote SHA when applicable before restoring. If the recorded status predates an actual commit, inspect and resolve that gap instead of redoing code. Missing evidence requires clarification of the branch/resume point rather than starting a duplicate run.

Finish with verification, commit, remote SHA, execution URL, path, drift and incomplete checks. PR creation/merge, deployment and worktree deletion are outside this workflow.

## Execution basis

The registry declares baseline_sets formal=[intent,spec,plan], brief=[brief]. The shared baselines resolver serves inspect, begin, checkpoints and the final handoff; empty requires never bypasses execution checks. For a new execution with no option, a brief without a plan selects brief even when intent/spec exist; inspect retains those documents as reference context. Without brief, formal documents select formal and require the complete current intent/spec/plan chain. A present plan and brief require --basis formal|brief even when the plan is stale or missing parents. Never treat an invalid plan as absent to select brief automatically. Malformed metadata and missing/replaced known documents retain their collection/conflict checks. No documents blocks execution. An explicit selection checks that path and never falls back.

New runs pin basis={kind,parents,proof} and basis_summary. Formal proof is plan and keeps plan_summary; brief proof is brief and never populates plan_summary. Mixed parents or conflicting basis/proof are rejected.

Resume keeps its basis even if another path later appears. Conflicting basis options fail before returning an active run or restoring it. begin/prepared/verified/committed/pushed and implement→pr validate selected references and parents. Unrelated valid documents or issue-body edits do not stale the selection. Restore reconciles historical Git evidence; it does not authorize new effects against stale documents. Report drift/failure with historical parents. A changed approved basis requires an explicitly requested separate execution after reviewing the resume point.

Code drift remains in the same authorized run with required revalidation. Changed content cannot reuse verified_tree. Resume only unfinished push/publication after a commit, with the original run/request IDs. Major drift is reported promptly without an automatic reapproval or formal-path conversion gate. Read the entire selected proof, including manual checks and explicit exclusions, and record every required check before completion.

Investigation-derived patches remain experimental until reviewed against the approved formal/brief baseline. Preserve origin and verify chosen portions on current code. Do not automatically copy an investigation worktree or treat its outcome as implementation verification.
