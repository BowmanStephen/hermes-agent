"""Tests for the deep tool catalog surface."""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace


def _tool(name: str, description: str = "tool") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_tool_catalog_returns_immutable_surface_with_source_metadata(monkeypatch):
    from agent import tool_catalog

    monkeypatch.setattr(tool_catalog, "validate_toolset", lambda name: name == "alpha")
    monkeypatch.setattr(tool_catalog, "resolve_toolset", lambda name: ["web_search", "terminal"])
    monkeypatch.setattr(
        tool_catalog.registry,
        "get_definitions",
        lambda names, quiet=False: [_tool(name) for name in sorted(names)],
    )

    surface = tool_catalog.build_tool_surface(
        tool_catalog.ToolCatalogRequest(enabled_toolsets=("alpha",), quiet_mode=True)
    )

    assert surface.valid_names == frozenset({"terminal", "web_search"})
    assert surface.requested_names == frozenset({"terminal", "web_search"})
    assert surface.source_metadata["terminal"].toolset == "alpha"
    assert surface.source_metadata["web_search"].source == "toolset"
    assert isinstance(surface.source_metadata, MappingProxyType)
    assert isinstance(surface.schemas, tuple)

    returned = surface.as_list()
    returned.append(_tool("mutated"))

    assert [tool["function"]["name"] for tool in surface.schemas] == [
        "terminal",
        "web_search",
    ]


def test_tool_catalog_applies_dynamic_schema_adapters(monkeypatch):
    from agent import tool_catalog

    monkeypatch.setattr(tool_catalog, "validate_toolset", lambda name: name == "browser")
    monkeypatch.setattr(tool_catalog, "resolve_toolset", lambda name: ["browser_navigate"])
    monkeypatch.setattr(
        tool_catalog.registry,
        "get_definitions",
        lambda names, quiet=False: [
            _tool(
                "browser_navigate",
                "Navigate. For simple information retrieval, prefer web_search or web_extract (faster, cheaper).",
            )
        ],
    )

    surface = tool_catalog.build_tool_surface(
        tool_catalog.ToolCatalogRequest(enabled_toolsets=("browser",), quiet_mode=True)
    )

    [schema] = surface.schemas
    assert schema["function"]["name"] == "browser_navigate"
    assert "prefer web_search or web_extract" not in schema["function"]["description"]


def test_tool_catalog_merges_runtime_schemas_with_deduped_metadata():
    from agent import tool_catalog

    merge = tool_catalog.merge_runtime_tool_schemas(
        [_tool("memory"), _tool("web_search")],
        [
            {"name": "memory", "description": "duplicate memory"},
            {"name": "honcho_search", "description": "memory provider"},
        ],
        source="runtime",
        toolset="memory_provider",
    )

    assert [tool["function"]["name"] for tool in merge.tools] == [
        "memory",
        "web_search",
        "honcho_search",
    ]
    assert merge.valid_names == frozenset({"memory", "web_search", "honcho_search"})
    assert merge.added_names == frozenset({"honcho_search"})
    assert merge.source_metadata["honcho_search"].source == "runtime"
    assert merge.source_metadata["honcho_search"].toolset == "memory_provider"
