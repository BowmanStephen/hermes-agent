"""Types for production cold-path command orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional


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
    """Result of cold-path orchestration before warm-agent dispatch."""

    outcome: ColdRouteOutcome
    response: Any = None
