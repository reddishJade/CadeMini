# 连接自定义 MCP 服务器

MCP 服务器支持 stdio 和 Streamable HTTP。Cade 为每个服务器保持一条连接，并把工具暴露为 `mcp__<server>__<tool>`。

## 配置

把服务器加入 `cade.json`：

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

`command` 和 `args` 必填；`cwd`、`env` 和 `timeout_ms` 可选。相对 `cwd` 按项目根目录解析。

## 放行

MCP 工具默认询问。无人值守运行时，为特定工具添加规则：

```json
{
  "rules": [
    {"tool": "mcp__files__read_file", "decision": "allow"}
  ]
}
```

计划模式和 `--read-only` 会拒绝 MCP 工具，即使有规则。

## 验证

1. 启动 Cade；服务器无法启动时会打印启动错误。
2. 让模型列出或调用已发现的工具。
3. 检查工具结果和日志中的 `mcp__` 前缀。
4. 停止 Cade；服务器进程在退出时关闭。

## 限制

支持的协议版本、资源、提示词、取消行为和可选能力限制见 [MCP](../mcp.md)。活动调用超时后会关闭连接，并等待 SDK 完成清理。
