"""终端共用的项目配置编辑和认证服务。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cade.ai.auth import AuthCredential, LoginCancelledError, get_auth_provider
from cade.ai.models import get_model_reasoning_efforts, get_models
from cade.ai.providers.registry import get_environment_api_key
from cade.app.auth import JsonCredentialStore, load_or_create_host_id
from cade.app.config import CadeConfig
from cade.app.provider import ProviderConfig


class SettingsService:
    """校验项目配置，并通过原子替换保存修改。"""

    def __init__(self, workspace: Path, config_path: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.explicit = config_path is not None
        if config_path is None:
            self.path = self.workspace / "cade.json"
        elif config_path.is_absolute():
            self.path = config_path.resolve()
        else:
            self.path = (self.workspace / config_path).resolve()

    def _load(self) -> CadeConfig:
        return CadeConfig.load(self.workspace, self.path if self.explicit else None)

    def configured_providers(
        self, credential_store: JsonCredentialStore | None = None
    ) -> tuple[str, ...]:
        """列出凭据有效、可刷新或已配置 API key 的提供方。"""
        credentials = (credential_store or JsonCredentialStore()).load_all()
        return tuple(
            provider
            for provider in ("chatgpt", "openai", "deepseek")
            if (
                provider in credentials
                and credentials[provider].access
                and (
                    not credentials[provider].is_expired()
                    or (
                        credentials[provider].type == "oauth"
                        and bool(credentials[provider].refresh)
                    )
                )
            )
            or bool(get_environment_api_key(provider))
        )

    def choices(self) -> tuple[tuple[str, str], ...]:
        """为每个支持的配置项生成一行展示内容。"""
        config = self._load()
        values = config.model_dump(mode="json")
        defaults = ProviderConfig()
        labels = (
            ("provider", "Default Provider"),
            ("model", "Default Model"),
            ("reasoning_effort", "Default Reasoning Effort"),
            ("context_window", "Context Window"),
            ("mode", "Default Mode"),
            ("max_steps", "Max Steps"),
            ("read_only", "Read Only"),
            ("instructions", "Project Instructions"),
            ("note", "Working Note"),
            ("memory", "Memory"),
            ("auto_review", "Automatic Review"),
            ("sessions_dir", "Sessions Directory"),
            ("prompt_modules", "Prompt Modules"),
            ("rules", "Permission Rules"),
            ("mcp_servers", "MCP Servers"),
        )
        entries: list[tuple[str, str]] = []
        for name, label in labels:
            value = values[name]
            if name in {"provider", "model", "reasoning_effort"}:
                value = value or getattr(defaults, name)
            if isinstance(value, bool):
                shown = "on" if value else "off"
            elif isinstance(value, (dict, list)):
                shown = f"{len(value)} entries"
            elif value is None:
                shown = "(model default)" if name == "context_window" else "(unset)"
            else:
                shown = str(value)
            entries.append((name, f"{label:<28}{shown}"))
        return tuple(entries)

    def edit_command(self, name: str) -> str:
        config = self._load()
        if name not in CadeConfig.model_fields:
            raise ValueError(f"Unknown config setting: {name}")
        value = config.model_dump(mode="json")[name]
        return f"/config {name}={json.dumps(value, ensure_ascii=False)}"

    def edit_options(self, name: str) -> tuple[tuple[str, str], ...]:
        """枚举配置返回候选值，自由文本配置交给内联编辑器。"""
        if name not in CadeConfig.model_fields:
            raise ValueError(f"Unknown config setting: {name}")
        config = self._load()
        if name in {"read_only", "instructions", "note", "memory", "auto_review"}:
            return (("true", "On"), ("false", "Off"))
        if name == "mode":
            return tuple((json.dumps(mode), mode) for mode in ("ask", "build", "plan"))
        if name == "provider":
            return tuple(
                (json.dumps(provider), provider)
                for provider in self.configured_providers()
            )
        if name == "model":
            providers = self.configured_providers()
            if not providers:
                return ()
            models = [model for provider in providers for model in get_models(provider)]
            current = config.model
            if current and current not in {item.id for item in models}:
                return ((json.dumps(current), f"{current} · configured"),) + tuple(
                    (json.dumps(model.id), model.name) for model in models
                )
            return tuple((json.dumps(model.id), model.name) for model in models)
        if name == "reasoning_effort":
            model = config.model or ProviderConfig().model
            return tuple(
                (json.dumps(level), level)
                for level in get_model_reasoning_efforts(model)
            )
        return ()

    def save(self, name: str, raw: str) -> str:
        return self.save_many({name: raw})

    def save_many(self, changes: dict[str, str]) -> str:
        """先校验全部修改，再原子替换完整配置文件。"""
        existing = self._load().model_dump(mode="json")
        for name, raw in changes.items():
            if name not in CadeConfig.model_fields:
                raise ValueError(f"Unknown config setting: {name}")
            try:
                parsed: Any = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            existing[name] = parsed
        config = CadeConfig.model_validate(existing)
        content = (
            json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2)
            + "\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tempname = tempfile.mkstemp(prefix=".cade-config-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tempname, self.path)
        finally:
            if os.path.exists(tempname):
                os.unlink(tempname)
        names = ", ".join(changes)
        return f"Saved {names} in {self.path.name}; takes effect on next startup"


class AuthenticationService:
    """管理凭据状态、API key 和可取消的 ChatGPT SIWC 登录。"""

    PROVIDERS = ("chatgpt", "openai", "deepseek")

    def __init__(self) -> None:
        self.store = JsonCredentialStore()

    def choices(self) -> tuple[tuple[str, str], ...]:
        credentials = self.store.load_all()
        result = []
        for provider in self.PROVIDERS:
            connected = provider in credentials or bool(
                get_environment_api_key(provider)
            )
            label = "connected" if connected else "not connected"
            result.append((provider, f"{provider} · {label}"))
        return tuple(result)

    def status(self) -> tuple[str, ...]:
        accounts = self.store.load_all()
        if not accounts:
            return ("No stored provider credentials",)
        return tuple(
            f"{name}: {credential.type} · {'expired' if credential.is_expired() else 'available'}"
            for name, credential in sorted(accounts.items())
        )

    def save_api_key(self, provider: str, secret: str) -> str:
        if provider not in {"openai", "deepseek"}:
            raise ValueError("Choose an API-key provider")
        if not secret.strip():
            raise ValueError("API key cannot be empty")
        self.store.save(
            AuthCredential(type="api_key", provider=provider, access=secret.strip())
        )
        return f"Saved {provider} credentials"

    def logout(self, provider: str) -> str:
        if provider not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        return (
            f"Logged out: {provider}"
            if self.store.delete(provider)
            else f"No {provider} credentials"
        )

    async def login_chatgpt(
        self,
        notify: Callable[[str], None],
        cancel: threading.Event,
    ) -> str:
        """沿用 CLI 的 SIWC 浏览器流程与持久化主机 ID。"""
        auth = get_auth_provider("chatgpt")
        if auth is None:
            raise RuntimeError("ChatGPT OAuth adapter is unavailable")
        credential = await asyncio.to_thread(
            auth.login,
            method="browser",
            notify_callback=lambda *parts: notify("\n".join(str(p) for p in parts)),
            cancel_event=cancel,
            existing=self.store.get("chatgpt"),
            host_id=load_or_create_host_id(),
        )
        if cancel.is_set():
            raise LoginCancelledError("ChatGPT authorization cancelled")
        if not credential.access:
            raise ValueError("Authorization returned an empty token")
        self.store.save(credential)
        return "ChatGPT login complete"
