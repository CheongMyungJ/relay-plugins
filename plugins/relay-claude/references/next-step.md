# Common next-step contract

Every published Relay artifact carries the next step inside itself. The two fields are part
of the candidate the user approves.

## The two fields

```json
{"next":"design","reason":"확정된 작업 범위를 구체적인 설계로 정리한다."}
```

`next` is one skill name registered in relay.json (open, intent, design, plan, brief,
implement, pr, review, investigate) or JSON null. Host call prefixes are never stored.
`reason` is one nonempty line of plain text explaining that choice. Nothing else belongs
in the object: no issue or PR number, repository, target, argument, status, permission or
reference. Displays render null as "없음", meaning there is no skill to suggest — it is not
a status code for success, hold or resume. The readable lines stay Korean whatever the
working language is.

Choose the next step from current evidence, not from a fixed successor table. The same
skill may be suggested again; that is not an instruction to retry automatically. No number
or argument is stored, so resolve the actual target from the user's input and verified
context at call time, and ask the person when it is unclear.

## Where it lives and what protects it

The helper renders the human-readable "다음 단계 / 사유" lines and the machine comment
`<!-- relay:next {...} -->` from the same object. Hosts submit the object; they never write
that pair into a draft, and a draft that already carries one at its anchor is rejected.

A document, PR body or review comment may quote this syntax in ordinary prose; only the
generated block at the anchor counts.

| Publication path | Comment position and protection |
| --- | --- |
| Issue body, document comments, implementation report, investigation result | Inside the existing relay:begin/end region, before relay:metadata. It is part of the body hash that document parents and approvals already cover. |
| PR body | At the end of the body, before the existing relay:pr-request marker, inside the existing request hash over the title, body and execution constraints. |
| Review general comments and inline replies | At the end of each posting unit, before its relay:review marker, inside the candidate hash and each unit's exact body hash. |

Changing `next` or `reason` changes the candidate. Prepare again and obtain a new approval;
an earlier approval or request hash never carries over. Review's allowed `{{commit}}` and
`{{verification}}` substitutions apply to the drafted detail only, never to these fields.

An artifact published before this contract has no relay:next comment. That absence is
"unreadable", which is distinct from `next` being null, and it never becomes null
automatically. Its body hash and parent references are unchanged, so those documents remain
usable; do not republish one merely to add metadata.

## Suggestion is not authorization

The next step assumes this publication succeeds. It grants nothing. Invoking the suggested
skill still requires the user's explicit call, a document publication still requires exact
candidate approval, and review changes still require the selected scope.

The conditions the recommendation depends on are checked where they were always checked, at
the entry point of the skill that actually runs: current, non-stale document parents; the
implementation's formal/brief basis and its required verification; the PR's real source,
base and permissions; review's selected scope and its pre-publication recheck; and the
current applicability of investigation evidence. If a next step does not hold up against
current evidence, refuse the candidate or submit a different, evidence-supported choice.
In particular, implementation → pr still requires that run's required checks to have
actually passed, and investigation → design still requires a valid, sufficient intent.

## No publication, partial success, uncertainty

An approval wait, a hold, a cancellation, a failure, or a review that posts nothing may end
with no published artifact at all. Report the current state and the judgment the person
needs to make, and do not present an unpublished or failed result as an executable success.
Never create an empty comment to record the exception, and never append a failure notice to
a comment that already succeeded.

When a write is uncertain or only part of a multi-unit publication succeeded, keep the
original request, its authorization and the confirmed URLs, and separate them from what is
unconfirmed. Reconcile that same original request; never repeat an unconfirmed POST and
never change the next step to route around it. One visible review comment is not evidence
that the whole selected scope completed.
