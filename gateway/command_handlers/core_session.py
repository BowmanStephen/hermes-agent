"""Core session and help command handlers."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService

class CoreSessionGatewayCommands(RunnerBackedCommandService):
    async def _handle_reset_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        """Handle /new or /reset command."""
        source = event.source

        # Get existing session key
        session_key = self._session_key_for_source(source)
        self._invalidate_session_run_generation(session_key, reason="session_reset")

        # Snapshot the old entry so on_session_finalize can report the
        # expiring session id before reset_session() rotates it.
        old_entry = self.session_store._entries.get(session_key)

        # Close tool resources on the old agent (terminal sandboxes, browser
        # daemons, background processes) before evicting from cache.
        # Guard with getattr because test fixtures may skip __init__.
        _cache_lock = getattr(self, "_agent_cache_lock", None)
        if _cache_lock is not None:
            with _cache_lock:
                _cached = self._agent_cache.get(session_key)
                _old_agent = _cached[0] if isinstance(_cached, tuple) else _cached if _cached else None
            if _old_agent is not None:
                self._cleanup_agent_resources(_old_agent)
        self._evict_cached_agent(session_key)

        # Discard any /queue overflow for this session — /new is a
        # conversation-boundary operation, queued follow-ups from the
        # previous conversation must not bleed into the new one.
        _qe = getattr(self, "_queued_events", None)
        if _qe is not None:
            _qe.pop(session_key, None)

        try:
            from tools.env_passthrough import clear_env_passthrough
            clear_env_passthrough()
        except Exception:
            pass

        try:
            from tools.credential_files import clear_credential_files
            clear_credential_files()
        except Exception:
            pass

        # Reset the session
        new_entry = self.session_store.reset_session(session_key)

        # Clear any session-scoped model/reasoning overrides so the next agent
        # picks up configured defaults instead of previous session switches.
        self._session_model_overrides.pop(session_key, None)
        self._set_session_reasoning_override(session_key, None)
        if hasattr(self, "_pending_model_notes"):
            self._pending_model_notes.pop(session_key, None)

        # Clear session-scoped dangerous-command approvals and /yolo state.
        # /new is a conversation-boundary operation — approval state from the
        # previous conversation must not survive the reset.
        self._clear_session_boundary_security_state(session_key)

        # Fire plugin on_session_finalize hook (session boundary)
        try:
            from hermes_cli.plugins import invoke_hook as _invoke_hook
            _old_sid = old_entry.session_id if old_entry else None
            _invoke_hook("on_session_finalize", session_id=_old_sid,
                         platform=source.platform.value if source.platform else "")
        except Exception:
            pass

        # Emit session:end hook (session is ending)
        await self.hooks.emit("session:end", {
            "platform": source.platform.value if source.platform else "",
            "user_id": source.user_id,
            "session_key": session_key,
        })

        # Emit session:reset hook
        await self.hooks.emit("session:reset", {
            "platform": source.platform.value if source.platform else "",
            "user_id": source.user_id,
            "session_key": session_key,
        })

        # Resolve session config info to surface to the user
        try:
            session_info = self._format_session_info()
        except Exception:
            session_info = ""

        if new_entry:
            header = self._telegram_topic_new_header(source) or t("gateway.reset.header_default")
        else:
            # No existing session, just create one
            new_entry = self.session_store.get_or_create_session(source, force_new=True)
            header = self._telegram_topic_new_header(source) or t("gateway.reset.header_new")

        # Set session title if provided with /new <title>
        _title_arg = event.get_command_args().strip()
        _title_note = ""
        if _title_arg and self._session_db and new_entry:
            from hermes_state import SessionDB
            try:
                sanitized = SessionDB.sanitize_title(_title_arg)
            except ValueError as e:
                sanitized = None
                _title_note = t("gateway.reset.title_rejected", error=str(e))
            if sanitized:
                try:
                    self._session_db.set_session_title(new_entry.session_id, sanitized)
                    header = t("gateway.reset.header_titled", title=sanitized)
                except ValueError as e:
                    _title_note = t("gateway.reset.title_error_untitled", error=str(e))
                except Exception:
                    pass
            elif not _title_note:
                # sanitize_title returned empty (whitespace-only / unprintable)
                _title_note = t("gateway.reset.title_empty_untitled")
        header = header + _title_note

        # When /new runs inside a Telegram DM topic lane, rewrite the
        # (chat_id, thread_id) → session_id binding so the next message
        # uses the freshly-created session. Without this, the binding
        # still points at the old session and the binding-lookup at the
        # top of _handle_message_with_agent would switch right back.
        if self._is_telegram_topic_lane(source) and new_entry is not None:
            try:
                self._record_telegram_topic_binding(source, new_entry)
            except Exception:
                logger.debug("Failed to rebind Telegram topic after /new", exc_info=True)

        # Fire plugin on_session_reset hook (new session guaranteed to exist)
        try:
            from hermes_cli.plugins import invoke_hook as _invoke_hook
            _new_sid = new_entry.session_id if new_entry else None
            _invoke_hook("on_session_reset", session_id=_new_sid,
                         platform=source.platform.value if source.platform else "")
        except Exception:
            pass

        # Append a random tip to the reset message
        try:
            from hermes_cli.tips import get_random_tip
            _tip_line = t("gateway.reset.tip", tip=get_random_tip())
        except Exception:
            _tip_line = ""

        if session_info:
            return EphemeralReply(f"{header}\n\n{session_info}{_tip_line}")
        return EphemeralReply(f"{header}{_tip_line}")

    async def _handle_profile_command(self, event: MessageEvent) -> str:
        """Handle /profile — show active profile name and home directory."""
        from hermes_constants import display_hermes_home
        from hermes_cli.profiles import get_active_profile_name

        display = display_hermes_home()
        profile_name = get_active_profile_name()

        lines = [
            t("gateway.profile.header", profile=profile_name),
            t("gateway.profile.home", home=display),
        ]

        return "\n".join(lines)

    def _check_slash_access(
        self, source: SessionSource, canonical_cmd: str
    ) -> Optional[str]:
        """Return a denial message if ``source`` cannot run ``canonical_cmd``,
        else None. Used by both the cold and running-agent dispatch paths
        in ``_handle_message`` so admin/user gating can't be bypassed by
        an in-flight agent.

        Backward-compat semantics live in
        :func:`gateway.slash_access.policy_for_source` — when the operator
        hasn't set ``allow_admin_from`` for the scope, the policy returns
        ``enabled=False`` and this method always returns None.
        """
        from gateway.slash_access import policy_for_source as _policy_for_source

        if not canonical_cmd:
            return None
        policy = _policy_for_source(self.config, source)
        if not policy.enabled or policy.can_run(source.user_id, canonical_cmd):
            return None
        logger.info(
            "Slash command /%s denied for %s:%s (not admin, not in user_allowed_commands)",
            canonical_cmd,
            source.platform.value if source.platform else "?",
            source.user_id,
        )
        allowed_preview = sorted(policy.user_allowed_commands)
        if allowed_preview:
            suffix = (
                "You can run: "
                + ", ".join(f"/{c}" for c in allowed_preview[:12])
                + ("…" if len(allowed_preview) > 12 else "")
                + ". Use /whoami for the full list."
            )
        else:
            suffix = (
                "No slash commands are enabled for non-admins on this "
                "platform. Ask an admin to add you to allow_admin_from "
                "or to set user_allowed_commands."
            )
        return f"⛔ /{canonical_cmd} is admin-only here. {suffix}"

    async def _handle_whoami_command(self, event: MessageEvent) -> str:
        """Handle /whoami — show the user's slash command access on this scope.

        Always works (it's in the always-allowed floor of slash_access).
        Reports: platform, scope (DM vs group), the user's tier
        (admin / user / unrestricted), and the slash commands they can
        actually run on this scope.
        """
        from gateway.slash_access import policy_for_source as _policy_for_source

        source = event.source
        policy = _policy_for_source(self.config, source)
        platform = source.platform.value if source and source.platform else "?"
        chat_type = (source.chat_type if source else "") or "dm"
        scope = "DM" if chat_type.lower() in {"dm", "direct", "private", ""} else "group/channel"
        user_id = (source.user_id if source else None) or "?"

        if not policy.enabled:
            return (
                f"**You** — {platform} ({scope})\n"
                f"User ID: `{user_id}`\n"
                f"Tier: unrestricted (no admin list configured for this scope)\n"
                f"Slash commands: all available"
            )

        if policy.is_admin(user_id):
            return (
                f"**You** — {platform} ({scope})\n"
                f"User ID: `{user_id}`\n"
                f"Tier: **admin**\n"
                f"Slash commands: all available"
            )

        # Non-admin user. Show what's actually reachable.
        floor = ["help", "whoami"]  # mirrors slash_access._ALWAYS_ALLOWED_FOR_USERS
        configured = sorted(policy.user_allowed_commands)
        # Combine + dedupe, preserve order: floor first, then operator additions.
        seen: set[str] = set()
        runnable: list[str] = []
        for c in floor + configured:
            if c not in seen:
                seen.add(c)
                runnable.append(c)
        runnable_str = ", ".join(f"/{c}" for c in runnable) if runnable else "(none)"
        return (
            f"**You** — {platform} ({scope})\n"
            f"User ID: `{user_id}`\n"
            f"Tier: user\n"
            f"Slash commands you can run: {runnable_str}"
        )

    async def _handle_help_command(self, event: MessageEvent) -> str:
        """Handle /help command - list available commands."""
        from hermes_cli.commands import gateway_help_lines
        lines = [
            t("gateway.help.header"),
            *gateway_help_lines(),
        ]
        try:
            from agent.skill_commands import get_skill_commands
            skill_cmds = get_skill_commands()
            if skill_cmds:
                lines.append(t("gateway.help.skill_header", count=len(skill_cmds)))
                # Show first 10, then point to /commands for the rest
                sorted_cmds = sorted(skill_cmds)
                for cmd in sorted_cmds[:10]:
                    lines.append(f"`{cmd}` — {skill_cmds[cmd]['description']}")
                if len(sorted_cmds) > 10:
                    lines.append(t("gateway.help.more_use_commands", count=len(sorted_cmds) - 10))
        except Exception:
            pass
        return _telegramize_command_mentions(
            "\n".join(lines),
            getattr(getattr(event, "source", None), "platform", None),
        )

    async def _handle_commands_command(self, event: MessageEvent) -> str:
        from hermes_cli.commands import gateway_help_lines

        raw_args = event.get_command_args().strip()
        if raw_args:
            try:
                requested_page = int(raw_args)
            except ValueError:
                return t("gateway.commands.usage")
        else:
            requested_page = 1

        # Build combined entry list: built-in commands + skill commands
        entries = list(gateway_help_lines())
        try:
            from agent.skill_commands import get_skill_commands
            skill_cmds = get_skill_commands()
            if skill_cmds:
                entries.append("")
                entries.append(t("gateway.commands.skill_header"))
                for cmd in sorted(skill_cmds):
                    desc = skill_cmds[cmd].get("description", "").strip() or t("gateway.commands.default_desc")
                    entries.append(f"`{cmd}` — {desc}")
        except Exception:
            pass

        if not entries:
            return t("gateway.commands.none")

        from gateway.config import Platform
        page_size = 15 if event.source.platform == Platform.TELEGRAM else 20
        total_pages = max(1, (len(entries) + page_size - 1) // page_size)
        page = max(1, min(requested_page, total_pages))
        start = (page - 1) * page_size
        page_entries = entries[start:start + page_size]

        lines = [
            t("gateway.commands.header", total=len(entries), page=page, total_pages=total_pages),
            "",
            *page_entries,
        ]
        if total_pages > 1:
            nav_parts = []
            if page > 1:
                nav_parts.append(t("gateway.commands.nav_prev", page=page - 1))
            if page < total_pages:
                nav_parts.append(t("gateway.commands.nav_next", page=page + 1))
            lines.extend(["", " | ".join(nav_parts)])
        if page != requested_page:
            lines.append(t("gateway.commands.out_of_range", requested=requested_page, page=page))
        return _telegramize_command_mentions(
            "\n".join(lines),
            getattr(getattr(event, "source", None), "platform", None),
        )
