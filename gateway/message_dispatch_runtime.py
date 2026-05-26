"""Gateway inbound message dispatch runtime service."""
from __future__ import annotations

import dataclasses
import logging
import os
import time
from typing import Any, Optional

from agent.i18n import t
from gateway.config import Platform
from gateway.platforms.base import (
    EphemeralReply,
    MessageEvent,
    MessageType,
    merge_pending_message_event,
)

logger = logging.getLogger("gateway.run")


def _float_env(name: str, default: float) -> float:
    from gateway import run as _run

    return _run._float_env(name, default)


def _check_unavailable_skill(command_name: str) -> str | None:
    from gateway import run as _run

    return _run._check_unavailable_skill(command_name)


class GatewayMessageDispatchRuntime:
    def __init__(
        self,
        runner: Any,
        *,
        pending_sentinel: Any,
        hermes_home: Any,
        interrupt_reason_stop: str,
        interrupt_reason_reset: str,
    ) -> None:
        object.__setattr__(self, "_runner", runner)
        object.__setattr__(self, "_pending_sentinel", pending_sentinel)
        object.__setattr__(self, "_hermes_home", hermes_home)
        object.__setattr__(self, "_interrupt_reason_stop", interrupt_reason_stop)
        object.__setattr__(self, "_interrupt_reason_reset", interrupt_reason_reset)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {
            "_runner",
            "_pending_sentinel",
            "_hermes_home",
            "_interrupt_reason_stop",
            "_interrupt_reason_reset",
        }:
            object.__setattr__(self, name, value)
        else:
            setattr(self._runner, name, value)

    async def _handle_message(self, event: MessageEvent) -> Optional[str]:
        """
        Handle an incoming message from any platform.

        This is the core message processing pipeline:
        1. Check user authorization
        2. Check for commands (/new, /reset, etc.)
        3. Check for running agent and interrupt if needed
        4. Get or create session
        5. Build context for agent
        6. Run agent conversation
        7. Return response
        """
        source = event.source

        # Internal events (e.g. background-process completion notifications)
        # are system-generated and must skip user authorization.
        is_internal = bool(getattr(event, "internal", False))

        # Fire pre_gateway_dispatch plugin hook for user-originated messages.
        # Plugins receive the MessageEvent and may return a dict influencing flow:
        #   {"action": "skip",    "reason": ...}    -> drop (no reply, plugin handled)
        #   {"action": "rewrite", "text":  ...}     -> replace event.text, continue
        #   {"action": "allow"}   /   None          -> normal dispatch
        # Hook runs BEFORE auth so plugins can handle unauthorized senders
        # (e.g. customer handover ingest) without triggering the pairing flow.
        if not is_internal:
            try:
                from hermes_cli.plugins import invoke_hook as _invoke_hook
                _hook_results = _invoke_hook(
                    "pre_gateway_dispatch",
                    event=event,
                    gateway=self,
                    session_store=self.session_store,
                )
            except Exception as _hook_exc:
                logger.warning("pre_gateway_dispatch invocation failed: %s", _hook_exc)
                _hook_results = []

            for _result in _hook_results:
                if not isinstance(_result, dict):
                    continue
                _action = _result.get("action")
                if _action == "skip":
                    logger.info(
                        "pre_gateway_dispatch skip: reason=%s platform=%s chat=%s",
                        _result.get("reason"),
                        source.platform.value if source.platform else "unknown",
                        source.chat_id or "unknown",
                    )
                    return None
                if _action == "rewrite":
                    _new_text = _result.get("text")
                    if isinstance(_new_text, str):
                        event = dataclasses.replace(event, text=_new_text)
                        source = event.source
                    break
                if _action == "allow":
                    break

        if is_internal:
            pass
        elif source.user_id is None:
            # Messages with no user identity (Telegram service messages,
            # channel forwards, anonymous admin posts, sender_chat) can't
            # be paired, but they can still be authorized via a
            # chat-scoped allowlist (e.g. TELEGRAM_GROUP_ALLOWED_CHATS
            # authorizes every member of the listed chat regardless of
            # sender). Defer to _is_user_authorized so that path runs.
            if not self._is_user_authorized(source):
                logger.debug("Ignoring message with no user_id from %s", source.platform.value)
                return None
        elif not self._is_user_authorized(source):
            logger.warning("Unauthorized user: %s (%s) on %s", source.user_id, source.user_name, source.platform.value)
            # In DMs: offer pairing code. In groups: silently ignore.
            if source.chat_type == "dm" and self._get_unauthorized_dm_behavior(source.platform) == "pair":
                platform_name = source.platform.value if source.platform else "unknown"
                # Rate-limit ALL pairing responses (code or rejection) to
                # prevent spamming the user with repeated messages when
                # multiple DMs arrive in quick succession.
                if self.pairing_store._is_rate_limited(platform_name, source.user_id):
                    return None
                code = self.pairing_store.generate_code(
                    platform_name, source.user_id, source.user_name or ""
                )
                if code:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            f"Hi~ I don't recognize you yet!\n\n"
                            f"Here's your pairing code: `{code}`\n\n"
                            f"Ask the bot owner to run:\n"
                            f"`hermes pairing approve {platform_name} {code}`"
                        )
                else:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            "Too many pairing requests right now~ "
                            "Please try again later!"
                        )
                    # Record rate limit so subsequent messages are silently ignored
                    self.pairing_store._record_rate_limit(platform_name, source.user_id)
            return None

        # Intercept messages that are responses to a pending /update prompt.
        # The update process (detached) wrote .update_prompt.json; the watcher
        # forwarded it to the user; now the user's reply goes back via
        # .update_response so the update process can continue.
        #
        # IMPORTANT: recognized slash commands must bypass this interception.
        # Otherwise control/session commands like /new or /help get silently
        # consumed as update answers instead of being dispatched normally.
        _quick_key = self._session_key_for_source(source)
        _update_prompts = getattr(self, "_update_prompt_pending", {})
        if _update_prompts.get(_quick_key):
            raw = (event.text or "").strip()
            # Accept /approve and /deny as shorthand for yes/no
            cmd = event.get_command()
            if cmd in {"approve", "yes"}:
                response_text = "y"
            elif cmd in {"deny", "no"}:
                response_text = "n"
            else:
                _recognized_cmd = None
                if cmd:
                    try:
                        from hermes_cli.commands import resolve_command as _resolve_update_cmd
                    except Exception:
                        _resolve_update_cmd = None
                    if _resolve_update_cmd is not None:
                        try:
                            _cmd_def = _resolve_update_cmd(cmd)
                            _recognized_cmd = _cmd_def.name if _cmd_def else None
                        except Exception:
                            _recognized_cmd = None
                if _recognized_cmd:
                    response_text = ""
                else:
                    response_text = raw
            if response_text:
                response_path = self._hermes_home / ".update_response"
                prompt_path = self._hermes_home / ".update_prompt.json"
                try:
                    tmp = response_path.with_suffix(".tmp")
                    tmp.write_text(response_text)
                    tmp.replace(response_path)
                    prompt_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("Failed to write update response: %s", e)
                    return f"✗ Failed to send response to update process: {e}"
                _update_prompts.pop(_quick_key, None)
                label = response_text if len(response_text) <= 20 else response_text[:20] + "…"
                return f"✓ Sent `{label}` to the update process."
            # Recognized slash command during a pending update prompt:
            # unblock the detached update subprocess by writing a blank
            # response so ``_gateway_prompt`` returns the prompt's default
            # (typically a safe "n" / skip) and exits cleanly instead of
            # blocking on stdin until the 30-minute watcher timeout.
            # The slash command then falls through to normal dispatch.
            if _recognized_cmd:
                response_path = self._hermes_home / ".update_response"
                prompt_path = self._hermes_home / ".update_prompt.json"
                try:
                    tmp = response_path.with_suffix(".tmp")
                    tmp.write_text("")
                    tmp.replace(response_path)
                    prompt_path.unlink(missing_ok=True)
                    logger.info(
                        "Recognized /%s during pending update prompt for %s; "
                        "cancelled prompt with default and dispatching command",
                        _recognized_cmd,
                        _quick_key,
                    )
                except OSError as e:
                    logger.warning(
                        "Failed to write cancel response for pending update prompt: %s",
                        e,
                    )
                _update_prompts.pop(_quick_key, None)

        # Intercept messages that are responses to a pending clarify
        # request that is awaiting free-form text (either an open-ended
        # clarify with no choices, or one where the user picked the
        # "Other" button).  The first non-empty user message in the
        # session resolves the clarify and unblocks the agent thread —
        # we do NOT route it to the agent as a new turn.
        try:
            from tools import clarify_gateway as _clarify_mod
            _pending_clarify = _clarify_mod.get_pending_for_session(_quick_key)
        except Exception:
            _pending_clarify = None
        if _pending_clarify is not None:
            _raw_clarify_reply = (event.text or "").strip()
            # Skip slash commands — the user clearly wanted to issue a
            # command, not answer the clarify.  Leave the clarify pending
            # so the user can retry; if it times out, the agent unblocks
            # with an empty response.
            if _raw_clarify_reply and not _raw_clarify_reply.startswith("/"):
                _resolved = _clarify_mod.resolve_gateway_clarify(
                    _pending_clarify.clarify_id, _raw_clarify_reply,
                )
                if _resolved:
                    logger.info(
                        "Gateway intercepted clarify text response (session=%s, id=%s)",
                        _quick_key, _pending_clarify.clarify_id,
                    )
                    # Acknowledge with empty string so adapters that emit
                    # the agent's response don't double-post.  The agent
                    # itself will produce the next user-facing message.
                    return ""

        # Intercept messages that are responses to a pending /reload-mcp
        # (or future) slash-confirm prompt.  Recognized confirm replies are
        # /approve, /always, /cancel (plus short aliases).  Anything else
        # falls through to normal dispatch — a stale pending confirm does
        # NOT block other commands.
        #
        # Important: if a dangerous-command approval is ALSO pending (agent
        # blocked inside tools/approval.py), the tool approval takes
        # precedence — /approve there unblocks the waiting tool thread.
        # Slash-confirm only catches /approve when no tool approval is live.
        from gateway.message_router import route_pending_slash_confirm_reply

        _slash_confirm_result = await route_pending_slash_confirm_reply(
            session_key=_quick_key,
            raw_reply=event.text or "",
            command=event.get_command(),
        )
        if _slash_confirm_result is not None:
            return _slash_confirm_result.response

        # PRIORITY handling when an agent is already running for this session.
        # Default behavior is to interrupt immediately so user text/stop messages
        # are handled with minimal latency.
        #
        # Special case: Telegram/photo bursts often arrive as multiple near-
        # simultaneous updates. Do NOT interrupt for photo-only follow-ups here;
        # let the adapter-level batching/queueing logic absorb them.

        # Staleness eviction: detect leaked locks from hung/crashed handlers.
        # With inactivity-based timeout, active tasks can run for hours, so
        # wall-clock age alone isn't sufficient.  Evict only when the agent
        # has been *idle* beyond the inactivity threshold (or when the agent
        # object has no activity tracker and wall-clock age is extreme).
        _raw_stale_timeout = _float_env("HERMES_AGENT_TIMEOUT", 1800)
        _stale_ts = self._running_agents_ts.get(_quick_key, 0)
        if _quick_key in self._running_agents and _stale_ts:
            _stale_age = time.time() - _stale_ts
            _stale_agent = self._running_agents.get(_quick_key)
            # Never evict the pending sentinel — it was just placed moments
            # ago during the async setup phase before the real agent is
            # created.  Sentinels have no get_activity_summary(), so the
            # idle check below would always evaluate to inf >= timeout and
            # immediately evict them, racing with the setup path.
            _stale_idle = float("inf")  # assume idle if we can't check
            _stale_detail = ""
            if _stale_agent and hasattr(_stale_agent, "get_activity_summary"):
                try:
                    _sa = _stale_agent.get_activity_summary()
                    _stale_idle = _sa.get("seconds_since_activity", float("inf"))
                    _stale_detail = (
                        f" | last_activity={_sa.get('last_activity_desc', 'unknown')} "
                        f"({_stale_idle:.0f}s ago) "
                        f"| iteration={_sa.get('api_call_count', 0)}/{_sa.get('max_iterations', 0)}"
                    )
                except Exception:
                    pass
            # Evict if: agent is idle beyond timeout, OR wall-clock age is
            # extreme (10x timeout or 2h, whichever is larger — catches
            # cases where the agent object was garbage-collected).
            _wall_ttl = max(_raw_stale_timeout * 10, 7200) if _raw_stale_timeout > 0 else float("inf")
            _should_evict = (
                _stale_agent is not self._pending_sentinel
                and (
                    (_raw_stale_timeout > 0 and _stale_idle >= _raw_stale_timeout)
                    or _stale_age > _wall_ttl
                )
            )
            if _should_evict:
                logger.warning(
                    "Evicting stale _running_agents entry for %s "
                    "(age: %.0fs, idle: %.0fs, timeout: %.0fs)%s",
                    _quick_key, _stale_age, _stale_idle,
                    _raw_stale_timeout, _stale_detail,
                )
                self._invalidate_session_run_generation(
                    _quick_key,
                    reason="stale_running_agent_eviction",
                )
                self._release_running_agent_state(_quick_key)

        if _quick_key in self._running_agents:
            if event.get_command() == "status":
                return await self._handle_status_command(event)

            # Resolve the command once for all early-intercept checks below.
            from hermes_cli.commands import (
                ACTIVE_SESSION_BYPASS_COMMANDS as _DEDICATED_HANDLERS,
                resolve_command as _resolve_cmd_inner,
            )
            from gateway.message_router import (
                ACTIVE_SESSION_ACTION_AGENTS,
                ACTIVE_SESSION_ACTION_APPROVE,
                ACTIVE_SESSION_ACTION_BACKGROUND,
                ACTIVE_SESSION_ACTION_DEDICATED,
                ACTIVE_SESSION_ACTION_DENY,
                ACTIVE_SESSION_ACTION_GOAL,
                ACTIVE_SESSION_ACTION_KANBAN,
                ACTIVE_SESSION_ACTION_NEW,
                ACTIVE_SESSION_ACTION_QUEUE,
                ACTIVE_SESSION_ACTION_RESTART,
                ACTIVE_SESSION_ACTION_STEER,
                ACTIVE_SESSION_ACTION_STOP,
                ACTIVE_SESSION_ACTION_SUBGOAL,
                ACTIVE_SESSION_ACTION_VERBOSE,
                ACTIVE_SESSION_ACTION_YOLO,
                ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE,
                ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT,
                ACTIVE_SESSION_FOLLOWUP_INTERRUPT,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE,
                ACTIVE_SESSION_FOLLOWUP_STEER_BUSY,
                ACTIVE_SESSION_FOLLOWUP_STOP_PENDING,
                resolve_active_session_command_decision,
                resolve_active_session_followup_decision,
            )
            _evt_cmd = event.get_command()
            _cmd_def_inner = _resolve_cmd_inner(_evt_cmd) if _evt_cmd else None
            _cmd_name_inner = _cmd_def_inner.name if _cmd_def_inner else None
            _active_cmd_decision = resolve_active_session_command_decision(
                command_name=_cmd_name_inner,
                command_args=event.get_command_args(),
                dedicated_handlers=_DEDICATED_HANDLERS,
            )

            # Slash command access control on the running-agent fast-path.
            # Mirrors the cold-path gate further below so non-admin users
            # can't bypass gating just because an agent happens to be busy.
            # /status above is intentionally pre-gate so users always see
            # session state. /help and /whoami fall under the always-allowed
            # floor inside _check_slash_access.
            if _evt_cmd and _cmd_def_inner is not None:
                _denied = self._check_slash_access(source, _cmd_def_inner.name)
                if _denied is not None:
                    return _denied

            if _active_cmd_decision.response is not None:
                return _active_cmd_decision.response

            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_RESTART:
                return await self._handle_restart_command(event)

            # /stop must hard-kill the session when an agent is running.
            # A soft interrupt (agent.interrupt()) doesn't help when the agent
            # is truly hung — the executor thread is blocked and never checks
            # _interrupt_requested.  Force-clean _running_agents so the session
            # is unlocked and subsequent messages are processed normally.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_STOP:
                await self._interrupt_and_clear_session(
                    _quick_key,
                    source,
                    interrupt_reason=self._interrupt_reason_stop,
                    invalidation_reason="stop_command",
                )
                logger.info("STOP for session %s — agent interrupted, session lock released", _quick_key)
                return EphemeralReply(t("gateway.stop.stopped"))

            # /reset and /new must bypass the running-agent guard so they
            # actually dispatch as commands instead of being queued as user
            # text (which would be fed back to the agent with the same
            # broken history — #2170).  Interrupt the agent first, then
            # clear the adapter's pending queue so the stale "/reset" text
            # doesn't get re-processed as a user message after the
            # interrupt completes.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_NEW:
                # Clear any pending messages so the old text doesn't replay
                await self._interrupt_and_clear_session(
                    _quick_key,
                    source,
                    interrupt_reason=self._interrupt_reason_reset,
                    invalidation_reason="new_command",
                )
                # Clean up the running agent entry so the reset handler
                # doesn't think an agent is still active.
                return await self._handle_reset_command(event)

            # /queue <prompt> — queue without interrupting.
            # Semantics: each /queue invocation produces its own full agent
            # turn, processed in FIFO order after the current run (and any
            # earlier /queue items) finishes.  Messages are NOT merged.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_QUEUE:
                queued_text = event.get_command_args().strip()
                if not queued_text:
                    return "Usage: /queue <prompt>"
                adapter = self.adapters.get(source.platform)
                if adapter:
                    queued_event = MessageEvent(
                        text=queued_text,
                        message_type=MessageType.TEXT,
                        source=event.source,
                        message_id=event.message_id,
                        channel_prompt=event.channel_prompt,
                    )
                    self._enqueue_fifo(_quick_key, queued_event, adapter)
                depth = self._queue_depth(_quick_key, adapter=self.adapters.get(source.platform))
                if depth <= 1:
                    return "Queued for the next turn."
                return f"Queued for the next turn. ({depth} queued)"

            # /steer <prompt> — inject mid-run after the next tool call.
            # Unlike /queue (turn boundary), /steer lands BETWEEN tool-call
            # iterations inside the same agent run, by appending to the
            # last tool result's content. No interrupt, no new user turn,
            # no role-alternation violation.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_STEER:
                steer_text = event.get_command_args().strip()
                if not steer_text:
                    return "Usage: /steer <prompt>"
                running_agent = self._running_agents.get(_quick_key)
                if running_agent is self._pending_sentinel:
                    # Agent hasn't started yet — queue as turn-boundary fallback.
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        queued_event = MessageEvent(
                            text=steer_text,
                            message_type=MessageType.TEXT,
                            source=event.source,
                            message_id=event.message_id,
                            channel_prompt=event.channel_prompt,
                        )
                        adapter._pending_messages[_quick_key] = queued_event
                    return "Agent still starting — /steer queued for the next turn."
                if running_agent and hasattr(running_agent, "steer"):
                    try:
                        accepted = running_agent.steer(steer_text)
                    except Exception as exc:
                        logger.warning("Steer failed for session %s: %s", _quick_key, exc)
                        return f"⚠️ Steer failed: {exc}"
                    if accepted:
                        preview = steer_text[:60] + ("..." if len(steer_text) > 60 else "")
                        return f"⏩ Steer queued — arrives after the next tool call: '{preview}'"
                    return "Steer rejected (empty payload)."
                # Running agent is missing or lacks steer() — fall back to queue.
                adapter = self.adapters.get(source.platform)
                if adapter:
                    queued_event = MessageEvent(
                        text=steer_text,
                        message_type=MessageType.TEXT,
                        source=event.source,
                        message_id=event.message_id,
                        channel_prompt=event.channel_prompt,
                    )
                    adapter._pending_messages[_quick_key] = queued_event
                return "No active agent — /steer queued for the next turn."

            # /approve and /deny must bypass the running-agent interrupt path.
            # The agent thread is blocked on a threading.Event inside
            # tools/approval.py — sending an interrupt won't unblock it.
            # Route directly to the approval handler so the event is signalled.
            if _active_cmd_decision.action in {ACTIVE_SESSION_ACTION_APPROVE, ACTIVE_SESSION_ACTION_DENY}:
                if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_APPROVE:
                    return await self._handle_approve_command(event)
                return await self._handle_deny_command(event)

            # /agents (/tasks alias) should be query-only and never interrupt.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_AGENTS:
                return await self._handle_agents_command(event)

            # /background must bypass the running-agent guard — it starts a
            # parallel task and must never interrupt the active conversation.
            # /btw is an alias of /background and resolves to the same canonical
            # name, so this branch handles both commands.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_BACKGROUND:
                return await self._handle_background_command(event)

            # /kanban must bypass the guard. It writes to a profile-agnostic
            # DB (kanban.db), not to the running agent's state. In fact
            # /kanban unblock is often the only way to free a worker that
            # has blocked waiting for a peer — letting that be dispatched
            # mid-run is the whole point of the board.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_KANBAN:
                return await self._handle_kanban_command(event)

            # /goal is safe mid-run for status/pause/clear (inspection and
            # control-plane only — doesn't interrupt the running turn).
            # Setting a new goal text mid-run is rejected with the same
            # "wait or /stop" message as /model so we don't race a second
            # continuation prompt against the current turn.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_GOAL:
                return await self._handle_goal_command(event)

            # /subgoal is safe mid-run — it only modifies the goal's
            # subgoals list, which the judge reads at the next turn
            # boundary. No race with the running turn.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_SUBGOAL:
                return await self._handle_subgoal_command(event)

            # Session-level toggles that are safe to run mid-agent —
            # /yolo can unblock a pending approval prompt, /verbose cycles
            # the tool-progress display mode for the ongoing stream.
            # Both modify session state without needing agent interaction
            # and must not be queued (the safety net would discard them).
            # /fast and /reasoning are config-only and take effect next
            # message, so they fall through to the catch-all busy response
            # below — users should wait and set them between turns.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_YOLO:
                return await self._handle_yolo_command(event)
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_VERBOSE:
                return await self._handle_verbose_command(event)

            # Gateway-handled info/control commands with dedicated
            # running-agent handlers.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_DEDICATED:
                if _cmd_def_inner.name == "help":
                    return await self._handle_help_command(event)
                if _cmd_def_inner.name == "commands":
                    return await self._handle_commands_command(event)
                if _cmd_def_inner.name == "profile":
                    return await self._handle_profile_command(event)
                if _cmd_def_inner.name == "update":
                    return await self._handle_update_command(event)

            # Any other recognized slash command already returned the
            # MessageRouter catch-all response above. Plain text and media
            # follow-ups continue into the active-session queue/interrupt path.
            _telegram_followup_grace = float(
                os.getenv("HERMES_TELEGRAM_FOLLOWUP_GRACE_SECONDS", "3.0")
            )
            _started_at = self._running_agents_ts.get(_quick_key, 0)
            _now = time.time()
            running_agent = self._running_agents.get(_quick_key)
            _queue_during_drain = (
                self._queue_during_drain_enabled() if self._draining else False
            )
            _followup_decision = resolve_active_session_followup_decision(
                platform=source.platform,
                message_type=event.message_type,
                command=event.get_command(),
                started_at=_started_at,
                now=_now,
                telegram_followup_grace_seconds=_telegram_followup_grace,
                running_agent_is_pending=running_agent is self._pending_sentinel,
                draining=self._draining,
                queue_during_drain=_queue_during_drain,
                busy_input_mode=self._busy_input_mode,
            )

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO:
                logger.debug("PRIORITY photo follow-up for session %s — queueing without interrupt", _quick_key)
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(adapter._pending_messages, _quick_key, event)
                return None

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE:
                logger.debug(
                    "Telegram follow-up arrived %.2fs after run start for %s — queueing without interrupt",
                    _now - _started_at,
                    _quick_key,
                )
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(
                        adapter._pending_messages,
                        _quick_key,
                        event,
                        merge_text=True,
                    )
                return None

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_STOP_PENDING:
                self._release_running_agent_state(_quick_key)
                logger.info("HARD STOP (pending) for session %s — sentinel cleared", _quick_key)
                return EphemeralReply("⚡ Force-stopped. The agent was still starting — session unlocked.")

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING:
                # Agent is being set up but not ready yet.
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(
                        adapter._pending_messages,
                        _quick_key,
                        event,
                        merge_text=True,
                    )
                return None

            if _followup_decision.action in {
                ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE,
                ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT,
            }:
                if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE:
                    self._queue_or_replace_pending_event(_quick_key, event)
                return (
                    f"⏳ Gateway {self._status_action_gerund()} — queued for the next turn after it comes back."
                    if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE
                    else f"⏳ Gateway is {self._status_action_gerund()} and is not accepting another turn right now."
                )

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY:
                logger.debug("PRIORITY queue follow-up for session %s", _quick_key)
                self._queue_or_replace_pending_event(_quick_key, event)
                return None

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_STEER_BUSY:
                # Steer mode: inject text into the running agent mid-run via
                # agent.steer().  Falls back to queue semantics if the payload
                # is empty, the agent lacks steer(), or steer() rejects.
                steer_text = (event.text or "").strip()
                steered = False
                if steer_text and hasattr(running_agent, "steer"):
                    try:
                        steered = bool(running_agent.steer(steer_text))
                    except Exception as exc:
                        logger.warning("PRIORITY steer failed for session %s: %s", _quick_key, exc)
                        steered = False
                if steered:
                    logger.debug("PRIORITY steer for session %s", _quick_key)
                    return None
                logger.debug("PRIORITY steer-fallback-to-queue for session %s", _quick_key)
                self._queue_or_replace_pending_event(_quick_key, event)
                return None

            if _followup_decision.action != ACTIVE_SESSION_FOLLOWUP_INTERRUPT:
                logger.debug(
                    "Unknown active-session follow-up action %s for %s; interrupting",
                    _followup_decision.action,
                    _quick_key,
                )
            logger.debug("PRIORITY interrupt for session %s", _quick_key)
            running_agent.interrupt(event.text)
            # NOTE: self._pending_messages was write-only (never consumed).
            # The actual interrupt message is delivered via adapter._pending_messages
            # which is read by _run_agent. Removed to prevent unbounded growth.
            return None

        # Check for commands
        command = event.get_command()

        from hermes_cli.commands import (
            CommandSurface,
            is_gateway_known_command,
            resolve_command_invocation,
            resolve_plugin_command_dispatch,
        )
        from gateway.cold_command_router import (
            COMMAND_HOOK_DENY,
            COMMAND_HOOK_HANDLED,
            COMMAND_HOOK_REWRITE,
            build_bundle_invocation,
            build_skill_invocation_decision,
            execute_plugin_command,
            execute_quick_command,
            resolve_builtin_precedence_quick_alias,
            resolve_cold_command_dispatch,
            resolve_command_hook_decision,
            should_return_unknown_slash_command,
            unavailable_gateway_command_response,
            unknown_slash_command_response,
        )

        # Resolve aliases to canonical handler keys through the shared seam so
        # dispatch, hooks, and access checks do not depend on the typed alias.
        _cmd_invocation = (
            resolve_command_invocation(
                name=command,
                args=event.get_command_args().strip(),
                surface=CommandSurface.GATEWAY,
            )
            if command
            else None
        )
        canonical = _cmd_invocation.canonical_name if _cmd_invocation else command

        # Expand alias quick commands before built-in dispatch so targets like
        # /model openai/gpt-5.5 --provider openrouter reach the /model handler.
        # Preserve built-in precedence; aliases only need early handling when
        # the typed command is not already known.
        _alias_rewrite = resolve_builtin_precedence_quick_alias(
            config=self.config,
            command=command,
            command_args=event.get_command_args().strip(),
        )
        if _alias_rewrite is not None:
            event.text = _alias_rewrite.text
            command = _alias_rewrite.command
            _cmd_invocation = (
                resolve_command_invocation(
                    name=command,
                    args=event.get_command_args().strip(),
                    surface=CommandSurface.GATEWAY,
                )
                if command
                else None
            )
            canonical = _cmd_invocation.canonical_name if _cmd_invocation else command

        # Per-platform slash command access control. Only kicks in when the
        # operator has set ``allow_admin_from`` for the source's scope (DM
        # vs group). When unset → backward-compat: every allowed user can
        # run every command. When set → non-admins can run only commands in
        # ``user_allowed_commands`` (plus the always-allowed floor: /help,
        # /whoami). Plain chat is unaffected — only slash commands gate.
        if command:
            _hook_dispatch = resolve_plugin_command_dispatch(
                name=command,
                args=event.get_command_args().strip(),
                surface=CommandSurface.GATEWAY,
            )
            if _hook_dispatch.route == "plugin":
                canonical = _hook_dispatch.handler_key

        if command and canonical and is_gateway_known_command(canonical):
            _denied = self._check_slash_access(source, canonical)
            if _denied is not None:
                return _denied

        # Fire the ``command:<canonical>`` hook for any recognized slash
        # command — built-in OR plugin-registered. Handlers can return a
        # dict with ``{"decision": "deny" | "handled" | "rewrite", ...}``
        # to intercept dispatch before core handling runs. This replaces
        # the previous fire-and-forget emit(): return values are now
        # honored, but handlers that return nothing behave exactly as
        # before (telemetry-style hooks keep working).
        if command and is_gateway_known_command(canonical):
            raw_args = event.get_command_args().strip()
            hook_ctx = {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "command": canonical,
                "raw_command": command,
                "args": raw_args,
                "raw_args": raw_args,
            }
            try:
                hook_results = await self.hooks.emit_collect(
                    f"command:{canonical}", hook_ctx
                )
            except Exception as _hook_err:
                logger.debug(
                    "command:%s hook dispatch failed (non-fatal): %s",
                    canonical, _hook_err,
                )
                hook_results = []

            hook_decision = resolve_command_hook_decision(
                command=command,
                hook_results=hook_results,
            )
            if hook_decision.action == COMMAND_HOOK_DENY:
                return hook_decision.response
            if hook_decision.action == COMMAND_HOOK_HANDLED:
                return hook_decision.response
            if hook_decision.action == COMMAND_HOOK_REWRITE:
                event.text = f"/{hook_decision.command_name} {hook_decision.raw_args}".strip()
                command = event.get_command()
                if command:
                    _cmd_invocation = (
                        resolve_command_invocation(
                            name=command,
                            args=hook_decision.raw_args,
                            surface=CommandSurface.GATEWAY,
                        )
                    )
                else:
                    _cmd_invocation = None
                canonical = (
                    _cmd_invocation.canonical_name
                    if _cmd_invocation
                    else command
                )
                if command:
                    _hook_dispatch = resolve_plugin_command_dispatch(
                        name=command,
                        args=hook_decision.raw_args,
                        surface=CommandSurface.GATEWAY,
                    )
                    if _hook_dispatch.route == "plugin":
                        canonical = _hook_dispatch.handler_key

        from gateway.command_registry import (
            get_gateway_command_handler,
            resolve_special_cold_command,
        )

        _special_telegram_root_lobby = (
            self._is_telegram_topic_root_lobby(source) if canonical == "new" else False
        )
        _topic_new_uses_reset_handler = (
            canonical == "new"
            and not _special_telegram_root_lobby
            and getattr(source, "platform", None) == Platform.TELEGRAM
            and bool(getattr(source, "thread_id", None))
        )
        if _topic_new_uses_reset_handler:
            return await self._handle_reset_command(event)
        _reset_override = vars(self).get("_handle_reset_command")
        if canonical == "new" and callable(_reset_override):
            return await _reset_override(event)
        else:
            special_command_decision = resolve_special_cold_command(
                canonical,
                command_args=event.get_command_args().strip(),
                telegram_root_lobby=_special_telegram_root_lobby,
                telegram_root_new_message=(
                    self._telegram_topic_root_new_message()
                    if _special_telegram_root_lobby
                    else ""
                ),
            )
        if special_command_decision is not None:
            if special_command_decision.response is not None:
                return special_command_decision.response
            if special_command_decision.rewrite_text is not None:
                try:
                    event.text = special_command_decision.rewrite_text
                except Exception:
                    pass
            if special_command_decision.confirm_command is not None:
                async def _do_special_command():
                    if special_command_decision.confirm_command == "new":
                        return await self._handle_reset_command(event)
                    if special_command_decision.confirm_command == "undo":
                        return await self._handle_undo_command(event)
                    return None

                return await self._maybe_confirm_destructive_slash(
                    event=event,
                    command=special_command_decision.confirm_command,
                    title=special_command_decision.confirm_title or "",
                    detail=special_command_decision.confirm_detail or "",
                    execute=_do_special_command,
                )

        gateway_handler = get_gateway_command_handler(
            self._gateway_commands.handler_map(),
            canonical,
        )
        if gateway_handler is not None:
            return await gateway_handler(event)

        if self._draining:
            return f"⏳ Gateway is {self._status_action_gerund()} and is not accepting new work right now."

        _cold_dispatch = resolve_cold_command_dispatch(
            config=self.config,
            command=command,
            command_args=event.get_command_args().strip(),
        )
        quick_commands = _cold_dispatch.quick_commands if _cold_dispatch else {}
        skill_cmds = _cold_dispatch.skill_commands if _cold_dispatch else {}
        command_dispatch = _cold_dispatch.command_dispatch if _cold_dispatch else None

        if command:
            if command_dispatch and command_dispatch.route == "quick_exec":
                qcmd = quick_commands.get(command_dispatch.handler_key, {})
                return await execute_quick_command(
                    command_name=command,
                    exec_cmd=qcmd.get("command", ""),
                    env=os.environ.copy(),
                )
            if command_dispatch and command_dispatch.route == "quick_alias":
                target = (command_dispatch.target or "").strip()
                if target:
                    target = target if target.startswith("/") else f"/{target}"
                    target_command = target.lstrip("/")
                    user_args = event.get_command_args().strip()
                    event.text = f"{target} {user_args}".strip()
                    command = (
                        target_command.split()[0]
                        if target_command
                        else target_command
                    )
                    _cold_dispatch = resolve_cold_command_dispatch(
                        config={},
                        command=command,
                        command_args=user_args,
                        skill_commands_provider=lambda: skill_cmds,
                    )
                    command_dispatch = (
                        _cold_dispatch.command_dispatch
                        if _cold_dispatch
                        else None
                    )
                else:
                    return f"Quick command '/{command}' has no target defined."
            elif command_dispatch and command_dispatch.route == "quick_unsupported":
                return (
                    f"Quick command '/{command}' has unsupported type "
                    "(supported: 'exec', 'alias')."
                )
            elif command_dispatch and command_dispatch.route == "unavailable":
                return unavailable_gateway_command_response(
                    command_dispatch.invocation.canonical_name
                )

        # Plugin-registered slash commands
        if command and command_dispatch and command_dispatch.route == "plugin":
            try:
                result = await execute_plugin_command(
                    handler_key=command_dispatch.handler_key,
                    raw_args=event.get_command_args().strip(),
                )
                return result
            except Exception as e:
                logger.debug("Plugin command dispatch failed (non-fatal): %s", e)

        # Skill slash commands: /skill-name loads the skill and sends to agent.
        # resolve_skill_command_key() handles the Telegram underscore/hyphen
        # round-trip so /claude_code from Telegram autocomplete still resolves
        # to the claude-code skill.
        if command and command_dispatch and command_dispatch.route == "skill_bundle":
            # Skill bundles take precedence over individual skill commands —
            # /<bundle> loads multiple skills at once. Mirrors CLI dispatch.
            _bundle_handled = False
            try:
                bundle_result = build_bundle_invocation(
                    bundle_key=command_dispatch.handler_slash_key,
                    user_instruction=event.get_command_args().strip(),
                    task_id=_quick_key,
                )
                if bundle_result:
                    event.text = bundle_result.message
                    _bundle_handled = True
                    if bundle_result.missing:
                        logger.info(
                            "Bundle %s skipped missing skills: %s",
                            command_dispatch.handler_slash_key,
                            ", ".join(bundle_result.missing),
                        )
                    # Fall through to normal message processing with bundle
                    # content.
            except Exception as exc:
                logger.debug("Bundle dispatch failed (non-fatal): %s", exc)

        if (
            command
            and command_dispatch
            and command_dispatch.route in {"skill", "unknown"}
            and not locals().get("_bundle_handled", False)
        ):
            try:
                skill_decision = build_skill_invocation_decision(
                    command_dispatch=command_dispatch,
                    command=command,
                    skill_commands=skill_cmds,
                    platform_value=source.platform.value if source.platform else None,
                    user_instruction=event.get_command_args().strip(),
                    task_id=_quick_key,
                    unavailable_skill_checker=_check_unavailable_skill,
                    known_command_checker=is_gateway_known_command,
                )
                if skill_decision.response is not None:
                    if skill_decision.response.startswith("Unknown command"):
                        logger.warning(
                            "Unrecognized slash command /%s from %s — "
                            "replying with unknown-command notice",
                            command,
                            source.platform.value if source.platform else "?",
                        )
                    return skill_decision.response
                if skill_decision.message:
                    event.text = skill_decision.message
                    # Fall through to normal message processing with skill content
            except Exception as e:
                logger.debug("Skill command check failed (non-fatal): %s", e)

        # Pending exec approvals are handled by /approve and /deny commands above.
        # No bare text matching — "yes" in normal conversation must not trigger
        # execution of a dangerous command.

        if self._is_telegram_topic_root_lobby(source):
            # Debounce the lobby reminder so a user who forgets about
            # topic mode and fires ten prompts doesn't get ten copies.
            if self._should_send_telegram_lobby_reminder(source):
                return self._telegram_topic_root_lobby_message()
            return None

        # ── Claim this session before any await ───────────────────────
        # Between here and _run_agent registering the real AIAgent, there
        # are numerous await points (hooks, vision enrichment, STT,
        # session hygiene compression).  Without this sentinel a second
        # message arriving during any of those yields would pass the
        # "already running" guard and spin up a duplicate agent for the
        # same session — corrupting the transcript.
        self._running_agents[_quick_key] = self._pending_sentinel
        self._running_agents_ts[_quick_key] = time.time()
        _run_generation = self._begin_session_run_generation(_quick_key)

        try:
            _agent_result = await self._handle_message_with_agent(event, source, _quick_key, _run_generation)
            # Goal continuation: after the agent returns a final response
            # for this turn, check any standing /goal — the judge will
            # either mark it done, pause it (budget), or enqueue a
            # continuation prompt back through the adapter FIFO so the
            # next turn makes more progress. Wrapped in try/except so a
            # broken judge never breaks normal message handling.
            try:
                _final_text = ""
                if isinstance(_agent_result, dict):
                    _final_text = str(_agent_result.get("final_response") or "")
                elif isinstance(_agent_result, str):
                    _final_text = _agent_result
                # Skip for empty responses (interrupted / errored) — the
                # judge would almost always say "continue" and we'd loop
                # on error. Let the user drive the next turn.
                if _final_text.strip():
                    try:
                        session_entry = self.session_store.get_or_create_session(source)
                    except Exception:
                        session_entry = None
                    if session_entry is not None:
                        await self._post_turn_goal_continuation(
                            session_entry=session_entry,
                            source=source,
                            final_response=_final_text,
                        )
            except Exception as _goal_exc:
                logger.debug("goal continuation hook failed: %s", _goal_exc)
            return _agent_result
        finally:
            # If _run_agent replaced the sentinel with a real agent and
            # then cleaned it up, this is a no-op.  If we exited early
            # (exception, command fallthrough, etc.) the sentinel must
            # not linger or the session would be permanently locked out.
            if self._running_agents.get(_quick_key) is self._pending_sentinel:
                self._release_running_agent_state(_quick_key)
            else:
                # Agent path already cleaned _running_agents; make sure
                # the paired metadata dicts are gone too.
                self._running_agents_ts.pop(_quick_key, None)
                if hasattr(self, "_busy_ack_ts"):
                    self._busy_ack_ts.pop(_quick_key, None)
