# Built-in Tools and Toolsets Guide

Read the repository operating rules first. Most new capabilities should be a
skill, CLI command, service-gated tool, plugin, or MCP server—not a new core
tool.

## Adding a built-in tool

- A `tools/*.py` module with a top-level `registry.register()` is discovered
  automatically. Register a handler that returns a JSON string.
- Exposure is separate from registration: explicitly add the name to the
  appropriate toolset in `toolsets.py`. Add to `_HERMES_CORE_TOOLS` only when
  every session should pay its schema cost.
- Use `check_fn` for a configured prerequisite or reachability, not client
  surface detection. Put niche capabilities in named, opt-in toolsets.
- Schema descriptions must be profile-aware (`display_hermes_home()`), and
  persistent state must use `get_hermes_home()`.
- Do not add pagination to instructional content the model must read in full;
  models will stop after the first page.

## Contracts to preserve

- `_last_resolved_tool_names` in `model_tools.py` is process-global; never
  treat it as session-local state.
- Do not hard-code cross-tool references in schema descriptions. The referenced
  tool may not be present in the caller's toolset.
- Tools must be reachable through the real registry, availability, dispatch,
  and error-wrapping path in at least one test.
