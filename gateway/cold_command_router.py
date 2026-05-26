"""Cold-path slash command routing helpers for the gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import logging
import os
from typing import Any, Awaitable, Callable, Mapping, Optional

from gateway.cold_route_types import (
    ColdRouteContext,
    ColdRouteOutcome,
    ColdRouteResult,
)
from gateway.event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuickAliasRewrite:
    """Rewritten text and command after expanding a quick-command alias."""

    text: str
    command: str


@dataclass(frozen=True)
class ColdCommandDispatch:
    """Resolved cold-path command dispatch inputs."""

    command_dispatch: Any
    quick_commands: dict[str, Any]
    skill_commands: dict[str, Any]


@dataclass(frozen=True)
class CommandHookDecision:
    """Interpreted command hook outcome."""

    action: str
    response: Optional[str] = None
    command_name: Optional[str] = None
    raw_args: str = ""


@dataclass(frozen=True)
class BundleInvocation:
    """Bundle invocation payload for the agent path."""

    message: str
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillInvocationDecision:
    """Skill command decision for the agent path."""

    message: Optional[str] = None
    response: Optional[str] = None


COMMAND_HOOK_ALLOW = "allow"
COMMAND_HOOK_DENY = "deny"
COMMAND_HOOK_HANDLED = "handled"
COMMAND_HOOK_REWRITE = "rewrite"


def quick_commands_from_config(config: object) -> dict[str, Any]:
    """Return gateway quick commands from object or dict config."""

    if isinstance(config, dict):
        quick_commands = config.get("quick_commands", {}) or {}
    else:
        quick_commands = getattr(config, "quick_commands", {}) or {}
    return quick_commands if isinstance(quick_commands, dict) else {}


def resolve_builtin_precedence_quick_alias(
    *,
    config: object,
    command: Optional[str],
    command_args: str,
) -> Optional[QuickAliasRewrite]:
    """Expand quick-command aliases before built-in command dispatch."""

    if not command:
        return None
    from hermes_cli.commands import (
        CommandSurface,
        resolve_command_dispatch,
        select_command_handler,
    )

    if select_command_handler(CommandSurface.GATEWAY, command) is not None:
        return None
    quick_commands = quick_commands_from_config(config)
    alias_dispatch = resolve_command_dispatch(
        name=command,
        args=command_args,
        surface=CommandSurface.GATEWAY,
        quick_commands=quick_commands,
    )
    if alias_dispatch.route != "quick_alias":
        return None
    qcmd = quick_commands.get(alias_dispatch.handler_key, {}) or {}
    target = qcmd.get("target", "").strip()
    if not target:
        return None
    target = target if target.startswith("/") else f"/{target}"
    target_command = target.lstrip("/")
    rewritten_command = target_command.split()[0] if target_command else target_command
    return QuickAliasRewrite(
        text=f"{target} {command_args}".strip(),
        command=rewritten_command,
    )


def resolve_cold_command_dispatch(
    *,
    config: object,
    command: Optional[str],
    command_args: str,
    skill_commands_provider: Optional[Callable[[], dict[str, Any]]] = None,
) -> Optional[ColdCommandDispatch]:
    """Resolve quick-command, plugin, skill, and bundle dispatch metadata."""

    if not command:
        return None
    from hermes_cli.commands import CommandSurface, resolve_command_dispatch_with_sources

    quick_commands = quick_commands_from_config(config)
    if skill_commands_provider is None:
        try:
            from agent.skill_commands import get_skill_commands

            skill_commands_provider = get_skill_commands
        except Exception:
            skill_commands_provider = None
    try:
        skill_commands = skill_commands_provider() if skill_commands_provider else {}
    except Exception:
        skill_commands = {}
    command_dispatch = resolve_command_dispatch_with_sources(
        name=command,
        args=command_args,
        surface=CommandSurface.GATEWAY,
        quick_commands=quick_commands,
        skill_commands_provider=lambda: skill_commands,
    )
    return ColdCommandDispatch(
        command_dispatch=command_dispatch,
        quick_commands=quick_commands,
        skill_commands=skill_commands,
    )


def unavailable_gateway_command_response(command_name: str) -> str:
    return (
        f"Command `/{command_name}` isn't available from this gateway surface. "
        "Type /commands to see what's available here."
    )


def unknown_slash_command_response(command: str) -> str:
    return (
        f"Unknown command `/{command}`. "
        "Type /commands to see what's available, "
        "or resend without the leading slash to send as a regular message."
    )


def should_return_unknown_slash_command(*, command: str, known_command: bool) -> bool:
    return bool(command and not known_command)


def resolve_command_hook_decision(
    *,
    command: str,
    hook_results: list[Any],
) -> CommandHookDecision:
    """Interpret command hook return values in dispatch order."""

    for hook_result in hook_results:
        if not isinstance(hook_result, dict):
            continue
        decision = str(hook_result.get("decision", "")).strip().lower()
        if not decision or decision == COMMAND_HOOK_ALLOW:
            continue
        if decision == COMMAND_HOOK_DENY:
            message = hook_result.get("message")
            response = (
                message
                if isinstance(message, str) and message
                else f"Command `/{command}` was blocked by a hook."
            )
            return CommandHookDecision(COMMAND_HOOK_DENY, response=response)
        if decision == COMMAND_HOOK_HANDLED:
            message = hook_result.get("message")
            response = message if isinstance(message, str) and message else None
            return CommandHookDecision(COMMAND_HOOK_HANDLED, response=response)
        if decision == COMMAND_HOOK_REWRITE:
            new_command = str(hook_result.get("command_name", "")).strip().lstrip("/")
            if not new_command:
                continue
            return CommandHookDecision(
                COMMAND_HOOK_REWRITE,
                command_name=new_command,
                raw_args=str(hook_result.get("raw_args", "")).strip(),
            )
    return CommandHookDecision(COMMAND_HOOK_ALLOW)


async def execute_plugin_command(
    *,
    handler_key: str,
    raw_args: str,
    handler_lookup: Optional[Callable[[str], Any]] = None,
) -> Optional[str]:
    """Run a plugin slash command handler and normalize its response."""

    if handler_lookup is None:
        from hermes_cli.plugins import get_plugin_command_handler

        handler_lookup = get_plugin_command_handler
    plugin_handler = handler_lookup(handler_key)
    if not plugin_handler:
        return None
    result = plugin_handler(raw_args)
    if inspect.isawaitable(result):
        result = await result
    return str(result) if result else None


async def execute_quick_command(
    *,
    command_name: str,
    exec_cmd: str,
    env: Optional[dict[str, str]] = None,
    timeout_seconds: float = 30.0,
    create_subprocess_shell: Optional[Callable[..., Any]] = None,
    sanitize_env: Optional[Callable[[dict[str, str]], dict[str, str]]] = None,
    redact_text: Optional[Callable[[str], str]] = None,
) -> str:
    """Execute a configured quick command with sanitized environment."""

    if not exec_cmd:
        return f"Quick command '/{command_name}' has no command defined."

    if create_subprocess_shell is None:
        create_subprocess_shell = asyncio.create_subprocess_shell
    if sanitize_env is None:
        from tools.environments.local import _sanitize_subprocess_env

        sanitize_env = _sanitize_subprocess_env
    if redact_text is None:
        from agent.redact import redact_sensitive_text

        redact_text = redact_sensitive_text

    try:
        sanitized_env = sanitize_env(dict(env or os.environ))
        proc = await create_subprocess_shell(
            exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=sanitized_env,
        )
        communicate_task = asyncio.create_task(proc.communicate())
        try:
            stdout, stderr = await asyncio.wait_for(
                communicate_task,
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            communicate_task.cancel()
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
            return "Quick command timed out (30s)."
        output = (stdout or stderr).decode().strip()
        if output:
            output = redact_text(output)
        return output if output else "Command returned no output."
    except Exception as exc:
        return f"Quick command error: {exc}"


def build_bundle_invocation(
    *,
    bundle_key: Optional[str],
    user_instruction: str,
    task_id: str,
    bundle_builder: Optional[Callable[..., Any]] = None,
) -> Optional[BundleInvocation]:
    """Build the agent-facing message for a skill bundle command."""

    if bundle_key is None:
        return None
    if bundle_builder is None:
        from agent.skill_bundles import build_bundle_invocation_message

        bundle_builder = build_bundle_invocation_message
    bundle_result = bundle_builder(bundle_key, user_instruction, task_id=task_id)
    if not bundle_result:
        return None
    msg, _loaded, missing = bundle_result
    return BundleInvocation(message=msg, missing=tuple(missing or ()))


def build_skill_invocation_decision(
    *,
    command_dispatch: Any,
    command: str,
    skill_commands: dict[str, Any],
    platform_value: Optional[str],
    user_instruction: str,
    task_id: str,
    unavailable_skill_checker: Callable[[str], Optional[str]],
    known_command_checker: Callable[[str], bool],
    disabled_skill_names_provider: Optional[Callable[..., set[str]]] = None,
    skill_key_resolver: Optional[Callable[[str], Optional[str]]] = None,
    skill_message_builder: Optional[Callable[..., Optional[str]]] = None,
) -> SkillInvocationDecision:
    """Build the agent-facing message or response for a skill slash command."""

    if skill_key_resolver is None:
        from agent.skill_commands import resolve_skill_command_key

        skill_key_resolver = resolve_skill_command_key
    if skill_message_builder is None:
        from agent.skill_commands import build_skill_invocation_message

        skill_message_builder = build_skill_invocation_message

    cmd_key = (
        command_dispatch.handler_slash_key
        if command_dispatch.route == "skill"
        else skill_key_resolver(command)
    )
    if cmd_key is not None:
        skill_name = skill_commands[cmd_key].get("name", "")
        if platform_value and skill_name:
            if disabled_skill_names_provider is None:
                from agent.skill_utils import get_disabled_skill_names

                disabled_skill_names_provider = get_disabled_skill_names
            if skill_name in disabled_skill_names_provider(platform=platform_value):
                return SkillInvocationDecision(
                    response=(
                        f"The **{skill_name}** skill is disabled for {platform_value}.\n"
                        "Enable it with: `hermes skills config`"
                    )
                )
        msg = skill_message_builder(cmd_key, user_instruction, task_id=task_id)
        return SkillInvocationDecision(message=msg if msg else None)

    unavailable_msg = unavailable_skill_checker(command)
    if unavailable_msg:
        return SkillInvocationDecision(response=unavailable_msg)
    if should_return_unknown_slash_command(
        command=command,
        known_command=known_command_checker(command.replace("_", "-")),
    ):
        return SkillInvocationDecision(response=unknown_slash_command_response(command))
    return SkillInvocationDecision()


async def orchestrate_cold_command(
    ctx: ColdRouteContext,
    *,
    event_bus: EventBus,
) -> ColdRouteResult:
    """Orchestrate gateway cold-path slash command dispatch.

    Resolves aliases, access control, hooks, built-in handlers, quick commands,
    plugins, skills, and bundles. Returns a terminal response or signals that
    the caller should continue to the warm agent path (possibly with mutated
    ``ctx.event.text``).
    """
    from hermes_cli.commands import (
        CommandSurface,
        is_gateway_known_command,
        resolve_command_invocation,
        resolve_plugin_command_dispatch,
    )
    from gateway.command_registry import (
        get_gateway_command_handler,
        resolve_special_cold_command,
    )

    event = ctx.event
    source = ctx.source
    command = event.get_command()

    _cmd_invocation = (
        resolve_command_invocation(
            name=command,
            args=event.get_command_args().strip(),
            surface=CommandSurface.GATEWAY,
        )
        if command
        else None
    )
    canonical = _cmd_invocation.canonical_name if _cmd_invocation else command

    _alias_rewrite = resolve_builtin_precedence_quick_alias(
        config=ctx.config,
        command=command,
        command_args=event.get_command_args().strip(),
    )
    if _alias_rewrite is not None:
        event.text = _alias_rewrite.text
        command = _alias_rewrite.command
        _cmd_invocation = (
            resolve_command_invocation(
                name=command,
                args=event.get_command_args().strip(),
                surface=CommandSurface.GATEWAY,
            )
            if command
            else None
        )
        canonical = _cmd_invocation.canonical_name if _cmd_invocation else command

    if command:
        _hook_dispatch = resolve_plugin_command_dispatch(
            name=command,
            args=event.get_command_args().strip(),
            surface=CommandSurface.GATEWAY,
        )
        if _hook_dispatch.route == "plugin":
            canonical = _hook_dispatch.handler_key

    if command and canonical and is_gateway_known_command(canonical):
        _denied = ctx.check_slash_access(source, canonical)
        if _denied is not None:
            return ColdRouteResult(ColdRouteOutcome.RETURN, _denied)

    if command and is_gateway_known_command(canonical):
        raw_args = event.get_command_args().strip()
        hook_ctx = {
            "platform": source.platform.value if source.platform else "",
            "user_id": source.user_id,
            "command": canonical,
            "raw_command": command,
            "args": raw_args,
            "raw_args": raw_args,
        }
        try:
            hook_results = await ctx.hooks_emit_collect(
                f"command:{canonical}", hook_ctx
            )
        except Exception as _hook_err:
            logger.debug(
                "command:%s hook dispatch failed (non-fatal): %s",
                canonical,
                _hook_err,
            )
            hook_results = []

        hook_decision = resolve_command_hook_decision(
            command=command,
            hook_results=hook_results,
        )
        if hook_decision.action == COMMAND_HOOK_DENY:
            return ColdRouteResult(ColdRouteOutcome.RETURN, hook_decision.response)
        if hook_decision.action == COMMAND_HOOK_HANDLED:
            return ColdRouteResult(ColdRouteOutcome.RETURN, hook_decision.response)
        if hook_decision.action == COMMAND_HOOK_REWRITE:
            event.text = f"/{hook_decision.command_name} {hook_decision.raw_args}".strip()
            command = event.get_command()
            if command:
                _cmd_invocation = resolve_command_invocation(
                    name=command,
                    args=hook_decision.raw_args,
                    surface=CommandSurface.GATEWAY,
                )
            else:
                _cmd_invocation = None
            canonical = (
                _cmd_invocation.canonical_name
                if _cmd_invocation
                else command
            )
            if command:
                _hook_dispatch = resolve_plugin_command_dispatch(
                    name=command,
                    args=hook_decision.raw_args,
                    surface=CommandSurface.GATEWAY,
                )
                if _hook_dispatch.route == "plugin":
                    canonical = _hook_dispatch.handler_key

    _special_telegram_root_lobby = (
        ctx.is_telegram_topic_root_lobby(source) if canonical == "new" else False
    )
    special_command_decision = resolve_special_cold_command(
        canonical,
        command_args=event.get_command_args().strip(),
        telegram_root_lobby=_special_telegram_root_lobby,
        telegram_root_new_message=(
            ctx.telegram_topic_root_new_message()
            if _special_telegram_root_lobby
            else ""
        ),
    )
    if special_command_decision is not None:
        if special_command_decision.response is not None:
            return ColdRouteResult(
                ColdRouteOutcome.RETURN,
                special_command_decision.response,
            )
        if special_command_decision.rewrite_text is not None:
            try:
                event.text = special_command_decision.rewrite_text
            except Exception:
                pass
        if special_command_decision.confirm_command is not None:

            async def _do_special_command():
                if special_command_decision.confirm_command == "new":
                    return await ctx.handle_reset_command(event)
                if special_command_decision.confirm_command == "undo":
                    return await ctx.handle_undo_command(event)
                return None

            confirm_result = await ctx.maybe_confirm_destructive_slash(
                event=event,
                command=special_command_decision.confirm_command,
                title=special_command_decision.confirm_title or "",
                detail=special_command_decision.confirm_detail or "",
                execute=_do_special_command,
            )
            return ColdRouteResult(ColdRouteOutcome.RETURN, confirm_result)

    await event_bus.emit(
        "gateway.cold_command.resolved",
        {
            "command": command,
            "canonical": canonical,
            "task_id": ctx.task_id,
            "platform": source.platform.value if source.platform else "",
        },
    )

    gateway_handler = get_gateway_command_handler(ctx.gateway_handlers, canonical)
    if gateway_handler is not None:
        handler_result = await gateway_handler(event)
        return ColdRouteResult(ColdRouteOutcome.RETURN, handler_result)

    if ctx.draining:
        draining_msg = (
            f"⏳ Gateway is {ctx.status_action_gerund()} "
            "and is not accepting new work right now."
        )
        return ColdRouteResult(ColdRouteOutcome.RETURN, draining_msg)

    _cold_dispatch = resolve_cold_command_dispatch(
        config=ctx.config,
        command=command,
        command_args=event.get_command_args().strip(),
    )
    quick_commands = _cold_dispatch.quick_commands if _cold_dispatch else {}
    skill_cmds = _cold_dispatch.skill_commands if _cold_dispatch else {}
    command_dispatch = _cold_dispatch.command_dispatch if _cold_dispatch else None

    if command:
        if command_dispatch and command_dispatch.route == "quick_exec":
            qcmd = quick_commands.get(command_dispatch.handler_key, {})
            quick_result = await execute_quick_command(
                command_name=command,
                exec_cmd=qcmd.get("command", ""),
                env=os.environ.copy(),
            )
            return ColdRouteResult(ColdRouteOutcome.RETURN, quick_result)
        if command_dispatch and command_dispatch.route == "quick_alias":
            target = (command_dispatch.target or "").strip()
            if target:
                target = target if target.startswith("/") else f"/{target}"
                target_command = target.lstrip("/")
                user_args = event.get_command_args().strip()
                event.text = f"{target} {user_args}".strip()
                command = (
                    target_command.split()[0]
                    if target_command
                    else target_command
                )
                _cold_dispatch = resolve_cold_command_dispatch(
                    config={},
                    command=command,
                    command_args=user_args,
                    skill_commands_provider=lambda: skill_cmds,
                )
                command_dispatch = (
                    _cold_dispatch.command_dispatch
                    if _cold_dispatch
                    else None
                )
            else:
                return ColdRouteResult(
                    ColdRouteOutcome.RETURN,
                    f"Quick command '/{command}' has no target defined.",
                )
        elif command_dispatch and command_dispatch.route == "quick_unsupported":
            return ColdRouteResult(
                ColdRouteOutcome.RETURN,
                (
                    f"Quick command '/{command}' has unsupported type "
                    "(supported: 'exec', 'alias')."
                ),
            )
        elif command_dispatch and command_dispatch.route == "unavailable":
            return ColdRouteResult(
                ColdRouteOutcome.RETURN,
                unavailable_gateway_command_response(
                    command_dispatch.invocation.canonical_name
                ),
            )

    if command and command_dispatch and command_dispatch.route == "plugin":
        try:
            plugin_result = await execute_plugin_command(
                handler_key=command_dispatch.handler_key,
                raw_args=event.get_command_args().strip(),
            )
            return ColdRouteResult(ColdRouteOutcome.RETURN, plugin_result)
        except Exception as exc:
            logger.debug("Plugin command dispatch failed (non-fatal): %s", exc)

    _bundle_handled = False
    if command and command_dispatch and command_dispatch.route == "skill_bundle":
        try:
            bundle_result = build_bundle_invocation(
                bundle_key=command_dispatch.handler_slash_key,
                user_instruction=event.get_command_args().strip(),
                task_id=ctx.task_id,
            )
            if bundle_result:
                event.text = bundle_result.message
                _bundle_handled = True
                if bundle_result.missing:
                    logger.info(
                        "Bundle %s skipped missing skills: %s",
                        command_dispatch.handler_slash_key,
                        ", ".join(bundle_result.missing),
                    )
        except Exception as exc:
            logger.debug("Bundle dispatch failed (non-fatal): %s", exc)

    if (
        command
        and command_dispatch
        and command_dispatch.route in {"skill", "unknown"}
        and not _bundle_handled
    ):
        try:
            skill_decision = build_skill_invocation_decision(
                command_dispatch=command_dispatch,
                command=command,
                skill_commands=skill_cmds,
                platform_value=source.platform.value if source.platform else None,
                user_instruction=event.get_command_args().strip(),
                task_id=ctx.task_id,
                unavailable_skill_checker=ctx.unavailable_skill_checker,
                known_command_checker=is_gateway_known_command,
            )
            if skill_decision.response is not None:
                if skill_decision.response.startswith("Unknown command"):
                    logger.warning(
                        "Unrecognized slash command /%s from %s — "
                        "replying with unknown-command notice",
                        command,
                        source.platform.value if source.platform else "?",
                    )
                return ColdRouteResult(
                    ColdRouteOutcome.RETURN,
                    skill_decision.response,
                )
            if skill_decision.message:
                event.text = skill_decision.message
        except Exception as exc:
            logger.debug("Skill command check failed (non-fatal): %s", exc)

    if ctx.is_telegram_topic_root_lobby(source):
        if ctx.should_send_telegram_lobby_reminder(source):
            return ColdRouteResult(
                ColdRouteOutcome.RETURN,
                ctx.telegram_topic_root_lobby_message(),
            )
        return ColdRouteResult(ColdRouteOutcome.RETURN, None)

    return ColdRouteResult(ColdRouteOutcome.WARM_AGENT)
