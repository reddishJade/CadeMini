# MCP reference servers

Cade Mini implements stdio `initialize`, `tools/list`, and `tools/call` with a
persistent connection per server. That is enough for the reference servers that
expose tools.

## Verified servers

| Server | Command | Verified |
| --- | --- | --- |
| Filesystem | `npx -y @modelcontextprotocol/server-filesystem <dir>` | Discovery and `read_text_file` |
| Memory | `npx -y @modelcontextprotocol/server-memory` | Discovery of entity tools |
| Sequential Thinking | `npx -y @modelcontextprotocol/server-sequential-thinking` | Discovery of `sequentialthinking` |
| Time | `uvx mcp-server-time` | Discovery and `get_current_time` |
| Fetch | `uvx mcp-server-fetch` | Server starts and lists tools |

On Windows, run npm servers through `cmd /c`:

```json
{
  "mcp_servers": {
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time"]
    },
    "fs": {
      "command": "cmd",
      "args": [
        "/c", "npx", "-y",
        "@modelcontextprotocol/server-filesystem",
        "C:/path/to/workspace"
      ]
    }
  }
}
```

On POSIX use `npx` directly. The Git server needs its repository flag, for
example `--repository <path>`; pass the exact arguments the server expects.

## Capability boundary

The reference servers above are tool servers. Cade Mini does not implement MCP
resources, prompts, sampling, or server-initiated requests. A server that relies
on those features will start but expose only its tools.

## Policy

MCP tools are named `mcp__<server>__<tool>`. They ask by default, are denied in
plan mode and with `--read-only`, and can be allowed by a rule:

```json
{
  "rules": [
    {"tool": "mcp__time__get_current_time", "decision": "allow"}
  ]
}
```

## Verify

```bash
cade-mini --mode build "Use the time MCP tool to get the current time in Asia/Singapore."
```

The log records a `tool_result` with the server's text content. A server process
is closed when Cade Mini exits.

## Related

- [Configuration](../configuration.md)
- [Hooks, MCP, and subagents](../hooks-mcp-subagents.md)
- [Custom MCP server](../cookbook/custom-mcp.md)
