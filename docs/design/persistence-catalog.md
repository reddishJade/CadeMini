# Persistence catalog

Cade Mini separates durable history from host state. The session log is the
authority for what the model saw and what happened; the other stores have
narrower jobs.

| Artifact | Default location | Owner | Lifetime | Schema |
| --- | --- | --- | --- | --- |
| Session log | `.cade-mini/sessions/session-*.jsonl` | `SessionLog` | Project | Closed v1 |
| Snapshots | `.cade-mini/snapshots/` | `SnapshotStore` | Project | Local index |
| Goal | `.cade-mini/goal.json` | `GoalStore` | Project | One object |
| Working note | `NOTE.md` | Project | Project | Markdown |
| Memory | `MEMORY.md` | `MemoryTool` | Project | Markdown |
| Instructions | `AGENTS.md`, `CLAUDE.md`, overrides | Project | Project | Markdown |
| Skills | `~/.cade-mini/skills`, `.cade-mini/skills` | Discovery | User or project | `SKILL.md` |
| Config | `cade-mini.json` | `MiniConfig` | Project | Pydantic, strict |
| Credentials | `~/.cade-mini/auth.json` | `JsonCredentialStore` | User | Provider credentials |
| Docs site | `docs/index.html` | Docs tool | Repository | Generated |
| LLM catalogs | `docs/llms*.txt` | Docs tool | Repository | Generated |

## The session log

The log is append-only and versioned. It stores model-visible facts and audit
facts in one tree. It is the only artifact needed to reconstruct the
conversation. Compaction and windows are log entries, not rewrites.

## Snapshots

Snapshots are an undo journal for file changes. They cover `write`, `edit`, and
common single-command shell mutations. Format v2 uses an atomically replaced
JSONL index, immutable before/after records, and content-addressed original blobs.
See [Snapshots and goals](../snapshots-goals.md) for conflict and legacy behavior.
Snapshots are not part of the session branch.

## Goals and notes

A goal is one objective with an active or done status. `NOTE.md` is working
state that is included in every request and summarized during compaction.
`MEMORY.md` is durable project knowledge. These files are intentionally plain:
they can be edited by hand and inspected with ordinary tools.

## Generated artifacts

The docs site, `llms.txt`, and `llms-full.txt` are generated from Markdown and
`docs.json`. They are checked for freshness; do not edit the HTML by hand.

## What is not persisted

- Request-only context is recorded in the request audit, not as messages.
- Grants last for the process; durable policy belongs in config rules.
- Approval prompts and TUI state are host state.
- Provider replay payloads live inside the assistant fact that produced them.

See [Session log schema](../reference/session-schema.md) for field-level detail and
[Change policy](../persistence/change-policy.md) for schema changes.
