# Tools and policy

## Contracts

A tool declares a name, description, JSON schema, and async `execute`. The loop
records content and the error flag; tools do not append session facts.

`core.permissions.Policy.check` returns a `PermissionCheck` with a decision
(`allow`, `deny`, or `ask`), reason, and `ExecutionScope`. `authorize` resolves
only asks using an `ApprovalRequest` and `ApprovalResult`.
The loop and CLI direct-shell path use the same authorization function.

`WorkspacePolicy` applies mode constraints and protected paths before explicit
rules, grants, and mode defaults. Matching rules use deny > ask > allow.
Subagent modes cannot expand parent permissions. See
[Tools and security](../tools-security.md) for user-facing semantics.

## Execution

The loop installs the authorized scope in a context variable only while the
specific tool runs. This scope is preserved across async tasks and worker
threads and reset afterward. It never changes the default workspace or grants.

The CLI constructs a `PlatformSandbox` with the project root, private temp,
network setting, and protected paths. `SubprocessShell` wraps commands before
spawning. File tools use the same backend through `tools._worker`; snapshot
storage remains host-owned. External file changes have no workspace snapshot.
Cancellation signals reach the subprocess launcher, and tools wait for process
termination before returning or cleaning up request files.

Linux uses bubblewrap, macOS uses Seatbelt, and Windows runs directly. Backend
errors become tool errors, not unsandboxed retries. Hooks and MCP server
processes are trusted host extensions outside this boundary.

## Reviewer

`AutoReviewer` receives the authorization reason, exact arguments, retained
conversation, ancestor conversations, and context constraints. It uses a
separate tool-free request. Oversized input is rejected rather than silently
truncated. Output is a strict object with `allowed: bool` and `reason: str`.
Rejections and failures return their reasons to the model as tool errors.

## Tests

- `test_mini_policy.py`: mode constraints, precedence, and write scope.
- `test_mini_grants.py`: exact grants and scope isolation.
- `test_mini_review.py`: strict responses and model-visible denial reasons.
- `test_mini_subagents.py`: child permission ceilings.
- `test_mini_sandbox.py`: real OS file/network isolation, worker integration,
  cancellation, Windows bypass, and unavailable backends.
