"""Message routing policies extracted from GatewayRunner.

This module currently owns routing decisions and characterization-safe helper
policies. Handler ownership still needs to move in the cold command vertical
slice before ``GatewayRunner`` can stop owning the command bodies.

Architecture:
- MessageRouter receives platform messages
- Routes to cold commands (slash commands not needing warm agent) or warm agent
- Uses event_bus for loose coupling (spec rule #1)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, Optional, Protocol

from gateway.active_session_routing import (
    ACTIVE_SESSION_ACTION_AGENTS,
    ACTIVE_SESSION_ACTION_APPROVE,
    ACTIVE_SESSION_ACTION_BACKGROUND,
    ACTIVE_SESSION_ACTION_DEDICATED,
    ACTIVE_SESSION_ACTION_DENY,
    ACTIVE_SESSION_ACTION_GOAL,
    ACTIVE_SESSION_ACTION_KANBAN,
    ACTIVE_SESSION_ACTION_NEW,
    ACTIVE_SESSION_ACTION_NONE,
    ACTIVE_SESSION_ACTION_QUEUE,
    ACTIVE_SESSION_ACTION_RESTART,
    ACTIVE_SESSION_ACTION_STEER,
    ACTIVE_SESSION_ACTION_STOP,
    ACTIVE_SESSION_ACTION_SUBGOAL,
    ACTIVE_SESSION_ACTION_VERBOSE,
    ACTIVE_SESSION_ACTION_YOLO,
    ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE,
    ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT,
    ACTIVE_SESSION_FOLLOWUP_INTERRUPT,
    ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY,
    ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING,
    ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO,
    ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE,
    ACTIVE_SESSION_FOLLOWUP_STEER_BUSY,
    ACTIVE_SESSION_FOLLOWUP_STOP_PENDING,
    ActiveSessionCommandDecision,
    ActiveSessionFollowupDecision,
    SlashConfirmRoutingResult,
    resolve_active_session_command_decision,
    resolve_active_session_followup_decision,
    resolve_unauthorized_dm_behavior,
    route_pending_slash_confirm_reply,
    should_queue_telegram_followup,
)
from gateway.event_bus import EventBus

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_SESSION_ACTION_AGENTS", "ACTIVE_SESSION_ACTION_APPROVE", "ACTIVE_SESSION_ACTION_BACKGROUND",
    "ACTIVE_SESSION_ACTION_DEDICATED", "ACTIVE_SESSION_ACTION_DENY", "ACTIVE_SESSION_ACTION_GOAL",
    "ACTIVE_SESSION_ACTION_KANBAN", "ACTIVE_SESSION_ACTION_NEW", "ACTIVE_SESSION_ACTION_NONE",
    "ACTIVE_SESSION_ACTION_QUEUE", "ACTIVE_SESSION_ACTION_RESTART", "ACTIVE_SESSION_ACTION_STEER",
    "ACTIVE_SESSION_ACTION_STOP", "ACTIVE_SESSION_ACTION_SUBGOAL", "ACTIVE_SESSION_ACTION_VERBOSE",
    "ACTIVE_SESSION_ACTION_YOLO", "ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE",
    "ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT", "ACTIVE_SESSION_FOLLOWUP_INTERRUPT",
    "ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY", "ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING",
    "ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO", "ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE",
    "ACTIVE_SESSION_FOLLOWUP_STEER_BUSY", "ACTIVE_SESSION_FOLLOWUP_STOP_PENDING",
    "ActiveSessionCommandDecision", "ActiveSessionFollowupDecision", "ColdCommandTable", "CommandHandler",
    "MessageRouter", "SlashConfirmRoutingResult", "resolve_active_session_command_decision",
    "resolve_active_session_followup_decision", "resolve_unauthorized_dm_behavior",
    "route_pending_slash_confirm_reply", "should_queue_telegram_followup",
]


# Protocol for command handlers passed through explicit handler maps.
class CommandHandler(Protocol):
    """Protocol for cold command handlers.

    Implementers receive event context and return command result.
    All state needed must be passed explicitly; no implicit GatewayRunner access.
    """

    async def __call__(
        self,
        event: Dict[str, Any],
        session_store: Any,  # SessionStoreInterface
        config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Execute command. Return result dict or None if handled silently."""
        ...


class ColdCommandTable:
    """Explicit command dispatch table, without getattr indirection.

    A later vertical slice can migrate gateway commands from the runner into
    this table once handler bodies have explicit dependencies.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler) -> None:
        """Register a handler for a command."""
        self._handlers[name] = handler
        logger.debug("Registered cold command: %s", name)

    def get(self, name: str) -> Optional[CommandHandler]:
        """Get handler by name. Returns None if not found."""
        return self._handlers.get(name)

    def names(self) -> frozenset[str]:
        """Return all registered command names."""
        return frozenset(self._handlers.keys())


class MessageRouter:
    """Composed service: routes messages to cold commands or warm agent.

    Replaces GatewayRunner.handle_message() logic with explicit routing.
    Services communicate via event_bus, not direct method calls.

    Usage:
        router = MessageRouter(config, event_bus, session_store)
        router.register_cold_commands()  # Sets up command table
        await router.handle_message(event)  # Routes to cold or warm
    """

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        session_store: Any,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._session_store = session_store
        self._cold_commands = ColdCommandTable()
        self._register_core_commands()

    def _register_core_commands(self) -> None:
        """Register prototype cold commands for the future router slice."""
        # Status commands
        self._cold_commands.register("/status", self._handle_status)
        self._cold_commands.register("/health", self._handle_status)

        # Session commands
        self._cold_commands.register("/new", self._handle_new_session)
        self._cold_commands.register("/clear", self._handle_clear_session)

        # Queue commands
        self._cold_commands.register("/queue", self._handle_queue_status)

        # Stop/approve commands that need warm agent interruption
        # These emit events rather than calling runner directly
        self._cold_commands.register("/stop", self._handle_stop_session)
        self._cold_commands.register("/approve", self._handle_approve)
        self._cold_commands.register("/deny", self._handle_deny)

    async def handle_message(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Route message to appropriate handler.

        Returns result dict if handled cold, None if passed to warm agent.
        """
        text = event.get("text", "").strip()
        if not text:
            return None

        # Check for cold command
        canonical = self._canonicalize(text)
        handler = self._cold_commands.get(canonical)

        if handler:
            logger.debug("Cold command: %s", canonical)
            return await handler(event, self._session_store, self._config)

        # Warm agent path; emit event for AgentRunner.
        logger.debug("Warm agent path for: %s", text[:50])
        await self._event_bus.emit("message_for_agent", {
            "event": event,
            "session_store": self._session_store,
        })
        return None

    def _canonicalize(self, text: str) -> str:
        """Convert message text to canonical command name."""
        text = text.strip().lower()
        # Extract first word if space present
        if " " in text:
            text = text.split()[0]
        return text

    # Prototype cold command handlers. Production ownership still moves in the
    # next vertical slice.

    async def _handle_status(
        self,
        event: Dict[str, Any],
        session_store: Any,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle /status and /health commands."""
        platform = event.get("platform", "unknown")
        user_id = event.get("user_id", "unknown")

        # Query session store for user status
        session_id = session_store.get_active_session(user_id) if hasattr(session_store, "get_active_session") else None

        result = {
            "type": "status",
            "platform": platform,
            "session_active": session_id is not None,
            "session_id": session_id,
        }

        await self._event_bus.emit("command_response", {
            "event": event,
            "response": result,
        })

        return result

    async def _handle_new_session(
        self,
        event: Dict[str, Any],
        session_store: Any,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle /new by creating a new session."""
        user_id = event.get("user_id")

        # Emit event for SessionManager to handle
        await self._event_bus.emit("session_create_requested", {
            "event": event,
            "user_id": user_id,
        })

        return {"type": "session_created", "user_id": user_id}

    async def _handle_clear_session(
        self,
        event: Dict[str, Any],
        session_store: Any,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle /clear by clearing the current session."""
        user_id = event.get("user_id")

        await self._event_bus.emit("session_clear_requested", {
            "event": event,
            "user_id": user_id,
        })

        return {"type": "session_cleared", "user_id": user_id}

    async def _handle_queue_status(
        self,
        event: Dict[str, Any],
        session_store: Any,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle /queue by showing pending message status."""
        await self._event_bus.emit("queue_status_requested", {
            "event": event,
        })

        return {"type": "queue_status"}

    async def _handle_stop_session(
        self,
        event: Dict[str, Any],
        session_store: Any,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle /stop by interrupting the warm agent through an event."""
        user_id = event.get("user_id")

        # Emit event for AgentRunner to handle (breaks direct coupling)
        await self._event_bus.emit("stop_agent_requested", {
            "event": event,
            "user_id": user_id,
        })

        return {"type": "stop_requested", "user_id": user_id}

    async def _handle_approve(
        self,
        event: Dict[str, Any],
        session_store: Any,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle /approve by approving a pending tool call."""
        user_id = event.get("user_id")

        await self._event_bus.emit("approval_granted", {
            "event": event,
            "user_id": user_id,
        })

        return {"type": "approved", "user_id": user_id}

    async def _handle_deny(
        self,
        event: Dict[str, Any],
        session_store: Any,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle /deny by denying a pending tool call."""
        user_id = event.get("user_id")

        await self._event_bus.emit("approval_denied", {
            "event": event,
            "user_id": user_id,
        })

        return {"type": "denied", "user_id": user_id}

    # Public API for GatewayRunner to delegate to (composition, not inheritance)

    def cold_command_names(self) -> frozenset[str]:
        """Return set of cold command names for help/documentation."""
        return self._cold_commands.names()

    def is_cold_command(self, text: str) -> bool:
        """Check if text is a cold command (fast path for early return)."""
        return self._cold_commands.get(self._canonicalize(text)) is not None
