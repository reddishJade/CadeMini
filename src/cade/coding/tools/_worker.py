"""在操作系统沙箱内执行固定的文件操作，不接受代码或 shell。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from cade.coding.tools.registry import build_tools
from cade.harness.execution.scope import ExecutionScope, execution_scope
from cade.harness.tools import ToolResult


def main() -> int:
    try:
        payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        tools = {
            tool.name: tool
            for tool in build_tools(Path(payload["workspace"]))
            if tool.name not in {"bash", "powershell"}
        }
        scope = ExecutionScope(
            read_only=payload["read_only"],
            writable_paths=tuple(Path(path) for path in payload["writable_paths"]),
        )
        with execution_scope(scope):
            tool = tools[payload["tool"]]
            tool.native_binary = payload.get("native_binary")
            result = tool.run(payload["args"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = ToolResult(f"File operation failed: {exc}", is_error=True)
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
