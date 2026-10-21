# 测试

测试按所保护的层次分组。修改后先跑覆盖该改动的最小分组，发布前再跑完整套件。

## 命令

```bash
mkdir -p e2e-results
uv run --locked pytest -q tests --basetemp=e2e-results/pytest
uv run --locked pytest -q tests/test_loop.py --basetemp=e2e-results/pytest
uv run --locked ruff check src/cade/ tests/
uv run --locked ruff format --check src/cade/ tests/
uv run --locked pyright src/cade/
uv run --locked python -m cade.docs check
```

## 覆盖地图

| 领域 | 测试 |
| --- | --- |
| 会话 | `test_log.py` |
| 投影与配对 | `test_projection.py` |
| 预算、裁剪、线上格式 | `test_request.py` |
| 循环、审批、重试、窗口 | `test_loop.py` |
| Provider 与认证 | `test_provider.py`、`test_auth.py`、`test_siwc_auth.py`、`test_responses_siwc_contract.py` |
| 工作区工具 | `test_tools.py` |
| 压缩与窗口 | `test_compaction.py`、`test_window.py` |
| CLI 与配置 | `test_cli.py`、`test_config.py` |
| 指令、技能、记忆、NOTE | `test_instructions.py`、`test_skills.py`、`test_memory.py`、`test_note.py` |
| 安全与模式 | `test_policy.py`、`test_shell.py`、`test_grants.py` |
| 扩展 | `test_hooks.py`、`test_mcp.py`、`test_subagents.py` |
| 恢复 | `test_snapshots.py`、`test_goal.py` |
| 文档 | `test_docs.py` |

## 真实 provider 检查

验收运行使用 OpenAI Responses，可以走 API key 或 ChatGPT 套餐，不属于默认测试套件。它们覆盖工具使用、恢复、压缩、技能、build 模式和读写。新增验收时，在对应改动里记录命令和观察到的结果。

## 文档检查

`test_docs.py` 和 `uv run --locked python -m cade.docs check` 校验清单、链接、锚点和生成的目录。CI 会运行文档任务。

## 独立发行

用 `uv build --no-sources` 构建 wheel 和源码归档。在干净的虚拟环境里安装 wheel，并在源码树之外检查 `cade --help` 和 `python -m cade --help`。安装后的环境只能包含本发行包的模块和命令。见[验证指南](../testing.md)。
