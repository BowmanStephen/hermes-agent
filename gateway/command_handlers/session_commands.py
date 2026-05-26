"""SessionGatewayCommands aggregate."""
from __future__ import annotations

from .session_history import SessionHistoryGatewayCommands
from .session_runtime import SessionRuntimeGatewayCommands


class SessionGatewayCommands(
    SessionHistoryGatewayCommands,
    SessionRuntimeGatewayCommands,
):
    """Backwards-compatible aggregate for split sessiongatewaycommands aggregate."""
