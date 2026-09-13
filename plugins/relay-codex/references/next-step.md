# Common next-step contract

Use this contract when choosing and submitting a Relay artifact's next step. Follow the
current skill's own contract for its execution, publication and recovery procedures.

## What to submit

Submit exactly these two fields as `next_step`:

```json
{"next":"design","reason":"확정된 작업 범위를 구체적인 설계로 정리한다."}
```

- `next`: a skill allowed from the current stage by the table below, or explicit null.
- `reason`: one nonempty line of plain text explaining the choice, without surrounding whitespace.

Do not add fields for targets, arguments, status or permissions. Store skill names without
host call prefixes; resolve target numbers and invocation arguments from the user's request
and verified context when the next skill runs.

The displayed labels are always Korean: "다음 단계 / 사유", with null shown as "없음".
Null ends automatic progress; it does not mean the task succeeded or every issue is resolved.
A missing or malformed next is "unreadable", not null, and must not be silently treated as null.

## Choosing the next step

Choose the next task assuming the candidate is successfully published. Waiting for approval
to publish that candidate does not by itself require null. Every stage may choose null;
non-null choices are limited to:

| Current stage | Allowed next |
| --- | --- |
| open | intent, brief |
| intent | design, brief |
| design | plan, brief |
| plan | implement |
| brief | implement |
| implement | pr |
| pr | review |
| review | null only |
| investigate | intent, design, brief |

An allowed transition still needs sufficient current scope, evidence and verification.
Missing prerequisites, self-recommendations, transitions outside the table and a user's
instruction to hold or end follow-up require null. Explain remaining work or the needed
human judgment in reason and keep the relevant detail in the body.

- Review always uses null, in reviewer and author modes, with or without code application
  or unresolved findings. Keep unresolved findings visible in the review.
- A completed implementation may suggest pr after the required implementation, checks and
  push are complete. A held implementation uses null.
- A resolved investigation may suggest brief for a bounded fix, intent for a goal or scope
  decision, or design when a current, sufficient approved intent exists. Assess the
  investigation evidence's applicability to current code. Held/no_change results use null.
- Intent/design may suggest brief when the scope is clear enough for the shorter path,
  never to bypass unresolved questions.

These judgments belong to the host. The helper's transition check does not establish that
the evidence is sufficient, and the next skill still checks its own entry conditions.

## Preparing the candidate

Submit `next_step` explicitly. The helper generates the readable lines and the
`<!-- relay:next {...} -->` machine comment; do not write that block into the draft yourself.
Ordinary prose may quote the syntax, but only the helper-generated block at the artifact's
anchor is its actual next step.

Choose an allowed value initially. The helper normalizes a well-formed forbidden transition
to null before freezing the candidate and preserves the submitted reason alongside the
blocked transition. Malformed input remains an error. Held implementation and held/no_change
investigation submissions must already use null; their non-null inputs are rejected before
normalization. An investigation's submitted next must match its saved conclusion after the
same policy check; see [investigation](investigation.md).

Inspect the returned next and reason and show the complete rendered candidate. If either
field changes, prepare again and apply the current skill's authorization rules to the revised
candidate. Do not reuse an old candidate hash for changed content. Review result substitutions
apply only to the detail body, never to next or reason; see [review](review.md).

## Suggestion is not authorization

Next adds no execution or publication authority. Follow the user's instructions and the
current skill's approval and execution rules. Check whether existing authorization covers
the revised scope; do not request the same approval again when it already covers the change.

Manual calls and configured dispatcher launches follow their respective execution rules.
The dispatcher applies the same transition policy before starting a suggested task; its
settings do not replace the skill's own authorization requirements. See [dispatcher](../docs/dispatcher.md).

If nothing is published, publication fails or only part completes, report the actual state,
confirmed results and remaining decisions. Do not create an empty comment merely to record
next, or append a failure notice to a comment that already succeeded.

For uncertain writes, preserve the original request, authorization and confirmed URLs and
follow the current skill's recovery procedure. Never repeat an unconfirmed POST or change
next to bypass recovery. Do not rewrite historical artifacts merely to update their next
metadata. A forbidden historical recommendation needs a new candidate before a new write;
reconcile any earlier write with its original request first.
