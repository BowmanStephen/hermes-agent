"""Model command handlers extracted from GatewayRunner."""
from __future__ import annotations

from ._imports import *  # noqa: F403
from .base import RunnerBackedCommandService


class RuntimeModeGatewayCommands(RunnerBackedCommandService):
    async def _handle_codex_runtime_command(self, event: MessageEvent) -> str:
        """Handle /codex-runtime command in the gateway.

        Same surface as the CLI handler in cli.py:
            /codex-runtime                  — show current state
            /codex-runtime auto             — Hermes default runtime
            /codex-runtime codex_app_server — codex subprocess runtime
            /codex-runtime on / off         — synonyms

        On change, the cached agent for this session is evicted so the next
        message creates a fresh AIAgent with the new api_mode wired in
        (avoids prompt-cache invalidation mid-session)."""
        from hermes_cli import codex_runtime_switch as crs

        raw_args = event.get_command_args().strip() if event else ""
        new_value, errors = crs.parse_args(raw_args)
        if errors:
            return "❌ " + "\n❌ ".join(errors)

        # Load + persist via the same helpers used for /model and /yolo
        try:
            from hermes_cli.config import load_config, save_config
        except Exception as exc:
            return f"❌ Could not load config: {exc}"
        cfg = load_config()

        result = crs.apply(
            cfg,
            new_value,
            persist_callback=(save_config if new_value is not None else None),
        )

        # On a real change, evict the cached agent so the new runtime takes
        # effect on the next message rather than waiting for cache TTL.
        if result.success and new_value is not None and result.requires_new_session:
            try:
                session_key = self._session_key_for_source(event.source)
                self._evict_cached_agent(session_key)
            except Exception:
                logger.debug("could not evict cached agent after codex-runtime change",
                             exc_info=True)

        prefix = "✓" if result.success else "✗"
        return f"{prefix} {result.message}"

    async def _handle_personality_command(self, event: MessageEvent) -> str:
        """Handle /personality command - list or set a personality."""
        from hermes_constants import display_hermes_home

        args = event.get_command_args().strip().lower()
        config_path = _hermes_home / 'config.yaml'

        try:
            config = _load_gateway_config()
            personalities = cfg_get(config, "agent", "personalities", default={})
        except Exception:
            config = {}
            personalities = {}

        if not personalities:
            return t("gateway.personality.none_configured", path=display_hermes_home())

        if not args:
            lines = [t("gateway.personality.header")]
            lines.append(t("gateway.personality.none_option"))
            for name, prompt in personalities.items():
                if isinstance(prompt, dict):
                    preview = prompt.get("description") or prompt.get("system_prompt", "")[:50]
                else:
                    preview = prompt[:50] + "..." if len(prompt) > 50 else prompt
                lines.append(t("gateway.personality.item", name=name, preview=preview))
            lines.append(t("gateway.personality.usage"))
            return "\n".join(lines)

        def _resolve_prompt(value):
            if isinstance(value, dict):
                parts = [value.get("system_prompt", "")]
                if value.get("tone"):
                    parts.append(f'Tone: {value["tone"]}')
                if value.get("style"):
                    parts.append(f'Style: {value["style"]}')
                return "\n".join(p for p in parts if p)
            return str(value)

        if args in {"none", "default", "neutral"}:
            try:
                if "agent" not in config or not isinstance(config.get("agent"), dict):
                    config["agent"] = {}
                config["agent"]["system_prompt"] = ""
                atomic_yaml_write(config_path, config)
            except Exception as e:
                return t("gateway.personality.save_failed", error=str(e))
            self._ephemeral_system_prompt = ""
            return t("gateway.personality.cleared")
        elif args in personalities:
            new_prompt = _resolve_prompt(personalities[args])

            # Write to config.yaml, same pattern as CLI save_config_value.
            try:
                if "agent" not in config or not isinstance(config.get("agent"), dict):
                    config["agent"] = {}
                config["agent"]["system_prompt"] = new_prompt
                atomic_yaml_write(config_path, config)
            except Exception as e:
                return t("gateway.personality.save_failed", error=str(e))

            # Update in-memory so it takes effect on the very next message.
            self._ephemeral_system_prompt = new_prompt

            return t("gateway.personality.set_to", name=args)

        available = "`none`, " + ", ".join(f"`{n}`" for n in personalities)
        return t("gateway.personality.unknown", name=args, available=available)

    async def _handle_reasoning_command(self, event: MessageEvent) -> str:
        """Handle /reasoning command — manage reasoning effort and display toggle.

        Usage:
            /reasoning                       Show current effort level and display state
            /reasoning <level>               Set reasoning effort for this session only
            /reasoning <level> --global      Persist reasoning effort to config.yaml
            /reasoning reset                 Clear this session's reasoning override
            /reasoning show|on               Show model reasoning in responses
            /reasoning hide|off              Hide model reasoning from responses
        """
        import yaml

        raw_args = event.get_command_args().strip()
        args, persist_global = self._parse_reasoning_command_args(raw_args)
        config_path = _hermes_home / "config.yaml"
        session_key = self._session_key_for_source(event.source)
        self._show_reasoning = self._load_show_reasoning()
        self._reasoning_config = self._resolve_session_reasoning_config(
            source=event.source,
            session_key=session_key,
        )

        def _save_config_key(key_path: str, value):
            """Save a dot-separated key to config.yaml."""
            try:
                user_config = {}
                if config_path.exists():
                    with open(config_path, encoding="utf-8") as f:
                        user_config = yaml.safe_load(f) or {}
                keys = key_path.split(".")
                current = user_config
                for k in keys[:-1]:
                    if k not in current or not isinstance(current[k], dict):
                        current[k] = {}
                    current = current[k]
                current[keys[-1]] = value
                atomic_yaml_write(config_path, user_config)
                return True
            except Exception as e:
                logger.error("Failed to save config key %s: %s", key_path, e)
                return False

        if not raw_args:
            # Show current state
            rc = self._reasoning_config
            if rc is None:
                level = t("gateway.reasoning.level_default")
            elif rc.get("enabled") is False:
                level = t("gateway.reasoning.level_disabled")
            else:
                level = rc.get("effort", "medium")
            display_state = (
                t("gateway.reasoning.display_on")
                if self._show_reasoning
                else t("gateway.reasoning.display_off")
            )
            has_session_override = session_key in (getattr(self, "_session_reasoning_overrides", {}) or {})
            scope = (
                t("gateway.reasoning.scope_session")
                if has_session_override
                else t("gateway.reasoning.scope_global")
            )
            return t(
                "gateway.reasoning.status",
                level=level,
                scope=scope,
                display=display_state,
            )

        # Display toggle (per-platform)
        platform_key = _platform_config_key(event.source.platform)
        if args in {"show", "on"}:
            self._show_reasoning = True
            _save_config_key(f"display.platforms.{platform_key}.show_reasoning", True)
            return t("gateway.reasoning.display_set_on", platform=platform_key)

        if args in {"hide", "off"}:
            self._show_reasoning = False
            _save_config_key(f"display.platforms.{platform_key}.show_reasoning", False)
            return t("gateway.reasoning.display_set_off", platform=platform_key)

        # Effort level change
        effort = args.strip()
        if effort == "reset":
            if persist_global:
                return t("gateway.reasoning.reset_global_unsupported")
            self._set_session_reasoning_override(session_key, None)
            self._reasoning_config = self._load_reasoning_config()
            self._evict_cached_agent(session_key)
            return t("gateway.reasoning.reset_done")
        if effort == "none":
            parsed = {"enabled": False}
        elif effort in {"minimal", "low", "medium", "high", "xhigh"}:
            parsed = {"enabled": True, "effort": effort}
        else:
            return t(
                "gateway.reasoning.unknown_arg",
                arg=effort or raw_args.lower(),
            )

        self._reasoning_config = parsed
        if persist_global:
            if _save_config_key("agent.reasoning_effort", effort):
                self._set_session_reasoning_override(session_key, None)
                self._evict_cached_agent(session_key)
                return t("gateway.reasoning.set_global", effort=effort)
            self._set_session_reasoning_override(session_key, parsed)
            self._evict_cached_agent(session_key)
            return t("gateway.reasoning.set_global_save_failed", effort=effort)

        self._set_session_reasoning_override(session_key, parsed)
        self._evict_cached_agent(session_key)
        return t("gateway.reasoning.set_session", effort=effort)
