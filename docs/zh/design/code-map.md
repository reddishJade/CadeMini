# 代码地图

Cade 是独立项目。`cade` 发行包安装 `cade` 包和 `cade` 命令，不安装也不依赖早期的 Cade 项目。

## 包结构

```text
src/cade/
  ai/             provider 适配器、事件、认证协议、模型
  execution_env/  本地子进程执行和输出捕获
  core/           日志、投影、请求、循环、压缩、窗口
  tools/          工作区操作和历史检索
  safety/         策略、shell 分析、授权、审批、复核
  extensions/     技能、记忆、钩子、mcp、子代理、快照、目标
  app/            配置、provider 组装、凭据、请求上下文
  hosts/          cli、auth
  docs.py
  __main__.py
tests/            回归和集成测试
```

provider 和执行模块源自早期的 Cade 项目，现在由本项目拥有和维护。Cade 只包含自己使用的进程执行代码，不包含早期项目的文件系统或 Linux 沙箱实现。自己的进程包装器在 Linux 上使用已安装的 bubblewrap，在 macOS 上使用 Seatbelt；Windows 直接运行。

## 入口

```bash
uv run --locked cade
uv run --locked cade exec "Fix the failing test."
uv run --locked python -m cade
uv run --locked cade login
```

控制台脚本和 Python 模块都调用 `cade.hosts.cli:main`。登录由 `cade.hosts.auth` 处理。凭据和用户级技能放在 `~/.cade/`。项目会话和其他状态放在 `.cade/`。早期 Cade 项目的状态不兼容。

## 导入方向

- `ai` 和 `execution_env` 不导入应用或宿主模块。
- `core` 导入核心类型和 AI 协议。
- `tools`、`safety` 和 `extensions` 把操作和策略接到核心。
- `app` 负责启动设置、凭据持久化和请求上下文。
- `hosts` 组装这些组件，并负责用户交互。

包根目录没有公开再导出；直接导入所属子模块。

运行时契约见[架构](../architecture.md)、[Cade 如何工作](how-it-works.md)和[能力接缝](capability-seams.md)。
