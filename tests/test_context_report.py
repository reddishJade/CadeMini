"""上下文报告与实际请求共用来源，并覆盖渐进式技能加载。"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest

from cade.ai.events import FinalMessage, ProviderEvent
from cade.ai.tokenization import TokenCounter
from cade.ai.types import StreamOptions, ToolDefinition
from cade.app.bootstrap import create_session
from cade.app.config import CadeConfig
from cade.app.context_report import collect_context_usage, inspect_context_usage
from cade.app.session import ApplicationSession
from cade.coding.mcp.client import McpClient
from cade.coding.mcp.tools import McpTool
from cade.coding.mcp_config import McpServerConfig
from cade.coding.security.policy import filter_tools
from cade.harness.request import build
from cade.harness.session.projection import project
from cade.harness.session.types import ToolCallBlock
from cade.harness.window import reset_window
from cade.hosts.tui.context_usage import render_context_usage


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


def test_context_summarizes_actual_sources_without_dumping_content(
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
    usage = collect_context_usage(session)
    report = render_context_usage(usage)
    assert {file.path.name for file in usage.files} == {
        "CLAUDE.md",
        "NOTE.md",
        "MEMORY.md",
    }
    assert "Loaded files" in report
    assert "Project instructions:" in report
    assert "Working note:" in report
    assert "Memory:" in report
    assert "Active goal:" in report
    assert "Skill catalog (1 available)" in report
    assert "Skill files" not in report
    assert "Finish the review" not in report
    assert "Execution mode" not in report
    assert "Extra runtime context" not in report
    assert "Tool definitions" not in report
    assert "Conversation" not in report
    assert "manual" not in report
    assert sum(category.tokens for category in usage.categories) == usage.used
    assert all(category.tokens >= 0 for category in usage.categories)


def test_context_does_not_list_unloaded_workspace_files(
    session: ApplicationSession,
) -> None:
    (session.workspace / "AGENTS.md").write_text("", encoding="utf-8")
    (session.workspace / "CLAUDE.md").write_text("Unused fallback", encoding="utf-8")
    (session.workspace / "NOTE.md").write_text("", encoding="utf-8")
    usage = collect_context_usage(session)
    report = render_context_usage(usage)
    assert usage.files == ()
    assert "Loaded files" not in report
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
    usage = collect_context_usage(session)
    report = render_context_usage(usage)
    skills = [file for file in usage.files if file.kind == "skill"]
    assert {file.path.parent.name + "/" + file.path.name for file in skills} == {
        "review/SKILL.md",
        "manual/SKILL.md",
        "references/guide.md",
    }
    assert "Skill files" in report
    assert "guide.md:" in report
    assert "input.txt:" in report
    assert "Exact workflow" not in report
    assert sum(category.tokens for category in usage.categories) == usage.used
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
    usage = collect_context_usage(session)
    report = render_context_usage(usage)
    request_context = session.request_context()
    assert sum("<skill name=" in part for part in request_context) == 1
    skills = [file for file in usage.files if file.kind == "skill"]
    assert len(skills) == 1
    assert skills[0].path.parent.name == "review"
    assert "Context summaries:" in report
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
    assert usage.used == request.report.estimated_tokens
    assert sum(category.tokens for category in usage.categories) == usage.used
    assert session.log.path.read_bytes() == before
    await session.run_task("Continue again", lambda event: None)
    assert isinstance(session.provider, RecordingProvider)
    sent = str(session.provider.requests[-1][0]["content"])
    assert sent.count("Exact workflow") == 1


def test_message_categories_do_not_double_count_tool_calls(
    session: ApplicationSession,
) -> None:
    session.log.append("user", {"text": "hello"})
    session.log.append(
        "assistant",
        {
            "blocks": [
                {"type": "text", "text": "answer"},
                {
                    "type": "tool_call",
                    "id": "r1",
                    "name": "read",
                    "args": {"path": "a"},
                },
            ]
        },
    )
    session.log.append(
        "tool_result", {"call_id": "r1", "content": "evidence", "status": "ok"}
    )
    usage = collect_context_usage(session)
    categories = {category.name: category.tokens for category in usage.categories}
    assert categories["User messages"] == 5
    assert categories["Agent responses"] == 5
    assert categories["Tool calls"] == 8
    assert categories["Tool results"] == 6
    assert any(file.path == session.workspace / "a" for file in usage.files)
    assert categories["Built-in tools"] > 0
    assert "Subagents" not in categories
    assert sum(categories.values()) == usage.used


def test_mcp_schemas_and_loaded_resources_follow_the_active_context(
    session: ApplicationSession,
) -> None:
    connection = McpClient(McpServerConfig(command=sys.executable), session.workspace)
    resource = McpTool(
        "docs", {"name": "resources_read"}, connection, operation="resources/read"
    )
    search = McpTool("docs", {"name": "search"}, connection)
    assert session.mcp is not None
    session.mcp.tools = (resource, search)
    session.log.append("user", {"text": "Read the handbook"})
    session.log.append(
        "assistant",
        {
            "blocks": [
                {
                    "type": "tool_call",
                    "id": "m1",
                    "name": resource.name,
                    "args": {"uri": "docs://handbook"},
                }
            ]
        },
    )
    session.log.append(
        "tool_result",
        {
            "call_id": "m1",
            "content": "Handbook instructions",
            "status": "ok",
            "artifact": {
                "tool_metadata": {"mcp_server": "docs", "mcp_tool": "resources_read"}
            },
        },
    )
    before = session.log.path.read_bytes()
    usage = collect_context_usage(session)
    report = render_context_usage(usage)
    assert usage.mcp_servers[0].schemas == 2
    assert "MCP tools:" in report
    assert "docs: 2 schemas" in report
    assert "docs://handbook" in report
    assert usage.mcp_content[0].server == "docs"
    assert sum(c.tokens for c in usage.categories) == usage.used
    assert not connection.connected
    assert session.log.path.read_bytes() == before
    session.mcp.tools = (search,)
    changed = collect_context_usage(session)
    assert changed.mcp_servers[0].schemas == 1
    assert changed.mcp_content == usage.mcp_content
    session.log.append("user", {"text": "Continue"})
    reset_window(session.log, handoff="Use the previous conclusion")
    assert collect_context_usage(session).mcp_content == ()


def test_last_request_usage_is_not_the_run_total(session: ApplicationSession) -> None:
    session.log.append("user", {"text": "hello"})
    for input_tokens, output_tokens in ((100, 20), (200, 30)):
        session.log.append(
            "assistant",
            {
                "blocks": [{"type": "text", "text": "answer"}],
                "model": "fake",
                "provider": "fake",
                "usage": {"input": input_tokens, "output": output_tokens},
            },
        )
    session.last_usage = {"input": 300, "output": 50}
    usage = collect_context_usage(session)
    assert usage.last_request == (200, 30)
    report = render_context_usage(usage)
    assert "Last request (provider): 200 input · 30 output" in report
    assert "300 input" not in report
    assert "Input remaining:" in report
    assert usage.input_budget == usage.window - 16384 - 1024


@pytest.mark.parametrize("fails", [False, True])
async def test_context_uses_provider_count_without_generating_or_changing_history(
    session: ApplicationSession, fails: bool
) -> None:
    requests: list[list[dict[str, Any]]] = []

    class CountingProvider(RecordingProvider):
        async def count_input_tokens(
            self,
            messages: list[dict[str, Any]],
            tools: list[ToolDefinition],
            options: StreamOptions | None = None,
        ) -> int | None:
            requests.append(messages)
            if fails:
                raise RuntimeError("Count endpoint is unavailable")
            return 7777

    provider = CountingProvider()
    session.provider = provider
    (session.workspace / "AGENTS.md").write_text(
        "Count these project instructions", encoding="utf-8"
    )
    before = session.log.path.read_bytes()
    result = await session.command("/context")
    usage = result.context_usage
    assert usage is not None
    assert "Count these project instructions" in str(requests[0][0]["content"])
    assert session.log.path.read_bytes() == before
    assert provider.requests == []
    report = render_context_usage(usage)
    if fails:
        assert usage.measured_input is None
        assert "Estimated input:" in report
        assert "Provider count unavailable:" in report
    else:
        assert usage.measured_input == 7777
        assert "Input (provider count): 7,777 / 100,000" in report
        assert usage.count_error is None


async def test_context_tokenizes_each_source_with_the_selected_model(
    session: ApplicationSession,
) -> None:
    class TokenizingProvider(RecordingProvider):
        async def load_token_counter(self) -> TokenCounter:
            return TokenCounter("fixture model tokenizer", len)

    session.provider = TokenizingProvider()
    session.log.append("user", {"text": "你好"})
    usage = await inspect_context_usage(session)
    categories = {category.name: category.tokens for category in usage.categories}
    assert categories["User messages"] == 6
    assert usage.counter_label == "fixture model tokenizer"
    assert sum(categories.values()) == usage.used
    assert "fixture model tokenizer" in render_context_usage(usage)
