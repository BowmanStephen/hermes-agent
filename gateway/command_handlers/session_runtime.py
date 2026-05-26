"""Session runtime command handlers."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService

class SessionRuntimeGatewayCommands(RunnerBackedCommandService):
    async def _handle_background_command(self, event: MessageEvent) -> str:
        """Handle /background <prompt> — run a prompt in a separate background session.

        Spawns a new AIAgent in a background thread with its own session.
        When it completes, sends the result back to the same chat without
        modifying the active session's conversation history.
        """
        prompt = event.get_command_args().strip()
        if not prompt:
            return t("gateway.background.usage")

        source = event.source
        task_id = f"bg_{datetime.now().strftime('%H%M%S')}_{os.urandom(3).hex()}"

        event_message_id = self._reply_anchor_for_event(event)

        # Forward image/audio attachments so the background agent can see them.
        media_urls = list(event.media_urls) if event.media_urls else []
        media_types = list(event.media_types) if event.media_types else []

        # Fire-and-forget the background task
        runner_override = getattr(
            object.__getattribute__(self, "_runner"),
            "__dict__",
            {},
        ).get("_run_background_task")
        background_task = runner_override or self._run_background_task
        _task = asyncio.create_task(
            background_task(
                prompt,
                source,
                task_id,
                event_message_id=event_message_id,
                media_urls=media_urls,
                media_types=media_types,
            )
        )
        self._background_tasks.add(_task)
        _task.add_done_callback(self._background_tasks.discard)

        preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
        return t("gateway.background.started", preview=preview, task_id=task_id)

    async def _run_background_task(
        self,
        prompt: str,
        source: "SessionSource",
        task_id: str,
        event_message_id: Optional[str] = None,
        media_urls: Optional[List[str]] = None,
        media_types: Optional[List[str]] = None,
    ) -> None:
        """Execute a background agent task and deliver the result to the chat."""
        from hermes_cli.tools_config import _get_platform_tools
        await run_background_task(
            prompt=prompt,
            source=source,
            task_id=task_id,
            adapters=self.adapters,
            thread_metadata_for_source=self._thread_metadata_for_source,
            load_gateway_config=_load_gateway_config,
            resolve_session_agent_runtime=self._resolve_session_agent_runtime,
            platform_config_key=_platform_config_key,
            get_platform_tools=_get_platform_tools,
            provider_routing=self._provider_routing,
            resolve_session_reasoning_config=self._resolve_session_reasoning_config,
            set_reasoning_config=lambda value: setattr(self, "_reasoning_config", value),
            load_service_tier=self._load_service_tier,
            set_service_tier=lambda value: setattr(self, "_service_tier", value),
            resolve_turn_agent_config=self._resolve_turn_agent_config,
            enrich_message_with_vision=self._enrich_message_with_vision,
            run_in_executor_with_context=self._run_in_executor_with_context,
            cleanup_agent_resources=self._cleanup_agent_resources,
            session_db=self._session_db,
            fallback_model=self._fallback_model,
            event_message_id=event_message_id,
            media_urls=media_urls,
            media_types=media_types,
            log=logger,
        )

    async def _handle_resume_command(self, event: MessageEvent) -> str:
        """Handle /resume command — switch to a previously-named session."""
        if not self._session_db:
            from hermes_state import format_session_db_unavailable
            return format_session_db_unavailable(prefix=t("gateway.shared.session_db_unavailable_prefix"))

        source = event.source
        session_key = self._session_key_for_source(source)
        name = event.get_command_args().strip()

        if not name:
            # List recent titled sessions for this user/platform
            try:
                user_source = source.platform.value if source.platform else None
                sessions = self._session_db.list_sessions_rich(
                    source=user_source, limit=10
                )
                titled = [s for s in sessions if s.get("title")]
                if not titled:
                    return t("gateway.resume.no_named_sessions")
                lines = [t("gateway.resume.list_header")]
                for s in titled[:10]:
                    title = s["title"]
                    preview = s.get("preview", "")[:40]
                    preview_part = t("gateway.resume.list_preview_suffix", preview=preview) if preview else ""
                    lines.append(t("gateway.resume.list_item", title=title, preview_part=preview_part))
                lines.append(t("gateway.resume.list_footer"))
                return "\n".join(lines)
            except Exception as e:
                logger.debug("Failed to list titled sessions: %s", e)
                return t("gateway.resume.list_failed", error=e)

        # Resolve the name to a session ID.
        target_id = self._session_db.resolve_session_by_title(name)
        if not target_id:
            return t("gateway.resume.not_found", name=name)

        current_entry = self.session_store.get_or_create_session(source)
        try:
            from session_lifecycle import SessionNotFound, resume_session
            resume = resume_session(
                self._session_db,
                target_id,
                current_session_id=current_entry.session_id,
                end_current_reason="session_switch",
            )
        except SessionNotFound:
            return t("gateway.resume.not_found", name=name)
        target_id = resume.session_id

        # Check if already on that session
        if current_entry.session_id == target_id:
            return t("gateway.resume.already_on", name=name)

        # Clear any running agent for this session key
        self._release_running_agent_state(session_key)

        # Switch the session entry to point at the old session
        new_entry = self.session_store.switch_session(session_key, target_id)
        if not new_entry:
            return t("gateway.resume.switch_failed")
        self._clear_session_boundary_security_state(session_key)

        # Evict any cached agent for this session so the next message
        # rebuilds with the correct session_id end-to-end — mirrors
        # /branch and /reset. Without this, the cached AIAgent (and its
        # memory provider, which cached `_session_id` during initialize())
        # keeps writing into the wrong session's record. See #6672.
        self._evict_cached_agent(session_key)

        # Get the title for confirmation
        title = resume.title or name
        msg_count = resume.user_message_count
        if not msg_count:
            return t("gateway.resume.resumed_no_count", title=title)
        if msg_count == 1:
            return t("gateway.resume.resumed_one", title=title, count=msg_count)
        return t("gateway.resume.resumed_many", title=title, count=msg_count)

    async def _handle_branch_command(self, event: MessageEvent) -> str:
        """Handle /branch [name] — fork the current session into a new independent copy.

        Copies conversation history to a new session so the user can explore
        a different approach without losing the original.
        Inspired by Claude Code's /branch command.
        """
        if not self._session_db:
            from hermes_state import format_session_db_unavailable
            return format_session_db_unavailable(prefix=t("gateway.shared.session_db_unavailable_prefix"))

        source = event.source
        session_key = self._session_key_for_source(source)

        # Load the current session and its transcript
        current_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(current_entry.session_id)
        if not history:
            return t("gateway.branch.no_conversation")

        branch_name = event.get_command_args().strip()
        parent_session_id = current_entry.session_id

        try:
            from session_lifecycle import branch_session
            branch = branch_session(
                self._session_db,
                parent_session_id=parent_session_id,
                history=history,
                branch_title=branch_name or None,
                source=source.platform.value if source.platform else "gateway",
                model=(self.config.get("model", {}) or {}).get("default") if isinstance(self.config, dict) else None,
            )
        except Exception as e:
            logger.error("Failed to create branch session: %s", e)
            return t("gateway.branch.create_failed", error=e)

        # Switch the session store entry to the new session
        new_entry = self.session_store.switch_session(session_key, branch.session_id)
        if not new_entry:
            return t("gateway.branch.switch_failed")
        self._clear_session_boundary_security_state(session_key)

        # Evict any cached agent for this session
        self._evict_cached_agent(session_key)

        key = "gateway.branch.branched_one" if branch.user_message_count == 1 else "gateway.branch.branched_many"
        return t(key, title=branch.title, count=branch.user_message_count, parent=parent_session_id, new=branch.session_id)
