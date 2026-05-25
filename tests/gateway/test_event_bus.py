"""Tests for gateway.event_bus — async pub/sub for GatewayRunner composition."""

import asyncio

import pytest

from gateway.event_bus import EventBus


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.mark.asyncio
async def test_emit_delivers_payload_to_subscriber(bus: EventBus) -> None:
    queue = bus.subscribe("session.created")
    await bus.emit("session.created", {"session_id": "abc"})

    payload = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert payload == {"session_id": "abc"}


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive_emit(bus: EventBus) -> None:
    q1 = bus.subscribe("message.received")
    q2 = bus.subscribe("message.received")
    await bus.emit("message.received", "hello")

    assert await asyncio.wait_for(q1.get(), timeout=1.0) == "hello"
    assert await asyncio.wait_for(q2.get(), timeout=1.0) == "hello"


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery(bus: EventBus) -> None:
    queue = bus.subscribe("gateway.shutdown")
    bus.unsubscribe("gateway.shutdown", queue)
    await bus.emit("gateway.shutdown", True)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_emit_with_no_subscribers_is_noop(bus: EventBus) -> None:
    await bus.emit("orphan.event", {"ok": True})


@pytest.mark.asyncio
async def test_events_are_isolated_by_name(bus: EventBus) -> None:
    queue_a = bus.subscribe("event.a")
    queue_b = bus.subscribe("event.b")
    await bus.emit("event.a", 1)

    assert await asyncio.wait_for(queue_a.get(), timeout=1.0) == 1

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue_b.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_emit_default_payload_is_none(bus: EventBus) -> None:
    queue = bus.subscribe("ping")
    await bus.emit("ping")

    assert await asyncio.wait_for(queue.get(), timeout=1.0) is None
