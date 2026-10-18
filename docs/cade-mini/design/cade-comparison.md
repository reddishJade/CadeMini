# Relationship to Cade

Cade and Cade Mini are separate projects. This repository develops, tests, and
releases only Cade Mini. It does not provide compatibility with Cade's Python
package, commands, settings, credentials, or session formats.

Mini originated as a log-first implementation alongside Cade. Its provider
adapters and process execution code were brought into the `cade_mini` package
when it became independent. They are no longer shared runtime dependencies.

## Mini's product boundary

Mini provides a terminal REPL, one-shot execution, and a local web host. Its
core separates append-only session facts, deterministic projection, request
admission, and the run loop.

Intentional limits include:

- No full-screen TUI, plugin runtime, RPC, or SDK.
- No artifact store or parallel tool scheduler.
- No permanent approval grants; durable rules belong in project config.
- No OS sandbox; permissions and shell classification are application checks.

The [historical parity acceptance](../parity.md) records workflows tested
before separation. It is not a promise of future feature parity. See the
[code map](code-map.md) for the current package and ownership boundaries.
