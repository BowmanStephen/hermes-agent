"""Tool-call execution — sequential and concurrent dispatch.

Both AIAgent methods (``_execute_tool_calls_sequential`` and
``_execute_tool_calls_concurrent``) live here as module-level
functions that take the parent ``AIAgent`` as their first argument.

``run_agent`` keeps thin wrappers so existing call sites work; tests
that patch ``run_agent._set_interrupt`` are honored because the
extracted functions reach back through the ``run_agent`` module via
``_ra()`` for that symbol.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging
import random
import threading
import time

from agent.display import (
    KawaiiSpinner,
    build_tool_preview as _build_tool_preview,
    get_cute_tool_message as _get_cute_tool_message_impl,
    get_tool_emoji as _get_tool_emoji,
)
from agent.tool_invocation import (
    invoke_prepared_tool as _invoke_prepared_tool_core,
    prepare_tool_call,
)
from agent.tool_result_flow import (
    finalize_tool_result,
    finalize_tool_result_batch,
    finalize_tool_results,
    make_cancelled_tool_result_message,
    make_not_started_tool_result_message,
)
from agent.tool_dispatch_helpers import (
    _multimodal_text_summary,
)
from tools.terminal_tool import (
    _get_approval_callback,
    _get_sudo_password_callback,
    set_approval_callback as _set_approval_callback,
    set_sudo_password_callback as _set_sudo_password_callback,
)

logger = logging.getLogger(__name__)

# Maximum number of concurrent worker threads for parallel tool execution.
# Mirrors the constant in ``run_agent`` for tests/imports that look here.
_MAX_TOOL_WORKERS = 8


def _ra():
    """Lazy reference to ``run_agent`` so patches like ``run_agent._set_interrupt`` work."""
    import run_agent
    return run_agent


def execute_tool_calls_concurrent(agent, assistant_message, messages: list, effective_task_id: str, api_call_count: int = 0) -> None:
    """Execute multiple tool calls concurrently using a thread pool.

    Results are collected in the original tool-call order and appended to
    messages so the API sees them in the expected sequence.
    """
    tool_calls = assistant_message.tool_calls
    num_tools = len(tool_calls)

    # ── Pre-flight: interrupt check ──────────────────────────────────
    if agent._interrupt_requested:
        print(f"{agent.log_prefix}⚡ Interrupt: skipping {num_tools} tool call(s)")
        for tc in tool_calls:
            messages.append(make_cancelled_tool_result_message(tc.function.name, tc.id))
        return

    # ── Parse args + pre-execution bookkeeping ───────────────────────
    prepared_calls = []
    for tool_call in tool_calls:
        prepared = prepare_tool_call(agent, tool_call, effective_task_id or "", messages)
        prepared_calls.append(prepared)

    # ── Logging / callbacks ──────────────────────────────────────────
    tool_names_str = ", ".join(prepared.name for prepared in prepared_calls)
    if not agent.quiet_mode:
        print(f"  ⚡ Concurrent: {num_tools} tool calls — {tool_names_str}")
        for i, prepared in enumerate(prepared_calls, 1):
            name = prepared.name
            args = prepared.args
            args_str = json.dumps(args, ensure_ascii=False)
            if agent.verbose_logging:
                print(f"  📞 Tool {i}: {name}({list(args.keys())})")
                print(agent._wrap_verbose("Args: ", json.dumps(args, indent=2, ensure_ascii=False)))
            else:
                args_preview = args_str[:agent.log_prefix_chars] + "..." if len(args_str) > agent.log_prefix_chars else args_str
                print(f"  📞 Tool {i}: {name}({list(args.keys())}) - {args_preview}")

    for prepared in prepared_calls:
        if not prepared.allows_execution:
            continue
        if agent.tool_progress_callback:
            try:
                preview = _build_tool_preview(prepared.name, prepared.args)
                agent.tool_progress_callback("tool.started", prepared.name, preview, prepared.args)
            except Exception as cb_err:
                logging.debug(f"Tool progress callback error: {cb_err}")

    for prepared in prepared_calls:
        if not prepared.allows_execution:
            continue
        if agent.tool_start_callback:
            try:
                agent.tool_start_callback(prepared.call_id, prepared.name, prepared.args)
            except Exception as cb_err:
                logging.debug(f"Tool start callback error: {cb_err}")

    # ── Concurrent execution ─────────────────────────────────────────
    # Each slot holds (prepared_invocation, invocation_result)
    results = [None] * num_tools
    for i, prepared in enumerate(prepared_calls):
        blocked_result = prepared.blocked_result
        if blocked_result is not None:
            results[i] = (prepared, blocked_result)

    runnable_calls = [
        (i, prepared)
        for i, prepared in enumerate(prepared_calls)
        if prepared.allows_execution
    ]
    runnable_tool_names_str = ", ".join(prepared.name for _, prepared in runnable_calls)

    if runnable_calls:
        # Touch activity before launching workers so the gateway knows
        # we're executing tools (not stuck).
        agent._current_tool = runnable_tool_names_str
        agent._touch_activity(
            f"executing {len(runnable_calls)} tools concurrently: {runnable_tool_names_str}"
        )

    # Capture CLI callbacks from the agent thread so worker threads can
    # register them locally.  Without this, _get_approval_callback() in
    # terminal_tool returns None in ThreadPoolExecutor workers, causing
    # the dangerous-command prompt to fall back to input() — which
    # deadlocks against prompt_toolkit's raw terminal mode (#13617).
    _parent_approval_cb = _get_approval_callback()
    _parent_sudo_cb = _get_sudo_password_callback()

    def _run_tool(index, prepared):
        """Worker function executed in a thread."""
        # Register this worker tid so the agent can fan out an interrupt
        # to it — see AIAgent.interrupt().  Must happen first thing, and
        # must be paired with discard + clear in the finally block.
        _worker_tid = threading.current_thread().ident
        with agent._tool_worker_threads_lock:
            agent._tool_worker_threads.add(_worker_tid)
        # Race: if the agent was interrupted between fan-out (which
        # snapshotted an empty/earlier set) and our registration, apply
        # the interrupt to our own tid now so is_interrupted() inside
        # the tool returns True on the next poll.
        if agent._interrupt_requested:
            try:
                _ra()._set_interrupt(True, _worker_tid)
            except Exception:
                pass
        # Set the activity callback on THIS worker thread so
        # _wait_for_process (terminal commands) can fire heartbeats.
        # The callback is thread-local; the main thread's callback
        # is invisible to worker threads.
        try:
            from tools.environments.base import set_activity_callback
            set_activity_callback(agent._touch_activity)
        except Exception:
            pass
        # Propagate approval/sudo callbacks to this worker thread.
        # Mirrors cli.py run_agent() pattern (GHSA-qg5c-hvr5-hjgr).
        if _parent_approval_cb is not None:
            try:
                _set_approval_callback(_parent_approval_cb)
            except Exception:
                pass
        if _parent_sudo_cb is not None:
            try:
                _set_sudo_password_callback(_parent_sudo_cb)
            except Exception:
                pass
        try:
            invocation_result = _invoke_prepared_tool_core(agent, prepared)
            result = invocation_result.content
            duration = invocation_result.duration_seconds
            is_error = bool(invocation_result.is_error)
            result_summary = _multimodal_text_summary(result)
            if is_error:
                logger.info("tool %s failed (%.2fs): %s", prepared.name, duration, result_summary[:200])
            else:
                logger.info("tool %s completed (%.2fs, %d chars)", prepared.name, duration, len(result_summary))
            results[index] = (prepared, invocation_result)
        finally:
            # Tear down worker-tid tracking.  Clear any interrupt bit we may
            # have set so the next task scheduled onto this recycled tid
            # starts with a clean slate.
            with agent._tool_worker_threads_lock:
                agent._tool_worker_threads.discard(_worker_tid)
            try:
                _ra()._set_interrupt(False, _worker_tid)
            except Exception:
                pass
            # Clear thread-local callbacks so a recycled worker thread
            # doesn't hold stale references to a disposed CLI instance.
            try:
                _set_approval_callback(None)
                _set_sudo_password_callback(None)
            except Exception:
                pass

    # Start spinner for CLI mode (skip when TUI handles tool progress)
    spinner = None
    if runnable_calls and agent._should_emit_quiet_tool_messages() and agent._should_start_quiet_spinner():
        face = random.choice(KawaiiSpinner.get_waiting_faces())
        spinner = KawaiiSpinner(
            f"{face} ⚡ running {len(runnable_calls)} tools concurrently",
            spinner_type='dots',
            print_fn=agent._print_fn,
        )
        spinner.start()

    try:
        futures = []
        future_names = {}
        if runnable_calls:
            max_workers = min(len(runnable_calls), _MAX_TOOL_WORKERS)
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                for i, prepared in runnable_calls:
                    # Propagate ContextVars (e.g. _approval_session_key); mirrors asyncio.to_thread.
                    ctx = contextvars.copy_context()
                    f = executor.submit(ctx.run, _run_tool, i, prepared)
                    futures.append(f)
                    future_names[f] = prepared.name

                # Wait for all to complete with periodic heartbeats so the
                # gateway's inactivity monitor doesn't kill us during long
                # concurrent tool batches. Also check for user interrupts
                # so we don't block indefinitely when the user sends /stop
                # or a new message during concurrent tool execution.
                _conc_start = time.time()
                _interrupt_logged = False
                while True:
                    done, not_done = concurrent.futures.wait(
                        futures, timeout=5.0,
                    )
                    if not not_done:
                        break

                    # Check for interrupt — the per-thread interrupt signal
                    # already causes individual tools (terminal, execute_code)
                    # to abort, but tools without interrupt checks (web_search,
                    # read_file) will run to completion. Cancel any futures
                    # that haven't started yet so we don't block on them.
                    if agent._interrupt_requested:
                        if not _interrupt_logged:
                            _interrupt_logged = True
                            agent._vprint(
                                f"{agent.log_prefix}⚡ Interrupt: cancelling "
                                f"{len(not_done)} pending concurrent tool(s)",
                                force=True,
                            )
                        for f in not_done:
                            f.cancel()
                        # Give already-running tools a moment to notice the
                        # per-thread interrupt signal and exit gracefully.
                        concurrent.futures.wait(not_done, timeout=3.0)
                        break

                    _conc_elapsed = int(time.time() - _conc_start)
                    # Heartbeat every ~30s (6 × 5s poll intervals)
                    if _conc_elapsed > 0 and _conc_elapsed % 30 < 6:
                        _still_running = [
                            future_names[f]
                            for f in not_done
                            if f in future_names
                        ]
                        agent._touch_activity(
                            f"concurrent tools running ({_conc_elapsed}s, "
                            f"{len(not_done)} remaining: {', '.join(_still_running[:3])})"
                        )
    finally:
        if spinner:
            # Build a summary message for the spinner stop
            runnable_results = [
                result
                for prepared, result in zip(prepared_calls, results)
                if prepared.allows_execution and result is not None
            ]
            completed = len(runnable_results)
            total_dur = sum(result[1].duration_seconds for result in runnable_results)
            spinner.stop(
                f"⚡ {completed}/{len(runnable_calls)} tools completed in {total_dur:.1f}s total"
            )

    # ── Post-execution: finalize and display per-tool results ─────────
    display_rows = []
    result_contexts = []
    for i, prepared in enumerate(prepared_calls):
        r = results[i]
        if r is None:
            # Tool was cancelled (interrupt) or thread didn't return
            if agent._interrupt_requested:
                invocation_result = prepared.cancelled_result()
            else:
                invocation_result = prepared.missing_result()
            function_result = invocation_result.content
            tool_duration = 0.0
        else:
            prepared, invocation_result = r
            function_result = invocation_result.content
            tool_duration = invocation_result.duration_seconds
        result_contexts.append(
            prepared.to_result_context(invocation_result, messages)
        )
        display_rows.append((i, prepared, function_result, tool_duration))

    finalized_results = finalize_tool_results(agent, result_contexts)
    for (i, prepared, _function_result, tool_duration), finalized in zip(display_rows, finalized_results):
        function_result = finalized.content

        # Print cute message per tool
        if agent._should_emit_quiet_tool_messages():
            cute_msg = _get_cute_tool_message_impl(prepared.name, prepared.args, tool_duration, result=function_result)
            agent._safe_print(f"  {cute_msg}")
        elif not agent.quiet_mode:
            _preview_str = _multimodal_text_summary(function_result)
            if agent.verbose_logging:
                print(f"  ✅ Tool {i+1} completed in {tool_duration:.2f}s")
                print(agent._wrap_verbose("Result: ", _preview_str))
            else:
                response_preview = _preview_str[:agent.log_prefix_chars] + "..." if len(_preview_str) > agent.log_prefix_chars else _preview_str
                print(f"  ✅ Tool {i+1} completed in {tool_duration:.2f}s - {response_preview}")



def execute_tool_calls_sequential(agent, assistant_message, messages: list, effective_task_id: str, api_call_count: int = 0) -> None:
    """Execute tool calls sequentially (original behavior). Used for single calls or interactive tools."""
    for i, tool_call in enumerate(assistant_message.tool_calls, 1):
        # SAFETY: check interrupt BEFORE starting each tool.
        # If the user sent "stop" during a previous tool's execution,
        # do NOT start any more tools -- skip them all immediately.
        if agent._interrupt_requested:
            remaining_calls = assistant_message.tool_calls[i-1:]
            if remaining_calls:
                agent._vprint(f"{agent.log_prefix}⚡ Interrupt: skipping {len(remaining_calls)} tool call(s)", force=True)
            for skipped_tc in remaining_calls:
                messages.append(make_cancelled_tool_result_message(skipped_tc.function.name, skipped_tc.id))
            break

        prepared = prepare_tool_call(agent, tool_call, effective_task_id or "", messages)
        function_name = prepared.name
        function_args = prepared.args
        _execution_blocked = not prepared.allows_execution

        if not agent.quiet_mode:
            args_str = json.dumps(function_args, ensure_ascii=False)
            if agent.verbose_logging:
                print(f"  📞 Tool {i}: {function_name}({list(function_args.keys())})")
                print(agent._wrap_verbose("Args: ", json.dumps(function_args, indent=2, ensure_ascii=False)))
            else:
                args_preview = args_str[:agent.log_prefix_chars] + "..." if len(args_str) > agent.log_prefix_chars else args_str
                print(f"  📞 Tool {i}: {function_name}({list(function_args.keys())}) - {args_preview}")

        if not _execution_blocked:
            agent._current_tool = function_name
            agent._touch_activity(f"executing tool: {function_name}")

        # Set activity callback for long-running tool execution (terminal
        # commands, etc.) so the gateway's inactivity monitor doesn't kill
        # the agent while a command is running.
        if not _execution_blocked:
            try:
                from tools.environments.base import set_activity_callback
                set_activity_callback(agent._touch_activity)
            except Exception:
                pass

        if not _execution_blocked and agent.tool_progress_callback:
            try:
                preview = _build_tool_preview(function_name, function_args)
                agent.tool_progress_callback("tool.started", function_name, preview, function_args)
            except Exception as cb_err:
                logging.debug(f"Tool progress callback error: {cb_err}")

        if not _execution_blocked and agent.tool_start_callback:
            try:
                agent.tool_start_callback(prepared.call_id, function_name, function_args)
            except Exception as cb_err:
                logging.debug(f"Tool start callback error: {cb_err}")

        spinner = None
        function_result = None
        tool_duration = 0.0
        if not _execution_blocked:
            should_emit = agent._should_emit_quiet_tool_messages()
            should_start = agent._should_start_quiet_spinner()
            is_context_engine_tool = prepared.adapter_name == "context_engine"
            is_memory_provider_tool = prepared.adapter_name == "memory_provider"
            should_show_spinner = (
                (function_name == "delegate_task" and should_emit and should_start)
                or (is_context_engine_tool and should_emit)
                or (is_memory_provider_tool and should_emit and should_start)
                or (
                    agent.quiet_mode
                    and should_emit
                    and should_start
                    and function_name
                    not in {"todo", "session_search", "memory", "clarify", "delegate_task"}
                    and not is_context_engine_tool
                    and not is_memory_provider_tool
                )
            )
            if should_show_spinner:
                face = random.choice(KawaiiSpinner.get_waiting_faces())
                if function_name == "delegate_task":
                    tasks_arg = function_args.get("tasks")
                    if tasks_arg and isinstance(tasks_arg, list):
                        spinner_label = f"🔀 delegating {len(tasks_arg)} tasks"
                    else:
                        goal_preview = (function_args.get("goal") or "")[:30]
                        spinner_label = f"🔀 {goal_preview}" if goal_preview else "🔀 delegating"
                    spinner_text = f"{face} {spinner_label}"
                else:
                    emoji = _get_tool_emoji(function_name)
                    preview = _build_tool_preview(function_name, function_args) or function_name
                    spinner_text = f"{face} {emoji} {preview}"
                spinner = KawaiiSpinner(
                    spinner_text,
                    spinner_type='dots',
                    print_fn=agent._print_fn,
                )
                spinner.start()
        if function_name == "delegate_task":
            agent._delegate_spinner = spinner
        try:
            invocation_result = _invoke_prepared_tool_core(
                agent,
                prepared,
            )
            function_result = invocation_result.content
            tool_duration = invocation_result.duration_seconds
        finally:
            if function_name == "delegate_task":
                agent._delegate_spinner = None
            if spinner:
                spinner.stop(
                    _get_cute_tool_message_impl(
                        function_name,
                        function_args,
                        tool_duration,
                        result=function_result,
                    )
                )
            elif agent._should_emit_quiet_tool_messages():
                agent._vprint(
                    f"  {_get_cute_tool_message_impl(function_name, function_args, tool_duration, result=function_result)}"
                )

        finalized = finalize_tool_result(
            agent,
            prepared.to_result_context(invocation_result, messages),
        )
        function_result = finalized.content

        if not agent.quiet_mode:
            if agent.verbose_logging:
                print(f"  ✅ Tool {i} completed in {tool_duration:.2f}s")
                print(agent._wrap_verbose("Result: ", function_result))
            else:
                _fr_str = function_result if isinstance(function_result, str) else str(function_result)
                response_preview = _fr_str[:agent.log_prefix_chars] + "..." if len(_fr_str) > agent.log_prefix_chars else _fr_str
                print(f"  ✅ Tool {i} completed in {tool_duration:.2f}s - {response_preview}")

        if agent._interrupt_requested and i < len(assistant_message.tool_calls):
            remaining = len(assistant_message.tool_calls) - i
            agent._vprint(f"{agent.log_prefix}⚡ Interrupt: skipping {remaining} remaining tool call(s)", force=True)
            for skipped_tc in assistant_message.tool_calls[i:]:
                messages.append(make_not_started_tool_result_message(skipped_tc.function.name, skipped_tc.id))
            break

        if agent.tool_delay > 0 and i < len(assistant_message.tool_calls):
            time.sleep(agent.tool_delay)

    # ── Per-turn aggregate budget enforcement ─────────────────────────
    num_tools_seq = len(assistant_message.tool_calls)
    finalize_tool_result_batch(agent, messages, num_tools_seq, effective_task_id)




__all__ = [
    "execute_tool_calls_concurrent",
    "execute_tool_calls_sequential",
]
