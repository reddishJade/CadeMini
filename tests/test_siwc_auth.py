"""验证 SIWC 授权地址、ID Token 校验与 Token 刷新。"""

from __future__ import annotations

import threading
import time
import urllib.parse
from typing import Any, Self
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cade.ai.auth import AuthCredential
from cade.ai.auth import chatgpt as siwc


@pytest.mark.parametrize(
    ("platform", "command"),
    [
        ("darwin", ["open"]),
        ("win32", ["rundll32", "url.dll,FileProtocolHandler"]),
        ("linux", ["xdg-open"]),
    ],
)
def test_browser_launcher_keeps_output_out_of_terminal(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    command: list[str],
) -> None:
    launcher = Mock()
    monkeypatch.setattr(siwc.sys, "platform", platform)
    monkeypatch.setattr(siwc.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(siwc.subprocess, "Popen", launcher)
    url = "https://example.com/authorize?state=test&nonce=value"

    siwc._open_browser(url)

    launcher.assert_called_once_with(
        [*command, url],
        stdin=siwc.subprocess.DEVNULL,
        stdout=siwc.subprocess.DEVNULL,
        stderr=siwc.subprocess.DEVNULL,
        start_new_session=platform != "win32",
        creationflags=siwc.subprocess.CREATE_NO_WINDOW if platform == "win32" else 0,
    )


@pytest.mark.parametrize("open_browser", [True, False])
def test_browser_login_keeps_manual_link_when_launcher_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, open_browser: bool
) -> None:
    launcher = Mock(side_effect=FileNotFoundError("xdg-open unavailable"))
    monkeypatch.setattr(siwc.subprocess, "Popen", launcher)
    messages: list[str] = []
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(siwc.LoginCancelledError):
        siwc.login_chatgpt_browser(
            existing=AuthCredential(
                provider="chatgpt",
                access="token",
                extra={
                    "client_id": "oaiapp_test",
                    "id_token": "saved-token",
                    "email": "user@example.com",
                },
            ),
            host_id="urn:uuid:test",
            open_browser=open_browser,
            notify_callback=messages.append,
            cancel_event=cancel,
        )

    assert len(messages) == 1
    assert messages[0].startswith("Complete ChatGPT authorization in your browser.")
    assert messages[0].count(siwc.AUTHORIZE_URL) == 1
    query = urllib.parse.parse_qs(
        urllib.parse.urlparse(messages[0].splitlines()[-1]).query
    )
    assert query["client_id"] == ["oaiapp_test"]
    assert "id_token_hint" not in query
    assert "login_hint" not in query
    assert launcher.call_count == int(open_browser)


def _signed_id_token(
    *,
    client_id: str = "oaiapp_test",
    nonce: str = "nonce-value",
    issuer: str = siwc.ISSUER,
    audience: str | None = None,
) -> tuple[str, Any]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    payload = {
        "iss": issuer,
        "aud": audience or client_id,
        "sub": "subject-123",
        "email": "user@example.com",
        "iat": now,
        "exp": now + 600,
        "nonce": nonce,
    }
    token = jwt.encode(payload, private_key, algorithm="RS256")
    return token, private_key.public_key()


class _SigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


class _Jwks:
    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> _SigningKey:
        return _SigningKey(self._key)


def _patch_jwks(monkeypatch: pytest.MonkeyPatch, key: Any) -> None:
    monkeypatch.setattr(siwc, "_jwks", lambda: _Jwks(key))


def test_initial_authorization_url_registers_dynamic_client() -> None:
    url = siwc.build_authorization_url(
        client_id=siwc.CLIENT_ID,
        redirect_uri="http://127.0.0.1:1455/auth/callback",
        state="state",
        nonce="nonce",
        challenge="challenge",
        host_id="urn:uuid:host",
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert url.startswith(siwc.AUTHORIZE_URL)
    assert query["client_id"] == [siwc.CLIENT_ID]
    assert query["agent_name_hint"] == [siwc.AGENT_NAME]
    assert query["ext_agent_host_id"] == ["urn:uuid:host"]
    assert query["scope"] == [siwc.SCOPE]
    assert query["resource"] == [siwc.RESOURCE]
    assert query["code_challenge_method"] == ["S256"]
    assert "id_token_hint" not in query
    assert "login_hint" not in query


def test_returning_authorization_url_reuses_registration() -> None:
    url = siwc.build_authorization_url(
        client_id="oaiapp_test",
        redirect_uri="http://127.0.0.1:1455/auth/callback",
        state="state",
        nonce="nonce",
        challenge="challenge",
        host_id="urn:uuid:host",
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    assert query["client_id"] == ["oaiapp_test"]
    assert "id_token_hint" not in query
    assert "login_hint" not in query
    assert "agent_name_hint" not in query


def test_validate_id_token_accepts_matching_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, public_key = _signed_id_token()
    _patch_jwks(monkeypatch, public_key)

    claims = siwc.validate_id_token(token, client_id="oaiapp_test", nonce="nonce-value")

    assert claims["sub"] == "subject-123"


def test_validate_id_token_rejects_wrong_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, public_key = _signed_id_token()
    _patch_jwks(monkeypatch, public_key)

    with pytest.raises(RuntimeError, match="nonce"):
        siwc.validate_id_token(token, client_id="oaiapp_test", nonce="other")


def test_validate_id_token_rejects_wrong_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, public_key = _signed_id_token(audience="other-client")
    _patch_jwks(monkeypatch, public_key)

    with pytest.raises(RuntimeError, match="Could not validate"):
        siwc.validate_id_token(token, client_id="oaiapp_test", nonce="nonce-value")


def test_credential_requires_plan_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    token, public_key = _signed_id_token()
    _patch_jwks(monkeypatch, public_key)

    with pytest.raises(RuntimeError, match="plan usage"):
        siwc._credential_from_token(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "id_token": token,
                "expires_in": 3600,
                "scope": "openid profile",
            },
            client_id="oaiapp_test",
            nonce="nonce-value",
        )


def test_credential_records_registration_and_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, public_key = _signed_id_token()
    _patch_jwks(monkeypatch, public_key)

    credential = siwc._credential_from_token(
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": token,
            "expires_in": 3600,
            "scope": f"openid {siwc.PLAN_SCOPE}",
        },
        client_id="oaiapp_test",
        nonce="nonce-value",
    )

    assert credential.provider == "chatgpt"
    assert credential.access == "access"
    assert credential.refresh == "refresh"
    assert credential.extra is not None
    assert credential.extra["client_id"] == "oaiapp_test"
    assert credential.extra["email"] == "user@example.com"
    assert credential.extra["scopes"] == ["openid", siwc.PLAN_SCOPE]
    assert "host_id" not in credential.extra


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def post(self, url: str, data: dict[str, Any]) -> _Response:
        self.calls.append({"url": url, "data": data})
        return _Response(self.payload)


def test_refresh_uses_issued_client_id_and_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "scope": f"openid {siwc.PLAN_SCOPE}",
        }
    )
    monkeypatch.setattr(siwc.httpx, "Client", lambda timeout: client)
    previous = AuthCredential(
        provider="chatgpt",
        access="old-access",
        refresh="old-refresh",
        extra={
            "client_id": "oaiapp_test",
            "id_token": "saved-id-token",
            "subject": "subject-123",
            "email": "user@example.com",
            "scopes": ["openid", siwc.PLAN_SCOPE],
        },
    )

    refreshed = siwc.refresh_chatgpt_token(
        "old-refresh",
        client_id="oaiapp_test",
        previous=previous,
    )

    call = client.calls[0]
    assert call["url"] == siwc.TOKEN_URL
    assert call["data"] == {
        "grant_type": "refresh_token",
        "client_id": "oaiapp_test",
        "refresh_token": "old-refresh",
        "resource": siwc.RESOURCE,
    }
    assert refreshed.access == "new-access"
    assert refreshed.refresh == "new-refresh"
    assert refreshed.extra is not None
    assert refreshed.extra["id_token"] == "saved-id-token"
    assert refreshed.extra["subject"] == "subject-123"
    assert refreshed.extra["email"] == "user@example.com"


def test_provider_rejects_device_code() -> None:
    provider = siwc.ChatGPTAuthProvider()

    with pytest.raises(ValueError, match="Unsupported"):
        provider.login(method="device_code", host_id="urn:uuid:host")


def test_reauthorization_rejects_changed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, public_key = _signed_id_token()
    _patch_jwks(monkeypatch, public_key)
    previous = AuthCredential(
        provider="chatgpt",
        access="old-access",
        refresh="old-refresh",
        extra={"client_id": "oaiapp_test", "subject": "someone-else"},
    )

    with pytest.raises(RuntimeError, match="identity changed"):
        siwc._credential_from_token(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "id_token": token,
                "expires_in": 3600,
                "scope": f"openid {siwc.PLAN_SCOPE}",
            },
            client_id="oaiapp_test",
            nonce="nonce-value",
            previous=previous,
        )
