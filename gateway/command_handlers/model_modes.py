"""Compatibility aggregate for model and mode command handlers."""
from __future__ import annotations

from .display_modes import DisplayModeGatewayCommands
from .model_switch import ModelSwitchGatewayCommands
from .runtime_modes import RuntimeModeGatewayCommands


class ModelModeGatewayCommands(
    ModelSwitchGatewayCommands,
    RuntimeModeGatewayCommands,
    DisplayModeGatewayCommands,
):
    """Backwards-compatible aggregate during command-service migration."""
