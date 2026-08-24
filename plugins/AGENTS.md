# Plugin Guide

Read the repository operating rules first. Plugins extend Hermes at the edge;
they must not acquire plugin-specific changes in core files.

## Native plugins

- `PluginManager` discovers user, project, and entry-point plugins. A native
  plugin exports `register(ctx)` and may register supported hooks, tools, or a
  CLI command.
- Discovery normally occurs when `model_tools.py` imports. A path that needs
  plugin state earlier must call the idempotent discovery function itself.
- Keep native contracts additive: add optional keyword data, inspect callback
  signatures, preserve `PluginContext` methods, ignore unknown manifest fields,
  and supply defaults for new provider methods.
- Deprecations need a once-per-process warning, documented replacement and
  migration, and two subsequent minor releases before removal. Test frozen
  plugins through real discovery.

## Provider families

- Memory providers implement `MemoryProvider` and are orchestrated by
  `memory_manager.py`. Discovery enumerates without importing and is
  bundled-first, so a local directory cannot shadow a shipped provider.
- Model providers use their separate lazy discovery path. User providers may
  override bundled providers by name; do not import them through the general
  plugin manager as well.
- Context engines and image-generation providers follow the same ABC plus
  orchestrator pattern. Follow the existing local interface rather than adding
  special cases to the agent loop.

## Scope policy

New memory providers and integrations for third-party products belong in
standalone repositories installable under `~/.hermes/plugins/` or via entry
points. Do not add them as in-tree directories or modify core files to support
one plugin. If a real need exposes a missing capability, expand the generic
interface instead.
