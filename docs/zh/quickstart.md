# 快速上手

Cade 需要 Python 3.12 或更新版本。仓库用 `uv` 做开发，并提供 `cade` 入口。

## 安装并登录

在仓库根目录执行：

```bash
uv sync --locked
```

Cade 自己管理凭据，位置是 `~/.cade/auth.json`。用浏览器里的 Sign in with ChatGPT（SIWC）登录：

```bash
uv run --locked cade login
```

浏览器流程会在首次使用时注册 Cade，后续登录复用这份注册信息。稳定的宿主 ID 保存在 `~/.cade/host.json`。SIWC 没有设备码流程。在无图形界面的主机上，先在本地完成登录，复制 `auth.json`，让远端主机在下次登录时创建自己的宿主 ID。也可以改用 API key。

使用 OpenAI API key 时，用隐藏输入提示，或设置环境变量：

```bash
uv run --locked cade login --provider openai
export OPENAI_API_KEY=...
uv run --locked cade --provider openai --model gpt-5.5 -p "Review this project."
```

DeepSeek 使用同样的 Responses 协议，但用自己的 API key：

```bash
uv run --locked cade login --provider deepseek
export DEEPSEEK_API_KEY=...
uv run --locked cade --provider deepseek --model deepseek-flash -p "Review this project."
```

默认 provider 是 `chatgpt`。在 `cade.json` 里设置 `provider` 和 `model`，可以改启动默认值。Cade 只支持 Responses 协议：`chatgpt` 用于 ChatGPT 套餐，`openai` 用于 OpenAI API key，`deepseek` 用于 DeepSeek API key。旧 Cade 项目的状态不兼容；请使用干净的 `~/.cade/` 和工作区 `.cade/` 目录。

## 运行一个任务

```bash
uv run --locked cade -p "Read README.md and explain the entry point in three bullets."
```

也可以显式写出命令：

```bash
uv run --locked cade exec "Read README.md and explain the entry point."
```

Cade 默认在当前目录运行。用 `--project-root` 指向其他工作区。

## 选择模式

- `build` 是默认模式。普通编辑、测试、构建和 shell 流水线自动运行；检测到风险时询问。
- `ask` 允许已归类的只读操作，在修改或未归类的 shell 命令之前询问。
- `plan` 隐藏写入和编辑工具，拒绝修改。只读 shell 命令允许执行。

```bash
uv run --locked cade --mode plan -p "Propose a fix for the failing test."
uv run --locked cade --mode build -p "Fix the bug and run the test."
```

非交互自动化可用 `--yes` 批准确认，用 `--read-only` 拒绝写入和 shell 命令。在共享仓库里优先使用规则授权，而不是 `--yes`；见[配置](configuration.md)。

## 继续之前的工作

会话默认保存在 `.cade/sessions`。

```bash
uv run --locked cade --list-sessions
uv run --locked cade --continue -p "What did you change?"
uv run --locked cade --resume SESSION_ID -p "Continue the task."
```

运行 `cade` 启动 TUI。`cade "Review this project."` 会启动 TUI 并发送首条消息，任务结束后保持打开。交互命令见 [CLI](cli.md)，按键绑定见[宿主](hosts.md)。

## 构建文档

```bash
uv sync --locked --extra docs
uv run --locked python -m cade.docs check
uv run --locked python -m cade.docs build
```

打开 `docs/index.html`。文档源文件在 `docs/` 下。中文文档在 `docs/zh/` 下，构建命令是 `uv run --locked python -m cade.docs --root docs/zh build --out docs/zh/index.html`。
