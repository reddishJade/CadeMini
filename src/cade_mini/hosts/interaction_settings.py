"""User-facing configuration and authentication services shared by terminal hosts."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cade_mini.ai.auth import AuthCredential, LoginCancelledError, get_auth_provider
from cade_mini.app.auth import JsonCredentialStore, load_or_create_host_id
from cade_mini.app.config import MiniConfig


class SettingsService:
    """Validate and atomically store project configuration edits."""

    def __init__(
        self, workspace: Path, config_path: Path | None = None
    ) -> None:
        self.workspace = workspace.resolve()
        self.explicit = config_path is not None
        if config_path is None:
            self.path = self.workspace / "cade-mini.json"
        elif config_path.is_absolute():
            self.path = config_path.resolve()
        else:
            self.path = (self.workspace / config_path).resolve()

    def _load(self) -> MiniConfig:
        return MiniConfig.load(
            self.workspace, self.path if self.explicit else None
        )

    def choices(self) -> tuple[tuple[str, str], ...]:
        config = self._load()
        values = config.model_dump(mode="json")
        items: list[tuple[str, str]] = []
        for name, value in values.items():
            if name in {"rules", "hooks", "mcp_servers", "prompt_modules"}:
                label = f"{name} · {len(value or [])} entries"
            else:
                label = f"{name} · {value}"
            items.append((name, label))
        return tuple(items)

    def edit_command(self, name: str) -> str:
        """Pre-fill the terminal editor with the effective validated value."""
        config = self._load()
        if name not in MiniConfig.model_fields:
            raise ValueError(f"Unknown config setting: {name}")
        value = config.model_dump(mode="json")[name]
        return f"/config {name}={json.dumps(value, ensure_ascii=False)}"

    def save(self, name: str, raw: str) -> str:
        return self.save_many({name: raw})

    def save_many(self, changes: dict[str, str]) -> str:
        """Validate every change before atomically publishing one complete config."""
        existing = self._load().model_dump(mode="json")
        for name, raw in changes.items():
            if name not in MiniConfig.model_fields:
                raise ValueError(f"Unknown config setting: {name}")
            try:
                parsed: Any = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            existing[name] = parsed
        config = MiniConfig.model_validate(existing)
        content = (
            json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2)
            + "\n"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tempname = tempfile.mkstemp(
            prefix=".cade-mini-config-", dir=self.path.parent
        )
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
    """Credential status, OpenAI API keys, and cancellable ChatGPT SIWC."""

    PROVIDERS = ("chatgpt", "openai")

    def __init__(self) -> None:
        self.store = JsonCredentialStore()

    def choices(self) -> tuple[tuple[str, str], ...]:
        credentials = self.store.load_all()
        return tuple(
            (
                provider,
                f"{provider} · {'connected' if provider in credentials else 'not connected'}",
            )
            for provider in self.PROVIDERS
        )

    def status(self) -> tuple[str, ...]:
        accounts = self.store.load_all()
        if not accounts:
            return ("No stored provider credentials",)
        return tuple(
            f"{name}: {credential.type} · {'expired' if credential.is_expired() else 'available'}"
            for name, credential in sorted(accounts.items())
        )

    def save_api_key(self, provider: str, secret: str) -> str:
        if provider != "openai":
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
        """Use the same SIWC browser flow and persistent host ID as the CLI."""
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
