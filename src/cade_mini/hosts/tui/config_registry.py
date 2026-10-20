"""TUI 设置浏览器使用的最小配置注册表。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cade_mini.ai.models import get_model_reasoning_efforts, get_models
from cade_mini.app.config import MiniConfig
from cade_mini.hosts.interaction_settings import AuthenticationService

CONFIG_FILENAME = "cade-mini.json"


class SettingKind(StrEnum):
    INFO = "info"
    TEXT = "text"
    BOOL = "bool"
    MENU = "menu"


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    description: str
    kind: SettingKind = SettingKind.TEXT
    nullable: bool = False

    @property
    def is_menu(self) -> bool:
        return self.kind is SettingKind.MENU


@dataclass(frozen=True)
class MenuItem:
    title: str
    value: str
    description: str = ""
    custom: bool = False


SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("provider", "Default Provider", "AI provider", SettingKind.MENU),
    SettingSpec("model", "Default Model", "Registered model id", SettingKind.MENU),
    SettingSpec(
        "reasoning_effort",
        "Default Reasoning Effort",
        "Reasoning effort level",
        SettingKind.MENU,
        nullable=True,
    ),
    SettingSpec(
        "context_window",
        "Context Window",
        "Maximum input tokens",
        SettingKind.TEXT,
        nullable=True,
    ),
    SettingSpec("mode", "Default Mode", "ask, build or plan", SettingKind.MENU),
    SettingSpec(
        "max_steps", "Max Steps", "Maximum agent steps", SettingKind.TEXT, True
    ),
    SettingSpec("read_only", "Read Only", "Deny writes and shell", SettingKind.BOOL),
    SettingSpec(
        "instructions", "Project Instructions", "Load AGENTS.md", SettingKind.BOOL
    ),
    SettingSpec("note", "Working Note", "Load NOTE.md", SettingKind.BOOL),
    SettingSpec("memory", "Memory", "Load MEMORY.md", SettingKind.BOOL),
    SettingSpec(
        "auto_review", "Automatic Review", "Review risky tools", SettingKind.BOOL
    ),
    SettingSpec(
        "sessions_dir",
        "Sessions Directory",
        "Session log directory",
        SettingKind.TEXT,
        True,
    ),
    SettingSpec("sandbox", "Sandbox", "Filesystem policy", SettingKind.MENU),
    SettingSpec("network", "Network", "Allow tool network access", SettingKind.BOOL),
)


def load_effective_config(config_path: Path) -> MiniConfig:
    if not config_path.is_file():
        return MiniConfig()
    return MiniConfig.load(config_path.parent, config_path)


def find_setting(query: str) -> SettingSpec | None:
    normalized = query.strip().lower()
    return next((spec for spec in SETTING_SPECS if spec.key == normalized), None)


def matching_settings(query: str) -> list[SettingSpec]:
    normalized = query.strip().lower()
    return [
        spec
        for spec in SETTING_SPECS
        if normalized in spec.key.lower() or normalized in spec.label.lower()
    ]


def format_setting(spec: SettingSpec, config: MiniConfig) -> str:
    value = getattr(config, spec.key)
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return "(unset)"
    return str(value)


def setting_detail(spec: SettingSpec, config: MiniConfig) -> list[str]:
    del config
    return [f"key: {spec.key}", f"kind: {spec.kind.value}"]


def _write_config(config_path: Path, config: MiniConfig) -> None:
    content = (
        json.dumps(config.model_dump(mode="json", exclude_none=True), indent=2) + "\n"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tempname = tempfile.mkstemp(prefix=".cade-mini-config-", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tempname, config_path)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)


def _parse_value(spec: SettingSpec, raw: str) -> Any:
    text = raw.strip()
    if spec.nullable and text.lower() == "none":
        return None
    if spec.kind is SettingKind.BOOL:
        return text.lower() in {"1", "true", "on", "yes"}
    if spec.kind is SettingKind.MENU:
        if text.startswith('"'):
            return json.loads(text)
        return text
    if spec.key in {"context_window", "max_steps"}:
        return int(text)
    return text


def save_setting_text(
    config_path: Path, spec: SettingSpec, raw: str
) -> tuple[bool, str]:
    try:
        value = _parse_value(spec, raw)
        existing = load_effective_config(config_path).model_dump(mode="json")
        existing[spec.key] = value
        config = MiniConfig.model_validate(existing)
        _write_config(config_path, config)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, f"Failed to save {spec.key}: {exc}"
    return True, f"Saved {spec.key} in {config_path.name}; takes effect on next start"


def commit_setting_value(
    config_path: Path, spec: SettingSpec, value: str
) -> tuple[bool, str]:
    return save_setting_text(config_path, spec, value)


def setting_menu(
    spec: SettingSpec,
    config: MiniConfig,
    env_files: tuple[Path, ...] = (),
) -> list[MenuItem]:
    del env_files
    if spec.key == "provider":
        return [
            MenuItem(provider, provider, label)
            for provider, label in AuthenticationService().choices()
        ]
    if spec.key == "model":
        provider = config.provider or "openai"
        return [
            MenuItem(model.name, model.id, model.id) for model in get_models(provider)
        ]
    if spec.key == "reasoning_effort":
        model = config.model or ""
        return [MenuItem(level, level) for level in get_model_reasoning_efforts(model)]
    if spec.key == "mode":
        return [MenuItem(mode, mode) for mode in ("ask", "build", "plan")]
    if spec.key == "sandbox":
        return [
            MenuItem(mode, mode)
            for mode in ("read-only", "workspace-write", "danger-full-access")
        ]
    return []
