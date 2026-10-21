# Cade

Cade 是一个独立运行的编码代理。整个设计围绕一条规则：

> 会话日志是唯一事实来源。模型可见的历史是确定性的投影。请求构造只产生当前请求。

这样得到的代理体积小，能在压缩、上下文窗口切换和进程重启后继续工作，不会编造历史。

## 从这里开始

- [快速上手](quickstart.md)：安装并跑通第一个任务。
- [CLI](cli.md)：一次性任务、TUI、会话和恢复命令。
- [配置](configuration.md)：`cade.json` 的字段。
- [架构](architecture.md)：日志、投影、请求和执行流水线。
- [会话与上下文](sessions-context.md)：恢复、压缩、裁剪和历史检索。
- [工具与安全](tools-security.md)：模式、规则、授权和 shell 分析。
- [快照与目标](snapshots-goals.md)：撤销和持久目标。
- [技能与记忆](skills-memory.md)：`SKILL.md` 和 `MEMORY.md`。
- [MCP 和子代理](mcp-subagents.md)：扩展机制。
- [宿主](hosts.md)：终端 TUI 和一次性执行。
- [开发](development.md)：检查、测试和文档流水线。
- [对齐验收](parity.md)：与 Cade 的验收对照。

## 设计

- [Cade 如何工作](design/how-it-works.md)：端到端模型的简版。
- [代码地图](design/code-map.md)：包边界和入口。
- [与 Cade 的关系](design/cade-comparison.md)：独立维护的边界。
- [能力接缝](design/capability-seams.md)：可替换的契约。
- [事件模型](design/event-model.md)：事实与审计记录的区分。
- [工具执行](design/tool-execution.md)：从策略到结果的流水线。
- [配置目录](design/config-catalog.md)和[事件目录](design/event-catalog.md)：生成的参考页。
- [工具目录](design/tool-catalog.md)：内置工具清单。
- [MCP 参考服务器](design/mcp-reference.md)：支持的服务器模式。
- [引导与排队](design/steering-queue.md)：TUI 中的并发输入。
- [模块图](design/module-graph.md)：由导入关系生成。
- [持久化目录](design/persistence-catalog.md)：所有产物的位置。
- [术语表](design/glossary.md)：统一用词。
- [测试](design/testing.md)：测试与层次对应关系。

生成的参考页保留英文标题和字段名，因为内容直接来自代码标识符。

## 子系统

[子系统](subsystems/README.md)是日志、投影与请求、执行循环、工具与策略、上下文与压缩、扩展的实现参考。

## 操作指南

[操作指南](cookbook/README.md)包含修 bug、加功能、只读评审、长会话和自定义 MCP 服务器的步骤。

## 历史记录

以下页面保留英文，记录项目过去的状态：

- [事故复盘](../postmortem/README.md)：事故和修复。
- [持久化](../persistence/README.md)：当前格式和变更策略。
- [升级指南](../upgrade/README.md)：破坏性变更。
- [决策记录](../decisions/README.md)：长期设计选择。

## 设计与参考

[会话格式](reference/session-schema.md)定义持久化契约。历史研究和验收记录描述项目独立之前的状态：

- [Cade 设计](../history/design-proposal.md)
- [会话日志格式](reference/session-schema.md)
- [Harvest 记录](../history/harvest-audit.md)
- [对齐验收](../history/parity-acceptance.md)

## 构建文档

文档以 Markdown 为准。一个小工具校验链接和锚点，生成单页站点，并为代理生成 `llms.txt` 目录：

```bash
uv run --locked python -m cade.docs check
uv run --locked --extra docs python -m cade.docs build
uv run --locked python -m cade.docs llms --write
uv run --locked python -m cade.docs catalogs --write
```

英文站点是 [docs/index.html](../index.html)。本目录的中文站点是 [index.html](index.html)，构建命令：

```bash
uv run --locked python -m cade.docs --root docs/zh build --out docs/zh/index.html
```

生成的目录是 `docs/llms.txt`、`docs/llms-full.txt`，以及 `design/` 下的配置、事件和模块图页面。
