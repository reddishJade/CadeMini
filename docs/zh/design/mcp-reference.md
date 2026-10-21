# MCP 参考服务器

Cade 实现 stdio 的 `initialize`、`tools/list` 和 `tools/call`，每个服务器保持一条长连接。这对暴露工具的参考服务器已经够用。

## 已验证的服务器

| 服务器 | 命令 | 验证内容 |
| --- | --- | --- |
| Filesystem | `npx -y @modelcontextprotocol/server-filesystem <dir>` | 发现工具和 `read_text_file` |
| Memory | `npx -y @modelcontextprotocol/server-memory` | 发现实体相关工具 |
| Sequential Thinking | `npx -y @modelcontextprotocol/server-sequential-thinking` | 发现 `sequentialthinking` |
| Time | `uvx mcp-server-time` | 发现工具和 `get_current_time` |
| Fetch | `uvx mcp-server-fetch` | 服务器启动并列出工具 |

在 Windows 上，通过 `cmd /c` 运行 npm 服务器：

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

在 POSIX 系统上直接使用 `npx`。Git 服务器需要仓库参数，例如 `--repository <path>`；按服务器要求传入精确参数。

## 能力边界

上面的参考服务器都是工具服务器。Cade 不实现 MCP resources、prompts、sampling 或服务器发起的请求。依赖这些功能的服务器可以启动，但只暴露工具。

## 策略

MCP 工具命名为 `mcp__<server>__<tool>`。它们默认询问，在计划模式和 `--read-only` 下被拒绝，可以由规则放行：

```json
{
  "rules": [
    {"tool": "mcp__time__get_current_time", "decision": "allow"}
  ]
}
```

## 验证

```bash
cade --mode build "Use the time MCP tool to get the current time in Asia/Singapore."
```

日志会记录一条 `tool_result`，内容来自服务器返回的文本。Cade 退出时关闭服务器进程。

## 相关页面

- [配置](../configuration.md)
- [MCP 和子代理](../mcp-subagents.md)
- [自定义 MCP 服务器](../cookbook/custom-mcp.md)
