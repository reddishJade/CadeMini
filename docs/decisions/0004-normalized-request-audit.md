# 0004: Persist normalized request inputs

Status: accepted

## Context

Digests and admission counts cannot reconstruct a request. Auditing an adapter's
HTTP body would couple the loop to each transport and could capture authentication.

## Decision

The request event includes a `snapshot` containing the
messages, tool definitions, non-authentication stream options, complete budget,
and admission report passed to the provider interface. Save it before calling the
provider, including each retry. Projection ignores the audit as before.

Exclude API keys, headers, cancellation signals, and callbacks. The snapshot is a
normalized provider-interface input, not a captured HTTP body. Adapter defaults,
server behavior, and authentication must be supplied separately for replay.
Request text, tool schemas, and user-supplied option metadata are not redacted.
Treat session files as sensitive. On POSIX, create logs with mode `0600` and
new session directories with `0700`; opening an existing log tightens its mode
without changing directory permissions. Windows inherits directory ACLs.

## Consequences

Audits increase session size with each request. Compaction bounds model context,
not log storage. `Request.as_record()` is pure and returns an isolated JSON tree.
`test_request.py` checks contents and credential exclusions;
`test_loop.py` checks audit-before-send with the provider inputs;
`test_log.py` checks nested audit validation.
