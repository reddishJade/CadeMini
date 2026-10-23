"""把 Cade TUI 的命令契约桥接到 Cade 的 ApplicationSession。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from cade.app.commands import COMMAND_INDEX, SPECS

from .compat import CommandContext, SessionEntry
from .context_usage import render_context_usage


@dataclass(frozen=True)
class CommandEntry:
    """补全与帮助需要的命令元数据。"""

    desc: str
    group: str
    args_desc: str = ""
    canonical: str | None = None
    visible: bool = True
    accepts_args: bool = True


COMMAND_REGISTRY: dict[str, CommandEntry] = {
    spec.name: CommandEntry(
        desc=spec.description,
        group=spec.group,
        args_desc=spec.args,
        canonical=spec.canonical,
        visible=spec.canonical is None,
        accepts_args=bool(spec.args),
    )
    for spec in SPECS
}
COMMAND_NAMES = tuple(spec.name for spec in SPECS)


def fork_input_text(entry: SessionEntry) -> str:
    """从用户记录恢复可编辑输入。"""
    return str(entry.content or "")


def handle_command(command: str, ctx: CommandContext) -> bool:
    """执行一条斜杠命令；返回是否退出 TUI。"""
    service = ctx.app.service
    ctx.changed_session = False
    try:
        result = asyncio.run(service.command(command))
    except (OSError, RuntimeError, ValueError) as exc:
        ctx.output.write(f"[error] {exc}")
        return False

    ctx.changed_session = result.changed_session
    for line in result.lines:
        ctx.output.write(line)
    if result.context_usage is not None:
        ctx.output.write(render_context_usage(result.context_usage))
    if result.draft is not None:
        ctx.state.draft_input = result.draft
    if result.task is not None:
        ctx.state.draft_input = result.task
    if result.side_question is not None:
        try:
            answer = asyncio.run(
                service.side_question(result.side_question, lambda _: None)
            )
        except (OSError, RuntimeError, ValueError) as exc:
            ctx.output.write(f"[error] {exc}")
        else:
            ctx.output.write(answer)
    if result.login_provider is not None:
        ctx.output.write(f"Login required: {result.login_provider}")
    if result.config_field is not None:
        ctx.output.write(f"Config setting: {result.config_field}")
    if result.picker is not None:
        choices = service.choices(result.picker)
        if choices:
            for name, label in choices:
                ctx.output.write(f"  {name:<24} {label}")
    ctx.state.mode = service.policy.mode
    ctx.state.thinking = service.provider_config.thinking
    from .compat import BusyMessageMode

    ctx.state.busy_mode = BusyMessageMode(service.busy_mode)
    return result.quit


__all__ = [
    "COMMAND_INDEX",
    "COMMAND_NAMES",
    "COMMAND_REGISTRY",
    "CommandEntry",
    "fork_input_text",
    "handle_command",
]
