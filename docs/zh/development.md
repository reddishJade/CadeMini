# 开发

Cade 独立维护，不依赖早期 Cade。所有工作都在本仓库根目录进行。

## 环境准备

需要 Python 3.12+ 和 uv。Linux 上的工具执行还需要可用的 bubblewrap（Debian/Ubuntu 上是 `sudo apt-get install bubblewrap`），以及允许非特权用户命名空间的主机。UID 映射被拒绝属于沙箱启动错误，不是审批失败。CI 使用 Ubuntu 22.04，不关闭主机安全策略。macOS 使用系统 `sandbox-exec`。原生沙箱测试在 Linux 和 macOS 上运行；Windows 跳过这些测试，直接执行工具。

通用 shell 回归测试在 Windows 上使用 PowerShell，在 Linux 和 macOS 上使用 Bash。
Windows 开发需要在 PATH 中提供 `pwsh` 或 `powershell`；使用 `bash` 工具时才需要 Git Bash。
CI 在 Linux 和 Windows 上运行回归测试。

```bash
uv sync --locked --extra dev --extra docs
mkdir -p tmp test-results
```

代码在 `src/cade/`，测试在 `tests/`，文档在 `docs/`。模块边界见[架构](architecture.md)。

## 检查

```bash
uv run --locked ruff check src/cade/ tests/
uv run --locked ruff format --check src/cade/ tests/
uv run --locked pyright src/cade/
uv run --locked pytest -q tests --basetemp=tmp/pytest
uv run --locked cade --help
uv run --locked python -m cade --help
uv run --locked cade login --help
uv run --locked python -m cade.docs check
uv build --no-sources
```

按[验证指南](testing.md)选择聚焦的检查。普通临时工作区放在 `tmp/`，回归报告放在 `test-results/`，端到端测试结果放在 `e2e-results/`。缓存使用各工具的专用目录。不要提交凭据或原始提供方跟踪记录。

## 文档

文档源在 `docs/`。清单是 `docs/docs.json`，定义导航顺序和发布页面。[docs/README.md](README.md) 是手册入口。[docs/AGENTS.md](../AGENTS.md) 定义文档规则。中文文档镜像在 `docs/zh/`，使用独立的 `docs/zh/docs.json`。

```bash
uv run --locked python -m cade.docs catalogs --write
uv run --locked python -m cade.docs llms --write
uv run --locked python -m cade.docs build
uv run --locked python -m cade.docs check
```

`check` 校验清单、页面文件、相对链接、锚点和生成的目录。它只依赖标准库，在 CI 中运行。`build` 渲染 `docs/index.html`，需要 `docs` 附加依赖。

`llms --write` 更新 `docs/llms.txt` 和 `docs/llms-full.txt`。`catalogs --write` 更新 `docs/design/` 下的配置、事件和模块参考。不要手工编辑生成的文件。

添加页面时：

1. 在 `docs/` 下添加 Markdown 文件。
2. 把它加入 `docs/docs.json` 的某个章节。
3. 使用指向其他页面的相对链接。
4. 重新生成目录和站点，然后运行文档检查。

除非匹配 `exclude`，每个 Markdown 文件都必须登记在清单里。排除的是代理指令、决策记录和事故模板。会话格式参考放在 `docs/reference/`，历史研究放在 `docs/history/`。

中文镜像的构建和目录生成：

```bash
uv run --locked python -m cade.docs --root docs/zh catalogs --write
uv run --locked python -m cade.docs --root docs/zh llms --write
uv run --locked python -m cade.docs --root docs/zh build --out docs/zh/index.html
```

`check` 会自动检查根目录下带独立清单的子目录，因此一次 `uv run --locked python -m cade.docs check` 会同时校验中英文文档。

## 边界

Cade 有 `ai`、`harness`、`coding`、`app` 和 `hosts` 五个运行时包。本地执行位于 `harness/execution/local`。下层不导入上层，`tests/test_architecture.py` 检查这些边界。不要添加对早期 Cade 项目的依赖。

公开接口包括会话格式、配置字段、CLI 参数、工具名和文档清单。修改它们时，测试和文档要在同一个提交里更新。

## 提交与发布

提交前检查 diff 并运行检查。没有维护者批准，不要推送或发布。推送前确认 Git 远端指向本仓库，而不是早期 Cade 项目。

CI 工作流检查代码、测试、文档、启动和发行构建。版本标签（`vX.Y.Z`）必须与 `pyproject.toml` 一致，并且属于 `main`。发布工作流在 GitHub `release` 环境批准后发布已检查的发行产物。已发布的标签永远不要移动。

## uv 缓存权限

如果 uv 无法访问全局缓存中的 `.lock` 文件，在仓库根目录的 PowerShell 中设置：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
```

设置仅影响当前终端。单次命令也可以使用 `uv --cache-dir ./.uv-cache run --locked ...`，无需修改全局目录权限。

## 依赖管理

使用 `uv add PACKAGE` 添加运行时依赖，使用 `uv add --dev PACKAGE` 添加开发工具，使用 `uv remove PACKAGE` 删除依赖。让 uv 生成版本要求并更新 `uv.lock`，不要凭感觉编写版本范围或手动修改依赖版本。只有明确的兼容性要求才能作为额外版本约束的依据，并须记录原因。

## 搜索程序

文件查找和内容搜索通过 shell 命令执行。可安装 fd 和 ripgrep，但启动 Cade 不依赖这两个程序。Debian/Ubuntu 使用 `sudo apt-get install ripgrep fd-find`，macOS 使用 `brew install ripgrep fd`，Windows（Scoop）使用 `scoop install ripgrep fd`。Debian/Ubuntu 上的 fd 命令名为 `fdfind`。
