"""Characterization tests for GatewayRunner's inbound message routing.

These tests pin dispatch precedence before extracting MessageRouter out of the
GatewayRunner god class.
"""

from datetime import datetime
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        user_id="user-1",
        chat_id="chat-1",
        user_name="Tester",
        chat_type="dm",
    )


def _make_event(text: str, source: SessionSource | None = None) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source or _make_source(),
        message_id="msg-1",
    )


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="***")}
    )

    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter._pending_messages = {}
    runner.adapters = {Platform.DISCORD: adapter}

    source = _make_source()
    session_entry = SessionEntry(
        session_key=build_session_key(source),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.DISCORD,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.update_session = MagicMock()

    runner.hooks = SimpleNamespace(
        emit=AsyncMock(),
        emit_collect=AsyncMock(return_value=[]),
        loaded_hooks=False,
    )
    from gateway.event_bus import EventBus
    from gateway.message_router import MessageRouter

    runner._event_bus = EventBus()
    runner._message_router = MessageRouter(
        runner.config,
        runner._event_bus,
        runner.session_store,
    )
    runner._gateway_cold_command_handlers = lambda: {
        "approve": runner._handle_approve_command,
    }
    runner.pairing_store = MagicMock()
    runner.pairing_store.is_approved.return_value = True
    runner.pairing_store._is_rate_limited.return_value = False
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._session_run_generation = {}
    runner._session_sources = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._update_prompt_pending = {}
    runner._voice_mode = {}
    runner._draining = False
    runner._busy_input_mode = "interrupt"
    runner._is_user_authorized = lambda _source: True
    runner._check_slash_access = lambda *_args, **_kwargs: None
    runner._is_telegram_topic_root_lobby = lambda _source: False
    runner._begin_session_run_generation = MagicMock(return_value=1)
    runner._post_turn_goal_continuation = AsyncMock()
    runner._handle_message_with_agent = AsyncMock(return_value="agent result")

    def _release(session_key: str) -> None:
        runner._running_agents.pop(session_key, None)
        runner._running_agents_ts.pop(session_key, None)

    runner._release_running_agent_state = MagicMock(side_effect=_release)
    return runner


@pytest.fixture(autouse=True)
def _clear_global_gateway_state(monkeypatch):
    from hermes_cli import plugins
    from tools import clarify_gateway, slash_confirm
    from tools import approval as approval_mod

    slash_confirm._pending.clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(plugins, "invoke_hook", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        clarify_gateway,
        "get_pending_for_session",
        lambda _session_key: None,
    )
    monkeypatch.setattr(
        approval_mod,
        "has_blocking_approval",
        lambda _session_key: False,
    )
    yield
    slash_confirm._pending.clear()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("command_name", "command_args", "expected_action"),
    [
        ("restart", "", "restart"),
        ("stop", "", "stop"),
        ("new", "", "new"),
        ("queue", "next prompt", "queue"),
        ("steer", "mid-run hint", "steer"),
        ("approve", "", "approve"),
        ("deny", "", "deny"),
        ("agents", "", "agents"),
        ("background", "parallel task", "background"),
        ("kanban", "status", "kanban"),
        ("goal", "status", "goal"),
        ("subgoal", "add acceptance criterion", "subgoal"),
        ("yolo", "", "yolo"),
        ("verbose", "", "verbose"),
        ("help", "", "dedicated"),
    ],
)
def test_active_session_command_policy_allows_expected_mid_run_actions(
    command_name,
    command_args,
    expected_action,
):
    from gateway.message_router import resolve_active_session_command_decision

    decision = resolve_active_session_command_decision(
        command_name=command_name,
        command_args=command_args,
        dedicated_handlers={"help", "commands", "profile", "update"},
    )

    assert decision.action == expected_action
    assert decision.response is None


@pytest.mark.parametrize(
    ("command_name", "command_args", "expected_text"),
    [
        ("model", "", "switch models"),
        ("codex-runtime", "", "change runtime"),
        ("goal", "write a second goal", "/goal status / pause / clear"),
        ("fast", "", "can't run mid-turn"),
    ],
)
def test_active_session_command_policy_rejects_mid_run_only_commands(
    command_name,
    command_args,
    expected_text,
):
    from gateway.message_router import resolve_active_session_command_decision

    decision = resolve_active_session_command_decision(
        command_name=command_name,
        command_args=command_args,
        dedicated_handlers={"help", "commands", "profile", "update"},
    )

    assert decision.action == "dedicated"
    assert decision.response is not None
    assert expected_text in decision.response


def test_active_session_command_policy_ignores_plain_text():
    from gateway.message_router import resolve_active_session_command_decision

    decision = resolve_active_session_command_decision(
        command_name=None,
        command_args="",
        dedicated_handlers={"help"},
    )

    assert decision.action == "none"
    assert decision.response is None


@pytest.mark.parametrize(
    ("platform", "message_type", "started_at", "now", "grace_seconds", "expected"),
    [
        (Platform.TELEGRAM, MessageType.TEXT, 100.0, 102.0, 3.0, True),
        (Platform.TELEGRAM, MessageType.TEXT, 100.0, 104.0, 3.0, False),
        (Platform.DISCORD, MessageType.TEXT, 100.0, 102.0, 3.0, False),
        (Platform.TELEGRAM, MessageType.PHOTO, 100.0, 102.0, 3.0, False),
        (Platform.TELEGRAM, MessageType.TEXT, 100.0, 100.5, 0.0, False),
        (Platform.TELEGRAM, MessageType.TEXT, 0.0, 100.5, 3.0, False),
    ],
)
def test_telegram_followup_grace_policy(
    platform,
    message_type,
    started_at,
    now,
    grace_seconds,
    expected,
):
    from gateway.message_router import should_queue_telegram_followup

    assert should_queue_telegram_followup(
        platform=platform,
        message_type=message_type,
        started_at=started_at,
        now=now,
        grace_seconds=grace_seconds,
    ) is expected


@pytest.mark.parametrize(
    ("kwargs", "expected_action"),
    [
        (
            {"message_type": MessageType.PHOTO},
            "queue_photo",
        ),
        (
            {
                "platform": Platform.TELEGRAM,
                "started_at": 100.0,
                "now": 101.0,
            },
            "queue_telegram_grace",
        ),
        (
            {"running_agent_is_pending": True},
            "queue_pending",
        ),
        (
            {"running_agent_is_pending": True, "command": "stop"},
            "stop_pending",
        ),
        (
            {"draining": True, "queue_during_drain": True},
            "drain_queue",
        ),
        (
            {"draining": True, "queue_during_drain": False},
            "drain_reject",
        ),
        (
            {"busy_input_mode": "queue"},
            "queue_busy",
        ),
        (
            {"busy_input_mode": "steer"},
            "steer_busy",
        ),
        (
            {},
            "interrupt",
        ),
    ],
)
def test_active_session_followup_policy(kwargs, expected_action):
    from gateway.message_router import resolve_active_session_followup_decision

    defaults = {
        "platform": Platform.DISCORD,
        "message_type": MessageType.TEXT,
        "command": None,
        "started_at": 100.0,
        "now": 110.0,
        "telegram_followup_grace_seconds": 3.0,
        "running_agent_is_pending": False,
        "draining": False,
        "queue_during_drain": False,
        "busy_input_mode": "interrupt",
    }
    defaults.update(kwargs)

    decision = resolve_active_session_followup_decision(**defaults)

    assert decision.action == expected_action


def test_quick_commands_from_config_reads_object_and_dict_config():
    from gateway.cold_command_router import quick_commands_from_config

    object_config = GatewayConfig()
    object_config.quick_commands = {"s": {"type": "alias", "target": "/status"}}
    dict_config = {"quick_commands": {"m": {"type": "exec", "command": "uptime"}}}

    assert quick_commands_from_config(object_config) == object_config.quick_commands
    assert quick_commands_from_config(dict_config) == dict_config["quick_commands"]
    assert quick_commands_from_config({"quick_commands": "invalid"}) == {}


def test_builtin_precedence_quick_alias_rewrites_unknown_alias_target():
    from gateway.cold_command_router import resolve_builtin_precedence_quick_alias

    config = GatewayConfig()
    config.quick_commands = {
        "s": {"type": "alias", "target": "/status"},
    }

    rewrite = resolve_builtin_precedence_quick_alias(
        config=config,
        command="s",
        command_args="--json",
    )

    assert rewrite is not None
    assert rewrite.text == "/status --json"
    assert rewrite.command == "status"


def test_builtin_precedence_quick_alias_preserves_real_builtin():
    from gateway.cold_command_router import resolve_builtin_precedence_quick_alias

    config = GatewayConfig()
    config.quick_commands = {
        "status": {"type": "alias", "target": "/model"},
    }

    assert resolve_builtin_precedence_quick_alias(
        config=config,
        command="status",
        command_args="",
    ) is None


def test_cold_command_dispatch_loads_quick_and_skill_commands():
    from gateway.cold_command_router import resolve_cold_command_dispatch

    config = GatewayConfig()
    config.quick_commands = {
        "dev": {"type": "alias", "target": "/hermes-agent-dev"},
    }
    skill_commands = {"/hermes-agent-dev": {"name": "Hermes Agent Dev"}}

    dispatch = resolve_cold_command_dispatch(
        config=config,
        command="dev",
        command_args="check",
        skill_commands_provider=lambda: skill_commands,
    )

    assert dispatch is not None
    assert dispatch.quick_commands == config.quick_commands
    assert dispatch.skill_commands == skill_commands
    assert dispatch.command_dispatch.route == "quick_alias"


def test_cold_command_dispatch_handles_skill_provider_failure():
    from gateway.cold_command_router import resolve_cold_command_dispatch

    def _raise():
        raise RuntimeError("skill cache unavailable")

    dispatch = resolve_cold_command_dispatch(
        config=GatewayConfig(),
        command="unknown",
        command_args="",
        skill_commands_provider=_raise,
    )

    assert dispatch is not None
    assert dispatch.skill_commands == {}


def test_cold_command_response_helpers():
    from gateway.cold_command_router import (
        should_return_unknown_slash_command,
        unavailable_gateway_command_response,
        unknown_slash_command_response,
    )

    unavailable = unavailable_gateway_command_response("config")
    unknown = unknown_slash_command_response("made_up_thing")

    assert "isn't available" in unavailable
    assert "/config" in unavailable
    assert "Unknown command" in unknown
    assert "/made_up_thing" in unknown
    assert should_return_unknown_slash_command(
        command="made_up_thing",
        known_command=False,
    ) is True
    assert should_return_unknown_slash_command(
        command="reload_mcp",
        known_command=True,
    ) is False


@pytest.mark.parametrize(
    ("hook_results", "expected_action", "expected_response"),
    [
        (
            [{"decision": "deny", "message": "Blocked by ACL"}],
            "deny",
            "Blocked by ACL",
        ),
        (
            [{"decision": "deny"}],
            "deny",
            "Command `/status` was blocked by a hook.",
        ),
        (
            [{"decision": "handled", "message": "Already done"}],
            "handled",
            "Already done",
        ),
        (
            [{"decision": "handled"}],
            "handled",
            None,
        ),
    ],
)
def test_command_hook_decision_terminal_actions(
    hook_results,
    expected_action,
    expected_response,
):
    from gateway.cold_command_router import resolve_command_hook_decision

    decision = resolve_command_hook_decision(
        command="status",
        hook_results=hook_results,
    )

    assert decision.action == expected_action
    assert decision.response == expected_response


def test_command_hook_decision_rewrite_normalizes_command_and_args():
    from gateway.cold_command_router import resolve_command_hook_decision

    decision = resolve_command_hook_decision(
        command="status",
        hook_results=[
            {
                "decision": "rewrite",
                "command_name": "/metricas",
                "raw_args": " dias:7 ",
            }
        ],
    )

    assert decision.action == "rewrite"
    assert decision.command_name == "metricas"
    assert decision.raw_args == "dias:7"


def test_command_hook_decision_ignores_allow_and_invalid_values():
    from gateway.cold_command_router import resolve_command_hook_decision

    decision = resolve_command_hook_decision(
        command="status",
        hook_results=["ignore", None, {}, {"decision": "allow"}],
    )

    assert decision.action == "allow"
    assert decision.response is None


@pytest.mark.asyncio
async def test_execute_plugin_command_runs_sync_handler():
    from gateway.cold_command_router import execute_plugin_command

    result = await execute_plugin_command(
        handler_key="metricas",
        raw_args="dias:7",
        handler_lookup=lambda key: (
            (lambda args: f"{key}:{args}") if key == "metricas" else None
        ),
    )

    assert result == "metricas:dias:7"


@pytest.mark.asyncio
async def test_execute_plugin_command_runs_async_handler():
    from gateway.cold_command_router import execute_plugin_command

    async def _handler(args):
        return f"async:{args}"

    result = await execute_plugin_command(
        handler_key="metricas",
        raw_args="dias:7",
        handler_lookup=lambda _key: _handler,
    )

    assert result == "async:dias:7"


@pytest.mark.asyncio
async def test_execute_plugin_command_returns_none_for_missing_or_empty_handler():
    from gateway.cold_command_router import execute_plugin_command

    missing = await execute_plugin_command(
        handler_key="missing",
        raw_args="",
        handler_lookup=lambda _key: None,
    )
    empty = await execute_plugin_command(
        handler_key="empty",
        raw_args="",
        handler_lookup=lambda _key: (lambda _args: ""),
    )

    assert missing is None
    assert empty is None


class _QuickCommandProcess:
    def __init__(self, stdout=b"", stderr=b""):
        self.stdout = stdout
        self.stderr = stderr
        self.killed = False
        self.waited = False

    async def communicate(self):
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True


@pytest.mark.asyncio
async def test_execute_quick_command_sanitizes_redacts_and_returns_stdout():
    from gateway.cold_command_router import execute_quick_command

    calls = {}

    async def _spawn(cmd, *, stdout, stderr, env):
        calls["cmd"] = cmd
        calls["stdout"] = stdout
        calls["stderr"] = stderr
        calls["env"] = env
        return _QuickCommandProcess(stdout=b"token=SECRET\n")

    result = await execute_quick_command(
        command_name="limits",
        exec_cmd="print-secret",
        env={"SECRET": "raw", "KEEP": "1"},
        create_subprocess_shell=_spawn,
        sanitize_env=lambda env: {"KEEP": env["KEEP"]},
        redact_text=lambda text: text.replace("SECRET", "[redacted]"),
    )

    assert result == "token=[redacted]"
    assert calls["cmd"] == "print-secret"
    assert calls["env"] == {"KEEP": "1"}


@pytest.mark.asyncio
async def test_execute_quick_command_returns_stderr_when_stdout_empty():
    from gateway.cold_command_router import execute_quick_command

    async def _spawn(*_args, **_kwargs):
        return _QuickCommandProcess(stdout=b"", stderr=b"warning\n")

    result = await execute_quick_command(
        command_name="limits",
        exec_cmd="warn",
        create_subprocess_shell=_spawn,
        sanitize_env=lambda env: env,
        redact_text=lambda text: text,
    )

    assert result == "warning"


@pytest.mark.asyncio
async def test_execute_quick_command_handles_empty_missing_and_spawn_errors():
    from gateway.cold_command_router import execute_quick_command

    async def _empty(*_args, **_kwargs):
        return _QuickCommandProcess()

    async def _raise(*_args, **_kwargs):
        raise RuntimeError("spawn failed")

    missing = await execute_quick_command(
        command_name="limits",
        exec_cmd="",
    )
    empty = await execute_quick_command(
        command_name="limits",
        exec_cmd="empty",
        create_subprocess_shell=_empty,
        sanitize_env=lambda env: env,
        redact_text=lambda text: text,
    )
    error = await execute_quick_command(
        command_name="limits",
        exec_cmd="boom",
        create_subprocess_shell=_raise,
        sanitize_env=lambda env: env,
        redact_text=lambda text: text,
    )

    assert missing == "Quick command '/limits' has no command defined."
    assert empty == "Command returned no output."
    assert error == "Quick command error: spawn failed"


@pytest.mark.asyncio
async def test_execute_quick_command_times_out_and_kills_process():
    from gateway.cold_command_router import execute_quick_command

    proc = _QuickCommandProcess()

    async def _slow_communicate():
        await asyncio.sleep(1)
        return b"late", b""

    proc.communicate = _slow_communicate

    async def _spawn(*_args, **_kwargs):
        return proc

    result = await execute_quick_command(
        command_name="limits",
        exec_cmd="slow",
        timeout_seconds=0.01,
        create_subprocess_shell=_spawn,
        sanitize_env=lambda env: env,
        redact_text=lambda text: text,
    )

    assert result == "Quick command timed out (30s)."
    assert proc.killed is True
    assert proc.waited is True


def test_build_bundle_invocation_returns_message_and_missing_skills():
    from gateway.cold_command_router import build_bundle_invocation

    result = build_bundle_invocation(
        bundle_key="/backend-dev",
        user_instruction="review",
        task_id="session-1",
        bundle_builder=lambda *args, **kwargs: ("bundle message", ["a"], ["b"]),
    )

    assert result is not None
    assert result.message == "bundle message"
    assert result.missing == ("b",)


def test_build_bundle_invocation_ignores_missing_key_or_empty_builder_result():
    from gateway.cold_command_router import build_bundle_invocation

    assert build_bundle_invocation(
        bundle_key=None,
        user_instruction="review",
        task_id="session-1",
        bundle_builder=lambda *args, **kwargs: ("bundle message", [], []),
    ) is None
    assert build_bundle_invocation(
        bundle_key="/backend-dev",
        user_instruction="review",
        task_id="session-1",
        bundle_builder=lambda *args, **kwargs: None,
    ) is None


def test_build_skill_invocation_decision_returns_skill_message():
    from types import SimpleNamespace

    from gateway.cold_command_router import build_skill_invocation_decision

    decision = build_skill_invocation_decision(
        command_dispatch=SimpleNamespace(route="skill", handler_slash_key="/dev"),
        command="dev",
        skill_commands={"/dev": {"name": "Dev"}},
        platform_value="telegram",
        user_instruction="review",
        task_id="session-1",
        unavailable_skill_checker=lambda _command: None,
        known_command_checker=lambda _command: False,
        disabled_skill_names_provider=lambda **_kwargs: set(),
        skill_message_builder=lambda *args, **kwargs: "skill message",
    )

    assert decision.message == "skill message"
    assert decision.response is None


def test_build_skill_invocation_decision_returns_disabled_response():
    from types import SimpleNamespace

    from gateway.cold_command_router import build_skill_invocation_decision

    decision = build_skill_invocation_decision(
        command_dispatch=SimpleNamespace(route="skill", handler_slash_key="/dev"),
        command="dev",
        skill_commands={"/dev": {"name": "Dev"}},
        platform_value="telegram",
        user_instruction="review",
        task_id="session-1",
        unavailable_skill_checker=lambda _command: None,
        known_command_checker=lambda _command: False,
        disabled_skill_names_provider=lambda **_kwargs: {"Dev"},
        skill_message_builder=lambda *args, **kwargs: "skill message",
    )

    assert decision.message is None
    assert "disabled for telegram" in decision.response


def test_build_skill_invocation_decision_handles_unavailable_and_unknown():
    from types import SimpleNamespace

    from gateway.cold_command_router import build_skill_invocation_decision

    unavailable = build_skill_invocation_decision(
        command_dispatch=SimpleNamespace(route="unknown", handler_slash_key=None),
        command="installed-disabled",
        skill_commands={},
        platform_value="telegram",
        user_instruction="review",
        task_id="session-1",
        unavailable_skill_checker=lambda command: f"{command} unavailable",
        known_command_checker=lambda _command: False,
        skill_key_resolver=lambda _command: None,
        skill_message_builder=lambda *args, **kwargs: None,
    )
    unknown = build_skill_invocation_decision(
        command_dispatch=SimpleNamespace(route="unknown", handler_slash_key=None),
        command="made_up_thing",
        skill_commands={},
        platform_value="telegram",
        user_instruction="review",
        task_id="session-1",
        unavailable_skill_checker=lambda _command: None,
        known_command_checker=lambda _command: False,
        skill_key_resolver=lambda _command: None,
        skill_message_builder=lambda *args, **kwargs: None,
    )
    known = build_skill_invocation_decision(
        command_dispatch=SimpleNamespace(route="unknown", handler_slash_key=None),
        command="reload_mcp",
        skill_commands={},
        platform_value="telegram",
        user_instruction="review",
        task_id="session-1",
        unavailable_skill_checker=lambda _command: None,
        known_command_checker=lambda _command: True,
        skill_key_resolver=lambda _command: None,
        skill_message_builder=lambda *args, **kwargs: None,
    )

    assert unavailable.response == "installed-disabled unavailable"
    assert "Unknown command" in unknown.response
    assert known.response is None
    assert known.message is None


@pytest.mark.asyncio
async def test_message_router_helper_resolves_plain_text_confirm_choice():
    from gateway.message_router import route_pending_slash_confirm_reply
    from tools import slash_confirm

    session_key = "agent:main:discord:dm:chat-1"
    choices = []

    async def _handler(choice: str) -> str:
        choices.append(choice)
        return f"confirmed:{choice}"

    slash_confirm.register(session_key, "confirm-1", "reload-mcp", _handler)

    result = await route_pending_slash_confirm_reply(
        session_key=session_key,
        raw_reply="always approve",
        command=None,
    )

    assert result is not None
    assert result.response == "confirmed:always"
    assert choices == ["always"]
    assert slash_confirm.get_pending(session_key) is None


@pytest.mark.asyncio
async def test_pending_slash_confirm_approve_resolves_confirmation_not_command():
    from tools import slash_confirm

    runner = _make_runner()
    event = _make_event("/approve")
    session_key = build_session_key(event.source)
    choices = []

    async def _handler(choice: str) -> str:
        choices.append(choice)
        return f"confirmed:{choice}"

    slash_confirm.register(session_key, "confirm-1", "reload-mcp", _handler)
    runner._handle_approve_command = AsyncMock(
        side_effect=AssertionError("/approve command should not run")
    )

    result = await runner._handle_message(event)

    assert result == "confirmed:once"
    assert choices == ["once"]
    assert slash_confirm.get_pending(session_key) is None
    runner._handle_approve_command.assert_not_awaited()
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_approval_takes_precedence_over_pending_slash_confirm(monkeypatch):
    from tools import approval as approval_mod
    from tools import slash_confirm

    runner = _make_runner()
    event = _make_event("/approve")
    session_key = build_session_key(event.source)
    choices = []

    async def _handler(choice: str) -> str:
        choices.append(choice)
        return f"confirmed:{choice}"

    slash_confirm.register(session_key, "confirm-1", "reload-mcp", _handler)
    monkeypatch.setattr(
        approval_mod,
        "has_blocking_approval",
        lambda _session_key: True,
    )
    runner._handle_approve_command = AsyncMock(return_value="tool approved")

    result = await runner._handle_message(event)

    assert result == "tool approved"
    assert choices == []
    assert slash_confirm.get_pending(session_key) is not None
    runner._handle_approve_command.assert_awaited_once_with(event)
    runner._handle_message_with_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_slash_confirm_does_not_block_unrelated_plain_text():
    from tools import slash_confirm

    runner = _make_runner()
    event = _make_event("continue the conversation")
    session_key = build_session_key(event.source)
    choices = []

    async def _handler(choice: str) -> str:
        choices.append(choice)
        return f"confirmed:{choice}"

    slash_confirm.register(session_key, "confirm-1", "reload-mcp", _handler)

    result = await runner._handle_message(event)

    assert result == "agent result"
    assert choices == []
    assert slash_confirm.get_pending(session_key) is not None
    runner._handle_message_with_agent.assert_awaited_once()
    runner._begin_session_run_generation.assert_called_once_with(session_key)
    assert session_key not in runner._running_agents
