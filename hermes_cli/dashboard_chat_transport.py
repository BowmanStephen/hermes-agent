"""Dashboard chat transport primitives.

This module owns the protocol shared by the dashboard chat adapters:

* browser xterm.js sends raw PTY input plus resize control frames
* FastAPI bridges those frames to a POSIX PTY
* the PTY-side TUI gateway publishes structured events to dashboard
  subscribers

The web server and React page should stay thin adapters around these
primitives so resize handling, sidecar URLs, and event fanout do not drift.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol


RESIZE_FRAME_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")
VALID_CHANNEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DEFAULT_PTY_READ_TIMEOUT = 0.2


@dataclass(frozen=True)
class PtyInput:
    """Ordinary bytes that should be written to the PTY master."""

    data: bytes


@dataclass(frozen=True)
class PtyResize:
    """Terminal resize request from the browser adapter."""

    cols: int
    rows: int


@dataclass(frozen=True)
class DashboardChatSession:
    """Resolved session identity for one dashboard chat PTY."""

    requested_resume: str | None
    resume: str | None
    channel: str | None
    sidecar_url: str | None


@dataclass(frozen=True)
class DashboardEventFrame:
    """Validated structured event frame for dashboard fanout."""

    raw: str
    event_type: str


@dataclass(frozen=True)
class DashboardWebSocketGateResult:
    """Decision for a dashboard chat WebSocket upgrade."""

    accepted: bool
    close_code: int | None = None
    channel: str | None = None


@dataclass(frozen=True)
class DashboardWebSocketGate:
    """Shared auth, client, and channel policy for dashboard chat adapters."""

    enabled: bool
    expected_token: str
    public_bind: bool = False
    loopback_hosts: frozenset[str] = frozenset(
        {"127.0.0.1", "::1", "localhost", "testclient"}
    )

    def validate(
        self,
        *,
        token: str,
        client_host: str | None,
        channel: str | None = None,
        require_channel: bool = False,
    ) -> DashboardWebSocketGateResult:
        if not self.enabled:
            return DashboardWebSocketGateResult(False, close_code=4403)

        if not hmac.compare_digest(token.encode(), self.expected_token.encode()):
            return DashboardWebSocketGateResult(False, close_code=4401)

        if not self.public_bind and client_host and client_host not in self.loopback_hosts:
            return DashboardWebSocketGateResult(False, close_code=4403)

        valid_channel = channel if channel and is_valid_channel(channel) else None
        if require_channel and not valid_channel:
            return DashboardWebSocketGateResult(False, close_code=4400)

        return DashboardWebSocketGateResult(True, channel=valid_channel)


class PtyPort(Protocol):
    def read(self, timeout: float = DEFAULT_PTY_READ_TIMEOUT) -> Optional[bytes]: ...

    def write(self, data: bytes) -> None: ...

    def resize(self, cols: int, rows: int) -> None: ...

    def close(self) -> None: ...


class WebSocketPort(Protocol):
    async def send_bytes(self, data: bytes) -> None: ...

    async def receive(self) -> dict[str, Any]: ...


class EventSubscriber(Protocol):
    async def send_text(self, payload: str) -> None: ...


def encode_resize_frame(cols: int, rows: int) -> bytes:
    """Encode the dashboard PTY resize control frame."""
    return f"\x1b[RESIZE:{max(1, cols)};{max(1, rows)}]".encode("ascii")


def parse_pty_client_frame(raw: bytes) -> PtyInput | PtyResize:
    """Parse one browser-to-PTY frame.

    Only an exact resize control frame is consumed as protocol.  Anything
    else, including a resize-looking prefix with trailing bytes, is user input.
    """
    match = RESIZE_FRAME_RE.fullmatch(raw)

    if not match:
        return PtyInput(raw)

    return PtyResize(cols=int(match.group(1)), rows=int(match.group(2)))


def is_valid_channel(channel: str) -> bool:
    """True if `channel` is safe to use as an opaque event fanout key."""
    return bool(VALID_CHANNEL_RE.fullmatch(channel))


def build_sidecar_url(
    *,
    host: str | None,
    port: int | str | None,
    token: str,
    channel: str,
) -> str | None:
    """Build the PTY-side TUI gateway publisher URL."""
    if not host or not port or not is_valid_channel(channel):
        return None

    netloc = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    qs = urllib.parse.urlencode({"token": token, "channel": channel})

    return f"ws://{netloc}/api/pub?{qs}"


def build_chat_session(
    *,
    requested_resume: str | None,
    channel: str | None,
    host: str | None,
    port: int | str | None,
    token: str,
    latest_descendant: Callable[[str], str | None] | None = None,
) -> DashboardChatSession:
    """Resolve the dashboard chat session and event sidecar identity."""
    resolved_resume = requested_resume or None

    if requested_resume and latest_descendant is not None:
        resolved_resume = latest_descendant(requested_resume) or requested_resume

    valid_channel = channel if channel and is_valid_channel(channel) else None
    sidecar_url = (
        build_sidecar_url(host=host, port=port, token=token, channel=valid_channel)
        if valid_channel
        else None
    )

    return DashboardChatSession(
        requested_resume=requested_resume or None,
        resume=resolved_resume,
        channel=valid_channel,
        sidecar_url=sidecar_url,
    )


def encode_event_frame(obj: dict) -> str:
    """Encode one structured event frame for the dashboard sidecar WS."""
    return json.dumps(obj, ensure_ascii=False)


def parse_event_frame(raw: str) -> DashboardEventFrame | None:
    """Parse and validate one PTY-side gateway event frame."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None

    if not isinstance(obj, dict) or obj.get("method") != "event":
        return None

    params = obj.get("params")

    if not isinstance(params, dict):
        return None

    event_type = params.get("type")

    if not isinstance(event_type, str) or not event_type:
        return None

    return DashboardEventFrame(raw=raw, event_type=event_type)


class PtyWebSocketTransport:
    """Pump raw PTY bytes across a WebSocket using dashboard chat protocol."""

    def __init__(
        self,
        bridge: PtyPort,
        websocket: WebSocketPort,
        *,
        read_timeout: float = DEFAULT_PTY_READ_TIMEOUT,
    ) -> None:
        self._bridge = bridge
        self._websocket = websocket
        self._read_timeout = read_timeout

    async def run(self) -> None:
        """Run until the browser or PTY disconnects, then close the bridge."""
        loop = asyncio.get_running_loop()

        async def pump_pty_to_ws() -> None:
            while True:
                chunk = await loop.run_in_executor(
                    None, self._bridge.read, self._read_timeout
                )

                if chunk is None:
                    return
                if not chunk:
                    await asyncio.sleep(0)
                    continue

                try:
                    await self._websocket.send_bytes(chunk)
                except Exception:
                    return

        async def pump_ws_to_pty() -> None:
            while True:
                msg = await self._websocket.receive()

                if msg.get("type") == "websocket.disconnect":
                    return

                raw = msg.get("bytes")
                if raw is None:
                    text = msg.get("text")
                    raw = text.encode("utf-8") if isinstance(text, str) else b""
                if not raw:
                    continue

                frame = parse_pty_client_frame(raw)

                if isinstance(frame, PtyResize):
                    self._bridge.resize(cols=frame.cols, rows=frame.rows)
                else:
                    self._bridge.write(frame.data)

        reader_task = asyncio.create_task(pump_pty_to_ws())
        writer_task = asyncio.create_task(pump_ws_to_pty())

        try:
            done, _pending = await asyncio.wait(
                {reader_task, writer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
        finally:
            for task in (reader_task, writer_task):
                if not task.done():
                    task.cancel()
            for task in (reader_task, writer_task):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._bridge.close()


class ChatEventFanout:
    """Fan out PTY-side gateway event frames to browser subscribers."""

    def __init__(self) -> None:
        self._channels: dict[str, set[EventSubscriber]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str, subscriber: EventSubscriber) -> None:
        async with self._lock:
            self._channels.setdefault(channel, set()).add(subscriber)

    async def unsubscribe(self, channel: str, subscriber: EventSubscriber) -> None:
        async with self._lock:
            subs = self._channels.get(channel)

            if subs is None:
                return

            subs.discard(subscriber)

            if not subs:
                self._channels.pop(channel, None)

    async def broadcast(self, channel: str, payload: str) -> int:
        async with self._lock:
            subs = list(self._channels.get(channel, ()))

        delivered = 0

        for sub in subs:
            try:
                await sub.send_text(payload)
                delivered += 1
            except Exception:
                # The subscriber cleanup path removes dead sockets when its
                # endpoint observes disconnect; a failed broadcast should not
                # block delivery to the remaining subscribers.
                pass

        return delivered

    async def has_subscribers(self, channel: str) -> bool:
        async with self._lock:
            return bool(self._channels.get(channel))

    def has_subscribers_nowait(self, channel: str) -> bool:
        """Best-effort diagnostic helper for sync tests."""
        return bool(self._channels.get(channel))
