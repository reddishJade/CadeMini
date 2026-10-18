# Tools and policy

## Tool contract

A tool has a name, description, JSON parameter schema, and async execute. Results
carry content, an error flag, and optional metadata. The loop records results; a
tool never appends to the log.

## Built-in tools

- Workspace: `read`, `write`, `edit`, `glob`, `grep`, `bash`.
- Evidence and context: `history`, `skill`, `memory`, `goal`.
- Delegation: `subagent`.
- Remote: `mcp__<server>__<tool>`.

`write` and `edit` snapshot the target before changing it. `bash` snapshots
common single-command mutation targets.

## Policy

`WorkspacePolicy.check` returns allow, deny, or ask.

Hard checks run first:

- `read` and file writes must stay inside the workspace.
- `bash` workdir must stay inside the workspace.
- Unknown tools are denied.
- `read_only` denies writes, edits, shell, memory, and MCP.

Then:

1. A matching static rule decides.
2. A matching session grant allows.
3. The mode default decides.

Modes:

- `plan`: read tools and read-only shell; writes and mutations denied.
- `build`: writes allowed; read-only shell allowed; mutating shell asks.
- `act`: writes and shell ask.

## Shell classification

`classify_command` labels a command read-only, mutating, or unknown. Pipelines,
redirections, command lists, substitutions, and unknown binaries are unknown.
Plan mode denies unknown; build asks; act asks.

## Grants

`GrantStore` records a path or command for the exact tool. A grant is
process-local. `/permissions` lists and clears it. Durable policy belongs in
rules.

## Approvals

The host resolves ask. `SessionApprover` prompts a TTY with yes/no/always.
`AutoReviewer` makes one tool-free model call and requires an `ALLOW` prefix.
Non-interactive hosts deny unless `--yes` or a rule allows.

## Process isolation

There is no OS sandbox. Shell commands run with the Cade Mini process
permissions. The path boundary, mode, shell classification, and approvals are
the safety layer.

## Tests

`test_mini_tools.py`, `test_mini_policy.py`, `test_mini_grants.py`, and
`test_mini_shell.py`.
