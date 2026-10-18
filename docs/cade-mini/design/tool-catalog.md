# Tool catalog

The built-in tool surface. MCP tools are discovered at startup and named
`mcp__<server>__<tool>`; their parameters come from the server.

| Tool | Parameters | Side effect | Default policy |
| --- | --- | --- | --- |
| `read` | `path`, `offset`, `limit` | None | Allow inside workspace |
| `write` | `path`, `content` | Creates or replaces a file; snapshot first | Ask in act, allow in build |
| `edit` | `path`, `old`, `new` | Replaces one occurrence; snapshot first | Ask in act, allow in build |
| `glob` | `pattern`, `path` | None | Allow |
| `grep` | `pattern`, `path`, `include` | None | Allow |
| `bash` | `command`, `workdir`, `timeout_ms` | Arbitrary process; snapshot common mutations | Ask except read-only shell |
| `history` | `query`, `entry_id`, `call_id`, `limit` | None | Allow |
| `skill` | `name` | None; loads a skill body | Allow |
| `memory` | `text` | Appends to `MEMORY.md` | Allow; denied read-only |
| `goal` | `action`, `objective` | Updates `.cade-mini/goal.json` | Allow |
| `subagent` | `prompt`, `description` | Creates a child session | Allow; hidden in plan |
| `mcp__*` | Server-defined | Remote side effects | Ask; denied plan and read-only |

## Reading the table

- "Side effect" is what the tool can change, not what it always changes.
- `write` and `edit` fail closed if the snapshot cannot be taken.
- `bash` read-only classification is conservative; unknown commands ask in
  build and act and are denied in plan.
- MCP tools are dynamic. A rule can allow a specific tool; the default is ask.

## Parameters

The authoritative JSON schemas are built into each tool. This table is a
navigation aid; when it disagrees with the tool schema, the schema wins.

## Related

- [Tools and security](../tools-security.md)
- [Tool execution](../design/tool-execution.md)
- [Configuration catalog](config-catalog.md)
