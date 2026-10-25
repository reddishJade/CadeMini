# CLI

`cade` 入口可以运行单个任务、启动交互式终端宿主，或管理会话。

```bash
cade [TASK] [options]
cade -p TASK [options]
cade exec TASK [options]
```

`cade` 在交互式终端里启动 TUI。可选的位置参数会作为第一条消息发送，任务结束后 TUI 保持打开。用 `-p TASK`（或 `--prompt TASK`）或 `exec TASK` 运行一次后退出。TUI 的位置参数与 `-p` 不能同时使用。

## 登录

```bash
cade login
cade login --provider openai
cade login --provider deepseek
```

`--provider` 默认是 `chatgpt`，使用浏览器里的 Sign in with ChatGPT（SIWC）。`openai` 和 `deepseek` 使用 `api_key`，通过隐藏输入提示填写。凭据保存在 `~/.cade/auth.json`，稳定的 SIWC 宿主 ID 保存在 `~/.cade/host.json`。登录不会修改项目的 provider 或模型设置。Cade 只支持 Responses 协议。ChatGPT 登录和 OpenAI API key 是两条独立的认证路径，都暴露已注册的 OpenAI 聊天模型池。可刷新的 OAuth token 仍然让模型可选；provider 在需要时刷新它。

## 一次性任务选项

- `-p TASK` / `--prompt TASK`：运行一次后退出，与 `cade exec TASK` 相同。

- `--project-root PATH`：设置工作区。默认是当前目录。
- `--config PATH`：选择配置文件。默认是 `<project-root>/cade.json`。
- `--provider NAME`、`--model NAME` 和 `--effort EFFORT`：覆盖 provider 默认值。
- `--mode plan|build|ask`：选择工作模式。默认是 `build`。
- `--sandbox read-only|workspace-write|danger-full-access`：选择文件系统范围。默认是 `workspace-write`；Windows 跳过操作系统隔离。
- `--network` / `--no-network`：控制沙箱网络。默认允许。
- `--read-only` / `--no-read-only`：控制是否拒绝修改。只读 shell 命令仍然可用。
- `--yes`：批准确认。只在可信的非交互环境里使用。
- `--auto-review` / `--no-auto-review`：让模型复核 ask 级别的调用。
- `--max-steps N`：限制模型步骤数。
- `--context-window N`：覆盖模型上下文窗口。内置注册表之外的模型必须提供。
- `--json`：为一次性任务输出 JSONL 事件。
- `--steer TEXT`：在一次性任务的下一个模型步骤注入指导；可以重复。

## 会话

- `--list-sessions`：打印 ID、更新时间和标题后退出。
- `--continue`：恢复项目里最新的会话。
- `--resume`：恢复最新会话；`--resume ID` 恢复指定会话。
- `--sessions-dir PATH`：修改会话目录。相对路径按项目根目录解析。
- `--undo`：撤销最新会话的一次检查点操作后退出，不联系 provider，也不启动 MCP。用 `--resume ID --undo` 指定会话。它与 TUI `/undo` 共用对话和文件恢复协议。

`--continue` 和 `--resume` 不能同时使用。会话列表和自动续接会跳过没有非空用户消息的对话。新的交互式会话在收到第一条用户或助手消息之前，只把初始化记录留在内存里；只打开 TUI 或只运行 `/new` 不会创建会话文件。已有文件不会被自动删除。

## 目标

- `--goal TEXT`：运行前设置工作区目标。

## 交互命令

TUI 接受以下斜杠命令，并为模型、模式、配置和权限提供原生选择器。

- `/new`：新建会话。`/clear` 是别名，会保留已保存的对话。
- `/continue`：打开与 `/sessions` 相同的已保存对话选择器。
- `/resume`：打开已保存对话选择器；`/resume ID` 恢复该会话。
- `/sessions`：打开与 `/resume` 相同的选择器。
- `/tree [ENTRY]`：在同一个会话内浏览完整历史树。选中一条用户输入会把它放回编辑器；选中其他条目则从该点继续。位置在重启后保留，旧分支仍然存在。
- `/fork [N|ENTRY]`：把某条用户输入之前的历史复制到新会话，并把该输入放进编辑器。可以选择非活动分支；`N=1` 选中最近记录的输入。工作区文件不变。
- `/clone`：只把活动路径复制到新会话，并清空编辑器。
- `/undo`：撤销一轮用户输入或一次显式恢复操作。`/redo` 重新应用一次已撤销的操作，不联系模型，也不重跑工具。
- `/rewind`：在活动路径上选择一条用户输入，然后选择恢复对话、代码或两者。`/rewind ENTRY conversation|code|both` 直接指定范围，再打开确认预览。
- `/revert`：选择一段轮次和一个或全部已跟踪文件，不裁剪对话。`/revert START [END] -- PATH ...` 选择多个文件；含空格的路径要加引号，并使用正斜杠。END 包含在内；省略时包含最新操作。后续编辑如果冲突，会阻止恢复。
- `/compact [INSTRUCTIONS]`：总结较早的轮次，并应用可选指导。
- `/model [PROVIDER/MODEL]`：切换到已认证的精确模型，或在模型选择器里按部分名称搜索。
- `/effort [LEVEL]`：选择模型推理强度，或打开选择器。
- `/thinking [LEVEL]`：选择推理强度。用 `Ctrl+T` 折叠推理输出，不改变强度。
- `/verbose [auto|low|medium|high]`：调整交互式 TUI 的显示详细程度。默认 `auto`：任务执行期间逐条显示工具调用和折叠的 Thinking，不聚合连续的只读工具，也不显示工具输出正文；任务完成后自动将活动记录按用户轮次折叠为一行，Assistant 消息始终可见。完成后按 `Ctrl+O` 可以展开活动列表，`high` 可查看详细记录。`low` 隐藏工具和 Thinking，`medium` 显示折叠摘要，`high` 展开详情。不带参数时打开选择器。切换只影响显示，不改变工具执行、模型请求或持久化会话。折叠时 Inline TUI 可能清理并重放终端滚动历史。
- `/rollover [--force]`：开启新的上下文窗口；`/new-context` 是别名。
- `/new-context [--force]`：用 `NOTE.md` 作为交接，开启新的上下文窗口。先写好该文件，或用 `--force` 在没有它的情况下重置。
- `/mode [MODE]`：显示或切换执行模式。
- `/permissions [list|clear]`：列出或清除会话授权；不带参数时打开选择器。
- `/goal [TEXT|done|clear]`：显示、设置、完成或清除目标。
- `/context-window [SIZE|default]`：显示或调整当前上下文窗口。
- `/context`：显示按来源分类的用量和剩余输入空间，包括指令文件、技能、MCP schema 和已加载的 MCP 内容。OpenAI API key 模式使用输入计数接口；ChatGPT 套餐模式使用本地估算，因为 SIWC 未公开输入计数端点。DeepSeek 使用官方 V4 文本分词器并估算请求开销。最近一次请求的 provider 用量单独显示。
- `/btw QUESTION`：提出一个独立的旁路问题。
- `/steer TEXT`：为下一个模型步骤排队指导。
- `/queue [followup|steer|interrupt|TEXT]`：设置忙碌时 `Enter` 的行为，或将 TEXT 排到下一轮。
- `/stop`：中断等待中的模型请求，包括等待首个输出的阶段。工具会在自己的取消检查点停止。
- `/help`：打印命令列表。
- `/auth [status|login|logout]`：查看认证状态，或转到登录和登出。
- `/login`：选择认证方式和 provider。`/logout` 只删除选中的已保存凭据，不删除环境变量。
- `/connect`：选择 provider，认证后打开它的模型选择器。
- `/skill [NAME]`：为下一个任务激活技能。
- `/tool [list]`：查看可用工具及说明。
- `/config [KEY[=VALUE]]`：浏览配置，或更新项目设置。
- `/memory`：显示工作区记忆文件的路径。
- `/exit` 或 `/quit`：退出。

TUI 不注册 `/rename` 和 `/mcp`。恢复操作只改已跟踪文件，不处理任意 shell 副作用、外部服务或用户的 Git 索引。参见[恢复范围和冲突](snapshots-goals.md#检查点恢复)。`--undo` 不能与任务同时使用；两个撤销入口共用同一份持久操作历史。

## 交互输入

- `@path`：把工作区文件附加到任务。
- `$skill`：把技能正文加载到任务。
- `!command`：不联系模型，直接运行 shell 命令。权限检查仍然生效。

```text
Review @src/cade/harness/loop.py and use $review-only
!git status
```

任务执行期间，TUI 会继续读取输入：

- `/steer TEXT`：在下一个模型步骤追加指导。
- `/queue TEXT`：在当前任务结束后运行 TEXT。
- `/stop` 或 `Esc`：中断任务，不等待下一次模型输出。未送达的排队输入会和当前草稿一起回到编辑器。
- `Ctrl+C`：清空编辑器，不中断任务。500 ms 内再按一次退出。
- 其他文本：作为后续任务排队。

空闲且输入为空时，500 ms 内按两次 `Esc` 打开 `/rewind`。树导航、fork 和 clone 不会恢复工作区文件。在选择器或表单里，`Esc` 改为关闭当前交互。用 `Ctrl+C` 清空输入与用 `Esc` 取消任务是两回事。

指导在步骤之间生效，不会进入 provider 数据流或正在运行的工具。参见[引导与排队](design/steering-queue.md)。

## 退出码

- `0`：运行完成。
- `1`：运行未完成，或 `--undo` 没有找到会话或可撤销操作，或恢复失败。
- `2`：参数、配置或启动错误。
- `130`：登录被取消。

## 示例

```bash
cade --list-sessions
cade --continue -p "Summarize the current state."
cade --undo
cade --mode plan -p "Find the smallest fix for the failing test."
cade --mode build --yes -p "Fix the test and run pytest."
```
