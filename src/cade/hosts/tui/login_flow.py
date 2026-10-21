"""TUI 内的账户登录与提供方连接流程。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import httpx

from cade.ai.auth import LoginCancelledError

if TYPE_CHECKING:
    from cade.app.session import ApplicationSession

OpenChoices = Callable[[Sequence[tuple[str, object]], Callable[[object], None]], None]


class OpenText(Protocol):
    def __call__(
        self, prompt: str, on_submit: Callable[[str], None], *, secret: bool = False
    ) -> None: ...


PostMessage = Callable[[str, str], None]
LoginTask = Callable[[threading.Event], None]
RunTask = Callable[[LoginTask], None]


@dataclass(frozen=True)
class LoginRequest:
    """登录参数；connect 在成功后打开该提供方的模型选择器。"""

    provider: str | None = None
    method: str | None = None
    connect: bool = False


def parse_login_command(text: str) -> LoginRequest | None:
    """解析登录命令；不把未指定提供方的 API-key 登录限定为 ChatGPT。"""
    parts = text.split()
    if not parts:
        return None
    command = parts[0]
    if command == "/auth":
        if len(parts) < 2 or parts[1] not in {"login", "connect"}:
            return None
        command, args = "/" + parts[1], parts[2:]
    elif command in {"/login", "/connect"}:
        args = parts[1:]
    else:
        return None
    provider = None
    method = None
    for arg in args:
        if arg in {"account", "browser"}:
            method = "browser"
        elif arg in {"api", "api_key"}:
            method = "api_key"
        else:
            provider = arg
    return LoginRequest(provider, method, command == "/connect")


class TuiLoginFlow:
    """使用原生选择器登录；浏览器授权只在后台线程执行。"""

    def __init__(
        self,
        *,
        open_choices: OpenChoices,
        open_text: OpenText,
        post: PostMessage,
        run_task: RunTask,
        service: ApplicationSession,
        on_connected: Callable[[str], None],
    ) -> None:
        self._open_choices = open_choices
        self._open_text = open_text
        self._post = post
        self._run_task = run_task
        self._service = service
        self._on_connected = on_connected

    def start(self, request: LoginRequest) -> None:
        provider, method = request.provider, request.method
        if provider is not None and provider not in self._service.auth.PROVIDERS:
            self._post("error", f"Unknown provider: {provider}")
            return
        if provider is None:
            if request.connect:
                providers = self._service.auth.PROVIDERS
            elif method is None:
                self._open_choices(
                    [
                        ("Sign in with an account", "browser"),
                        ("Use an API key", "api_key"),
                    ],
                    lambda choice: self.start(LoginRequest(method=str(choice))),
                )
                return
            else:
                providers = (
                    ("chatgpt",) if method == "browser" else ("openai", "deepseek")
                )
            self._open_choices(
                [(name, name) for name in providers],
                lambda choice: self.start(
                    LoginRequest(str(choice), method, request.connect)
                ),
            )
            return
        method = method or ("browser" if provider == "chatgpt" else "api_key")
        if method == "api_key" and provider in {"openai", "deepseek"}:
            self._open_text(
                f"{provider} API key (Esc to cancel)",
                lambda value: self._save_api_key(provider, value, request.connect),
                secret=True,
            )
        elif method == "browser" and provider == "chatgpt":
            self._start_account_login(request.connect)
        else:
            self._post("error", f"{provider} does not support {method} login.")

    def _success(self, provider: str, message: str, connect: bool) -> None:
        self._post("system", message)
        if self._service.provider_config.provider == provider:
            try:
                self._service.select_model(self._service.provider_config.model)
            except (OSError, RuntimeError, ValueError) as exc:
                self._post("error", f"Credentials saved, but model setup failed: {exc}")
        if connect:
            self._on_connected(provider)

    def _save_api_key(self, provider: str, secret: str, connect: bool) -> None:
        try:
            message = self._service.auth.save_api_key(provider, secret)
        except (OSError, ValueError) as exc:
            self._post("error", f"Failed to save credentials: {exc}")
            return
        self._success(provider, message, connect)

    def _start_account_login(self, connect: bool) -> None:
        def notify(message: str) -> None:
            self._post(
                "system",
                f"{message}\nWaiting for the browser callback (Esc to cancel)...",
            )

        def work(cancel: threading.Event) -> None:
            self._post("system", "Starting ChatGPT sign-in...")
            try:
                message = asyncio.run(self._service.auth.login_chatgpt(notify, cancel))
            except LoginCancelledError:
                self._post("system", "Login cancelled.")
                return
            except (
                OSError,
                RuntimeError,
                TimeoutError,
                ValueError,
                httpx.HTTPError,
            ) as exc:
                self._post("error", f"Login failed: {exc}")
                return
            self._success("chatgpt", message, connect)

        self._run_task(work)
