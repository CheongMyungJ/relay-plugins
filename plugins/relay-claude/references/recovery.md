# Recovery

Read the section a helper error names (`Recovery: references/recovery.md#<anchor>`, relative to the plugin root) only when it occurs. Preserve files and report the concrete failed step. New sessions recover remote current documents and runs; missing local drafts are not reconstructable.

## Uncertain

After any ambiguous write, rerun publish with the same authorization. Do not manufacture a new request to evade uncertainty. If a creation remains unconfirmed, inspect GitHub and resolve it with the user; never automatically create again. An update can be retried only against its unchanged expected baseline. Preserve the original authorization and request after an uncertain write; never change them to evade recovery. For kb and kb-sync, a retry with the same authorization completes the missing step, recovers a matching result, or stops with `conflict`/`uncertain`; nothing is force-pushed, re-created or re-posted automatically.

## Locked

One local work copy is serialized by its lock. If the owner process has ended, inspect actual state and remote result before removing that specific stale lock.

## Conflict

A deleted/forbidden comment is an error, not grounds to create another. A later prepare also rejects a missing or replaced known document. External edits and older retries cannot overwrite newer content. Third-party edits of planned KB files are conflicts. A cursor conflict means the KB, SHA or query changed: query again from the first request.

## Stale

A document, snapshot or basis changed after it was pinned. Never bypass it with a new request or another basis. Revise the document through its stage; after a user-requested workspace refresh, assess the documents that still apply (see [workflow](workflow.md)). Implementation and review follow their own rules in [implementation](implementation.md) and [review](review.md).

## Legacy

Without Relay metadata, comment documents are not automatically assumed approved. inspect exposes raw comment body and numeric target ID. The issue body cannot be bound or adopted as intent. After user confirmation of a comment, send inspect the same input plus legacy_confirmed:true and a legacy mapping:

```json relay:inspect
{"stage":"design","raw":"1","cwd":"<abs-path>","legacy_confirmed":true,"legacy":{"123":{"kind":"intent","version":2,"hash":"<hash>","parents":{}}}}
```

For spec/plan use exact numeric target IDs, their confirmed versions/hashes and parents mapping. A parent reference is `{"target":"123","version":1,"hash":"<hash>"}`. The hash is `relay_core.artifacts.digest` of the exact remote body. Bind parents to the current confirmed comment records.

Never rewrite a remote body merely to introduce metadata. On a later approved revision, pass adopt:true to prepare only after reviewing the full replacement, preserving unrelated material in the draft. Changing a bound legacy body invalidates it until reconfirmed.
