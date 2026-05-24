"""Tests for the plugin tool-contract validator.

Pure unit tests — no plugin loading, no I/O. They assert that the contract
between a plugin manifest's advertised ``provides_tools`` and the tools the
plugin actually registered is checked correctly.
"""

from hermes_cli.plugin_tool_contract import validate_tool_contract


def test_advertised_but_not_registered_is_error():
    # The hermes-lcm case (upstream #200): manifest advertises 7 tools, but
    # register() wired up none of them.
    advertised = [
        "lcm_grep",
        "lcm_load_session",
        "lcm_describe",
        "lcm_expand",
        "lcm_expand_query",
        "lcm_status",
        "lcm_doctor",
    ]
    violations = validate_tool_contract(advertised, registered=[])

    assert len(violations) == 7
    assert all(v.kind == "advertised-not-registered" for v in violations)
    assert all(v.severity == "error" for v in violations)
    assert {v.tool for v in violations} == set(advertised)


def test_advertised_set_fully_registered_has_no_violations():
    tools = ["calculate", "unit_convert"]
    assert validate_tool_contract(tools, registered=tools) == []


def test_registered_but_not_advertised_is_info():
    violations = validate_tool_contract(["calculate"], registered=["calculate", "secret_tool"])
    assert len(violations) == 1
    assert violations[0].tool == "secret_tool"
    assert violations[0].kind == "registered-not-advertised"
    assert violations[0].severity == "info"


def test_empty_manifest_and_no_registrations_is_clean():
    assert validate_tool_contract([], registered=[]) == []


# ── Loader integration ───────────────────────────────────────────────────────


def test_loader_records_violation_for_advertised_unregistered_tool(tmp_path, monkeypatch):
    """A plugin that advertises a tool its register() never wires up still
    loads (fail-open), but the loader records an advertised-not-registered
    violation — the hermes-lcm #200 failure mode, now visible."""
    from tests.hermes_cli.test_plugins import _make_plugin_dir
    from hermes_cli.plugins import PluginManager

    plugins_dir = tmp_path / "hermes_test" / "plugins"
    _make_plugin_dir(
        plugins_dir,
        "ghost_plugin",
        register_body="pass",  # advertises a tool but registers nothing
        manifest_extra={"provides_tools": ["ghost_tool"]},
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes_test"))

    mgr = PluginManager()
    mgr.discover_and_load()

    loaded = mgr._plugins["ghost_plugin"]
    assert loaded.enabled  # fail-open: the plugin still loads
    violations = loaded.contract_violations
    assert len(violations) == 1
    assert violations[0].tool == "ghost_tool"
    assert violations[0].kind == "advertised-not-registered"
    assert violations[0].severity == "error"
