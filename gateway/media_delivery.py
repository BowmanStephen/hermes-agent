"""Post-stream media attachment delivery helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from gateway.platforms.base import MessageEvent, SessionSource, should_send_media_as_audio

logger = logging.getLogger(__name__)

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


async def deliver_media_from_response(
    *,
    response: str,
    event: MessageEvent,
    adapter: Any,
    thread_metadata_for_source: Callable[[SessionSource, Any], Any],
    reply_anchor_for_event: Callable[[MessageEvent], Any],
    log: logging.Logger = logger,
) -> None:
    """Extract media tags and local file paths from a streamed response."""
    try:
        force_document_attachments = "[[as_document]]" in response
        media_files, local_files = _extract_response_media(response, adapter)
        thread_meta = thread_metadata_for_source(event.source, reply_anchor_for_event(event))
        image_paths, non_image_media, non_image_local = _partition_media_paths(
            media_files, local_files, force_document_attachments,
        )
        await _send_image_batch(adapter, event, image_paths, thread_meta, log)
        await _send_media_files(adapter, event, non_image_media, thread_meta, log)
        await _send_local_files(adapter, event, non_image_local, thread_meta, log)
    except Exception as exc:
        log.warning("Post-stream media extraction failed: %s", exc)


def _extract_response_media(response: str, adapter: Any) -> tuple[list[tuple[str, bool]], list[str]]:
    media_files, _ = adapter.extract_media(response)
    _, cleaned = adapter.extract_images(response)
    local_files, _ = adapter.extract_local_files(cleaned)
    return media_files, local_files


def _partition_media_paths(
    media_files: list[tuple[str, bool]],
    local_files: list[str],
    force_document_attachments: bool,
) -> tuple[list[str], list[tuple[str, bool]], list[str]]:
    image_paths: list[str] = []
    non_image_media: list[tuple[str, bool]] = []
    non_image_local: list[str] = []
    for media_path, is_voice in media_files:
        if _should_batch_as_image(media_path, is_voice, force_document_attachments):
            image_paths.append(media_path)
        else:
            non_image_media.append((media_path, is_voice))
    for file_path in local_files:
        if Path(file_path).suffix.lower() in _IMAGE_EXTS and not force_document_attachments:
            image_paths.append(file_path)
        else:
            non_image_local.append(file_path)
    return image_paths, non_image_media, non_image_local


def _should_batch_as_image(path: str, is_voice: bool, force_document_attachments: bool) -> bool:
    return (
        Path(path).suffix.lower() in _IMAGE_EXTS
        and not is_voice
        and not force_document_attachments
    )


async def _send_image_batch(
    adapter: Any,
    event: MessageEvent,
    image_paths: list[str],
    metadata: Any,
    log: logging.Logger,
) -> None:
    if not image_paths:
        return
    try:
        images = [(f"file://{quote(path)}", "") for path in image_paths]
        await adapter.send_multiple_images(
            chat_id=event.source.chat_id,
            images=images,
            metadata=metadata,
        )
    except Exception as exc:
        log.warning("[%s] Post-stream image batch delivery failed: %s", adapter.name, exc)


async def _send_media_files(
    adapter: Any,
    event: MessageEvent,
    media_files: list[tuple[str, bool]],
    metadata: Any,
    log: logging.Logger,
) -> None:
    for media_path, is_voice in media_files:
        try:
            await _send_media_file(adapter, event, media_path, is_voice, metadata)
        except Exception as exc:
            log.warning("[%s] Post-stream media delivery failed: %s", adapter.name, exc)


async def _send_media_file(
    adapter: Any,
    event: MessageEvent,
    media_path: str,
    is_voice: bool,
    metadata: Any,
) -> None:
    ext = Path(media_path).suffix.lower()
    if should_send_media_as_audio(event.source.platform, ext, is_voice=is_voice):
        await adapter.send_voice(
            chat_id=event.source.chat_id,
            audio_path=media_path,
            metadata=metadata,
        )
    elif ext in _VIDEO_EXTS:
        await adapter.send_video(
            chat_id=event.source.chat_id,
            video_path=media_path,
            metadata=metadata,
        )
    else:
        await adapter.send_document(
            chat_id=event.source.chat_id,
            file_path=media_path,
            metadata=metadata,
        )


async def _send_local_files(
    adapter: Any,
    event: MessageEvent,
    local_files: list[str],
    metadata: Any,
    log: logging.Logger,
) -> None:
    for file_path in local_files:
        try:
            await _send_local_file(adapter, event, file_path, metadata)
        except Exception as exc:
            log.warning("[%s] Post-stream file delivery failed: %s", adapter.name, exc)


async def _send_local_file(adapter: Any, event: MessageEvent, file_path: str, metadata: Any) -> None:
    if Path(file_path).suffix.lower() in _VIDEO_EXTS:
        await adapter.send_video(
            chat_id=event.source.chat_id,
            video_path=file_path,
            metadata=metadata,
        )
    else:
        await adapter.send_document(
            chat_id=event.source.chat_id,
            file_path=file_path,
            metadata=metadata,
        )
