# Server sessions

Follow this file only when the environment variable `RELAY_SERVER_CONTEXT` is set: relay-server started this CLI and a person works with you through its web terminal.

- Every approval you record names the user `relay-server`: `authorization.json` of documents, held reports and investigations, the review `decision.user`, and the kb publish `user`. The helper refuses any other name in a server session. The server maps the approval to whoever held terminal input when the helper recorded it; never ask for or write the person's identity.
- Before asking for approval, print the complete final candidate (`review.md`) and its `change.diff` as terminal text. Split long text into numbered, ordered parts (`[1/3]`, `[2/3]`, …) and print every part. Files and absolute paths on the server are not visible to the person, so a link or path never replaces the text.
- An approval error saying no web terminal holds input means nothing was written. Ask the person to reconnect, show the candidate again when it changed, and publish with the same request once they confirm. Never prepare a new request to get around it.
- Retrying the same request reuses its first recorded approval; do not ask again for an unchanged candidate. A changed candidate needs a new approval.
- Do not add `--watch` or change labels and assignees yourself; the server marks new PRs.
- `server_receipt` fields in helper results are for the server. A receipt that failed to record does not undo the publication; report it with the result.
