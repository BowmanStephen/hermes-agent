"""Voice control command handlers."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService

class VoiceControlGatewayCommands(RunnerBackedCommandService):
    @staticmethod
    def _get_guild_id(event: MessageEvent) -> Optional[int]:
        """Extract Discord guild_id from the raw message object."""
        raw = getattr(event, "raw_message", None)
        if raw is None:
            return None
        # Slash command interaction
        if hasattr(raw, "guild_id") and raw.guild_id:
            return int(raw.guild_id)
        # Regular message
        if hasattr(raw, "guild") and raw.guild:
            return raw.guild.id
        return None

    async def _handle_voice_command(self, event: MessageEvent) -> str:
        """Handle /voice [on|off|tts|channel|leave|status] command."""
        args = event.get_command_args().strip().lower()
        chat_id = event.source.chat_id
        platform = event.source.platform
        voice_key = self._voice_key(platform, chat_id)

        adapter = self.adapters.get(platform)

        if args in {"on", "enable"}:
            self._voice_mode[voice_key] = "voice_only"
            self._save_voice_modes()
            if adapter:
                self._set_adapter_auto_tts_enabled(adapter, chat_id, enabled=True)
            return t("gateway.voice.enabled_voice_only")
        elif args in {"off", "disable"}:
            self._voice_mode[voice_key] = "off"
            self._save_voice_modes()
            if adapter:
                self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=True)
            return t("gateway.voice.disabled_text")
        elif args == "tts":
            self._voice_mode[voice_key] = "all"
            self._save_voice_modes()
            if adapter:
                self._set_adapter_auto_tts_enabled(adapter, chat_id, enabled=True)
            return t("gateway.voice.tts_enabled")
        elif args in {"channel", "join"}:
            return await self._handle_voice_channel_join(event)
        elif args == "leave":
            return await self._handle_voice_channel_leave(event)
        elif args == "status":
            mode = self._voice_mode.get(voice_key, "off")
            labels = {
                "off": t("gateway.voice.label_off"),
                "voice_only": t("gateway.voice.label_voice_only"),
                "all": t("gateway.voice.label_all"),
            }
            # Append voice channel info if connected
            adapter = self.adapters.get(event.source.platform)
            guild_id = self._get_guild_id(event)
            if guild_id and hasattr(adapter, "get_voice_channel_info"):
                info = adapter.get_voice_channel_info(guild_id)
                if info:
                    lines = [
                        t("gateway.voice.status_mode", label=labels.get(mode, mode)),
                        t("gateway.voice.status_channel", channel=info['channel_name']),
                        t("gateway.voice.status_participants", count=info['member_count']),
                    ]
                    for m in info["members"]:
                        status = t("gateway.voice.speaking") if m.get("is_speaking") else ""
                        lines.append(t("gateway.voice.status_member", name=m['display_name'], status=status))
                    return "\n".join(lines)
            return t("gateway.voice.status_mode", label=labels.get(mode, mode))
        else:
            # Toggle: off → on, on/all → off
            current = self._voice_mode.get(voice_key, "off")
            if current == "off":
                self._voice_mode[voice_key] = "voice_only"
                self._save_voice_modes()
                if adapter:
                    self._set_adapter_auto_tts_enabled(adapter, chat_id, enabled=True)
                return t("gateway.voice.enabled_short")
            else:
                self._voice_mode[voice_key] = "off"
                self._save_voice_modes()
                if adapter:
                    self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=True)
                return t("gateway.voice.disabled_short")

    async def _handle_voice_channel_join(self, event: MessageEvent) -> str:
        """Join the user's current Discord voice channel."""
        adapter = self.adapters.get(event.source.platform)
        if not hasattr(adapter, "join_voice_channel"):
            return "Voice channels are not supported on this platform."

        guild_id = self._get_guild_id(event)
        if not guild_id:
            return "This command only works in a Discord server."

        voice_channel = await adapter.get_user_voice_channel(
            guild_id, event.source.user_id
        )
        if not voice_channel:
            return "You need to be in a voice channel first."

        # Wire callbacks BEFORE join so voice input arriving immediately
        # after connection is not lost.
        if hasattr(adapter, "_voice_input_callback"):
            adapter._voice_input_callback = self._handle_voice_channel_input
        if hasattr(adapter, "_on_voice_disconnect"):
            adapter._on_voice_disconnect = self._handle_voice_timeout_cleanup

        try:
            success = await adapter.join_voice_channel(voice_channel)
        except Exception as e:
            logger.warning("Failed to join voice channel: %s", e)
            adapter._voice_input_callback = None
            err_lower = str(e).lower()
            if "pynacl" in err_lower or "nacl" in err_lower or "davey" in err_lower:
                return (
                    "Voice dependencies are missing (PyNaCl / davey). "
                    f"Install with: `{sys.executable} -m pip install PyNaCl`"
                )
            return f"Failed to join voice channel: {e}"

        if success:
            adapter._voice_text_channels[guild_id] = int(event.source.chat_id)
            if hasattr(adapter, "_voice_sources"):
                adapter._voice_sources[guild_id] = event.source.to_dict()
            self._voice_mode[self._voice_key(event.source.platform, event.source.chat_id)] = "all"
            self._save_voice_modes()
            self._set_adapter_auto_tts_enabled(adapter, event.source.chat_id, enabled=True)
            return (
                f"Joined voice channel **{voice_channel.name}**.\n"
                f"I'll speak my replies and listen to you. Use /voice leave to disconnect."
            )
        # Join failed — clear callback
        adapter._voice_input_callback = None
        return "Failed to join voice channel. Check bot permissions (Connect + Speak)."

    async def _handle_voice_channel_leave(self, event: MessageEvent) -> str:
        """Leave the Discord voice channel."""
        adapter = self.adapters.get(event.source.platform)
        guild_id = self._get_guild_id(event)

        if not guild_id or not hasattr(adapter, "leave_voice_channel"):
            return "Not in a voice channel."

        if not hasattr(adapter, "is_in_voice_channel") or not adapter.is_in_voice_channel(guild_id):
            return "Not in a voice channel."

        try:
            await adapter.leave_voice_channel(guild_id)
        except Exception as e:
            logger.warning("Error leaving voice channel: %s", e)
        # Always clean up state even if leave raised an exception
        self._voice_mode[self._voice_key(event.source.platform, event.source.chat_id)] = "off"
        self._save_voice_modes()
        self._set_adapter_auto_tts_disabled(adapter, event.source.chat_id, disabled=True)
        if hasattr(adapter, "_voice_input_callback"):
            adapter._voice_input_callback = None
        return "Left voice channel."

    def _handle_voice_timeout_cleanup(self, chat_id: str) -> None:
        """Called by the adapter when a voice channel times out.

        Cleans up runner-side voice_mode state that the adapter cannot reach.
        """
        self._voice_mode[self._voice_key(Platform.DISCORD, chat_id)] = "off"
        self._save_voice_modes()
        adapter = self.adapters.get(Platform.DISCORD)
        self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=True)
