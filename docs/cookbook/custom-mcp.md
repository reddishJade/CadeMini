# Connect a custom MCP server

MCP servers use stdio or Streamable HTTP. Cade keeps one connection per server and exposes each
tool as `mcp__<server>__<tool>`.

## Configure

Add the server to `cade.json`:

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

1. Start Cade; it prints a startup error if the server cannot start.
2. Ask the model to list or call a discovered tool.
3. Check the tool result and the `mcp__` prefix in the log.
4. Stop Cade; the server process closes at exit.

## Limits

See [MCP](../mcp.md) for supported protocol revisions,
resources, prompts, and optional capability limits. An active call that times
out closes its connection and waits for SDK cleanup.
