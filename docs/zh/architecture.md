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

## 包边界

Cade 有五个运行时包：

- `ai`：模型协议、provider、模型元数据和认证协议。
- `harness`：日志驱动的代理循环、会话事实与投影、请求准入、工具执行契约和内部执行后端。
- `coding`：编程工具、项目上下文、工作区策略、子代理、MCP 集成、快照和工作区恢复。
- `app`：配置、凭据、provider 装配、应用会话、设置和命令。
- `hosts`：CLI 参数、终端输入输出和 TUI 展示。

依赖从 hosts 指向 app、coding、harness 和 ai。每层可以使用下层，下层不导入上层。harness 包含代理运行时，不再设置平级的 agent 包。

本地进程与沙箱实现放在 `harness/execution/local`，不作为顶层包暴露。执行范围契约不导入平台后端。`tests/test_architecture.py` 检查这些边界。

`app/bootstrap.py` 为一次性执行和 TUI 创建同一种 `ApplicationSession`。`app/session.py` 管理活动会话，不导入终端模块。`harness/executor.py` 为模型调用和应用直接调用统一处理授权、逐调用执行范围、超时和工具错误。循环仍负责记录模型回合事实，在结果写入后发出工具完成事件。

实现位置见[代码地图](design/code-map.md)。Cade 不依赖早期的 Cade 项目，也不提供兼容导入路径。类型和函数直接从所属模块导入。

## 恢复

应用装配时把 `TurnLifecycle` 注入 harness 循环。它在 user 事实之前创建根回合检查点，在该事实之后建立关联，并在执行结束后关闭检查点。工具工作线程和同步子代理继承捕获所有权。harness 不导入工作区存储。恢复除了会话运行锁，还用工作区锁串行化协作写入者。

原生快照存储保留前后两份文件镜像。追加式工作区操作日志驱动逐步撤销和重做；会话日志仍是对话血统的权威来源。持久化的待处理计划协调文件与对话提交。中断后恢复可以幂等续作，遇到未知的外部编辑就停止。见[快照与目标](snapshots-goals.md)。

## 下层模块与状态

`ai/` 负责 provider 协议、流式适配器、模型元数据和 OAuth。`harness/execution/` 负责本地进程执行和输出捕获。这两层都不导入应用或宿主模块。AI 层只支持 Responses 协议，记录在[决策 0007](../decisions/0007-responses-only-provider.md)；套餐用量使用 Sign in with ChatGPT，记录在[决策 0008](../decisions/0008-siwc-login.md)。DeepSeek 使用独立传输，但流格式同样是 Responses，端点由自己提供。`harness/execution/local/sandbox.py` 负责 bubblewrap 和 Seatbelt 的进程包装。`harness/permissions.py` 承载结构化审批结果和每次调用的范围。CLI 在 Linux 和 macOS 上隔离 shell 与文件工作进程；Windows 直接运行。边界和可信扩展见[工具与安全](tools-security.md)。

凭据和用户级技能放在 `~/.cade/`。SIWC 宿主 ID 在 `~/.cade/host.json`。会话、快照、目标和项目技能放在 `<workspace>/.cade/`。项目设置放在 `cade.json`。早期 Cade 项目的状态格式与本项目不兼容；请从干净的 `~/.cade/` 和工作区 `.cade/` 目录开始。

实现位置和持久状态归属见[代码地图](design/code-map.md)和[持久化目录](design/persistence-catalog.md)。

## 为什么这样拆分

这种拆分避免一个常见故障：请求构造器开始隐藏消息，随后历史改写成了唯一的事件副本。Cade 保留原始事实，让投影和准入可以被替换，而不丢失历史。
