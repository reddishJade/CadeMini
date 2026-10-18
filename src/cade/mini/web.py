"""本地 web host：浏览器提交任务，服务器同步返回最终答案。"""

from __future__ import annotations

import html
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .log import SessionLog, list_sessions
from .loop import RunResult

_INDEX = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cade Mini</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 52rem; }
textarea { width: 100%; box-sizing: border-box; font: inherit; padding: .6rem; }
button { margin-top: .6rem; padding: .5rem 1rem; font: inherit; }
pre { white-space: pre-wrap; background: #f4f4f4; padding: 1rem; min-height: 4rem; }
.muted { color: #666; }
</style>
</head>
<body>
<h1>Cade Mini</h1>
<p class="muted">session <code>__SESSION__</code></p>
<textarea id="task" rows="5" placeholder="Describe a task..."></textarea>
<button id="run">Run</button>
<pre id="answer"></pre>
<script>
async function run() {
  const answer = document.getElementById('answer');
  answer.textContent = 'Running...';
  const response = await fetch('/api/task', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({task: document.getElementById('task').value})
  });
  const data = await response.json();
  answer.textContent = data.answer || data.error || JSON.stringify(data);
}
document.getElementById('run').onclick = run;
</script>
</body>
</html>
"""


class WebHost:
    """一个绑定单会话的本地 HTTP host。"""

    def __init__(
        self,
        *,
        runner: Callable[[str], RunResult],
        log: SessionLog,
        sessions_dir: Path,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self.runner = runner
        self.log = log
        self.sessions_dir = sessions_dir
        self.host = host
        self.port = port
        self._lock = threading.Lock()

    def run_task(self, task: str) -> RunResult:
        with self._lock:
            return self.runner(task)

    def make_server(self) -> ThreadingHTTPServer:
        handler = _make_handler(self)
        server = ThreadingHTTPServer((self.host, self.port), handler)
        server.daemon_threads = True
        return server

    def serve_forever(self) -> None:
        server = self.make_server()
        host, port = server.server_address[:2]
        print(f"Cade Mini web: http://{host}:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


def _make_handler(host: WebHost) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                body = _INDEX.replace("__SESSION__", html.escape(host.log.session_id))
                self._send(200, "text/html; charset=utf-8", body.encode("utf-8"))
                return
            if self.path == "/api/sessions":
                infos = [
                    {
                        "id": info.id,
                        "title": info.title,
                        "updated_at": info.updated_at,
                    }
                    for info in list_sessions(host.sessions_dir)
                ]
                self._json(200, {"sessions": infos})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/api/task":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                self._json(400, {"error": "invalid content-length"})
                return
            if length <= 0 or length > 1_000_000:
                self._json(400, {"error": "invalid request size"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            task = payload.get("task") if isinstance(payload, dict) else None
            if not isinstance(task, str) or not task.strip():
                self._json(400, {"error": "task is required"})
                return
            try:
                result = host.run_task(task.strip())
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)})
                return
            self._json(
                200,
                {
                    "answer": result.answer,
                    "termination": result.termination,
                    "steps": result.steps,
                    "error": result.error,
                    "session_id": host.log.session_id,
                },
            )

        def log_message(self, format: str, *args: Any) -> None:
            del format, args

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, "application/json; charset=utf-8", body)

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
