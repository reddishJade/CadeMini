# Tools and security

## Workspace tools

- `read`: read a UTF-8 file with line numbers. Files are capped.
- `write`: write UTF-8 text and create parent directories.
- `edit`: replace one exact occurrence of old text.
- `glob`: list files matching a workspace glob.
- `grep`: search file contents with a regular expression.
- `bash`: run a shell command in the workspace.

## Context and extension tools

- `history`: retrieve evidence outside the current context boundary.
- `skill`: load the full body of an available `SKILL.md`.
- `memory`: append a durable fact to `MEMORY.md`.
- `goal`: read, set, complete, or clear the workspace goal.
- `subagent`: run one child task in its own session.
- `delegate`: run one or more child tasks sequentially, optionally in a mode.
- `mcp__<server>__<tool>`: tools discovered from a configured MCP server.

## Modes

`plan` exposes read, glob, grep, history, skill, and bash. Writes and edits are
hidden and denied. Only classified read-only shell commands are allowed.

`build` exposes all tools. Writes and edits are allowed. Read-only shell
commands are allowed; mutating or unknown commands ask.

`act` exposes all tools. Writes, edits, and shell commands ask.

`--read-only` denies writes, edits, shell commands, memory, and MCP regardless of
mode. It does not block reads, search, history, skills, or goal state.

## Rules and grants

Static rules live in `cade-mini.json`. They are evaluated last match first and
can match tool, path prefix, and command prefix. A hard path boundary always
wins.

A session grant is created when an approval answer is `a` (always). The grant
matches the same tool and the same path or command. `/permissions` lists grants;
`/permissions clear` removes them. Grants last for the process only. Use rules
for durable policy.

## Shell classification

The shell analyzer labels a command as `read_only`, `mutating`, or `unknown`.
It is deliberately conservative:

- `ls`, `cat`, `grep`, `rg`, `git status`, `git diff`, and similar commands are
  read-only.
- `rm`, `mv`, `cp`, `git commit`, `python`, and other mutating commands are
  mutating.
- Pipelines, redirections, command lists, substitutions, and unknown binaries
  are unknown.

Plan mode denies unknown commands. Build and act ask.

## Approvals

An ask is answered by:

- `--yes`: approve.
- `--auto-review`: ask the model for `ALLOW` or `DENY`; failures deny.
- Otherwise a TTY prompt: `y` once, `n` deny, `a` always for this exact call.
- Non-TTY without `--yes` or auto-review: deny.

## Process isolation

Cade Mini does not implement an OS sandbox. `bash` runs with the permissions of
the Cade Mini process. The path boundary, modes, shell classification, and
approvals are the safety layer; do not run untrusted commands or repositories
with `--yes`.

## Best practices

- Use `plan` for review and `build` for scoped edits.
- Prefer explicit rules over `--yes` in shared repositories.
- Keep commands simple so the classifier can reason about them.
- Use `--read-only` for inspection-only automation.
