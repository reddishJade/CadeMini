"""Cade 项目配置的回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cade.app.config import CadeConfig


def test_defaults_when_config_is_missing(tmp_path: Path) -> None:
    config = CadeConfig.load(tmp_path)

    assert config.provider is None
    assert config.model is None
    assert config.max_steps is None
    assert config.read_only is False
    assert config.instructions is True
    assert config.mode == "build"
    assert config.sandbox == "workspace-write"
    assert config.network is True


def test_loads_values(tmp_path: Path) -> None:
    (tmp_path / "cade.json").write_text(
        json.dumps(
            {
                "provider": "openai",
                "model": "gpt-5.5",
                "reasoning_effort": "low",
                "context_window": 64000,
                "max_steps": 7,
                "sessions_dir": "data/sessions",
                "read_only": True,
                "instructions": False,
                "auto_review": True,
                "prompt_modules": ["identity"],
                "mode": "plan",
                "sandbox": "read-only",
                "network": False,
                "rules": [
                    {
                        "tool": "bash",
                        "command_prefix": "git status",
                        "decision": "allow",
                    }
                ],
                "mcp_servers": {"fake": {"command": "python", "args": ["server.py"]}},
            }
        ),
        encoding="utf-8",
    )

    config = CadeConfig.load(tmp_path)

    assert config.provider == "openai"
    assert config.model == "gpt-5.5"
    assert config.reasoning_effort == "low"
    assert config.context_window == 64_000
    assert config.max_steps == 7
    assert config.sessions_dir == "data/sessions"
    assert config.read_only is True
    assert config.instructions is False
    assert config.auto_review is True
    assert config.prompt_modules == ["identity"]
    assert config.mode == "plan"
    assert config.sandbox == "read-only"
    assert config.network is False
    assert config.rules[0].tool == "bash"
    assert config.rules[0].decision == "allow"
    assert config.mcp_servers["fake"].command == "python"


def test_explicit_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        CadeConfig.load(tmp_path, tmp_path / "missing.json")


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "cade.json").write_text(
        json.dumps({"unknown": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid config"):
        CadeConfig.load(tmp_path)


def test_invalid_value_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "cade.json").write_text(
        json.dumps({"max_steps": 0}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid config"):
        CadeConfig.load(tmp_path)
