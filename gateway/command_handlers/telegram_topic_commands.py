"""Telegram topic command handlers."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService

class TelegramTopicCommandGatewayCommands(RunnerBackedCommandService):
    def _telegram_topic_help_text(self) -> str:
        return telegram_topic_help_text()

    def _disable_telegram_topic_mode_for_chat(self, source: SessionSource) -> str:
        """Cleanly disable topic mode for a chat via /topic off."""
        from hermes_state import format_session_db_unavailable

        return disable_telegram_topic_mode_for_chat(
            source,
            self._session_db,
            lobby_reminder_ts=getattr(self, "_telegram_lobby_reminder_ts", None),
            capability_hint_ts=getattr(self, "_telegram_capability_hint_ts", None),
            unavailable_message=format_session_db_unavailable(
                prefix=t("gateway.shared.session_db_unavailable_prefix")
            ),
            logger=logger,
        )

    async def _handle_topic_command(self, event: MessageEvent, args: str = "") -> str:
        """Handle /topic for Telegram DM user-managed topic sessions."""
        source = event.source
        if source.platform != Platform.TELEGRAM or source.chat_type != "dm":
            return t("gateway.topic.not_telegram_dm")
        if not self._session_db:
            from hermes_state import format_session_db_unavailable
            return format_session_db_unavailable(prefix=t("gateway.shared.session_db_unavailable_prefix"))

        # Authorization: /topic activates multi-session mode and mutates
        # SQLite side tables. Unauthorized senders (not in allowlist) must
        # not be able to do that. Gateway routes already authorize the
        # message before reaching here, but defense in depth.
        auth_fn = getattr(self, "_is_user_authorized", None)
        if callable(auth_fn):
            try:
                if not auth_fn(source):
                    return t("gateway.topic.unauthorized")
            except Exception:
                logger.debug("Topic auth check failed", exc_info=True)

        args = event.get_command_args().strip()

        # /topic help — inline usage without leaving the bot.
        if args.lower() in {"help", "?", "-h", "--help"}:
            return self._telegram_topic_help_text()

        # /topic off — clean disable path so users don't have to edit the DB.
        if args.lower() in {"off", "disable", "stop"}:
            return self._disable_telegram_topic_mode_for_chat(source)

        if args:
            if not source.thread_id:
                return t("gateway.topic.restore_needs_topic")
            return await self._restore_telegram_topic_session(event, args)

        capabilities = await self._get_telegram_topic_capabilities(source)
        if capabilities.get("checked"):
            if capabilities.get("has_topics_enabled") is False:
                # Debounce the BotFather screenshot: don't re-send on every
                # /topic while threads are still disabled.
                if self._should_send_telegram_capability_hint(source):
                    await self._send_telegram_topic_setup_image(source)
                return t("gateway.topic.topics_disabled")
            if capabilities.get("allows_users_to_create_topics") is False:
                if self._should_send_telegram_capability_hint(source):
                    await self._send_telegram_topic_setup_image(source)
                return t("gateway.topic.topics_user_disallowed")

        try:
            self._session_db.enable_telegram_topic_mode(
                chat_id=str(source.chat_id),
                user_id=str(source.user_id),
                has_topics_enabled=capabilities.get("has_topics_enabled"),
                allows_users_to_create_topics=capabilities.get("allows_users_to_create_topics"),
            )
        except Exception as exc:
            logger.exception("Failed to enable Telegram topic mode")
            return t("gateway.topic.enable_failed", error=exc)

        if not source.thread_id:
            await self._ensure_telegram_system_topic(source)

        if source.thread_id:
            try:
                binding = self._session_db.get_telegram_topic_binding(
                    chat_id=str(source.chat_id),
                    thread_id=str(source.thread_id),
                )
            except Exception:
                logger.debug("Failed to read Telegram topic binding", exc_info=True)
                binding = None
            if binding:
                session_id = str(binding.get("session_id") or "")
                title = None
                try:
                    title = self._session_db.get_session_title(session_id)
                except Exception:
                    title = None
                session_label = title or t("gateway.topic.untitled_session")
                return t(
                    "gateway.topic.bound_status",
                    label=session_label,
                    session_id=session_id,
                )
            return t("gateway.topic.thread_ready")

        return self._telegram_topic_root_status_message(source)

    def _telegram_topic_root_status_message(self, source: SessionSource) -> str:
        return telegram_topic_root_status_message(
            source,
            self._session_db,
            logger=logger,
        )

    async def _restore_telegram_topic_session(self, event: MessageEvent, raw_session_id: str) -> str:
        """Restore an existing Telegram-owned Hermes session into this topic."""
        source = event.source
        session_id = self._session_db.resolve_session_id(raw_session_id.strip())
        if not session_id:
            return f"Session not found: {raw_session_id.strip()}"

        session = self._session_db.get_session(session_id)
        if not session:
            return f"Session not found: {raw_session_id.strip()}"
        if str(session.get("source") or "") != "telegram":
            return "That session is not a Telegram session and cannot be restored into this topic."
        if str(session.get("user_id") or "") != str(source.user_id):
            return "That session does not belong to this Telegram user."

        linked = self._session_db.is_telegram_session_linked_to_topic(session_id=session_id)
        current_binding = self._session_db.get_telegram_topic_binding(
            chat_id=str(source.chat_id),
            thread_id=str(source.thread_id),
        )
        if linked:
            if not current_binding or current_binding.get("session_id") != session_id:
                return "That session is already linked to another Telegram topic."

        session_key = self._session_key_for_source(source)
        try:
            self._session_db.bind_telegram_topic(
                chat_id=str(source.chat_id),
                thread_id=str(source.thread_id),
                user_id=str(source.user_id),
                session_key=session_key,
                session_id=session_id,
                managed_mode="restored",
            )
        except ValueError as exc:
            if "already linked" in str(exc):
                return "That session is already linked to another Telegram topic."
            raise

        title = self._session_db.get_session_title(session_id) or session_id
        last_assistant = None
        try:
            for message in reversed(self._session_db.get_messages(session_id)):
                if message.get("role") == "assistant" and message.get("content"):
                    last_assistant = str(message.get("content"))
                    break
        except Exception:
            last_assistant = None

        response = f"Session restored: {title}"
        if last_assistant:
            response += f"\n\nLast Hermes message:\n{last_assistant}"
        return response
