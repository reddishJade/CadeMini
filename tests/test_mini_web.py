"""Cade Mini web host 的回归测试。"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from cade_mini.core.log import SessionLog
from cade_mini.core.loop import RunResult
from cade_mini.hosts.web import WebHost


def _run(host: WebHost) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = host.make_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_web_index_task_and_sessions(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    log = SessionLog.create(sessions, session_id="web")
    host = WebHost(
        runner=lambda task: RunResult(
            answer=f"echo:{task}",
            steps=1,
            termination="completed",
        ),
        log=log,
        sessions_dir=sessions,
        port=0,
    )
    server, thread = _run(host)
    port = int(server.server_address[1])
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
            page = response.read().decode("utf-8")
        assert "Cade Mini" in page
        assert "web" in page

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/task",
            data=json.dumps({"task": "hi"}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["answer"] == "echo:hi"
        assert payload["termination"] == "completed"
        assert payload["session_id"] == "web"

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/sessions"
        ) as response:
            sessions_payload = json.loads(response.read().decode("utf-8"))
        assert sessions_payload["sessions"][0]["id"] == "web"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
