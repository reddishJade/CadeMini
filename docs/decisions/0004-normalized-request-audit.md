# 0004: Persist normalized request inputs

Status: accepted

## Context

Digests and admission counts cannot reconstruct a request. Auditing an adapter's
HTTP body would couple the loop to each transport and could capture authentication.

## Decision

Write session envelope v2. The request event adds a `snapshot` containing the
messages, tool definitions, non-authentication stream options, complete budget,
and admission report passed to the provider interface. Save it before calling the
provider, including each retry. Projection ignores the audit as before.

Exclude API keys, headers, cancellation signals, and callbacks. The snapshot is a
normalized provider-interface input, not a captured HTTP body. Adapter defaults,
server behavior, and authentication must be supplied separately for replay.
Request text, tool schemas, and user-supplied option metadata are not redacted.
Treat session files as sensitive.

This is an additive format change. Read v1 and v2 envelopes, append only v2, and
never rewrite old facts. Mixed-version branches are supported. Reject unknown
future versions. No migration command or in-place upgrade is needed. Old readers
reject v2, so keep an old-session backup before resuming if rollback is needed.

## Consequences

Audits increase session size with each request. Compaction bounds model context,
not log storage. `Request.as_record()` is pure and returns an isolated JSON tree.
`test_mini_request.py` checks contents and credential exclusions;
`test_mini_loop.py` checks audit-before-send with the provider inputs;
`test_mini_log.py` checks v1 resume and future-version rejection.
