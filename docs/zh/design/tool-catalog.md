# 工具目录

内置工具面。MCP 工具在启动时发现，命名为 `mcp__<server>__<tool>`；参数由服务器提供。

| 工具 | 参数 | 副作用 | 默认策略 |
| --- | --- | --- | --- |
| `read` | `path`、`offset`、`limit` | 无 | 允许，受保护路径除外 |
| `write` | `path`、`content` | 创建或替换文件；先做快照 | ask 模式下询问，build 模式下允许 |
| `edit` | `path`、`old`、`new` | 替换一处内容；先做快照 | ask 模式下询问，build 模式下允许 |
| `glob` | `pattern`、`path` | 无 | 允许 |
| `grep` | `pattern`、`path`、`include` | 无 | 允许 |
| `bash` | `command`、`workdir`、`timeout_ms`、`additional_write_paths` | 任意进程；对常见修改做快照 | build 模式下允许普通开发；检测到风险时询问 |
| `history` | `query`、`entry_id`、`call_id`、`limit` | 无 | 允许 |
| `skill` | `name` | 无；加载技能正文 | 允许 |
| `memory` | `text` | 追加到 `MEMORY.md` | 允许；只读模式拒绝 |
| `goal` | `action`、`objective` | 更新 `.cade/goal.json` | 允许 |
| `subagent` | `prompt`、`description` | 创建子会话 | 允许；继承父级限制 |
| `delegate` | `prompt` 或 `tasks`、`mode`、`description` | 创建一个或多个子会话 | 允许 |
| `mcp__*` | 由服务器定义 | 远程副作用 | 询问；计划模式和只读模式拒绝 |

## 如何阅读这张表

- “副作用”指工具可能改变什么，不是每次都会改变。
- 工作区的 `write` 和 `edit` 在无法取得快照时按失败关闭。外部写入需要权限，并且没有工作区快照。
- `bash` 的只读分类是保守的；未知命令在 ask 模式下询问，在计划模式下拒绝。build 模式允许普通开发命令。
- MCP 工具是动态的。规则可以放行特定工具；默认是询问。

## 参数

权威 JSON schema 内建于每个工具。这张表只用于导航；与工具 schema 不一致时，以 schema 为准。

## 相关页面

- [工具与安全](../tools-security.md)
- [工具执行](tool-execution.md)
- [配置目录](config-catalog.md)
