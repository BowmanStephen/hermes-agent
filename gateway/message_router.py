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
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Coroutine, Dict, Mapping, Optional, Protocol

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
    "ActiveSessionCommandDecision", "ActiveSessionFollowupDecision", "ColdCommandTable", "ColdRouteContext",
    "ColdRouteResult", "CommandHandler", "MessageRouter", "SlashConfirmRoutingResult",
    "resolve_active_session_command_decision",
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


class ColdRouteOutcome(str, Enum):
    """How ``GatewayRunner`` should continue after cold-path routing."""

    RETURN = "return"
    WARM_AGENT = "warm_agent"


@dataclass
class ColdRouteContext:
    """Explicit dependencies for production cold command orchestration."""

    event: Any
    source: Any
    task_id: str
    config: Any
    hooks_emit_collect: Callable[..., Awaitable[list[Any]]]
    gateway_handlers: Mapping[str, Callable[..., Awaitable[Any]]]
    check_slash_access: Callable[[Any, str], Optional[str]]
    is_telegram_topic_root_lobby: Callable[[Any], bool]
    telegram_topic_root_new_message: Callable[[], str]
    should_send_telegram_lobby_reminder: Callable[[Any], bool]
    telegram_topic_root_lobby_message: Callable[[], str]
    status_action_gerund: Callable[[], str]
    maybe_confirm_destructive_slash: Callable[..., Awaitable[Any]]
    handle_reset_command: Callable[[Any], Awaitable[Any]]
    handle_undo_command: Callable[[Any], Awaitable[Any]]
    unavailable_skill_checker: Callable[[str], Optional[str]]
    draining: bool = False


@dataclass(frozen=True)
class ColdRouteResult:
    """Result of ``route_cold_command`` before warm-agent dispatch."""

    outcome: ColdRouteOutcome
    response: Any = None


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

    async def route_cold_command(self, ctx: ColdRouteContext) -> ColdRouteResult:
        """Orchestrate gateway cold-path slash command dispatch.

        Resolves aliases, access control, hooks, built-in handlers, quick commands,
        plugins, skills, and bundles. Returns a terminal response or signals that
        the caller should continue to the warm agent path (possibly with mutated
        ``ctx.event.text``).
        """
        from hermes_cli.commands import (
            CommandSurface,
            is_gateway_known_command,
            resolve_command_invocation,
            resolve_plugin_command_dispatch,
        )
        from gateway.cold_command_router import (
            COMMAND_HOOK_DENY,
            COMMAND_HOOK_HANDLED,
            COMMAND_HOOK_REWRITE,
            build_bundle_invocation,
            build_skill_invocation_decision,
            execute_plugin_command,
            execute_quick_command,
            resolve_builtin_precedence_quick_alias,
            resolve_cold_command_dispatch,
            resolve_command_hook_decision,
            unavailable_gateway_command_response,
        )
        from gateway.command_registry import (
            get_gateway_command_handler,
            resolve_special_cold_command,
        )

        event = ctx.event
        source = ctx.source
        command = event.get_command()

        _cmd_invocation = (
            resolve_command_invocation(
                name=command,
                args=event.get_command_args().strip(),
                surface=CommandSurface.GATEWAY,
            )
            if command
            else None
        )
        canonical = _cmd_invocation.canonical_name if _cmd_invocation else command

        _alias_rewrite = resolve_builtin_precedence_quick_alias(
            config=ctx.config,
            command=command,
            command_args=event.get_command_args().strip(),
        )
        if _alias_rewrite is not None:
            event.text = _alias_rewrite.text
            command = _alias_rewrite.command
            _cmd_invocation = (
                resolve_command_invocation(
                    name=command,
                    args=event.get_command_args().strip(),
                    surface=CommandSurface.GATEWAY,
                )
                if command
                else None
            )
            canonical = _cmd_invocation.canonical_name if _cmd_invocation else command

        if command:
            _hook_dispatch = resolve_plugin_command_dispatch(
                name=command,
                args=event.get_command_args().strip(),
                surface=CommandSurface.GATEWAY,
            )
            if _hook_dispatch.route == "plugin":
                canonical = _hook_dispatch.handler_key

        if command and canonical and is_gateway_known_command(canonical):
            _denied = ctx.check_slash_access(source, canonical)
            if _denied is not None:
                return ColdRouteResult(ColdRouteOutcome.RETURN, _denied)

        if command and is_gateway_known_command(canonical):
            raw_args = event.get_command_args().strip()
            hook_ctx = {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "command": canonical,
                "raw_command": command,
                "args": raw_args,
                "raw_args": raw_args,
            }
            try:
                hook_results = await ctx.hooks_emit_collect(
                    f"command:{canonical}", hook_ctx
                )
            except Exception as _hook_err:
                logger.debug(
                    "command:%s hook dispatch failed (non-fatal): %s",
                    canonical,
                    _hook_err,
                )
                hook_results = []

            hook_decision = resolve_command_hook_decision(
                command=command,
                hook_results=hook_results,
            )
            if hook_decision.action == COMMAND_HOOK_DENY:
                return ColdRouteResult(ColdRouteOutcome.RETURN, hook_decision.response)
            if hook_decision.action == COMMAND_HOOK_HANDLED:
                return ColdRouteResult(ColdRouteOutcome.RETURN, hook_decision.response)
            if hook_decision.action == COMMAND_HOOK_REWRITE:
                event.text = f"/{hook_decision.command_name} {hook_decision.raw_args}".strip()
                command = event.get_command()
                if command:
                    _cmd_invocation = resolve_command_invocation(
                        name=command,
                        args=hook_decision.raw_args,
                        surface=CommandSurface.GATEWAY,
                    )
                else:
                    _cmd_invocation = None
                canonical = (
                    _cmd_invocation.canonical_name
                    if _cmd_invocation
                    else command
                )
                if command:
                    _hook_dispatch = resolve_plugin_command_dispatch(
                        name=command,
                        args=hook_decision.raw_args,
                        surface=CommandSurface.GATEWAY,
                    )
                    if _hook_dispatch.route == "plugin":
                        canonical = _hook_dispatch.handler_key

        _special_telegram_root_lobby = (
            ctx.is_telegram_topic_root_lobby(source) if canonical == "new" else False
        )
        special_command_decision = resolve_special_cold_command(
            canonical,
            command_args=event.get_command_args().strip(),
            telegram_root_lobby=_special_telegram_root_lobby,
            telegram_root_new_message=(
                ctx.telegram_topic_root_new_message()
                if _special_telegram_root_lobby
                else ""
            ),
        )
        if special_command_decision is not None:
            if special_command_decision.response is not None:
                return ColdRouteResult(
                    ColdRouteOutcome.RETURN,
                    special_command_decision.response,
                )
            if special_command_decision.rewrite_text is not None:
                try:
                    event.text = special_command_decision.rewrite_text
                except Exception:
                    pass
            if special_command_decision.confirm_command is not None:

                async def _do_special_command():
                    if special_command_decision.confirm_command == "new":
                        return await ctx.handle_reset_command(event)
                    if special_command_decision.confirm_command == "undo":
                        return await ctx.handle_undo_command(event)
                    return None

                confirm_result = await ctx.maybe_confirm_destructive_slash(
                    event=event,
                    command=special_command_decision.confirm_command,
                    title=special_command_decision.confirm_title or "",
                    detail=special_command_decision.confirm_detail or "",
                    execute=_do_special_command,
                )
                return ColdRouteResult(ColdRouteOutcome.RETURN, confirm_result)

        await self._event_bus.emit(
            "gateway.cold_command.resolved",
            {
                "command": command,
                "canonical": canonical,
                "task_id": ctx.task_id,
                "platform": source.platform.value if source.platform else "",
            },
        )

        gateway_handler = get_gateway_command_handler(ctx.gateway_handlers, canonical)
        if gateway_handler is not None:
            handler_result = await gateway_handler(event)
            return ColdRouteResult(ColdRouteOutcome.RETURN, handler_result)

        if ctx.draining:
            draining_msg = (
                f"⏳ Gateway is {ctx.status_action_gerund()} "
                "and is not accepting new work right now."
            )
            return ColdRouteResult(ColdRouteOutcome.RETURN, draining_msg)

        _cold_dispatch = resolve_cold_command_dispatch(
            config=ctx.config,
            command=command,
            command_args=event.get_command_args().strip(),
        )
        quick_commands = _cold_dispatch.quick_commands if _cold_dispatch else {}
        skill_cmds = _cold_dispatch.skill_commands if _cold_dispatch else {}
        command_dispatch = _cold_dispatch.command_dispatch if _cold_dispatch else None

        if command:
            if command_dispatch and command_dispatch.route == "quick_exec":
                qcmd = quick_commands.get(command_dispatch.handler_key, {})
                quick_result = await execute_quick_command(
                    command_name=command,
                    exec_cmd=qcmd.get("command", ""),
                    env=os.environ.copy(),
                )
                return ColdRouteResult(ColdRouteOutcome.RETURN, quick_result)
            if command_dispatch and command_dispatch.route == "quick_alias":
                target = (command_dispatch.target or "").strip()
                if target:
                    target = target if target.startswith("/") else f"/{target}"
                    target_command = target.lstrip("/")
                    user_args = event.get_command_args().strip()
                    event.text = f"{target} {user_args}".strip()
                    command = (
                        target_command.split()[0]
                        if target_command
                        else target_command
                    )
                    _cold_dispatch = resolve_cold_command_dispatch(
                        config={},
                        command=command,
                        command_args=user_args,
                        skill_commands_provider=lambda: skill_cmds,
                    )
                    command_dispatch = (
                        _cold_dispatch.command_dispatch
                        if _cold_dispatch
                        else None
                    )
                else:
                    return ColdRouteResult(
                        ColdRouteOutcome.RETURN,
                        f"Quick command '/{command}' has no target defined.",
                    )
            elif command_dispatch and command_dispatch.route == "quick_unsupported":
                return ColdRouteResult(
                    ColdRouteOutcome.RETURN,
                    (
                        f"Quick command '/{command}' has unsupported type "
                        "(supported: 'exec', 'alias')."
                    ),
                )
            elif command_dispatch and command_dispatch.route == "unavailable":
                return ColdRouteResult(
                    ColdRouteOutcome.RETURN,
                    unavailable_gateway_command_response(
                        command_dispatch.invocation.canonical_name
                    ),
                )

        if command and command_dispatch and command_dispatch.route == "plugin":
            try:
                plugin_result = await execute_plugin_command(
                    handler_key=command_dispatch.handler_key,
                    raw_args=event.get_command_args().strip(),
                )
                return ColdRouteResult(ColdRouteOutcome.RETURN, plugin_result)
            except Exception as exc:
                logger.debug("Plugin command dispatch failed (non-fatal): %s", exc)

        _bundle_handled = False
        if command and command_dispatch and command_dispatch.route == "skill_bundle":
            try:
                bundle_result = build_bundle_invocation(
                    bundle_key=command_dispatch.handler_slash_key,
                    user_instruction=event.get_command_args().strip(),
                    task_id=ctx.task_id,
                )
                if bundle_result:
                    event.text = bundle_result.message
                    _bundle_handled = True
                    if bundle_result.missing:
                        logger.info(
                            "Bundle %s skipped missing skills: %s",
                            command_dispatch.handler_slash_key,
                            ", ".join(bundle_result.missing),
                        )
            except Exception as exc:
                logger.debug("Bundle dispatch failed (non-fatal): %s", exc)

        if (
            command
            and command_dispatch
            and command_dispatch.route in {"skill", "unknown"}
            and not _bundle_handled
        ):
            try:
                skill_decision = build_skill_invocation_decision(
                    command_dispatch=command_dispatch,
                    command=command,
                    skill_commands=skill_cmds,
                    platform_value=source.platform.value if source.platform else None,
                    user_instruction=event.get_command_args().strip(),
                    task_id=ctx.task_id,
                    unavailable_skill_checker=ctx.unavailable_skill_checker,
                    known_command_checker=is_gateway_known_command,
                )
                if skill_decision.response is not None:
                    if skill_decision.response.startswith("Unknown command"):
                        logger.warning(
                            "Unrecognized slash command /%s from %s — "
                            "replying with unknown-command notice",
                            command,
                            source.platform.value if source.platform else "?",
                        )
                    return ColdRouteResult(
                        ColdRouteOutcome.RETURN,
                        skill_decision.response,
                    )
                if skill_decision.message:
                    event.text = skill_decision.message
            except Exception as exc:
                logger.debug("Skill command check failed (non-fatal): %s", exc)

        if ctx.is_telegram_topic_root_lobby(source):
            if ctx.should_send_telegram_lobby_reminder(source):
                return ColdRouteResult(
                    ColdRouteOutcome.RETURN,
                    ctx.telegram_topic_root_lobby_message(),
                )
            return ColdRouteResult(ColdRouteOutcome.RETURN, None)

        return ColdRouteResult(ColdRouteOutcome.WARM_AGENT)
