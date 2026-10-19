# Upgrade guide

This page records changes that require action from an existing user or
automation.

## Sign in with ChatGPT

The `openai-codex` provider ID and `openai_codex` transport are removed. Use
`chatgpt` for ChatGPT plan usage. The new provider uses Sign in with ChatGPT
(SIWC) instead of the legacy Codex OAuth flow. Update `cade-mini.json`, CLI
flags, and scripts that name the old provider, then run `cade-mini login` again.
Saved `openai-codex` credentials and the old device-code method are not read or
migrated. On a headless host, complete the login locally, copy `auth.json`, and
let the remote host create its own host ID on the next login. Or use an OpenAI
API key. See [decision 0008](../decisions/0008-siwc-login.md).

## V2 payload validation

V2 events now enforce their closed payload schemas on write and read. Invalid
complete records are rejected instead of being ignored or silently repaired.
V1 keeps its legacy reading rules. Branch copies preserve each event's version.
See [decision 0006](../decisions/0006-validate-event-payloads.md).

## Explicit context reduction

Request construction no longer trims tool outputs or old turns. The loop first
tries compaction, then a logged window fallback. If the current task, instructions,
or tools still exceed the budget, the run returns `context_error`. Reduce the
input or start a new task; no hidden request-only truncation occurs.

## Session v2 and snapshot v2

New session facts use v2. This reader also accepts v1 and mixed-version branches.
Resume an old session normally; its existing bytes remain unchanged. Request
audits now contain normalized request inputs, excluding authentication and runtime
objects. See [decision 0004](../decisions/0004-normalized-request-audit.md).

Older releases reject sessions after v2 facts are appended. Back up `.cade-mini/`
before resuming if rollback is needed. Rollback uses that backup; do not remove
v2 records manually. No migration command is required.

Snapshot v2 records before/after file state. Old snapshots remain available for
manual inspection but cannot be undone automatically. New snapshots can be
recorded above them. See [Snapshots and goals](../snapshots-goals.md).

## Standalone project

Cade Mini now installs as `cade-mini`, with Python imports under `cade_mini`.
The old `cade` command and `cade.mini` namespace are removed. No compatibility
or migration tools are provided.

Authenticate again with `cade-mini login`. Credentials and skills use
`~/.cade-mini/`; project skills use `.cade-mini/skills/`. Mini no longer
reads `.cade` directories. This earlier packaging change retained schema v1.

## When an upgrade is needed

An upgrade entry is required when:

- the session schema version changes;
- a config key is renamed or removed;
- a CLI flag changes meaning;
- a tool name or parameter becomes incompatible;
- a persisted host-state file changes shape.

## Procedure for a future upgrade

1. Read the relevant decision record.
2. Back up `.cade-mini/` before migrating.
3. Run the documented migration command once.
4. Open a session and verify the projection and `history` tool.
5. Keep the old directory until the new version has passed a real task.

## Compatibility promise

Additive optional config fields are not breaking. A new event type is breaking
because the schema is closed. A removed tool or a changed parameter is breaking.
The change policy is in [Persistence](../persistence/change-policy.md).
