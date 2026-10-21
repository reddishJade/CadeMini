"""Cade 的独立凭据存储与刷新。"""

from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

import filelock

from cade.ai.auth import (
    AUTH_PROVIDER_REGISTRY,
    AuthCredential,
    CredentialResolver,
)

DEFAULT_AUTH_FILE = Path.home() / ".cade" / "auth.json"
DEFAULT_HOST_FILE = Path.home() / ".cade" / "host.json"
_HOST_ID_KEY = "ext_agent_host_id"


def load_or_create_host_id(path: Path | None = None) -> str:
    """读取或生成宿主的稳定 SIWC 标识。

    标识必须跨登录保持稳定，且不能包含邮箱、用户 ID 等可识别信息。
    无法持久化时抛出 OSError，避免每次登录生成不同的标识。
    """
    path = path or DEFAULT_HOST_FILE
    host_id = _read_host_id(path)
    if host_id:
        return host_id

    host_id = f"urn:uuid:{uuid.uuid4()}"
    _atomic_write_json(path, {_HOST_ID_KEY: host_id})
    return host_id


def _atomic_write_json(path: Path, payload: object) -> None:
    """原子写入 JSON，并把 Unix 权限限制为仅所有者可读写。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_host_id(path: Path) -> str | None:
    """读取已保存的宿主标识，格式无效时返回 None。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get(_HOST_ID_KEY)
    if isinstance(value, str) and value.startswith("urn:uuid:"):
        return value
    return None


class JsonCredentialStore:
    """读取和保存 ``~/.cade/auth.json`` 中的凭据。"""

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
        self._write(credentials)

    def delete(self, provider: str) -> bool:
        credentials = self.load_all()
        if provider not in credentials:
            return False
        del credentials[provider]
        self._write(credentials)
        return True

    def _write(self, credentials: dict[str, AuthCredential]) -> None:
        """原子写入凭据文件，避免中断时留下半个 JSON。"""
        payload = {provider: item.to_dict() for provider, item in credentials.items()}
        _atomic_write_json(self.path, payload)


def resolve_chatgpt_credential(
    store: JsonCredentialStore | None = None,
    *,
    provider: str = "chatgpt",
) -> AuthCredential | None:
    """读取并按需刷新 ChatGPT 套餐凭据。

    刷新令牌每次都会轮换，跨进程用文件锁串行化刷新，避免并发请求
    使其中一个进程拿到已失效的令牌。
    """
    resolved_store = store or JsonCredentialStore()
    if not resolved_store.path.exists():
        return None
    resolved_store.path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(resolved_store.path.with_suffix(".refresh.lock")))
    with lock:
        resolver = CredentialResolver(resolved_store, AUTH_PROVIDER_REGISTRY)
        return resolver.resolve(provider)
