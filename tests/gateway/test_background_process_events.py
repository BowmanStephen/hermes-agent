from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.background_process_events import (
    build_process_event_source,
    inject_watch_notification,
)
from gateway.config import Platform
from gateway.session import SessionSource


class _SessionStore:
    def __init__(self, entries):
        self._entries = entries
        self.loaded = False

    def _ensure_loaded(self):
        self.loaded = True


def test_build_process_event_source_prefers_session_store_origin():
    origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-100",
        chat_type="group",
        thread_id="42",
        user_id="u1",
        user_name="Alice",
    )
    session_store = _SessionStore(
        {
            "agent:main:telegram:group:-100:42": SimpleNamespace(origin=origin),
        }
    )

    source = build_process_event_source(
        {
            "session_id": "proc-watch",
            "session_key": "agent:main:telegram:group:-100:42",
        },
        session_store=session_store,
        get_cached_session_source=lambda _key: None,
    )

    assert source is origin
    assert session_store.loaded is True


@pytest.mark.asyncio
async def test_inject_watch_notification_routes_internal_event_to_matching_adapter():
    adapter = SimpleNamespace(handle_message=AsyncMock())
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_type="dm",
        thread_id="777",
        user_id="u1",
        user_name="Alice",
    )

    await inject_watch_notification(
        "[SYSTEM: process matched]",
        {"session_id": "proc-watch", "message_id": "msg-7"},
        source_resolver=lambda _evt: source,
        adapters={Platform.TELEGRAM: adapter},
    )

    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.internal is True
    assert event.text == "[SYSTEM: process matched]"
    assert event.message_id == "msg-7"
    assert event.source is source
