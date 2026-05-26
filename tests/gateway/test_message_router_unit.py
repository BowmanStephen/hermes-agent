"""Unit tests for MessageRouter — test extracted module in isolation.

Replaces: test_message_router_characterization.py which stubbed 20+ GatewayRunner attributes
With: Direct MessageRouter construction and testing

This is the correct migration pattern: test the extracted module, not the God object.
"""
from __future__ import annotations

import asyncio
import pytest
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, AsyncMock

from gateway.event_bus import EventBus
from gateway.message_router import MessageRouter, ColdCommandTable, CommandHandler


class TestMessageRouterUnit:
    """Test MessageRouter in isolation — no GatewayRunner, no 20 stubs."""

    @pytest.fixture
    def event_bus(self):
        """Fresh event bus for each test."""
        bus = EventBus()
        bus.set_loop(asyncio.get_event_loop())
        return bus

    @pytest.fixture
    def mock_session_store(self):
        """Mock session store — thin interface, no God object."""
        store = MagicMock()
        store.get_active_session = MagicMock(return_value=None)
        return store

    @pytest.fixture
    def router(self, event_bus, mock_session_store):
        """MessageRouter constructed with explicit dependencies."""
        config = {"model": {"default": "test-model"}}
        return MessageRouter(
            config=config,
            event_bus=event_bus,
            session_store=mock_session_store,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Cold command routing
    # ═══════════════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_cold_command_status(self, router, event_bus):
        """/status routes to cold command and emits event."""
        event_received = False
        received_data = None

        @event_bus.on("command_response")
        async def capture_event(data):
            nonlocal event_received, received_data
            event_received = True
            received_data = data

        event = {
            "text": "/status",
            "platform": "telegram",
            "user_id": "user123",
        }

        result = await router.handle_message(event)

        assert result is not None
        assert result["type"] == "status"
        assert result["platform"] == "telegram"
        assert event_received
        assert received_data["response"] == result

    @pytest.mark.asyncio
    async def test_cold_command_health(self, router):
        """/health is alias for /status."""
        event = {"text": "/health", "platform": "discord", "user_id": "user456"}
        result = await router.handle_message(event)

        assert result is not None
        assert result["type"] == "status"

    @pytest.mark.asyncio
    async def test_cold_command_new_session(self, router, event_bus):
        """/new emits session_create_requested event."""
        event_received = False

        @event_bus.on("session_create_requested")
        async def capture_event(data):
            nonlocal event_received
            event_received = True

        event = {"text": "/new", "user_id": "user789"}
        result = await router.handle_message(event)

        assert result["type"] == "session_created"
        assert event_received

    @pytest.mark.asyncio
    async def test_cold_command_clear(self, router, event_bus):
        """/clear emits session_clear_requested event."""
        event_received = False

        @event_bus.on("session_clear_requested")
        async def capture_event(data):
            nonlocal event_received
            event_received = True

        event = {"text": "/clear", "user_id": "user000"}
        result = await router.handle_message(event)

        assert result["type"] == "session_cleared"
        assert event_received

    # ═══════════════════════════════════════════════════════════════════════════
    # Warm agent routing
    # ═══════════════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_warm_agent_routing(self, router, event_bus):
        """Non-command messages route to warm agent via event."""
        event_received = False
        received_data = None

        @event_bus.on("message_for_agent")
        async def capture_event(data):
            nonlocal event_received, received_data
            event_received = True
            received_data = data

        event = {
            "text": "Hello, can you help me?",
            "platform": "telegram",
            "user_id": "user123",
        }

        result = await router.handle_message(event)

        assert result is None  # Warm path returns None
        assert event_received
        assert received_data["event"] == event

    @pytest.mark.asyncio
    async def test_empty_message_not_routed(self, router, event_bus):
        """Empty messages are ignored."""
        event_emitted = False

        @event_bus.on("message_for_agent")
        async def capture_event(data):
            nonlocal event_emitted
            event_emitted = True

        event = {"text": "   ", "user_id": "user123"}
        result = await router.handle_message(event)

        assert result is None
        assert not event_emitted

    # ═══════════════════════════════════════════════════════════════════════════
    # Command table
    # ═══════════════════════════════════════════════════════════════════════════

    def test_cold_command_names(self, router):
        """Router exposes cold command names."""
        names = router.cold_command_names()

        assert "/status" in names
        assert "/health" in names
        assert "/new" in names
        assert "/clear" in names
        assert "/stop" in names

    def test_is_cold_command(self, router):
        """Router can check if text is a cold command."""
        assert router.is_cold_command("/status")
        assert router.is_cold_command("/STATUS")  # Case insensitive
        assert router.is_cold_command("/help")
        assert not router.is_cold_command("Hello")
        assert not router.is_cold_command("Can you help me?")

    # ═══════════════════════════════════════════════════════════════════════════
    # Event handling
    # ═══════════════════════════════════════════════════════════════════════════

    @pytest.mark.asyncio
    async def test_stop_emits_stop_agent_requested(self, router, event_bus):
        """/stop emits stop_agent_requested event."""
        event_received = False

        @event_bus.on("stop_agent_requested")
        async def capture_event(data):
            nonlocal event_received
            event_received = True

        event = {"text": "/stop", "user_id": "user123"}
        await router.handle_message(event)

        assert event_received

    @pytest.mark.asyncio
    async def test_approve_emits_approval_granted(self, router, event_bus):
        """/approve emits approval_granted event."""
        event_received = False

        @event_bus.on("approval_granted")
        async def capture_event(data):
            nonlocal event_received
            event_received = True

        event = {"text": "/approve", "user_id": "user123"}
        await router.handle_message(event)

        assert event_received


class TestColdCommandTableUnit:
    """Test ColdCommandTable in isolation."""

    def test_register_and_get(self):
        """Commands can be registered and retrieved."""
        table = ColdCommandTable()

        async def handler(event, session_store, config):
            return {"handled": True}

        table.register("/test", handler)

        assert table.get("/test") == handler
        assert table.get("/unknown") is None

    def test_names(self):
        """Names returns all registered commands."""
        table = ColdCommandTable()

        async def handler1(e, s, c): return {}
        async def handler2(e, s, c): return {}

        table.register("/a", handler1)
        table.register("/b", handler2)

        names = table.names()
        assert "/a" in names
        assert "/b" in names
        assert "/c" not in names


class TestEventBusIntegration:
    """Test MessageRouter with real EventBus (integration, not characterization)."""

    @pytest.mark.asyncio
    async def test_multiple_subscribers_receive_event(self):
        """Multiple handlers can subscribe to same event type."""
        bus = EventBus()
        bus.set_loop(asyncio.get_event_loop())

        received_1 = []
        received_2 = []

        @bus.on("test_event")
        async def handler1(data):
            received_1.append(data)

        @bus.on("test_event")
        async def handler2(data):
            received_2.append(data)

        await bus.emit("test_event", {"key": "value"})

        assert len(received_1) == 1
        assert len(received_2) == 1
        assert received_1[0] == received_2[0]

    @pytest.mark.asyncio
    async def test_handler_exception_not_crashed_bus(self):
        """Handler exceptions don't crash the event bus."""
        bus = EventBus()
        bus.set_loop(asyncio.get_event_loop())

        good_received = []

        @bus.on("test_event")
        async def bad_handler(data):
            raise ValueError("Boom!")

        @bus.on("test_event")
        async def good_handler(data):
            good_received.append(data)

        # Should not raise
        await bus.emit("test_event", {"test": "data"})

        assert len(good_received) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Migration notes
# ═══════════════════════════════════════════════════════════════════════════════

"""
OLD PATTERN (characterization test — deprecated):
    def test_message_routing():
        runner = object.__new__(GatewayRunner)
        runner.session_store = MagicMock()  # Stub 1
        runner.config = {}  # Stub 2
        runner._running_agents = {}  # Stub 3
        # ... 17 more stubs
        result = runner.handle_message(event)

NEW PATTERN (unit test — current):
    def test_message_routing():
        router = MessageRouter(config, event_bus, session_store)
        result = router.handle_message(event)

KEY DIFFERENCES:
1. Construct MessageRouter directly — no God object, no stubs
2. Explicit dependencies passed to constructor
3. Tests extracted module API, not GatewayRunner internals
4. EventBus used for integration points instead of method calls
5. State owned by MessageRouter, not GatewayRunner

For migration:
- Keep ONE characterization test as regression guard
- All new tests use this pattern
- Gradually port old tests as modules are extracted
"""
