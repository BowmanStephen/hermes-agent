"""Source authorization helpers for gateway inbound messages."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.whatsapp_identity import (
    expand_whatsapp_aliases,
    normalize_whatsapp_identifier,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceAuthorizationDecision:
    """Result of checking whether an inbound source may use the gateway."""

    authorized: bool
    warned_telegram_group_users_legacy: bool = False


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in {"true", "1", "yes"}


def _csv_values(raw: str) -> set[str]:
    return {value.strip() for value in raw.split(",") if value.strip()}


def _auth_env_maps() -> tuple[dict[Platform, str], dict[Platform, str], dict[Platform, str], dict[Platform, str], dict[Platform, str]]:
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
        Platform.YUANBAO: "YUANBAO_ALLOWED_USERS",
    }
    platform_group_user_env_map = {
        Platform.TELEGRAM: "TELEGRAM_GROUP_ALLOWED_USERS",
    }
    platform_group_chat_env_map = {
        Platform.TELEGRAM: "TELEGRAM_GROUP_ALLOWED_CHATS",
        Platform.QQBOT: "QQ_GROUP_ALLOWED_USERS",
    }
    platform_allow_all_map = {
        Platform.TELEGRAM: "TELEGRAM_ALLOW_ALL_USERS",
        Platform.DISCORD: "DISCORD_ALLOW_ALL_USERS",
        Platform.WHATSAPP: "WHATSAPP_ALLOW_ALL_USERS",
        Platform.SLACK: "SLACK_ALLOW_ALL_USERS",
        Platform.SIGNAL: "SIGNAL_ALLOW_ALL_USERS",
        Platform.EMAIL: "EMAIL_ALLOW_ALL_USERS",
        Platform.SMS: "SMS_ALLOW_ALL_USERS",
        Platform.MATTERMOST: "MATTERMOST_ALLOW_ALL_USERS",
        Platform.MATRIX: "MATRIX_ALLOW_ALL_USERS",
        Platform.DINGTALK: "DINGTALK_ALLOW_ALL_USERS",
        Platform.FEISHU: "FEISHU_ALLOW_ALL_USERS",
        Platform.WECOM: "WECOM_ALLOW_ALL_USERS",
        Platform.WECOM_CALLBACK: "WECOM_CALLBACK_ALLOW_ALL_USERS",
        Platform.WEIXIN: "WEIXIN_ALLOW_ALL_USERS",
        Platform.BLUEBUBBLES: "BLUEBUBBLES_ALLOW_ALL_USERS",
        Platform.QQBOT: "QQ_ALLOW_ALL_USERS",
        Platform.YUANBAO: "YUANBAO_ALLOW_ALL_USERS",
    }
    platform_allow_bots_map = {
        Platform.DISCORD: "DISCORD_ALLOW_BOTS",
        Platform.FEISHU: "FEISHU_ALLOW_BOTS",
    }
    return (
        platform_env_map,
        platform_group_user_env_map,
        platform_group_chat_env_map,
        platform_allow_all_map,
        platform_allow_bots_map,
    )


def _add_plugin_auth_envs(
    source: SessionSource,
    platform_env_map: dict[Platform, str],
    platform_allow_all_map: dict[Platform, str],
) -> None:
    if source.platform in platform_env_map:
        return
    try:
        from gateway.platform_registry import platform_registry

        entry = platform_registry.get(source.platform.value)
        if entry is None:
            try:
                from hermes_cli.plugins import discover_plugins

                discover_plugins()
                entry = platform_registry.get(source.platform.value)
            except Exception:
                entry = None
        if entry:
            if entry.allowed_users_env:
                platform_env_map[source.platform] = entry.allowed_users_env
            if entry.allow_all_env:
                platform_allow_all_map[source.platform] = entry.allow_all_env
    except Exception:
        pass


def resolve_source_authorization(
    source: SessionSource,
    *,
    pairing_store: Any,
    warned_telegram_group_users_legacy: bool = False,
    logger_: logging.Logger | None = None,
) -> SourceAuthorizationDecision:
    """Check whether a gateway source is authorized."""

    active_logger = logger_ or logger

    if source.platform in {Platform.HOMEASSISTANT, Platform.WEBHOOK}:
        return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    user_id = source.user_id

    if source.chat_type in {"group", "forum", "channel"} and source.chat_id:
        chat_allowlist_env = {
            Platform.TELEGRAM: "TELEGRAM_GROUP_ALLOWED_CHATS",
            Platform.QQBOT: "QQ_GROUP_ALLOWED_USERS",
        }.get(source.platform, "")
        if chat_allowlist_env:
            allowed_group_ids = _csv_values(os.getenv(chat_allowlist_env, "").strip())
            if "*" in allowed_group_ids or source.chat_id in allowed_group_ids:
                return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    if not user_id:
        return SourceAuthorizationDecision(False, warned_telegram_group_users_legacy)

    (
        platform_env_map,
        platform_group_user_env_map,
        platform_group_chat_env_map,
        platform_allow_all_map,
        platform_allow_bots_map,
    ) = _auth_env_maps()
    _add_plugin_auth_envs(source, platform_env_map, platform_allow_all_map)

    platform_allow_all_var = platform_allow_all_map.get(source.platform, "")
    if platform_allow_all_var and _env_truthy(platform_allow_all_var):
        return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    if getattr(source, "is_bot", False):
        allow_bots_var = platform_allow_bots_map.get(source.platform)
        if allow_bots_var and os.getenv(allow_bots_var, "none").lower().strip() in {"mentions", "all"}:
            return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    if source.platform == Platform.DISCORD and os.getenv("DISCORD_ALLOWED_ROLES", "").strip():
        return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    platform_name = source.platform.value if source.platform else ""
    if pairing_store is not None and pairing_store.is_approved(platform_name, user_id):
        return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    platform_allowlist = os.getenv(platform_env_map.get(source.platform, ""), "").strip()
    group_user_allowlist = ""
    group_chat_allowlist = ""
    if source.chat_type in {"group", "forum"}:
        group_user_allowlist = os.getenv(platform_group_user_env_map.get(source.platform, ""), "").strip()
        group_chat_allowlist = os.getenv(platform_group_chat_env_map.get(source.platform, ""), "").strip()
    global_allowlist = os.getenv("GATEWAY_ALLOWED_USERS", "").strip()

    if not platform_allowlist and not group_user_allowlist and not group_chat_allowlist and not global_allowlist:
        return SourceAuthorizationDecision(
            _env_truthy("GATEWAY_ALLOW_ALL_USERS"),
            warned_telegram_group_users_legacy,
        )

    if group_chat_allowlist and source.chat_type in {"group", "forum"} and source.chat_id:
        allowed_group_ids = _csv_values(group_chat_allowlist)
        if "*" in allowed_group_ids or source.chat_id in allowed_group_ids:
            return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    if (
        source.platform == Platform.TELEGRAM
        and group_user_allowlist
        and source.chat_type in {"group", "forum"}
        and source.chat_id
    ):
        legacy_chat_ids = {
            value
            for value in _csv_values(group_user_allowlist)
            if value.startswith("-")
        }
        if legacy_chat_ids:
            if not warned_telegram_group_users_legacy:
                active_logger.warning(
                    "TELEGRAM_GROUP_ALLOWED_USERS contains chat-ID-shaped values "
                    "(%s). Treating them as chat IDs for backward compatibility. "
                    "Move chat IDs to TELEGRAM_GROUP_ALLOWED_CHATS; the _USERS var "
                    "is now for sender user IDs.",
                    ",".join(sorted(legacy_chat_ids)),
                )
                warned_telegram_group_users_legacy = True
            if source.chat_id in legacy_chat_ids:
                return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    allowed_ids = set()
    if platform_allowlist:
        allowed_ids.update(_csv_values(platform_allowlist))
    if group_user_allowlist:
        allowed_ids.update(_csv_values(group_user_allowlist))
    if global_allowlist:
        allowed_ids.update(_csv_values(global_allowlist))

    if "*" in allowed_ids:
        return SourceAuthorizationDecision(True, warned_telegram_group_users_legacy)

    check_ids = {user_id}
    if "@" in user_id:
        check_ids.add(user_id.split("@")[0])

    if source.platform == Platform.WHATSAPP:
        normalized_allowed_ids = set()
        for allowed_id in allowed_ids:
            normalized_allowed_ids.update(expand_whatsapp_aliases(allowed_id))
        if normalized_allowed_ids:
            allowed_ids = normalized_allowed_ids

        check_ids.update(expand_whatsapp_aliases(user_id))
        normalized_user_id = normalize_whatsapp_identifier(user_id)
        if normalized_user_id:
            check_ids.add(normalized_user_id)

    return SourceAuthorizationDecision(
        bool(check_ids & allowed_ids),
        warned_telegram_group_users_legacy,
    )
