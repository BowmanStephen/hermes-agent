"""Delivery notification helpers for GatewayRunner."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from gateway.config import GatewayConfig, Platform
from gateway.config_loader import _parse_session_key


logger = logging.getLogger(__name__)

RestartTarget = tuple[str, str, Optional[str]]


async def notify_active_sessions_of_shutdown(
    *,
    active_session_keys: Any,
    adapters: Mapping[Platform, Any],
    config: GatewayConfig,
    restart_requested: bool,
    session_store: Any = None,
    get_cached_session_source: Any = None,
    logger: logging.Logger = logger,
) -> set[RestartTarget]:
    """Send shutdown/restart notifications to active chats and home channels."""

    action = "restarting" if restart_requested else "shutting down"
    hint = (
        "Your current task will be interrupted. "
        "Send any message after restart and I'll try to resume where you left off."
        if restart_requested
        else "Your current task will be interrupted."
    )
    msg = f"⚠️ Gateway {action} — {hint}"

    notified: set[RestartTarget] = set()
    for session_key in active_session_keys:
        source = None
        try:
            if session_store is not None:
                session_store._ensure_loaded()
                entry = session_store._entries.get(session_key)
                source = getattr(entry, "origin", None) if entry else None
        except Exception as e:
            logger.debug(
                "Failed to load session origin for shutdown notification %s: %s",
                session_key,
                e,
            )

        if source is None and get_cached_session_source is not None:
            source = get_cached_session_source(session_key)

        if source is not None:
            platform_str = source.platform.value
            chat_id = str(source.chat_id)
            thread_id = source.thread_id
        else:
            parsed = _parse_session_key(session_key)
            if not parsed:
                continue
            platform_str = parsed["platform"]
            chat_id = parsed["chat_id"]
            thread_id = parsed.get("thread_id")

        dedup_key = (platform_str, chat_id, str(thread_id) if thread_id else None)
        if dedup_key in notified:
            continue

        try:
            platform = Platform(platform_str)
            adapter = adapters.get(platform)
            if not adapter:
                continue

            platform_cfg = config.platforms.get(platform)
            if platform_cfg is not None and not platform_cfg.gateway_restart_notification:
                logger.info(
                    "Shutdown notification suppressed for active session: %s has gateway_restart_notification=false",
                    platform_str,
                )
                continue

            metadata = {"thread_id": thread_id} if thread_id else None

            result = await adapter.send(chat_id, msg, metadata=metadata)
            if result is not None and getattr(result, "success", True) is False:
                logger.debug(
                    "Failed to send shutdown notification to %s:%s: %s",
                    platform_str,
                    chat_id,
                    getattr(result, "error", "send returned success=False"),
                )
                continue

            notified.add(dedup_key)
            logger.info(
                "Sent shutdown notification to active chat %s:%s",
                platform_str,
                chat_id,
            )
        except Exception as e:
            logger.debug(
                "Failed to send shutdown notification to %s:%s: %s",
                platform_str,
                chat_id,
                e,
            )

    for platform, adapter in list(adapters.items()):
        home = config.get_home_channel(platform)
        if not home or not home.chat_id:
            continue

        platform_cfg = config.platforms.get(platform)
        if platform_cfg is not None and not platform_cfg.gateway_restart_notification:
            logger.info(
                "Shutdown notification suppressed for home channel: %s has gateway_restart_notification=false",
                platform.value,
            )
            continue

        dedup_key = (platform.value, str(home.chat_id), str(home.thread_id) if home.thread_id else None)
        if dedup_key in notified:
            continue

        try:
            metadata = {"thread_id": home.thread_id} if home.thread_id else None
            if metadata:
                result = await adapter.send(str(home.chat_id), msg, metadata=metadata)
            else:
                result = await adapter.send(str(home.chat_id), msg)
            if result is not None and getattr(result, "success", True) is False:
                logger.debug(
                    "Failed to send shutdown notification to home channel %s:%s: %s",
                    platform.value,
                    home.chat_id,
                    getattr(result, "error", "send returned success=False"),
                )
                continue

            notified.add(dedup_key)
            logger.info(
                "Sent shutdown notification to home channel %s:%s",
                platform.value,
                home.chat_id,
            )
        except Exception as e:
            logger.debug(
                "Failed to send shutdown notification to home channel %s:%s: %s",
                platform.value,
                home.chat_id,
                e,
            )

    return notified


async def send_update_notification(
    hermes_home: Path,
    *,
    adapters: Mapping[Platform, Any],
    logger: logging.Logger = logger,
) -> bool:
    """Notify the initiating chat when a gateway update has finished."""

    pending_path = hermes_home / ".update_pending.json"
    claimed_path = hermes_home / ".update_pending.claimed.json"
    output_path = hermes_home / ".update_output.txt"
    exit_code_path = hermes_home / ".update_exit_code"

    if not pending_path.exists() and not claimed_path.exists():
        return False

    cleanup = True
    active_pending_path = claimed_path
    try:
        if pending_path.exists():
            try:
                pending_path.replace(claimed_path)
            except FileNotFoundError:
                if not claimed_path.exists():
                    return True
        elif not claimed_path.exists():
            return True

        pending = json.loads(claimed_path.read_text())
        platform_str = pending.get("platform")
        chat_id = pending.get("chat_id")
        thread_id = pending.get("thread_id")

        if not exit_code_path.exists():
            logger.info("Update notification deferred: update still running")
            cleanup = False
            active_pending_path = pending_path
            claimed_path.replace(pending_path)
            return False

        exit_code_raw = exit_code_path.read_text().strip() or "1"
        exit_code = int(exit_code_raw)

        output = ""
        if output_path.exists():
            output = output_path.read_text()

        platform = Platform(platform_str)
        adapter = adapters.get(platform)

        if adapter and chat_id:
            metadata = {"thread_id": thread_id} if thread_id else None
            output = re.sub(r"\x1b\[[0-9;]*m", "", output).strip()
            if output:
                if len(output) > 3500:
                    output = "…" + output[-3500:]
                if exit_code == 0:
                    msg = f"✅ Hermes update finished.\n\n```\n{output}\n```"
                else:
                    msg = f"❌ Hermes update failed.\n\n```\n{output}\n```"
            elif exit_code == 0:
                msg = "✅ Hermes update finished successfully."
            else:
                msg = "❌ Hermes update failed. Check the gateway logs or run `hermes update` manually for details."
            await adapter.send(chat_id, msg, metadata=metadata)
            logger.info(
                "Sent post-update notification to %s:%s (exit=%s)",
                platform_str,
                chat_id,
                exit_code,
            )
    except Exception as e:
        logger.warning("Post-update notification failed: %s", e)
    finally:
        if cleanup:
            active_pending_path.unlink(missing_ok=True)
            claimed_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            exit_code_path.unlink(missing_ok=True)

    return True


async def send_restart_notification(
    hermes_home: Path,
    *,
    adapters: Mapping[Platform, Any],
    config: GatewayConfig,
    logger: logging.Logger = logger,
) -> RestartTarget | None:
    """Notify the chat that initiated /restart that the gateway is back."""

    notify_path = hermes_home / ".restart_notify.json"
    if not notify_path.exists():
        return None

    try:
        data = json.loads(notify_path.read_text())
        platform_str = data.get("platform")
        chat_id = data.get("chat_id")
        thread_id = data.get("thread_id")

        if not platform_str or not chat_id:
            return None

        platform = Platform(platform_str)
        adapter = adapters.get(platform)
        if not adapter:
            logger.debug(
                "Restart notification skipped: %s adapter not connected",
                platform_str,
            )
            return None

        platform_cfg = config.platforms.get(platform)
        if platform_cfg is not None and not platform_cfg.gateway_restart_notification:
            logger.info(
                "Restart notification suppressed: %s has gateway_restart_notification=false",
                platform_str,
            )
            return None

        metadata = {"thread_id": thread_id} if thread_id else None
        result = await adapter.send(
            str(chat_id),
            "♻ Gateway restarted successfully. Your session continues.",
            metadata=metadata,
        )
        if result is not None and getattr(result, "success", True) is False:
            logger.warning(
                "Restart notification to %s:%s was not delivered: %s",
                platform_str,
                chat_id,
                getattr(result, "error", "send returned success=False"),
            )
            return None

        logger.info(
            "Sent restart notification to %s:%s",
            platform_str,
            chat_id,
        )
        return str(platform_str), str(chat_id), str(thread_id) if thread_id else None
    except Exception as e:
        logger.warning("Restart notification failed: %s", e)
        return None
    finally:
        notify_path.unlink(missing_ok=True)


async def send_home_channel_startup_notifications(
    *,
    adapters: Mapping[Platform, Any],
    config: GatewayConfig,
    skip_targets: set[RestartTarget] | None = None,
    logger: logging.Logger = logger,
) -> set[RestartTarget]:
    """Notify configured home channels that the gateway is back online."""

    delivered: set[RestartTarget] = set()
    skipped = skip_targets or set()
    message = "♻️ Gateway online — Hermes is back and ready."

    for platform, adapter in adapters.items():
        home = config.get_home_channel(platform)
        if not home or not home.chat_id:
            continue

        platform_cfg = config.platforms.get(platform)
        if platform_cfg is not None and not platform_cfg.gateway_restart_notification:
            logger.info(
                "Home-channel startup notification suppressed: %s has gateway_restart_notification=false",
                platform.value,
            )
            continue

        target = (platform.value, str(home.chat_id), str(home.thread_id) if home.thread_id else None)
        if target in skipped or target in delivered:
            continue

        try:
            metadata = {"thread_id": home.thread_id} if home.thread_id else None
            if metadata:
                result = await adapter.send(str(home.chat_id), message, metadata=metadata)
            else:
                result = await adapter.send(str(home.chat_id), message)
            if result is not None and getattr(result, "success", True) is False:
                logger.warning(
                    "Home-channel startup notification failed for %s:%s: %s",
                    platform.value,
                    home.chat_id,
                    getattr(result, "error", "send returned success=False"),
                )
                continue

            delivered.add(target)
            logger.info(
                "Sent home-channel startup notification to %s:%s",
                platform.value,
                home.chat_id,
            )
        except Exception as exc:
            logger.warning(
                "Home-channel startup notification failed for %s:%s: %s",
                platform.value,
                home.chat_id,
                exc,
            )

    return delivered
