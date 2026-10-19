"""First-run terminal setup: explicit provider/model choice and credential onboarding."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from prompt_toolkit.shortcuts import input_dialog, radiolist_dialog

from cade_mini.ai.models import (
    get_model_reasoning_efforts,
    get_models,
)
from cade_mini.hosts.interaction_settings import AuthenticationService, SettingsService


def _choose(title: str, values: list[tuple[str, str]]) -> str | None:
    """One native terminal choice; Esc/Cancel leaves settings untouched."""
    if not values:
        return None
    return radiolist_dialog(
        title=title,
        text="Use ↑/↓ and Enter to select.",
        values=values,
    ).run()


def run_first_time_setup(workspace: Path) -> bool:
    """Configure a real model before resolving a provider or starting the TUI.

    This runs only when the project has no configuration and the user enters
    an interactive terminal. Credentials remain in ~/.cade-mini/auth.json.
    """
    auth = AuthenticationService()
    provider = _choose(
        "Welcome to Cade v2 · provider",
        [
            ("chatgpt", "Sign in with ChatGPT · plan usage"),
            ("openai", "OpenAI Responses · API key"),
        ],
    )
    if provider is None:
        return False
    models = get_models("openai")
    model = _choose(
        f"{provider} · model",
        [(item.id, item.name) for item in models]
        + [("__custom__", "Custom model ID (enter exactly)")],
    )
    if model is None:
        return False
    if model == "__custom__":
        model = input_dialog(
            title="Custom model",
            text="Enter the model ID exposed by your provider:",
        ).run()
        if not model or not model.strip():
            return False
        model = model.strip()

    efforts = get_model_reasoning_efforts(model)
    effort: str | None = None
    if efforts:
        effort = _choose(
            f"{model} · reasoning effort",
            [(value, value) for value in efforts],
        )
        if effort is None:
            return False

    if auth.store.get(provider) is None:
        if provider == "chatgpt":

            def show(message: str) -> None:
                print(message, flush=True)

            try:
                asyncio.run(auth.login_chatgpt(show, threading.Event()))
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                print(f"Authentication failed: {exc}")
                return False
        else:
            secret = input_dialog(
                title=f"{provider} API key",
                text="Enter API key (hidden, saved outside the workspace):",
                password=True,
            ).run()
            if not secret:
                return False
            auth.save_api_key(provider, secret)
            secret = ""

    settings = SettingsService(workspace)
    # Do not leave a half-configured project if a subsequent write fails.
    changes = {"provider": provider, "model": model}
    if effort is not None:
        changes["reasoning_effort"] = effort
    settings.save_many(changes)
    return True
