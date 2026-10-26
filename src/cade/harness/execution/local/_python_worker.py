"""在独立 Python 进程中运行程序，通过标准输入输出调用宿主工具。"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
import traceback
from typing import Any


async def main() -> None:
    """读取执行请求，注入工具代理，并发送 JSON 结果。"""
    incoming = sys.stdin
    outgoing = sys.stdout
    # 程序输出与工具协议分开；直接向 fd 1 写入会使宿主拒绝损坏的协议。
    sys.stdout = sys.stderr
    boot = json.loads(incoming.readline())

    def send(value: dict[str, Any]) -> None:
        if value.get("type") == "done":
            sys.stdout.flush()
            sys.stderr.flush()
        outgoing.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
        outgoing.flush()

    class Tools:
        def __getitem__(self, name: str) -> Any:
            if name not in boot["tools"]:
                raise KeyError(f"Unknown tool: {name}")

            async def invoke(args: dict[str, Any]) -> Any:
                send({"type": "call", "name": name, "args": args})
                reply = json.loads(incoming.readline())
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply["value"]

            return invoke

        def __getattr__(self, name: str) -> Any:
            return self[name]

    try:
        module = ast.parse(boot["code"], filename="<run_code>")
        function = ast.AsyncFunctionDef(
            name="__program__",
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=module.body or [ast.Pass()],
            decorator_list=[],
            returns=None,
            type_comment=None,
            type_params=[],
        )
        wrapped = ast.fix_missing_locations(
            ast.Module(body=[function], type_ignores=[])
        )
        namespace: dict[str, Any] = {"tools": Tools(), "asyncio": asyncio}
        exec(compile(wrapped, "<run_code>", "exec", dont_inherit=True), namespace)  # noqa: S102
        value = await namespace["__program__"]()
        send({"type": "done", "value": value})
    except BaseException:  # noqa: BLE001 -- 模型代码的 SystemExit 也必须报告给宿主。
        send({"type": "done", "error": traceback.format_exc(limit=10)[-16000:]})
    # 宿主收到结果后终止进程组，避免程序启动的子进程继续运行。
    incoming.read()


if __name__ == "__main__":
    asyncio.run(main())
