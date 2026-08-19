"""Task-level cost, context, and outcome telemetry helpers.

The SessionDB table is deliberately a narrow ledger: the existing token
accounting path owns live usage deltas, while this module owns turn-boundary
metadata and the best-effort lifecycle wrapper. Telemetry must never break a
user turn.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Dict, Optional

from agent.model_metadata import (
    estimate_messages_tokens_rough,
    estimate_request_tokens_rough,
    estimate_tokens_rough,
)

logger = logging.getLogger(__name__)

_BREAKDOWN_BUCKETS = (
    "system_prompt",
    "tools",
    "context_files",
    "memory",
    "user_message",
    "conversation",
    "injected_context",
)


def _text_tokens(value: Any) -> int:
    if not value:
        return 0
    if isinstance(value, str):
        return estimate_tokens_rough(value)
    return estimate_messages_tokens_rough([{"role": "user", "content": value}])


def _memory_text(agent: Any) -> str:
    store = getattr(agent, "_memory_store", None)
    if store is None:
        return ""
    blocks = []
    try:
        if getattr(agent, "_memory_enabled", True):
            blocks.append(store.format_for_system_prompt("memory") or "")
        if getattr(agent, "_user_profile_enabled", True):
            blocks.append(store.format_for_system_prompt("user") or "")
    except Exception:
        return ""
    return "\n\n".join(block for block in blocks if block)


def _context_categories(agent: Any, messages) -> Optional[Dict[str, int]]:
    """Reuse the existing live context estimator when a full agent is present."""
    try:
        from agent.context_breakdown import compute_session_context_breakdown

        payload = compute_session_context_breakdown(agent, messages)
        values = {
            str(item.get("id")): int(item.get("tokens") or 0)
            for item in payload.get("categories", [])
        }
        return {
            "system_prompt": values.get("system_prompt", 0)
            + values.get("skills", 0),
            "tools": values.get("tool_definitions", 0)
            + values.get("mcp", 0)
            + values.get("subagent_definitions", 0),
            # The existing estimator's rules bucket is the workspace/context
            # tier. It includes the context-file payload and coding snapshot.
            "context_files": values.get("rules", 0),
            "memory": values.get("memory", 0),
        }
    except Exception:
        return None


def build_task_context_breakdown(
    agent: Any,
    messages,
    *,
    current_user_message: Any,
    current_user_index: Optional[int] = None,
    ext_prefetch_cache: str = "",
    plugin_user_context: str = "",
) -> Dict[str, int]:
    """Estimate the named input-token buckets for the current task turn.

    This uses the same rough estimator as compression/context surfaces. The
    breakdown is diagnostic, not an invoice: provider-reported usage remains
    authoritative in the task row's input/output counters.
    """
    categories = _context_categories(agent, messages)
    if categories is None:
        categories = {
            "system_prompt": _text_tokens(getattr(agent, "_cached_system_prompt", "")),
            "tools": estimate_request_tokens_rough(
                [], tools=list(getattr(agent, "tools", None) or []) or None
            ),
            "context_files": _text_tokens(
                getattr(agent, "_context_files_prompt", "")
            ),
            "memory": _text_tokens(_memory_text(agent)),
        }

    if current_user_index is None:
        current_user_index = len(messages) - 1 if messages else -1
    prior_messages = [
        message
        for index, message in enumerate(messages or [])
        if index != current_user_index
    ]
    breakdown = {
        "system_prompt": max(0, int(categories.get("system_prompt", 0))),
        "tools": max(0, int(categories.get("tools", 0))),
        "context_files": max(0, int(categories.get("context_files", 0))),
        "memory": max(0, int(categories.get("memory", 0)))
        + _text_tokens(ext_prefetch_cache),
        "user_message": _text_tokens(current_user_message),
        "conversation": estimate_messages_tokens_rough(prior_messages),
        "injected_context": _text_tokens(plugin_user_context),
    }
    breakdown["estimated_input_tokens"] = sum(
        breakdown[bucket] for bucket in _BREAKDOWN_BUCKETS
    )
    return breakdown


def _session_db(agent: Any):
    return getattr(agent, "_session_db", None)


def start_task_telemetry(
    agent: Any,
    *,
    task_id: str,
    turn_id: str,
    started_at: Optional[float] = None,
    breakdown: Optional[Dict[str, Any]] = None,
) -> None:
    """Create/update the live row at turn start, fail-open."""
    db = _session_db(agent)
    if (
        db is None
        or not isinstance(task_id, str)
        or not task_id
        or not isinstance(turn_id, str)
        or not turn_id
    ):
        return
    try:
        budget = {
            "max_iterations": int(getattr(agent, "max_iterations", 0) or 0),
        }
        db.record_task_telemetry(
            task_id=task_id,
            turn_id=turn_id,
            session_id=getattr(agent, "session_id", None),
            model=getattr(agent, "model", None),
            provider=getattr(agent, "provider", None),
            started_at=started_at,
            breakdown=breakdown,
            budget=budget,
        )
    except Exception:
        logger.debug("task telemetry start failed", exc_info=True)


def finish_task_telemetry(
    agent: Any,
    *,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[BaseException] = None,
) -> None:
    """Flush live deltas and write the terminal outcome, fail-open."""
    db = _session_db(agent)
    task_id = getattr(agent, "_current_task_id", None)
    turn_id = getattr(agent, "_current_turn_id", None)
    started_at = getattr(agent, "_task_telemetry_started_at", None)
    if (
        db is None
        or not isinstance(task_id, str)
        or not task_id
        or not isinstance(turn_id, str)
        or not turn_id
    ):
        return
    try:
        db.flush_token_counts()
        ended_at = time.time()
        if error is not None:
            outcome = "failed"
            error_text = str(error)
        elif result and result.get("completed"):
            outcome = "completed"
            error_text = result.get("error")
        elif result and result.get("interrupted"):
            outcome = "interrupted"
            error_text = result.get("error")
        elif result and (result.get("failed") or result.get("error")):
            outcome = "failed"
            error_text = result.get("error")
        else:
            outcome = "partial"
            error_text = result.get("error") if result else None
        latency_ms = (
            max(0.0, (ended_at - started_at) * 1000.0)
            if isinstance(started_at, (int, float))
            else None
        )
        db.record_task_telemetry(
            task_id=task_id,
            turn_id=turn_id,
            session_id=getattr(agent, "session_id", None),
            model=getattr(agent, "model", None),
            provider=getattr(agent, "provider", None),
            ended_at=ended_at,
            latency_ms=latency_ms,
            outcome=outcome,
            quality_score=(result or {}).get("quality_score"),
            error=str(error_text) if error_text else None,
        )
    except Exception:
        logger.debug("task telemetry finish failed", exc_info=True)


def with_task_telemetry(run_turn):
    """Wrap every run-conversation exit, including early-return paths."""

    @functools.wraps(run_turn)
    def wrapped(agent, *args, **kwargs):
        result = None
        caught = None
        try:
            result = run_turn(agent, *args, **kwargs)
            return result
        except BaseException as exc:
            caught = exc
            raise
        finally:
            finish_task_telemetry(agent, result=result, error=caught)

    return wrapped
