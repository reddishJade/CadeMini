"""查找用户已安装的搜索程序。"""

import shutil


def find_tool(tool: str) -> str:
    executable = shutil.which(tool) or (
        shutil.which("fdfind") if tool == "fd" else None
    )
    if executable is None:
        raise ValueError(f"{tool} is not installed. Install it and add it to PATH.")
    return executable
