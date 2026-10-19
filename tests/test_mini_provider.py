"""Cade Mini provider 与凭据的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade_mini.ai.auth import AuthCredential
from cade_mini.app.auth import JsonCredentialStore, resolve_codex_credential
from cade_mini.app.provider import ProviderConfig, build_provider


def _credential(access: str = "token") -> AuthCredential:
    return AuthCredential(
        type="oauth",
        provider="openai-codex",
        access=access,
        refresh="refresh",
        expires=9999999999,
        account_id="account",
    )


def test_json_credential_store_roundtrip(tmp_path: Path) -> None:
    store = JsonCredentialStore(tmp_path / "auth.json")

    store.save(_credential())

    loaded = store.get("openai-codex")
    assert loaded == _credential()
    assert store.delete("openai-codex") is True
    assert store.get("openai-codex") is None


def test_resolve_codex_credential_uses_store(tmp_path: Path) -> None:
    store = JsonCredentialStore(tmp_path / "auth.json")
    store.save(_credential())

    credential = resolve_codex_credential(store)

    assert credential is not None
    assert credential.access == "token"


def test_build_codex_provider_uses_stored_oauth(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    JsonCredentialStore(auth_path).save(_credential())

    provider = build_provider(
        ProviderConfig(provider="openai-codex", model="gpt-5.6-luna"),
        auth_path=auth_path,
    )

    assert provider.transport == "openai_codex"
    assert provider.model == "gpt-5.6-luna"


def test_build_codex_provider_requires_login(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Missing openai-codex login"):
        build_provider(
            ProviderConfig(provider="openai-codex", model="gpt-5.6-luna"),
            auth_path=tmp_path / "missing.json",
        )


def test_build_responses_provider_with_explicit_key() -> None:
    provider = build_provider(
        ProviderConfig(
            provider="openai",
            model="gpt-5.5",
            api_key="test-key",
        )
    )

    assert provider.transport == "openai_responses"
    assert provider.model == "gpt-5.5"


def test_build_responses_provider_uses_stored_api_key(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    JsonCredentialStore(auth_path).save(
        AuthCredential(
            type="api_key",
            provider="openai",
            access="stored-key",
        )
    )

    provider = build_provider(
        ProviderConfig(provider="openai", model="gpt-5.5"),
        auth_path=auth_path,
    )

    assert provider.transport == "openai_responses"
    assert provider.model == "gpt-5.5"


def test_unsupported_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported provider 'deepseek'"):
        build_provider(
            ProviderConfig(provider="deepseek", model="deepseek-chat"),
            auth_path=tmp_path / "missing.json",
        )


def test_provider_config_rejects_empty_values() -> None:
    with pytest.raises(ValueError):
        ProviderConfig(provider="")
    with pytest.raises(ValueError):
        ProviderConfig(model="")


def test_default_provider_config() -> None:
    config = ProviderConfig()

    assert config.provider == "openai-codex"
    assert config.model == "gpt-5.6-luna"
