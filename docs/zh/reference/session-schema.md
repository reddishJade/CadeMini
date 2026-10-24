# Cade 会话格式

> 这是当前的持久结构和投影契约。记录没有格式版本字段。

## 信封

`session-<id>.jsonl` 每行一个 JSON 对象：

```json
{"id":"e2","parent":"e1","ts":"2026-10-18T03:00:00+00:00","type":"user","data":{"text":"Fix the test","source":"user"}}
```

| 字段 | 类型 | 契约 |
| --- | --- | --- |
| `id` | string | 文件内唯一；12 个小写十六进制字符 |
| `parent` | string 或 null | 通常是前一个头部；导航时是选中的续接点；根记录为 null |
| `ts` | string | ISO 8601 UTC 时间戳 |
| `type` | string | 下方事件类型之一 |
| `data` | object | 类型专属载荷 |

头部是最后一条物理记录。普通记录指向前一个头部。`branch` 记录则指向选中的历史续接点。其他路径仍留在同一个文件里。非空父记录必须在子记录之前。

未知事件类型非法，不要忽略。

载荷在追加和读取时都会校验。未知字段、错误的原始类型、非法枚举和非有限数字都会被拒绝。缺失的可选字段允许存在，但默认值不会写进记录。`user.source` 默认是 `user`；assistant 的 model/provider/usage/replay 元数据以及可空的 artifact/handoff/usage/details 字段可以缺失。跨事件的调用配对和上下文切点引用由投影校验。

## 事件类型

图片工具把附件写入 `tool_result.artifact.tool_metadata.images`。
每张图片包含 `data`（base64）和 `mimeType`。
投影恢复文本和图片，请求快照把图片记录在工具消息的 `images` 字段。
Responses 适配器将其发送为函数输出中的 `input_image`。
工具负责生成附件数据，数据随会话日志保留。

### 对话事件

这些事件产生模型可见的消息。

| 类型 | 载荷 | 投影 |
| --- | --- | --- |
| `system` | `{"text": str}` | 最新一条最先输出；永不被遮蔽 |
| `user` | `{"text": str, "source": "user"\|"runtime"}` | 在所在位置输出用户消息 |
| `assistant` | `{"blocks": [Block], "model": str, "provider": str, "usage": object\|null, "provider_state": object\|null, "reasoning": str\|null, "response_metrics": object\|null}` | 助手消息；空 blocks 被跳过 |
| `tool_result` | `{"call_id": str, "status": "ok"\|"error"\|"interrupted", "content": str, "artifact": object\|null}` | 工具消息；与对应调用配对 |
| `compaction` | `{"summary": str, "first_kept_id": str, "tokens_before": int, "model": str, "provider": str, "usage": object\|null, "details": object\|null}` | 先输出摘要消息，再从 `first_kept_id` 输出尾部 |
| `window` | `{"start_id": str, "reason": "manual"\|"fallback", "handoff": str\|null}` | 从 `start_id` 开始投影；可选交接摘要 |

block 是以下之一：

```json
{"type":"text","text":"..."}
{"type":"tool_call","id":"c1","name":"bash","args":{"command":"pytest -q"}}
```

`assistant.blocks` 是工具调用的唯一记录。不要另外添加 `tool_use` 事件。`tool_result.call_id` 与它配对。

`provider_state` 是 Responses 重放用的不透明适配器数据。它保存在 assistant 事实里，并传回同一个适配器。它永远不会进入对话词汇。

harness 负责可选字段 `assistant.reasoning` 和 `assistant.response_metrics`。
`reasoning` 保存 provider 流式输出的推理摘要。
`response_metrics` 包含 `ttft_seconds` 和 `tokens_per_second`，值为非负有限数或 null。
TTFT 包含等待首个非空文本或推理增量的时间。
TPS 使用服务端输出 token 数，除以首个增量到响应结束的时间。
请求失败或取消时，TPS 为 null。
这些字段随 assistant 记录保留，用于恢复宿主显示，不进入模型消息或 provider 重放数据。
重试丢弃的失败尝试不会成为 assistant 记录。

TUI 根据 `run.termination` 和 `run.error` 恢复停止提示，不保存格式化提示，也不重复保存回答。
见[决策 0016](../../decisions/0016-durable-response-observations.md)。

### 仅日志事件

这些事件会被记录，但不会投影。

| 类型 | 载荷 | 用途 |
| --- | --- | --- |
| `meta` | `{"session": str, "project": str, "created": str}` | 第一条事件；标识会话 |
| `request` | `{"model": str, "provider": str, "tools_digest": str, "conversation_digest": str, "context": [str], "estimated_tokens": int, "dropped": [str], "snapshot": object}` | 带归一化输入的请求审计 |
| `run` | `{"termination": str, "steps": int, "usage": object\|null, "error": str\|null}` | 运行结果 |
| `attempt` | `{"model": str, "provider": str, "error": str, "usage": object\|null}` | 没有产生消息的失败模型尝试 |
| `branch` | `{"selected_id": str}` | 导航控制；永远不是模型消息 |
| `checkpoint` | `{"checkpoint_id": str, "user_id": str, "anchor_id": str\|null, "phase": "started"\|"completed"\|"incomplete"}` | 回合所有权和完成状态审计 |
| `restore` | `{"operation_id": str, "target_id": str\|null, "draft_id": str\|null, "scope": "conversation"\|"code"\|"both", "kind": "undo"\|"redo"\|"rewind"\|"revert"}` | 恢复提交和对话位置 |

`request.snapshot` 包含 `messages`、`tools`、`options`、`budget` 和 `report`。`budget` 记录上下文窗口、输出预留和余量；`report` 记录准入结果。`options` 省略 API key、所有请求头、取消信号和回调。这是 provider 接口的输入，不是 HTTP 抓包。其余请求数据不做脱敏。

失败的尝试不会变成 assistant 消息。运行结果不重复最终答案；答案就是最后一段 assistant 文本。

## 不变式

1. **一个事实一条记录。** 工具调用只出现一次，在它的 assistant 事件里。最终答案不会再次保存。
2. **没有派生内容。** 没有替换用的消息列表，没有把渲染预览当历史，没有展示摘要。
3. **决策也是事实。** 压缩切点和窗口起点会记录在案；它们产生的消息重新计算。
4. **请求上下文是审计，不是历史。** 动态上下文和请求信封放在 `request` 里；只有对话事件参与投影。
5. **压缩切点是用户轮次。** `first_kept_id` 必须指向压缩之前的 `user` 事件。工具调用和结果不能跨越切点。
6. **窗口起点是用户轮次。** `start_id` 必须指向窗口之前的 `user` 事件。
7. **最新 system 事件生效。** 它最先投影，不会被压缩或窗口遮蔽。

## 分支导航

`branch.selected_id` 必须引用同一个文件里更早的条目。该条目是用户输入时，导航事件的父节点必须等于该输入的父节点。否则父节点必须等于选中条目的 ID。选中旧版根用户时，可能产生父节点为 null 的导航事件。

导航事件成为物理头部。`branch()` 沿父链重建活动路径。兄弟事实不会被删除或复制，`project()` 忽略导航记录。活动路径上最新的导航会在重启后恢复选中的用户文本，直到后续 user 或 assistant 条目消费它。选中非用户条目时恢复空草稿。没有可变的游标附属文件。

导航和路径复制会获取整个会话的运行锁。非法目标在尾部修复或追加之前失败。Fork 和 clone 把一条连通路径复制到新文件，省略导航、检查点、恢复控制，保留时间戳，并重映射条目 ID 和上下文切点。

## 检查点与协调恢复

宿主生命周期在 user 事实之前创建 workspace 检查点。它的 `started` 事件把保存的检查点关联到较早的用户条目和回合前祖先 `anchor_id`。`completed` 或 `incomplete` 事件在执行后关闭回合。完成描述的是已跟踪的捕获，不是模型成功。核心校验用户和祖先引用；文件镜像和恢复操作历史属于工作区恢复存储。

只有当待处理计划的文件写入成功后，才追加 restore 事件。它的父节点等于 `target_id`，必须是较早的条目或 null。只恢复代码时，目标必须是紧邻的前一个物理头部，以保留对话血统。`draft_id` 为 null，或指向一个较早的用户条目，其文本会回到编辑器。新的对话消息会消费历史草稿。旧版根恢复缺少 system 事实时，待处理计划会先提供一个当前系统提示作为新的显式事实，然后才确认操作。

工作区操作日志和 `pending.json` 计划使用封闭模型。稳定的操作 ID 在中断后对会话提交和操作提交去重。遇到未知的外部文件状态时，恢复停止并阻止新写入。见[检查点恢复](../snapshots-goals.md#检查点恢复)。

## 投影

`project(branch) -> list[Message]` 是纯函数，结果确定。

1. 找到最新的 `system` 事件，最先输出。
2. 找到最新的 `window`。存在时，从它的 `start_id` 开始，并保留非空 `handoff` 作为摘要消息。
3. 找到窗口起点处或之后最新的 `compaction`。存在时，输出摘要，并从 `first_kept_id` 开始输出尾部。
4. 按顺序遍历保留的条目。输出 `user`、`assistant` 和 `tool_result` 消息。跳过 `system`、`meta`、`window`、`compaction`、`request`、`run`、`attempt`、`branch`、`checkpoint` 和 `restore`。
5. 按调用 ID 配对工具调用和结果。
6. 末尾存在未闭合调用时，合成一条“已中断”结果。不要写入日志。把它插在包含该调用的 assistant 消息之后。
7. 遇到孤立结果、重复结果、重复调用、错误切点或错误窗口时，用 `ProjectionError` 拒绝。

摘要消息的输出顺序：system、window 交接、compaction 摘要、保留的尾部。

线上格式适配器把 `SummaryMessage` 渲染成包裹在 `<compaction-summary>` 或 `<window-handoff>` 里的 user 角色消息。内部角色保持独立，宿主可以按不同方式渲染。

## 存储

- 目录：默认 `.cade/sessions/`。
- 文件：`session-<id>.jsonl`。
- 新的交互式会话把文件创建推迟到第一条 user 或 assistant 消息。初始化事件留在内存中，随该消息一起刷盘。第一次请求被取消时，user 消息仍会留在磁盘上。
- 追加锁：`session-<id>.lock`。
- 运行锁：`session-<id>.run.lock`；并发回合被拒绝。
- 追加：一行，`flush`，`fsync`。
- 追加前：截断崩溃留下的不完整尾行。
- 读取时：忽略不完整的尾行；拒绝任何更早的非法行。
- 拒绝重复 ID 和向前的父引用。
- 不改写，不原地编辑。

## 压缩记录

```json
{"id":"e80","parent":"e79","ts":"...","type":"compaction",
 "data":{"summary":"## Goal\n...","first_kept_id":"e52","tokens_before":48210,
         "model":"gpt-5.6-luna","provider":"chatgpt",
         "usage":{"input":43000,"output":1200},"details":null}}
```

摘要是模型生成的内容，必须记录在案。`first_kept_id` 之前的原始事件仍留在日志里。后续压缩从前一次之后开始，可以把前一次摘要纳入输入。

## 窗口记录

```json
{"id":"e90","parent":"e89","ts":"...","type":"window",
 "data":{"start_id":"e88","reason":"manual","handoff":"Optional note."}}
```

窗口是硬回退，压缩是默认方案。窗口可以由 `/new-context` 显式开启，也可以在压缩不足以缩减时自动开启。

## 开发中的变更

当前只有一种结构，没有格式版本字段、兼容读取器或迁移层。破坏性变更要同时更新生产者、读取器、测试和文档。见[变更策略](../../persistence/change-policy.md)。
