# 引导与排队

任务运行时，TUI 继续接受输入。UI 线程渲染事件、处理按键；bridge 在带独立 `asyncio` 事件循环的工作线程里运行任务。

## 当前设计

```text
prompt_toolkit UI 线程
  |  Enter / Alt+Enter / 斜杠命令
  v
ApplicationSession
  |  steering、pending、cancel
  v
工作线程  --  asyncio.run(run_task)
  |
  v
结构化事件队列  -->  UI 线程
```

- `ApplicationSession.run_task` 调用与一次性模式相同的 `run` 循环。
- `service.steering` 保存下一个模型步骤的指导。
- `service.pending` 保存下一轮的后续任务。
- `service.cancel` 立即唤醒等待中的 provider 流，即使它还没有输出。工具在安全点检查同一个取消令牌。
- bridge 把循环事件转换成结构化事件，排队交给 UI 线程。provider 流从不阻塞按键处理。

## 运行中的命令

| 输入 | 效果 |
| --- | --- |
| `Enter` | 使用当前的忙时输入模式：排队、引导或替换 |
| `Alt+Enter` | 在下一个模型步骤追加指导 |
| `/steer TEXT` | 在下一个模型步骤追加指导 |
| `/queue TEXT` | 把 TEXT 排为后续任务 |
| `/queue followup` | 让 `Enter` 排队后续任务 |
| `/queue steer` | 让 `Enter` 追加指导 |
| `/queue interrupt` | 让 `Enter` 停止任务并立即运行该消息 |
| `/stop` 或 `Esc` | 中断工作，把未送达的排队输入放回编辑器 |
| `Ctrl+C` | 清空编辑器，不取消工作；500 ms 内再按一次退出 |
| `/exit` 或 `/quit` | 退出 TUI |

当前任务进入终止状态时，后续任务自动运行。队列按先进先出，排队的任务留在同一个会话。显式按 Esc 或用 `/stop` 取消时，未送达的输入会回到编辑器，不会立刻开始另一次运行。

`/steer` 不是向正在运行的 provider 请求注入提示。循环在每个步骤之前检查引导队列，追加一条带 `source: runtime` 的 `user` 事实，再从投影构造下一次请求。这样会话日志仍是唯一事实来源。

## 会话命令

`/new`、`/resume`、`/fork`、`/clone`、`/compact` 和 `/new-context` 会改变会话状态。在没有活动任务时运行它们。运行期间，普通文本改为排队。

## 限制

- 文件工作进程会先完成当前操作，再响应取消。Shell 工作进程会向子进程发信号。两者都不回滚文件修改；需要恢复时用快照。
- 引导在步骤之间生效，不会进入 provider 数据流或正在运行的工具。
- 一次性任务的 `--steer` 参数在第一步之前预置指导，不能替代运行期间的 `/steer`。

## 测试

`test_mid_run_steer_reaches_the_next_provider_request` 会阻塞一个工具，直到宿主送达指导，然后检查下一次 provider 请求包含引导事实。
