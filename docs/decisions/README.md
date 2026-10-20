# Decision records

Decision records capture choices that are expensive to reverse: the session
schema, public config, tool contracts, and host interfaces.

Each record has a number, a status, the context, the decision, and the
consequences. Keep records short and link to the code and tests that enforce
them.

## Records

- [0001: Session log is the source of truth](0001-session-log-source-of-truth.md)
- [0002: Reject concurrent session runs](0002-session-run-lock.md)
- [0003: Verify file state before undo](0003-snapshot-conflict-detection.md)
- [0004: Persist normalized request inputs](0004-normalized-request-audit.md)
- [0005: Keep request admission lossless](0005-explicit-context-reduction.md)
- [0006: Validate persisted event payloads](0006-validate-event-payloads.md)
- [0007: The AI layer supports only the Responses protocol](0007-responses-only-provider.md)
- [0008: Use Sign in with ChatGPT for ChatGPT plan usage](0008-siwc-login.md)

## Adding a record

1. Copy `TEMPLATE.md` to the next number.
2. Fill in status, context, decision, and consequences.
3. Add it to the list above.
4. Link the record from the design or guide page it affects.

Decision records are excluded from the published guide manifest. They are
reviewed with the change that adopts them.
