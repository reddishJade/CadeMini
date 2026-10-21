# 架构

Cade 是 `src/cade/` 下的独立编码代理。它使用四段流水线，每段只有一个所有者，数据只朝一个方向流动。

```text
会话日志 -> 对话投影 -> 请求 -> 运行
```

## 会话日志

`SessionLog` 是追加式 JSONL 文件。每条记录包含 ID、时间戳、类型、父 ID 和数据。活动分支结束于最新记录。`/tree` 追加一条导航事件，用父 ID 指明续接点；其他分支仍留在同一个文件里。执行循环只沿活动路径工作。

日志保存事实：

- `system`、`user`、`assistant` 和 `tool_result` 是模型可见的事实。
- `compaction` 记录摘要和 `first_kept_id`。
- `window` 记录新的上下文起点和可选的交接。
- `request`、`run`、`attempt`、`branch`、`checkpoint` 和 `restore` 是审计事实。它们永远不会变成对话消息。branch 事件还会保留选中的输入，供重启后编辑。

格式是封闭的。见[会话日志格式](reference/session-schema.md)。

## 投影

`project(entries)` 是纯函数。它读取分支，产生确定性的对话投影：

1. 取最新的 system 消息。
2. 应用最新的 window 起点。
3. 应用最新的 compaction 摘要。
4. 保留未被裁掉的 user、assistant 和 tool-result 消息。
5. 配对工具调用和结果；日志在调用中途结束时，在尾部插入一条合成的“已中断”结果。

投影不写日志。重放相同记录总是得到相同对话。

## 请求

`build(conversation, tools, budget, context)` 把消息纳入当前模型窗口。它：

- 拼接只属于请求的上下文，例如指令、技能目录、NOTE、记忆和目标；
- 保留每条投影消息和工具结果；
- 用输入预算检查完整请求；
- 把结果转换成 provider 的线上格式；
- 返回带 `BuildReport` 的 `Request`。

准入不修改对话，也不写事实。只有 compaction 和 window 事件会改变可见历史。超出预算的请求会被拒绝，不会静默截断。

## 运行

在追加新的 user 事实之前，`run()` 会为未完成的工具调用保存“已中断”结果。它不会重跑结果未知的工具。随后它调用 provider，追加 assistant 和 tool-result 事实，并执行工具。它是回合内唯一写日志的阶段。压缩和窗口回退都是决策，最终以新的日志事实表示。

## 包地图

- `core/types.py`：日志、消息、块和错误类型。
- `core/events.py`：严格的事件载荷校验。
- `core/log.py`：追加、加锁、尾部修复、导航、会话列表、fork/clone。
- `core/history.py`：用于历史选择的整棵树纯投影。
- `app/recovery.py`：回合检查点、后进先出操作和协调恢复。
- `extensions/recovery.py`：操作日志和待处理事务记录。
- `core/projection.py`：确定性的对话投影。
- `core/request.py`：预算、准入、线上格式转换。
- `core/loop.py`：provider 流、工具执行、重试和事实写入。
- `core/compaction.py` 和 `core/window.py`：上下文缩减决策。
- `tools/workspace.py`、`tools/history.py`：工作区工具和证据检索。
- `safety/policy.py`、`safety/shell.py`、`safety/grants.py`、`safety/approval.py`、`safety/review.py`：权限和审批。
- `extensions/skills.py`、`extensions/memory.py`、`extensions/hooks.py`、`extensions/mcp.py`、`extensions/subagents.py`、`extensions/snapshots.py`、`extensions/goal.py`：附加功能和持久宿主状态。
- `app/config.py`、`app/provider.py`、`app/auth.py`、`app/prompts.py`、`app/instructions.py`、`app/note.py`：启动和请求上下文。
- `hosts/cli.py`、`docs.py`：宿主和文档。

Cade 把 `cade.ai` 和 `cade.execution_env` 作为下层模块，分别负责 provider 协议和本地进程执行。它不依赖早期的 Cade 项目。没有顶层再导出，直接导入所属子模块。

## 恢复

宿主把 `TurnLifecycle` 注入核心循环。它在 user 事实之前创建根回合检查点，在该事实之后建立关联，并在执行结束后关闭检查点。工具工作线程和同步子代理继承捕获所有权。核心不导入工作区存储。恢复除了会话运行锁，还用工作区锁串行化协作写入者。

原生快照存储保留前后两份文件镜像。追加式工作区操作日志驱动逐步撤销和重做；会话日志仍是对话血统的权威来源。持久化的待处理计划协调文件与对话提交。中断后恢复可以幂等续作，遇到未知的外部编辑就停止。见[快照与目标](snapshots-goals.md)。

## 下层模块与状态

`ai/` 负责 provider 协议、流式适配器、模型元数据和 OAuth。`execution_env/` 负责本地进程执行和输出捕获。这两层都不导入应用或宿主模块。AI 层只支持 Responses 协议，记录在[决策 0007](../decisions/0007-responses-only-provider.md)；套餐用量使用 Sign in with ChatGPT，记录在[决策 0008](../decisions/0008-siwc-login.md)。DeepSeek 使用独立传输，但流格式同样是 Responses，端点由自己提供。`execution_env/sandbox.py` 负责 bubblewrap 和 Seatbelt 的进程包装。`core/permissions.py` 承载结构化审批结果和每次调用的范围。CLI 在 Linux 和 macOS 上隔离 shell 与文件工作进程；Windows 直接运行。边界和可信扩展见[工具与安全](tools-security.md)。

凭据和用户级技能放在 `~/.cade/`。SIWC 宿主 ID 在 `~/.cade/host.json`。会话、快照、目标和项目技能放在 `<workspace>/.cade/`。项目设置放在 `cade.json`。早期 Cade 项目的状态格式与本项目不兼容；请从干净的 `~/.cade/` 和工作区 `.cade/` 目录开始。

实现位置和持久状态归属见[代码地图](design/code-map.md)和[持久化目录](design/persistence-catalog.md)。

## 为什么这样拆分

这种拆分避免一个常见故障：请求构造器开始隐藏消息，随后历史改写成了唯一的事件副本。Cade 保留原始事实，让投影和准入可以被替换，而不丢失历史。
