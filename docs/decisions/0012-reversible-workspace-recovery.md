# 0012: Coordinate reversible conversation and workspace recovery

Status: accepted

## Context

History navigation preserves conversation branches but does not restore files.
The existing file journal has no turn ownership or redo history. OpenCode's
staged revert restores an entire range on redo, whereas Cade needs stepwise LIFO
undo/redo. Claude Code and Gemini CLI provide the reference for selecting a user
prompt and choosing conversation, code, or both.

## Decision

Use log-only checkpoint and restore facts. Conversation restore changes ancestry and retains
all abandoned facts. Copying a session omits workspace recovery controls and
never imports another session's workspace undo stack.

Reuse the native content-addressed snapshot store. Retain both file images and
associate captured mutations with the root user turn, including synchronous
subagents. A queued input gets its checkpoint only when execution starts.
Steering belongs to the current turn. Tracking covers registered file mutations,
not arbitrary shell scripts, directory trees, or external side effects.

A workspace recovery coordinator owns persistent action history and transaction
recovery. A normal root turn is one action; explicit rewind and revert are also
single actions. Undo and redo move one action at a time. New actions discard the
active redo chain without deleting old conversation facts. Snapshot contents are
immutable; action records reference their hashes rather than copying bytes.

Serialize cooperating workspace writers. Before recovery, validate the complete
plan, expected current file states, payloads, paths, and conversation targets.
Persist the plan and both images before touching a file. Apply per-file atomic
writes, then commit conversation position and the action journal. Recovery rolls
an interrupted plan forward idempotently, but stops on a third, externally edited
state. No new agent writes are allowed while recovery remains unresolved.

Rewind selects the point before a user prompt and offers conversation, code, or
both. Revert restores a selected turn range and file subset without trimming
conversation. Idle double Esc opens rewind; active Esc still cancels work.
Code-only restoration adds an audited request-context notice rather than an
invented conversation message.

## Consequences

Recovery works without Git and leaves the user's index and commits untouched.
Conflicts fail before writes; multi-file I/O failures remain explicitly pending
until recovered. Symlinks, hard links, protected metadata, corrupt payloads, and
unknown mutation outcomes are not automatically restored. External editors do
not honor the workspace lock and must be stopped during recovery.

Copied turns without owned checkpoints remain conversation-restorable only.
Checkpoint ownership, LIFO behavior, restart, conflict preflight, interruption,
subagents, and real TUI input require regression coverage before shipping.
