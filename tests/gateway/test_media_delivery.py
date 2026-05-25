from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform
from gateway.media_delivery import deliver_media_from_response
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, SendResult
from gateway.session import SessionSource


def _event() -> MessageEvent:
    return MessageEvent(
        text="make media",
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="chat-1",
            chat_type="dm",
            thread_id="topic-1",
        ),
        message_id="msg-1",
    )


def _adapter() -> SimpleNamespace:
    return SimpleNamespace(
        name="test",
        extract_media=BasePlatformAdapter.extract_media,
        extract_images=BasePlatformAdapter.extract_images,
        extract_local_files=BasePlatformAdapter.extract_local_files,
        send_multiple_images=AsyncMock(return_value=SendResult(success=True)),
        send_voice=AsyncMock(return_value=SendResult(success=True)),
        send_document=AsyncMock(return_value=SendResult(success=True)),
        send_video=AsyncMock(return_value=SendResult(success=True)),
    )


@pytest.mark.asyncio
async def test_deliver_media_from_response_batches_images_with_thread_metadata() -> None:
    adapter = _adapter()
    event = _event()

    await deliver_media_from_response(
        response="MEDIA:/tmp/a.png\nMEDIA:/tmp/b.jpg",
        event=event,
        adapter=adapter,
        thread_metadata_for_source=lambda source, anchor=None: {"thread_id": source.thread_id},
        reply_anchor_for_event=lambda _event: "msg-1",
    )

    adapter.send_multiple_images.assert_awaited_once_with(
        chat_id="chat-1",
        images=[("file:///tmp/a.png", ""), ("file:///tmp/b.jpg", "")],
        metadata={"thread_id": "topic-1"},
    )
    adapter.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_media_from_response_respects_as_document_for_images() -> None:
    adapter = _adapter()

    await deliver_media_from_response(
        response="[[as_document]]\nMEDIA:/tmp/full-res.png",
        event=_event(),
        adapter=adapter,
        thread_metadata_for_source=lambda _source, _anchor=None: {"thread_id": "topic-1"},
        reply_anchor_for_event=lambda _event: None,
    )

    adapter.send_multiple_images.assert_not_awaited()
    adapter.send_document.assert_awaited_once_with(
        chat_id="chat-1",
        file_path="/tmp/full-res.png",
        metadata={"thread_id": "topic-1"},
    )


@pytest.mark.asyncio
async def test_deliver_media_from_response_routes_audio_and_video() -> None:
    adapter = _adapter()

    await deliver_media_from_response(
        response="MEDIA:/tmp/speech.mp3\nMEDIA:/tmp/movie.mp4",
        event=_event(),
        adapter=adapter,
        thread_metadata_for_source=lambda _source, _anchor=None: None,
        reply_anchor_for_event=lambda _event: None,
    )

    adapter.send_voice.assert_awaited_once_with(
        chat_id="chat-1",
        audio_path="/tmp/speech.mp3",
        metadata=None,
    )
    adapter.send_video.assert_awaited_once_with(
        chat_id="chat-1",
        video_path="/tmp/movie.mp4",
        metadata=None,
    )
