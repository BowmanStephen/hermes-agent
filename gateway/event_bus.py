"""Event bus for gateway services.

Step 1 of GatewayRunner decomposition:
- Services communicate via events, not direct method calls
- Zero-risk extraction (new file, no existing code moved yet)
- 32 lines of core event dispatch + 20 lines of type annotations

Usage:
    from gateway.event_bus import EventBus, event_bus

    # Subscribe
    @event_bus.on("message_received")
    async def handler(event): ...

    # Emit
    await event_bus.emit("message_received", {"msg": msg, "platform": "telegram"})
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)

# Type alias for event handlers
EventHandler = Callable[[dict], Coroutine[Any, Any, None]]


class EventBus:
    """Async event bus with weak-reference subscriptions.

    Design decisions:
    - Weak references: subscribers can be garbage collected without explicit unsubscribe
    - One event -> many handlers: all handlers receive each event
    - Fire-and-forget: emit() schedules handlers but does not await completion
    - Thread-safe: uses asyncio.call_soon_threadsafe for cross-thread emission
    """

    def __init__(self) -> None:
        # event_type -> list of weakref.ref(handler)
        self._handlers: Dict[str, List[weakref.ref]] = defaultdict(list)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind to an event loop (call once at startup)."""
        self._loop = loop

    def on(self, event_type: str) -> Callable[[EventHandler], EventHandler]:
        """Decorator to subscribe a handler to an event type.

        Example:
            @event_bus.on("session_created")
            async def handle_session(event): ...
        """
        def decorator(handler: EventHandler) -> EventHandler:
            self.subscribe(event_type, handler)
            return handler
        return decorator

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event type (non-decorator form)."""
        ref = weakref.ref(handler)
        self._handlers[event_type].append(ref)
        logger.debug("Subscribed %s to %s", handler.__name__, event_type)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe a handler (rarely needed with weak references)."""
        self._handlers[event_type] = [
            ref for ref in self._handlers[event_type]
            if ref() is not None and ref() != handler
        ]

    async def emit(self, event_type: str, data: dict) -> None:
        """Emit an event to all subscribers.

        Schedules handlers on the event loop without awaiting completion.
        Handlers run concurrently via asyncio.gather.
        """
        if self._loop is None:
            self._loop = asyncio.get_event_loop()

        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        # Resolve weak references, filtering dead ones
        alive_handlers = []
        dead_refs = []
        for ref in handlers:
            handler = ref()
            if handler is not None:
                alive_handlers.append(handler)
            else:
                dead_refs.append(ref)

        # Clean up dead references
        if dead_refs:
            self._handlers[event_type] = [ref for ref in handlers if ref not in dead_refs]

        # Schedule handlers
        if alive_handlers:
            results = await asyncio.gather(
                *[h(data) for h in alive_handlers],
                return_exceptions=True
            )
            # Log any exceptions but don't crash the bus
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Event handler %s for %s failed: %s",
                        alive_handlers[i].__name__, event_type, result
                    )

    def emit_threadsafe(self, event_type: str, data: dict) -> None:
        """Thread-safe emit for cross-thread events (e.g., from sync callbacks)."""
        if self._loop is None:
            raise RuntimeError("EventBus loop not set. Call set_loop() first.")

        def _emit():
            asyncio.create_task(self.emit(event_type, data))

        self._loop.call_soon_threadsafe(_emit)

    def once(self, event_type: str) -> Callable[[EventHandler], EventHandler]:
        """Decorator for one-time subscription (auto-unsubscribe after first event)."""
        def decorator(handler: EventHandler) -> EventHandler:
            async def wrapper(data: dict) -> None:
                await handler(data)
                self.unsubscribe(event_type, wrapper)
            wrapper.__name__ = handler.__name__  # Preserve name for logging
            self.subscribe(event_type, wrapper)
            return wrapper
        return decorator


# Global event bus instance; inject into GatewayRunner at startup.
# Services should accept event_bus as a dependency, not import this directly
event_bus = EventBus()
