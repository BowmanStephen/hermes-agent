"""Platform adapter lifecycle helpers for the gateway."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, MutableMapping
from typing import Any

from gateway.config import Platform

logger = logging.getLogger(__name__)

PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT = 30.0
ADAPTER_DISCONNECT_TIMEOUT_SECS_DEFAULT = 5.0


def adapter_disconnect_timeout_secs(
    *,
    default: float = ADAPTER_DISCONNECT_TIMEOUT_SECS_DEFAULT,
    logger: logging.Logger = logger,
) -> float:
    """Return the per-adapter disconnect timeout used during shutdown."""

    return _timeout_from_env(
        "HERMES_GATEWAY_ADAPTER_DISCONNECT_TIMEOUT",
        default=default,
        logger=logger,
    )


def platform_connect_timeout_secs(
    *,
    default: float = PLATFORM_CONNECT_TIMEOUT_SECS_DEFAULT,
    logger: logging.Logger = logger,
) -> float:
    """Return the per-platform connect timeout used during startup/retry."""

    return _timeout_from_env(
        "HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT",
        default=default,
        logger=logger,
    )


async def safe_adapter_disconnect(
    adapter: Any,
    platform: Platform | None,
    *,
    timeout: float,
    logger: logging.Logger = logger,
) -> None:
    """Call adapter.disconnect() defensively, swallowing any error."""

    platform_name = platform.value if platform is not None else "adapter"
    try:
        if timeout <= 0:
            await adapter.disconnect()
        else:
            await asyncio.wait_for(adapter.disconnect(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "Timed out after %.1fs while disconnecting %s adapter; continuing shutdown",
            timeout,
            platform_name,
        )
    except Exception as exc:
        logger.debug(
            "Defensive %s disconnect after failed connect raised: %s",
            platform_name,
            exc,
        )


async def connect_adapter_with_timeout(
    adapter: Any,
    platform: Platform,
    *,
    timeout: float,
) -> bool:
    """Connect an adapter without allowing one platform to block others."""

    if timeout <= 0:
        return await adapter.connect()
    try:
        return await asyncio.wait_for(adapter.connect(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"{platform.value} connect timed out after {timeout:g}s"
        ) from exc


async def handle_adapter_fatal_error(
    adapter: Any,
    *,
    config: Any,
    adapters: MutableMapping[Platform, Any],
    delivery_router: Any,
    failed_platforms: MutableMapping[Platform, dict[str, Any]],
    update_platform_runtime_status: Callable[..., Any],
    set_exit_state: Callable[[str, bool], Any],
    stop_gateway: Callable[[], Any],
    now: Callable[[], float] = time.monotonic,
    logger: logging.Logger = logger,
) -> None:
    """React to an adapter runtime failure and queue or stop the gateway."""

    logger.error(
        "Fatal %s adapter error (%s): %s",
        adapter.platform.value,
        adapter.fatal_error_code or "unknown",
        adapter.fatal_error_message or "unknown error",
    )
    _update_platform_status(
        update_platform_runtime_status,
        adapter.platform,
        platform_state="retrying" if adapter.fatal_error_retryable else "fatal",
        error_code=adapter.fatal_error_code,
        error_message=adapter.fatal_error_message,
    )

    existing = adapters.get(adapter.platform)
    if existing is adapter:
        try:
            await adapter.disconnect()
        finally:
            adapters.pop(adapter.platform, None)
            delivery_router.adapters = adapters

    if adapter.fatal_error_retryable:
        platform_config = getattr(config, "platforms", {}).get(adapter.platform)
        if platform_config and adapter.platform not in failed_platforms:
            failed_platforms[adapter.platform] = {
                "config": platform_config,
                "attempts": 0,
                "next_retry": now() + 30,
            }
            logger.info(
                "%s queued for background reconnection",
                adapter.platform.value,
            )

    if not adapters and not failed_platforms:
        exit_reason = (
            adapter.fatal_error_message or "All messaging adapters disconnected"
        )
        set_exit_state(exit_reason, bool(adapter.fatal_error_retryable))
        if adapter.fatal_error_retryable:
            logger.error(
                "No connected messaging platforms remain. Shutting down gateway for service restart."
            )
        else:
            logger.error(
                "No connected messaging platforms remain. Shutting down gateway cleanly."
            )
        await stop_gateway()
    elif not adapters and failed_platforms:
        logger.warning(
            "No connected messaging platforms remain, but %d platform(s) "
            "queued for reconnection - gateway staying alive, watcher will "
            "retry in background.",
            len(failed_platforms),
        )


def pause_failed_platform(
    failed_platforms: MutableMapping[Platform, dict[str, Any]],
    platform: Platform,
    *,
    reason: str = "",
    update_platform_runtime_status: Callable[..., Any] | None = None,
    logger: logging.Logger = logger,
) -> None:
    """Mark a queued platform as paused while keeping it in the retry queue."""

    info = failed_platforms.get(platform)
    if info is None:
        return
    if info.get("paused"):
        return
    info["paused"] = True
    info["pause_reason"] = reason or "auto-paused after repeated failures"
    info["next_retry"] = float("inf")
    _update_platform_status(
        update_platform_runtime_status,
        platform,
        platform_state="paused",
        error_code=None,
        error_message=info["pause_reason"],
    )
    logger.warning(
        "%s paused after %d consecutive failures (%s) - "
        "fix the underlying issue then run `/platform resume %s` "
        "to retry, or `hermes gateway restart` to restart the gateway.",
        platform.value,
        info.get("attempts", 0),
        info["pause_reason"],
        platform.value,
    )


def resume_paused_platform(
    failed_platforms: MutableMapping[Platform, dict[str, Any]],
    platform: Platform,
    *,
    now: Callable[[], float] = time.monotonic,
    update_platform_runtime_status: Callable[..., Any] | None = None,
    logger: logging.Logger = logger,
) -> bool:
    """Unpause a queued platform and schedule an immediate retry."""

    info = failed_platforms.get(platform)
    if info is None:
        return False
    if not info.get("paused"):
        return False
    info["paused"] = False
    info.pop("pause_reason", None)
    info["attempts"] = 0
    info["next_retry"] = now()
    _update_platform_status(
        update_platform_runtime_status,
        platform,
        platform_state="retrying",
        error_code=None,
        error_message=None,
    )
    logger.info("%s resumed - retrying on next watcher tick", platform.value)
    return True


def _timeout_from_env(
    env_name: str,
    *,
    default: float,
    logger: logging.Logger,
) -> float:
    raw = os.getenv(env_name, "").strip()
    if raw:
        try:
            timeout = float(raw)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r", env_name, raw)
        else:
            return max(0.0, timeout)
    return default


def _update_platform_status(
    update_platform_runtime_status: Callable[..., Any] | None,
    platform: Platform,
    **kwargs: Any,
) -> None:
    if update_platform_runtime_status is None:
        return
    try:
        update_platform_runtime_status(platform.value, **kwargs)
    except Exception:
        pass
