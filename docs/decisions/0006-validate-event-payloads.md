# 0006: Validate persisted event payloads

Status: accepted

## Context

The envelope type alone permits invalid type-specific data. Projection catches
some errors only after the record is already durable, and ignores malformed
audit payloads altogether.

## Decision

`core/events.py` defines strict Pydantic schemas for every event payload.
Validate before append can repair or modify a file, and validate payloads on
read. Reject missing required fields, unknown fields, wrong primitive types,
invalid statuses, duplicate calls within an assistant event, and non-finite JSON
numbers. Validate nested request messages, tools, options, budgets, and reports.
Do not inject defaults into persisted data. Cross-event pairing and context-cut
references remain projection responsibilities.

Do not infer or rewrite historical request contents.

## Consequences

Invalid new facts fail before affecting the file or cached head. Malformed
records from earlier unchecked writers now fail on read; fix their producer and
inspect a backup rather than silently repairing complete records. Error messages
identify the event type without including the rejected data.

`test_log.py` covers every event category, nested snapshot errors, rejection
before torn-tail repair, and read validation.
Existing loop, provider, compaction, and acceptance tests exercise valid producers.
