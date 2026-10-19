"""验证独立登录入口、凭据目录和失败时不写入凭据。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from cade_mini.ai.auth import AuthCredential
from cade_mini.app import auth as storage
from cade_mini.app.auth import JsonCredentialStore
from cade_mini.extensions.skills import discover_skills
from cade_mini.hosts import auth
from cade_mini.hosts.cli import main


@pytest.fixture
def auth_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".cade-mini" / "auth.json"
    monkeypatch.setattr(storage, "DEFAULT_AUTH_FILE", path)
    return path


def test_default_auth_directory_is_owned_by_mini() -> None:
    assert JsonCredentialStore().path == Path.home() / ".cade-mini" / "auth.json"


def test_api_key_login(auth_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt: " test-key ")

    assert main(["login", "--provider", "openai"]) == 0
    credential = JsonCredentialStore(auth_path).get("openai")
    assert credential is not None
    assert credential.type == "api_key"
    assert credential.access == "test-key"


@pytest.mark.parametrize("method", ["browser", "device_code"])
def test_oauth_login(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    provider = Mock()
    provider.login.return_value = AuthCredential(
        provider="openai-codex", access="token", refresh="refresh"
    )
    monkeypatch.setattr(auth, "get_auth_provider", lambda name: provider)

    assert main(["login", "--method", method]) == 0
    assert provider.login.call_args.kwargs["method"] == method
    assert (
        JsonCredentialStore(auth_path).get("openai-codex")
        == provider.login.return_value
    )


def test_empty_key_does_not_write(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt: " ")

    assert main(["login", "--provider", "openai"]) == 1
    assert not auth_path.exists()


def test_cancelled_login_does_not_write(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth.getpass, "getpass", Mock(side_effect=KeyboardInterrupt))

    assert main(["login", "--provider", "openai"]) == 130
    assert not auth_path.exists()


def test_oauth_failure_preserves_credentials(
    auth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JsonCredentialStore(auth_path)
    store.save(AuthCredential(provider="openai-codex", access="original"))
    before = auth_path.read_bytes()
    provider = Mock()
    provider.login.side_effect = RuntimeError("authorization failed")
    monkeypatch.setattr(auth, "get_auth_provider", lambda name: provider)

    assert main(["login"]) == 1
    assert auth_path.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [["--method", "api_key"], ["--provider", "openai", "--method", "browser"]],
)
def test_login_rejects_wrong_method(auth_path: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["login", *args])

    assert exc.value.code == 2
    assert not auth_path.exists()


def test_old_cade_skill_directory_is_not_discovered(tmp_path: Path) -> None:
    path = tmp_path / ".cade" / "skills" / "legacy" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("Legacy skill body.", encoding="utf-8")

    assert discover_skills(tmp_path, user_dir=tmp_path / "user") == ()
