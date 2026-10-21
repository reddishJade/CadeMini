# 持久化目录

Cade 把持久历史和宿主状态分开。会话日志是模型看到什么、发生过什么的权威来源；其他存储职责更窄。

| 产物 | 默认位置 | 所有者 | 生命周期 | 格式 |
| --- | --- | --- | --- | --- |
| 会话日志 | `.cade/sessions/session-*.jsonl` | `SessionLog` | 项目 | 当前封闭结构 |
| 快照 | `.cade/snapshots/` | `SnapshotStore` | 项目 | 本地索引 |
| 恢复 | `.cade/recovery/` | 恢复协调器 | 工作区 | 封闭记录；操作日志和待处理计划 |
| 目标 | `.cade/goal.json` | `GoalStore` | 项目 | 单个对象 |
| 工作笔记 | `NOTE.md` | 项目 | 项目 | Markdown |
| 记忆 | `MEMORY.md` | `MemoryTool` | 项目 | Markdown |
| 指令 | `AGENTS.md`、`CLAUDE.md`、覆盖文件 | 项目 | 项目 | Markdown |
| 技能 | `~/.cade/skills`、`.cade/skills` | 发现逻辑 | 用户或项目 | `SKILL.md` |
| 配置 | `cade.json` | `CadeConfig` | 项目 | Pydantic，严格校验 |
| 凭据 | `~/.cade/auth.json` | `JsonCredentialStore` | 用户 | provider 凭据 |
| SIWC 宿主 ID | `~/.cade/host.json` | `load_or_create_host_id` | 用户 | 稳定的代理宿主标识 |
| 文档站点 | `docs/index.html` | 文档工具 | 仓库 | 生成 |
| LLM 目录 | `docs/llms*.txt` | 文档工具 | 仓库 | 生成 |

## 会话日志

日志是追加式的。它把模型可见事实和审计事实存放在同一棵树里。重建对话和当前历史位置只需要这一个产物。压缩、窗口和分支导航都是日志记录，不是改写。

## 快照

快照是文件变更的撤销日志，覆盖 `write`、`edit` 和常见的单命令 shell 修改。它使用原子替换的 JSONL 索引、不可变的前后记录和按内容寻址的原始内容块。冲突和旧版行为见[快照与目标](../snapshots-goals.md)。快照不属于会话分支。

## 目标与笔记

目标是一个带 active 或 done 状态的对象。`NOTE.md` 是工作状态，会进入每次请求，并在压缩时被总结。`MEMORY.md` 是长期项目知识。这些文件刻意保持朴素：可以手工编辑，也可以用普通工具查看。

## 生成的产物

文档站点、`llms.txt` 和 `llms-full.txt` 由 Markdown 和 `docs.json` 生成。它们会检查是否最新；不要手工编辑 HTML。

## 不持久化的内容

- 请求专属上下文记录在请求审计里，不作为消息。
- 授权只在进程内有效；长期策略属于配置规则。
- 审批提示和 TUI 状态属于宿主状态。
- provider 重放载荷保存在产生它的 assistant 事实里。

字段级细节见[会话日志格式](../reference/session-schema.md)，格式变更见[变更策略](../../persistence/change-policy.md)。
