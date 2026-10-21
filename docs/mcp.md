# MCP

## Connection and tools

Cade uses the official Python MCP SDK with stdio and Streamable HTTP.
It discovers tools at startup and keeps one connection per server:

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

A server that fails to start is reported and skipped. A later request can
reconnect it; calls with unknown outcomes are never automatically repeated.
Cancelling or timing out an active call closes its transport and waits for SDK
cleanup. Connections are also closed when the application session exits.

The default `protocol: "auto"` probes the 2026-07-28 stateless protocol and falls
back to the legacy initialize handshake. `protocol: "legacy"` forces the
handshake. The installed SDK supports the 2024-11-05, 2025-03-26, 2025-06-18,
2025-11-25, and 2026-07-28 revisions. The negotiated version is recorded with
tool results. Unknown versions are rejected by the SDK.

Tool discovery follows pagination. Legacy list-change notifications refresh the
live tool catalog before the next model request. Stateless connections refresh
the listing at each model request. Ambiguous or overlong host tool names are
excluded with diagnostics. Input arguments and structured outputs are checked
against their JSON Schemas. Progress and result metadata enter the session log.

Servers advertising resources or prompts also expose these permission-gated
operations under the same server prefix: `resources_list`, `resources_read`,
`prompts_list`, and `prompts_get`. List results preserve `nextCursor`; supply it
as `cursor` to read the next page. Returned resources and prompts are untrusted
data, not instructions or authorization.

For HTTP transport and credential configuration, see [Configuration](configuration.md#mcp-servers).

## Limits

MCP extensions, sampling, elicitation, resource subscriptions, and automatic
OAuth login are not enabled. Image, audio, and resource content blocks are
preserved as JSON in Cade's text tool-result contract rather than sent as native
multimodal provider messages. Tool output exceeding the runtime limit is marked
as truncated; exact structured metadata remains in the log.
Schemas must be self-contained: local `$ref` values work, but schema validation
does not fetch external URLs.
