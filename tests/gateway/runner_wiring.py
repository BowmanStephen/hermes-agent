"""Wire partial GatewayRunner test doubles with MessageRouter cold-path deps."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from gateway.command_registry import GATEWAY_HANDLER_METHODS
from gateway.event_bus import EventBus
from gateway.message_router import MessageRouter


def wire_message_router(runner: Any) -> None:
    """Attach event bus, router, and cold handlers when tests skip __init__."""
    if getattr(runner, "_message_router", None) is not None:
        return

    runner._event_bus = EventBus()
    session_store = getattr(runner, "session_store", None) or MagicMock()
    runner.session_store = session_store
    runner._message_router = MessageRouter(
        getattr(runner, "config", {}),
        runner._event_bus,
        session_store,
    )

    if not hasattr(runner, "hooks") or runner.hooks is None:
        runner.hooks = MagicMock()
        runner.hooks.emit_collect = AsyncMock(return_value=[])
        runner.hooks.emit = AsyncMock()

    runner._gateway_cold_handlers = {
        name: getattr(runner, method_name)
        for name, method_name in GATEWAY_HANDLER_METHODS.items()
        if hasattr(runner, method_name) and callable(getattr(runner, method_name))
    }

    if not hasattr(runner, "_draining"):
        runner._draining = False
