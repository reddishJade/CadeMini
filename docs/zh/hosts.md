# 宿主

Cade 只有一个终端宿主，包含两种模式：交互式 TUI 和一次性执行。两者共用同一个会话日志、请求构造器、工具、策略和核心循环。TUI 用 `ApplicationSession` 处理命令和会话控制。

## 终端 UI

没有 `-p` 或 `exec` 时，CLI 在交互式终端里启动 TUI：

```bash
cade
cade "Fix the failing test."
```

位置参数会作为 TUI 的第一条消息发送一次。任务完成后，TUI 保持打开。

TUI 把对话放在可滚动记录里，显示流式 assistant 文本和工具进度，并在应用内处理审批和提问。常用按键：

- `Enter`：提交消息；任务运行时按照当前输入模式排队、引导或中断。
- `Alt+Enter`：在下一个模型步骤引导正在运行的任务。
- `Alt+S`：引导当前任务；`Alt+Q`：排队后续任务。这两个快捷键以固定行为提交编辑器中的普通文本，不受 `Enter` 当前输入模式影响。
- `Shift+Tab`：在 `ask`、`build` 和 `plan` 之间循环。
- `Esc`：中断正在运行的任务，包括还没有产生输出的模型请求。未送达的排队输入回到编辑器。
- 空闲且输入为空时，连按两次 `Esc` 打开 `/rewind` 检查点选择器。用 `/tree` 在同一会话中切换分支，用 `/fork` 将历史复制到新会话。
- `Ctrl+C`：清空输入，不中断任务；500 ms 内再按一次退出。
- `Ctrl+D`：空闲且输入为空时连按两次退出。
- `Ctrl+O`：展开或折叠工具详情；`Ctrl+T`：展开或折叠推理输出。
- `Ctrl+Q`：退出。

TUI 为模型、模式、推理强度、配置和权限打开原生选择器。在 `/config` 中选 **TUI Rendering**，再选 **Inline** 或 **Fullscreen**，下次启动生效。Inline 使用终端原生滚动历史；Fullscreen 使用备用屏幕，按 PageUp/PageDown 浏览对话。按 `?` 或运行 `/help` 查看按键和命令参考。

TUI 要求 stdin 和 stdout 都是终端。在管道或非交互进程里，使用 `-p TASK` 或 `exec TASK`。

对话记录会显示上下文预算不足和输出令牌达到上限的停止原因；如果提供方返回错误详情，也会一并显示。

## 一次性执行

提供 `-p` 或 `exec` 时，CLI 运行一次后退出：

```bash
cade -p "Fix the failing test."
cade exec "Fix the failing test."
```

一次性模式支持 `--json`，输出 JSONL 事件流。在自动化和 CI 中，优先使用 `--read-only` 或显式规则，而不是不加区分地使用 `--yes`。
