import asyncio

import pytest


@pytest.mark.asyncio
async def test_event_bus_dispatches_to_subscribers_and_once_handlers():
    from gateway.event_bus import EventBus

    bus = EventBus()
    seen: list[tuple[str, dict]] = []

    async def persistent(payload: dict) -> None:
        seen.append(("persistent", payload))

    @bus.once("message.received")
    async def one_time(payload: dict) -> None:
        seen.append(("once", payload))

    bus.subscribe("message.received", persistent)

    await bus.emit("message.received", {"text": "first"})
    await bus.emit("message.received", {"text": "second"})

    assert seen == [
        ("once", {"text": "first"}),
        ("persistent", {"text": "first"}),
        ("persistent", {"text": "second"}),
    ]


@pytest.mark.asyncio
async def test_event_bus_threadsafe_emit_uses_bound_loop():
    from gateway.event_bus import EventBus

    bus = EventBus()
    loop = asyncio.get_running_loop()
    bus.set_loop(loop)
    delivered = asyncio.Event()

    async def handler(payload: dict) -> None:
        if payload == {"status": "ok"}:
            delivered.set()

    bus.subscribe("gateway.ready", handler)
    bus.emit_threadsafe("gateway.ready", {"status": "ok"})

    await asyncio.wait_for(delivered.wait(), timeout=1)
