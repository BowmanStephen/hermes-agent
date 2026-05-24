"""Plugin tool-contract validation.

A plugin's manifest advertises tools via ``provides_tools``; its ``register()``
is expected to register each via ``ctx.register_tool``. This module checks that
the advertised set matches the set the plugin actually registered and reports
any mismatch, so a plugin that advertises tools it never wires up — e.g.
hermes-lcm v0.11.1, which advertised 7 ``lcm_*`` tools and registered none
(upstream stephenschoettler/hermes-lcm#200) — is caught at load time instead of
silently shipping a missing tool surface the model is told to use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Literal

ViolationKind = Literal["advertised-not-registered", "registered-not-advertised"]
Severity = Literal["error", "info"]


@dataclass(frozen=True)
class ContractViolation:
    """A single advertised-vs-registered mismatch for one plugin."""

    tool: str
    kind: ViolationKind
    severity: Severity


def validate_tool_contract(
    advertised: Iterable[str],
    registered: Iterable[str],
) -> List[ContractViolation]:
    """Compare a plugin's advertised tools against what it actually registered.

    ``advertised``: the manifest's ``provides_tools``.
    ``registered``: the tool names the plugin registered during ``register()``.

    Returns a list of violations, deterministically ordered:
    advertised-but-not-registered (severity ``error`` — the dangerous direction)
    first, then registered-but-not-advertised (severity ``info`` — stale
    manifest). An empty list means the contract holds.
    """
    advertised_set = set(advertised)
    registered_set = set(registered)

    violations: List[ContractViolation] = [
        ContractViolation(tool, "advertised-not-registered", "error")
        for tool in sorted(advertised_set - registered_set)
    ]
    violations.extend(
        ContractViolation(tool, "registered-not-advertised", "info")
        for tool in sorted(registered_set - advertised_set)
    )
    return violations
