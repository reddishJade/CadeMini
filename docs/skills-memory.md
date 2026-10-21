# Skills and memory

## Skills

Cade discovers user skills in `~/.cade/skills/` and `~/.agents/skills/`.
Set `trust_project_skills` to `true` to include the matching directories under
this project. Project skills are disabled by default. An explicit `skills_dir`
is trusted and has the highest priority, followed by project and user roots;
`.cade/skills` precedes `.agents/skills` within each scope. Duplicate names
produce a warning and keep the first definition.

Each skill directory must contain `SKILL.md` with YAML frontmatter:

```markdown
---
name: release-check
description: Validate release metadata before tagging.
---

Run the release checklist...
```

The loader validates required fields, naming rules, directory-name agreement,
length limits, and metadata types. Invalid definitions are skipped with a
diagnostic. Discovery reads frontmatter only; activation reads the body and
indexes `references/`, `scripts/`, and `assets/`.

The request carries a compact catalog. The model loads instructions with:

```json
{"name": "release-check"}
```

The result includes the absolute skill root and resource paths. Read a reference
without loading every resource:

```json
{"name": "release-check", "reference": "checklist.md"}
```

Reference names are relative to `references/`; hidden files, symbolic links,
binary references, and paths outside the skill root are rejected. References
are limited to 50 KiB. Scripts run through the ordinary `bash` tool and its
permission policy. Trusted skill directories are readable but protected from
writes in native sandboxes; other private `.cade/` state stays hidden.

Use `$release-check` or `/skill release-check` for explicit activation.
`disable-model-invocation: true` hides a skill from automatic selection while
keeping explicit activation available. `compatibility` is disclosed when loaded;
`allowed-tools` is advisory and never grants permissions.

Bodies are tool-result facts. Previously activated instructions missing after
compaction or a window reset are restored from the active branch into request
context. A model can reload a skill when it needs its exact instructions.

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
