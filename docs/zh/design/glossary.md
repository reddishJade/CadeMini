# 术语表

每个概念只用一个规范术语。链接指向负责说明它的页面。

## 会话日志（session log）

记录事实的追加式 JSONL 文件，是活动分支的事实来源。见[日志](../subsystems/log.md)。

## 分支（branch）

条目树中的一条路径。活动分支结束于最新记录，供投影使用。

## 投影（projection）

由分支生成的确定性对话。它应用最新的 system、compaction 和 window 切点，然后配对工具调用和结果。见[投影与请求](../subsystems/projection-request.md)。

## 请求（request）

一次模型调用中，对话经过准入后的 provider 就绪形式。它包含工具、预算和请求专属上下文。请求不是事实，永远不会被投影。

## 运行（run）

循环针对一个任务的一次调用。一次运行包含一个或多个步骤，以 `run` 事实结束。

## 步骤（step）

一次模型请求，加上这次响应引发的工具调用。

## 工具结果（tool result）

一次工具调用的记录输出，按调用 ID 配对。它属于事实。

## 压缩（compaction）

追加摘要和 `first_kept_id` 的事实。它在后续投影中遮蔽较早的消息，但不删除它们。

## 窗口（window）

把上下文起点前移的事实，可附带交接。压缩放不下时用作回退。

## 历史（history）

在压缩或裁剪之后，按条目 ID 或调用 ID 检索精确证据的工具。

## 模式（mode）

`plan`、`build` 或 `ask`。模式选择工具和 shell 命令的默认策略。

## 规则（rule）

配置中的静态策略条目。规则可以匹配工具、路径前缀和命令前缀，返回 allow、deny 或 ask。

## 授权（grant）

回答 `a` 时创建的进程内审批。它匹配相同的工具和路径或命令。

## 技能（skill）

按需加载的 `SKILL.md` 正文。请求携带技能目录；`skill` 工具加载正文。

## MCP

Model Context Protocol。Cade 运行 stdio MCP 服务器，并把它们的工具暴露为 `mcp__<server>__<tool>`。

## 子代理（subagent）

拥有独立会话和受限工具集的子运行。它不能再生子代理。

## 快照（snapshot）

文件修改前的副本，供一次性任务的 `--undo` 使用。

## 目标（goal）

`.cade/goal.json` 中的持久目标，与会话格式分离。
