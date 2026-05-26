"""VoiceGatewayCommands aggregate."""
from __future__ import annotations

from .voice_control import VoiceControlGatewayCommands
from .voice_io import VoiceIOGatewayCommands


class VoiceGatewayCommands(
    VoiceControlGatewayCommands,
    VoiceIOGatewayCommands,
):
    """Backwards-compatible aggregate for split voicegatewaycommands aggregate."""
