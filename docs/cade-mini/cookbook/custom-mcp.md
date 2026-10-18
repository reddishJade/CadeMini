# Connect a custom MCP server

MCP servers use stdio. Cade Mini keeps one process per server and exposes each
tool as `mcp__<server>__<tool>`.

## Configure

Add the server to `cade-mini.json`:

```json
{
  "mcp_servers": {
    "files": {
      "command": "python",
      "args": ["tools/mcp_files.py"],
      "timeout_ms": 30000,
      "env": {"LOG_LEVEL": "warn"}
    }
  }
}
```

`command` and `args` are required; `cwd`, `env`, and `timeout_ms` are optional.
A relative `cwd` resolves against the project root.

## Allow

MCP tools ask by default. Add a rule for a specific tool if unattended runs need
it:

```json
{
  "rules": [
    {"tool": "mcp__files__read_file", "decision": "allow"}
  ]
}
```

Plan mode and `--read-only` deny MCP tools even with a rule.

## Verify

1. Start Cade Mini; it prints a startup error if the server cannot start.
2. Ask the model to list or call a discovered tool.
3. Check the tool result and the `mcp__` prefix in the log.
4. Stop Cade Mini; the server process closes at exit.

## Limits

The client implements `initialize`, `tools/list`, and `tools/call`. It does not
implement resources, prompts, sampling, or server-initiated requests. A server
that hangs is killed after the configured timeout.
