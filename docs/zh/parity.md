# 对齐验收

本项目从 Cade Mini 更名为 Cade 之前，曾在两个真实仓库工作流和一次会话恢复上，对照早期 Cade 项目完成验收。本页保留当时的称呼：“Mini” 指当时的本项目，“Cade” 指早期项目。完整报告见[历史对齐验收](../history/parity-acceptance.md)。

## 工作流

两个干净的 Git 仓库从同一基线开始，使用同一份 `AGENTS.md` 指令：“Run pytest after changes. Keep changes minimal.”

Bug 修复：

- 任务：修复 `calc.py` 里的 `add`，让 pytest 通过。
- 两个代理都把 `a - b` 改成 `a + b`，运行 pytest，得到一个通过的测试，回答 `FIXED`。

功能：

- 任务：添加 `mul(a, b)` 和对应测试。
- 两个代理产生相同 diff，运行 pytest，得到两个通过的测试，回答 `PASS`。

恢复：

- 两个代理都恢复了之前的会话，回忆起 `calc.py`，并指出修正后的 `+` 运算符。

## 证据

- Mini 使用 `--mode build --yes`；Cade 使用 `--mode build --approval auto-review`。
- 当时两者都使用 DeepSeek `deepseek-chat`。Cade 现在只支持 Responses 协议。
- Mini 每个工作流用五步；Cade 用四步。Mini 多做了一次 `glob` 检查，没有改变结果。
- Cade 输出 JSONL 遥测和预算/审批事件；Mini 输出人类可读的工具进度，并在会话日志中追加 request/run 事实。

## 差异

- Mini 的快照覆盖 write/edit 和常见的单命令 shell 修改；复杂流水线不覆盖。
- Mini 的 MCP 客户端按服务器保持长连接，但能力面比 Cade 窄。
- 当时没有实现操作系统沙箱；Mini 依赖路径边界、模式、shell 分类和审批。
- 早期的 Codex 在线验收通过：write 创建了 `codex.txt`，恢复后的 Codex 会话读回了它。

对齐报告记录具体夹具和观察结果。重跑工作流时，创建干净的夹具，用相同的 provider 和设置运行两个代理，比较 diff 和测试结果，不要比较文字回答。Cade 只支持 Responses 协议。
