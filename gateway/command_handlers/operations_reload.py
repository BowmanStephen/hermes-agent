"""Operations reload command handlers."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService

class OperationsReloadGatewayCommands(RunnerBackedCommandService):
    async def _handle_reload_mcp_command(self, event: MessageEvent) -> Optional[str]:
        """Handle /reload-mcp — reconnect MCP servers and rebuild the cached agent.

        Reloading MCP tools invalidates the provider prompt cache for the
        active session (tool schemas are baked into the system prompt).  The
        next message re-sends full input tokens, which is expensive on
        long-context or high-reasoning models.

        To surface that cost, the command routes through the slash-confirm
        primitive: users get an Approve Once / Always Approve / Cancel
        prompt before the reload actually runs.  "Always Approve" persists
        ``approvals.mcp_reload_confirm: false`` so the prompt is silenced
        for subsequent reloads in any session.

        Users can also skip the confirm by flipping the config key directly.
        """
        source = event.source
        session_key = self._session_key_for_source(source)

        # Read the gate fresh from disk so a prior "always" click takes
        # effect on the next invocation without restarting the gateway.
        user_config = self._read_user_config()
        approvals = user_config.get("approvals") if isinstance(user_config, dict) else None
        confirm_required = True
        if isinstance(approvals, dict):
            confirm_required = bool(approvals.get("mcp_reload_confirm", True))

        if not confirm_required:
            return await self._execute_mcp_reload(event)

        # Route through slash-confirm.  The primitive sends the prompt and
        # stores the resume handler; the button/text response triggers
        # ``_resolve_slash_confirm`` which invokes the handler with the
        # chosen outcome.
        async def _on_confirm(choice: str) -> Optional[str]:
            if choice == "cancel":
                return t("gateway.reload_mcp.cancelled")
            if choice == "always":
                # Persist the opt-out and run the reload.
                try:
                    from cli import save_config_value
                    save_config_value("approvals.mcp_reload_confirm", False)
                    logger.info(
                        "User opted out of /reload-mcp confirmation (session=%s)",
                        session_key,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist mcp_reload_confirm=false: %s", exc)
            # once / always → run the reload
            result = await self._execute_mcp_reload(event)
            if choice == "always":
                return f"{result}\n\n" + t("gateway.reload_mcp.always_followup")
            return result

        prompt_message = t("gateway.reload_mcp.confirm_prompt")
        return await self._request_slash_confirm(
            event=event,
            command="reload-mcp",
            title="/reload-mcp",
            message=prompt_message,
            handler=_on_confirm,
        )

    async def _execute_mcp_reload(self, event: MessageEvent) -> str:
        """Actually disconnect, reconnect, and notify MCP tool changes.

        Split out from ``_handle_reload_mcp_command`` so the confirmation
        wrapper can invoke the same path whether the user confirmed via
        button, text reply, or has the confirm gate disabled.
        """
        loop = asyncio.get_running_loop()
        try:
            from tools.mcp_tool import shutdown_mcp_servers, discover_mcp_tools, _servers, _lock

            # Capture old server names before shutdown
            with _lock:
                old_servers = set(_servers.keys())

            # Read new config before shutting down, so we know what will be added/removed
            # Shutdown existing connections
            await loop.run_in_executor(None, shutdown_mcp_servers)

            # Reconnect by discovering tools (reads config.yaml fresh)
            new_tools = await loop.run_in_executor(None, discover_mcp_tools)

            # Compute what changed
            with _lock:
                connected_servers = set(_servers.keys())

            added = connected_servers - old_servers
            removed = old_servers - connected_servers
            reconnected = connected_servers & old_servers

            lines = [t("gateway.reload_mcp.header")]
            if reconnected:
                lines.append(t("gateway.reload_mcp.reconnected", names=", ".join(sorted(reconnected))))
            if added:
                lines.append(t("gateway.reload_mcp.added", names=", ".join(sorted(added))))
            if removed:
                lines.append(t("gateway.reload_mcp.removed", names=", ".join(sorted(removed))))
            if not connected_servers:
                lines.append(t("gateway.reload_mcp.none_connected"))
            else:
                lines.append(t("gateway.reload_mcp.tools_available", tools=len(new_tools), servers=len(connected_servers)))

            # Inject a message at the END of the session history so the
            # model knows tools changed on its next turn.  Appended after
            # all existing messages to preserve prompt-cache for the prefix.
            change_parts = []
            if added:
                change_parts.append(f"Added servers: {', '.join(sorted(added))}")
            if removed:
                change_parts.append(f"Removed servers: {', '.join(sorted(removed))}")
            if reconnected:
                change_parts.append(f"Reconnected servers: {', '.join(sorted(reconnected))}")
            tool_summary = f"{len(new_tools)} MCP tool(s) now available" if new_tools else "No MCP tools available"
            change_detail = ". ".join(change_parts) + ". " if change_parts else ""
            reload_msg = {
                "role": "user",
                "content": f"[IMPORTANT: MCP servers have been reloaded. {change_detail}{tool_summary}. The tool list for this conversation has been updated accordingly.]",
            }
            try:
                session_entry = self.session_store.get_or_create_session(event.source)
                self.session_store.append_to_transcript(
                    session_entry.session_id, reload_msg
                )
            except Exception:
                pass  # Best-effort; don't fail the reload over a transcript write

            return "\n".join(lines)

        except Exception as e:
            logger.warning("MCP reload failed: %s", e)
            return t("gateway.reload_mcp.failed", error=e)

    async def _handle_reload_skills_command(self, event: MessageEvent) -> str:
        """Handle /reload-skills — rescan skills dir, queue a note for next turn.

        Skills don't need to be in the system prompt for the model to use
        them (they're invoked via ``/skill-name``, ``skills_list``, or
        ``skill_view`` at runtime), so this does NOT clear the prompt cache
        — prefix caching stays intact.

        If any skills were added or removed, a one-shot note is queued on
        ``self._pending_skills_reload_notes[session_key]``. The gateway
        prepends it to the NEXT user message in this session (see the
        consumer at ~L11025 in ``_run_agent_turn``), then clears it. Nothing
        is written to the session transcript out-of-band, so message
        alternation is preserved.
        """
        loop = asyncio.get_running_loop()
        try:
            from agent.skill_commands import reload_skills

            result = await loop.run_in_executor(None, reload_skills)
            added = result.get("added", [])      # [{"name", "description"}, ...]
            removed = result.get("removed", [])  # [{"name", "description"}, ...]
            total = result.get("total", 0)

            # Let each connected adapter refresh any platform-side state
            # that cached the skill list at startup. Today that's the
            # Discord /skill autocomplete (registered once per connect);
            # without this call, new skills stay invisible in the
            # dropdown and deleted skills error out when clicked. Other
            # adapters that don't override refresh_skill_group (Telegram's
            # BotCommand menu, Slack subcommand map, etc.) are silently
            # skipped — the in-process reload above is enough for them.
            for adapter in list(self.adapters.values()):
                refresh = getattr(adapter, "refresh_skill_group", None)
                if not callable(refresh):
                    continue
                try:
                    maybe = refresh()
                    if inspect.isawaitable(maybe):
                        await maybe
                except Exception as exc:
                    logger.warning(
                        "Adapter %s refresh_skill_group raised: %s",
                        getattr(adapter, "name", adapter), exc,
                    )

            lines = [t("gateway.reload_skills.header")]
            if not added and not removed:
                lines.append(t("gateway.reload_skills.no_new"))
                lines.append(t("gateway.reload_skills.total", count=total))
                return "\n".join(lines)

            def _fmt_line(item: dict) -> str:
                nm = item.get("name", "")
                desc = item.get("description", "")
                if desc:
                    return t("gateway.reload_skills.item_with_desc", name=nm, desc=desc)
                return t("gateway.reload_skills.item_no_desc", name=nm)

            if added:
                lines.append(t("gateway.reload_skills.added_header"))
                for item in added:
                    lines.append(_fmt_line(item))
            if removed:
                lines.append(t("gateway.reload_skills.removed_header"))
                for item in removed:
                    lines.append(_fmt_line(item))
            lines.append(t("gateway.reload_skills.total", count=total))

            # Queue the one-shot note for the next user turn in this session.
            # Format matches how the system prompt renders pre-existing
            # skills (``    - name: description``) so the model reads the
            # diff in the same shape as its original skill catalog.
            sections = ["[USER INITIATED SKILLS RELOAD:"]
            if added:
                sections.append("")
                sections.append("Added Skills:")
                for item in added:
                    sections.append(_fmt_line(item))
            if removed:
                sections.append("")
                sections.append("Removed Skills:")
                for item in removed:
                    sections.append(_fmt_line(item))
            sections.append("")
            sections.append("Use skills_list to see the updated catalog.]")
            note = "\n".join(sections)

            session_key = self._session_key_for_source(event.source)
            if not hasattr(self, "_pending_skills_reload_notes"):
                self._pending_skills_reload_notes = {}
            if session_key:
                self._pending_skills_reload_notes[session_key] = note

            return "\n".join(lines)

        except Exception as e:
            logger.warning("Skills reload failed: %s", e)
            return t("gateway.reload_skills.failed", error=e)

    async def _handle_bundles_command(self, event: MessageEvent) -> str:
        """Handle /bundles — list installed skill bundles.

        Mirrors the CLI ``/bundles`` handler. Returns a single text
        message suitable for any gateway adapter; bundles are loaded by
        invoking the bundle's own ``/<slug>`` command, not by this one.
        """
        try:
            from agent.skill_bundles import list_bundles, _bundles_dir
        except Exception as exc:
            logger.warning("Bundles command unavailable: %s", exc)
            return f"Bundles subsystem unavailable: {exc}"

        bundles = list_bundles()
        if not bundles:
            return (
                "No skill bundles installed.\n"
                "Create one on the host with:\n"
                "  `hermes bundles create <name> --skill <s1> --skill <s2>`\n"
                f"Directory: `{_bundles_dir()}`"
            )

        lines = [f"**Skill Bundles** ({len(bundles)} installed):", ""]
        for info in bundles:
            skill_count = len(info.get("skills", []))
            desc = info.get("description") or f"Load {skill_count} skills"
            lines.append(
                f"• `/{info['slug']}` — {desc} _({skill_count} skills)_"
            )
            for s in info.get("skills", []):
                lines.append(f"    · {s}")
        lines.append("")
        lines.append("Invoke a bundle with `/<slug>` to load all its skills.")
        return "\n".join(lines)
