"""Shared runner-backed command service base."""
from __future__ import annotations

import sys
from typing import Any

from ._imports import sync_run_globals


class RunnerBackedCommandService:
    """Compatibility base while command handlers shed GatewayRunner state."""

    def __init__(self, runner: Any) -> None:
        object.__setattr__(self, "_runner", runner)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_runner"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_runner":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_runner"), name, value)

    def _sync_run_globals(self) -> None:
        sync_run_globals(sys.modules[self.__class__.__module__].__dict__)
