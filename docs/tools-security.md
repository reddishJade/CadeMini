# Tools and security

## Workspace tools

- `read`: read file text or attach an image. Output is capped at 2,000 lines or 50 KB.
- `write`: write UTF-8 text and create parent directories.
- `edit`: apply unique, non-overlapping replacements against the original file.
- `find`: find paths with a glob, respecting ignore rules.
- `ls`: list directory entries, including dotfiles.
- `grep`: search with regex or literal matching, case controls, and context lines.
- `bash`, `powershell`: run a shell command, optionally with a workdir and
  `additional_write_paths` that request extra permissions for this call.
- `question`: collect choices or custom answers; dismissal stops the turn.
- `websearch`, `webfetch`: search or retrieve web content. Both honor
  `network=false` on every platform.

Searches exclude `.cade`, including session logs and snapshot payloads.
See the [tool catalog](design/tool-catalog.md) for parameters and limits.

The workspace is the fixed project root, not the shell's current directory or
an alias for the entire home directory. Reads can use external paths, except
protected files. Workspace writes have snapshots; external writes do not.

## Modes

- `build` is the default. Normal edits, tests, builds, and shell pipelines run
  automatically. Detected destructive, privileged, publishing, or external-send
  operations require approval.
- `ask` allows classified read-only operations and asks before changes or
  unclassified shell commands.
- `plan` supports exploration and discussion, not implementation. Project
  changes, memory changes, goal changes, and MCP calls are denied. File tools
  can write under `<workspace>/.cade-plans/` only after approval for a
  user-requested plan. Implementation requires the user to switch modes.

Subagents inherit the parent's limits. A child mode can only narrow them.
`--read-only` and `--sandbox read-only` deny mutations while allowing classified
read-only shell commands. These constraints cannot be overridden by rules,
grants, `--yes`, or Reviewer approval.

## Rules and grants

Rules in `cade.json` match a tool, normalized path subtree, or command
prefix. All matching rules participate: `deny` wins over `ask`, which wins over
`allow`. An explicit allow overrides a default ask, not an effective deny.
Remove or change a deny rule to revoke it; an approval answer cannot do that.

A TTY answer of `a` creates a process-local session grant. File grants match the
same tool and path. Shell grants also match workdir and extra write paths;
MCP grants match the complete arguments. A grant can answer an ask but cannot
bypass a deny. `/permissions` lists grants; `/permissions clear` removes them.

Explicit reads and writes of `.env*` files (except `.env.example`) and common
home credential paths such as `~/.ssh`, `~/.aws`, and `~/.config/gh` require
approval on every use. Detected shell access follows the same rule, including
relative paths resolved from `workdir`. Sensitive approvals do not create or
reuse session grants, and explicit deny rules still apply. Read-only external
Git commands remain allowed; detected mutations of external repositories
require approval. Approval does not expand the OS sandbox's write scope.

## Approvals and Reviewer

Only an `ask` enters approval:

- `--yes` approves asks, not denials.
- `--auto-review` sends the pending action, authorization reason, retained
  conversation, parent conversations, and context constraints to a tool-free
  Reviewer call. The response must contain a strict boolean `allowed` and a
  non-empty `reason`.
- Otherwise, a TTY prompts for `y` once, `n` deny, or `a` always.
- Without an available approver, the action is denied.

Reviewer rejection, timeout, provider failure, and malformed responses become
error tool results with reasons. They return to the acting model without an
automatic human prompt. The model must respect the denial while adjusting its
approach. Reviewer does not inspect every ordinary development operation.

Shell classification and risk detection are conservative heuristics, not a
security boundary or a complete analysis of arbitrary scripts. They cannot
identify every destructive or external effect hidden inside programs.

## OS sandbox

Filesystem scope is independent of mode and approval:

| Policy | Scope |
| --- | --- |
| `workspace-write` (default) | Write in the project and private session temp directory |
| `read-only` | No regular file writes |
| `danger-full-access` | Skip OS confinement; explicit deny rules still apply |

Linux uses installed `bwrap` (bubblewrap). macOS uses system `sandbox-exec`
(Seatbelt). Missing backends and startup failures return tool errors; neither
platform silently retries without confinement. Windows skips OS confinement
and retains application policy and approval checks without extra prompts.

The CLI routes shell commands and file tools through the same execution
policy. On Linux/macOS, file tools execute in a fixed-operation sandboxed Python
worker. Approving an external file write grants that target for this call.
Shell `additional_write_paths` must name existing files or directories; a
directory grant includes its descendants. Changing workdir does not expand
write permissions. Approvals never silently grant full host access.

Network access is independent and enabled by default. `--no-network` disables
sandbox network access; approving network access does not grant external file
writes. Windows and `danger-full-access` do not provide OS-level network
restriction. The provider connection runs outside the tool sandbox.

On Linux/macOS, the CLI hides existing user and project `.cade` directories
from sandboxed commands and protects existing Git hooks and Git configuration
from writes. The home directory is not writable by default. Private temp and
common tool caches are redirected to a session directory, cleaned on exit.
Apart from hidden paths, the filesystem remains readable; this is not complete
secret isolation. Restrict credentials and exposed resources accordingly.

MCP server processes and host-managed state run outside the tool sandbox. Treat
their configuration and code as trusted. The sandbox is not a container or VM,
and it does not make an untrusted repository safe.

See [decision 0009](decisions/0009-execution-permissions.md) for the contract.
