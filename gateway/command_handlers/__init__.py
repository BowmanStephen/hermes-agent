"""Gateway command handler services."""
from __future__ import annotations

import inspect
from functools import wraps
from typing import Any

from gateway.command_registry import GATEWAY_HANDLER_METHODS
from ._imports import sync_run_globals
from .core import CoreGatewayCommands
from .model_modes import ModelModeGatewayCommands
from .goals import GoalGatewayCommands
from .voice import VoiceGatewayCommands
from .telegram_topics import TelegramTopicGatewayCommands
from .session_commands import SessionGatewayCommands
from .operations import OperationsGatewayCommands
from .updates import UpdateGatewayCommands


class GatewayCommandService:
    """Owns gateway command handler bodies during the strangler migration."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self.core = CoreGatewayCommands(runner)
        self.model_modes = ModelModeGatewayCommands(runner)
        self.goals = GoalGatewayCommands(runner)
        self.voice = VoiceGatewayCommands(runner)
        self.telegram_topics = TelegramTopicGatewayCommands(runner)
        self.session_commands = SessionGatewayCommands(runner)
        self.operations = OperationsGatewayCommands(runner)
        self.updates = UpdateGatewayCommands(runner)

        self._services = (
            self.core,
            self.model_modes,
            self.goals,
            self.voice,
            self.telegram_topics,
            self.session_commands,
            self.operations,
            self.updates,
        )

    def __getattr__(self, name: str) -> Any:
        for service in self._services:
            if not any(name in cls.__dict__ for cls in type(service).__mro__):
                continue
            if name in {"__getattr__", "__setattr__"}:
                continue
            attr = getattr(service, name)
            if callable(attr):
                return self._synced(service, attr)
            return attr
        raise AttributeError(name)

    def _synced(self, service: Any, attr: Any) -> Any:
        def _sync_attr_globals() -> None:
            service._sync_run_globals()
            func = getattr(attr, "__func__", attr)
            target = getattr(func, "__globals__", None)
            if isinstance(target, dict):
                sync_run_globals(target)

        if inspect.iscoroutinefunction(attr):
            @wraps(attr)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _sync_attr_globals()
                return await attr(*args, **kwargs)
            return async_wrapper

        @wraps(attr)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _sync_attr_globals()
            return attr(*args, **kwargs)
        return wrapper

    def handler_map(self) -> dict[str, Any]:
        handlers = {}
        runner_overrides = vars(self._runner)
        for canonical, method_name in GATEWAY_HANDLER_METHODS.items():
            handler = runner_overrides.get(method_name)
            if not callable(handler):
                handler = getattr(self, method_name)
            handlers[canonical] = handler
        return handlers
