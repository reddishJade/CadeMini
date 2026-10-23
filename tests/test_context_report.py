"""上下文报告与实际请求共用来源，并覆盖渐进式技能加载。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest

from cade.ai.events import FinalMessage, ProviderEvent
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.app.context_report import format_context_usage
from cade.app.session import ApplicationSession
from cade.coding.security.policy import filter_tools
from cade.harness.request import build
from cade.harness.session.projection import project
from cade.harness.session.types import ToolCallBlock
from cade.harness.window import reset_window


class RecordingProvider:
    model = "fake"
    transport = "fake"
    context_window = 100_000

    def __init__(self) -> None:
        self.requests: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolDefinition],
        options: StreamOptions | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        self.requests.append(messages)
        yield FinalMessage("done", "end_turn")


@pytest.fixture
def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[ApplicationSession]:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    for name in ("review", "manual"):
        directory = tmp_path / ".cade" / "skills" / name
        directory.mkdir(parents=True)
        manual = "disable-model-invocation: true\n" if name == "manual" else ""
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Review code\n{manual}---\nExact workflow\n",
            encoding="utf-8",
        )
        references = directory / "references"
        references.mkdir()
        (references / "guide.md").write_text("Review guide", encoding="utf-8")
    with ExitStack() as resources:
        yield create_session(
            tmp_path,
            CadeConfig(trust_project_skills=True),
            provider=RecordingProvider(),
            resources=resources,
        ).session


async def approve(call: ToolCallBlock) -> bool:
    return True


def test_context_lists_actual_sources_and_every_available_tool(
    session: ApplicationSession,
) -> None:
    (session.workspace / "CLAUDE.md").write_text("Project rules", encoding="utf-8")
    (session.workspace / "NOTE.md").write_text("Current work", encoding="utf-8")
    (session.workspace / "MEMORY.md").write_text("Durable fact", encoding="utf-8")
    session.goals.set("Finish the review")
    original_context = session.context
    session.context = lambda: (
        *original_context(),
        "<custom>Extra runtime context</custom>",
    )
    report = "\n".join(format_context_usage(session))
    assert "Project instructions · CLAUDE.md" in report
    assert "Working note · NOTE.md" in report
    assert "Memory · MEMORY.md" in report
    assert "Finish the review" in report
    assert "Execution mode: build" in report
    assert "Extra runtime context" in report
    assert "Skill catalog · review" in report
    assert "manual" not in report
    for tool in filter_tools(session.tools, session.policy.mode):
        assert f"  {tool.name} · ~" in report


def test_context_does_not_list_unloaded_workspace_files(
    session: ApplicationSession,
) -> None:
    (session.workspace / "AGENTS.md").write_text("", encoding="utf-8")
    (session.workspace / "CLAUDE.md").write_text("Unused fallback", encoding="utf-8")
    (session.workspace / "NOTE.md").write_text("", encoding="utf-8")
    report = "\n".join(format_context_usage(session))
    assert "Project instructions ·" not in report
    assert "Working note ·" not in report
    assert "Unused fallback" not in report


async def test_context_shows_skill_body_reference_and_attached_file(
    session: ApplicationSession,
) -> None:
    await session.execute_tool("skill", {"name": "review"}, approve)
    await session.execute_tool(
        "skill", {"name": "review", "reference": "guide.md"}, approve
    )
    (session.workspace / "input.txt").write_text("Attached evidence", encoding="utf-8")
    session.log.append("user", {"text": session.expand("$manual @input.txt")})
    before = session.log.path.read_bytes()
    report = "\n".join(format_context_usage(session))
    assert "Skill instructions · review" in report
    assert "Skill instructions · manual" in report
    assert "reference: guide.md" in report
    assert "Attached file · input.txt" in report
    assert "Conversation · 7 messages" in report
    assert "Exact workflow" in report
    assert session.log.path.read_bytes() == before
    assert isinstance(session.provider, RecordingProvider)
    assert session.provider.requests == []


@pytest.mark.parametrize("transition", ["compaction", "window"])
async def test_context_includes_restored_skill_and_matches_next_request(
    session: ApplicationSession, transition: str
) -> None:
    await session.execute_tool("skill", {"name": "review"}, approve)
    retained = session.log.append("user", {"text": "Continue"})
    if transition == "compaction":
        session.log.append(
            "compaction",
            {
                "summary": "Earlier review",
                "first_kept_id": retained.id,
                "tokens_before": 1000,
                "model": "fake",
                "provider": "fake",
            },
        )
    else:
        assert reset_window(session.log, handoff="Earlier review")
    before = session.log.path.read_bytes()
    report = "\n".join(format_context_usage(session))
    request_context = session.request_context()
    assert sum("<skill name=" in part for part in request_context) == 1
    prefix, conversation = report.split("Conversation ·", 1)
    assert "Skill instructions · review" in prefix
    assert "Skill instructions · review" not in conversation
    assert "summary" in conversation
    definitions = tuple(
        ToolDefinition(tool.name, tool.description, tool.parameters)
        for tool in filter_tools(session.tools, session.policy.mode)
    )
    request = build(
        project(session.log.branch()),
        tools=definitions,
        budget=session.budget,
        context=request_context,
    )
    expected = request.report.estimated_tokens
    display = f"{expected / 1000:.1f}k" if expected >= 1000 else str(expected)
    assert f"~{display}/" in report
    assert session.log.path.read_bytes() == before
    await session.run_task("Continue again", lambda event: None)
    assert isinstance(session.provider, RecordingProvider)
    sent = str(session.provider.requests[-1][0]["content"])
    assert sent.count("Exact workflow") == 1
