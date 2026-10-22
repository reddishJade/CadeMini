# 0013: Use checkpoint recovery for CLI undo

Status: accepted

## Context

CLI `--undo` restored one file snapshot while TUI `/undo` updated checkpoint
action history and conversation position. The CLI could change files without
updating that history, causing a later coordinated undo or redo to conflict.

## Decision

Keep `--undo`, but route it through `WorkspaceRecovery.step`, the same operation
used by TUI `/undo`. Select the latest saved session in the configured session
directory, or the session named by `--resume ID`. Reject sessions from another
workspace. Do not assemble providers or MCP connections for recovery.

Undo reverses one checkpoint action and preserves its redo history. Honor
read-only configuration and existing conflict and interruption handling. Do not
fall back to file-only undo when a snapshot has no owned checkpoint. Reject
combining `--undo` with a task.

## Consequences

The flag now restores conversation position and all tracked changes in one
action. This intentionally replaces its previous file-only behavior. Resume the
same session in the TUI to redo. Standalone legacy snapshots remain stored but
do not establish a recovery action.

`app.bootstrap.undo_session` owns CLI recovery assembly. `test_cli.py` verifies
latest and explicit session selection, application redo after CLI undo,
read-only enforcement, and rejection of foreign workspaces and unowned snapshots.
