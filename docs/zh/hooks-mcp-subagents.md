# 钩子、MCP 和子代理

## 钩子

钩子在工具前后运行 shell 命令，配置写在 `cade.json`：

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

- `before_tool` 在策略和审批之后、工具执行之前运行。非零退出会拒绝调用，并记录一条错误工具结果。
- `after_tool` 在工具执行成功或失败后运行。它的输出被忽略。
- `matcher` 是精确工具名、以 `*` 结尾的前缀，或省略表示所有工具。
- 命令以工作区为工作目录运行，并收到 `CADE_HOOK_EVENT`、`CADE_TOOL_NAME`、`CADE_TOOL_ARGS` 和 `CADE_TOOL_RESULT`。

钩子是可信配置。挂起的钩子会在超时后被杀掉。

## MCP

MCP 服务器使用 stdio。Cade 为每个服务器启动一个常驻进程，并在启动时发现工具：

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

工具命名为 `mcp__<server>__<tool>`。输入格式来自服务器的 `tools/list` 响应。调用在连接上顺序执行，保留服务器状态。

策略：

- `plan` 模式隐藏 MCP 工具并拒绝调用。
- `read_only` 拒绝 MCP 调用。
- `build` 和 `ask` 默认询问；规则可以放行特定工具。

启动失败的服务器会在启动时报告并跳过。进程退出时关闭连接。

## 子代理

`subagent` 工具在独立会话中运行一个子任务，返回最终答案和子会话 ID：

```json
{"prompt": "Find the failing test and report the file and line."}
```

子会话：

- 继承父级的 provider、预算、策略、审批者、钩子和请求上下文；
- 获得基础工具，以及绑定到自己日志的 history 工具；
- 不再获得 `subagent` 工具，因此不能递归；
- 最多使用 20 个步骤；
- 把自己的会话写在 `<sessions>/subagents/` 下。

父会话只记录工具调用和返回的摘要。详细证据留在子会话日志里。

`delegate` 用同一个运行器处理一个或多个任务：

```json
{"tasks": ["Inspect the parser", "Add a regression test"], "mode": "build"}
```

任务在各自独立的子会话中顺序运行。每个结果都包含子会话 ID；父会话收到一条合并的工具结果。子级模式不能扩大父级权限：Plan 模式的父会话不能委派 Build 工作。

## 限制

MCP 是一个小型 stdio 客户端，不实现完整的 capabilities 协商。钩子是 shell 命令，不是进程内回调。子代理顺序运行，共享父级审批上下文，不并行执行。
