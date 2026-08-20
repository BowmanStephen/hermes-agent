from __future__ import annotations


def test_invalid_cli_toolsets_discovers_plugins_before_validation(monkeypatch):
    import cli

    discovered = []
    monkeypatch.setattr(
        "hermes_cli.plugins.discover_plugins",
        lambda: discovered.append(True),
    )
    monkeypatch.setattr(
        cli,
        "validate_toolset",
        lambda name: name in {"plugin_demo", "terminal"},
    )

    invalid = cli._invalid_cli_toolsets(
        ["plugin_demo", "terminal", "stale_plugin"],
        {"configured_mcp"},
    )

    assert discovered == [True]
    assert invalid == ["stale_plugin"]


def test_invalid_cli_toolsets_keeps_mcp_names_out_of_validation(monkeypatch):
    import cli

    monkeypatch.setattr(cli, "validate_toolset", lambda _name: False)

    invalid = cli._invalid_cli_toolsets(
        ["configured_mcp", "stale_plugin"],
        {"configured_mcp"},
    )

    assert invalid == ["stale_plugin"]
