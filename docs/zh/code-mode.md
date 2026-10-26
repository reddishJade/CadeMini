# Code mode

使用 `run_code`，让一段 Python 程序串联工具并筛选中间结果。
它与直接工具调用同时可用，无需额外配置。
例如，可以要求 Cade 使用 `run_code` 读取多个文件，只返回其中的标题。

## 编写程序

`code` 和 `description` 都是必填参数。
`description` 是工具卡片显示的简短操作说明，例如“统计文件行数”，不能留空。
`code` 参数是异步 Python 函数体，支持顶层 `await` 和 `return`。
工具参数与直接调用一致：

```python
result = await tools.read({"path": "README.md"})
return [line for line in result["content"].splitlines() if line.startswith("#")]
```

使用 `await tools.name({...})` 或 `await tools["name"]({...})` 调用工具。
成功返回 `{content, metadata}`。metadata 保留工具已有的结构化数据，例如 MCP 的 `mcp_result`。
工具失败或被拒绝时抛出 `RuntimeError`，程序可以捕获。

只有打印的文本和 JSON 返回值进入模型历史，其他工具结果留在程序内部。
每次执行使用新 Python 进程，变量不跨调用保留。
脚本可使用 `asyncio`，但工具调用按顺序执行，`asyncio.gather` 提交的调用也不例外。
脚本不能递归调用 `run_code`。

## 结果与限制

可选参数 `timeout` 指定整段程序的时限，单位为秒，包含嵌套工具执行和审批等待。
它必须是正的有限数值，没有固定最大值；省略时默认使用 120 秒。例如：

```json
{"code": "await asyncio.sleep(180)\nreturn 'done'", "description": "等待耗时操作", "timeout": 300}
```

嵌套工具继承所选时限作为默认超时，整段程序仍受同一个总时限约束。
你可以在超时前随时取消程序。
打印文本和返回值共享 50 KiB 预算，控制消息上限为 1 MiB，超出大小限制时返回错误。

脚本报错时返回已捕获的输出和错误。
失败不撤销已完成操作，也不自动重跑程序。
取消或超时会终止 Python，并等待已启动的嵌套调用结束。

嵌套调用的 `tool_execution` 审计记录不进入模型历史，可通过 `history` 检索。
通过 `tools.write` 和 `tools.edit` 写入的文件保留已有的恢复能力。

执行权限和 Python 直接 I/O 见[工具与安全](tools-security.md#python-执行权限)。
设计依据见[决策 0019](../decisions/0019-python-tool-orchestration.md)。
