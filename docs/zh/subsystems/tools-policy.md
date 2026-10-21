# 工具与策略

## 契约

工具声明名称、描述、JSON schema 和异步 `execute`。循环记录内容和错误标志；工具不追加会话事实。

`harness.permissions.Policy.check` 返回 `PermissionCheck`，包含决策（`allow`、`deny` 或 `ask`）、原因和 `ExecutionScope`。`authorize` 只用 `ApprovalRequest` 和 `ApprovalResult` 处理 ask。循环和应用直接调用工具时，共用 `harness.executor.execute_tool`。

`WorkspacePolicy` 在显式规则、授权和模式默认值之前应用模式约束和受保护路径。匹配规则按 deny > ask > allow 生效。子代理模式不能扩大父级权限。面向用户的语义见[工具与安全](../tools-security.md)。

## 执行

共享执行器只在特定工具运行期间，把授权范围放进上下文变量。该范围会跨异步任务和工作线程保留，之后重置。它从不改变默认工作区或授权。

应用装配时用项目根目录、私有临时目录、网络设置和受保护路径构造 `PlatformSandbox`。`SubprocessShell` 在启动进程之前包装命令。文件工具通过 `coding.tools._worker` 使用同一个后端；快照存储与工作进程分开管理。外部文件修改没有工作区快照。取消信号会传到子进程启动器，工具在返回或清理请求文件之前等待进程终止。

Linux 使用 bubblewrap，macOS 使用 Seatbelt，Windows 直接运行。后端错误会变成工具错误，不会退回到无沙箱重试。MCP 服务器进程是可信的宿主扩展，位于这个边界之外。

## Reviewer

`AutoReviewer` 收到授权原因、精确参数、保留的对话、祖先对话和上下文限制。它使用一次独立的无工具请求。过大的输入会被拒绝，不会静默截断。输出是严格对象，包含 `allowed: bool` 和 `reason: str`。拒绝和失败会把原因作为工具错误返回给模型。

## 测试

- `test_policy.py`：模式约束、优先级和写入范围。
- `test_grants.py`：精确授权和作用域隔离。
- `test_review.py`：严格响应和模型可见的拒绝原因。
- `test_subagents.py`：子级权限上限。
- `test_sandbox.py`：真实操作系统文件/网络隔离、工作进程集成、取消、Windows 绕过和后端缺失。
