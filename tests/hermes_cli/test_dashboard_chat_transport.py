from __future__ import annotations

import asyncio

import pytest

from hermes_cli.dashboard_chat_transport import (
    ChatEventFanout,
    DashboardChatSession,
    DashboardEventFrame,
    DashboardWebSocketGate,
    DashboardWebSocketGateResult,
    PtyInput,
    PtyResize,
    PtyWebSocketTransport,
    build_chat_session,
    encode_event_frame,
    parse_event_frame,
    parse_pty_client_frame,
)


def test_parse_pty_client_frame_recognizes_resize_control_frame():
    frame = parse_pty_client_frame(b"\x1b[RESIZE:123;45]")

    assert frame == PtyResize(cols=123, rows=45)


def test_parse_pty_client_frame_keeps_non_control_bytes_as_input():
    raw = b"\x1b[RESIZE:123;45]not-a-control-frame"

    frame = parse_pty_client_frame(raw)

    assert frame == PtyInput(data=raw)


def test_encode_event_frame_uses_dashboard_sidecar_json_shape():
    assert encode_event_frame({"type": "status", "payload": "café"}) == (
        '{"type": "status", "payload": "café"}'
    )


def test_parse_event_frame_accepts_dispatcher_event_envelope():
    raw = (
        '{"jsonrpc":"2.0","method":"event",'
        '"params":{"type":"tool.start","session_id":"s1","payload":{"tool_id":"t1"}}}'
    )

    frame = parse_event_frame(raw)

    assert frame == DashboardEventFrame(
        raw=raw,
        event_type="tool.start",
        session_id="s1",
        payload={"tool_id": "t1"},
    )


def test_parse_event_frame_rejects_non_event_json():
    assert parse_event_frame('{"type":"tool.start"}') is None
    assert parse_event_frame("not-json") is None


def test_parse_event_frame_rejects_malformed_typed_fields():
    assert parse_event_frame(
        '{"jsonrpc":"2.0","method":"event",'
        '"params":{"type":"tool.start","session_id":123,"payload":{}}}'
    ) is None
    assert parse_event_frame(
        '{"jsonrpc":"2.0","method":"event",'
        '"params":{"type":"tool.start","payload":"bad"}}'
    ) is None


def test_build_chat_session_resolves_resume_and_sidecar_together():
    session = build_chat_session(
        requested_resume="sess-parent",
        channel="tab-1",
        host="127.0.0.1",
        port=9119,
        token="tok",
        latest_descendant=lambda sid: f"{sid}-child",
    )

    assert session == DashboardChatSession(
        requested_resume="sess-parent",
        resume="sess-parent-child",
        channel="tab-1",
        sidecar_url="ws://127.0.0.1:9119/api/pub?token=tok&channel=tab-1",
    )


def test_build_chat_session_drops_invalid_channel_but_keeps_resume():
    session = build_chat_session(
        requested_resume="sess-42",
        channel="../bad",
        host="127.0.0.1",
        port=9119,
        token="tok",
        latest_descendant=lambda sid: None,
    )

    assert session == DashboardChatSession(
        requested_resume="sess-42",
        resume="sess-42",
        channel=None,
        sidecar_url=None,
    )


def test_dashboard_ws_gate_rejects_disabled_bad_token_and_bad_channel():
    gate = DashboardWebSocketGate(enabled=False, expected_token="tok")

    assert gate.validate(token="tok", client_host="127.0.0.1") == (
        DashboardWebSocketGateResult(False, close_code=4403)
    )

    gate = DashboardWebSocketGate(enabled=True, expected_token="tok")

    assert gate.validate(token="wrong", client_host="127.0.0.1") == (
        DashboardWebSocketGateResult(False, close_code=4401)
    )
    assert gate.validate(
        token="tok",
        client_host="127.0.0.1",
        channel="../bad",
        require_channel=True,
    ) == DashboardWebSocketGateResult(False, close_code=4400)


def test_dashboard_ws_gate_allows_loopback_and_public_bind_clients():
    loopback_gate = DashboardWebSocketGate(enabled=True, expected_token="tok")

    assert loopback_gate.validate(
        token="tok",
        client_host="localhost",
        channel="tab-1",
        require_channel=True,
    ) == DashboardWebSocketGateResult(True, channel="tab-1")
    assert loopback_gate.validate(token="tok", client_host="203.0.113.7") == (
        DashboardWebSocketGateResult(False, close_code=4403)
    )

    public_gate = DashboardWebSocketGate(
        enabled=True,
        expected_token="tok",
        public_bind=True,
    )

    assert public_gate.validate(token="tok", client_host="203.0.113.7") == (
        DashboardWebSocketGateResult(True)
    )


@pytest.mark.asyncio
async def test_event_fanout_broadcasts_and_cleans_up_subscribers():
    fanout = ChatEventFanout()
    first = _FakeEventSubscriber()
    second = _FakeEventSubscriber()

    await fanout.subscribe("chat-1", first)
    await fanout.subscribe("chat-1", second)

    assert await fanout.has_subscribers("chat-1")
    assert await fanout.broadcast("chat-1", '{"type":"tool.start"}') == 2

    await fanout.unsubscribe("chat-1", first)
    assert await fanout.broadcast("chat-1", '{"type":"tool.complete"}') == 1

    await fanout.unsubscribe("chat-1", second)
    assert not await fanout.has_subscribers("chat-1")
    assert first.sent == ['{"type":"tool.start"}']
    assert second.sent == ['{"type":"tool.start"}', '{"type":"tool.complete"}']


@pytest.mark.asyncio
async def test_pty_transport_forwards_input_resize_and_closes_on_disconnect():
    bridge = _FakePty(reads=[])
    ws = _FakePtyWebSocket(
        receives=[
            {"type": "websocket.receive", "text": "hello\n"},
            {"type": "websocket.receive", "text": "\x1b[RESIZE:99;41]"},
            {"type": "websocket.disconnect"},
        ]
    )

    await PtyWebSocketTransport(bridge, ws, read_timeout=0).run()

    assert bridge.writes == [b"hello\n"]
    assert bridge.resizes == [(99, 41)]
    assert bridge.closed is True


@pytest.mark.asyncio
async def test_pty_transport_returns_when_pty_reaches_eof():
    bridge = _FakePty(reads=[b"ready", None])
    ws = _FakePtyWebSocket(receives=[])

    await PtyWebSocketTransport(bridge, ws, read_timeout=0).run()

    assert ws.sent_bytes == [b"ready"]
    assert bridge.closed is True


class _FakeEventSubscriber:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


class _FakePty:
    def __init__(self, reads: list[bytes | None]) -> None:
        self._reads = list(reads)
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.closed = False

    def read(self, timeout: float = 0.2) -> bytes | None:
        if self._reads:
            return self._reads.pop(0)
        return b""

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, cols: int, rows: int) -> None:
        self.resizes.append((cols, rows))

    def close(self) -> None:
        self.closed = True


class _FakePtyWebSocket:
    def __init__(self, receives: list[dict]) -> None:
        self._receives = list(receives)
        self.sent_bytes: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def receive(self) -> dict:
        if self._receives:
            return self._receives.pop(0)
        await asyncio.Future()
        raise AssertionError("unreachable")
