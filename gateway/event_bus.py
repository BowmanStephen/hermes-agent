"""Async event bus for GatewayRunner composition refactor.

Services communicate via named events; each subscriber receives copies on
its own ``asyncio.Queue``.
"""

from __future__ import annotations

import asyncio
from typing import Any

EventName = str


class EventBus:
    """In-process async pub/sub with per-subscriber queues."""

    def __init__(self) -> None:
        self._subscribers: dict[EventName, list[asyncio.Queue[Any]]] = {}

    def subscribe(self, event_name: EventName) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._subscribers.setdefault(event_name, []).append(queue)
        return queue

    def unsubscribe(self, event_name: EventName, queue: asyncio.Queue[Any]) -> None:
        subscribers = self._subscribers.get(event_name)
        if not subscribers:
            return
        try:
            subscribers.remove(queue)
        except ValueError:
            return
        if not subscribers:
            del self._subscribers[event_name]

    async def emit(self, event_name: EventName, payload: Any = None) -> None:
        for queue in list(self._subscribers.get(event_name, [])):
            await queue.put(payload)
