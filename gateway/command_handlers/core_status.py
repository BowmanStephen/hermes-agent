"""Core status command handlers."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService

class CoreStatusGatewayCommands(RunnerBackedCommandService):
    async def _handle_kanban_command(self, event: MessageEvent) -> str:
        """Handle /kanban — delegate to the shared kanban CLI.

        Run the potentially-blocking DB work in a thread pool so the
        gateway event loop stays responsive.  Read operations (list,
        show, context, tail) are permitted while an agent is running;
        mutations are allowed too because the board is profile-agnostic
        and does not touch the running agent's state.

        For ``/kanban create`` invocations we also auto-subscribe the
        originating gateway source (platform + chat + thread) to the new
        task's terminal events, so the user hears back when the worker
        completes / blocks / auto-blocks / crashes without having to poll.
        """
        import asyncio
        import re
        import shlex
        from hermes_cli.kanban import run_slash

        text = (event.text or "").strip()
        # Strip the leading "/kanban" (with or without slash), leaving args.
        if text.startswith("/"):
            text = text.lstrip("/")
        if text.startswith("kanban"):
            text = text[len("kanban"):].lstrip()

        tokens = shlex.split(text) if text else []
        requested_board = None
        action = None
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "--board":
                if i + 1 >= len(tokens):
                    break
                requested_board = tokens[i + 1]
                i += 2
                continue
            if tok.startswith("--board="):
                requested_board = tok.split("=", 1)[1]
                i += 1
                continue
            action = tok
            break

        is_create = action == "create"

        try:
            output = await asyncio.to_thread(run_slash, text)
        except Exception as exc:  # pragma: no cover - defensive
            return t("gateway.kanban.error_prefix", error=exc)

        # Auto-subscribe on create. Parse the task id from the CLI's standard
        # success line ("Created t_abcd  (ready, assignee=...)"). If the user
        # passed --json we don't subscribe; they're clearly scripting and
        # can call /kanban notify-subscribe explicitly.
        if is_create and output:
            m = re.search(r"Created\s+(t_[0-9a-f]+)\b", output)
            if m:
                task_id = m.group(1)
                try:
                    source = event.source
                    platform = getattr(source, "platform", None)
                    platform_str = (
                        platform.value if hasattr(platform, "value") else str(platform or "")
                    ).lower()
                    chat_id = str(getattr(source, "chat_id", "") or "")
                    thread_id = str(getattr(source, "thread_id", "") or "")
                    user_id = str(getattr(source, "user_id", "") or "") or None
                    if platform_str and chat_id:
                        def _sub():
                            from hermes_cli import kanban_db as _kb
                            conn = _kb.connect(board=requested_board)
                            try:
                                _kb.add_notify_sub(
                                    conn, task_id=task_id,
                                    platform=platform_str, chat_id=chat_id,
                                    thread_id=thread_id or None,
                                    user_id=user_id,
                                    notifier_profile=getattr(self, "_kanban_notifier_profile", None) or self._active_profile_name(),
                                )
                            finally:
                                conn.close()
                        await asyncio.to_thread(_sub)
                        output = (
                            output.rstrip()
                            + "\n"
                            + t("gateway.kanban.subscribed_suffix", task_id=task_id)
                        )
                except Exception as exc:
                    logger.warning("kanban create auto-subscribe failed: %s", exc)

        # Gateway messages have practical length caps; truncate long
        # listings to keep the UX reasonable.
        if len(output) > 3800:
            output = output[:3800] + "\n" + t("gateway.kanban.truncated_suffix")
        return output or t("gateway.kanban.no_output")

    async def _handle_status_command(self, event: MessageEvent) -> str:
        """Handle /status command."""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)

        connected_platforms = [p.value for p in self.adapters.keys()]

        # Check if there's an active agent
        session_key = session_entry.session_key
        is_running = session_key in self._running_agents

        # Count pending /queue follow-ups (slot + overflow).
        adapter = self.adapters.get(source.platform) if source else None
        queue_depth = self._queue_depth(session_key, adapter=adapter)

        title = None
        # Pull token totals from the SQLite session DB rather than the
        # in-memory SessionStore.  The agent's per-turn token deltas are
        # persisted into sessions_db (run_agent.py), not into SessionEntry,
        # so session_entry.total_tokens is always 0.  SessionDB is the
        # single source of truth; reading it here keeps /status accurate
        # without duplicating token writes into two stores.
        db_total_tokens = 0
        if self._session_db:
            try:
                title = self._session_db.get_session_title(session_entry.session_id)
            except Exception:
                title = None
            try:
                row = self._session_db.get_session(session_entry.session_id)
                if row:
                    db_total_tokens = (
                        (row.get("input_tokens") or 0)
                        + (row.get("output_tokens") or 0)
                        + (row.get("cache_read_tokens") or 0)
                        + (row.get("cache_write_tokens") or 0)
                        + (row.get("reasoning_tokens") or 0)
                    )
            except Exception:
                db_total_tokens = 0

        lines = [
            t("gateway.status.header"),
            "",
            t("gateway.status.session_id", session_id=session_entry.session_id),
        ]
        if title:
            lines.append(t("gateway.status.title", title=title))
        lines.extend([
            t("gateway.status.created", timestamp=session_entry.created_at.strftime('%Y-%m-%d %H:%M')),
            t("gateway.status.last_activity", timestamp=session_entry.updated_at.strftime('%Y-%m-%d %H:%M')),
            t("gateway.status.tokens", tokens=f"{db_total_tokens:,}"),
            t("gateway.status.agent_running", state=t("gateway.status.state_yes") if is_running else t("gateway.status.state_no")),
        ])
        if queue_depth:
            lines.append(t("gateway.status.queued", count=queue_depth))
        lines.extend([
            "",
            t("gateway.status.platforms", platforms=', '.join(connected_platforms)),
        ])

        # Session recap — what was this session ABOUT? Pure local compute,
        # no LLM call, no prompt-cache impact. Useful when juggling multiple
        # gateway sessions and you want a one-glance reminder of where this
        # one left off. Inspired by Claude Code 2.1.114's /recap.
        try:
            from hermes_cli.session_recap import build_recap
            history = self.session_store.load_transcript(session_entry.session_id)
            recap = build_recap(
                history,
                session_title=title,
                session_id=session_entry.session_id,
                platform=source.platform.value if source else None,
            )
            if recap:
                lines.extend(["", recap])
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("build_recap failed in /status: %s", exc)

        return "\n".join(lines)

    async def _handle_agents_command(self, event: MessageEvent) -> str:
        """Handle /agents command - list active agents and running tasks."""
        from tools.process_registry import format_uptime_short, process_registry

        now = time.time()
        current_session_key = self._session_key_for_source(event.source)

        running_agents: dict = getattr(self, "_running_agents", {}) or {}
        running_started: dict = getattr(self, "_running_agents_ts", {}) or {}

        agent_rows: list[dict] = []
        for session_key, agent in running_agents.items():
            started = float(running_started.get(session_key, now))
            elapsed = max(0, int(now - started))
            is_pending = agent is _AGENT_PENDING_SENTINEL
            agent_rows.append(
                {
                    "session_key": session_key,
                    "elapsed": elapsed,
                    "state": t("gateway.agents.state_starting") if is_pending else t("gateway.agents.state_running"),
                    "session_id": "" if is_pending else str(getattr(agent, "session_id", "") or ""),
                    "model": "" if is_pending else str(getattr(agent, "model", "") or ""),
                }
            )

        agent_rows.sort(key=lambda row: row["elapsed"], reverse=True)

        running_processes: list[dict] = []
        try:
            running_processes = [
                p for p in process_registry.list_sessions()
                if p.get("status") == "running"
            ]
        except Exception:
            running_processes = []

        background_tasks = [
            t for t in (getattr(self, "_background_tasks", set()) or set())
            if hasattr(t, "done") and not t.done()
        ]

        lines = [
            t("gateway.agents.header"),
            "",
            t("gateway.agents.active_agents", count=len(agent_rows)),
        ]

        if agent_rows:
            for idx, row in enumerate(agent_rows[:12], 1):
                current = t("gateway.agents.this_chat") if row["session_key"] == current_session_key else ""
                sid = f" · `{row['session_id']}`" if row["session_id"] else ""
                model = f" · `{row['model']}`" if row["model"] else ""
                lines.append(
                    f"{idx}. `{row['session_key']}` · {row['state']} · "
                    f"{format_uptime_short(row['elapsed'])}{sid}{model}{current}"
                )
            if len(agent_rows) > 12:
                lines.append(t("gateway.agents.more", count=len(agent_rows) - 12))

        lines.extend(
            [
                "",
                t("gateway.agents.running_processes", count=len(running_processes)),
            ]
        )
        if running_processes:
            for proc in running_processes[:12]:
                cmd = " ".join(str(proc.get("command", "")).split())
                if len(cmd) > 90:
                    cmd = cmd[:87] + "..."
                lines.append(
                    f"- `{proc.get('session_id', '?')}` · "
                    f"{format_uptime_short(int(proc.get('uptime_seconds', 0)))} · `{cmd}`"
                )
            if len(running_processes) > 12:
                lines.append(t("gateway.agents.more", count=len(running_processes) - 12))

        lines.extend(
            [
                "",
                t("gateway.agents.async_jobs", count=len(background_tasks)),
            ]
        )

        if not agent_rows and not running_processes and not background_tasks:
            lines.append("")
            lines.append(t("gateway.agents.none"))

        return "\n".join(lines)

    async def _handle_stop_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        """Handle /stop command - interrupt a running agent.

        When an agent is truly hung (blocked thread that never checks
        _interrupt_requested), the early intercept in _handle_message()
        handles /stop before this method is reached.  This handler fires
        only through normal command dispatch (no running agent) or as a
        fallback.  Force-clean the session lock in all cases for safety.

        The session is preserved so the user can continue the conversation.
        """
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        session_key = session_entry.session_key

        agent = self._running_agents.get(session_key)
        if agent is _AGENT_PENDING_SENTINEL:
            # Force-clean the sentinel so the session is unlocked.
            await self._interrupt_and_clear_session(
                session_key,
                source,
                interrupt_reason=_INTERRUPT_REASON_STOP,
                invalidation_reason="stop_command_pending",
            )
            logger.info("STOP (pending) for session %s — sentinel cleared", session_key)
            return EphemeralReply(t("gateway.stop.stopped_pending"))
        if agent:
            # Force-clean the session lock so a truly hung agent doesn't
            # keep it locked forever.
            await self._interrupt_and_clear_session(
                session_key,
                source,
                interrupt_reason=_INTERRUPT_REASON_STOP,
                invalidation_reason="stop_command_handler",
            )
            return EphemeralReply(t("gateway.stop.stopped"))
        else:
            return t("gateway.stop.no_active")
