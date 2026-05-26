"""Active-session command and follow-up routing helpers."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import os
from typing import Optional

from gateway.config import Platform
from gateway.platforms.base import MessageType


@dataclass(frozen=True)
class SlashConfirmRoutingResult:
    """Result marker for a handled slash-confirm reply."""

    response: str


@dataclass(frozen=True)
class ActiveSessionCommandDecision:
    """Policy decision for slash commands received during an active agent run."""

    action: str
    response: Optional[str] = None


ACTIVE_SESSION_ACTION_NONE = "none"
ACTIVE_SESSION_ACTION_RESTART = "restart"
ACTIVE_SESSION_ACTION_STOP = "stop"
ACTIVE_SESSION_ACTION_NEW = "new"
ACTIVE_SESSION_ACTION_QUEUE = "queue"
ACTIVE_SESSION_ACTION_STEER = "steer"
ACTIVE_SESSION_ACTION_APPROVE = "approve"
ACTIVE_SESSION_ACTION_DENY = "deny"
ACTIVE_SESSION_ACTION_AGENTS = "agents"
ACTIVE_SESSION_ACTION_BACKGROUND = "background"
ACTIVE_SESSION_ACTION_KANBAN = "kanban"
ACTIVE_SESSION_ACTION_GOAL = "goal"
ACTIVE_SESSION_ACTION_SUBGOAL = "subgoal"
ACTIVE_SESSION_ACTION_YOLO = "yolo"
ACTIVE_SESSION_ACTION_VERBOSE = "verbose"
ACTIVE_SESSION_ACTION_DEDICATED = "dedicated"

ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO = "queue_photo"
ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE = "queue_telegram_grace"
ACTIVE_SESSION_FOLLOWUP_STOP_PENDING = "stop_pending"
ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING = "queue_pending"
ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE = "drain_queue"
ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT = "drain_reject"
ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY = "queue_busy"
ACTIVE_SESSION_FOLLOWUP_STEER_BUSY = "steer_busy"
ACTIVE_SESSION_FOLLOWUP_INTERRUPT = "interrupt"


@dataclass(frozen=True)
class ActiveSessionFollowupDecision:
    """Policy decision for non-command follow-ups during an active agent run."""

    action: str


def _slash_confirm_choice(raw_reply: str, command: Optional[str]) -> Optional[str]:
    if command in {"approve", "yes", "ok", "confirm"}:
        return "once"
    if command in {"always", "remember"}:
        return "always"
    if command in {"cancel", "no", "deny", "nevermind"}:
        return "cancel"

    normalized = raw_reply.lower()
    if normalized in {"approve", "approve once", "once"}:
        return "once"
    if normalized in {"always", "always approve"}:
        return "always"
    if normalized in {"cancel", "nevermind", "no"}:
        return "cancel"
    return None


async def route_pending_slash_confirm_reply(
    *,
    session_key: str,
    raw_reply: str,
    command: Optional[str],
) -> Optional[SlashConfirmRoutingResult]:
    """Resolve pending slash-confirm replies before normal command dispatch."""

    from tools import slash_confirm

    pending_confirm = slash_confirm.get_pending(session_key)
    if not pending_confirm:
        return None

    try:
        from tools.approval import has_blocking_approval

        tool_approval_live = has_blocking_approval(session_key)
    except Exception:
        tool_approval_live = False
    if tool_approval_live:
        return None

    choice = _slash_confirm_choice(raw_reply.strip(), command)
    if choice is not None:
        resolved = await slash_confirm.resolve(
            session_key,
            pending_confirm.get("confirm_id"),
            choice,
        )
        return SlashConfirmRoutingResult(response=resolved or "")

    slash_confirm.clear_if_stale(session_key)
    return None


def resolve_unauthorized_dm_behavior(
    *,
    config: object,
    platform: Optional[Platform],
) -> str:
    """Return how unauthorized direct messages should be handled."""

    if config and hasattr(config, "get_unauthorized_dm_behavior") and platform:
        platform_cfg = config.platforms.get(platform) if hasattr(config, "platforms") else None
        if platform_cfg and "unauthorized_dm_behavior" in getattr(platform_cfg, "extra", {}):
            return config.get_unauthorized_dm_behavior(platform)

    if config and hasattr(config, "unauthorized_dm_behavior"):
        if config.unauthorized_dm_behavior != "pair":
            return config.unauthorized_dm_behavior

    if platform:
        behavior = _allowlist_authorized_dm_behavior(platform)
        if behavior:
            return behavior

    if os.getenv("GATEWAY_ALLOWED_USERS", "").strip():
        return "ignore"
    return "pair"


def _allowlist_authorized_dm_behavior(platform: Platform) -> Optional[str]:
    platform_env_map = {
        Platform.TELEGRAM: "TELEGRAM_ALLOWED_USERS",
        Platform.DISCORD: "DISCORD_ALLOWED_USERS",
        Platform.WHATSAPP: "WHATSAPP_ALLOWED_USERS",
        Platform.SLACK: "SLACK_ALLOWED_USERS",
        Platform.SIGNAL: "SIGNAL_ALLOWED_USERS",
        Platform.EMAIL: "EMAIL_ALLOWED_USERS",
        Platform.SMS: "SMS_ALLOWED_USERS",
        Platform.MATTERMOST: "MATTERMOST_ALLOWED_USERS",
        Platform.MATRIX: "MATRIX_ALLOWED_USERS",
        Platform.DINGTALK: "DINGTALK_ALLOWED_USERS",
        Platform.FEISHU: "FEISHU_ALLOWED_USERS",
        Platform.WECOM: "WECOM_ALLOWED_USERS",
        Platform.WECOM_CALLBACK: "WECOM_CALLBACK_ALLOWED_USERS",
        Platform.WEIXIN: "WEIXIN_ALLOWED_USERS",
        Platform.BLUEBUBBLES: "BLUEBUBBLES_ALLOWED_USERS",
        Platform.QQBOT: "QQ_ALLOWED_USERS",
    }
    if os.getenv(platform_env_map.get(platform, ""), "").strip():
        return "ignore"
    return _group_allowlist_authorized_dm_behavior(platform)


def _group_allowlist_authorized_dm_behavior(platform: Platform) -> Optional[str]:
    platform_group_env_map = {
        Platform.TELEGRAM: (
            "TELEGRAM_GROUP_ALLOWED_USERS",
            "TELEGRAM_GROUP_ALLOWED_CHATS",
        ),
        Platform.QQBOT: ("QQ_GROUP_ALLOWED_USERS",),
    }
    for env_key in platform_group_env_map.get(platform, ()):
        if os.getenv(env_key, "").strip():
            return "ignore"
    return None


def resolve_active_session_command_decision(
    *,
    command_name: Optional[str],
    command_args: str,
    dedicated_handlers: Collection[str],
) -> ActiveSessionCommandDecision:
    """Classify a slash command received while an agent is already running."""

    if not command_name:
        return ActiveSessionCommandDecision(ACTIVE_SESSION_ACTION_NONE)

    action = _direct_active_session_command_action(command_name)
    if action:
        return ActiveSessionCommandDecision(action)

    return _dedicated_active_session_command_decision(
        command_name=command_name,
        command_args=command_args,
        dedicated_handlers=dedicated_handlers,
    )


def _direct_active_session_command_action(command_name: str) -> Optional[str]:
    return {
        "restart": ACTIVE_SESSION_ACTION_RESTART,
        "stop": ACTIVE_SESSION_ACTION_STOP,
        "new": ACTIVE_SESSION_ACTION_NEW,
        "queue": ACTIVE_SESSION_ACTION_QUEUE,
        "steer": ACTIVE_SESSION_ACTION_STEER,
        "approve": ACTIVE_SESSION_ACTION_APPROVE,
        "deny": ACTIVE_SESSION_ACTION_DENY,
        "agents": ACTIVE_SESSION_ACTION_AGENTS,
        "background": ACTIVE_SESSION_ACTION_BACKGROUND,
        "kanban": ACTIVE_SESSION_ACTION_KANBAN,
        "subgoal": ACTIVE_SESSION_ACTION_SUBGOAL,
        "yolo": ACTIVE_SESSION_ACTION_YOLO,
        "verbose": ACTIVE_SESSION_ACTION_VERBOSE,
    }.get(command_name)


def _dedicated_active_session_command_decision(
    *,
    command_name: str,
    command_args: str,
    dedicated_handlers: Collection[str],
) -> ActiveSessionCommandDecision:
    if command_name == "model":
        return ActiveSessionCommandDecision(
            ACTIVE_SESSION_ACTION_DEDICATED,
            "Agent is running — wait or /stop first, then switch models.",
        )
    if command_name == "codex-runtime":
        return ActiveSessionCommandDecision(
            ACTIVE_SESSION_ACTION_DEDICATED,
            "Agent is running — wait or /stop first, then change runtime.",
        )
    if command_name == "goal":
        return _active_session_goal_decision(command_args)
    if command_name in dedicated_handlers:
        return ActiveSessionCommandDecision(ACTIVE_SESSION_ACTION_DEDICATED)
    return ActiveSessionCommandDecision(
        ACTIVE_SESSION_ACTION_DEDICATED,
        (
            f"⏳ Agent is running — `/{command_name}` can't run "
            "mid-turn. Wait for the current response or `/stop` first."
        ),
    )


def _active_session_goal_decision(command_args: str) -> ActiveSessionCommandDecision:
    goal_arg = (command_args or "").strip().lower()
    if not goal_arg or goal_arg in {"status", "pause", "resume", "clear", "stop", "done"}:
        return ActiveSessionCommandDecision(ACTIVE_SESSION_ACTION_GOAL)
    return ActiveSessionCommandDecision(
        ACTIVE_SESSION_ACTION_DEDICATED,
        "Agent is running — use /goal status / pause / clear mid-run, or /stop before setting a new goal.",
    )


def should_queue_telegram_followup(
    *,
    platform: Optional[Platform],
    message_type: MessageType,
    started_at: float,
    now: float,
    grace_seconds: float,
) -> bool:
    """Return True when a fresh Telegram text follow-up should be queued."""

    return (
        platform == Platform.TELEGRAM
        and message_type == MessageType.TEXT
        and grace_seconds > 0
        and bool(started_at)
        and (now - started_at) <= grace_seconds
    )


def resolve_active_session_followup_decision(
    *,
    platform: Optional[Platform],
    message_type: MessageType,
    command: Optional[str],
    started_at: float,
    now: float,
    telegram_followup_grace_seconds: float,
    running_agent_is_pending: bool,
    draining: bool,
    queue_during_drain: bool,
    busy_input_mode: str,
) -> ActiveSessionFollowupDecision:
    """Classify plain/media follow-ups once active-session commands are handled."""

    if message_type == MessageType.PHOTO:
        return ActiveSessionFollowupDecision(ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO)
    if should_queue_telegram_followup(
        platform=platform,
        message_type=message_type,
        started_at=started_at,
        now=now,
        grace_seconds=telegram_followup_grace_seconds,
    ):
        return ActiveSessionFollowupDecision(ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE)
    if running_agent_is_pending:
        action = ACTIVE_SESSION_FOLLOWUP_STOP_PENDING if command == "stop" else ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING
        return ActiveSessionFollowupDecision(action)
    if draining:
        action = ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE if queue_during_drain else ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT
        return ActiveSessionFollowupDecision(action)
    if busy_input_mode == "queue":
        return ActiveSessionFollowupDecision(ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY)
    if busy_input_mode == "steer":
        return ActiveSessionFollowupDecision(ACTIVE_SESSION_FOLLOWUP_STEER_BUSY)
    return ActiveSessionFollowupDecision(ACTIVE_SESSION_FOLLOWUP_INTERRUPT)
