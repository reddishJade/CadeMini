# Relationship to the earlier Cade project

This repository was previously named Cade Mini. It is separate from the earlier
Cade implementation. It develops, tests, and releases only this Cade
implementation and does not provide compatibility with the earlier project's
Python package, commands, settings, credentials, or session formats.

This project originated as a log-first implementation alongside the earlier
Cade. Its provider adapters and process execution code were brought into the
`cade` package when it became independent. They are no longer shared runtime
dependencies.

## Product boundary

Cade provides a terminal TUI and one-shot execution. Its core separates
append-only session facts, deterministic projection, request admission, and the
run loop.

Intentional limits include:

- No plugin runtime, RPC, or SDK.
- No artifact store or parallel tool scheduler.
- No permanent approval grants; durable rules belong in project config.
- No Windows OS sandbox; Linux uses bubblewrap and macOS uses Seatbelt.

The [historical parity acceptance](../parity.md) records workflows tested
before separation. It is not a promise of future feature parity. See the
[code map](code-map.md) for the current package and ownership boundaries.
