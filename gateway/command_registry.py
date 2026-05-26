"""Gateway cold-path command metadata and explicit handler lookup.

This is a strangler seam for command routing: callers pass a concrete handler
map, and this module validates canonical command names without reaching back
into ``GatewayRunner`` via ``getattr``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional


# Special cold command decisions (unchanged from original)

@dataclass(frozen=True)
class SpecialColdCommandDecision:
    """Decision for special cold-path commands needing custom handling."""

    response: str | None = None
    rewrite_text: str | None = None
    confirm_command: str | None = None
    confirm_title: str | None = None
    confirm_detail: str | None = None


def resolve_special_cold_command(
    canonical: Optional[str],
    *,
    command_args: str,
    telegram_root_lobby: bool,
    telegram_root_new_message: str = "",
) -> SpecialColdCommandDecision | None:
    """Resolve cold-path commands that need special handling before agent dispatch."""

    if canonical == "new":
        if telegram_root_lobby:
            return SpecialColdCommandDecision(response=telegram_root_new_message)
        return SpecialColdCommandDecision(
            confirm_command="new",
            confirm_title="/new",
            confirm_detail=(
                "This starts a fresh session and discards the current "
                "conversation history."
            ),
        )

    if canonical == "undo":
        return SpecialColdCommandDecision(
            confirm_command="undo",
            confirm_title="/undo",
            confirm_detail="This removes the last user/assistant exchange from history.",
        )

    if canonical == "steer":
        steer_payload = command_args.strip()
        if not steer_payload:
            return SpecialColdCommandDecision(
                response=(
                    "Usage: /steer <prompt>  "
                    "(no agent is running; sending as a normal message)"
                )
            )
        return SpecialColdCommandDecision(rewrite_text=steer_payload)

    return None


# Canonical command names routed by the gateway cold path. Values document the
# current GatewayRunner method that still owns the body until the vertical slice
# migrates those handlers into a real router/service.
GATEWAY_HANDLER_METHODS: dict[str, str] = {
    "topic": "_handle_topic_command",
    "help": "_handle_help_command",
    "commands": "_handle_commands_command",
    "profile": "_handle_profile_command",
    "whoami": "_handle_whoami_command",
    "status": "_handle_status_command",
    "agents": "_handle_agents_command",
    "platform": "_handle_platform_command",
    "restart": "_handle_restart_command",
    "stop": "_handle_stop_command",
    "reasoning": "_handle_reasoning_command",
    "fast": "_handle_fast_command",
    "verbose": "_handle_verbose_command",
    "footer": "_handle_footer_command",
    "yolo": "_handle_yolo_command",
    "model": "_handle_model_command",
    "codex-runtime": "_handle_codex_runtime_command",
    "personality": "_handle_personality_command",
    "kanban": "_handle_kanban_command",
    "retry": "_handle_retry_command",
    "sethome": "_handle_set_home_command",
    "compress": "_handle_compress_command",
    "usage": "_handle_usage_command",
    "insights": "_handle_insights_command",
    "reload-mcp": "_handle_reload_mcp_command",
    "reload-skills": "_handle_reload_skills_command",
    "bundles": "_handle_bundles_command",
    "approve": "_handle_approve_command",
    "deny": "_handle_deny_command",
    "update": "_handle_update_command",
    "debug": "_handle_debug_command",
    "title": "_handle_title_command",
    "resume": "_handle_resume_command",
    "branch": "_handle_branch_command",
    "rollback": "_handle_rollback_command",
    "background": "_handle_background_command",
    "goal": "_handle_goal_command",
    "subgoal": "_handle_subgoal_command",
    "voice": "_handle_voice_command",
}


class CommandHandlerRegistry:
    """Small explicit command-handler table for composed gateway routers."""

    def __init__(self, handlers: Mapping[str, Callable[[Any], Any]] | None = None) -> None:
        self._handlers: dict[str, Callable[[Any], Any]] = dict(handlers or {})

    def register(self, canonical: str, handler: Callable[[Any], Any]) -> None:
        self._handlers[canonical] = handler

    def get(self, canonical: Optional[str]) -> Optional[Callable[[Any], Any]]:
        if not canonical:
            return None
        return self._handlers.get(canonical)

    def names(self) -> frozenset[str]:
        return frozenset(self._handlers)


def get_gateway_command_handler(
    handlers: Mapping[str, Any],
    canonical: Optional[str],
) -> Optional[Callable[[Any], Any]]:
    """Return a validated cold-path handler from an explicit handler map."""
    if not canonical or canonical not in GATEWAY_HANDLER_METHODS:
        return None
    handler = handlers.get(canonical)
    if not callable(handler):
        return None
    return handler
