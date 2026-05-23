"""Deep tool catalog construction for agent tool surfaces.

This module owns toolset resolution, registry schema retrieval, runtime schema
adapters, source metadata, and compatibility sanitization. Callers should
receive one immutable surface instead of reconstructing catalog rules from
toolsets, registry state, and ad hoc schema patches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from tools.registry import registry
from toolsets import resolve_toolset, validate_toolset

logger = logging.getLogger(__name__)


LEGACY_TOOLSET_MAP = {
    "web_tools": ["web_search", "web_extract"],
    "terminal_tools": ["terminal"],
    "vision_tools": ["vision_analyze"],
    "moa_tools": ["mixture_of_agents"],
    "image_tools": ["image_generate"],
    "skills_tools": ["skills_list", "skill_view", "skill_manage"],
    "browser_tools": [
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_back",
        "browser_press",
        "browser_get_images",
        "browser_vision",
        "browser_console",
    ],
    "cronjob_tools": ["cronjob"],
    "file_tools": ["read_file", "write_file", "patch", "search_files"],
    "tts_tools": ["text_to_speech"],
}


@dataclass(frozen=True)
class ToolCatalogRequest:
    enabled_toolsets: Optional[tuple[str, ...]] = None
    disabled_toolsets: tuple[str, ...] = ()
    quiet_mode: bool = False
    kanban_task: bool = field(default_factory=lambda: bool(os.environ.get("HERMES_KANBAN_TASK")))

    @classmethod
    def from_args(
        cls,
        enabled_toolsets: Optional[Iterable[str]] = None,
        disabled_toolsets: Optional[Iterable[str]] = None,
        quiet_mode: bool = False,
    ) -> "ToolCatalogRequest":
        return cls(
            enabled_toolsets=tuple(enabled_toolsets) if enabled_toolsets is not None else None,
            disabled_toolsets=tuple(disabled_toolsets or ()),
            quiet_mode=quiet_mode,
        )


@dataclass(frozen=True)
class ToolSourceMetadata:
    name: str
    source: str
    toolset: str
    enabled: bool = True


@dataclass(frozen=True)
class ToolSurface:
    schemas: tuple[dict[str, Any], ...]
    tool_names: tuple[str, ...]
    requested_names: frozenset[str]
    source_metadata: Mapping[str, ToolSourceMetadata]

    @property
    def valid_names(self) -> frozenset[str]:
        return frozenset(self.tool_names)

    def as_list(self) -> list[dict[str, Any]]:
        """Return an OpenAI-compatible list that callers may append to."""
        return list(self.schemas)


@dataclass(frozen=True)
class RuntimeToolMerge:
    tools: list[dict[str, Any]]
    valid_names: frozenset[str]
    added_names: frozenset[str]
    source_metadata: Mapping[str, ToolSourceMetadata]


def build_tool_surface(request: ToolCatalogRequest) -> ToolSurface:
    """Build the complete tool surface for one agent/runtime request."""
    tools_to_include, source_metadata = _resolve_requested_tools(request)
    filtered_tools = registry.get_definitions(tools_to_include, quiet=request.quiet_mode)
    filtered_tools = _apply_schema_adapters(filtered_tools)
    filtered_tools = _sanitize_schemas(filtered_tools)

    tool_names = tuple(t["function"]["name"] for t in filtered_tools)
    source_metadata = {
        name: meta for name, meta in source_metadata.items() if name in set(tool_names)
    }

    if not request.quiet_mode:
        if filtered_tools:
            print(f"🛠️  Final tool selection ({len(filtered_tools)} tools): {', '.join(tool_names)}")
        else:
            print("🛠️  No tools selected (all filtered out or unavailable)")

    return ToolSurface(
        schemas=tuple(filtered_tools),
        tool_names=tool_names,
        requested_names=frozenset(tools_to_include),
        source_metadata=MappingProxyType(source_metadata),
    )


def merge_runtime_tool_schemas(
    tools: Iterable[dict[str, Any]],
    schemas: Iterable[dict[str, Any]],
    *,
    source: str,
    toolset: str,
    source_metadata: Optional[Mapping[str, ToolSourceMetadata]] = None,
) -> RuntimeToolMerge:
    """Merge runtime-provided tool schemas into an existing tool surface.

    Memory providers and context engines are initialized after the base
    registry catalog is built, so they cannot always participate in static
    toolset resolution. They still use this catalog helper so deduplication,
    wrapping, valid-name tracking, and source metadata stay in one place.
    """
    merged_tools = list(tools or [])
    metadata = dict(source_metadata or {})
    existing_names = {
        t.get("function", {}).get("name")
        for t in merged_tools
        if isinstance(t, dict)
    }
    existing_names.discard(None)
    added_names: set[str] = set()

    for schema in schemas or ():
        if not isinstance(schema, dict):
            continue
        name = schema.get("name") or schema.get("function", {}).get("name")
        if not name or name in existing_names:
            continue
        wrapped = schema if schema.get("type") == "function" else {"type": "function", "function": schema}
        merged_tools.append(wrapped)
        existing_names.add(name)
        added_names.add(name)
        metadata[name] = ToolSourceMetadata(
            name=name,
            source=source,
            toolset=toolset,
            enabled=True,
        )

    return RuntimeToolMerge(
        tools=merged_tools,
        valid_names=frozenset(name for name in existing_names if name),
        added_names=frozenset(added_names),
        source_metadata=MappingProxyType(metadata),
    )


def _resolve_requested_tools(
    request: ToolCatalogRequest,
) -> tuple[set[str], dict[str, ToolSourceMetadata]]:
    tools_to_include: set[str] = set()
    source_metadata: dict[str, ToolSourceMetadata] = {}

    if request.enabled_toolsets is not None:
        enabled_toolsets = list(request.enabled_toolsets)
        if request.kanban_task and "kanban" not in enabled_toolsets:
            enabled_toolsets.append("kanban")
        for toolset_name in enabled_toolsets:
            resolved, source = _resolve_one_toolset(toolset_name, request.quiet_mode, enabled=True)
            tools_to_include.update(resolved)
            _record_sources(source_metadata, resolved, toolset_name, source, enabled=True)
    else:
        from toolsets import get_all_toolsets

        for toolset_name in get_all_toolsets():
            resolved = resolve_toolset(toolset_name)
            tools_to_include.update(resolved)
            _record_sources(source_metadata, resolved, toolset_name, "toolset", enabled=True)

    for toolset_name in request.disabled_toolsets:
        resolved, source = _resolve_one_toolset(toolset_name, request.quiet_mode, enabled=False)
        tools_to_include.difference_update(resolved)
        for name in resolved:
            source_metadata.pop(name, None)
        if source:
            _record_sources(source_metadata, resolved, toolset_name, source, enabled=False)
            for name in resolved:
                source_metadata.pop(name, None)

    return tools_to_include, source_metadata


def _resolve_one_toolset(
    toolset_name: str,
    quiet_mode: bool,
    *,
    enabled: bool,
) -> tuple[list[str], str]:
    action = "Enabled" if enabled else "Disabled"
    prefix = "✅" if enabled else "🚫"
    if validate_toolset(toolset_name):
        resolved = list(resolve_toolset(toolset_name))
        if not quiet_mode:
            print(f"{prefix} {action} toolset '{toolset_name}': {', '.join(resolved) if resolved else 'no tools'}")
        return resolved, "toolset"
    if toolset_name in LEGACY_TOOLSET_MAP:
        resolved = list(LEGACY_TOOLSET_MAP[toolset_name])
        if not quiet_mode:
            print(f"{prefix} {action} legacy toolset '{toolset_name}': {', '.join(resolved)}")
        return resolved, "legacy"
    if not quiet_mode:
        print(f"⚠️  Unknown toolset: {toolset_name}")
    return [], ""


def _record_sources(
    source_metadata: dict[str, ToolSourceMetadata],
    tool_names: Iterable[str],
    toolset_name: str,
    source: str,
    *,
    enabled: bool,
) -> None:
    for name in tool_names:
        source_metadata[name] = ToolSourceMetadata(
            name=name,
            source=source,
            toolset=toolset_name,
            enabled=enabled,
        )


def _apply_schema_adapters(filtered_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    available_tool_names = {t["function"]["name"] for t in filtered_tools}
    filtered_tools = _adapt_execute_code_schema(filtered_tools, available_tool_names)
    filtered_tools, available_tool_names = _adapt_discord_schemas(filtered_tools, available_tool_names)
    filtered_tools = _adapt_browser_schema(filtered_tools, available_tool_names)
    return filtered_tools


def _adapt_execute_code_schema(
    filtered_tools: list[dict[str, Any]],
    available_tool_names: set[str],
) -> list[dict[str, Any]]:
    if "execute_code" not in available_tool_names:
        return filtered_tools
    from tools.code_execution_tool import SANDBOX_ALLOWED_TOOLS, build_execute_code_schema, _get_execution_mode

    sandbox_enabled = SANDBOX_ALLOWED_TOOLS & available_tool_names
    dynamic_schema = build_execute_code_schema(sandbox_enabled, mode=_get_execution_mode())
    return _replace_tool_schema(filtered_tools, "execute_code", dynamic_schema)


def _adapt_discord_schemas(
    filtered_tools: list[dict[str, Any]],
    available_tool_names: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    schema_fns = {
        "discord": "get_dynamic_schema_core",
        "discord_admin": "get_dynamic_schema_admin",
    }
    for tool_name, schema_fn_name in schema_fns.items():
        if tool_name not in available_tool_names:
            continue
        try:
            from tools import discord_tool as discord_tools

            dynamic = getattr(discord_tools, schema_fn_name)()
        except Exception:
            dynamic = None
        if dynamic is None:
            filtered_tools = [
                t for t in filtered_tools if t.get("function", {}).get("name") != tool_name
            ]
            available_tool_names.discard(tool_name)
        else:
            filtered_tools = _replace_tool_schema(filtered_tools, tool_name, dynamic)
    return filtered_tools, available_tool_names


def _adapt_browser_schema(
    filtered_tools: list[dict[str, Any]],
    available_tool_names: set[str],
) -> list[dict[str, Any]]:
    if "browser_navigate" not in available_tool_names:
        return filtered_tools
    if {"web_search", "web_extract"} & available_tool_names:
        return filtered_tools
    for index, tool_def in enumerate(filtered_tools):
        if tool_def.get("function", {}).get("name") != "browser_navigate":
            continue
        desc = tool_def["function"].get("description", "")
        desc = desc.replace(
            " For simple information retrieval, prefer web_search or web_extract (faster, cheaper).",
            "",
        )
        filtered_tools[index] = {
            "type": "function",
            "function": {**tool_def["function"], "description": desc},
        }
        break
    return filtered_tools


def _replace_tool_schema(
    filtered_tools: list[dict[str, Any]],
    tool_name: str,
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    for index, tool_def in enumerate(filtered_tools):
        if tool_def.get("function", {}).get("name") == tool_name:
            filtered_tools[index] = {"type": "function", "function": schema}
            break
    return filtered_tools


def _sanitize_schemas(filtered_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from tools.schema_sanitizer import sanitize_tool_schemas

        return sanitize_tool_schemas(filtered_tools)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Schema sanitization skipped: %s", exc)
        return filtered_tools


__all__ = [
    "LEGACY_TOOLSET_MAP",
    "ToolCatalogRequest",
    "RuntimeToolMerge",
    "ToolSourceMetadata",
    "ToolSurface",
    "build_tool_surface",
    "merge_runtime_tool_schemas",
]
