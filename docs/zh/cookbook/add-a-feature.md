# 添加功能

先写测试，再实现，最后验证。使用 `build` 模式，让 diff 便于评审。

## 步骤

1. 让模型写一个会失败的测试，或自己写：

   ```bash
   cade --mode build --yes "Add a test for mul() that asserts 2 * 3 == 6; run it and confirm it fails."
   ```

2. 做最小改动：

   ```bash
   cade --mode build --yes "Implement mul() and run the new test."
   ```

3. 检查：

   ```bash
   git diff
   uv run --locked pytest -q
   ```

## 验证

- 新测试在修改前失败，修改后通过。
- 已有测试仍然通过。
- 实现里没有无关修改。
- 公开行为写进了对应的指南。

## 撤销

如果改动错误，在开始下一次尝试之前运行 `cade --undo`。它一次恢复一份记录的文件改动，不回退对话。

## 保留

功能改变公开契约时，更新指南或决策记录。测试放在 `tests/`，文档放在 `docs/`。
