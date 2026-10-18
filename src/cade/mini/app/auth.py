"""Cade Mini 的凭据读取；复用 cade.ai.auth 的类型与刷新协议。"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from cade.ai.auth import (
    AUTH_PROVIDER_REGISTRY,
    AuthCredential,
    CredentialResolver,
)

DEFAULT_AUTH_FILE = Path.home() / ".cade" / "auth.json"


class JsonCredentialStore:
    """与 Cade 共用 ``~/.cade/auth.json`` 的最小凭据存储。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_AUTH_FILE

    def load_all(self) -> dict[str, AuthCredential]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        credentials: dict[str, AuthCredential] = {}
        for provider, value in raw.items():
            if isinstance(value, dict):
                credentials[str(provider)] = AuthCredential.from_dict(
                    value,
                    provider=str(provider),
                )
        return credentials

    def get(self, provider: str) -> AuthCredential | None:
        return self.load_all().get(provider)

    def save(self, credential: AuthCredential) -> None:
        credentials = self.load_all()
        credentials[credential.provider] = credential
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {provider: item.to_dict() for provider, item in credentials.items()}
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def delete(self, provider: str) -> bool:
        credentials = self.load_all()
        if provider not in credentials:
            return False
        del credentials[provider]
        payload = {name: item.to_dict() for name, item in credentials.items()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return True


def resolve_codex_credential(
    store: JsonCredentialStore | None = None,
    *,
    provider: str = "openai-codex",
) -> AuthCredential | None:
    """读取并按需刷新 Codex OAuth 凭据。"""
    resolver = CredentialResolver(
        store or JsonCredentialStore(),
        AUTH_PROVIDER_REGISTRY,
    )
    return resolver.resolve(provider)
