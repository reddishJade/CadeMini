# 0011: Persist same-session history navigation

Status: accepted

## Context

Pi separates same-session tree navigation from fork and clone. Mini already has
parent-linked facts but follows the last physical record. Changing an in-memory
cursor would lose the selected position on restart. A mutable sidecar creates a
second authority, and copying a session cannot preserve same-session semantics.

The reference behavior comes from Pi's session manager, `navigateTree`, and
interactive tree/fork/clone handlers. This increment implements direct navigation
without an automatic abandoned-branch summary. Workspace rollback is separate.

## Decision

Append a log-only `branch` event containing `selected_id`. Its parent points to
the selected user's parent, or to the selected non-user entry itself. The event
becomes the physical head. Validate targets and parent consistency against earlier
records before writing. Projection follows the head's ancestry and ignores branch
controls. Old siblings remain navigable in the same file.

Derive the editor draft from the latest active navigation until a new user or
assistant fact consumes it. There is no duplicate user record or cursor sidecar.
List sessions from their complete history so navigation before the first user does
not hide an existing saved conversation.

Navigation and copying acquire the session run lock. Fork can copy the path before
an inactive input. Clone copies only the active path. Copies omit branch controls,
receive independent IDs, remap context cuts, retain timestamps,
and write one snapshot. Empty copies retain deferred persistence.

## Consequences

Navigation survives restart without file rollback or an extra model request.

[Storage](../../src/cade_mini/core/log.py),
[tree projection](../../src/cade_mini/core/history.py), and
[interaction commands](../../src/cade_mini/hosts/interaction.py) own the behavior.
[Core tests](../../tests/test_mini_session_history.py) cover record preservation,
references, locks, alternate branches, and copies.
[TUI E2E tests](../../tests/test_mini_history_e2e.py) cover real input, restart,
request isolation, inactive forks, and independent sessions.
