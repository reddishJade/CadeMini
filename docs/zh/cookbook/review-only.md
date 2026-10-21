# 只读评审

任务只是检查、不修改时，使用 `plan` 和 `--read-only`。

## 步骤

```bash
cade --mode plan --read-only "Review the request builder for evidence loss. Cite files and lines; do not modify anything."
```

计划模式隐藏写入和编辑工具，并拒绝修改类 shell 命令。`--read-only` 还会拒绝 memory、MCP 和任何剩余修改。

## 验证

- `git status` 干净。
- 回答用相对路径和行号引用文件。
- 关于测试的结论写明命令和结果。
- 发现的问题区分 bug 和偏好。

## 保留

评审报告留在对话或评审文档里。评审跨多个会话时，才创建工作笔记。

## 限制

模型仍然可以读取工作区内的任何内容。评审不可信代码仍需人工判断；策略阻止的是写入，不是误导性叙述。
