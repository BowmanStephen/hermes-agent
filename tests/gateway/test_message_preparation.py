from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.message_preparation import prepare_inbound_message_text
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


def _source(chat_type: str = "group") -> SessionSource:
    return SessionSource(
        platform=Platform.DISCORD,
        chat_id="chat-1",
        chat_type=chat_type,
        user_id="user-1",
        user_name="Ada",
    )


@pytest.mark.asyncio
async def test_preparation_applies_shared_sender_channel_and_reply_context():
    consumed = []
    source = _source("group")
    event = MessageEvent(
        text="what changed?",
        message_type=MessageType.TEXT,
        source=source,
        message_id="m1",
        reply_to_message_id="m0",
        reply_to_text="previous topic",
        channel_context="[Channel context]\nBob: earlier message",
    )

    prepared = await prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
        config=SimpleNamespace(
            group_sessions_per_user=False,
            thread_sessions_per_user=False,
        ),
        session_key_for_source=lambda _source: "discord:chat-1",
        consume_pending_native_image_paths=lambda key: consumed.append(key),
    )

    assert consumed == ["discord:chat-1"]
    assert prepared == (
        '[Replying to: "previous topic"]\n\n'
        "[Channel context]\nBob: earlier message\n\n"
        "[New message]\n[Ada] what changed?"
    )


@pytest.mark.asyncio
async def test_preparation_buffers_native_image_paths_per_session():
    source = _source("dm")
    event = MessageEvent(
        text="inspect this",
        message_type=MessageType.PHOTO,
        source=source,
        media_urls=["/tmp/photo.png"],
        media_types=["image/png"],
    )
    pending = {}

    prepared = await prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
        config=SimpleNamespace(
            group_sessions_per_user=True,
            thread_sessions_per_user=False,
        ),
        session_key_for_source=lambda _source: "discord:user-1",
        consume_pending_native_image_paths=lambda _key: None,
        decide_image_input_mode=lambda: "native",
        pending_native_image_paths_by_session=pending,
    )

    assert prepared == "inspect this"
    assert pending == {"discord:user-1": ["/tmp/photo.png"]}
