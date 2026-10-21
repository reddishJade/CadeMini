# 会话与上下文

会话默认是 `.cade/sessions` 下的追加式 JSONL 文件，文件名是 `session-<id>.jsonl`。标题取自第一条用户消息，不需要额外的事件类型。

## 恢复会话

```bash
cade --list-sessions
cade --continue "Continue where we stopped."
cade --resume SESSION_ID "Explain the current state."
```

`--continue` 和 `--resume` 打开最新会话或指定会话。已有会话保留原来的 system 事实；新的 `cade.json` 不会改写历史。如果恢复的会话没有 system 记录，就补上当前提示。

## 树、fork 和 clone

| 命令 | 结果 |
| --- | --- |
| `/tree [ENTRY]` | 在同一个会话内切换位置，保留所有分支 |
| `/fork [N\|ENTRY]` | 把选中用户输入之前的历史复制到新会话 |
| `/clone` | 把当前活动路径完整复制到新会话 |

`/tree` 显示完整历史树。选中一条用户输入会把文本放回编辑器，并从它的父节点继续。选中其他条目则在该条目之后继续，编辑器为空。导航位置和待处理的输入在重启后保留。下一次模型请求只包含选中的路径。空闲且输入为空时，连按两次 Esc 会打开独立的 `/rewind` 检查点选择器。

`/fork` 可以选择活动分支和非活动分支里的用户输入。`/fork N` 选中第 N 条最近记录的输入；`/fork ENTRY` 直接选中条目。精确条目 ID 优先于数字轮次选择器。选中的输入会回到新会话的编辑器。`/clone` 清空编辑器，只复制活动路径，不复制其他分支。副本获得新的条目 ID，保留时间戳，并重映射 compaction/window 引用。导航和检查点控制不会复制。副本不继承源会话的工作区撤销栈。

这三种操作都不改工作区文件。fork 和 clone 不改源会话。它们不实现对话或文件撤销，也不会为被放弃的分支生成自动摘要。没有活动任务时，运行历史才会变化。

持久字段见[会话格式](reference/session-schema.md)，文件恢复边界见[恢复](snapshots-goals.md#检查点恢复)。

## 检查点恢复

`/undo` 和 `/redo` 撤销或重应用一个操作。一轮根用户输入算一个操作，包括同步子代理的编辑。显式 rewind 和 revert 也是单个可逆操作。新操作会丢弃当前的重做链；原有对话分支仍然保留。在操作链之外导航会阻止过期的撤销/重做，用 `/rewind` 选择想要的位置。

`/rewind` 恢复到选中活动路径用户输入之前的位置，并询问恢复对话、已跟踪代码或两者。`/revert` 只选择一段轮次和文件子集里的代码变更。只恢复代码会保留对话消息，并追加一条有审计记录的请求上下文通知。它不回退 provider 设置、权限、凭据或其他宿主状态。

没有自有检查点的旧轮次或复制轮次只能恢复对话。文件覆盖范围、冲突和中断事务见[快照与目标](snapshots-goals.md#检查点恢复)。

## 压缩

完整对话超出输入预算时，`Compactor` 会：

1. 在可见的用户边界选择切点。
2. 把较早的消息发给 provider 生成摘要。
3. 追加一条 `compaction` 事实，包含摘要和 `first_kept_id`。
4. 把对话重新投影为最新 system、摘要和保留的尾部。

压缩是迭代的：之前的摘要会序列化为 `PRIOR SUMMARY`，可以再次总结。原始 user、assistant 和 tool-result 事实仍留在日志里。

## 请求准入

请求构造保留整个投影，包括工具输出。只有记录在案的 compaction 和 window 事件会缩减可见历史。如果这些步骤之后请求仍超出预算，循环返回 `context_error`，不发给 provider。用 `history` 工具检索当前上下文之外的证据。

## 上下文窗口

`window` 事实在某个用户条目处开启新上下文，可以附带交接。TUI 可以用 `/new-context` 显式开启。如果压缩仍无法把请求带入预算，循环可以追加自动 window，附带指向 `NOTE.md` 和 history 工具的固定交接。

## History 工具

`history` 接受以下参数之一：

- `query`：在分支上做不区分大小写的搜索，返回条目 ID 和片段。
- `entry_id`：某条日志记录的完整内容。
- `call_id`：某次调用的完整工具结果。

完整内容有上限。搜索结果也有上限，并指回条目 ID。

## 请求上下文顺序

每个任务的请求依次包含：

1. system 消息。
2. 来自 `AGENTS.md`、`CLAUDE.md` 或其变体的项目指令。
3. 技能目录。
4. `NOTE.md` 工作状态。
5. `MEMORY.md` 长期事实。
6. 活动目标。

第 2 到 6 项只属于请求上下文。它们记入请求审计，但不会变成对话事实。参见[架构](architecture.md)和[设计](../history/design-proposal.md)。
