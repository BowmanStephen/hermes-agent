"""Shared post-tool result flow for agent tool execution."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional

from agent.display import _detect_tool_failure
from agent.tool_dispatch_helpers import (
    _append_subdir_hint_to_multimodal,
    _is_multimodal_tool_result,
    _multimodal_text_summary,
    make_tool_result_message,
)
from tools.terminal_tool import get_active_env
from tools.tool_result_storage import enforce_turn_budget, maybe_persist_tool_result

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolResultContext:
    name: str
    args: dict[str, Any]
    content: Any
    duration: float
    call_id: str
    task_id: str
    messages: list
    blocked: bool = False
    is_error: Optional[bool] = None


@dataclass(frozen=True)
class FinalizedToolResult:
    content: Any
    model_content: Any
    is_error: bool


def _preview(result: Any, verbose: bool) -> tuple[Any, int]:
    if isinstance(result, str):
        return (
            result if verbose else (result[:200] if len(result) > 200 else result),
            len(result),
        )
    return result, len(str(result))


def finalize_tool_result(agent, ctx: ToolResultContext) -> FinalizedToolResult:
    """Finalize one completed tool result and append its tool message.

    Display code still decides how to render progress. This module owns the
    common non-display flow: guardrail observations, mutation verification,
    completion callbacks, persistence, subdir hints, model-content conversion,
    message append, and activity touch.
    """
    is_error = ctx.is_error
    if is_error is None:
        is_error, _ = _detect_tool_failure(ctx.name, ctx.content)

    result = ctx.content
    if not ctx.blocked:
        result = agent._append_guardrail_observation(
            ctx.name,
            ctx.args,
            result,
            failed=is_error,
        )

    result_preview, result_len = _preview(result, getattr(agent, "verbose_logging", False))
    if is_error:
        result_preview = _multimodal_text_summary(result)
        result_preview = result_preview[:200] if len(result_preview) > 200 else result_preview
        logger.warning(
            "Tool %s returned error (%.2fs): %s",
            ctx.name,
            ctx.duration,
            result_preview,
        )
    else:
        logger.info("tool %s completed (%.2fs, %d chars)", ctx.name, ctx.duration, result_len)

    if not ctx.blocked:
        try:
            agent._record_file_mutation_result(ctx.name, ctx.args, result, is_error)
        except Exception as ver_err:
            logging.debug("file-mutation verifier record failed: %s", ver_err)

    if not ctx.blocked and getattr(agent, "tool_progress_callback", None):
        try:
            agent.tool_progress_callback(
                "tool.completed",
                ctx.name,
                None,
                None,
                duration=ctx.duration,
                is_error=is_error,
            )
        except Exception as cb_err:
            logging.debug("Tool progress callback error: %s", cb_err)

    agent._current_tool = None
    agent._touch_activity(f"tool completed: {ctx.name} ({ctx.duration:.1f}s)")

    if getattr(agent, "verbose_logging", False):
        logging.debug("Tool %s completed in %.2fs", ctx.name, ctx.duration)
        log_result = _multimodal_text_summary(result)
        logging.debug("Tool result (%d chars): %s", len(log_result), log_result)

    if not ctx.blocked and getattr(agent, "tool_complete_callback", None):
        try:
            agent.tool_complete_callback(ctx.call_id, ctx.name, ctx.args, result)
        except Exception as cb_err:
            logging.debug("Tool complete callback error: %s", cb_err)

    final_result = (
        maybe_persist_tool_result(
            content=result,
            tool_name=ctx.name,
            tool_use_id=ctx.call_id,
            env=get_active_env(ctx.task_id),
        )
        if not _is_multimodal_tool_result(result)
        else result
    )

    subdir_hints = agent._subdirectory_hints.check_tool_call(ctx.name, ctx.args)
    if subdir_hints:
        if _is_multimodal_tool_result(final_result):
            _append_subdir_hint_to_multimodal(final_result, subdir_hints)
        else:
            final_result += subdir_hints

    model_content = agent._tool_result_content_for_active_model(ctx.name, final_result)
    ctx.messages.append(make_tool_result_message(ctx.name, model_content, ctx.call_id))

    return FinalizedToolResult(
        content=final_result,
        model_content=model_content,
        is_error=is_error,
    )


def finalize_tool_results(agent, contexts: list[ToolResultContext]) -> list[FinalizedToolResult]:
    """Finalize a batch of tool results and run batch-level result handling."""
    finalized = [finalize_tool_result(agent, ctx) for ctx in contexts]
    if contexts:
        finalize_tool_result_batch(agent, contexts[0].messages, len(contexts), contexts[0].task_id)
    return finalized


def finalize_tool_result_batch(agent, messages: list, num_tool_msgs: int, task_id: str) -> None:
    """Run batch-level result handling after tool messages have been appended."""
    if num_tool_msgs <= 0:
        return
    turn_tool_msgs = messages[-num_tool_msgs:]
    enforce_turn_budget(turn_tool_msgs, env=get_active_env(task_id))
    agent._apply_pending_steer_to_tool_results(messages, num_tool_msgs)


def make_cancelled_tool_result_message(name: str, call_id: str) -> dict[str, Any]:
    return make_tool_result_message(
        name,
        f"[Tool execution cancelled — {name} was skipped due to user interrupt]",
        call_id,
    )


def make_not_started_tool_result_message(name: str, call_id: str) -> dict[str, Any]:
    return make_tool_result_message(
        name,
        f"[Tool execution skipped — {name} was not started. User sent a new message]",
        call_id,
    )


__all__ = [
    "FinalizedToolResult",
    "ToolResultContext",
    "finalize_tool_result_batch",
    "finalize_tool_result",
    "finalize_tool_results",
    "make_cancelled_tool_result_message",
    "make_not_started_tool_result_message",
]
