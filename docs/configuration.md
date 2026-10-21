# Configuration

Cade reads `<project-root>/cade.json` by default. Use `--config PATH`
to select another file. Command-line options override the file; the file
overrides built-in defaults.

The file is strict: unknown keys and invalid values are rejected instead of
silently ignored.

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
  requires Linux/macOS confinement.
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

## Hooks

Hooks run shell commands around tools. A non-zero `before_tool` hook denies the
call.

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

`event` is `before_tool` or `after_tool`. `matcher` is a tool name, a prefix
ending in `*`, or omitted for all tools. Hooks receive `CADE_HOOK_EVENT`,
`CADE_TOOL_NAME`, `CADE_TOOL_ARGS`, and `CADE_TOOL_RESULT` in the environment.

## MCP servers

MCP servers use stdio and keep one persistent connection per server.

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
