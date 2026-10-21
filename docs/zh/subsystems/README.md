# 子系统

每一页说明一个子系统的类型、行为和失败模式。[架构](../architecture.md)是地图，这些页面是参考。

- [日志](log.md)：追加、加锁、分支、列表和修复。
- [投影与请求](projection-request.md)：确定性上下文和预算准入。
- [循环](loop.md)：provider 调用、工具、审批、重试和事实。
- [工具与策略](tools-policy.md)：工作区工具、模式、规则、授权、shell 分类和审批。
- [上下文与压缩](context-compaction.md)：摘要、窗口、历史和请求上下文。
- [扩展](extensions.md)：技能、记忆、钩子、MCP、子代理、快照、目标和宿主。

子系统页面描述当前行为。历史决策写在[决策记录](../../decisions/README.md)，事故写在[事故复盘](../../postmortem/README.md)。
