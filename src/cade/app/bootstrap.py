"""装配 Cade 应用；不解析命令行参数或操作终端。"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from cade.app.config import CadeConfig
from cade.app.provider import ProviderConfig, build_provider
from cade.app.session import ApplicationSession, UnavailableProvider
from cade.coding.context.instructions import load_project_instructions
from cade.coding.context.memory import MemoryTool, load_memory
from cade.coding.context.note import load_working_note
from cade.coding.context.prompts import render_system_prompt
from cade.coding.goal import GoalStore, GoalTool
from cade.coding.mcp.tools import McpRegistry
from cade.coding.security.approval import SessionApprover
from cade.coding.security.grants import GrantStore
from cade.coding.security.policy import WorkspacePolicy
from cade.coding.security.review import AutoReviewer
from cade.coding.skills.tools import (
    LoadSkillTool,
    discover_skills,
    render_skill_catalog,
)
from cade.coding.subagents import DelegateTool, SubagentRunner, SubagentTool
from cade.coding.tools.question import AnswerHandler, QuestionTool
from cade.coding.tools.registry import build_tools
from cade.coding.tools.webfetch import WebFetchTool
from cade.coding.tools.websearch import WebSearchTool
from cade.coding.workspace.recovery.coordinator import WorkspaceRecovery
from cade.coding.workspace.snapshots import SnapshotStore
from cade.harness.compaction import Compactor
from cade.harness.execution.local.sandbox import PlatformSandbox
from cade.harness.permissions import Approver
from cade.harness.provider import Provider
from cade.harness.request import Budget
from cade.harness.session.log import SessionLog, list_sessions
from cade.harness.tools import Tool
from cade.harness.window import reset_window

WINDOW_HANDOFF = (
    "Earlier history was discarded to fit the model window. "
    "Consult NOTE.md and use the history tool with an entry_id or call_id "
    "to retrieve exact details."
)


@dataclass(frozen=True)
class BootstrappedSession:
    session: ApplicationSession
    approver: Approver
    warnings: tuple[str, ...]


def resolve_sessions_dir(workspace: Path, config: CadeConfig) -> Path:
    """将会话目录配置解析为绝对路径。"""
    raw = Path(config.sessions_dir) if config.sessions_dir else None
    if raw is None:
        return (workspace / ".cade" / "sessions").resolve()
    return (raw if raw.is_absolute() else workspace / raw).resolve()


def undo_session(
    workspace: Path, config: CadeConfig, session_id: str | None = None
) -> str:
    """不装配模型或扩展，使用会话恢复协议撤销一个动作。"""
    workspace = workspace.resolve()
    sessions_dir = resolve_sessions_dir(workspace, config)
    if not session_id:
        sessions = list_sessions(sessions_dir)
        if not sessions:
            raise ValueError("No sessions to undo")
        path = sessions[0].path
    else:
        path = sessions_dir / f"session-{session_id}.jsonl"
    log = SessionLog.open(path)
    meta = next((entry for entry in log.entries() if entry.type == "meta"), None)
    if meta is None or Path(str(meta.data["project"])).resolve() != workspace:
        raise ValueError("Session belongs to another workspace")
    recovery = WorkspaceRecovery(
        SnapshotStore(workspace),
        read_only=config.read_only or config.sandbox == "read-only",
    )
    return recovery.step(log, "undo")


def open_log(
    sessions_dir: Path,
    resume: str | None,
    system_prompt: str,
    project_root: Path,
    *,
    deferred: bool = False,
    recovery: WorkspaceRecovery | None = None,
) -> SessionLog:
    """创建或恢复会话，并在补充系统提示前恢复未完成事务。"""
    sessions = list_sessions(sessions_dir) if resume == "" else []
    if resume is None or (resume == "" and not sessions):
        log = SessionLog.create(
            sessions_dir, project_root=project_root, deferred=deferred
        )
    else:
        path = (
            sessions[0].path
            if resume == ""
            else (sessions_dir / f"session-{resume}.jsonl")
        )
        log = SessionLog.open(path)
        if recovery is not None:
            recovery.recover(log)
    if not any(entry.type == "system" for entry in log.branch()):
        log.append("system", {"text": system_prompt})
    return log


def create_session(
    workspace: Path,
    config: CadeConfig,
    *,
    resources: ExitStack,
    provider: Provider | None = None,
    interactive: bool = False,
    resume: str | None = None,
    auto_approve: bool = False,
    config_path: Path | None = None,
    goal: str | None = None,
    answer: AnswerHandler | None = None,
) -> BootstrappedSession:
    """为单次任务和交互入口创建相同的应用会话。"""
    workspace = workspace.resolve()
    system_prompt = render_system_prompt(workspace, config.prompt_modules)
    defaults = ProviderConfig()
    provider_config = ProviderConfig(
        provider=config.provider or defaults.provider,
        model=config.model or defaults.model,
        reasoning_effort=(
            config.reasoning_effort
            if config.reasoning_effort is not None
            else defaults.reasoning_effort
        ),
        context_window=config.context_window,
    )
    try:
        selected = provider or build_provider(provider_config)
    except RuntimeError as exc:
        if not interactive:
            raise
        selected = UnavailableProvider(
            provider_config.model, provider_config.provider, str(exc)
        )
    context_window = config.context_window or getattr(selected, "context_window", None)
    if context_window is None:
        raise ValueError(
            "Model context window is unknown; register the model with "
            "metadata or pass --context-window"
        )
    budget = Budget(context_window=context_window)
    max_steps = config.max_steps or 50
    sessions_dir = resolve_sessions_dir(workspace, config)
    skill_registry = discover_skills(
        workspace,
        trust_project_skills=config.trust_project_skills,
        skills_dir=Path(config.skills_dir) if config.skills_dir else None,
    )
    skill_catalog = render_skill_catalog(skill_registry)
    mcp = McpRegistry(config.mcp_servers, workspace)
    resources.callback(mcp.close)
    mcp_tools = asyncio.run(mcp.refresh())
    snapshots = SnapshotStore(workspace)
    goals = GoalStore(workspace)
    if goal:
        goals.set(goal)
    temp_dir = Path(
        resources.enter_context(tempfile.TemporaryDirectory(prefix="cade-"))
    )
    sandbox = PlatformSandbox(
        workspace,
        temp_dir,
        mode=config.sandbox,
        network=config.network,
        protected_paths=(workspace / ".git" / "hooks", workspace / ".git" / "config"),
        hidden_paths=(workspace / ".cade", Path.home() / ".cade"),
        readable_paths=(
            *(skill.file_path.parent for skill in skill_registry.definitions()),
        ),
    )
    tools = (
        *build_tools(workspace, snapshots=snapshots, sandbox=sandbox),
        WebFetchTool(),
        WebSearchTool(),
        LoadSkillTool(workspace, skill_registry),
        MemoryTool(workspace),
        GoalTool(workspace),
        *mcp_tools,
    )
    grants = GrantStore()
    policy = WorkspacePolicy(
        workspace,
        read_only=config.read_only,
        mode=config.mode,
        sandbox=sandbox.mode,
        temp_dir=temp_dir,
        rules=config.rules,
        grants=grants,
        network=config.network,
    )
    approver: Approver = SessionApprover(auto_approve=auto_approve, grants=grants)
    if config.auto_review and not auto_approve:
        approver = AutoReviewer(selected, workspace=workspace)

    def note_context() -> str | None:
        return load_working_note(workspace) if config.note else None

    def fallback_window(log: SessionLog) -> bool:
        return reset_window(log, handoff=WINDOW_HANDOFF, reason="fallback")

    def context() -> Sequence[str]:
        parts = [policy.describe()]
        if config.instructions:
            instructions = load_project_instructions(workspace)
            if instructions:
                parts.append(instructions)
        if skill_catalog:
            parts.append(skill_catalog)
        note = note_context()
        if note:
            parts.append(note)
        memory = load_memory(workspace) if config.memory else None
        if memory:
            parts.append(memory)
        active_goal = goals.load()
        if active_goal is not None and active_goal.status == "active":
            parts.append(f"<goal>\n{active_goal.objective}\n</goal>")
        return tuple(parts)

    recovery = WorkspaceRecovery(
        snapshots, system_prompt=system_prompt, read_only=policy.read_only
    )

    async def refresh_base_tools() -> Sequence[Tool]:
        remote = await mcp.refresh()
        return (
            *(tool for tool in tools if not tool.name.startswith("mcp__")),
            *remote,
            *((session.question_tool,) if session.question_tool is not None else ()),
        )

    runner = SubagentRunner(
        workspace=workspace,
        provider=selected,
        tools=tools,
        budget=budget,
        sessions_dir=sessions_dir,
        system_prompt=system_prompt,
        policy=policy,
        approve=approver,
        context=context,
        max_steps=min(max_steps, 20),
        lifecycle=recovery,
        refresh_tools=refresh_base_tools,
    )
    log = open_log(
        sessions_dir,
        resume,
        system_prompt,
        workspace,
        deferred=interactive,
        recovery=recovery,
    )
    recovery.recover(log)
    session = ApplicationSession(
        log=log,
        sessions_dir=sessions_dir,
        system_prompt=system_prompt,
        provider=selected,
        provider_config=provider_config,
        tools=(*tools, SubagentTool(runner=runner), DelegateTool(runner=runner)),
        skills=skill_registry.definitions(),
        mcp=mcp,
        workspace=workspace,
        snapshots=snapshots,
        goals=goals,
        policy=policy,
        budget=budget,
        context=context,
        max_steps=max_steps,
        compactor=Compactor(selected, extra_context=note_context),
        window=fallback_window,
        automatic_approver=approver if auto_approve or config.auto_review else None,
        config_path=config_path,
        context_window_explicit=bool(config.context_window),
        recovery=recovery,
    )
    if answer is not None:
        session.question_tool = QuestionTool(answer)
    return BootstrappedSession(session, approver, mcp.errors)
