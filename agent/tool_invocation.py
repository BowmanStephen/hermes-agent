"""Deep tool invocation module for agent-owned tool execution.

This module owns the behavior that must be true for every tool call before
display code decides how to present it: plugin blocking, guardrails,
checkpointing, stateful agent adapters, duration measurement, and post-call
plugin hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import time
from typing import Any, Optional

from agent.tool_dispatch_helpers import _is_destructive_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolInvocation:
    """One tool call crossing the tool invocation boundary."""

    name: str
    args: dict[str, Any]
    task_id: str = ""
    call_id: Optional[str] = None
    messages: Optional[list] = None


@dataclass(frozen=True)
class ToolDisplayHint:
    """UI-neutral invocation metadata for caller-owned display code."""

    adapter_name: str
    duration_ms: int
    blocked_by: Optional[str] = None

    @property
    def blocked(self) -> bool:
        return self.blocked_by is not None


@dataclass(frozen=True)
class ToolInvocationResult:
    """Result plus execution metadata for callers and display adapters."""

    content: Any
    duration_ms: int
    adapter_name: str
    blocked_by: Optional[str] = None
    is_error: Optional[bool] = None

    @classmethod
    def error(cls, content: Any, *, duration_ms: int = 0) -> "ToolInvocationResult":
        return cls(content=content, duration_ms=duration_ms, adapter_name="error", is_error=True)

    @classmethod
    def cancelled(cls, content: Any) -> "ToolInvocationResult":
        return cls(content=content, duration_ms=0, adapter_name="cancelled", is_error=True)

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000.0

    @property
    def display_hint(self) -> ToolDisplayHint:
        return ToolDisplayHint(
            adapter_name=self.adapter_name,
            duration_ms=self.duration_ms,
            blocked_by=self.blocked_by,
        )

    def to_result_context(
        self,
        invocation: "ToolInvocation",
        messages: list,
        *,
        is_error: Optional[bool] = None,
    ):
        from agent.tool_result_flow import ToolResultContext

        return ToolResultContext(
            name=invocation.name,
            args=invocation.args,
            content=self.content,
            duration=self.duration_seconds,
            call_id=invocation.call_id or "",
            task_id=invocation.task_id or "",
            messages=messages,
            blocked=self.blocked_by is not None,
            is_error=self.is_error if is_error is None else is_error,
        )


@dataclass(frozen=True)
class ToolInvocationPreparation:
    """Pre-execution decision for callbacks and execution."""

    blocked_content: Optional[str] = None
    blocked_by: Optional[str] = None

    @property
    def allows_execution(self) -> bool:
        return self.blocked_by is None

    def to_blocked_result(self) -> Optional[ToolInvocationResult]:
        if self.blocked_by is None:
            return None
        return ToolInvocationResult(
            content=self.blocked_content,
            duration_ms=0,
            adapter_name="blocked",
            blocked_by=self.blocked_by,
            is_error=True,
        )


@dataclass(frozen=True)
class PreparedToolInvocation:
    """Invocation plus its pre-execution policy decision."""

    invocation: ToolInvocation
    preparation: ToolInvocationPreparation
    adapter_name: str = "registry"

    @property
    def name(self) -> str:
        return self.invocation.name

    @property
    def args(self) -> dict[str, Any]:
        return self.invocation.args

    @property
    def call_id(self) -> str:
        return self.invocation.call_id or ""

    @property
    def allows_execution(self) -> bool:
        return self.preparation.allows_execution

    @property
    def blocked_result(self) -> Optional[ToolInvocationResult]:
        return self.preparation.to_blocked_result()

    def to_result_context(self, result: ToolInvocationResult, messages: list):
        return result.to_result_context(self.invocation, messages)

    def cancelled_result(self) -> ToolInvocationResult:
        return ToolInvocationResult.cancelled(
            f"[Tool execution cancelled — {self.name} was skipped due to user interrupt]"
        )

    def missing_result(self) -> ToolInvocationResult:
        return ToolInvocationResult.error(
            f"Error executing tool '{self.name}': thread did not return a result"
        )


def _ra():
    """Lazy reference to ``run_agent`` so existing tests can patch it."""
    import run_agent

    return run_agent


def _plugin_block_message(invocation: ToolInvocation) -> Optional[str]:
    try:
        from hermes_cli.plugins import get_pre_tool_call_block_message

        return get_pre_tool_call_block_message(
            invocation.name,
            invocation.args,
            task_id=invocation.task_id or "",
        )
    except Exception:
        return None


def _guardrail_block_result(agent, invocation: ToolInvocation) -> tuple[Optional[str], bool]:
    guardrails = getattr(agent, "_tool_guardrails", None)
    if guardrails is None:
        return None, False
    decision = guardrails.before_call(invocation.name, invocation.args)
    if decision.allows_execution:
        return None, False
    return agent._guardrail_block_result(decision), True


def _adapter_name_for(agent, invocation: ToolInvocation) -> str:
    name = invocation.name
    if name in {"todo", "session_search", "memory", "clarify", "delegate_task"}:
        return name

    context_engine_names = getattr(agent, "_context_engine_tool_names", None) or set()
    if context_engine_names and name in context_engine_names:
        return "context_engine"

    memory_manager = getattr(agent, "_memory_manager", None)
    if memory_manager and memory_manager.has_tool(name):
        return "memory_provider"

    return "registry"


def _ensure_checkpoint(agent, invocation: ToolInvocation) -> None:
    checkpoint_mgr = getattr(agent, "_checkpoint_mgr", None)
    if not (checkpoint_mgr and getattr(checkpoint_mgr, "enabled", False)):
        return

    if invocation.name in {"write_file", "patch"}:
        try:
            file_path = invocation.args.get("path", "")
            if file_path:
                work_dir = checkpoint_mgr.get_working_dir_for_path(file_path)
                checkpoint_mgr.ensure_checkpoint(work_dir, f"before {invocation.name}")
        except Exception:
            pass
        return

    if invocation.name == "terminal":
        try:
            cmd = invocation.args.get("command", "")
            if _is_destructive_command(cmd):
                cwd = invocation.args.get("workdir") or os.getenv("TERMINAL_CWD", os.getcwd())
                checkpoint_mgr.ensure_checkpoint(cwd, f"before terminal: {cmd[:60]}")
        except Exception:
            pass


def _reset_nudge_counters(agent, invocation: ToolInvocation) -> None:
    if invocation.name == "memory" and hasattr(agent, "_turns_since_memory"):
        agent._turns_since_memory = 0
    elif invocation.name == "skill_manage" and hasattr(agent, "_iters_since_skill"):
        agent._iters_since_skill = 0


def _invoke_post_and_transform_hooks(agent, invocation: ToolInvocation, result: Any, duration_ms: int) -> Any:
    try:
        from hermes_cli.plugins import invoke_hook

        invoke_hook(
            "post_tool_call",
            tool_name=invocation.name,
            args=invocation.args,
            result=result,
            task_id=invocation.task_id or "",
            session_id=getattr(agent, "session_id", "") or "",
            tool_call_id=invocation.call_id or "",
            duration_ms=duration_ms,
        )
    except Exception as hook_err:
        logger.debug("post_tool_call hook error: %s", hook_err)

    try:
        from hermes_cli.plugins import invoke_hook

        hook_results = invoke_hook(
            "transform_tool_result",
            tool_name=invocation.name,
            args=invocation.args,
            result=result,
            task_id=invocation.task_id or "",
            session_id=getattr(agent, "session_id", "") or "",
            tool_call_id=invocation.call_id or "",
            duration_ms=duration_ms,
        )
        for hook_result in hook_results:
            if isinstance(hook_result, str):
                return hook_result
    except Exception as hook_err:
        logger.debug("transform_tool_result hook error: %s", hook_err)
    return result


def _detect_invocation_error(invocation: ToolInvocation, result: Any) -> bool:
    from agent.display import _detect_tool_failure

    is_error, _ = _detect_tool_failure(invocation.name, result)
    return is_error


def _invoke_agent_adapter(agent, invocation: ToolInvocation) -> tuple[Any, str, bool]:
    name = invocation.name
    args = invocation.args

    if name == "todo":
        from tools.todo_tool import todo_tool as _todo_tool

        return (
            _todo_tool(
                todos=args.get("todos"),
                merge=args.get("merge", False),
                store=agent._todo_store,
            ),
            "todo",
            True,
        )

    if name == "session_search":
        session_db = agent._get_session_db_for_recall()
        if not session_db:
            from hermes_state import format_session_db_unavailable

            return (
                json.dumps({"success": False, "error": format_session_db_unavailable()}),
                "session_search",
                True,
            )
        from tools.session_search_tool import session_search as _session_search

        return (
            _session_search(
                query=args.get("query", ""),
                role_filter=args.get("role_filter"),
                limit=args.get("limit", 3),
                session_id=args.get("session_id"),
                around_message_id=args.get("around_message_id"),
                window=args.get("window", 5),
                sort=args.get("sort"),
                db=session_db,
                current_session_id=agent.session_id,
            ),
            "session_search",
            True,
        )

    if name == "memory":
        target = args.get("target", "memory")
        from tools.memory_tool import memory_tool as _memory_tool

        result = _memory_tool(
            action=args.get("action"),
            target=target,
            content=args.get("content"),
            old_text=args.get("old_text"),
            store=agent._memory_store,
        )
        if getattr(agent, "_memory_manager", None) and args.get("action") in {"add", "replace"}:
            try:
                agent._memory_manager.on_memory_write(
                    args.get("action", ""),
                    target,
                    args.get("content", ""),
                    metadata=agent._build_memory_write_metadata(
                        task_id=invocation.task_id,
                        tool_call_id=invocation.call_id,
                    ),
                )
            except Exception:
                pass
        return result, "memory", True

    if name == "clarify":
        from tools.clarify_tool import clarify_tool as _clarify_tool

        return (
            _clarify_tool(
                question=args.get("question", ""),
                choices=args.get("choices"),
                callback=getattr(agent, "clarify_callback", None),
            ),
            "clarify",
            True,
        )

    if name == "delegate_task":
        return agent._dispatch_delegate_task(args), "delegate_task", True

    context_engine_names = getattr(agent, "_context_engine_tool_names", None) or set()
    if context_engine_names and name in context_engine_names:
        try:
            return (
                agent.context_compressor.handle_tool_call(name, args, messages=invocation.messages),
                "context_engine",
                True,
            )
        except Exception as tool_error:
            logger.error(
                "context_engine.handle_tool_call raised for %s: %s",
                name,
                tool_error,
                exc_info=True,
            )
            return (
                json.dumps({"error": f"Context engine tool '{name}' failed: {tool_error}"}),
                "context_engine",
                True,
            )

    memory_manager = getattr(agent, "_memory_manager", None)
    if memory_manager and memory_manager.has_tool(name):
        try:
            return memory_manager.handle_tool_call(name, args), "memory_provider", True
        except Exception as tool_error:
            logger.error(
                "memory_manager.handle_tool_call raised for %s: %s",
                name,
                tool_error,
                exc_info=True,
            )
            return (
                json.dumps({"error": f"Memory tool '{name}' failed: {tool_error}"}),
                "memory_provider",
                True,
            )

    return "", "registry", False


def _invoke_registry_adapter(agent, invocation: ToolInvocation) -> Any:
    enabled_tools = _enabled_tool_names(agent)
    return _ra().handle_function_call(
        invocation.name,
        invocation.args,
        invocation.task_id,
        tool_call_id=invocation.call_id,
        session_id=getattr(agent, "session_id", "") or "",
        enabled_tools=enabled_tools,
        skip_pre_tool_call_hook=True,
    )


def _enabled_tool_names(agent) -> Optional[list[str]]:
    tool_defs = getattr(agent, "tools", None) or []
    names: list[str] = []
    for tool_def in tool_defs:
        if not isinstance(tool_def, dict):
            continue
        function = tool_def.get("function", {})
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if name:
            names.append(str(name))
    if names:
        return names

    valid_names = getattr(agent, "valid_tool_names", None)
    if valid_names is None:
        return None
    if isinstance(valid_names, set):
        return sorted(valid_names)
    return list(valid_names)


def invoke_tool(
    agent,
    invocation: ToolInvocation,
    *,
    pre_tool_block_checked: bool = False,
    apply_guardrails: bool = True,
    preparation: Optional[ToolInvocationPreparation] = None,
) -> ToolInvocationResult:
    """Invoke a tool for an agent and return content plus metadata.

    Invariants:
    - plugin pre-tool blocking runs before guardrails unless the caller has
      already checked it;
    - blocked calls do not checkpoint or execute adapters;
    - registry calls still go through ``model_tools.handle_function_call`` for
      ACP approval, argument coercion, read-loop notification, and registry
      dispatch compatibility;
    - agent-state adapters receive post/transform hooks here because they do
      not cross ``model_tools.handle_function_call``.
    """
    preparation = preparation or prepare_tool_invocation(
        agent,
        invocation,
        pre_tool_block_checked=pre_tool_block_checked,
        apply_guardrails=apply_guardrails,
    )
    blocked_result = preparation.to_blocked_result()
    if blocked_result is not None:
        return blocked_result

    _reset_nudge_counters(agent, invocation)
    _ensure_checkpoint(agent, invocation)

    start = time.monotonic()
    try:
        result, adapter_name, handled = _invoke_agent_adapter(agent, invocation)
        if not handled:
            result = _invoke_registry_adapter(agent, invocation)
        duration_ms = int((time.monotonic() - start) * 1000)
    except Exception as tool_error:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            "tool invocation raised for %s: %s",
            invocation.name,
            tool_error,
            exc_info=True,
        )
        return ToolInvocationResult.error(
            f"Error executing tool '{invocation.name}': {tool_error}",
            duration_ms=duration_ms,
        )

    if handled:
        result = _invoke_post_and_transform_hooks(agent, invocation, result, duration_ms)

    return ToolInvocationResult(
        content=result,
        duration_ms=duration_ms,
        adapter_name=adapter_name,
        blocked_by=None,
        is_error=_detect_invocation_error(invocation, result),
    )


def invoke_prepared_tool(agent, prepared: PreparedToolInvocation) -> ToolInvocationResult:
    """Invoke a tool whose pre-execution checks were already prepared."""
    return invoke_tool(
        agent,
        prepared.invocation,
        preparation=prepared.preparation,
    )


def prepare_tool_invocation(
    agent,
    invocation: ToolInvocation,
    *,
    pre_tool_block_checked: bool = False,
    apply_guardrails: bool = True,
) -> ToolInvocationPreparation:
    """Run pre-execution checks without callbacks, checkpoints, or adapters."""
    if not pre_tool_block_checked:
        block_message = _plugin_block_message(invocation)
        if block_message is not None:
            return ToolInvocationPreparation(
                blocked_content=json.dumps({"error": block_message}, ensure_ascii=False),
                blocked_by="plugin",
            )

    if apply_guardrails:
        guardrail_result, blocked = _guardrail_block_result(agent, invocation)
        if blocked:
            return ToolInvocationPreparation(
                blocked_content=guardrail_result,
                blocked_by="guardrail",
            )

    return ToolInvocationPreparation()


def prepare_tool(
    agent,
    invocation: ToolInvocation,
    *,
    pre_tool_block_checked: bool = False,
    apply_guardrails: bool = True,
) -> PreparedToolInvocation:
    """Prepare a tool invocation and keep the decision attached to it."""
    return PreparedToolInvocation(
        invocation,
        prepare_tool_invocation(
            agent,
            invocation,
            pre_tool_block_checked=pre_tool_block_checked,
            apply_guardrails=apply_guardrails,
        ),
        _adapter_name_for(agent, invocation),
    )


def _parse_tool_call_args(tool_call) -> dict[str, Any]:
    try:
        args = json.loads(tool_call.function.arguments)
    except json.JSONDecodeError:
        args = {}
    return args if isinstance(args, dict) else {}


def prepare_tool_call(agent, tool_call, task_id: str, messages: Optional[list] = None) -> PreparedToolInvocation:
    """Build and prepare a ToolInvocation from a provider tool-call object."""
    invocation = ToolInvocation(
        tool_call.function.name,
        _parse_tool_call_args(tool_call),
        task_id=task_id or "",
        call_id=getattr(tool_call, "id", None),
        messages=messages,
    )
    return prepare_tool(agent, invocation)


__all__ = [
    "PreparedToolInvocation",
    "ToolDisplayHint",
    "ToolInvocation",
    "ToolInvocationPreparation",
    "ToolInvocationResult",
    "invoke_prepared_tool",
    "invoke_tool",
    "prepare_tool",
    "prepare_tool_call",
    "prepare_tool_invocation",
]
