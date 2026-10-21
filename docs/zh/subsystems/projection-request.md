# 投影与请求

## 投影

`project(entries)` 是纯函数。它会：

1. 找到最新的 `system` 记录。
2. 应用最新的 `window` 起点。
3. 应用最新的 `compaction` 切点，并生成一条摘要消息。
4. 把 user、assistant 和 tool-result 事实转换成消息。
5. 按调用 ID 配对工具调用和结果。
6. 分支在调用中途结束时，插入一条合成的“已中断”结果。

它拒绝孤立结果、重复结果和没有指向用户条目的切点。投影不调用 provider，也不写日志。

## 请求

`build(conversation, tools, budget, context)`：

1. 拼接基础系统提示和请求专属上下文。
2. 估算 token。
3. 保留所有投影消息和工具正文。
4. 转换成 provider 消息和工具定义。
5. 返回 `Request` 和 `BuildReport`。

`BuildReport` 记录估算 token 和预算。旧的 dropped-call 和轮次计数器保持为空或零。完整请求放不下时，`require_sendable()` 抛出 `RequestTooLarge`。循环只通过记录在案的 compaction 和 window 决策缩减上下文，随后拒绝仍然溢出的请求。

## 上下文顺序

系统消息携带基础指令，并按顺序附加：指令、技能目录、NOTE、记忆和目标。这些输入只属于请求。请求审计记录它们的完整文本、准入的消息、工具定义、非认证选项和预算。会话文件可能包含敏感输入。

## 测试

`test_projection.py`、`test_request.py` 和 `test_compaction.py` 覆盖切点、严格准入和线上格式契约。
