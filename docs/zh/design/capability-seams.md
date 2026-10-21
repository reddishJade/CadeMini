# 能力接缝

Cade 保留少量显式契约。接缝是实现可以被替换的地方，替换时不改会话格式，也不改循环。

| 接缝 | 契约 | 实现 | 主要使用方 |
| --- | --- | --- | --- |
| Provider | `harness.provider.Provider` 流式事件 | OpenAI Responses、ChatGPT 套餐（SIWC）、DeepSeek Responses | 循环、压缩、自动复核 |
| Tool | `harness.tools.Tool` 的名称、描述、参数、execute | 工作区工具、history、skill、memory、goal、subagent、MCP | 循环 |
| Policy | `permissions.Policy.check` 返回决策、原因和范围 | `WorkspacePolicy` | 循环、CLI shell |
| Approval | `ApprovalRequest` 到 `ApprovalResult`，可选异步 | `SessionApprover`、`AutoReviewer` | 授权 |
| 执行范围 | `ExecutionScope` 和 `PlatformSandbox.wrap` | bubblewrap、Seatbelt、Windows 直接运行 | 工作区工具 |
| 压缩 | `Compactor(log, budget)` | LLM 摘要压缩器 | 循环 |
| 会话存储 | `SessionLog` 的追加、分支、打开 | 文件支持的 JSONL 日志 | 其上所有层 |
| 恢复 | `SnapshotStore` 的捕获和撤销 | 文件快照存储 | 工具、CLI |
| 宿主 | 循环之上的任务运行器 | CLI | 用户 |

## Provider

循环需要一个流式事件源：文本增量、工具调用、最终消息、用量和失败。provider 负责线上格式转换和重放状态。在这个接缝之上，Cade 不依赖 provider 专有的消息类型。

## 工具

工具声明 JSON schema 和异步 `execute`。工具结果是内容、错误标志和可选元数据。工具不写会话日志；循环记录它们的结果。

## 策略与审批

策略返回决策，不执行操作。只有 `ask` 才会咨询审批。拒绝会变成工具错误事实，模型可以据此恢复。把两者分开，宿主就能选择 TTY 提示、模型复核或非交互拒绝。

## 存储

`SessionLog` 是持久历史。`SnapshotStore`、`GoalStore` 以及记忆和 NOTE 文件是宿主状态，不是会话事实。把它们当作独立存储，可以在保持格式封闭的同时支持恢复和长期状态。

## 宿主

宿主负责展示、输入和事件循环边界。它不拥有代理循环或格式。CLI 渲染事件并运行任务，两者都不改。

## 测试即契约

每个接缝都有专门的测试模块，避免它悄悄变成隐藏依赖。
