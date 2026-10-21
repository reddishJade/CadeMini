"""终端工作台退出后显示耗时与可直接使用的恢复命令。"""

from __future__ import annotations

import shlex
from pathlib import Path

from rich.console import Console
from rich.text import Text

from .bridge import CadeSessionStore
from .chrome import CADE_LOGO


def print_exit_summary(
    store: CadeSessionStore,
    duration_seconds: float,
    project_root: Path,
    config_path: Path | None,
) -> None:
    """在终端恢复后打印摘要；空会话不提供无效的恢复入口。"""
    seconds = max(0, int(duration_seconds))
    minutes, seconds = divmod(seconds, 60)
    duration = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    info = [Text(f"Time {duration}", style="#8b949e")]
    saved = store.current_path.is_file()
    if saved:
        args = ["cade", "--resume", store.session_id]
        if project_root.resolve() != Path.cwd().resolve():
            args.extend(["--project-root", str(project_root.resolve())])
        if config_path is not None:
            args.extend(["--config", str(config_path.resolve())])
        info.append(Text(f"Session {store.session_id}", style="#8b949e"))
        resume = Text("To resume: ", style="#8b949e")
        resume.append(shlex.join(args), style="#58a6ff")
        info.append(resume)
    else:
        info.append(Text("No task saved.", style="#8b949e"))
    console = Console(highlight=False)
    try:
        console.print()
        for index, logo in enumerate(CADE_LOGO):
            line = Text(f"  {logo}", style="#8b949e")
            if index < len(info):
                line.append("  ")
                line.append_text(info[index])
            console.print(line, soft_wrap=True)
        console.print()
    except UnicodeEncodeError:
        # 旧版 Windows 控制台可能无法编码徽标；退化为纯文本摘要。
        print()
        for item in info:
            print(f"  {item.plain}")
        print()
