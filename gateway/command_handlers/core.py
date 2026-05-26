"""CoreGatewayCommands aggregate."""
from __future__ import annotations

from .core_session import CoreSessionGatewayCommands
from .core_status import CoreStatusGatewayCommands
from .core_platform import CorePlatformGatewayCommands


class CoreGatewayCommands(
    CoreSessionGatewayCommands,
    CoreStatusGatewayCommands,
    CorePlatformGatewayCommands,
):
    """Backwards-compatible aggregate for split coregatewaycommands aggregate."""
