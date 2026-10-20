# 0005: Keep request admission lossless

Status: accepted

## Context

Request-only pruning makes the visible conversation depend on both projection
boundaries and hidden budget heuristics. Retaining pruning as an optional fallback
would preserve that ambiguity.

## Decision

Remove request-stage tool-body replacement and turn dropping. `build()` converts
the complete projection plus explicit context and reports budget overflow. The
loop tries compaction first, then a window fallback expressed as a log event.
If the resulting request still exceeds the budget, return `context_error` without
sending it. Never substitute a partial request to make it fit.

Audit dropped-call and turn counters remain empty/zero; historical
audits retain their original values. The log format and projection semantics do
not change. Tool output capture limits and compaction summary-input limits are
separate, existing behaviors; this decision removes request admission pruning.

## Consequences

A large current-turn tool result may stop the run instead of being hidden.
Earlier evidence remains available through history retrieval. Tests in
`test_mini_request.py` protect complete over-budget inputs; loop and compaction
tests cover failed summary fallback and current-turn overflow.
