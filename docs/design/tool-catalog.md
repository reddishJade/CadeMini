# Tool catalog

The built-in tool surface. MCP tools are discovered at startup and named
`mcp__<server>__<tool>`; their parameters come from the server.

| Tool | Parameters | Side effect | Default policy |
| --- | --- | --- | --- |
| `read` | `path`, `offset`, `limit` | None | Allow except protected paths |
| `write` | `path`, `content` | Creates or replaces a file; snapshot first | Ask in ask, allow in build |
| `edit` | `path`, `edits: [{oldText, newText}]` | Applies disjoint replacements against the original file; snapshot first | Ask in ask, allow in build |
| `find` | `pattern`, `path`, `limit` | None | Allow except protected paths |
| `ls` | `path`, `limit` | None | Allow except protected paths |
| `grep` | `pattern`, `path`, `glob`, `ignoreCase`, `literal`, `context`, `limit` | None | Allow except protected paths |
| `bash`, `powershell` | `command`, `timeout`, `workdir`, `additional_write_paths` | Arbitrary process; snapshot common mutations | Normal development allowed in build; detected risks ask |
| `question` | `questions: [{question, header, options, multiple?}]` | Collects user answers; dismissal ends the turn | Allow, including plan |
| `websearch` | `query` | Searches through Exa | Allow when network access is enabled |
| `webfetch` | `url`, `format`, `timeout` | Fetches text, Markdown, or HTML | Allow when network access is enabled |
| `history` | `query`, `entry_id`, `call_id`, `limit` | None | Allow |
| `skill` | `name` | None; loads a skill body | Allow |
| `memory` | `text` | Appends to `MEMORY.md` | Allow; denied read-only |
| `goal` | `action`, `objective` | Updates `.cade/goal.json` | Allow |
| `delegate` | `prompt` or `tasks`, `mode`, `description` | Creates one or more child sessions | Allow |
| `mcp__*` | Server-defined | Remote side effects | Ask; denied plan and read-only |

## Reading the table

- "Side effect" is what the tool can change, not what it always changes.
- Workspace `write` and `edit` fail closed if the snapshot cannot be taken.
  External writes require permission and have no workspace snapshot.
- `bash` read-only classification is conservative; unknown commands ask in
  ask mode and are denied in plan. Build permits ordinary development commands.
- MCP tools are dynamic. A rule can allow a specific tool; the default is ask.

## Parameters

The authoritative JSON schemas are built into each tool. This table is a
navigation aid; when it disagrees with the tool schema, the schema wins.

See [the behavior contract](../decisions/0015-tool-behavior-parity.md)
for tool defaults and Cade integration. `read` returns file content without
line-number prefixes and supports image attachments. `offset` and `limit`
are positive integers. Editing uses exact text matching; all edits target the
original file and must be unique and disjoint. Use `ls` for directories.
Shell `timeout` is in seconds and has no default. `bash` uses Bash on Windows;
`powershell` uses PowerShell and is available only on Windows.

`find` uses fd and `grep` uses ripgrep. Install both on PATH. Cade reports
missing programs without downloading binaries. Searches exclude `.cade`, including snapshots and session logs.

## Related

- [Tools and security](../tools-security.md)
- [Tool execution](tool-execution.md)
- [Configuration catalog](config-catalog.md)
