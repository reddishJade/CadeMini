"""First-run terminal setup: explicit provider/model choice and credential onboarding."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from cade_mini.ai.models import (
    get_model_reasoning_efforts,
    get_models,
)
from cade_mini.hosts.interaction_settings import AuthenticationService, SettingsService

from .inline_prompt import enter_text, select


def run_first_time_setup(workspace: Path) -> bool:
    """Configure a real model before resolving a provider or starting the TUI.

    This runs only when the project has no configuration and the user enters
    an interactive terminal. Credentials remain in ~/.cade-mini/auth.json.
    """
    auth = AuthenticationService()
    provider = select(
        "Step 1  ·  Choose a provider",
        [
            ("chatgpt", "Sign in with ChatGPT · plan usage"),
            ("openai", "OpenAI Responses · API key"),
        ],
    )
    if provider is None:
        return False
    models = get_models("openai")
    model = select(
        "Step 2  ·  Choose a model",
        [(item.id, item.name) for item in models]
        + [("__custom__", "Custom model ID (enter exactly)")],
    )
    if model is None:
        return False
    if model == "__custom__":
        model = enter_text("Enter the model ID exposed by your provider")
        if not model or not model.strip():
            return False
        model = model.strip()

    efforts = get_model_reasoning_efforts(model)
    effort: str | None = None
    if efforts:
        effort = select(
            "Step 3  ·  Select reasoning effort",
            [(value, value) for value in efforts],
        )
        if effort is None:
            return False

    saved = auth.store.get(provider)
    ready = (
        saved is not None
        and bool(saved.access)
        and not saved.is_expired()
        and saved.type == ("oauth" if provider == "chatgpt" else "api_key")
    )
    if not ready:
        if provider == "chatgpt":
            print("Cade Mini  ·  Sign in with ChatGPT", flush=True)

            def show(message: str) -> None:
                print(message, flush=True)

            try:
                asyncio.run(auth.login_chatgpt(show, threading.Event()))
            except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
                print(f"Authentication failed: {exc}")
                return False
        elif not os.environ.get("OPENAI_API_KEY"):
            secret = enter_text(
                "Connect OpenAI  ·  Enter API key (hidden)",
                password=True,
            )
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
