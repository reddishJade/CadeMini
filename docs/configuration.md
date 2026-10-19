# Configuration

Cade Mini reads `<project-root>/cade-mini.json` by default. Use `--config PATH`
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
  "sessions_dir": ".cade-mini/sessions",
  "mode": "act",
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
  for ChatGPT plan usage (Sign in with ChatGPT) or `openai` for an OpenAI API
  key. The CLI flags override both.
- `reasoning_effort`: provider reasoning effort. The CLI flag is `--effort`.
- `context_window`: model context window in tokens.
- `max_steps`: maximum model steps in one run.
- `sessions_dir`: session directory. Relative paths resolve against the project
  root.
- `mode`: `plan`, `build`, or `act`.
- `read_only`: deny writes and shell commands.
- `instructions`: load `AGENTS.md` or `CLAUDE.md` into request context.
- `note`: load `NOTE.md` working state.
- `memory`: load `MEMORY.md` durable facts.
- `auto_review`: let the model review ask-level tool calls. Default is false.
- `prompt_modules`: system prompt modules. Available names are `identity`,
  `tool_discipline`, `working_note`, and `environment`.

## Rules

Rules are evaluated last match first. A rule matches a tool name or `*`, and
optionally a path or command prefix. `decision` is `allow`, `deny`, or `ask`.

```json
{
  "rules": [
    {"tool": "bash", "command_prefix": "git", "decision": "deny"},
    {"tool": "bash", "command_prefix": "git status", "decision": "allow"},
    {"tool": "write", "path_prefix": "docs/", "decision": "allow"}
  ]
}
```

Hard boundaries win over rules: a path outside the workspace is denied even if a
rule allows the tool. `read_only` also wins over grants and rules.

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
