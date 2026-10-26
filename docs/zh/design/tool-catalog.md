# 工具目录

内置工具面。MCP 工具在启动时发现，命名为 `mcp__<server>__<tool>`；参数由服务器提供。

| 工具 | 参数 | 副作用 | 默认策略 |
| --- | --- | --- | --- |
| `read` | `path`、`offset`、`limit` | 无 | 允许，受保护路径除外 |
| `write` | `path`、`content` | 创建或替换文件；先做快照 | ask 模式下询问，build 模式下允许 |
| `edit` | `path`、`edits: [{oldText, newText}]` | 对原始文件执行互不重叠的替换；先做快照 | ask 模式下询问，build 模式下允许 |
| `bash`、`powershell` | `command`、`timeout`、`workdir`、`additional_write_paths` | 任意进程；对常见修改做快照 | build 模式下允许普通开发；检测到风险时询问 |
| `run_code` | `code`、`description`、`timeout`、`max_output_tokens` | 运行 Python 并调用会话工具；直接 I/O 遵守操作系统沙箱 | build 模式允许，ask 模式询问；计划和只读模式拒绝 |
| `question` | `questions: [{question, header, options, multiple?}]` | 收集用户答案；关闭问题时结束当前轮次 | 允许，包括计划模式 |
| `websearch` | `query` | 通过 Exa 搜索网页 | 网络开启时允许 |
| `webfetch` | `url`、`format`、`timeout` | 获取文本、Markdown 或 HTML | 网络开启时允许 |
| `history` | `query`、`entry_id`、`call_id`、`limit` | 无 | 允许 |
| `skill` | `name` | 无；加载技能正文 | 允许 |
| `memory` | `text` | 追加到 `MEMORY.md` | 允许；只读模式拒绝 |
| `goal` | `action`、`objective` | 更新 `.cade/goal.json` | 允许 |
| `delegate` | `prompt` 或 `tasks`、`mode`、`description` | 创建一个或多个子会话 | 允许 |
| `mcp__*` | 由服务器定义 | 远程副作用 | 询问；计划模式和只读模式拒绝 |

## 如何阅读这张表

- “副作用”指工具可能改变什么，不是每次都会改变。
- 工作区的 `write` 和 `edit` 在无法取得快照时按失败关闭。外部写入需要权限，并且没有工作区快照。
- `bash` 的只读分类是保守的；未知命令在 ask 模式下询问，在计划模式下拒绝。build 模式允许普通开发命令。
- MCP 工具是动态的。规则可以放行特定工具；默认是询问。

## 参数

权威 JSON schema 内建于每个工具。这张表只用于导航；与工具 schema 不一致时，以 schema 为准。

工具默认值和 Cade 集成见[行为契约](../../decisions/0015-tool-behavior-parity.md)。
`read` 返回不带行号前缀的文件内容，并支持图片附件。`offset` 和 `limit` 必须为正整数。
编辑只支持精确匹配，每个替换都基于原始文件，命中位置必须唯一且互不重叠。列目录、查找文件和搜索内容请使用 shell 命令。
Shell 的 `timeout` 单位是秒，没有默认超时。Windows 上的 `bash` 使用 Bash；
`powershell` 使用 PowerShell，仅支持 Windows。

不再提供独立的 `ls`、`find` 或 `grep` 工具。搜索命令遵守 shell 的输出限制、权限和沙箱策略。见[决策 0017](../../decisions/0017-shell-search.md)。

## 相关页面

- [工具与安全](../tools-security.md)
- [工具执行](tool-execution.md)
- [配置目录](config-catalog.md)
