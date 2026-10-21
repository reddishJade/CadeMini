# 子代理

## 子会话

`subagent` 工具在独立会话中运行一个子任务，返回最终答案和子会话 ID：

```json
{"prompt": "Find the failing test and report the file and line."}
```

子会话：

- 继承父级的 provider、预算、策略、审批者和请求上下文；
- 获得基础工具，以及绑定到自己日志的 history 工具；
- 不再获得 `subagent` 工具，因此不能递归；
- 最多使用 20 个步骤；
- 把自己的会话写在 `<sessions>/subagents/` 下。

父会话只记录工具调用和返回的摘要。详细证据留在子会话日志里。

`delegate` 用同一个运行器处理一个或多个任务：

```json
{"tasks": ["Inspect the parser", "Add a regression test"], "mode": "build"}
```

任务在各自独立的子会话中顺序运行。每个结果都包含子会话 ID；父会话收到一条合并的工具结果。子级模式不能扩大父级权限：Plan 模式的父会话不能委派 Build 工作。

## 限制

子代理顺序运行，共享父级审批上下文，不并行执行。
