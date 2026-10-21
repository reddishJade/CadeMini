# 扩展

扩展通过窄接缝增加行为，都不改变会话格式。

## 技能

发现过程读取用户级和项目级根目录中的 `SKILL.md`。请求携带技能目录；`skill` 工具把正文加载成工具结果。正文是事实，不是请求专属上下文。

## 记忆

`MEMORY.md` 属于请求上下文。`memory` 工具追加一条列表项，不能写其他路径。文件有上限，以保护请求预算。

## 钩子

`HookRunner` 在工具前后执行配置的 shell 命令。`before_tool` 可以拒绝；`after_tool` 只观察。钩子可信，并且有超时限制。见[钩子、MCP 和子代理](../hooks-mcp-subagents.md)。

## MCP

每个配置的 stdio 服务器保持一个常驻进程。发现过程读取 `tools/list`；调用通过串行队列使用 `tools/call`。工具带 `mcp__` 前缀，默认需要确认，在计划模式和只读模式下被拒绝。无法启动的服务器会报告并跳过。

## 子代理

`SubagentTool` 在 `<sessions>/subagents` 下创建子会话。子会话继承父级的 provider、预算、策略、审批者、钩子、上下文和基础工具，外加自己的 history。它不能再启动子代理，最多执行 20 个步骤。父会话只记录调用和摘要。`DelegateTool` 顺序运行一个或多个子任务，并可选应用更严格的子级模式。Reviewer 请求保留祖先对话，因此子提示不能替换人工限制。

## 快照

`SnapshotStore` 捕获 `write`、`edit` 和常见的单命令 shell 修改。一次性任务的 `--undo` 选项恢复最新快照。快照是项目状态，不是会话事实。

## 目标

`GoalStore` 在 `.cade/goal.json` 里持久保存一个目标。`goal` 工具和 `/goal` 命令负责管理。活动目标属于请求上下文。

## 宿主

终端宿主运行一次性任务和 TUI。它与其他所有入口共用同一个循环和策略。

## 限制

扩展刻意保持小。MCP 是 stdio 客户端，不实现完整的 capabilities 协商。钩子是 shell 命令，不是进程内回调。子代理顺序运行，共享父级审批上下文。

## 测试

`test_skills.py`、`test_memory.py`、`test_hooks.py`、`test_mcp.py`、`test_subagents.py`、`test_snapshots.py` 和 `test_goal.py`。
