# Configuration

Cade reads `<project-root>/cade.json` by default. Use `--config PATH`
to select another file. Command-line options override the file; the file
overrides built-in defaults.

The file is strict: unknown keys and invalid values are rejected instead of
silently ignored.

In the TUI, `/config` opens a settings browser. Every setting opens an arrow-key
menu with the current value selected. Numeric settings offer presets; nullable
settings offer **Use default**. Choose **Custom value...** to enter an arbitrary
number, path, or model name in a prefilled form. Press Enter to save or Esc to
return to the choices. Saved defaults take effect on the next start.

**TUI Rendering** is a two-option menu: **Inline** (default) keeps terminal
scrollback and native text selection, while **Fullscreen** uses the alternate
screen and in-app PageUp/PageDown scrolling. This option has no custom text
input; choose a mode and restart Cade to apply it.

## Example

```json
{
  "provider": "chatgpt",
  "model": "gpt-5.6-luna",
  "reasoning_effort": "high",
  "context_window": 128000,
  "max_steps": 30,
  "sessions_dir": ".cade/sessions",
  "mode": "build",
  "sandbox": "workspace-write",
  "network": true,
  "read_only": false,
  "instructions": true,
  "note": true,
  "memory": true,
  "auto_review": false,
  "prompt_modules": ["identity", "tool_discipline", "working_note", "environment"],
  "rules": [
    {
      "tool": "bash",
      "command_prefix": "git status",
      "decision": "allow"
    }
  ]
}
```

## Fields

- `provider`, `model`: provider name and model identifier. Use `chatgpt`
  for ChatGPT plan usage (Sign in with ChatGPT), `openai` for an OpenAI API
  key, or `deepseek` for a DeepSeek API key. The CLI flags override both.
- `reasoning_effort`: provider reasoning effort. The CLI flag is `--effort`.
- `context_window`: model context window in tokens. The default comes from the
  registered model metadata. Unregistered models require this field or
  `--context-window`; Cade rejects them otherwise.
- `max_steps`: maximum model steps in one run.
- `sessions_dir`: session directory. Relative paths resolve against the project
  root.
- `mode`: `plan`, `build`, or `ask`. Default is `build`; replace old `act` values with `ask`.
- `sandbox`: `read-only`, `workspace-write` (default), or `danger-full-access`.
  Windows skips OS confinement regardless of this setting.
- `network`: allow tool network access. Default is true. OS enforcement of false
  for shell commands requires Linux/macOS confinement. Web tools reject calls
  on every platform when this is false.
- Web search uses Exa. Set `EXA_API_KEY` to supply your own key; it is optional.
  Remove the obsolete `websearch_provider` field from existing `cade.json` files.
- `read_only`: deny mutations and allow only classified read-only shell commands.
- `instructions`: load `AGENTS.md` or `CLAUDE.md` into request context.
- `note`: load `NOTE.md` working state.
- `memory`: load `MEMORY.md` durable facts.
- `auto_review`: let the model review ask-level tool calls. Default is false.
- `prompt_modules`: system prompt modules. Available names are `identity`,
  `tool_discipline`, `working_note`, and `environment`.

## Rules

Rules match a tool name or `*`, optionally a normalized path subtree or command
prefix. All matches participate: `deny` takes precedence over `ask`, then
`allow`. Order does not matter. `allow` overrides mode defaults, not an effective
explicit deny.

```json
{
  "rules": [
    {"tool": "bash", "command_prefix": "git", "decision": "deny"},
    {"tool": "bash", "command_prefix": "git status", "decision": "allow"},
    {"tool": "write", "path_prefix": "docs/", "decision": "allow"}
  ]
}
```

In this example, `git status` is denied because the broader deny still matches.
Remove or narrow that deny to allow it. Plan constraints, protected paths, and
`read_only` win over grants and rules. External writes request additional
permissions instead of being unconditionally denied. See
[Tools and security](tools-security.md) for scopes and approval behavior.

## MCP servers

MCP servers use stdio or Streamable HTTP and keep one connection per server.

```json
{
  "mcp_servers": {
    "files": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": ".",
      "timeout_ms": 30000,
      "env": {"LOG_LEVEL": "warn"}
    }
  }
}
```

Discovered tools are exposed as `mcp__<server>__<tool>`. They ask by default,
are denied in plan mode and read-only mode, and can be allowed by a rule.

Use `env` for ordinary process settings and `env_from` to pass named host
environment variables to a stdio server. Only the SDK's basic process environment
is inherited; other host credentials are not copied automatically. For HTTP,
use `headers_from` to load request headers from host environment variables:

```json
{
  "mcp_servers": {
    "remote": {
      "transport": "streamable-http",
      "url": "https://mcp.example.com/mcp",
      "headers_from": {"Authorization": "EXAMPLE_MCP_AUTH"},
      "timeout_ms": 30000
    }
  }
}
```

Set `EXAMPLE_MCP_AUTH` outside Cade to the complete authorization header value.
Store credentials in your environment or external secret manager, not project
configuration, URLs, command arguments, or skill files. Cade does not persist a
credential cache; explicitly passed credential values are redacted from MCP
results and diagnostics. MCP server processes are trusted extensions outside
the workspace tool sandbox, so configure only servers you trust.

Set `trust_project_skills: true` to discover project skills. `skills_dir` selects
an additional trusted directory relative to the project root or by absolute
path. See [Skills and memory](skills-memory.md).
