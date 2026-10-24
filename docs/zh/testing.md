# 验证指南

按正在修改的行为和风险选择检查。普通测试工作区和临时文件放在 `tmp/`，回归报告放在 `test-results/`，端到端测试结果放在 `e2e-results/`。缓存使用各工具的专用目录，如 `.uv-cache/` 和 `.pytest_cache/`。不要把账号凭据复制到测试证据里。

## 必需检查

代码修改后运行：

```bash
mkdir -p tmp test-results
uv run --locked ruff check src/cade/ tests/
uv run --locked ruff format --check src/cade/ tests/
uv run --locked pyright src/cade/
uv run --locked pytest -q tests --basetemp=tmp/pytest
```

导入、包或入口变化后，还要检查：

```bash
uv run --locked cade --help
uv run --locked python -m cade --help
uv run --locked cade login --help
uv build --no-sources
```

发行相关改动要检查 wheel，并在干净的虚拟环境里安装。在源码树之外检查启动，确认不再需要任何 `cade` 包、命令或依赖。

## 执行环境

通用 shell 契约在 Windows 上使用 PowerShell，在 Linux 和 macOS 上使用 Bash。
使用 `tests/conftest.py` 中的 `shell_tool_class` 和 `python_command`，选择工具并转义 Python 参数。
各平台保留相同的行为断言。
专门验证 Bash 语法或可执行文件查找的测试仍使用 Bash。

Shell 取消测试必须能终止自己启动的进程。Windows 上的 `taskkill /T /F` 需要终止测试进程树的权限。如果返回 `Access denied`，请在获准且具备该权限的执行环境中运行检查。

如果 uv 无法访问全局缓存，使用[工作区缓存](development.md#uv-缓存权限)。如果 pytest 无法写入 `.pytest_cache`，可用 `-p no:cacheprovider` 禁用可选缓存。两种情况下，`--basetemp` 都放在 `tmp/` 下。

## 选择行为检查

- 本地计算或策略：使用聚焦的回归测试，期望值和边界情况要独立核对。
- 已连接的组件：把真实组件放在一起测试，只在必要时控制外部边界。
- Provider 解析：使用捕获的、有来源的协议载荷走适配器。替换整个 provider 测的是应用，不是远程协议。
- 登录：测试宿主路由、凭据持久化和失败行为，不联系真实账号。真实 OAuth 验收需要明确批准。
- 真实宿主或服务：只有底层检查仍留下具体工作流风险时才跑 E2E。运行前记录任务、前置条件、命令、预期可观察结果和独立验证方式。
- 文档：检查事实、链接、命令、生成的目录和 diff。

只为具体覆盖缺口添加可复用测试。测试应保护用户结果或显式契约，不保护私有实现细节。不要为了让测试通过而修改生产 API。

## 报告结果

说明每项检查能证明什么。把代码导致的失败与缺少权限、环境前置条件或外部服务区分开。明确报告没有运行的检查和剩余风险。provider 的完成文本本身不能证明工作流成功。
