"""OperationsGatewayCommands aggregate."""
from __future__ import annotations

from .operations_usage import OperationsUsageGatewayCommands
from .operations_reload import OperationsReloadGatewayCommands
from .operations_approval import OperationsApprovalGatewayCommands


class OperationsGatewayCommands(
    OperationsUsageGatewayCommands,
    OperationsReloadGatewayCommands,
    OperationsApprovalGatewayCommands,
):
    """Backwards-compatible aggregate for split operationsgatewaycommands aggregate."""
