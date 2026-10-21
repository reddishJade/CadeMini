# 修一个 bug

修改时使用 `build` 模式。补丁尽量小，并用仓库的测试验证。

## 步骤

1. 复现失败：

   ```bash
   cade --mode plan "Read the failing test and explain the failure."
   ```

2. 修复：

   ```bash
   cade --mode build --yes "Fix the failing test with the smallest change, then run pytest."
   ```

3. 检查 diff 和快照：

   ```bash
   git diff
   cade --undo  # 只在改动错误时使用
   ```

## 验证

- 目标测试通过。
- 相关的完整测试文件通过。
- diff 只包含预期改动。
- 助手说明了它运行过的命令。

## 保留

运行日志记录请求、工具调用和测试输出。快照允许在下一次运行前恢复。需要评审的改动留在提交里，不要写进文档文件。

## 避免

- 打算编辑时使用 `--read-only`。
- 在共享仓库里没有规则就使用 `--yes`。
- 一行条件就能修好测试，却做了大范围重构。
