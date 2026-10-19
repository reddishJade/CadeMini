"""Cade Mini provider 与凭据的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cade_mini.ai.auth import AuthCredential
from cade_mini.app.auth import JsonCredentialStore, resolve_chatgpt_credential
from cade_mini.app.provider import ProviderConfig, build_provider


def _credential(access: str = "token") -> AuthCredential:
    return AuthCredential(
        type="oauth",
        provider="chatgpt",
        access=access,
        refresh="refresh",
        expires=9999999999,
        extra={
            "client_id": "oaiapp_test",
            "id_token": "id-token",
            "scopes": ["openid", "chatgpt.tokens.use.direct"],
        },
    )


def test_json_credential_store_roundtrip(tmp_path: Path) -> None:
    store = JsonCredentialStore(tmp_path / "auth.json")

    store.save(_credential())

    loaded = store.get("chatgpt")
    assert loaded == _credential()
    assert store.delete("chatgpt") is True
    assert store.get("chatgpt") is None


def test_resolve_chatgpt_credential_uses_store(tmp_path: Path) -> None:
    store = JsonCredentialStore(tmp_path / "auth.json")
    store.save(_credential())

    credential = resolve_chatgpt_credential(store)

    assert credential is not None
    assert credential.access == "token"


def test_build_chatgpt_provider_uses_stored_oauth(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    JsonCredentialStore(auth_path).save(_credential())

    provider = build_provider(
        ProviderConfig(provider="chatgpt", model="gpt-5.6-luna"),
        auth_path=auth_path,
    )

    assert provider.transport == "chatgpt"
    assert provider.model == "gpt-5.6-luna"


def test_build_chatgpt_provider_requires_login(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Missing ChatGPT login"):
        build_provider(
            ProviderConfig(provider="chatgpt", model="gpt-5.6-luna"),
            auth_path=tmp_path / "missing.json",
        )


def test_build_chatgpt_provider_rejects_incomplete_credentials(
    tmp_path: Path,
) -> None:
    auth_path = tmp_path / "auth.json"
    JsonCredentialStore(auth_path).save(
        AuthCredential(provider="chatgpt", access="token", refresh="refresh")
    )

    with pytest.raises(RuntimeError, match="missing an issued client ID"):
        build_provider(
            ProviderConfig(provider="chatgpt", model="gpt-5.6-luna"),
            auth_path=auth_path,
        )


def test_build_chatgpt_provider_requires_plan_scope(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    credential = _credential()
    assert credential.extra is not None
    credential.extra["scopes"] = ["openid"]
    JsonCredentialStore(auth_path).save(credential)

    with pytest.raises(RuntimeError, match="plan usage is not authorized"):
        build_provider(
            ProviderConfig(provider="chatgpt", model="gpt-5.6-luna"),
            auth_path=auth_path,
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

    assert config.provider == "chatgpt"
    assert config.model == "gpt-5.6-luna"
