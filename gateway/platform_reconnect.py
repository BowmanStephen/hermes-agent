"""Reconnect-attempt helpers for gateway platform adapters."""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable, MutableMapping
from typing import Any

from gateway.config import Platform
from gateway.platform_manager import pause_failed_platform

logger = logging.getLogger(__name__)


async def run_platform_reconnect_watcher(
    *,
    is_running: Callable[[], bool],
    failed_platforms: MutableMapping[Platform, dict[str, Any]],
    retry_platform: Callable[[Platform], Any],
    sleep: Callable[[float], Any],
    initial_delay: float = 10,
    idle_sleep_ticks: int = 30,
    active_sleep_ticks: int = 10,
) -> None:
    """Run the reconnect watcher loop around per-platform retry attempts."""

    await _maybe_await(sleep(initial_delay))
    while is_running():
        if not failed_platforms:
            for _ in range(idle_sleep_ticks):
                if not is_running():
                    return
                await _maybe_await(sleep(1))
            continue

        for platform in list(failed_platforms.keys()):
            if not is_running():
                return
            await _maybe_await(retry_platform(platform))

        for _ in range(active_sleep_ticks):
            if not is_running():
                return
            await _maybe_await(sleep(1))


async def retry_failed_platform(
    platform: Platform,
    *,
    failed_platforms: MutableMapping[Platform, dict[str, Any]],
    adapters: MutableMapping[Platform, Any],
    delivery_router: Any,
    create_adapter: Callable[[Platform, Any], Any],
    connect_adapter: Callable[[Any, Platform], Any],
    handle_message: Callable[..., Any],
    handle_adapter_fatal_error: Callable[..., Any],
    session_store: Any,
    handle_active_session_busy_message: Callable[..., Any],
    sync_voice_mode_state_to_adapter: Callable[[Any], Any],
    update_platform_runtime_status: Callable[..., Any],
    rebuild_channel_directory: Callable[[MutableMapping[Platform, Any]], Any] | None = None,
    now: Callable[[], float] = time.monotonic,
    backoff_cap: int = 300,
    pause_after_failures: int = 10,
    logger: logging.Logger = logger,
) -> bool:
    """Retry one failed platform if it is due for reconnect."""

    info = failed_platforms.get(platform)
    if info is None:
        return False
    if info.get("paused"):
        return False

    current_time = now()
    if current_time < info["next_retry"]:
        return False

    platform_config = info["config"]
    attempt = info["attempts"] + 1
    logger.info("Reconnecting %s (attempt %d)...", platform.value, attempt)

    try:
        adapter = create_adapter(platform, platform_config)
        if not adapter:
            logger.warning(
                "Reconnect %s: adapter creation returned None, removing from retry queue",
                platform.value,
            )
            failed_platforms.pop(platform, None)
            return True

        adapter.set_message_handler(handle_message)
        adapter.set_fatal_error_handler(handle_adapter_fatal_error)
        adapter.set_session_store(session_store)
        adapter.set_busy_session_handler(handle_active_session_busy_message)

        success = await _maybe_await(connect_adapter(adapter, platform))
        if success:
            adapters[platform] = adapter
            sync_voice_mode_state_to_adapter(adapter)
            delivery_router.adapters = adapters
            failed_platforms.pop(platform, None)
            _update_platform_status(
                update_platform_runtime_status,
                platform,
                platform_state="connected",
                error_code=None,
                error_message=None,
            )
            logger.info("✓ %s reconnected successfully", platform.value)
            if rebuild_channel_directory is not None:
                try:
                    await _maybe_await(rebuild_channel_directory(adapters))
                except Exception:
                    pass
        elif getattr(adapter, "has_fatal_error", False) and not getattr(
            adapter, "fatal_error_retryable", False
        ):
            _update_platform_status(
                update_platform_runtime_status,
                platform,
                platform_state="fatal",
                error_code=adapter.fatal_error_code,
                error_message=adapter.fatal_error_message,
            )
            logger.warning(
                "Reconnect %s: non-retryable error (%s), removing from retry queue",
                platform.value,
                adapter.fatal_error_message,
            )
            failed_platforms.pop(platform, None)
        else:
            _record_retry_failure(
                failed_platforms,
                platform,
                info,
                attempt=attempt,
                reason=adapter.fatal_error_message or "failed to reconnect",
                error_code=adapter.fatal_error_code,
                update_platform_runtime_status=update_platform_runtime_status,
                now=now,
                backoff_cap=backoff_cap,
                pause_after_failures=pause_after_failures,
                logger=logger,
            )
    except Exception as exc:
        _record_retry_failure(
            failed_platforms,
            platform,
            info,
            attempt=attempt,
            reason=str(exc),
            error_code=None,
            update_platform_runtime_status=update_platform_runtime_status,
            now=now,
            backoff_cap=backoff_cap,
            pause_after_failures=pause_after_failures,
            logger=logger,
            exception=exc,
        )
    return True


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _record_retry_failure(
    failed_platforms: MutableMapping[Platform, dict[str, Any]],
    platform: Platform,
    info: dict[str, Any],
    *,
    attempt: int,
    reason: str,
    error_code: str | None,
    update_platform_runtime_status: Callable[..., Any],
    now: Callable[[], float],
    backoff_cap: int,
    pause_after_failures: int,
    logger: logging.Logger,
    exception: Exception | None = None,
) -> None:
    _update_platform_status(
        update_platform_runtime_status,
        platform,
        platform_state="retrying",
        error_code=error_code,
        error_message=reason,
    )
    backoff = min(30 * (2 ** (attempt - 1)), backoff_cap)
    info["attempts"] = attempt
    info["next_retry"] = now() + backoff
    if exception is None:
        logger.info(
            "Reconnect %s failed, next retry in %ds",
            platform.value,
            backoff,
        )
    else:
        logger.warning(
            "Reconnect %s error: %s, next retry in %ds",
            platform.value,
            exception,
            backoff,
        )
    if attempt >= pause_after_failures:
        pause_failed_platform(
            failed_platforms,
            platform,
            reason=reason,
            update_platform_runtime_status=update_platform_runtime_status,
            logger=logger,
        )


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
