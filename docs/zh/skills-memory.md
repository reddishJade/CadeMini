# 技能与记忆

## 技能

Cade 从 `~/.cade/skills/` 和 `~/.agents/skills/` 发现用户级技能。将 `trust_project_skills` 设为 `true` 后，还会扫描项目内的对应目录。项目技能默认关闭。

显式配置的 `skills_dir` 可信且优先级最高，其后是项目级和用户级目录。同一范围内，`.cade/skills` 优先于 `.agents/skills`。同名技能保留优先级较高的定义，并记录警告。

每个技能目录必须包含带 YAML frontmatter 的 `SKILL.md`：

```markdown
---
name: release-check
description: Validate release metadata before tagging.
---

Run the release checklist...
```

加载器校验必填字段、名称规则、目录名是否一致、长度和元数据类型。无效定义会被跳过，并记录诊断。发现阶段只读取 frontmatter；激活时才读取正文并扫描 `references/`、`scripts/` 和 `assets/`。

请求会收到一份紧凑的 `<available-skills>` 目录，包含名称和描述。模型按需用 `skill` 工具加载正文：

```json
{"name": "release-check"}
```

结果包含技能根目录的绝对路径和资源清单。按需读取引用文件：

```json
{"name": "release-check", "reference": "checklist.md"}
```

引用名相对于 `references/`。隐藏文件、符号链接、二进制引用和逃出技能根目录的路径会被拒绝。引用内容上限为 50 KiB，截断时会标明。脚本通过普通 `bash` 工具执行，沿用其权限策略。原生沙箱允许读取可信技能目录，但禁止写入；其余私有 `.cade/` 状态仍保持隐藏。

用户也可以用 `$release-check` 或 `/skill release-check` 显式激活技能。`disable-model-invocation: true` 会从自动选择目录中隐藏技能，但保留显式激活入口。加载时会披露 `compatibility`；`allowed-tools` 仅作提示，不授予权限。

技能正文是工具结果中的事实。压缩或换窗后，若已激活的说明不再可见，应用会从活动分支恢复到请求上下文。模型也可以重新加载技能，获取完整说明。

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
