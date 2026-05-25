"""Inbound message text preparation for gateway agent turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
import logging
import os
import re
from typing import Any, Optional

from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource, is_shared_multi_user_session

logger = logging.getLogger(__name__)


async def prepare_inbound_message_text(
    *,
    event: MessageEvent,
    source: SessionSource,
    history: list[dict[str, Any]] | None,
    config: Any,
    session_key_for_source: Callable[[SessionSource], str],
    consume_pending_native_image_paths: Callable[[str], Any],
    decide_image_input_mode: Callable[[], str] | None = None,
    enrich_message_with_vision: Callable[[str, list[str]], Awaitable[str]] | None = None,
    enrich_message_with_transcription: Callable[[str, list[str]], Awaitable[str]] | None = None,
    adapters: MutableMapping[Any, Any] | None = None,
    thread_metadata_for_source: Callable[[SessionSource, Any], Any] | None = None,
    reply_anchor_for_event: Callable[[MessageEvent], Any] | None = None,
    has_setup_skill: Callable[[], bool] | None = None,
    model: str = "",
    base_url: str = "",
    resolve_runtime_agent_kwargs: Callable[[], dict[str, Any]] | None = None,
    load_gateway_config: Callable[[], dict[str, Any]] | None = None,
    pending_native_image_paths_by_session: dict[str, list[str]] | None = None,
    set_pending_native_image_paths: Callable[[dict[str, list[str]]], None] | None = None,
    logger_: logging.Logger | None = None,
) -> Optional[str]:
    """Prepare inbound event text for the agent path."""

    active_logger = logger_ or logger
    message_text = event.text or ""
    group_sessions_per_user = getattr(config, "group_sessions_per_user", True)
    thread_sessions_per_user = getattr(config, "thread_sessions_per_user", False)
    session_key = session_key_for_source(source)
    consume_pending_native_image_paths(session_key)

    is_shared_multi_user = is_shared_multi_user_session(
        source,
        group_sessions_per_user=group_sessions_per_user,
        thread_sessions_per_user=thread_sessions_per_user,
    )
    if is_shared_multi_user and source.user_name:
        message_text = f"[{source.user_name}] {message_text}"

    if getattr(event, "channel_context", None):
        message_text = f"{event.channel_context}\n\n[New message]\n{message_text}"

    audio_file_paths: list[str] = []

    if event.media_urls:
        image_paths = []
        audio_paths = []
        for i, path in enumerate(event.media_urls):
            mtype = event.media_types[i] if i < len(event.media_types) else ""
            if mtype.startswith("image/") or event.message_type == MessageType.PHOTO:
                image_paths.append(path)
            if event.message_type == MessageType.AUDIO:
                audio_file_paths.append(path)
            elif event.message_type == MessageType.VOICE or (
                mtype.startswith("audio/")
                and event.message_type not in {MessageType.AUDIO, MessageType.DOCUMENT}
            ):
                audio_paths.append(path)

        if image_paths:
            image_mode = decide_image_input_mode() if decide_image_input_mode else "text"
            if image_mode == "native":
                pending_native = pending_native_image_paths_by_session
                if pending_native is None:
                    pending_native = {}
                    if set_pending_native_image_paths is not None:
                        set_pending_native_image_paths(pending_native)
                pending_native[session_key] = list(image_paths)
                active_logger.info(
                    "Image routing: native (model supports vision). %d image(s) will be attached inline.",
                    len(image_paths),
                )
            else:
                active_logger.info(
                    "Image routing: text (mode=%s). Pre-analyzing %d image(s) via vision_analyze.",
                    image_mode,
                    len(image_paths),
                )
                if enrich_message_with_vision is not None:
                    message_text = await enrich_message_with_vision(
                        message_text,
                        image_paths,
                    )

        if audio_paths and enrich_message_with_transcription is not None:
            message_text = await enrich_message_with_transcription(
                message_text,
                audio_paths,
            )
            stt_fail_markers = (
                "No STT provider",
                "STT is disabled",
                "can't listen",
                "VOICE_TOOLS_OPENAI_KEY",
            )
            if any(marker in message_text for marker in stt_fail_markers):
                adapter = (adapters or {}).get(source.platform)
                reply_anchor = reply_anchor_for_event(event) if reply_anchor_for_event else None
                metadata = (
                    thread_metadata_for_source(source, reply_anchor)
                    if thread_metadata_for_source
                    else None
                )
                if adapter:
                    try:
                        stt_msg = (
                            "🎤 I received your voice message but can't transcribe it — "
                            "no speech-to-text provider is configured.\n\n"
                            "To enable voice: install faster-whisper "
                            "(`pip install faster-whisper` in the Hermes venv) "
                            "and set `stt.enabled: true` in config.yaml, "
                            "then /restart the gateway."
                        )
                        if has_setup_skill and has_setup_skill():
                            stt_msg += "\n\nFor full setup instructions, type: `/skill hermes-agent-setup`"
                        await adapter.send(
                            source.chat_id,
                            stt_msg,
                            metadata=metadata,
                        )
                    except Exception:
                        pass

    if audio_file_paths:
        from tools.credential_files import to_agent_visible_cache_path as to_agent_path

        for audio_path in audio_file_paths:
            basename = os.path.basename(audio_path)
            parts = basename.split("_", 2)
            display = parts[2] if len(parts) >= 3 else basename
            display = re.sub(r"[^\w.\- ]", "_", display)
            agent_path = to_agent_path(audio_path)
            note = (
                f"[The user sent an audio file attachment: '{display}'. "
                f"It is saved at: {agent_path}. "
                f"Ask the user what they'd like you to do with it, or pass the path to a transcription or media tool.]"
            )
            message_text = f"{note}\n\n{message_text}"

    if event.media_urls and event.message_type == MessageType.DOCUMENT:
        import mimetypes
        from tools.credential_files import to_agent_visible_cache_path

        text_extensions = {
            ".txt",
            ".md",
            ".csv",
            ".log",
            ".json",
            ".xml",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
        }
        for i, path in enumerate(event.media_urls):
            mtype = event.media_types[i] if i < len(event.media_types) else ""
            if mtype in {"", "application/octet-stream"}:
                ext = os.path.splitext(path)[1].lower()
                if ext in text_extensions:
                    mtype = "text/plain"
                else:
                    guessed, _ = mimetypes.guess_type(path)
                    if guessed:
                        mtype = guessed
            if not mtype.startswith(("application/", "text/")):
                continue

            basename = os.path.basename(path)
            parts = basename.split("_", 2)
            display_name = parts[2] if len(parts) >= 3 else basename
            display_name = re.sub(r"[^\w.\- ]", "_", display_name)
            agent_path = to_agent_visible_cache_path(path)

            if mtype.startswith("text/"):
                context_note = (
                    f"[The user sent a text document: '{display_name}'. "
                    f"Its content has been included below. "
                    f"The file is also saved at: {agent_path}]"
                )
            else:
                context_note = (
                    f"[The user sent a document: '{display_name}'. "
                    f"The file is saved at: {agent_path}. "
                    f"Ask the user what they'd like you to do with it.]"
                )
            message_text = f"{context_note}\n\n{message_text}"

    if getattr(event, "reply_to_text", None) and event.reply_to_message_id:
        reply_snippet = event.reply_to_text[:500]
        message_text = f'[Replying to: "{reply_snippet}"]\n\n{message_text}'

    if "@" in message_text:
        try:
            from agent.context_references import preprocess_context_references_async
            from agent.model_metadata import get_model_context_length

            cwd = os.environ.get("TERMINAL_CWD", os.path.expanduser("~"))
            runtime = resolve_runtime_agent_kwargs() if resolve_runtime_agent_kwargs else {}
            config_context_length = None
            try:
                gateway_config = load_gateway_config() if load_gateway_config else {}
                model_config = gateway_config.get("model", {})
                if isinstance(model_config, dict):
                    raw_context_length = model_config.get("context_length")
                    if raw_context_length is not None:
                        config_context_length = int(raw_context_length)
            except Exception:
                pass
            context_length = get_model_context_length(
                model,
                base_url=base_url or runtime.get("base_url") or "",
                api_key=runtime.get("api_key") or "",
                config_context_length=config_context_length,
            )
            context_result = await preprocess_context_references_async(
                message_text,
                cwd=cwd,
                context_length=context_length,
                allowed_root=cwd,
            )
            if context_result.blocked:
                adapter = (adapters or {}).get(source.platform)
                if adapter:
                    await adapter.send(
                        source.chat_id,
                        "\n".join(context_result.warnings) or "Context injection refused.",
                    )
                return None
            if context_result.expanded:
                message_text = context_result.message
        except Exception as exc:
            active_logger.debug("@ context reference expansion failed: %s", exc)

    return message_text
