# 0006: Validate persisted event payloads

Status: accepted

## Context

The envelope type alone permits invalid type-specific data. Projection catches
some errors only after the record is already durable, and ignores malformed
audit payloads altogether.

## Decision

`core/events.py` defines strict Pydantic schemas for every v2 event payload.
Validate before append can repair or modify a file, and validate v2 payloads on
read. Reject missing required fields, unknown fields, wrong primitive types,
invalid statuses, duplicate calls within an assistant event, and non-finite JSON
numbers. Validate nested request messages, tools, options, budgets, and reports.
Do not inject defaults into persisted data. Cross-event pairing and context-cut
references remain projection responsibilities.

This enforces the existing closed v2 format, rather than adding a new payload.
V1 reads retain legacy validation. Branch copies preserve each source event's
version, so a v1 audit is never relabeled as a v2 snapshot. Do not infer or rewrite
historical request contents.

## Consequences

Invalid new facts fail before affecting the file or cached head. Malformed v2
records from earlier unchecked writers now fail on read; fix their producer and
inspect a backup rather than silently repairing complete records. Error messages
identify the event type without including the rejected data.

`test_mini_log.py` covers every event category, nested snapshot errors, rejection
before torn-tail repair, v2 read validation, and version-preserving legacy copies.
Existing loop, provider, compaction, and acceptance tests exercise valid producers.
