# 运行长会话

长任务需要持久状态和恢复上下文的方法。使用 `NOTE.md`、目标和 history 工具。

## 步骤

1. 设置目标：

   ```bash
   cade --goal "Migrate the request builder to the new budget model" "Start by mapping the current admission rules."
   ```

2. 维护工作笔记。每个里程碑后让模型更新 `NOTE.md`：

   ```text
   Update NOTE.md with completed work, current blockers, and the next action.
   ```

3. 继续同一个会话：

   ```bash
   cade --continue "Resume from NOTE.md and run the next step."
   ```

4. 会话变大时压缩：

   ```text
   /compact
   ```

5. 找回旧细节：

   ```text
   Use history with query "budget" to find the earlier discussion.
   ```

## 验证

- `NOTE.md` 写明下一步动作，而不只是历史。
- 恢复后的运行不需要原来的终端也能继续。
- 只有验收检查通过后，才把目标标记为完成。

## 限制

`NOTE.md` 属于请求上下文，压缩时会被总结。`MEMORY.md` 用于长期事实，不是任务日志。精确证据以会话日志为准。
