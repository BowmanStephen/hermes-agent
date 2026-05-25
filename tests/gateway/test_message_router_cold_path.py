"""Unit tests for MessageRouter.route_cold_command (production cold path)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.event_bus import EventBus
from gateway.message_router import (
    ColdRouteContext,
    ColdRouteOutcome,
    MessageRouter,
)
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        user_id="user-1",
        chat_id="chat-1",
        user_name="Tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_make_source(),
        message_id="msg-1",
    )


def _make_router() -> MessageRouter:
    return MessageRouter(
        GatewayConfig(
            platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="***")}
        ),
        EventBus(),
        MagicMock(),
    )


def _cold_ctx(
    event: MessageEvent,
    *,
    handlers: dict | None = None,
    draining: bool = False,
    check_slash_access=None,
    hooks_emit_collect=None,
) -> ColdRouteContext:
    source = event.source
    return ColdRouteContext(
        event=event,
        source=source,
        task_id="agent:main:discord:dm:chat-1",
        config=GatewayConfig(),
        hooks_emit_collect=hooks_emit_collect or AsyncMock(return_value=[]),
        gateway_handlers=handlers or {},
        check_slash_access=check_slash_access or (lambda *_a, **_k: None),
        is_telegram_topic_root_lobby=lambda _s: False,
        telegram_topic_root_new_message=lambda: "",
        should_send_telegram_lobby_reminder=lambda _s: False,
        telegram_topic_root_lobby_message=lambda: "lobby",
        status_action_gerund=lambda: "shutting down",
        maybe_confirm_destructive_slash=AsyncMock(),
        handle_reset_command=AsyncMock(),
        handle_undo_command=AsyncMock(),
        unavailable_skill_checker=lambda _c: None,
        draining=draining,
    )


@pytest.mark.asyncio
async def test_route_cold_command_dispatches_gateway_handler():
    router = _make_router()
    event = _make_event("/status")
    handler = AsyncMock(return_value="status ok")

    result = await router.route_cold_command(
        _cold_ctx(event, handlers={"status": handler})
    )

    assert result.outcome == ColdRouteOutcome.RETURN
    assert result.response == "status ok"
    handler.assert_awaited_once_with(event)


@pytest.mark.asyncio
async def test_route_cold_command_emits_resolved_event():
    router = _make_router()
    bus = router._event_bus
    queue = bus.subscribe("gateway.cold_command.resolved")
    event = _make_event("/help")
    handler = AsyncMock(return_value="help")

    await router.route_cold_command(_cold_ctx(event, handlers={"help": handler}))

    payload = await queue.get()
    assert payload["command"] == "help"
    assert payload["canonical"] == "help"
    assert payload["task_id"] == "agent:main:discord:dm:chat-1"


@pytest.mark.asyncio
async def test_route_cold_command_denies_slash_access():
    router = _make_router()
    event = _make_event("/status")

    result = await router.route_cold_command(
        _cold_ctx(
            event,
            handlers={"status": AsyncMock()},
            check_slash_access=lambda _s, _c: "denied",
        )
    )

    assert result.outcome == ColdRouteOutcome.RETURN
    assert result.response == "denied"


@pytest.mark.asyncio
async def test_route_cold_command_hook_deny_short_circuits():
    router = _make_router()
    event = _make_event("/status")
    hooks = AsyncMock(
        return_value=[{"decision": "deny", "message": "blocked by hook"}]
    )

    result = await router.route_cold_command(
        _cold_ctx(event, handlers={"status": AsyncMock()}, hooks_emit_collect=hooks)
    )

    assert result.response == "blocked by hook"
    hooks.assert_awaited()


@pytest.mark.asyncio
async def test_route_cold_command_draining_rejects_before_quick_commands():
    router = _make_router()
    event = _make_event("/unknown-quick")

    result = await router.route_cold_command(_cold_ctx(event, draining=True))

    assert result.outcome == ColdRouteOutcome.RETURN
    assert "not accepting new work" in result.response


@pytest.mark.asyncio
async def test_route_cold_command_plain_text_falls_through_warm():
    router = _make_router()
    event = _make_event("hello there")

    result = await router.route_cold_command(_cold_ctx(event))

    assert result.outcome == ColdRouteOutcome.WARM_AGENT


@pytest.mark.asyncio
async def test_route_cold_command_expands_quick_alias_before_builtin():
    router = _make_router()
    config = GatewayConfig()
    config.quick_commands = {
        "s": {"type": "alias", "target": "/status"},
    }
    event = _make_event("/s --json")
    handler = AsyncMock(return_value="ok")

    result = await router.route_cold_command(
        ColdRouteContext(
            event=event,
            source=event.source,
            task_id="task-1",
            config=config,
            hooks_emit_collect=AsyncMock(return_value=[]),
            gateway_handlers={"status": handler},
            check_slash_access=lambda *_a, **_k: None,
            is_telegram_topic_root_lobby=lambda _s: False,
            telegram_topic_root_new_message=lambda: "",
            should_send_telegram_lobby_reminder=lambda _s: False,
            telegram_topic_root_lobby_message=lambda: "lobby",
            status_action_gerund=lambda: "draining",
            maybe_confirm_destructive_slash=AsyncMock(),
            handle_reset_command=AsyncMock(),
            handle_undo_command=AsyncMock(),
            unavailable_skill_checker=lambda _c: None,
        )
    )

    assert result.response == "ok"
    assert event.text == "/status --json"
    handler.assert_awaited_once()
