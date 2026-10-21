# 0002: One huge tool result exhausted the request budget

Date: 2026-10-18

Status: resolved

## Impact

A single tool result larger than the input budget could make a request
impossible. The request builder protected the most recent four tool results, so
a lone huge result was never replaced, and the run ended with `context_error`.

## Timeline

- A tool returned a result larger than the budget.
- Admission protected the result because it was among the latest four.
- Turn dropping removed old turns but not the current turn's huge result.
- The request remained over budget and the run failed.

## Root cause

The pruning policy treated "recent" as always protected. Protection is a
heuristic for preserving working evidence, not a hard invariant. With only one
result, the protected set was the entire candidate set.

## Fix

Admission now prunes in passes:

1. Replace unprotected results, oldest first.
2. If still over, replace protected results oldest first.
3. If still over, replace the newest result as a last resort.

Every replacement points to the history tool, so the evidence remains
retrievable. A regression test covers a single huge result.

## Verification

`test_request.py::test_build_prunes_a_single_huge_tool_result` passes and
the resulting request is under budget.

## Follow-up

Keep the history tool available whenever pruning can remove the newest result.
See [Projection and request](../subsystems/projection-request.md).
