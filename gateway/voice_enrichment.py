"""Voice/audio transcription enrichment helpers for GatewayRunner."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import Any


logger = logging.getLogger(__name__)

EMPTY_CONTENT_PLACEHOLDER = "(The user sent a message with no text content)"

Transcriber = Callable[[str], dict[str, Any]]
DurationProbe = Callable[[str], Awaitable[str | None]]
ToThread = Callable[..., Awaitable[Any]]


async def enrich_message_with_transcription(
    user_text: str,
    audio_paths: Sequence[str],
    *,
    stt_enabled: bool = True,
    probe_audio_duration: DurationProbe | None = None,
    transcribe_audio_fn: Transcriber | None = None,
    has_setup_skill: Callable[[], bool] | None = None,
    to_thread_fn: ToThread = asyncio.to_thread,
    logger: logging.Logger = logger,
) -> str:
    """Auto-transcribe voice/audio messages and prepend transcript context."""

    if not stt_enabled:
        notes = []
        for path in audio_paths:
            abs_path = os.path.abspath(path)
            duration_str = await probe_audio_duration(abs_path) if probe_audio_duration else None
            if duration_str:
                notes.append(
                    f"[The user sent a voice message: {abs_path} (duration: {duration_str})]"
                )
            else:
                notes.append(f"[The user sent a voice message: {abs_path}]")
        if not notes:
            return user_text
        prefix = "\n\n".join(notes)
        if user_text and user_text.strip() == EMPTY_CONTENT_PLACEHOLDER:
            return prefix
        if user_text:
            return f"{prefix}\n\n{user_text}"
        return prefix

    if transcribe_audio_fn is None:
        from tools.transcription_tools import transcribe_audio

        transcribe_audio_fn = transcribe_audio

    enriched_parts = []
    for path in audio_paths:
        try:
            logger.debug("Transcribing user voice: %s", path)
            result = await to_thread_fn(transcribe_audio_fn, path)
            if result["success"]:
                transcript = result["transcript"]
                enriched_parts.append(
                    f'[The user sent a voice message~ '
                    f'Here\'s what they said: "{transcript}"]'
                )
            else:
                error = result.get("error", "unknown error")
                if (
                    "No STT provider" in error
                    or error.startswith("Neither VOICE_TOOLS_OPENAI_KEY nor OPENAI_API_KEY is set")
                ):
                    no_stt_note = (
                        "[The user sent a voice message but I can't listen "
                        "to it right now — no STT provider is configured. "
                        "A direct message has already been sent to the user "
                        "with setup instructions."
                    )
                    if has_setup_skill is not None and has_setup_skill():
                        no_stt_note += (
                            " You have a skill called hermes-agent-setup "
                            "that can help users configure Hermes features "
                            "including voice, tools, and more."
                        )
                    no_stt_note += "]"
                    enriched_parts.append(no_stt_note)
                else:
                    enriched_parts.append(
                        "[The user sent a voice message but I had trouble "
                        f"transcribing it~ ({error})]"
                    )
        except Exception as e:
            logger.error("Transcription error: %s", e)
            enriched_parts.append(
                "[The user sent a voice message but something went wrong "
                "when I tried to listen to it~ Let them know!]"
            )

    if enriched_parts:
        prefix = "\n\n".join(enriched_parts)
        if user_text and user_text.strip() == EMPTY_CONTENT_PLACEHOLDER:
            return prefix
        if user_text:
            return f"{prefix}\n\n{user_text}"
        return prefix
    return user_text
