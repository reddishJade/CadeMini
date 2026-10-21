# 代码地图

Cade 是独立项目。`cade` 发行包安装 `cade` 包和 `cade` 命令，不安装也不依赖早期的 Cade 项目。

## 包结构

```text
src/cade/
  ai/                       provider、模型协议和认证
  harness/
    session/                事实、格式、日志、投影和历史
    execution/              执行范围契约和本地执行后端
    loop.py                 日志驱动的回合与 provider 重试
    executor.py             共享工具授权与执行
    tools.py, provider.py   运行时契约
    request.py              请求准入与线上格式转换
    compaction.py           基于摘要的上下文缩减
    window.py               显式上下文窗口重置
  coding/
    tools/                  读写、编辑、搜索、shell、历史和提问工具
    context/                提示词、指令、笔记和记忆
    security/               工作区策略、规则、授权、审批和复核
    workspace/              快照和协调恢复
    subagents.py            编程子会话和委派工具
    mcp/, mcp_config.py      MCP 连接、工具、结果和配置
    skills/                 技能发现、加载、资源和上下文恢复
    goal.py                 工作区目标状态和工具
  app/                      装配、活动会话、配置、认证和命令
  hosts/                    CLI 和 TUI 展示
  docs.py
  __main__.py
tests/                      回归和集成测试
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

- `ai` 不导入其他 Cade 层。
- `harness` 只导入自身和 `ai`，本地执行是它的内部实现。
- `coding` 导入自身、`harness` 和 `ai`。
- `app` 装配这些下层模块，管理产品级会话操作。
- `hosts` 调用应用操作，把下层契约适配为输入输出。

`app.bootstrap.create_session()` 是共享装配入口。它接收配置和调用方管理的资源，不导入终端模块。`ApplicationSession.run_task()` 同时用于一次性执行和 TUI。`harness.executor.execute_tool()` 是统一的工具执行边界。

配置类型由对应能力拥有：权限规则放在 `coding/security/rules.py`，MCP 连接设置放在 `coding/mcp_config.py`。`CadeConfig` 组合这些类型，能力模块不反向导入应用配置。

`tests/test_architecture.py` 检查包依赖和执行契约隔离。Cade 根目录不再保留旧的 `core`、`execution_env`、`extensions`、`safety` 或 `tools` 包。

包根目录没有公开再导出；直接导入所属子模块。

运行时契约见[架构](../architecture.md)、[Cade 如何工作](how-it-works.md)和[能力接缝](capability-seams.md)。
