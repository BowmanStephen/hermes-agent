"""Message routing policies extracted from GatewayRunner.

This module re-exports active-session routing policies and hosts a thin
``MessageRouter`` facade for cold-path orchestration (delegated to
``gateway.cold_command_router``).

Architecture:
- MessageRouter receives platform messages
- Routes to cold commands (slash commands not needing warm agent) or warm agent
- Uses event_bus for loose coupling (spec rule #1)
"""
from __future__ import annotations

from typing import Any, Dict

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
from gateway.cold_command_router import orchestrate_cold_command
from gateway.cold_route_types import (
    ColdRouteContext,
    ColdRouteOutcome,
    ColdRouteResult,
)
from gateway.event_bus import EventBus

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
    "ActiveSessionCommandDecision", "ActiveSessionFollowupDecision", "ColdRouteContext",
    "ColdRouteOutcome", "ColdRouteResult", "MessageRouter", "SlashConfirmRoutingResult",
    "resolve_active_session_command_decision",
    "resolve_active_session_followup_decision", "resolve_unauthorized_dm_behavior",
    "route_pending_slash_confirm_reply", "should_queue_telegram_followup",
]


class MessageRouter:
    """Thin facade: delegates cold-path orchestration to ``orchestrate_cold_command``."""

    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: EventBus,
        session_store: Any,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._session_store = session_store

    async def route_cold_command(self, ctx: ColdRouteContext) -> ColdRouteResult:
        return await orchestrate_cold_command(ctx, event_bus=self._event_bus)
