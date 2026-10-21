# 0009: Separate execution modes, sandbox scope, and approval

Status: accepted

## Context

Build asked before normal tests and builds. Application path checks neither
confined shell effects nor allowed approved external file writes. Boolean
approval results lost Reviewer rejection reasons. Child mode overrides could
expand a parent's permissions.

## Decision

Use independent work modes (`plan`, `build`, `ask`), filesystem policies, and
network permission. Default to Build, workspace writes, and enabled networking.
Use existing bubblewrap on Linux and Seatbelt on macOS. Skip OS confinement on
Windows. Never silently downgrade a failed Linux/macOS sandbox.

Apply hard constraints first, then matching rules with deny > ask > allow,
then scoped grants and defaults. Explicit allows answer default asks, not
effective denies. Approvals grant only the requested operation and scope.
Children cannot expand the parent's work mode or other constraints.

Use structured approval requests and results. Reviewer runs only for asks;
its rejection or failure returns a reason to the acting model without human
fallback. File operations and shell commands share the platform execution
boundary. Host hooks and MCP processes remain trusted external extensions.

## Consequences

Old `act` configuration values must change to `ask`. Last-match rule ordering
is removed. Linux requires installed, usable bubblewrap. macOS requires usable
`sandbox-exec`. Windows retains application checks without OS isolation.

The implementation lives in [permissions](../../src/cade/core/permissions.py),
[policy](../../src/cade/safety/policy.py),
[sandbox](../../src/cade/execution_env/sandbox.py), and
[workspace tools](../../src/cade/tools/workspace.py).
[Policy tests](../../tests/test_policy.py),
[Reviewer tests](../../tests/test_review.py), and
[native sandbox tests](../../tests/test_sandbox.py) enforce the contracts.
