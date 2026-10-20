"""First-run onboarding must remain inline, cancellable and non-destructive."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from cade_mini.hosts.tui import inline_prompt, setup


def _press(fn: Any, keys: str) -> str | None:
    """Drive the actual terminal event loop without an attached terminal."""
    with create_pipe_input() as pipe:
        result: list[str | None] = []
        errors: list[Exception] = []

        def run() -> None:
            try:
                result.append(fn(input=pipe, output=DummyOutput()))
            except Exception as exc:  # noqa: BLE001 - propagate worker errors
                errors.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        pipe.send_text(keys)
        worker.join(timeout=4)
        assert not worker.is_alive(), "The setup selector did not return"
        assert not errors, errors
        return result[0]


def test_inline_provider_selector_keyboard_and_cancel() -> None:
    def pick(**kwargs: Any) -> str | None:
        return inline_prompt.select(
            "Choose a provider",
            [("chatgpt", "ChatGPT"), ("openai", "OpenAI")],
            **kwargs,
        )

    assert _press(pick, "\r") == "chatgpt"
    assert _press(pick, "\x1b[B\r") == "openai"
    assert _press(pick, "\x1b") is None


def test_inline_text_and_secret_prompts_are_cancellable() -> None:
    def ask(**kwargs: Any) -> str | None:
        return inline_prompt.enter_text("Enter API key", password=True, **kwargs)

    assert _press(ask, "a-test-secret\r") == "a-test-secret"
    assert _press(ask, "\x1b") is None


def test_setup_saves_validated_choices_after_authentication(
    tmp_path: Path, monkeypatch: Any
) -> None:
    saved: list[tuple[str, str]] = []

    class FakeAuth:
        def __init__(self) -> None:
            self.store = SimpleNamespace(get=lambda _provider: None)

        def save_api_key(self, provider: str, key: str) -> None:
            saved.append((provider, key))

    options = iter(["openai", "model-from-registry", "medium"])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(setup, "AuthenticationService", FakeAuth)
    monkeypatch.setattr(setup, "select", lambda *_: next(options))
    monkeypatch.setattr(setup, "enter_text", lambda *_, **__: "a-test-secret")
    monkeypatch.setattr(
        setup,
        "get_models",
        lambda _: [SimpleNamespace(id="model-from-registry", name="Test Model")],
    )
    monkeypatch.setattr(
        setup, "get_model_reasoning_efforts", lambda _: ("none", "medium")
    )

    assert setup.run_first_time_setup(tmp_path)
    assert saved == [("openai", "a-test-secret")]
    project = json.loads((tmp_path / "cade-mini.json").read_text())
    assert project["provider"] == "openai"
    assert project["model"] == "model-from-registry"
    assert project["reasoning_effort"] == "medium"


def test_cancel_does_not_write_partial_configuration(
    tmp_path: Path, monkeypatch: Any
) -> None:
    options = iter(["chatgpt", None])
    monkeypatch.setattr(setup, "select", lambda *_: next(options))
    monkeypatch.setattr(setup, "get_models", lambda _: [])
    assert not setup.run_first_time_setup(tmp_path)
    assert not (tmp_path / "cade-mini.json").exists()
