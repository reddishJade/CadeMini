# Skills and memory

## Skills

Cade Mini discovers `SKILL.md` files in three roots:

1. `~/.cade/skills/<name>/SKILL.md` for user skills.
2. `<project>/.cade/skills/<name>/SKILL.md` for project skills.
3. `<project>/.cade-mini/skills/<name>/SKILL.md` for Mini-specific skills.

Later roots override earlier roots with the same skill name. A skill file can
start with YAML frontmatter:

```markdown
---
name: release-check
description: Validate release metadata before tagging.
---

Run the release checklist...
```

`name` defaults to the parent directory. `description` defaults to the first
non-empty body line. Invalid names and empty bodies are skipped.

The request gets a compact `<available-skills>` catalog with names and
descriptions. The model loads a body on demand with the `skill` tool:

```json
{"name": "release-check"}
```

Skill bodies are facts in the tool result, not request-only context. Loading the
same skill twice is harmless but adds another tool result.

## Memory

`MEMORY.md` at the project root stores durable facts. It is loaded into request
context as `<memory source="MEMORY.md">` on every task, capped at 32 KiB.

The model appends facts with the `memory` tool:

```json
{"text": "Prefer pytest and keep changes minimal."}
```

`memory` writes only `MEMORY.md`; it cannot write arbitrary paths. The file is
capped at 64 KiB for writes. Remove stale entries manually when it fills.

Memory is different from the other state files:

- `AGENTS.md` is project instruction, owned by the project.
- `NOTE.md` is short-lived working state; it is summarized with compaction.
- `MEMORY.md` is a durable preference or convention.
- The session log is the record of what happened.

## Request context

Instructions, skill catalog, `NOTE.md`, `MEMORY.md`, and an active goal are all
request-only context. They appear in each request audit but never become
conversation messages. See [Sessions and context](sessions-context.md).
