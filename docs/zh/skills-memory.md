# 技能与记忆

## 技能

Cade 从两个根目录发现 `SKILL.md`：

1. `~/.cade/skills/<name>/SKILL.md` 是用户级技能。
2. `<project>/.cade/skills/<name>/SKILL.md` 是项目技能。

后一个根目录会覆盖同名的前一个。技能文件可以以 YAML frontmatter 开头：

```markdown
---
name: release-check
description: Validate release metadata before tagging.
---

Run the release checklist...
```

`name` 默认取父目录名。`description` 默认取正文第一行非空内容。非法名称和空正文会被跳过。

请求会收到一份紧凑的 `<available-skills>` 目录，包含名称和描述。模型按需用 `skill` 工具加载正文：

```json
{"name": "release-check"}
```

技能正文是工具结果中的事实，不属于请求专属上下文。重复加载同一技能没有副作用，但会多出一条工具结果。

## 记忆

项目根目录的 `MEMORY.md` 存放长期事实。每个任务都会把它作为 `<memory source="MEMORY.md">` 加入请求上下文，上限 32 KiB。

模型用 `memory` 工具追加事实：

```json
{"text": "Prefer pytest and keep changes minimal."}
```

`memory` 只写 `MEMORY.md`，不能写任意路径。写入上限是 64 KiB。文件写满后，手动删除过期条目。

记忆与其他状态文件的区别：

- `AGENTS.md` 是项目指令，归项目所有。
- `NOTE.md` 是短期工作状态，会随压缩一起总结。
- `MEMORY.md` 是长期偏好或约定。
- 会话日志记录发生过什么。

## 请求上下文

指令、技能目录、`NOTE.md`、`MEMORY.md` 和活动目标都只属于请求上下文。它们出现在每次请求审计里，但不会变成对话消息。见[会话与上下文](sessions-context.md)。
