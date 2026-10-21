# 配置

Cade 默认读取 `<project-root>/cade.json`。用 `--config PATH` 选择其他文件。命令行选项覆盖文件，文件覆盖内置默认值。

配置是严格的：未知键和非法值会被拒绝，不会静默忽略。

## 示例

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

## 字段

- `provider`、`model`：provider 名称和模型标识。`chatgpt` 用于 ChatGPT 套餐（Sign in with ChatGPT），`openai` 用于 OpenAI API key，`deepseek` 用于 DeepSeek API key。CLI 参数会覆盖这两个字段。
- `reasoning_effort`：provider 的推理强度。CLI 参数是 `--effort`。
- `context_window`：模型的上下文窗口，单位是 token。默认值来自已注册的模型元数据。未注册的模型必须提供这个字段或 `--context-window`，否则 Cade 会拒绝启动。
- `max_steps`：单次运行中最多执行多少模型步骤。
- `sessions_dir`：会话目录。相对路径按项目根目录解析。
- `mode`：`plan`、`build` 或 `ask`。默认是 `build`；旧的 `act` 值请改成 `ask`。
- `sandbox`：`read-only`、`workspace-write`（默认）或 `danger-full-access`。Windows 会跳过操作系统隔离，与这里的设置无关。
- `network`：允许工具访问网络。默认是 true。设为 false 时，只有 Linux 和 macOS 的隔离能强制生效。
- `read_only`：拒绝修改，只允许已归类的只读 shell 命令。
- `instructions`：把 `AGENTS.md` 或 `CLAUDE.md` 加载到请求上下文。
- `note`：加载 `NOTE.md` 工作状态。
- `memory`：加载 `MEMORY.md` 中的长期事实。
- `auto_review`：让模型复核 ask 级别的工具调用。默认是 false。
- `prompt_modules`：系统提示模块。可用名称是 `identity`、`tool_discipline`、`working_note` 和 `environment`。

## 规则

规则匹配工具名或 `*`，可选匹配归一化后的路径子树或命令前缀。所有命中的规则都参与决策：`deny` 优先于 `ask`，`ask` 优先于 `allow`。顺序不影响结果。`allow` 覆盖模式默认值，但不能覆盖生效的显式 `deny`。

```json
{
  "rules": [
    {"tool": "bash", "command_prefix": "git", "decision": "deny"},
    {"tool": "bash", "command_prefix": "git status", "decision": "allow"},
    {"tool": "write", "path_prefix": "docs/", "decision": "allow"}
  ]
}
```

这个例子里 `git status` 仍会被拒绝，因为更宽的 deny 也匹配。要放行，就删除或收窄那条 deny。计划模式约束、受保护路径和 `read_only` 优先于授权和规则。工作区外的写入会申请额外权限，不会被无条件拒绝。作用范围和审批行为见[工具与安全](tools-security.md)。

## 钩子

钩子在工具调用前后执行 shell 命令。`before_tool` 钩子返回非零值时，调用被拒绝。

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

`event` 是 `before_tool` 或 `after_tool`。`matcher` 是工具名、以 `*` 结尾的前缀，或省略表示所有工具。钩子通过环境变量收到 `CADE_HOOK_EVENT`、`CADE_TOOL_NAME`、`CADE_TOOL_ARGS` 和 `CADE_TOOL_RESULT`。

## MCP 服务器

MCP 服务器使用 stdio，每个服务器保持一条长连接。

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

发现的工具以 `mcp__<server>__<tool>` 命名。它们默认需要确认，在计划模式和只读模式下被拒绝，也可以由规则放行。
