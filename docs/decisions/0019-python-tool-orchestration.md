# 0019: One-shot Python tool orchestration

Status: accepted

## Context

Models need to chain session tools and filter intermediate results without a
model round trip for every call. Cade already ships Python and shell process
confinement. A second language runtime would add installation and bridge ownership
without a current requirement for tool-only access to external resources.

## Decision

Expose `run_code` beside existing tools. Require `code` and a nonblank
`description`; tool cards display the description instead of the program body.
Each call executes an async Python
function body in a fresh process under the shell's OS sandbox. Tool proxies use
the shared executor and return `{content, metadata}`; failures raise
`RuntimeError`. Nested calls execute serially. The worker retains no state.

Keep credentials out of the inherited environment. Bound tool-call control
messages to 1 MiB. Printed output and the JSON completion share a fixed 16 MiB
hard limit; completion frames have a separate bound that admits this result size.
The optional `max_output_tokens` defaults to 10,000, estimated at four characters
per token. Above the budget, keep the head and tail and save complete rendered
text to a user-private temporary file. Return its path in the text and metadata.
The executor leaves this tool-owned preview intact rather than truncating it a
second time. A larger budget cannot bypass the hard output limit.

The optional `timeout` argument accepts positive finite seconds without a fixed maximum. Omission uses
the executor's default 120 seconds. The tool owns the whole-program timer and
passes the selected timeout to nested tools as their default. Tool execution and
nested approval waits count toward the total deadline. Reuse shell process
termination; cancel and await started nested work before the parent settles.
Do not replay failed programs.

The executor reports nested dispatch and outcome through a caller-owned callback.
Session owners append log-only `tool_execution` events, including parent and
child call identities, arguments, status, result text, and authorization outcome.
These events never become model messages. A missing outcome remains unknown.

Direct Python I/O has shell sandbox authority, without shell risk classification
or per-operation approvals. Build allows programs, Ask approves complete programs,
and Plan and read-only sessions deny them. Nested tools retain independent
policy checks and recovery ownership. Direct writes are not snapshot-tracked.

## Consequences

No new dependency, persistent kernel, language-provider interface, or alternative
agent loop is required. Ordinary tool declarations also describe the script's
call arguments. Runtime memory and escaped descendants retain the existing
platform limitations; this is not a tool-only or multi-tenant sandbox.

`tests/test_run_code.py` covers real tool chaining, selective model output,
permission and schema failures, nested audit, fresh state, environment filtering,
output budgets, head/tail retention, spill files, hard limits, cancellation,
native sandbox scope, and tracked-write undo.
