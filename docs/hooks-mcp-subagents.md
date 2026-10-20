# Hooks, MCP, and subagents

## Hooks

Hooks run shell commands around tools. They are configured in `cade-mini.json`:

```json
{
  "hooks": [
    {
      "event": "before_tool",
      "command": "python tools/check_tool.py",
      "matcher": "bash",
      "timeout_ms": 10000
    }
  ]
}
```

- `before_tool` runs after policy and approval, before the tool executes. A
  non-zero exit denies the call and records an error tool result.
- `after_tool` runs after a successful or failed executed tool. Its output is
  ignored.
- `matcher` is an exact tool name, a prefix ending in `*`, or omitted for all.
- Commands run with the workspace as the working directory and receive
  `CADE_HOOK_EVENT`, `CADE_TOOL_NAME`, `CADE_TOOL_ARGS`, and
  `CADE_TOOL_RESULT`.

Hooks are trusted configuration. A hook that hangs is killed after its timeout.

## MCP

MCP servers use stdio. Cade Mini starts one persistent process per server and
discovers its tools at startup:

```json
{
  "mcp_servers": {
    "files": {
      "command": "python",
      "args": ["mcp_server.py"],
      "timeout_ms": 30000
    }
  }
}
```

Tools are named `mcp__<server>__<tool>`. Their input schema comes from the
server's `tools/list` response. Calls run sequentially over the connection and
preserve server state.

Policy:

- `plan` mode hides MCP tools and denies them.
- `read_only` denies them.
- `build` and `ask` ask by default; a rule can allow a specific tool.

A server that fails to start is reported at startup and skipped. Connections
are closed at process exit.

## Subagents

The `subagent` tool runs one child task in its own session and returns the final
answer with the child session ID:

```json
{"prompt": "Find the failing test and report the file and line."}
```

The child:

- gets the parent's provider, budget, policy, approver, hooks, and request
  context;
- gets the base tools plus a history tool bound to its own log;
- does not get another `subagent` tool, so it cannot recurse;
- uses at most 20 steps;
- writes its own session under `<sessions>/subagents/`.

The parent records only the tool call and the returned summary. The child log
keeps the detailed evidence.

`delegate` uses the same runner for one or more tasks:

```json
{"tasks": ["Inspect the parser", "Add a regression test"], "mode": "build"}
```

Tasks run sequentially in separate child sessions. Each result includes the
child session ID; the parent gets one combined tool result. Child modes cannot
expand parent permissions: a Plan parent cannot delegate Build work.

## Limits

MCP is a small stdio client, not a full capability negotiation implementation.
Hooks are shell commands, not in-process callbacks. Subagents are sequential and
share the parent's approval context rather than running in parallel.
