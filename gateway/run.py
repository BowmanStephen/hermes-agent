"""
Gateway runner - entry point for messaging platform integrations.

This module provides:
- start_gateway(): Start all configured platform adapters
- GatewayRunner: Main class managing the gateway lifecycle

Usage:
    # Start the gateway
    python -m gateway.run

    # Or from CLI
    python cli.py --gateway
"""

# IMPORTANT: hermes_bootstrap must be the very first import — UTF-8 stdio
# on Windows.  No-op on POSIX.  See hermes_bootstrap.py for full rationale.
try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    # Graceful fallback when hermes_bootstrap isn't registered in the venv
    # yet — happens during partial ``hermes update`` where git-reset landed
    # new code but ``uv pip install -e .`` didn't finish.  Missing bootstrap
    # means UTF-8 stdio setup is skipped on Windows; POSIX is unaffected.
    pass

import asyncio
import dataclasses
import inspect
import json
import logging
import os
import re
import shlex
import sys
import signal
import tempfile
import threading
import time
import sqlite3
import weakref as _weakref
from collections import OrderedDict
from contextvars import copy_context
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Any, List, Union

_gateway_runner_ref: _weakref.ref = lambda: None

# account_usage imports the OpenAI SDK chain (~230 ms). Only needed by
# /usage; we still import it at module top in the gateway because test
# patches (tests/gateway/test_usage_command.py) target
# `gateway.run.fetch_account_usage` as a module-level attribute. The
# gateway is a long-running daemon, so its boot cost matters less than
# preserving the established test-patch surface.
from agent.account_usage import fetch_account_usage, render_account_usage_lines
from agent.async_utils import safe_schedule_threadsafe
from agent.i18n import t
from hermes_cli.config import cfg_get

from gateway.event_bus import event_bus
from gateway.message_router import MessageRouter
from gateway.command_registry import CommandHandlerRegistry


# --- Agent cache tuning ---------------------------------------------------
# Bounds the per-session AIAgent cache to prevent unbounded growth in
# long-lived gateways (each AIAgent holds LLM clients, tool schemas,
# memory providers, etc.).  LRU order + idle TTL eviction are enforced
# from _enforce_agent_cache_cap() and _session_expiry_watcher() below.
_AGENT_CACHE_MAX_SIZE = 128
_AGENT_CACHE_IDLE_TTL_SECS = 3600.0  # evict agents idle for >1h
_TELEGRAM_COMMAND_MENTION_RE = re.compile(r"(?<![\w:/])/([A-Za-z0-9][A-Za-z0-9_-]*)")

_TELEGRAM_NOISY_STATUS_RE = re.compile(
    r"("  # transient/auxiliary status that should stay in logs, not Telegram chat
    r"auxiliary\s+.+\s+failed"
    r"|compression\s+summary\s+failed"
    r"|fallback\s+context\s+marker"
    r"|configured\s+compression\s+model\s+.+\s+failed"
    r"|no\s+auxiliary\s+llm\s+provider\s+configured"
    r"|auto-lowered\s+compression\s+threshold"
    r"|preflight\s+compression"
    r"|rate\s+limited\.\s+waiting\s+\d"
    r"|retrying\s+in\s+\d"
    r"|max\s+retries\s+\(\d+\).*(?:trying\s+fallback|exhausted|invalid\s+responses)"
    r"|stream\s+(?:drop|drop\s+mid\s+tool-call).+retry\s+\d"
    r"|stale\s+connections\s+from\s+a\s+previous\s+provider\s+issue"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_GATEWAY_PROVIDER_ERROR_RE = re.compile(
    r"("  # infrastructure/provider error preambles, not ordinary assistant prose
    r"api\s+(?:call\s+)?failed"
    r"|provider\s+authentication\s+failed"
    r"|non-retryable\s+error"
    r"|rate\s+limited\s+after\s+\d+\s+retries"
    r"|error\s+code\s*:"
    r"|\bhttp\s*\d{3}\b"
    r"|incorrect\s+api\s+key"
    r"|invalid\s+api\s+key"
    r")",
    re.IGNORECASE,
)

_GATEWAY_PROVIDER_POLICY_RE = re.compile(
    r"("  # raw provider policy/safety bodies are noisy and may be sensitive
    r"cybersecurity\s+risk"
    r"|security\s+policy"
    r"|safety\s+policy"
    r"|policy\s+violation"
    r"|violat(?:e|es|ed|ion)"
    r"|blocked\s+(?:because|by|under)"
    r"|request\s+(?:was\s+)?(?:blocked|rejected)"
    r"|disallowed"
    r"|moderation"
    r")",
    re.IGNORECASE,
)

_GATEWAY_AUTH_ERROR_RE = re.compile(
    r"(provider\s+authentication\s+failed|incorrect\s+api\s+key|invalid\s+api\s+key|\b401\b)",
    re.IGNORECASE,
)

_GATEWAY_RATE_LIMIT_RE = re.compile(
    r"(rate\s+limit|rate-limited|\b429\b|quota|usage\s+limit)",
    re.IGNORECASE,
)

_GATEWAY_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._\-]{20,}\b"),
)


def _gateway_platform_value(platform: Any) -> str:
    """Return a normalized gateway platform value for enums or raw strings."""
    return str(getattr(platform, "value", platform) or "").strip().lower()


def _redact_gateway_user_facing_secrets(text: str) -> str:
    """Best-effort secret redaction before text can leave the gateway."""
    redacted = str(text or "")
    for pattern in _GATEWAY_SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", redacted)
    return redacted


def _gateway_provider_error_reply(text: str) -> str:
    """Map raw provider/API errors to a short user-safe Telegram reply."""
    if _GATEWAY_AUTH_ERROR_RE.search(text):
        return (
            "⚠️ Provider authentication failed. Check the configured credentials; "
            "raw provider details are in the gateway logs."
        )
    if _GATEWAY_PROVIDER_POLICY_RE.search(text):
        return (
            "⚠️ The model provider rejected the request. I kept the raw provider "
            "error out of chat; check gateway logs for details or try rephrasing."
        )
    if _GATEWAY_RATE_LIMIT_RE.search(text):
        return "⏱️ The model provider is rate-limiting requests. Please wait a moment and try again."
    return (
        "⚠️ The model provider failed after retries. I kept raw provider details "
        "out of chat; check gateway logs for diagnostics."
    )


_GATEWAY_PROVIDER_ERROR_SHAPE_RE = re.compile(
    r"^\s*(\W*\s*)?("
    r"api\s+(?:call\s+)?failed"
    r"|provider\s+authentication\s+failed"
    r"|non-retryable\s+error"
    r"|rate\s+limited\s+after\s+\d+\s+retries"
    r"|error\s+code\s*:"
    r"|http\s*\d{3}\b"
    r"|incorrect\s+api\s+key"
    r"|invalid\s+api\s+key"
    r")",
    re.IGNORECASE,
)


def _looks_like_gateway_provider_error(text: str) -> bool:
    """True when text is infrastructure/provider failure, not normal content.

    Two heuristics combined so the rewrite only fires on actual provider
    error envelopes, not on assistant prose that happens to mention an
    HTTP status code:

    1. The text is short — real provider errors are 1–3 lines of envelope
       text; assistant answers are usually longer.
    2. AND the error marker appears at the start of the message (optionally
       behind a punctuation/symbol prefix), not buried mid-paragraph in an
       explanation like "HTTP 404 means 'not found' — ...".
    """
    if not text:
        return False
    body = str(text).strip()
    # Provider failure envelopes are short. Assistant answers that happen
    # to mention HTTP status codes ("HTTP 404 means...") tend to be longer.
    if len(body) > 400 or body.count("\n") > 4:
        return False
    return bool(_GATEWAY_PROVIDER_ERROR_SHAPE_RE.search(body))


def _sanitize_gateway_final_response(platform: Any, text: str) -> str:
    """Sanitize final gateway replies before sending them to high-noise chats.

    Telegram is Bob's mobile inbox, so it should receive concise, safe provider
    failure categories instead of raw HTTP bodies, request IDs, or policy text.
    Other platforms keep the existing behaviour for now.
    """
    if not text:
        return text
    if _gateway_platform_value(platform) != "telegram":
        return text

    redacted = _redact_gateway_user_facing_secrets(str(text))
    if _looks_like_gateway_provider_error(redacted):
        return _gateway_provider_error_reply(redacted)
    return redacted


def _prepare_gateway_status_message(platform: Any, event_type: str, message: str) -> Optional[str]:
    """Filter/sanitize agent status callbacks before platform delivery."""
    text = str(message or "").strip()
    if not text:
        return None
    if _gateway_platform_value(platform) != "telegram":
        return text

    text = _redact_gateway_user_facing_secrets(text)
    if _TELEGRAM_NOISY_STATUS_RE.search(text):
        return None
    if _looks_like_gateway_provider_error(text):
        return _gateway_provider_error_reply(text)
    return text


def _telegramize_command_mentions(text: str, platform: Any) -> str:
    """Rewrite slash-command mentions to Telegram-valid command names.

    Telegram Bot API command names allow only lowercase letters, digits, and
    underscores.  Keep other platform renderings unchanged, but normalize
    Telegram help text so command mentions remain clickable/valid there.
    """
    platform_value = getattr(platform, "value", platform)
    if platform_value != "telegram":
        return text

    from hermes_cli.commands import _sanitize_telegram_name

    def _replace(match: re.Match[str]) -> str:
        sanitized = _sanitize_telegram_name(match.group(1))
        return f"/{sanitized}" if sanitized else match.group(0)

    return _TELEGRAM_COMMAND_MENTION_RE.sub(_replace, text)


# Only auto-continue interrupted gateway turns while the interruption is fresh.
# Stale tool-tail/resume markers can otherwise revive an unrelated old task
# after a gateway restart when the user's next message starts new work.
#
# The freshness signal is the timestamp of the last transcript row, which
# ``hermes_state.get_messages`` carries on every persisted message.  This
# handles the two auto-continue cases uniformly:
#   * resume_pending (gateway restart/shutdown watchdog marked the session)
#   * tool-tail     (last persisted message is a tool result the agent
#                    never got to reply to)
# In both cases "when did we last do anything on this transcript" is the
# correct freshness question, so one signal replaces two divergent ones.
#
# Default window: 1 hour.  This comfortably covers ``agent.gateway_timeout``
# (30 min default) plus runtime slack — a legitimate long-running turn that
# gets interrupted near its timeout boundary and is resumed shortly after
# is still classified fresh.  Override via
# ``config.yaml`` ``agent.gateway_auto_continue_freshness``.
_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT = 60 * 60


def _coerce_gateway_timestamp(value: Any) -> Optional[float]:
    """Best-effort conversion of stored gateway timestamps to epoch seconds.

    Missing/unparseable timestamps return None so legacy transcripts keep the
    historical auto-continue behaviour instead of being silently dropped.
    Accepts: datetime, epoch seconds (int/float), epoch milliseconds (when
    the magnitude exceeds year-2286), ISO-8601 strings (with or without a
    trailing ``Z``), and numeric strings.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, bool):  # bool is a subclass of int — skip it
        return None
    if isinstance(value, (int, float)):
        # Some platform events use milliseconds; Hermes state rows use seconds.
        return float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric = float(text)
            return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _auto_continue_freshness_window() -> float:
    """Return the configured auto-continue freshness window in seconds.

    Reads ``HERMES_AUTO_CONTINUE_FRESHNESS`` (bridged from
    ``config.yaml`` ``agent.gateway_auto_continue_freshness`` at gateway
    startup, same pattern as ``HERMES_AGENT_TIMEOUT``).  Falls back to the
    module default when unset or malformed.  Non-positive values disable
    the freshness gate (restores the pre-fix "always fresh" behaviour for
    users who want to opt out).
    """
    raw = os.environ.get("HERMES_AUTO_CONTINUE_FRESHNESS")
    if raw is None or raw == "":
        return float(_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT)


def _float_env(name: str, default: float) -> float:
    """Read an env var as float, falling back to ``default`` on typos/empty.

    A misconfigured env var (e.g. ``HERMES_AGENT_TIMEOUT=abc``) must not
    crash the gateway or an agent turn.  Unset/empty also falls back.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _is_fresh_gateway_interruption(
    value: Any,
    *,
    now: Optional[float] = None,
    window_secs: Optional[float] = None,
) -> bool:
    """Return True when an interruption marker is fresh enough to auto-continue.

    Unknown timestamps are treated as fresh for backward compatibility with
    legacy transcripts (pre-dating timestamp persistence) and with in-memory
    test scaffolding that constructs history entries without timestamps.

    A non-positive ``window_secs`` disables the gate (always fresh), which
    restores the pre-fix behaviour for users who opt out via config.
    """
    window = (
        float(window_secs)
        if window_secs is not None
        else float(_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT)
    )
    if window <= 0:
        return True
    timestamp = _coerce_gateway_timestamp(value)
    if timestamp is None:
        return True
    current = time.time() if now is None else now
    return current - timestamp <= window


# Assistant-message fields that must survive transcript replay so multi-turn
# reasoning context, prefix-cache hits, and provider-specific echo
# requirements all behave the same on the gateway as they do in the CLI.
#
# ``reasoning`` and ``reasoning_details`` were the original three preserved
# by PR #2974 (schema v6).  ``reasoning_content``, ``codex_reasoning_items``,
# ``codex_message_items``, and ``finish_reason`` were added to the DB later
# but the gateway's replay whitelist was never expanded to match — so any
# pure-text assistant turn (no ``tool_calls``) silently dropped them on
# replay, regressing the CLI-vs-gateway behavioural parity.
#
# Why each field matters on replay:
#   * ``reasoning`` / ``reasoning_content``: provider-facing thinking text.
#     ``_copy_reasoning_content_for_api`` promotes ``reasoning`` →
#     ``reasoning_content`` at send time, but only when the strings happen to
#     match.  Carrying the original ``reasoning_content`` verbatim avoids
#     reconstruction loss for providers that return them as distinct fields
#     (DeepSeek/Kimi/Moonshot thinking modes).
#   * ``reasoning_details``: opaque structured array (signature,
#     encrypted_content) used by OpenRouter/Anthropic to maintain reasoning
#     continuity across turns.
#   * ``codex_reasoning_items``: encrypted reasoning blobs for the OpenAI
#     Codex Responses API.
#   * ``codex_message_items``: exact assistant message items with ``phase``.
#     OpenAI docs: "preserve and resend phase on all assistant messages —
#     dropping it can degrade performance."  Required for prefix cache hits.
#   * ``finish_reason``: informational; cheap to keep so transcripts replay
#     identically across CLI and gateway.
_ASSISTANT_REPLAY_FIELDS: tuple[str, ...] = (
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "codex_reasoning_items",
    "codex_message_items",
    "finish_reason",
)


def _build_replay_entry(role: str, content: Any, msg: Dict[str, Any]) -> Dict[str, Any]:
    """Build a replay entry for a non-tool-calling message, preserving the
    assistant fields the agent's API builders rely on for multi-turn fidelity.

    Lifted out of the inline ``run_sync`` closure so the field whitelist can
    be unit-tested in isolation.  Mirrors the ``_ASSISTANT_REPLAY_FIELDS``
    contract above.

    Empty values: most fields are dropped when falsy (matching the original
    PR #2974 behaviour) since an empty list/string for those carries no
    information.  The exception is ``reasoning_content``: DeepSeek/Kimi
    thinking-mode replay treats an empty string as a meaningful sentinel
    that ``_copy_reasoning_content_for_api`` upgrades to a single space.
    Dropping it here would make the gateway send no ``reasoning_content`` at
    all on the next turn, which can cause HTTP 400 from strict thinking
    providers.
    """
    entry: Dict[str, Any] = {"role": role, "content": content}
    if role == "assistant":
        for _rkey in _ASSISTANT_REPLAY_FIELDS:
            if _rkey not in msg:
                continue
            _rval = msg.get(_rkey)
            if _rkey == "reasoning_content":
                # Preserve empty-string sentinel for thinking-mode replay.
                if _rval is None:
                    continue
            elif not _rval:
                continue
            entry[_rkey] = _rval
    return entry


def _last_transcript_timestamp(history: Optional[List[Dict[str, Any]]]) -> Any:
    """Return the ``timestamp`` of the last usable transcript row, if any.

    Skips metadata-only rows (``session_meta``, system injections) that are
    dropped before being handed to the agent.  Returns ``None`` when no
    usable row carries a timestamp — callers should treat that as "fresh"
    for backward compatibility.
    """
    if not history:
        return None
    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if not role or role in {"session_meta", "system"}:
            continue
        ts = msg.get("timestamp")
        if ts is not None:
            return ts
        # First non-meta row without a timestamp — legacy transcript row.
        # Returning None lets the caller fall through to the legacy-fresh path.
        return None
    return None


# ---------------------------------------------------------------------------
# SSL certificate auto-detection for NixOS and other non-standard systems.
# Must run BEFORE any HTTP library (discord, aiohttp, etc.) is imported.
# ---------------------------------------------------------------------------
def _ensure_ssl_certs() -> None:
    """Set SSL_CERT_FILE if the system doesn't expose CA certs to Python."""
    if "SSL_CERT_FILE" in os.environ:
        return  # user already configured it

    import ssl

    # 1. Python's compiled-in defaults
    paths = ssl.get_default_verify_paths()
    for candidate in (paths.cafile, paths.openssl_cafile):
        if candidate and os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            return

    # 2. certifi (ships its own Mozilla bundle)
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        return
    except ImportError:
        pass

    # 3. Common distro / macOS locations
    for candidate in (
        "/etc/ssl/certs/ca-certificates.crt",               # Debian/Ubuntu/Gentoo
        "/etc/pki/tls/certs/ca-bundle.crt",                 # RHEL/CentOS 7
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", # RHEL/CentOS 8+
        "/etc/ssl/ca-bundle.pem",                            # SUSE/OpenSUSE
        "/etc/ssl/cert.pem",                                 # Alpine / macOS
        "/etc/pki/tls/cert.pem",                             # Fedora
        "/usr/local/etc/openssl@1.1/cert.pem",               # macOS Homebrew Intel
        "/opt/homebrew/etc/openssl@1.1/cert.pem",            # macOS Homebrew ARM
    ):
        if os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            return

def _home_target_env_var(platform_name: str) -> str:
    """Return the configured home-target env var for a platform.

    Consults built-in ``_HOME_TARGET_ENV_VARS`` first, then the plugin
    registry via ``cron.scheduler._resolve_home_env_var``, then falls back
    to ``<PLATFORM>_HOME_CHANNEL`` for unknown names.
    """
    from cron.scheduler import _resolve_home_env_var

    resolved = _resolve_home_env_var(platform_name)
    if resolved:
        return resolved
    return f"{platform_name.upper()}_HOME_CHANNEL"


def _home_thread_env_var(platform_name: str) -> str:
    """Return the optional thread/topic env var for a platform home target."""
    return f"{_home_target_env_var(platform_name)}_THREAD_ID"


def _restart_notification_pending() -> bool:
    """Return True when a /restart completion marker is waiting to be delivered."""
    return (_hermes_home / ".restart_notify.json").exists()


# Mark this process as a gateway so cli.py's module-level load_cli_config()
# knows not to clobber TERMINAL_CWD if lazily imported.
os.environ["_HERMES_GATEWAY"] = "1"

_ensure_ssl_certs()

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Resolve Hermes home directory (respects HERMES_HOME override)
from hermes_constants import get_hermes_home
from utils import atomic_json_write, atomic_yaml_write, base_url_host_matches, is_truthy_value
_hermes_home = get_hermes_home()

# Wire extracted config_loader module so it shares the Hermes home path
from gateway.config_loader import set_hermes_home as _set_loader_home

_set_loader_home(_hermes_home)

# Load environment variables from ~/.hermes/.env first.
# User-managed env files should override stale shell exports on restart.
from dotenv import load_dotenv  # backward-compat for tests that monkeypatch this symbol
from hermes_cli.env_loader import load_hermes_dotenv
_env_path = _hermes_home / '.env'
load_hermes_dotenv(hermes_home=_hermes_home, project_env=Path(__file__).resolve().parents[1] / '.env')


def _reload_runtime_env_preserving_config_authority() -> None:
    """Reload .env for fresh credentials without letting stale .env override config.

    Gateway processes are long-lived, so per-turn code reloads ~/.hermes/.env to
    pick up rotated API keys. config.yaml remains authoritative for agent budget
    settings such as agent.max_turns; otherwise a stale HERMES_MAX_ITERATIONS in
    .env can replace the startup bridge on later turns.
    """
    load_hermes_dotenv(
        hermes_home=_hermes_home,
        project_env=Path(__file__).resolve().parents[1] / '.env',
    )

    config_path = _hermes_home / 'config.yaml'
    if not config_path.exists():
        return
    try:
        import yaml as _yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = _yaml.safe_load(f) or {}
        from hermes_cli.config import _expand_env_vars
        cfg = _expand_env_vars(cfg)
    except Exception:
        return

    agent_cfg = cfg.get("agent", {})
    if isinstance(agent_cfg, dict) and "max_turns" in agent_cfg:
        os.environ["HERMES_MAX_ITERATIONS"] = str(agent_cfg["max_turns"])


_DOCKER_VOLUME_SPEC_RE = re.compile(r"^(?P<host>.+):(?P<container>/[^:]+?)(?::(?P<options>[^:]+))?$")
_DOCKER_MEDIA_OUTPUT_CONTAINER_PATHS = {"/output", "/outputs"}

# Bridge config.yaml values into the environment so os.getenv() picks them up.
# config.yaml is authoritative for terminal settings — overrides .env.
_config_path = _hermes_home / 'config.yaml'
if _config_path.exists():
    try:
        import yaml as _yaml
        with open(_config_path, encoding="utf-8") as _f:
            _cfg = _yaml.safe_load(_f) or {}
        # Expand ${ENV_VAR} references before bridging to env vars.
        from hermes_cli.config import _expand_env_vars
        _cfg = _expand_env_vars(_cfg)
        # Top-level simple values (fallback only — don't override .env)
        for _key, _val in _cfg.items():
            if isinstance(_val, (str, int, float, bool)) and _key not in os.environ:
                os.environ[_key] = str(_val)
        # Terminal config is nested — bridge to TERMINAL_* env vars.
        # config.yaml overrides .env for these since it's the documented config path.
        _terminal_cfg = _cfg.get("terminal", {})
        if _terminal_cfg and isinstance(_terminal_cfg, dict):
            _terminal_env_map = {
                "backend": "TERMINAL_ENV",
                "cwd": "TERMINAL_CWD",
                "timeout": "TERMINAL_TIMEOUT",
                "lifetime_seconds": "TERMINAL_LIFETIME_SECONDS",
                "docker_image": "TERMINAL_DOCKER_IMAGE",
                "docker_forward_env": "TERMINAL_DOCKER_FORWARD_ENV",
                "singularity_image": "TERMINAL_SINGULARITY_IMAGE",
                "modal_image": "TERMINAL_MODAL_IMAGE",
                "daytona_image": "TERMINAL_DAYTONA_IMAGE",
                "vercel_runtime": "TERMINAL_VERCEL_RUNTIME",
                "ssh_host": "TERMINAL_SSH_HOST",
                "ssh_user": "TERMINAL_SSH_USER",
                "ssh_port": "TERMINAL_SSH_PORT",
                "ssh_key": "TERMINAL_SSH_KEY",
                "container_cpu": "TERMINAL_CONTAINER_CPU",
                "container_memory": "TERMINAL_CONTAINER_MEMORY",
                "container_disk": "TERMINAL_CONTAINER_DISK",
                "container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
                "docker_volumes": "TERMINAL_DOCKER_VOLUMES",
                "docker_env": "TERMINAL_DOCKER_ENV",
                "docker_mount_cwd_to_workspace": "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
                "docker_run_as_host_user": "TERMINAL_DOCKER_RUN_AS_HOST_USER",
                "sandbox_dir": "TERMINAL_SANDBOX_DIR",
                "persistent_shell": "TERMINAL_PERSISTENT_SHELL",
            }
            for _cfg_key, _env_var in _terminal_env_map.items():
                if _cfg_key in _terminal_cfg:
                    _val = _terminal_cfg[_cfg_key]
                    # Skip cwd placeholder values (".", "auto", "cwd") — the
                    # gateway resolves these to Path.home() later (line ~255).
                    # Writing the raw placeholder here would just be noise.
                    # Only bridge explicit absolute paths from config.yaml.
                    if _cfg_key == "cwd" and str(_val) in {".", "auto", "cwd"}:
                        continue
                    # Expand shell tilde in cwd so subprocess.Popen never
                    # receives a literal "~/" which the kernel rejects.
                    if _cfg_key == "cwd" and isinstance(_val, str):
                        _val = os.path.expanduser(_val)
                    if isinstance(_val, (list, dict)):
                        os.environ[_env_var] = json.dumps(_val)
                    else:
                        os.environ[_env_var] = str(_val)
        # Compression config is read directly from config.yaml by run_agent.py
        # and auxiliary_client.py — no env var bridging needed.
        # Auxiliary model/direct-endpoint overrides (vision, web_extract).
        # Each task has provider/model/base_url/api_key; bridge non-default values to env vars.
        _auxiliary_cfg = _cfg.get("auxiliary", {})
        if _auxiliary_cfg and isinstance(_auxiliary_cfg, dict):
            _aux_task_env = {
                "vision": {
                    "provider": "AUXILIARY_VISION_PROVIDER",
                    "model": "AUXILIARY_VISION_MODEL",
                    "base_url": "AUXILIARY_VISION_BASE_URL",
                    "api_key": "AUXILIARY_VISION_API_KEY",
                },
                "web_extract": {
                    "provider": "AUXILIARY_WEB_EXTRACT_PROVIDER",
                    "model": "AUXILIARY_WEB_EXTRACT_MODEL",
                    "base_url": "AUXILIARY_WEB_EXTRACT_BASE_URL",
                    "api_key": "AUXILIARY_WEB_EXTRACT_API_KEY",
                },
                "approval": {
                    "provider": "AUXILIARY_APPROVAL_PROVIDER",
                    "model": "AUXILIARY_APPROVAL_MODEL",
                    "base_url": "AUXILIARY_APPROVAL_BASE_URL",
                    "api_key": "AUXILIARY_APPROVAL_API_KEY",
                },
            }
            for _task_key, _env_map in _aux_task_env.items():
                _task_cfg = _auxiliary_cfg.get(_task_key, {})
                if not isinstance(_task_cfg, dict):
                    continue
                _prov = str(_task_cfg.get("provider", "")).strip()
                _model = str(_task_cfg.get("model", "")).strip()
                _base_url = str(_task_cfg.get("base_url", "")).strip()
                _api_key = str(_task_cfg.get("api_key", "")).strip()
                if _prov and _prov != "auto":
                    os.environ[_env_map["provider"]] = _prov
                if _model:
                    os.environ[_env_map["model"]] = _model
                if _base_url:
                    os.environ[_env_map["base_url"]] = _base_url
                if _api_key:
                    os.environ[_env_map["api_key"]] = _api_key
        # config.yaml is the documented, authoritative source for these
        # settings — it unconditionally wins over .env values. Previously
        # the guards below read `if X not in os.environ` and let stale
        # .env entries (e.g. HERMES_MAX_ITERATIONS=60 written by an old
        # `hermes setup` run) silently shadow the user's current config.
        # See PR #18413 / the 60-vs-500 max_turns incident.
        _agent_cfg = _cfg.get("agent", {})
        if _agent_cfg and isinstance(_agent_cfg, dict):
            if "max_turns" in _agent_cfg:
                os.environ["HERMES_MAX_ITERATIONS"] = str(_agent_cfg["max_turns"])
            if "gateway_timeout" in _agent_cfg:
                os.environ["HERMES_AGENT_TIMEOUT"] = str(_agent_cfg["gateway_timeout"])
            if "gateway_timeout_warning" in _agent_cfg:
                os.environ["HERMES_AGENT_TIMEOUT_WARNING"] = str(_agent_cfg["gateway_timeout_warning"])
            if "gateway_notify_interval" in _agent_cfg:
                os.environ["HERMES_AGENT_NOTIFY_INTERVAL"] = str(_agent_cfg["gateway_notify_interval"])
            if "restart_drain_timeout" in _agent_cfg:
                os.environ["HERMES_RESTART_DRAIN_TIMEOUT"] = str(_agent_cfg["restart_drain_timeout"])
            if "gateway_auto_continue_freshness" in _agent_cfg:
                os.environ["HERMES_AUTO_CONTINUE_FRESHNESS"] = str(
                    _agent_cfg["gateway_auto_continue_freshness"]
                )
        _display_cfg = _cfg.get("display", {})
        if _display_cfg and isinstance(_display_cfg, dict):
            if "busy_input_mode" in _display_cfg:
                os.environ["HERMES_GATEWAY_BUSY_INPUT_MODE"] = str(_display_cfg["busy_input_mode"])
            if "busy_ack_enabled" in _display_cfg:
                os.environ["HERMES_GATEWAY_BUSY_ACK_ENABLED"] = str(_display_cfg["busy_ack_enabled"])
        # Timezone: bridge config.yaml → HERMES_TIMEZONE env var.
        _tz_cfg = _cfg.get("timezone", "")
        if _tz_cfg and isinstance(_tz_cfg, str):
            os.environ["HERMES_TIMEZONE"] = _tz_cfg.strip()
        # Security settings
        _security_cfg = _cfg.get("security", {})
        if isinstance(_security_cfg, dict):
            _redact = _security_cfg.get("redact_secrets")
            if _redact is not None:
                os.environ["HERMES_REDACT_SECRETS"] = str(_redact).lower()
    except Exception as _bridge_err:
        # Previously this was silent (`except Exception: pass`), which
        # hid partial bridge failures and let .env defaults shadow
        # config.yaml values — users observed max_turns=500 in config
        # but a 60-iteration cap in practice. Surface the failure to
        # stderr so operators see it even though `logger` is not yet
        # initialized at module-import time (logger is defined further
        # down this module).
        print(
            f"  Warning: config.yaml → env bridge failed: "
            f"{type(_bridge_err).__name__}: {_bridge_err}",
            file=sys.stderr,
        )
        print(
            "  Gateway will fall back to .env values, which may not match "
            "your current config.yaml. Run `hermes doctor` to investigate.",
            file=sys.stderr,
        )

# Apply IPv4 preference if configured (before any HTTP clients are created).
try:
    from hermes_constants import apply_ipv4_preference
    _network_cfg = (_cfg if '_cfg' in dir() else {}).get("network", {})
    if isinstance(_network_cfg, dict) and _network_cfg.get("force_ipv4"):
        apply_ipv4_preference(force=True)
except Exception as _bootstrap_exc:
    print(f"  Warning: IPv4 preference application failed: {_bootstrap_exc}", file=sys.stderr)

# Validate config structure early — log warnings so gateway operators see problems
try:
    from hermes_cli.config import print_config_warnings
    print_config_warnings()
except Exception as _bootstrap_exc:
    print(f"  Warning: config validation failed: {_bootstrap_exc}", file=sys.stderr)

# Warn if user has deprecated MESSAGING_CWD / TERMINAL_CWD in .env
try:
    from hermes_cli.config import warn_deprecated_cwd_env_vars
    warn_deprecated_cwd_env_vars()
except Exception as _bootstrap_exc:
    print(f"  Warning: deprecation check failed: {_bootstrap_exc}", file=sys.stderr)

# Gateway runs in quiet mode - suppress debug output and use cwd directly (no temp dirs)
os.environ["HERMES_QUIET"] = "1"

# Enable interactive exec approval for dangerous commands on messaging platforms
os.environ["HERMES_EXEC_ASK"] = "1"

# Set terminal working directory for messaging platforms.
# config.yaml terminal.cwd is the canonical source (bridged to TERMINAL_CWD
# by the config bridge above).  When it's unset or a placeholder, default
# to home directory.  MESSAGING_CWD is accepted as a backward-compat
# fallback (deprecated — the warning above tells users to migrate).
_configured_cwd = os.environ.get("TERMINAL_CWD", "")
if not _configured_cwd or _configured_cwd in {".", "auto", "cwd"}:
    _fallback = os.getenv("MESSAGING_CWD") or str(Path.home())
    os.environ["TERMINAL_CWD"] = _fallback

from gateway.config import (
    Platform,
    GatewayConfig,
    HomeChannel,
    PlatformConfig,
    load_gateway_config,
)
from gateway.config_loader import (
    _load_gateway_config as _load_gateway_config_from_loader,
    _resolve_gateway_model,
    _resolve_hermes_bin,
    _parse_session_key,
    _format_gateway_process_notification,
)
from gateway.agent_runner import (
    CACHE_BUSTING_CONFIG_KEYS,
    apply_session_model_override,
    compute_agent_config_signature,
    enforce_agent_cache_cap,
    extract_cache_busting_config,
    init_cached_agent_for_turn,
    is_intentional_model_switch,
    load_fallback_model,
    load_provider_routing,
    load_service_tier,
    release_evicted_agent_soft,
    release_running_agent_state,
    resolve_session_agent_runtime,
    resolve_turn_agent_config,
    snapshot_running_agents,
    sweep_idle_cached_agents,
)
from gateway.agent_shutdown import (
    cleanup_agent_resources,
    drain_active_agents,
    finalize_shutdown_agents,
    interrupt_running_agents,
)
from gateway.agent_progress import send_agent_progress_messages
from gateway.active_session_busy import handle_active_session_busy_message
from gateway.delivery_notifications import (
    notify_active_sessions_of_shutdown,
    send_home_channel_startup_notifications,
    send_restart_notification,
    send_update_notification,
)
from gateway.background_process_events import (
    build_process_event_source,
    inject_watch_notification,
)
from gateway.background_task_runner import run_background_task
from gateway.message_enrichment import (
    enrich_message_with_vision,
    resolve_image_input_mode,
)
from gateway.media_delivery import deliver_media_from_response
from gateway.platform_manager import (
    adapter_disconnect_timeout_secs as resolve_adapter_disconnect_timeout_secs,
    connect_adapter_with_timeout,
    handle_adapter_fatal_error as handle_platform_adapter_fatal_error,
    pause_failed_platform,
    platform_connect_timeout_secs as resolve_platform_connect_timeout_secs,
    resume_paused_platform,
    safe_adapter_disconnect,
)
from gateway.platform_reconnect import (
    retry_failed_platform,
    run_platform_reconnect_watcher,
)
from gateway.voice_enrichment import enrich_message_with_transcription
from gateway.voice_mode_manager import (
    load_voice_modes,
    save_voice_modes,
    set_adapter_auto_tts_disabled,
    set_adapter_auto_tts_enabled,
    sync_voice_mode_state_to_adapter,
    voice_mode_key,
)
from gateway.process_watcher import run_process_watcher


def _load_gateway_config() -> dict:
    """Load gateway config using the run-module Hermes home binding."""

    _set_loader_home(_hermes_home)
    return _load_gateway_config_from_loader()


from gateway.session import (
    SessionStore,
    SessionSource,
    SessionContext,
    build_session_context,
    build_session_context_prompt,
    build_session_key,
)
from gateway.session_manager import (
    begin_session_run_generation,
    cache_session_source,
    clear_session_boundary_security_state,
    evict_cached_agent,
    get_cached_session_source,
    invalidate_session_run_generation,
    interrupt_and_clear_session,
    is_session_run_current,
    resolve_session_key_for_source,
)
from gateway.session_info import format_session_info
from gateway.kanban_artifacts import KanbanArtifactDelivery
from gateway.kanban_dispatcher import KanbanDispatcherWatcher
from gateway.kanban_goal_continuation import KanbanGoalContinuation
from gateway.kanban_notifier import KanbanNotifierWatcher
from gateway.session_handoff import SessionHandoffWatcher
from gateway.session_expiry import SessionExpiryWatcher
from gateway.stop_runtime import GatewayStopRuntime
from gateway.telegram_topic_manager import (
    TELEGRAM_CAPABILITY_HINT_COOLDOWN_S,
    TELEGRAM_LOBBY_REMINDER_COOLDOWN_S,
    disable_telegram_topic_mode_for_chat,
    is_telegram_topic_lane,
    is_telegram_topic_mode_enabled,
    is_telegram_topic_root_lobby,
    recover_telegram_topic_thread_id,
    record_telegram_topic_binding,
    sanitize_telegram_topic_title,
    should_send_telegram_topic_notice,
    telegram_topic_auto_rename_disabled,
    telegram_topic_help_text,
    telegram_topic_new_header,
    telegram_topic_root_lobby_message,
    telegram_topic_root_new_message,
    telegram_topic_root_status_message,
)
from gateway.delivery import DeliveryRouter
from gateway.message_authorization import resolve_source_authorization
from gateway.message_preparation import prepare_inbound_message_text
from gateway.platform_factory import create_platform_adapter
from gateway.platforms.base import (
    BasePlatformAdapter,
    EphemeralReply,
    MessageEvent,
    MessageType,
    _reply_anchor_for_event,
    merge_pending_message_event,
)
from gateway.restart import (
    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
    GATEWAY_SERVICE_RESTART_EXIT_CODE,
    parse_restart_drain_timeout,
)
from gateway.restart_runtime import (
    clear_restart_failure_count,
    increment_restart_failure_counts,
    launch_detached_restart_command,
    schedule_resume_pending_sessions,
    suspend_stuck_loop_sessions,
)

logger = logging.getLogger(__name__)


# Sentinel placed into _running_agents immediately when a session starts
# processing, *before* any await.  Prevents a second message for the same
# session from bypassing the "already running" guard during the async gap
# between the guard check and actual agent creation.
_AGENT_PENDING_SENTINEL = object()


def _resolve_runtime_agent_kwargs() -> dict:
    """Resolve provider credentials for gateway-created AIAgent instances.

    If the primary provider fails with an authentication error, attempt to
    resolve credentials using the fallback provider chain from config.yaml
    before giving up.
    """
    from hermes_cli.runtime_provider import (
        resolve_runtime_provider,
        format_runtime_provider_error,
    )
    from hermes_cli.auth import AuthError

    try:
        runtime = resolve_runtime_provider(
            requested=os.getenv("HERMES_INFERENCE_PROVIDER"),
        )
    except AuthError as auth_exc:
        # Primary provider auth failed (expired token, revoked key, etc.).
        # Try the fallback provider chain before raising.
        logger.warning("Primary provider auth failed: %s — trying fallback", auth_exc)
        fb_config = _try_resolve_fallback_provider()
        if fb_config is not None:
            return fb_config
        raise RuntimeError(format_runtime_provider_error(auth_exc)) from auth_exc
    except Exception as exc:
        raise RuntimeError(format_runtime_provider_error(exc)) from exc

    return {
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "provider": runtime.get("provider"),
        "api_mode": runtime.get("api_mode"),
        "command": runtime.get("command"),
        "args": list(runtime.get("args") or []),
        "credential_pool": runtime.get("credential_pool"),
    }


def _try_resolve_fallback_provider() -> dict | None:
    """Attempt to resolve credentials from the fallback_model/fallback_providers config."""
    from hermes_cli.runtime_provider import resolve_runtime_provider
    try:
        import yaml as _y
        cfg_path = _hermes_home / "config.yaml"
        if not cfg_path.exists():
            return None
        with open(cfg_path, encoding="utf-8") as _f:
            cfg = _y.safe_load(_f) or {}
        fb = cfg.get("fallback_providers") or cfg.get("fallback_model")
        if not fb:
            return None
        # Normalize to list
        fb_list = fb if isinstance(fb, list) else [fb]
        for entry in fb_list:
            if not isinstance(entry, dict):
                continue
            try:
                runtime = resolve_runtime_provider(
                    requested=entry.get("provider"),
                    explicit_base_url=entry.get("base_url"),
                    explicit_api_key=entry.get("api_key"),
                )
                logger.info(
                    "Fallback provider resolved: %s model=%s",
                    runtime.get("provider"),
                    entry.get("model"),
                )
                return {
                    "api_key": runtime.get("api_key"),
                    "base_url": runtime.get("base_url"),
                    "provider": runtime.get("provider"),
                    "api_mode": runtime.get("api_mode"),
                    "command": runtime.get("command"),
                    "args": list(runtime.get("args") or []),
                    "credential_pool": runtime.get("credential_pool"),
                    "model": entry.get("model"),
                }
            except Exception as fb_exc:
                logger.debug("Fallback entry %s failed: %s", entry.get("provider"), fb_exc)
                continue
    except Exception:
        pass
    return None


def _build_media_placeholder(event) -> str:
    """Build a text placeholder for media-only events so they aren't dropped.

    When a photo/document is queued during active processing and later
    dequeued, only .text is extracted.  If the event has no caption,
    the media would be silently lost.  This builds a placeholder that
    the vision enrichment pipeline will replace with a real description.
    """
    parts = []
    media_urls = getattr(event, "media_urls", None) or []
    media_types = getattr(event, "media_types", None) or []
    for i, url in enumerate(media_urls):
        mtype = media_types[i] if i < len(media_types) else ""
        if mtype.startswith("image/") or getattr(event, "message_type", None) == MessageType.PHOTO:
            parts.append(f"[User sent an image: {url}]")
        elif mtype.startswith("audio/"):
            parts.append(f"[User sent audio: {url}]")
        else:
            parts.append(f"[User sent a file: {url}]")
    return "\n".join(parts)


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


async def _probe_audio_duration(path: str) -> Optional[str]:
    """Best-effort duration probe. Returns formatted MM:SS / HH:MM:SS, or None on failure."""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".wav":
        try:
            def _wav_duration() -> float:
                import wave
                with wave.open(path, "rb") as wf:
                    frames = wf.getnframes()
                    rate = wf.getframerate() or 1
                    return frames / float(rate)
            secs = await asyncio.to_thread(_wav_duration)
            return _format_duration(secs)
        except Exception:
            pass

    if ext in (".ogg", ".opus", ".oga"):
        try:
            def _ogg_duration() -> float:
                from mutagen.oggopus import OggOpus
                return float(OggOpus(path).info.length)
            secs = await asyncio.to_thread(_ogg_duration)
            return _format_duration(secs)
        except Exception:
            pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            return _format_duration(float(stdout.decode().strip()))
    except Exception:
        pass

    return None


def _dequeue_pending_event(adapter, session_key: str) -> MessageEvent | None:
    """Consume and return the full pending event for a session.

    Queued follow-ups must preserve their media metadata so they can re-enter
    the normal image/STT/document preprocessing path instead of being reduced
    to a placeholder string.
    """
    return adapter.get_pending_message(session_key)


_INTERRUPT_REASON_STOP = "Stop requested"
_INTERRUPT_REASON_RESET = "Session reset requested"
_INTERRUPT_REASON_TIMEOUT = "Execution timed out (inactivity)"
_INTERRUPT_REASON_SSE_DISCONNECT = "SSE client disconnected"
_INTERRUPT_REASON_GATEWAY_SHUTDOWN = "Gateway shutting down"
_INTERRUPT_REASON_GATEWAY_RESTART = "Gateway restarting"

_CONTROL_INTERRUPT_MESSAGES = frozenset(
    {
        _INTERRUPT_REASON_STOP.lower(),
        _INTERRUPT_REASON_RESET.lower(),
        _INTERRUPT_REASON_TIMEOUT.lower(),
        _INTERRUPT_REASON_SSE_DISCONNECT.lower(),
        _INTERRUPT_REASON_GATEWAY_SHUTDOWN.lower(),
        _INTERRUPT_REASON_GATEWAY_RESTART.lower(),
    }
)


def _is_control_interrupt_message(message: Optional[str]) -> bool:
    """Return True when an interrupt message is internal control flow."""
    if not message:
        return False
    normalized = " ".join(str(message).strip().split()).lower()
    return normalized in _CONTROL_INTERRUPT_MESSAGES


def _skill_slug_from_frontmatter(skill_md: Path) -> tuple[str | None, str | None]:
    """Derive the /command slug and declared frontmatter name from a SKILL.md.

    Matches the exact normalization used by
    :func:`agent.skill_commands.scan_skill_commands` so the slug here is the
    same string a user types after the leading ``/`` (e.g. a skill with
    frontmatter ``name: Stable Diffusion Image Generation`` resolves to
    ``stable-diffusion-image-generation`` — NOT the parent directory name,
    which is commonly shorter/different, e.g. ``stable-diffusion``).

    Using the directory name silently broke :func:`_check_unavailable_skill`
    for every skill whose directory name drifted from its frontmatter name
    (19 such skills on a standard install as of 2026-05), causing a generic
    "unknown command" response where a "disabled — enable with …" or
    "not installed — install with …" hint was expected.

    Returns ``(slug, declared_name)`` or ``(None, None)`` when the file
    can't be read or lacks a ``name:`` in its frontmatter.
    """
    try:
        content = skill_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None, None
    if not content.startswith("---"):
        return None, None
    end = content.find("\n---", 3)
    if end < 0:
        return None, None
    declared_name: str | None = None
    for line in content[3:end].splitlines():
        line = line.strip()
        if line.startswith("name:"):
            raw = line.split(":", 1)[1].strip()
            # Strip YAML quote wrappers if present
            if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
                raw = raw[1:-1]
            declared_name = raw.strip()
            break
    if not declared_name:
        return None, None
    slug = declared_name.lower().replace(" ", "-").replace("_", "-")
    # Mirror _SKILL_INVALID_CHARS and _SKILL_MULTI_HYPHEN from skill_commands
    import re as _re
    slug = _re.sub(r"[^a-z0-9-]", "", slug)
    slug = _re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        return None, declared_name
    return slug, declared_name


def _check_unavailable_skill(command_name: str) -> str | None:
    """Check if a command matches a known-but-inactive skill.

    Returns a helpful message if the skill exists but is disabled or only
    available as an optional install. Returns None if no match found.

    The slug for each on-disk skill is derived from its frontmatter ``name:``
    (via :func:`_skill_slug_from_frontmatter`), NOT from its containing
    directory name — because the two can differ (e.g. directory
    ``stable-diffusion`` + frontmatter ``Stable Diffusion Image Generation``
    yields slug ``stable-diffusion-image-generation``). Matching on
    directory name would miss that slug entirely and fall through to the
    generic "unknown command" path.
    """
    # Normalize: command uses hyphens, skill names may use hyphens or underscores
    normalized = command_name.lower().replace("_", "-")
    try:
        from tools.skills_tool import _get_disabled_skill_names
        from agent.skill_utils import get_all_skills_dirs, is_excluded_skill_path
        disabled = _get_disabled_skill_names()

        # Check disabled skills across all dirs (local + external)
        for skills_dir in get_all_skills_dirs():
            if not skills_dir.exists():
                continue
            for skill_md in skills_dir.rglob("SKILL.md"):
                if is_excluded_skill_path(skill_md):
                    continue
                slug, declared_name = _skill_slug_from_frontmatter(skill_md)
                if not slug or not declared_name:
                    continue
                # disabled is keyed by the declared frontmatter name (what
                # skills.disabled / skills.platform_disabled store).
                if slug == normalized and declared_name in disabled:
                    return (
                        f"The **{command_name}** skill is installed but disabled.\n"
                        f"Enable it with: `hermes skills config`"
                    )

        # Check optional skills (shipped with repo but not installed)
        from hermes_constants import get_optional_skills_dir
        repo_root = Path(__file__).resolve().parent.parent
        optional_dir = get_optional_skills_dir(repo_root / "optional-skills")
        if optional_dir.exists():
            for skill_md in optional_dir.rglob("SKILL.md"):
                if is_excluded_skill_path(skill_md):
                    continue
                slug, _declared = _skill_slug_from_frontmatter(skill_md)
                if not slug:
                    continue
                if slug == normalized:
                    # Build install path: official/<category>/<name>
                    rel = skill_md.parent.relative_to(optional_dir)
                    parts = list(rel.parts)
                    install_path = f"official/{'/'.join(parts)}"
                    return (
                        f"The **{command_name}** skill is available but not installed.\n"
                        f"Install it with: `hermes skills install {install_path}`"
                    )
    except Exception:
        pass
    return None


def _platform_config_key(platform: "Platform") -> str:
    """Map a Platform enum to its config.yaml key (LOCAL→"cli", rest→enum value)."""
    return "cli" if platform == Platform.LOCAL else platform.value


def _teams_pipeline_plugin_enabled() -> bool:
    """Return True when the standalone Teams pipeline plugin is enabled."""
    config = _load_gateway_config()
    enabled = cfg_get(config, "plugins", "enabled", default=[])
    if not isinstance(enabled, list):
        return False
    return "teams_pipeline" in enabled or "teams-pipeline" in enabled


def _normalize_empty_agent_response(
    agent_result: dict,
    response: str,
    *,
    history_len: int = 0,
) -> str:
    """Normalize empty/None agent responses into user-facing messages.

    Consolidates the existing ``failed`` handler and adds a catch-all for
    the case where the agent did work (api_calls > 0) but returned no text.
    Fix for #18765.
    """
    if response:
        return response

    if agent_result.get("failed"):
        error_detail = agent_result.get("error", "unknown error")
        error_str = str(error_detail).lower()
        is_context_failure = any(
            p in error_str
            for p in ("context", "token", "too large", "too long", "exceed", "payload")
        ) or ("400" in error_str and history_len > 50)
        if is_context_failure:
            return (
                "⚠️ Session too large for the model's context window.\n"
                "Use /compact to compress the conversation, or "
                "/reset to start fresh."
            )
        return (
            f"The request failed: {str(error_detail)[:300]}\n"
            "Try again or use /reset to start a fresh session."
        )

    api_calls = int(agent_result.get("api_calls", 0) or 0)
    if api_calls > 0 and not agent_result.get("interrupted"):
        if agent_result.get("partial"):
            err = agent_result.get("error", "processing incomplete")
            return f"⚠️ Processing stopped: {str(err)[:200]}. Try again."
        return (
            "⚠️ Processing completed but no response was generated. "
            "This may be a transient error — try sending your message again."
        )

    return response


def _should_clear_resume_pending_after_turn(agent_result: dict) -> bool:
    """Return True only when a gateway turn really completed successfully.

    Restart recovery uses ``resume_pending`` as a durable marker for sessions
    interrupted during gateway drain.  A soft interrupt can still bubble out as
    a syntactically normal agent result with an empty final response; clearing
    the marker in that case loses the recovery signal and startup auto-resume
    has nothing to schedule.
    """
    if not isinstance(agent_result, dict):
        return False
    if agent_result.get("interrupted"):
        return False
    if agent_result.get("failed") or agent_result.get("partial") or agent_result.get("error"):
        return False
    if agent_result.get("completed") is False:
        return False
    return True


def _preserve_queued_followup_history_offset(
    current_result: dict,
    followup_result: dict,
) -> dict:
    """Carry the outer history offset through queued follow-up drains.

    ``_process_message_background()`` persists transcript rows only once, after the
    entire in-band queued-follow-up chain returns.  Each recursive ``_run_agent()``
    call advances ``history_offset`` to the history it received, so without
    correction the outermost persistence step sees only the *last* queued turn as
    "new" and silently drops earlier turns from the same drain chain.

    Preserve the earliest (outermost) history offset so the final transcript slice
    still includes every queued turn that ran during the chain.
    """
    if not isinstance(followup_result, dict):
        return followup_result
    if not isinstance(current_result, dict):
        return followup_result

    current_offset = current_result.get("history_offset")
    followup_offset = followup_result.get("history_offset")
    if not isinstance(current_offset, int):
        return followup_result
    if isinstance(followup_offset, int) and followup_offset <= current_offset:
        return followup_result

    merged = dict(followup_result)
    merged["history_offset"] = current_offset
    return merged


class GatewayRunner:
    """
    Main gateway controller.

    Manages the lifecycle of all platform adapters and routes
    messages to/from the agent.
    """

    # Class-level defaults so partial construction in tests doesn't
    # blow up on attribute access.
    _running_agents_ts: Dict[str, float] = {}
    _busy_input_mode: str = "interrupt"
    _restart_drain_timeout: float = DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT
    _exit_code: Optional[int] = None
    _draining: bool = False
    _restart_requested: bool = False
    _restart_task_started: bool = False
    _restart_detached: bool = False
    _restart_via_service: bool = False
    _stop_task: Optional[asyncio.Task] = None
    _session_model_overrides: Dict[str, Dict[str, str]] = {}
    _session_reasoning_overrides: Dict[str, Dict[str, Any]] = {}

    def __init__(self, config: Optional[GatewayConfig] = None):
        global _gateway_runner_ref
        self.config = config or load_gateway_config()
        self.adapters: Dict[Platform, BasePlatformAdapter] = {}
        self._warn_if_docker_media_delivery_is_risky()
        _gateway_runner_ref = _weakref.ref(self)

        # Load ephemeral config from config.yaml / env vars.
        # Both are injected at API-call time only and never persisted.
        self._prefill_messages = self._load_prefill_messages()
        self._ephemeral_system_prompt = self._load_ephemeral_system_prompt()
        self._reasoning_config = self._load_reasoning_config()
        self._service_tier = self._load_service_tier()
        self._show_reasoning = self._load_show_reasoning()
        self._busy_input_mode = self._load_busy_input_mode()
        self._restart_drain_timeout = self._load_restart_drain_timeout()
        self._provider_routing = self._load_provider_routing()
        self._fallback_model = self._load_fallback_model()

        # Wire process registry into session store for reset protection
        from tools.process_registry import process_registry
        self.session_store = SessionStore(
            self.config.sessions_dir, self.config,
            has_active_processes_fn=lambda key: process_registry.has_active_for_session(key),
        )
        self.delivery_router = DeliveryRouter(self.config)

        # Initialize event bus and message router for decoupled command handling
        self._event_bus = event_bus
        self._message_router = MessageRouter(
            config=self.config,
            event_bus=self._event_bus,
            session_store=self.session_store
        )
        self._command_registry = CommandHandlerRegistry()
        self._running = False
        self._gateway_loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event = asyncio.Event()
        self._exit_cleanly = False
        self._exit_with_failure = False
        self._exit_reason: Optional[str] = None
        self._exit_code: Optional[int] = None
        self._draining = False
        self._restart_requested = False
        self._restart_task_started = False
        self._restart_detached = False
        self._restart_via_service = False
        self._stop_task: Optional[asyncio.Task] = None

        # Track running agents per session for interrupt support
        # Key: session_key, Value: AIAgent instance
        self._running_agents: Dict[str, Any] = {}
        self._running_agents_ts: Dict[str, float] = {}  # start timestamp per session
        self._pending_messages: Dict[str, str] = {}  # Queued messages during interrupt
        # Overflow buffer for explicit /queue commands.  The adapter-level
        # _pending_messages dict is a single slot per session (designed for
        # "next-turn" follow-ups where repeated sends collapse into one
        # event).  /queue has different semantics: each invocation must
        # produce its own full agent turn, in FIFO order, with no merging.
        # When the slot is occupied, additional /queue items land here and
        # are promoted one-at-a-time after each run's drain.  Cleared on
        # /new and /reset.  /model and other mid-session operations
        # preserve the queue.
        self._queued_events: Dict[str, List[MessageEvent]] = {}
        self._pending_native_image_paths_by_session: Dict[str, List[str]] = {}
        self._busy_ack_ts: Dict[str, float] = {}  # last busy-ack timestamp per session (debounce)
        self._session_run_generation: Dict[str, int] = {}
        # LRU cache of live SessionSources keyed by session_key. Used by
        # fallback routing paths (shutdown notifications, synthetic
        # background-process events) when the persisted origin is missing
        # and _parse_session_key can't recover thread_id. Capped so it
        # cannot grow unbounded over a long-running gateway lifetime.
        self._session_sources: "OrderedDict[str, SessionSource]" = OrderedDict()
        self._session_sources_max = 512

        # Cache AIAgent instances per session to preserve prompt caching.
        # Without this, a new AIAgent is created per message, rebuilding the
        # system prompt (including memory) every turn — breaking prefix cache
        # and costing ~10x more on providers with prompt caching (Anthropic).
        # Key: session_key, Value: (AIAgent, config_signature_str)
        #
        # OrderedDict so _enforce_agent_cache_cap() can pop the least-recently-
        # used entry (move_to_end() on cache hits, popitem(last=False) for
        # eviction).  Hard cap via _AGENT_CACHE_MAX_SIZE, idle TTL enforced
        # from _session_expiry_watcher().
        import threading as _threading
        self._agent_cache: "OrderedDict[str, tuple]" = OrderedDict()
        self._agent_cache_lock = _threading.Lock()

        # Per-session model overrides from /model command.
        # Key: session_key, Value: dict with model/provider/api_key/base_url/api_mode
        self._session_model_overrides: Dict[str, Dict[str, str]] = {}
        # Per-session reasoning effort overrides from /reasoning.
        # Key: session_key, Value: parsed reasoning config dict.
        self._session_reasoning_overrides: Dict[str, Dict[str, Any]] = {}
        self._kanban_notifier_profile = self._active_profile_name()
        # Teams meeting pipeline runtime (bound later when msgraph_webhook adapter exists).
        self._teams_pipeline_runtime = None
        self._teams_pipeline_runtime_error: Optional[str] = None
        # Track pending exec approvals per session
        # Key: session_key, Value: {"command": str, "pattern_key": str, ...}
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}

        # Track platforms that failed to connect for background reconnection.
        # Key: Platform enum, Value: {"config": platform_config, "attempts": int, "next_retry": float}
        self._failed_platforms: Dict[Platform, Dict[str, Any]] = {}

        # Track pending /update prompt responses per session.
        # Key: session_key, Value: True when a prompt is waiting for user input.
        self._update_prompt_pending: Dict[str, bool] = {}

        # Slash-confirm state lives in tools.slash_confirm (module-level),
        # so platform adapters can resolve callbacks without a backref to
        # this runner.  Keep a local counter for confirm_id generation so
        # IDs stay compact (button callback_data has a 64-byte cap on
        # some platforms).
        import itertools as _itertools
        self._slash_confirm_counter = _itertools.count(1)

        # Persistent Honcho managers keyed by gateway session key.
        # This preserves write_frequency="session" semantics across short-lived
        # per-message AIAgent instances.



        # Ensure tirith security scanner is available (downloads if needed)
        try:
            from tools.tirith_security import ensure_installed
            ensure_installed(log_failures=False)
        except Exception:
            pass  # Non-fatal — fail-open at scan time if unavailable

        # Initialize session database for session_search tool support
        self._session_db = None
        try:
            from hermes_state import SessionDB
            self._session_db = SessionDB()
        except Exception as e:
            # WARNING (not DEBUG) so the failure appears in errors.log — matches
            # cli.py's handling of the same init path.  Users hitting NFS-mounted
            # HERMES_HOME silently lost /resume, /title, /history, /branch, and
            # session search without this.  The underlying cause (usually
            # "locking protocol" from NFS) is now also captured by
            # hermes_state.get_last_init_error() for slash-command error strings.
            logger.warning("SQLite session store not available: %s", e)

        # Opportunistic state.db maintenance: prune ended sessions older
        # than sessions.retention_days + optional VACUUM. Tracks last-run
        # in state_meta so it only actually executes once per
        # sessions.min_interval_hours.  Gateway is long-lived so blocking
        # a few seconds once per day is acceptable; failures are logged
        # but never raised.
        if self._session_db is not None:
            try:
                from hermes_cli.config import load_config as _load_full_config
                _sess_cfg = (_load_full_config().get("sessions") or {})
                if _sess_cfg.get("auto_prune", False):
                    self._session_db.maybe_auto_prune_and_vacuum(
                        retention_days=int(_sess_cfg.get("retention_days", 90)),
                        min_interval_hours=int(_sess_cfg.get("min_interval_hours", 24)),
                        vacuum=bool(_sess_cfg.get("vacuum_after_prune", True)),
                        sessions_dir=self.config.sessions_dir,
                    )
            except Exception as exc:
                logger.debug("state.db auto-maintenance skipped: %s", exc)

        # Opportunistic shadow-repo cleanup — deletes orphan/stale
        # checkpoint repos under ~/.hermes/checkpoints/.  Opt-in via
        # checkpoints.auto_prune, idempotent via .last_prune marker.
        try:
            from hermes_cli.config import load_config as _load_full_config
            _ckpt_cfg = (_load_full_config().get("checkpoints") or {})
            if _ckpt_cfg.get("auto_prune", False):
                from tools.checkpoint_manager import maybe_auto_prune_checkpoints
                maybe_auto_prune_checkpoints(
                    retention_days=int(_ckpt_cfg.get("retention_days", 7)),
                    min_interval_hours=int(_ckpt_cfg.get("min_interval_hours", 24)),
                    delete_orphans=bool(_ckpt_cfg.get("delete_orphans", True)),
                    max_total_size_mb=int(_ckpt_cfg.get("max_total_size_mb", 500)),
                )
        except Exception as exc:
            logger.debug("checkpoint auto-maintenance skipped: %s", exc)

        # DM pairing store for code-based user authorization
        from gateway.pairing import PairingStore
        self.pairing_store = PairingStore()

        # Event hook system
        from gateway.hooks import HookRegistry
        self.hooks = HookRegistry()

        # Per-chat voice reply mode: "off" | "voice_only" | "all"
        self._voice_mode: Dict[str, str] = self._load_voice_modes()
        # Recent voice transcripts per (guild,user) for duplicate suppression.
        # Protects against the same utterance being emitted twice by the voice
        # capture / STT pipeline, which otherwise produces a second delayed reply.
        self._recent_voice_transcripts: Dict[tuple[int, int], List[tuple[float, str]]] = {}

        # Track background tasks to prevent garbage collection mid-execution
        self._background_tasks: set = set()


    def _wire_teams_pipeline_runtime(self) -> None:
        """Bind the Teams meeting pipeline runtime to Graph webhook ingress.

        No-op when the msgraph_webhook adapter isn't running or the
        teams_pipeline plugin isn't enabled — lets the gateway start cleanly
        whether or not the user has opted into the pipeline.
        """
        if Platform.MSGRAPH_WEBHOOK not in self.adapters:
            return
        if not _teams_pipeline_plugin_enabled():
            logger.debug("Teams pipeline plugin is disabled; skipping runtime wiring")
            return
        try:
            from plugins.teams_pipeline.runtime import bind_gateway_runtime
        except Exception as exc:
            logger.warning("Teams pipeline runtime import failed: %s", exc)
            return
        try:
            bound = bind_gateway_runtime(self)
        except Exception as exc:
            logger.warning("Teams pipeline runtime wiring failed: %s", exc)
            return
        if bound:
            logger.info("Teams pipeline runtime bound to msgraph webhook ingress")
        elif self._teams_pipeline_runtime_error:
            logger.warning(
                "Teams pipeline runtime unavailable: %s",
                self._teams_pipeline_runtime_error,
            )


    def _warn_if_docker_media_delivery_is_risky(self) -> None:
        """Warn when Docker-backed gateways lack an explicit export mount.

        MEDIA delivery happens in the gateway process, so paths emitted by the model
        must be readable from the host. A plain container-local path like
        `/workspace/report.txt` or `/output/report.txt` often exists only inside
        Docker, so users commonly need a dedicated export mount such as
        `host-dir:/output`.
        """
        if os.getenv("TERMINAL_ENV", "").strip().lower() != "docker":
            return

        connected = self.config.get_connected_platforms()
        messaging_platforms = [p for p in connected if p not in {Platform.LOCAL, Platform.API_SERVER, Platform.WEBHOOK}]
        if not messaging_platforms:
            return

        raw_volumes = os.getenv("TERMINAL_DOCKER_VOLUMES", "").strip()
        volumes: List[str] = []
        if raw_volumes:
            try:
                parsed = json.loads(raw_volumes)
                if isinstance(parsed, list):
                    volumes = [str(v) for v in parsed if isinstance(v, str)]
            except Exception:
                logger.debug("Could not parse TERMINAL_DOCKER_VOLUMES for gateway media warning", exc_info=True)

        has_explicit_output_mount = False
        for spec in volumes:
            match = _DOCKER_VOLUME_SPEC_RE.match(spec)
            if not match:
                continue
            container_path = match.group("container")
            if container_path in _DOCKER_MEDIA_OUTPUT_CONTAINER_PATHS:
                has_explicit_output_mount = True
                break

        if has_explicit_output_mount:
            return

        logger.warning(
            "Docker backend is enabled for the messaging gateway but no explicit host-visible "
            "output mount (for example '/home/user/.hermes/cache/documents:/output') is configured. "
            "This is fine if the model already emits host-visible paths, but MEDIA file delivery can fail "
            "for container-local paths like '/workspace/...' or '/output/...'."
        )



    # -- Setup skill availability ----------------------------------------

    def _has_setup_skill(self) -> bool:
        """Check if the hermes-agent-setup skill is installed."""
        try:
            from tools.skill_manager_tool import _find_skill
            return _find_skill("hermes-agent-setup") is not None
        except Exception:
            return False

    # -- Voice mode persistence ------------------------------------------

    _VOICE_MODE_PATH = _hermes_home / "gateway_voice_mode.json"

    def _voice_key(self, platform: Platform, chat_id: str) -> str:
        """Return a platform-namespaced key for voice mode state."""
        return voice_mode_key(platform, chat_id)

    def _load_voice_modes(self) -> Dict[str, str]:
        return load_voice_modes(self._VOICE_MODE_PATH, logger=logger)

    def _save_voice_modes(self) -> None:
        save_voice_modes(self._VOICE_MODE_PATH, self._voice_mode, logger=logger)

    def _set_adapter_auto_tts_disabled(self, adapter, chat_id: str, disabled: bool) -> None:
        """Update an adapter's in-memory auto-TTS suppression set if present."""
        set_adapter_auto_tts_disabled(adapter, chat_id, disabled)

    def _set_adapter_auto_tts_enabled(self, adapter, chat_id: str, enabled: bool) -> None:
        """Update an adapter's per-chat auto-TTS opt-in set if present.

        Used for ``/voice on``/``/voice tts`` where the user explicitly wants
        auto-TTS even when ``voice.auto_tts`` is False globally.
        """
        set_adapter_auto_tts_enabled(adapter, chat_id, enabled)

    def _sync_voice_mode_state_to_adapter(self, adapter) -> None:
        """Restore persisted /voice state into a live platform adapter.

        Populates three fields from config + ``self._voice_mode``:
          - ``_auto_tts_default``: global default from ``voice.auto_tts``
          - ``_auto_tts_enabled_chats``: chats with mode ``voice_only``/``all``
          - ``_auto_tts_disabled_chats``: chats with mode ``off``
        """
        sync_voice_mode_state_to_adapter(adapter, self._voice_mode)

    async def _safe_adapter_disconnect(self, adapter, platform) -> None:
        """Call adapter.disconnect() defensively, swallowing any error.

        Used when adapter.connect() failed or raised — the adapter may
        have allocated partial resources (aiohttp.ClientSession, poll
        tasks, child subprocesses) that would otherwise leak and surface
        as "Unclosed client session" warnings at process exit.

        Must tolerate partial-init state and never raise, since callers
        use it inside error-handling blocks.
        """
        await safe_adapter_disconnect(
            adapter,
            platform,
            timeout=self._adapter_disconnect_timeout_secs(),
            logger=logger,
        )

    def _adapter_disconnect_timeout_secs(self) -> float:
        """Return the per-adapter disconnect timeout used during shutdown."""
        return resolve_adapter_disconnect_timeout_secs(logger=logger)

    def _platform_connect_timeout_secs(self) -> float:
        """Return the per-platform connect timeout used during startup/retry."""
        return resolve_platform_connect_timeout_secs(logger=logger)

    async def _connect_adapter_with_timeout(self, adapter, platform) -> bool:
        """Connect an adapter without allowing one platform to block others."""
        return await connect_adapter_with_timeout(
            adapter,
            platform,
            timeout=self._platform_connect_timeout_secs(),
        )

    @property
    def should_exit_cleanly(self) -> bool:
        return self._exit_cleanly

    @property
    def should_exit_with_failure(self) -> bool:
        return self._exit_with_failure

    @property
    def exit_reason(self) -> Optional[str]:
        return self._exit_reason

    @property
    def exit_code(self) -> Optional[int]:
        return self._exit_code

    def _session_key_for_source(self, source: SessionSource) -> str:
        """Resolve the current session key for a source, honoring gateway config when available."""
        return resolve_session_key_for_source(
            source,
            session_store=getattr(self, "session_store", None),
            config=getattr(self, "config", None),
        )

    def _telegram_topic_mode_enabled(self, source: SessionSource) -> bool:
        """Return whether Telegram DM topic mode is active for this chat."""
        return is_telegram_topic_mode_enabled(
            source,
            getattr(self, "_session_db", None),
            logger=logger,
        )

    def _is_telegram_topic_root_lobby(self, source: SessionSource) -> bool:
        """True for the main Telegram DM (or General topic) when topic mode has made it a lobby."""
        return is_telegram_topic_root_lobby(
            source,
            topic_mode_enabled=self._telegram_topic_mode_enabled(source),
        )

    def _is_telegram_topic_lane(self, source: SessionSource) -> bool:
        """True for a user-created Telegram private-chat topic lane."""
        return is_telegram_topic_lane(
            source,
            topic_mode_enabled=self._telegram_topic_mode_enabled(source),
        )

    def _should_send_telegram_lobby_reminder(self, source: SessionSource) -> bool:
        """Rate-limit root-DM lobby reminders to one message per cooldown window.

        A user who forgets multi-session mode is enabled and types several
        prompts in the root DM would otherwise get a reminder for every
        message. Cap it so the first one lands and the rest stay quiet.
        """
        if not hasattr(self, "_telegram_lobby_reminder_ts"):
            self._telegram_lobby_reminder_ts = {}
        return should_send_telegram_topic_notice(
            source,
            self._telegram_lobby_reminder_ts,
            cooldown_s=TELEGRAM_LOBBY_REMINDER_COOLDOWN_S,
        )

    def _telegram_topic_root_lobby_message(self) -> str:
        return telegram_topic_root_lobby_message()

    def _telegram_topic_root_new_message(self) -> str:
        return telegram_topic_root_new_message()

    def _telegram_topic_new_header(self, source: SessionSource) -> Optional[str]:
        return telegram_topic_new_header(
            source,
            topic_mode_enabled=self._telegram_topic_mode_enabled(source),
        )

    def _record_telegram_topic_binding(
        self,
        source: SessionSource,
        session_entry,
    ) -> None:
        """Persist the Telegram topic -> Hermes session binding for topic lanes."""
        record_telegram_topic_binding(
            source,
            session_entry,
            getattr(self, "_session_db", None),
        )

    def _recover_telegram_topic_thread_id(
        self,
        source: SessionSource,
    ) -> Optional[str]:
        """Pin DM-topic routing to the user's last-active topic.

        Telegram fragments topic-mode DMs two ways: a Reply on a message
        in another topic delivers ``message_thread_id`` for *that* topic,
        and ``_build_message_event`` strips the thread_id on plain replies
        (#3206 — needed for non-topic users). Both route the user to the
        wrong session. When topic mode is on, rewrite the thread_id to the
        user's most-recent binding if the inbound id is missing/General or
        not a known topic for this chat. Returns None to leave it alone.
        """
        return recover_telegram_topic_thread_id(
            source,
            getattr(self, "_session_db", None),
            topic_mode_enabled=self._telegram_topic_mode_enabled(source),
            logger=logger,
        )

    def _resolve_session_agent_runtime(
        self,
        *,
        source: Optional[SessionSource] = None,
        session_key: Optional[str] = None,
        user_config: Optional[dict] = None,
    ) -> tuple[str, dict]:
        """Resolve model/runtime for a session."""
        return resolve_session_agent_runtime(
            source=source,
            session_key=session_key,
            user_config=user_config,
            session_model_overrides=self._session_model_overrides,
            resolve_session_key_for_source=self._session_key_for_source,
            resolve_gateway_model=_resolve_gateway_model,
            resolve_runtime_agent_kwargs=_resolve_runtime_agent_kwargs,
            logger=logger,
        )

    def _resolve_turn_agent_config(self, user_message: str, model: str, runtime_kwargs: dict) -> dict:
        """Build the effective model/runtime config for a single turn.

        Always uses the session's primary model/provider.  If `/fast` is
        enabled and the model supports Priority Processing / Anthropic fast
        mode, attach `request_overrides` so the API call is marked
        accordingly.
        """
        return resolve_turn_agent_config(
            model=model,
            runtime_kwargs=runtime_kwargs,
            service_tier=getattr(self, "_service_tier", None),
        )

    async def _handle_adapter_fatal_error(self, adapter: BasePlatformAdapter) -> None:
        """React to an adapter failure after startup.

        If the error is retryable (e.g. network blip, DNS failure), queue the
        platform for background reconnection instead of giving up permanently.
        """
        def set_exit_state(reason: str, exit_with_failure: bool) -> None:
            self._exit_reason = reason
            self._exit_with_failure = exit_with_failure

        await handle_platform_adapter_fatal_error(
            adapter,
            config=self.config,
            adapters=self.adapters,
            delivery_router=self.delivery_router,
            failed_platforms=self._failed_platforms,
            update_platform_runtime_status=self._update_platform_runtime_status,
            set_exit_state=set_exit_state,
            stop_gateway=self.stop,
            logger=logger,
        )

    def _request_clean_exit(self, reason: str) -> None:
        self._exit_cleanly = True
        self._exit_reason = reason
        self._shutdown_event.set()

    def _running_agent_count(self) -> int:
        return len(self._running_agents)

    def _status_action_label(self) -> str:
        return "restart" if self._restart_requested else "shutdown"

    def _status_action_gerund(self) -> str:
        return "restarting" if self._restart_requested else "shutting down"

    def _queue_during_drain_enabled(self) -> bool:
        # Both "queue" and "steer" modes imply the user doesn't want messages
        # to be lost during restart — queue them for the newly-spawned gateway
        # process to pick up.  "interrupt" mode drops them (current behaviour).
        return self._restart_requested and self._busy_input_mode in {"queue", "steer"}

    # -------- /queue FIFO helpers --------------------------------------
    # /queue must produce one full agent turn per invocation, in FIFO
    # order, with no merging.  The adapter's _pending_messages dict is a
    # single "next-up" slot (shared with photo-burst follow-ups), so we
    # use it for the head of the queue and an overflow list for the
    # tail.  Enqueue puts new items in the slot when free, otherwise in
    # the overflow.  Promotion (called after each run's drain) moves the
    # next overflow item into the slot so the following recursion picks
    # it up.  Clearing happens on /new and /reset via
    # _handle_reset_command.

    def _enqueue_fifo(self, session_key: str, queued_event: "MessageEvent", adapter: Any) -> None:
        """Append a /queue event to the FIFO chain for a session."""
        if adapter is None:
            return
        pending_slot = getattr(adapter, "_pending_messages", None)
        if pending_slot is None:
            return
        queued_events = getattr(self, "_queued_events", None)
        if queued_events is None:
            queued_events = {}
            self._queued_events = queued_events
        if session_key in pending_slot:
            queued_events.setdefault(session_key, []).append(queued_event)
        else:
            pending_slot[session_key] = queued_event

    def _promote_queued_event(
        self,
        session_key: str,
        adapter: Any,
        pending_event: Optional["MessageEvent"],
    ) -> Optional["MessageEvent"]:
        """Promote the next overflow item after the slot was drained.

        Called at the drain site after _dequeue_pending_event consumed
        (or failed to consume) the slot.  If there's an overflow item:
          - When pending_event is None (slot was empty), return the
            overflow head as the new pending_event.
          - When pending_event already exists (slot was populated by an
            interrupt follow-up or similar), stage the overflow head in
            the slot so the NEXT recursion picks it up.
        Returns the (possibly updated) pending_event for drain to use.
        """
        queued_events = getattr(self, "_queued_events", None)
        if not queued_events:
            return pending_event
        overflow = queued_events.get(session_key)
        if not overflow:
            return pending_event
        next_queued = overflow.pop(0)
        if not overflow:
            queued_events.pop(session_key, None)
        if pending_event is None:
            return next_queued
        if adapter is not None and hasattr(adapter, "_pending_messages"):
            adapter._pending_messages[session_key] = next_queued
        else:
            # No adapter — push back so we don't silently drop the item.
            queued_events.setdefault(session_key, []).insert(0, next_queued)
        return pending_event

    def _queue_depth(self, session_key: str, *, adapter: Any = None) -> int:
        """Total pending /queue items for a session — slot + overflow."""
        queued_events = getattr(self, "_queued_events", None) or {}
        depth = len(queued_events.get(session_key, []))
        if adapter is not None and session_key in getattr(adapter, "_pending_messages", {}):
            depth += 1
        return depth

    @staticmethod
    def _is_goal_continuation_event(event_or_text: Any) -> bool:
        return KanbanGoalContinuation._is_goal_continuation_event(event_or_text)

    def _clear_goal_pending_continuations(self, session_key: str, adapter: Any) -> int:
        return KanbanGoalContinuation(self)._clear_goal_pending_continuations(session_key, adapter)

    def _goal_still_active_for_session(self, session_id: str) -> bool:
        return KanbanGoalContinuation(self)._goal_still_active_for_session(session_id)

    def _update_runtime_status(self, gateway_state: Optional[str] = None, exit_reason: Optional[str] = None) -> None:
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(
                gateway_state=gateway_state,
                exit_reason=exit_reason,
                restart_requested=self._restart_requested,
                active_agents=self._running_agent_count(),
            )
        except Exception:
            pass

    def _update_platform_runtime_status(
        self,
        platform: str,
        *,
        platform_state: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(
                platform=platform,
                platform_state=platform_state,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Per-platform circuit breaker (pause/resume) — used by the reconnect
    # watcher when a retryable failure recurs past a threshold, and by the
    # /platform pause|resume slash command for manual control.
    # ------------------------------------------------------------------
    def _pause_failed_platform(self, platform, *, reason: str = "") -> None:
        """Mark a queued platform as paused — keep it in ``_failed_platforms``
        but stop the reconnect watcher from hammering it.

        Used by the circuit breaker after ``_PAUSE_AFTER_FAILURES`` consecutive
        retryable failures, and by ``/platform pause <name>`` for manual
        intervention.  Paused platforms are surfaced in ``/platform list``
        and resumed with ``/platform resume <name>``.
        """
        pause_failed_platform(
            getattr(self, "_failed_platforms", {}),
            platform,
            reason=reason,
            update_platform_runtime_status=self._update_platform_runtime_status,
            logger=logger,
        )

    def _resume_paused_platform(self, platform) -> bool:
        """Unpause a platform — reset its attempt counter and schedule an
        immediate retry.  Returns True if the platform was paused and is
        now queued; False if it wasn't paused (or wasn't in the queue).
        """
        return resume_paused_platform(
            getattr(self, "_failed_platforms", {}),
            platform,
            update_platform_runtime_status=self._update_platform_runtime_status,
            logger=logger,
        )

    @staticmethod
    def _load_prefill_messages() -> List[Dict[str, Any]]:
        """Load ephemeral prefill messages from config or env var.

        Checks HERMES_PREFILL_MESSAGES_FILE env var first, then falls back to
        the prefill_messages_file key in ~/.hermes/config.yaml.
        Relative paths are resolved from ~/.hermes/.
        """
        file_path = os.getenv("HERMES_PREFILL_MESSAGES_FILE", "")
        if not file_path:
            try:
                import yaml as _y
                cfg_path = _hermes_home / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path, encoding="utf-8") as _f:
                        cfg = _y.safe_load(_f) or {}
                    file_path = cfg.get("prefill_messages_file", "")
            except Exception:
                pass
        if not file_path:
            return []
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = _hermes_home / path
        if not path.exists():
            logger.warning("Prefill messages file not found: %s", path)
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                logger.warning("Prefill messages file must contain a JSON array: %s", path)
                return []
            return data
        except Exception as e:
            logger.warning("Failed to load prefill messages from %s: %s", path, e)
            return []

    @staticmethod
    def _load_ephemeral_system_prompt() -> str:
        """Load ephemeral system prompt from config or env var.

        Checks HERMES_EPHEMERAL_SYSTEM_PROMPT env var first, then falls back to
        agent.system_prompt in ~/.hermes/config.yaml.
        """
        prompt = os.getenv("HERMES_EPHEMERAL_SYSTEM_PROMPT", "")
        if prompt:
            return prompt
        try:
            import yaml as _y
            cfg_path = _hermes_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                return (cfg_get(cfg, "agent", "system_prompt", default="") or "").strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _load_reasoning_config() -> dict | None:
        """Load reasoning effort from config.yaml.

        Reads agent.reasoning_effort from config.yaml. Valid: "none",
        "minimal", "low", "medium", "high", "xhigh". Returns None to use
        default (medium).
        """
        from hermes_constants import parse_reasoning_effort
        effort = ""
        try:
            import yaml as _y
            cfg_path = _hermes_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                effort = str(cfg_get(cfg, "agent", "reasoning_effort", default="") or "").strip()
        except Exception:
            pass
        result = parse_reasoning_effort(effort)
        if effort and effort.strip() and result is None:
            logger.warning("Unknown reasoning_effort '%s', using default (medium)", effort)
        return result

    @staticmethod
    def _parse_reasoning_command_args(raw_args: str) -> tuple[str, bool]:
        """Parse `/reasoning` args into `(value, persist_global)`.

        `/reasoning <level>` is session-scoped by default. `--global` may be
        supplied in any position to persist the change to config.yaml.
        """
        import shlex

        text = str(raw_args or "").strip().replace("—", "--")
        if not text:
            return "", False
        try:
            tokens = shlex.split(text)
        except ValueError:
            tokens = text.split()

        persist_global = False
        value_tokens = []
        for token in tokens:
            if token == "--global":
                persist_global = True
            else:
                value_tokens.append(token)
        return " ".join(value_tokens).strip().lower(), persist_global

    def _resolve_session_reasoning_config(
        self,
        *,
        source: Optional[SessionSource] = None,
        session_key: Optional[str] = None,
    ) -> dict | None:
        """Resolve reasoning effort for a session, honoring session overrides."""
        resolved_session_key = session_key
        if not resolved_session_key and source is not None:
            try:
                resolved_session_key = self._session_key_for_source(source)
            except Exception:
                resolved_session_key = None

        overrides = getattr(self, "_session_reasoning_overrides", {}) or {}
        if resolved_session_key and resolved_session_key in overrides:
            return overrides[resolved_session_key]
        return self._load_reasoning_config()

    def _set_session_reasoning_override(
        self,
        session_key: str,
        reasoning_config: Optional[dict],
    ) -> None:
        """Set or clear the session-scoped reasoning override."""
        if not session_key:
            return
        if not hasattr(self, "_session_reasoning_overrides"):
            self._session_reasoning_overrides = {}
        if reasoning_config is None:
            self._session_reasoning_overrides.pop(session_key, None)
        else:
            self._session_reasoning_overrides[session_key] = dict(reasoning_config)

    @staticmethod
    def _load_service_tier() -> str | None:
        """Load Priority Processing setting from config.yaml.

        Reads agent.service_tier from config.yaml. Accepted values mirror the CLI:
        "fast"/"priority"/"on" => "priority", while "normal"/"off" disables it.
        Returns None when unset or unsupported.
        """
        return load_service_tier(_hermes_home / "config.yaml", logger=logger)

    @staticmethod
    def _load_show_reasoning() -> bool:
        """Load show_reasoning toggle from config.yaml display section."""
        try:
            import yaml as _y
            cfg_path = _hermes_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                return is_truthy_value(
                    cfg_get(cfg, "display", "show_reasoning"),
                    default=False,
                )
        except Exception:
            pass
        return False

    @staticmethod
    def _load_busy_input_mode() -> str:
        """Load gateway drain-time busy-input behavior from config/env."""
        mode = os.getenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "").strip().lower()
        if not mode:
            try:
                import yaml as _y
                cfg_path = _hermes_home / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path, encoding="utf-8") as _f:
                        cfg = _y.safe_load(_f) or {}
                    mode = str(cfg_get(cfg, "display", "busy_input_mode", default="") or "").strip().lower()
            except Exception:
                pass
        if mode == "queue":
            return "queue"
        if mode == "steer":
            return "steer"
        return "interrupt"

    @staticmethod
    def _load_restart_drain_timeout() -> float:
        """Load graceful gateway restart/stop drain timeout in seconds."""
        raw = os.getenv("HERMES_RESTART_DRAIN_TIMEOUT", "").strip()
        if not raw:
            try:
                import yaml as _y
                cfg_path = _hermes_home / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path, encoding="utf-8") as _f:
                        cfg = _y.safe_load(_f) or {}
                    raw = str(cfg_get(cfg, "agent", "restart_drain_timeout", default="") or "").strip()
            except Exception:
                pass
        value = parse_restart_drain_timeout(raw)
        if raw and value == DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT:
            try:
                float(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid restart_drain_timeout '%s', using default %.0fs",
                    raw,
                    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
                )
        return value

    @staticmethod
    def _load_background_notifications_mode() -> str:
        """Load background process notification mode from config or env var.

        Modes:
          - ``all``    — push running-output updates *and* the final message (default)
          - ``result`` — only the final completion message (regardless of exit code)
          - ``error``  — only the final message when exit code is non-zero
          - ``off``    — no watcher messages at all
        """
        mode = os.getenv("HERMES_BACKGROUND_NOTIFICATIONS", "")
        if not mode:
            try:
                import yaml as _y
                cfg_path = _hermes_home / "config.yaml"
                if cfg_path.exists():
                    with open(cfg_path, encoding="utf-8") as _f:
                        cfg = _y.safe_load(_f) or {}
                    raw = cfg_get(cfg, "display", "background_process_notifications")
                    if raw is False:
                        mode = "off"
                    elif raw not in {None, ""}:
                        mode = str(raw)
            except Exception:
                pass
        mode = (mode or "all").strip().lower()
        valid = {"all", "result", "error", "off"}
        if mode not in valid:
            logger.warning(
                "Unknown background_process_notifications '%s', defaulting to 'all'",
                mode,
            )
            return "all"
        return mode

    @staticmethod
    def _load_provider_routing() -> dict:
        """Load OpenRouter provider routing preferences from config.yaml."""
        return load_provider_routing(_hermes_home / "config.yaml")

    @staticmethod
    def _load_fallback_model() -> list | dict | None:
        """Load fallback provider chain from config.yaml.

        Returns a list of provider dicts (``fallback_providers``), a single
        dict (legacy ``fallback_model``), or None if not configured.
        AIAgent.__init__ normalizes both formats into a chain.
        """
        return load_fallback_model(_hermes_home / "config.yaml")

    def _snapshot_running_agents(self) -> Dict[str, Any]:
        return snapshot_running_agents(
            self._running_agents,
            pending_sentinel=_AGENT_PENDING_SENTINEL,
        )

    def _queue_or_replace_pending_event(self, session_key: str, event: MessageEvent) -> None:
        adapter = self.adapters.get(event.source.platform)
        if not adapter:
            return
        merge_pending_message_event(adapter._pending_messages, session_key, event)

    async def _handle_active_session_busy_message(self, event: MessageEvent, session_key: str) -> bool:
        return await handle_active_session_busy_message(
            event=event, session_key=session_key,
            is_user_authorized=self._is_user_authorized,
            adapters=self.adapters, running_agents=self._running_agents,
            running_agents_ts=self._running_agents_ts, busy_ack_ts=getattr(self, "_busy_ack_ts", {}),
            busy_input_mode=self._busy_input_mode, draining=self._draining,
            pending_sentinel=_AGENT_PENDING_SENTINEL,
            queue_during_drain_enabled=self._queue_during_drain_enabled,
            queue_or_replace_pending_event=self._queue_or_replace_pending_event,
            status_action_gerund=self._status_action_gerund,
            reply_anchor_for_event=self._reply_anchor_for_event,
            thread_metadata_for_source=self._thread_metadata_for_source,
            load_gateway_config=_load_gateway_config, hermes_home=_hermes_home, log=logger,
            merge_pending_message_event_fn=merge_pending_message_event,
        )

    async def _drain_active_agents(self, timeout: float) -> tuple[Dict[str, Any], bool]:
        return await drain_active_agents(
            running_agents=self._running_agents,
            snapshot_running_agents=self._snapshot_running_agents,
            running_agent_count=self._running_agent_count,
            update_runtime_status=self._update_runtime_status,
            timeout=timeout,
        )

    def _interrupt_running_agents(self, reason: str) -> None:
        interrupt_running_agents(
            running_agents=self._running_agents,
            pending_sentinel=_AGENT_PENDING_SENTINEL,
            reason=reason,
            log=logger,
        )

    async def _notify_active_sessions_of_shutdown(self) -> None:
        """Send shutdown/restart notifications to active chats and home channels.

        Called at the very start of stop() — adapters are still connected so
        messages can be delivered. Best-effort: individual send failures are
        logged and swallowed so they never block the shutdown sequence.
        """
        await notify_active_sessions_of_shutdown(
            active_session_keys=self._snapshot_running_agents(),
            adapters=self.adapters,
            config=getattr(self, "config", GatewayConfig()),
            restart_requested=self._restart_requested,
            session_store=getattr(self, "session_store", None),
            get_cached_session_source=self._get_cached_session_source,
            logger=logger,
        )

    def _finalize_shutdown_agents(self, active_agents: Dict[str, Any]) -> None:
        try:
            from hermes_cli.plugins import invoke_hook as _invoke_hook
        except Exception:
            _invoke_hook = lambda *_args, **_kwargs: None
        finalize_shutdown_agents(
            active_agents=active_agents,
            cleanup_agent_resources=self._cleanup_agent_resources,
            invoke_hook=_invoke_hook,
        )

    def _cleanup_agent_resources(self, agent: Any) -> None:
        cleanup_agent_resources(agent)

    _STUCK_LOOP_THRESHOLD = 3  # restarts while active before auto-suspend
    _STUCK_LOOP_FILE = ".restart_failure_counts"

    def _increment_restart_failure_counts(self, active_session_keys: set) -> None:
        increment_restart_failure_counts(
            hermes_home=_hermes_home,
            failure_file=self._STUCK_LOOP_FILE,
            active_session_keys=active_session_keys,
            atomic_json_write_fn=atomic_json_write,
        )

    def _suspend_stuck_loop_sessions(self) -> int:
        return suspend_stuck_loop_sessions(
            hermes_home=_hermes_home,
            failure_file=self._STUCK_LOOP_FILE,
            threshold=self._STUCK_LOOP_THRESHOLD,
            session_store=self.session_store,
            logger=logger,
        )

    def _clear_restart_failure_count(self, session_key: str) -> None:
        clear_restart_failure_count(
            hermes_home=_hermes_home,
            failure_file=self._STUCK_LOOP_FILE,
            session_key=session_key,
            atomic_json_write_fn=atomic_json_write,
        )

    async def _launch_detached_restart_command(self) -> None:
        await launch_detached_restart_command(
            resolve_hermes_bin=_resolve_hermes_bin,
            logger=logger,
        )

    def request_restart(self, *, detached: bool = False, via_service: bool = False) -> bool:
        if self._restart_task_started:
            return False
        self._restart_requested = True
        self._restart_detached = detached
        self._restart_via_service = via_service
        self._restart_task_started = True

        async def _run_restart() -> None:
            await asyncio.sleep(0.05)
            await self.stop(restart=True, detached_restart=detached, service_restart=via_service)

        task = asyncio.create_task(_run_restart())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return True

    # Drain-timeout reasons set by _stop_impl() when a still-running turn is
    # force-interrupted; "restart_interrupted" is set by
    # SessionStore.suspend_recently_active() on crash recovery (no
    # .clean_shutdown marker).  All three mean "the agent was mid-turn and
    # we killed it" — eligible for startup auto-resume.
    _AUTO_RESUME_REASONS = frozenset(
        {"restart_timeout", "shutdown_timeout", "restart_interrupted"}
    )

    def _schedule_resume_pending_sessions(self) -> int:
        return schedule_resume_pending_sessions(
            session_store=self.session_store,
            adapters=self.adapters,
            background_tasks=getattr(self, "_background_tasks", set()),
            auto_resume_reasons=self._AUTO_RESUME_REASONS,
            freshness_window=_auto_continue_freshness_window(),
            logger=logger,
        )

    async def start(self) -> bool:
        """
        Start the gateway and all configured platform adapters.

        Returns True if at least one adapter connected successfully.
        """
        logger.info("Starting Hermes Gateway...")
        try:
            self._gateway_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._gateway_loop = None
        logger.info("Session storage: %s", self.config.sessions_dir)

        # Sanity-check that systemd's TimeoutStopSec covers our drain
        # window.  When the user upgraded hermes-agent without re-running
        # ``hermes setup``, their unit file may still encode the old
        # default — in which case SIGKILL hits mid-drain and looks like
        # a phantom kill in the journal.  Best-effort, never raises.
        try:
            from gateway.shutdown_forensics import check_systemd_timing_alignment
            _alignment = check_systemd_timing_alignment(self._restart_drain_timeout)
            if _alignment is not None and _alignment.get("mismatch"):
                logger.warning(
                    "Stale systemd unit detected: %s has TimeoutStopSec=%.0fs but "
                    "drain_timeout=%.0fs (expected >=%.0fs). systemd may SIGKILL the "
                    "gateway mid-drain. Run `hermes gateway service install --replace` "
                    "to regenerate the unit, or shorten agent.restart_drain_timeout.",
                    _alignment.get("unit", "(unknown)"),
                    _alignment["timeout_stop_sec"],
                    _alignment["drain_timeout"],
                    _alignment["expected_min"],
                )
        except Exception as _e:
            logger.debug("check_systemd_timing_alignment failed: %s", _e)
        # Log the resolved max_iterations budget so operators can verify the
        # config.yaml → env bridge did the right thing at a glance (instead
        # of silently running at a stale .env value for weeks).
        try:
            _effective_max_iter = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))
            logger.info(
                "Agent budget: max_iterations=%d (agent.max_turns from config.yaml, "
                "or HERMES_MAX_ITERATIONS from .env, or default 90)",
                _effective_max_iter,
            )
        except Exception:
            pass
        # Redaction status: ON by default (#17691). Surface a prominent
        # warning if an operator has explicitly opted out so they don't
        # forget the downgrade is active — the redactor snapshots its
        # state at import time, so this log line is the source of truth
        # for this process's lifetime.
        try:
            _redact_raw = os.getenv("HERMES_REDACT_SECRETS", "true")
            _redact_on = _redact_raw.lower() in {"1", "true", "yes", "on"}
            if _redact_on:
                logger.info(
                    "Secret redaction: ENABLED (tool output, logs, and chat "
                    "responses are scrubbed before delivery)"
                )
            else:
                logger.warning(
                    "Secret redaction: DISABLED (HERMES_REDACT_SECRETS=%s). "
                    "API keys and tokens may appear verbatim in chat output, "
                    "session JSONs, and logs. Set security.redact_secrets: true "
                    "in config.yaml to re-enable.",
                    _redact_raw,
                )
        except Exception:
            pass
        try:
            from hermes_cli.profiles import get_active_profile_name
            _profile = get_active_profile_name()
            if _profile and _profile != "default":
                logger.info("Active profile: %s", _profile)
        except Exception:
            pass
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(gateway_state="starting", exit_reason=None)
        except Exception:
            pass

        # Log any active supply-chain security advisories. Operators see this
        # in gateway.log and `hermes status` surfaces it; we do NOT block
        # startup or surface it inline to user messages, since the gateway
        # operator is the one who can act on it (uninstall the package,
        # rotate credentials).  See hermes_cli/security_advisories.py.
        try:
            from hermes_cli.security_advisories import (
                detect_compromised,
                gateway_log_message,
            )
            _adv_hits = detect_compromised()
            _adv_msg = gateway_log_message(_adv_hits)
            if _adv_msg:
                logger.warning("%s", _adv_msg)
                logger.warning(
                    "Run `hermes doctor` on the gateway host for full "
                    "remediation steps."
                )
        except Exception:
            logger.debug(
                "security advisory check failed at gateway startup",
                exc_info=True,
            )

        # Warn if no user allowlists are configured and open access is not opted in
        _builtin_allowed_vars = (
            "TELEGRAM_ALLOWED_USERS", "DISCORD_ALLOWED_USERS",
            "WHATSAPP_ALLOWED_USERS", "SLACK_ALLOWED_USERS",
            "SIGNAL_ALLOWED_USERS", "SIGNAL_GROUP_ALLOWED_USERS",
            "TELEGRAM_GROUP_ALLOWED_USERS",
            "TELEGRAM_GROUP_ALLOWED_CHATS",
            "EMAIL_ALLOWED_USERS",
            "SMS_ALLOWED_USERS", "MATTERMOST_ALLOWED_USERS",
            "MATRIX_ALLOWED_USERS", "DINGTALK_ALLOWED_USERS",
            "FEISHU_ALLOWED_USERS",
            "WECOM_ALLOWED_USERS",
            "WECOM_CALLBACK_ALLOWED_USERS",
            "WEIXIN_ALLOWED_USERS",
            "BLUEBUBBLES_ALLOWED_USERS",
            "QQ_ALLOWED_USERS",
            "YUANBAO_ALLOWED_USERS",
            "GATEWAY_ALLOWED_USERS",
        )
        _builtin_allow_all_vars = (
            "TELEGRAM_ALLOW_ALL_USERS", "DISCORD_ALLOW_ALL_USERS",
            "WHATSAPP_ALLOW_ALL_USERS", "SLACK_ALLOW_ALL_USERS",
            "SIGNAL_ALLOW_ALL_USERS", "EMAIL_ALLOW_ALL_USERS",
            "SMS_ALLOW_ALL_USERS", "MATTERMOST_ALLOW_ALL_USERS",
            "MATRIX_ALLOW_ALL_USERS", "DINGTALK_ALLOW_ALL_USERS",
            "FEISHU_ALLOW_ALL_USERS",
            "WECOM_ALLOW_ALL_USERS",
            "WECOM_CALLBACK_ALLOW_ALL_USERS",
            "WEIXIN_ALLOW_ALL_USERS",
            "BLUEBUBBLES_ALLOW_ALL_USERS",
            "QQ_ALLOW_ALL_USERS",
            "YUANBAO_ALLOW_ALL_USERS",
        )
        # Also pick up plugin-registered platforms — each entry can declare
        # its own allowed_users_env / allow_all_env, so the warning stays
        # accurate as plugins like IRC come online.
        _plugin_allowed_vars: tuple = ()
        _plugin_allow_all_vars: tuple = ()
        try:
            from gateway.platform_registry import platform_registry
            _plugin_allowed_vars = tuple(
                e.allowed_users_env for e in platform_registry.plugin_entries()
                if e.allowed_users_env
            )
            _plugin_allow_all_vars = tuple(
                e.allow_all_env for e in platform_registry.plugin_entries()
                if e.allow_all_env
            )
        except Exception:
            pass
        _any_allowlist = any(
            os.getenv(v) for v in _builtin_allowed_vars + _plugin_allowed_vars
        )
        _allow_all = os.getenv("GATEWAY_ALLOW_ALL_USERS", "").lower() in {"true", "1", "yes"} or any(
            os.getenv(v, "").lower() in {"true", "1", "yes"}
            for v in _builtin_allow_all_vars + _plugin_allow_all_vars
        )
        if not _any_allowlist and not _allow_all:
            logger.warning(
                "No user allowlists configured. All unauthorized users will be denied. "
                "Set GATEWAY_ALLOW_ALL_USERS=true in ~/.hermes/.env to allow open access, "
                "or configure platform allowlists (e.g., TELEGRAM_ALLOWED_USERS=your_id)."
            )

        # Discover Python plugins before shell hooks so plugin block
        # decisions take precedence in tie cases.  The CLI startup path
        # does this via an explicit call in hermes_cli/main.py; the
        # gateway lazily imports run_agent inside per-request handlers,
        # so the discover_plugins() side-effect in model_tools.py is NOT
        # guaranteed to have run by the time we reach this point.
        try:
            from hermes_cli.plugins import discover_plugins
            discover_plugins()
        except Exception:
            logger.warning(
                "plugin discovery failed at gateway startup", exc_info=True,
            )

        # Register declarative shell hooks from cli-config.yaml.  Gateway
        # has no TTY, so consent has to come from one of the three opt-in
        # channels (--accept-hooks on launch, HERMES_ACCEPT_HOOKS env var,
        # or hooks_auto_accept: true in config.yaml).  We pass
        # accept_hooks=False here and let register_from_config resolve
        # the effective value from env + config itself — the CLI-side
        # registration already honored --accept-hooks, and re-reading
        # hooks_auto_accept here would just duplicate that lookup.
        # Failures are logged but must never block gateway startup.
        try:
            from hermes_cli.config import load_config
            from agent.shell_hooks import register_from_config
            register_from_config(load_config(), accept_hooks=False)
        except Exception:
            logger.debug(
                "shell-hook registration failed at gateway startup",
                exc_info=True,
            )

        # Discover and load event hooks
        self.hooks.discover_and_load()


        # Recover background processes from checkpoint (crash recovery)
        try:
            from tools.process_registry import process_registry
            recovered = process_registry.recover_from_checkpoint()
            if recovered:
                logger.info("Recovered %s background process(es) from previous run", recovered)
        except Exception as e:
            logger.warning("Process checkpoint recovery: %s", e)

        # Suspend sessions that were active when the gateway last exited.
        # This prevents stuck sessions from being blindly resumed on restart,
        # which can create an unrecoverable loop (#7536).  Suspended sessions
        # auto-reset on the next incoming message, giving the user a clean start.
        #
        # SKIP suspension after a clean (graceful) shutdown — the previous
        # process already drained active agents, so sessions aren't stuck.
        # This prevents unwanted auto-resets after `hermes update`,
        # `hermes gateway restart`, or `/restart`.
        _clean_marker = _hermes_home / ".clean_shutdown"
        if _clean_marker.exists():
            logger.info("Previous gateway exited cleanly — skipping session suspension")
            try:
                _clean_marker.unlink()
            except Exception:
                pass
        else:
            try:
                suspended = self.session_store.suspend_recently_active()
                if suspended:
                    logger.info("Marked %d in-flight session(s) as resumable from previous run", suspended)
            except Exception as e:
                logger.warning("Session suspension on startup failed: %s", e)

        # Stuck-loop detection (#7536): if a session has been active across
        # 3+ consecutive restarts, it's probably stuck in a loop (the same
        # history keeps causing the agent to hang).  Auto-suspend it so the
        # user gets a clean slate on the next message.
        try:
            stuck = self._suspend_stuck_loop_sessions()
            if stuck:
                logger.warning("Auto-suspended %d stuck-loop session(s)", stuck)
        except Exception as e:
            logger.debug("Stuck-loop detection failed: %s", e)

        connected_count = 0
        enabled_platform_count = 0
        startup_nonretryable_errors: list[str] = []
        startup_retryable_errors: list[str] = []

        # Initialize and connect each configured platform
        for platform, platform_config in self.config.platforms.items():
            if not platform_config.enabled:
                continue
            enabled_platform_count += 1

            adapter = self._create_adapter(platform, platform_config)
            if not adapter:
                # Distinguish between missing builtin deps and missing plugin
                _pval = platform.value
                _builtin_names = {m.value for m in Platform.__members__.values()}
                if _pval not in _builtin_names:
                    logger.warning(
                        "No adapter for '%s' — is the plugin installed? "
                        "(platform is enabled in config.yaml but no plugin registered it)",
                        _pval,
                    )
                else:
                    logger.warning("No adapter available for %s", _pval)
                continue

            # Set up message + fatal error handlers
            adapter.set_message_handler(self._handle_message)
            adapter.set_fatal_error_handler(self._handle_adapter_fatal_error)
            adapter.set_session_store(self.session_store)
            adapter.set_busy_session_handler(self._handle_active_session_busy_message)

            # Try to connect
            logger.info("Connecting to %s...", platform.value)
            self._update_platform_runtime_status(
                platform.value,
                platform_state="connecting",
                error_code=None,
                error_message=None,
            )
            try:
                success = await self._connect_adapter_with_timeout(adapter, platform)
                if success:
                    self.adapters[platform] = adapter
                    self._sync_voice_mode_state_to_adapter(adapter)
                    connected_count += 1
                    self._update_platform_runtime_status(
                        platform.value,
                        platform_state="connected",
                        error_code=None,
                        error_message=None,
                    )
                    logger.info("✓ %s connected", platform.value)
                else:
                    logger.warning("✗ %s failed to connect", platform.value)
                    # Defensive cleanup: a failed connect() may have
                    # allocated resources (aiohttp.ClientSession, poll
                    # tasks, bridge subprocesses) before giving up.
                    # Without this call, those resources are orphaned
                    # and Python logs "Unclosed client session" at
                    # process exit. Adapter disconnect() implementations
                    # are expected to be idempotent and tolerate
                    # partial-init state.
                    await self._safe_adapter_disconnect(adapter, platform)
                    if adapter.has_fatal_error:
                        self._update_platform_runtime_status(
                            platform.value,
                            platform_state="retrying" if adapter.fatal_error_retryable else "fatal",
                            error_code=adapter.fatal_error_code,
                            error_message=adapter.fatal_error_message,
                        )
                        target = (
                            startup_retryable_errors
                            if adapter.fatal_error_retryable
                            else startup_nonretryable_errors
                        )
                        target.append(
                            f"{platform.value}: {adapter.fatal_error_message}"
                        )
                        # Queue for reconnection if the error is retryable
                        if adapter.fatal_error_retryable:
                            self._failed_platforms[platform] = {
                                "config": platform_config,
                                "attempts": 1,
                                "next_retry": time.monotonic() + 30,
                            }
                    else:
                        self._update_platform_runtime_status(
                            platform.value,
                            platform_state="retrying",
                            error_code=None,
                            error_message="failed to connect",
                        )
                        startup_retryable_errors.append(
                            f"{platform.value}: failed to connect"
                        )
                        # No fatal error info means likely a transient issue — queue for retry
                        self._failed_platforms[platform] = {
                            "config": platform_config,
                            "attempts": 1,
                            "next_retry": time.monotonic() + 30,
                        }
            except Exception as e:
                logger.error("✗ %s error: %s", platform.value, e)
                # Same defensive cleanup path for exceptions — an adapter
                # that raised mid-connect may still have a live
                # aiohttp.ClientSession or child subprocess.
                await self._safe_adapter_disconnect(adapter, platform)
                self._update_platform_runtime_status(
                    platform.value,
                    platform_state="retrying",
                    error_code=None,
                    error_message=str(e),
                )
                startup_retryable_errors.append(f"{platform.value}: {e}")
                # Unexpected exceptions are typically transient — queue for retry
                self._failed_platforms[platform] = {
                    "config": platform_config,
                    "attempts": 1,
                    "next_retry": time.monotonic() + 30,
                }

        if connected_count == 0:
            if startup_nonretryable_errors:
                reason = "; ".join(startup_nonretryable_errors)
                logger.error("Gateway hit a non-retryable startup conflict: %s", reason)
                try:
                    from gateway.status import write_runtime_status
                    write_runtime_status(gateway_state="startup_failed", exit_reason=reason)
                except Exception:
                    pass
                self._request_clean_exit(reason)
                return True
            if enabled_platform_count > 0:
                if startup_retryable_errors:
                    # All enabled platforms hit retryable failures (network
                    # blip, bridge not paired, npm install timeout, etc.).
                    # Keep the gateway alive so:
                    #   • cron jobs still run
                    #   • the reconnect watcher gets a chance to recover the
                    #     failing platforms once the underlying problem is
                    #     fixed (e.g. user runs `hermes whatsapp`, fixes
                    #     proxy, etc.)
                    # Exiting here used to convert a single misconfigured
                    # platform into an infinite systemd restart loop.
                    reason = "; ".join(startup_retryable_errors)
                    logger.warning(
                        "Gateway started with no connected platforms — "
                        "%d platform(s) queued for retry: %s",
                        len(self._failed_platforms), reason,
                    )
                    try:
                        from gateway.status import write_runtime_status
                        write_runtime_status(
                            gateway_state="degraded",
                            exit_reason=None,
                        )
                    except Exception:
                        pass
                    # Fall through to the normal "running" state — reconnect
                    # watcher takes it from here.
                # All enabled platforms had no adapter (missing library or credentials).
                # In fleet deployments the same config.yaml is shared across nodes that
                # may only have credentials for a subset of platforms.  Rather than
                # failing hard, degrade gracefully and allow cron jobs to run (#5196).
                logger.warning(
                    "No adapter could be created for any of the %d configured platform(s). "
                    "Check that required dependencies are installed and credentials are set. "
                    "Gateway will continue for cron job execution.",
                    enabled_platform_count,
                )
            else:
                logger.warning("No messaging platforms enabled.")
                logger.info("Gateway will continue running for cron job execution.")

        # Update delivery router with adapters
        self.delivery_router.adapters = self.adapters
        self._wire_teams_pipeline_runtime()

        self._running = True
        self._update_runtime_status("running")

        # Emit gateway:startup hook
        hook_count = len(self.hooks.loaded_hooks)
        if hook_count:
            logger.info("%s hook(s) loaded", hook_count)
        await self.hooks.emit("gateway:startup", {
            "platforms": [p.value for p in self.adapters.keys()],
        })

        if connected_count > 0:
            logger.info("Gateway running with %s platform(s)", connected_count)

        # Build initial channel directory for send_message name resolution
        try:
            from gateway.channel_directory import build_channel_directory
            directory = await build_channel_directory(self.adapters)
            ch_count = sum(len(chs) for chs in directory.get("platforms", {}).values())
            logger.info("Channel directory built: %d target(s)", ch_count)
        except Exception as e:
            logger.warning("Channel directory build failed: %s", e)

        # Check if we're restarting after a /update command. If the update is
        # still running, keep watching so we notify once it actually finishes.
        notified = await self._send_update_notification()
        if not notified and any(
            path.exists()
            for path in (
                _hermes_home / ".update_pending.json",
                _hermes_home / ".update_pending.claimed.json",
            )
        ):
            self._schedule_update_notification_watch()

        # Give freshly connected platform adapters a brief moment to settle
        # before sending restart/startup lifecycle messages. In practice this
        # helps Discord thread deliveries right after reconnect.
        if connected_count > 0:
            await asyncio.sleep(1.0)

        # Notify the chat that initiated /restart that the gateway is back.
        restart_notification_pending = _restart_notification_pending()
        delivered_restart_target = await self._send_restart_notification()

        # Broadcast a lightweight "gateway is back" message to configured
        # home channels only when this startup is resuming from /restart. If a
        # /restart requester already received a direct completion notice in the
        # same chat, skip the generic broadcast there to avoid duplicates while
        # still allowing a home-channel fallback when the direct send fails.
        if restart_notification_pending or delivered_restart_target is not None:
            skip_home_targets = (
                {delivered_restart_target} if delivered_restart_target else None
            )
            await self._send_home_channel_startup_notifications(
                skip_targets=skip_home_targets,
            )

        # Automatically continue fresh sessions that were interrupted by the
        # previous gateway restart/shutdown.  The resume_pending flag is cleared
        # by the normal successful-turn path, so a failed auto-resume remains
        # visible for manual recovery on the next user message.
        self._schedule_resume_pending_sessions()

        # Drain any recovered process watchers (from crash recovery checkpoint)
        try:
            from tools.process_registry import process_registry
            while process_registry.pending_watchers:
                watcher = process_registry.pending_watchers.pop(0)
                asyncio.create_task(self._run_process_watcher(watcher))
                logger.info("Resumed watcher for recovered process %s", watcher.get("session_id"))
        except Exception as e:
            logger.error("Recovered watcher setup error: %s", e)

        # Start background session expiry watcher to finalize expired sessions
        asyncio.create_task(self._session_expiry_watcher())

        # Start background kanban notifier — delivers `completed`, `blocked`,
        # `spawn_auto_blocked`, and `crashed` events to gateway subscribers
        # so human-in-the-loop workflows hear back without polling.
        asyncio.create_task(self._kanban_notifier_watcher())

        # Start background kanban dispatcher — spawns workers for ready
        # tasks. Gated by `kanban.dispatch_in_gateway` (default True).
        # When false, users run `hermes kanban daemon` externally or
        # simply don't use kanban; this loop becomes a no-op.
        asyncio.create_task(self._kanban_dispatcher_watcher())

        # Start background reconnection watcher for platforms that failed at startup
        if self._failed_platforms:
            logger.info(
                "Starting reconnection watcher for %d failed platform(s): %s",
                len(self._failed_platforms),
                ", ".join(p.value for p in self._failed_platforms),
            )
        asyncio.create_task(self._platform_reconnect_watcher())

        # Start background handoff watcher — picks up CLI sessions marked
        # handoff_state='pending' in state.db and re-binds them to the
        # destination platform's home channel, then forges a synthetic user
        # turn so the agent kicks off the new chat.
        asyncio.create_task(self._handoff_watcher())

        logger.info("Press Ctrl+C to stop")

        return True

    async def _handoff_watcher(self, interval: float = 2.0) -> None:
        await SessionHandoffWatcher(self)._handoff_watcher(interval)

    async def _process_handoff(self, row: Dict[str, Any]) -> None:
        await SessionHandoffWatcher(self)._process_handoff(row)

    async def _session_expiry_watcher(self, interval: int = 300):
        await SessionExpiryWatcher(self, _AGENT_PENDING_SENTINEL)._session_expiry_watcher(interval)

    def _active_profile_name(self) -> str:
        """Return the profile name this gateway represents."""
        try:
            from hermes_cli.profiles import get_active_profile_name
            return get_active_profile_name() or "default"
        except Exception:
            return "default"

    async def _kanban_notifier_watcher(self, interval: float = 5.0) -> None:
        await KanbanNotifierWatcher(self)._kanban_notifier_watcher(interval)

    def _kanban_advance(self, sub: dict, cursor: int, board: Optional[str] = None) -> None:
        KanbanNotifierWatcher(self)._kanban_advance(sub, cursor, board)

    def _kanban_unsub(self, sub: dict, board: Optional[str] = None) -> None:
        KanbanNotifierWatcher(self)._kanban_unsub(sub, board)

    def _kanban_rewind(self, sub: dict, claimed_cursor: int, old_cursor: int, board: Optional[str] = None) -> None:
        KanbanNotifierWatcher(self)._kanban_rewind(sub, claimed_cursor, old_cursor, board)

    async def _deliver_kanban_artifacts(
        self,
        *,
        adapter: Any,
        chat_id: str,
        metadata: Optional[Dict[str, Any]],
        event_payload: Optional[dict],
        task: Any,
    ) -> None:
        await KanbanArtifactDelivery(self)._deliver_kanban_artifacts(
            adapter=adapter,
            chat_id=chat_id,
            metadata=metadata,
            event_payload=event_payload,
            task=task,
        )

    async def _kanban_dispatcher_watcher(self) -> None:
        await KanbanDispatcherWatcher(self)._kanban_dispatcher_watcher()

    async def _platform_reconnect_watcher(self) -> None:
        """Background task that periodically retries connecting failed platforms.

        Uses exponential backoff: 30s → 60s → 120s → 240s → 300s (cap).
        Retryable failures keep retrying at the backoff cap indefinitely
        — but if a platform fails ``_PAUSE_AFTER_FAILURES`` times in a row
        without ever succeeding, it is *paused*: kept in the retry queue
        but no longer hammered.  The user surfaces it with ``/platform list``
        and resumes it with ``/platform resume <name>``.  Non-retryable
        failures (bad auth, etc.) still drop out of the queue immediately.
        """
        _BACKOFF_CAP = 300  # 5 minutes max between retries
        _PAUSE_AFTER_FAILURES = 10  # circuit-breaker threshold

        async def retry_platform(platform: Platform) -> bool:
            try:
                from gateway.channel_directory import build_channel_directory
            except Exception:
                build_channel_directory = None

            return await retry_failed_platform(
                platform,
                failed_platforms=self._failed_platforms,
                adapters=self.adapters,
                delivery_router=self.delivery_router,
                create_adapter=self._create_adapter,
                connect_adapter=self._connect_adapter_with_timeout,
                handle_message=self._handle_message,
                handle_adapter_fatal_error=self._handle_adapter_fatal_error,
                session_store=self.session_store,
                handle_active_session_busy_message=self._handle_active_session_busy_message,
                sync_voice_mode_state_to_adapter=self._sync_voice_mode_state_to_adapter,
                update_platform_runtime_status=self._update_platform_runtime_status,
                rebuild_channel_directory=build_channel_directory,
                backoff_cap=_BACKOFF_CAP,
                pause_after_failures=_PAUSE_AFTER_FAILURES,
                logger=logger,
            )

        await run_platform_reconnect_watcher(
            is_running=lambda: self._running,
            failed_platforms=self._failed_platforms,
            retry_platform=retry_platform,
            sleep=asyncio.sleep,
        )

    async def stop(
        self,
        *,
        restart: bool = False,
        detached_restart: bool = False,
        service_restart: bool = False,
    ) -> None:
        """Stop the gateway and disconnect all adapters."""
        await GatewayStopRuntime(
            self,
            pending_sentinel=_AGENT_PENDING_SENTINEL,
            hermes_home=_hermes_home,
            service_restart_exit_code=GATEWAY_SERVICE_RESTART_EXIT_CODE,
            interrupt_reason_restart=_INTERRUPT_REASON_GATEWAY_RESTART,
            interrupt_reason_shutdown=_INTERRUPT_REASON_GATEWAY_SHUTDOWN,
        ).stop(
            restart=restart,
            detached_restart=detached_restart,
            service_restart=service_restart,
        )

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

    def _create_adapter(
        self,
        platform: Platform,
        config: Any,
    ) -> Optional[BasePlatformAdapter]:
        """Create the appropriate adapter for a platform."""
        return create_platform_adapter(
            platform,
            config,
            gateway_config=self.config,
            gateway_runner=self,
            load_gateway_config=_load_gateway_config,
            logger_=logger,
        )
    def _is_user_authorized(self, source: SessionSource) -> bool:
        """Check if a user is authorized to use the bot."""
        decision = resolve_source_authorization(
            source,
            pairing_store=getattr(self, "pairing_store", None),
            warned_telegram_group_users_legacy=getattr(
                self,
                "_warned_telegram_group_users_legacy",
                False,
            ),
            logger_=logger,
        )
        if decision.warned_telegram_group_users_legacy:
            self._warned_telegram_group_users_legacy = True
        return decision.authorized

    def _get_unauthorized_dm_behavior(self, platform: Optional[Platform]) -> str:
        """Return how unauthorized DMs should be handled for a platform.

        Resolution order:
        1. Explicit per-platform ``unauthorized_dm_behavior`` in config — always wins.
        2. Explicit global ``unauthorized_dm_behavior`` in config — wins when no per-platform.
        3. When an allowlist (``PLATFORM_ALLOWED_USERS``,
           ``PLATFORM_GROUP_ALLOWED_USERS`` / ``PLATFORM_GROUP_ALLOWED_CHATS``,
           or ``GATEWAY_ALLOWED_USERS``) is configured, default to ``"ignore"`` —
           the allowlist signals that the owner has deliberately restricted
           access; spamming unknown contacts with pairing codes is both noisy
           and a potential info-leak. (#9337)
        4. No allowlist and no explicit config → ``"pair"`` (open-gateway default).
        """
        from gateway.message_router import resolve_unauthorized_dm_behavior

        return resolve_unauthorized_dm_behavior(
            config=getattr(self, "config", None),
            platform=platform,
        )

    async def _deliver_platform_notice(self, source, content: str) -> None:
        """Deliver a setup/operational notice using platform-specific privacy rules."""
        adapter = self.adapters.get(source.platform)
        if not adapter:
            return

        config = getattr(self, "config", None)
        notice_delivery = "public"
        if config and hasattr(config, "get_notice_delivery"):
            notice_delivery = config.get_notice_delivery(source.platform)

        metadata = self._thread_metadata_for_source(source)
        if notice_delivery == "private" and getattr(source, "user_id", None):
            try:
                result = await adapter.send_private_notice(
                    source.chat_id,
                    source.user_id,
                    content,
                    metadata=metadata,
                )
                if getattr(result, "success", False):
                    return
            except Exception:
                logger.debug(
                    "[%s] send_private_notice failed, falling back to public",
                    getattr(source, "platform", "?"),
                    exc_info=True,
                )

        await adapter.send(source.chat_id, content, metadata=metadata)

    async def _handle_message(self, event: MessageEvent) -> Optional[str]:
        """
        Handle an incoming message from any platform.

        This is the core message processing pipeline:
        1. Check user authorization
        2. Check for commands (/new, /reset, etc.)
        3. Check for running agent and interrupt if needed
        4. Get or create session
        5. Build context for agent
        6. Run agent conversation
        7. Return response
        """
        source = event.source

        # Internal events (e.g. background-process completion notifications)
        # are system-generated and must skip user authorization.
        is_internal = bool(getattr(event, "internal", False))

        # Fire pre_gateway_dispatch plugin hook for user-originated messages.
        # Plugins receive the MessageEvent and may return a dict influencing flow:
        #   {"action": "skip",    "reason": ...}    -> drop (no reply, plugin handled)
        #   {"action": "rewrite", "text":  ...}     -> replace event.text, continue
        #   {"action": "allow"}   /   None          -> normal dispatch
        # Hook runs BEFORE auth so plugins can handle unauthorized senders
        # (e.g. customer handover ingest) without triggering the pairing flow.
        if not is_internal:
            try:
                from hermes_cli.plugins import invoke_hook as _invoke_hook
                _hook_results = _invoke_hook(
                    "pre_gateway_dispatch",
                    event=event,
                    gateway=self,
                    session_store=self.session_store,
                )
            except Exception as _hook_exc:
                logger.warning("pre_gateway_dispatch invocation failed: %s", _hook_exc)
                _hook_results = []

            for _result in _hook_results:
                if not isinstance(_result, dict):
                    continue
                _action = _result.get("action")
                if _action == "skip":
                    logger.info(
                        "pre_gateway_dispatch skip: reason=%s platform=%s chat=%s",
                        _result.get("reason"),
                        source.platform.value if source.platform else "unknown",
                        source.chat_id or "unknown",
                    )
                    return None
                if _action == "rewrite":
                    _new_text = _result.get("text")
                    if isinstance(_new_text, str):
                        event = dataclasses.replace(event, text=_new_text)
                        source = event.source
                    break
                if _action == "allow":
                    break

        if is_internal:
            pass
        elif source.user_id is None:
            # Messages with no user identity (Telegram service messages,
            # channel forwards, anonymous admin posts, sender_chat) can't
            # be paired, but they can still be authorized via a
            # chat-scoped allowlist (e.g. TELEGRAM_GROUP_ALLOWED_CHATS
            # authorizes every member of the listed chat regardless of
            # sender). Defer to _is_user_authorized so that path runs.
            if not self._is_user_authorized(source):
                logger.debug("Ignoring message with no user_id from %s", source.platform.value)
                return None
        elif not self._is_user_authorized(source):
            logger.warning("Unauthorized user: %s (%s) on %s", source.user_id, source.user_name, source.platform.value)
            # In DMs: offer pairing code. In groups: silently ignore.
            if source.chat_type == "dm" and self._get_unauthorized_dm_behavior(source.platform) == "pair":
                platform_name = source.platform.value if source.platform else "unknown"
                # Rate-limit ALL pairing responses (code or rejection) to
                # prevent spamming the user with repeated messages when
                # multiple DMs arrive in quick succession.
                if self.pairing_store._is_rate_limited(platform_name, source.user_id):
                    return None
                code = self.pairing_store.generate_code(
                    platform_name, source.user_id, source.user_name or ""
                )
                if code:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            f"Hi~ I don't recognize you yet!\n\n"
                            f"Here's your pairing code: `{code}`\n\n"
                            f"Ask the bot owner to run:\n"
                            f"`hermes pairing approve {platform_name} {code}`"
                        )
                else:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            "Too many pairing requests right now~ "
                            "Please try again later!"
                        )
                    # Record rate limit so subsequent messages are silently ignored
                    self.pairing_store._record_rate_limit(platform_name, source.user_id)
            return None

        # Intercept messages that are responses to a pending /update prompt.
        # The update process (detached) wrote .update_prompt.json; the watcher
        # forwarded it to the user; now the user's reply goes back via
        # .update_response so the update process can continue.
        #
        # IMPORTANT: recognized slash commands must bypass this interception.
        # Otherwise control/session commands like /new or /help get silently
        # consumed as update answers instead of being dispatched normally.
        _quick_key = self._session_key_for_source(source)
        _update_prompts = getattr(self, "_update_prompt_pending", {})
        if _update_prompts.get(_quick_key):
            raw = (event.text or "").strip()
            # Accept /approve and /deny as shorthand for yes/no
            cmd = event.get_command()
            if cmd in {"approve", "yes"}:
                response_text = "y"
            elif cmd in {"deny", "no"}:
                response_text = "n"
            else:
                _recognized_cmd = None
                if cmd:
                    try:
                        from hermes_cli.commands import resolve_command as _resolve_update_cmd
                    except Exception:
                        _resolve_update_cmd = None
                    if _resolve_update_cmd is not None:
                        try:
                            _cmd_def = _resolve_update_cmd(cmd)
                            _recognized_cmd = _cmd_def.name if _cmd_def else None
                        except Exception:
                            _recognized_cmd = None
                if _recognized_cmd:
                    response_text = ""
                else:
                    response_text = raw
            if response_text:
                response_path = _hermes_home / ".update_response"
                prompt_path = _hermes_home / ".update_prompt.json"
                try:
                    tmp = response_path.with_suffix(".tmp")
                    tmp.write_text(response_text)
                    tmp.replace(response_path)
                    prompt_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("Failed to write update response: %s", e)
                    return f"✗ Failed to send response to update process: {e}"
                _update_prompts.pop(_quick_key, None)
                label = response_text if len(response_text) <= 20 else response_text[:20] + "…"
                return f"✓ Sent `{label}` to the update process."
            # Recognized slash command during a pending update prompt:
            # unblock the detached update subprocess by writing a blank
            # response so ``_gateway_prompt`` returns the prompt's default
            # (typically a safe "n" / skip) and exits cleanly instead of
            # blocking on stdin until the 30-minute watcher timeout.
            # The slash command then falls through to normal dispatch.
            if _recognized_cmd:
                response_path = _hermes_home / ".update_response"
                prompt_path = _hermes_home / ".update_prompt.json"
                try:
                    tmp = response_path.with_suffix(".tmp")
                    tmp.write_text("")
                    tmp.replace(response_path)
                    prompt_path.unlink(missing_ok=True)
                    logger.info(
                        "Recognized /%s during pending update prompt for %s; "
                        "cancelled prompt with default and dispatching command",
                        _recognized_cmd,
                        _quick_key,
                    )
                except OSError as e:
                    logger.warning(
                        "Failed to write cancel response for pending update prompt: %s",
                        e,
                    )
                _update_prompts.pop(_quick_key, None)

        # Intercept messages that are responses to a pending clarify
        # request that is awaiting free-form text (either an open-ended
        # clarify with no choices, or one where the user picked the
        # "Other" button).  The first non-empty user message in the
        # session resolves the clarify and unblocks the agent thread —
        # we do NOT route it to the agent as a new turn.
        try:
            from tools import clarify_gateway as _clarify_mod
            _pending_clarify = _clarify_mod.get_pending_for_session(_quick_key)
        except Exception:
            _pending_clarify = None
        if _pending_clarify is not None:
            _raw_clarify_reply = (event.text or "").strip()
            # Skip slash commands — the user clearly wanted to issue a
            # command, not answer the clarify.  Leave the clarify pending
            # so the user can retry; if it times out, the agent unblocks
            # with an empty response.
            if _raw_clarify_reply and not _raw_clarify_reply.startswith("/"):
                _resolved = _clarify_mod.resolve_gateway_clarify(
                    _pending_clarify.clarify_id, _raw_clarify_reply,
                )
                if _resolved:
                    logger.info(
                        "Gateway intercepted clarify text response (session=%s, id=%s)",
                        _quick_key, _pending_clarify.clarify_id,
                    )
                    # Acknowledge with empty string so adapters that emit
                    # the agent's response don't double-post.  The agent
                    # itself will produce the next user-facing message.
                    return ""

        # Intercept messages that are responses to a pending /reload-mcp
        # (or future) slash-confirm prompt.  Recognized confirm replies are
        # /approve, /always, /cancel (plus short aliases).  Anything else
        # falls through to normal dispatch — a stale pending confirm does
        # NOT block other commands.
        #
        # Important: if a dangerous-command approval is ALSO pending (agent
        # blocked inside tools/approval.py), the tool approval takes
        # precedence — /approve there unblocks the waiting tool thread.
        # Slash-confirm only catches /approve when no tool approval is live.
        from gateway.message_router import route_pending_slash_confirm_reply

        _slash_confirm_result = await route_pending_slash_confirm_reply(
            session_key=_quick_key,
            raw_reply=event.text or "",
            command=event.get_command(),
        )
        if _slash_confirm_result is not None:
            return _slash_confirm_result.response

        # PRIORITY handling when an agent is already running for this session.
        # Default behavior is to interrupt immediately so user text/stop messages
        # are handled with minimal latency.
        #
        # Special case: Telegram/photo bursts often arrive as multiple near-
        # simultaneous updates. Do NOT interrupt for photo-only follow-ups here;
        # let the adapter-level batching/queueing logic absorb them.

        # Staleness eviction: detect leaked locks from hung/crashed handlers.
        # With inactivity-based timeout, active tasks can run for hours, so
        # wall-clock age alone isn't sufficient.  Evict only when the agent
        # has been *idle* beyond the inactivity threshold (or when the agent
        # object has no activity tracker and wall-clock age is extreme).
        _raw_stale_timeout = _float_env("HERMES_AGENT_TIMEOUT", 1800)
        _stale_ts = self._running_agents_ts.get(_quick_key, 0)
        if _quick_key in self._running_agents and _stale_ts:
            _stale_age = time.time() - _stale_ts
            _stale_agent = self._running_agents.get(_quick_key)
            # Never evict the pending sentinel — it was just placed moments
            # ago during the async setup phase before the real agent is
            # created.  Sentinels have no get_activity_summary(), so the
            # idle check below would always evaluate to inf >= timeout and
            # immediately evict them, racing with the setup path.
            _stale_idle = float("inf")  # assume idle if we can't check
            _stale_detail = ""
            if _stale_agent and hasattr(_stale_agent, "get_activity_summary"):
                try:
                    _sa = _stale_agent.get_activity_summary()
                    _stale_idle = _sa.get("seconds_since_activity", float("inf"))
                    _stale_detail = (
                        f" | last_activity={_sa.get('last_activity_desc', 'unknown')} "
                        f"({_stale_idle:.0f}s ago) "
                        f"| iteration={_sa.get('api_call_count', 0)}/{_sa.get('max_iterations', 0)}"
                    )
                except Exception:
                    pass
            # Evict if: agent is idle beyond timeout, OR wall-clock age is
            # extreme (10x timeout or 2h, whichever is larger — catches
            # cases where the agent object was garbage-collected).
            _wall_ttl = max(_raw_stale_timeout * 10, 7200) if _raw_stale_timeout > 0 else float("inf")
            _should_evict = (
                _stale_agent is not _AGENT_PENDING_SENTINEL
                and (
                    (_raw_stale_timeout > 0 and _stale_idle >= _raw_stale_timeout)
                    or _stale_age > _wall_ttl
                )
            )
            if _should_evict:
                logger.warning(
                    "Evicting stale _running_agents entry for %s "
                    "(age: %.0fs, idle: %.0fs, timeout: %.0fs)%s",
                    _quick_key, _stale_age, _stale_idle,
                    _raw_stale_timeout, _stale_detail,
                )
                self._invalidate_session_run_generation(
                    _quick_key,
                    reason="stale_running_agent_eviction",
                )
                self._release_running_agent_state(_quick_key)

        if _quick_key in self._running_agents:
            if event.get_command() == "status":
                return await self._handle_status_command(event)

            # Resolve the command once for all early-intercept checks below.
            from hermes_cli.commands import (
                ACTIVE_SESSION_BYPASS_COMMANDS as _DEDICATED_HANDLERS,
                resolve_command as _resolve_cmd_inner,
            )
            from gateway.message_router import (
                ACTIVE_SESSION_ACTION_AGENTS,
                ACTIVE_SESSION_ACTION_APPROVE,
                ACTIVE_SESSION_ACTION_BACKGROUND,
                ACTIVE_SESSION_ACTION_DEDICATED,
                ACTIVE_SESSION_ACTION_DENY,
                ACTIVE_SESSION_ACTION_GOAL,
                ACTIVE_SESSION_ACTION_KANBAN,
                ACTIVE_SESSION_ACTION_NEW,
                ACTIVE_SESSION_ACTION_QUEUE,
                ACTIVE_SESSION_ACTION_RESTART,
                ACTIVE_SESSION_ACTION_STEER,
                ACTIVE_SESSION_ACTION_STOP,
                ACTIVE_SESSION_ACTION_SUBGOAL,
                ACTIVE_SESSION_ACTION_VERBOSE,
                ACTIVE_SESSION_ACTION_YOLO,
                ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE,
                ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT,
                ACTIVE_SESSION_FOLLOWUP_INTERRUPT,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO,
                ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE,
                ACTIVE_SESSION_FOLLOWUP_STEER_BUSY,
                ACTIVE_SESSION_FOLLOWUP_STOP_PENDING,
                resolve_active_session_command_decision,
                resolve_active_session_followup_decision,
            )
            _evt_cmd = event.get_command()
            _cmd_def_inner = _resolve_cmd_inner(_evt_cmd) if _evt_cmd else None
            _cmd_name_inner = _cmd_def_inner.name if _cmd_def_inner else None
            _active_cmd_decision = resolve_active_session_command_decision(
                command_name=_cmd_name_inner,
                command_args=event.get_command_args(),
                dedicated_handlers=_DEDICATED_HANDLERS,
            )

            # Slash command access control on the running-agent fast-path.
            # Mirrors the cold-path gate further below so non-admin users
            # can't bypass gating just because an agent happens to be busy.
            # /status above is intentionally pre-gate so users always see
            # session state. /help and /whoami fall under the always-allowed
            # floor inside _check_slash_access.
            if _evt_cmd and _cmd_def_inner is not None:
                _denied = self._check_slash_access(source, _cmd_def_inner.name)
                if _denied is not None:
                    return _denied

            if _active_cmd_decision.response is not None:
                return _active_cmd_decision.response

            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_RESTART:
                return await self._handle_restart_command(event)

            # /stop must hard-kill the session when an agent is running.
            # A soft interrupt (agent.interrupt()) doesn't help when the agent
            # is truly hung — the executor thread is blocked and never checks
            # _interrupt_requested.  Force-clean _running_agents so the session
            # is unlocked and subsequent messages are processed normally.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_STOP:
                await self._interrupt_and_clear_session(
                    _quick_key,
                    source,
                    interrupt_reason=_INTERRUPT_REASON_STOP,
                    invalidation_reason="stop_command",
                )
                logger.info("STOP for session %s — agent interrupted, session lock released", _quick_key)
                return EphemeralReply(t("gateway.stop.stopped"))

            # /reset and /new must bypass the running-agent guard so they
            # actually dispatch as commands instead of being queued as user
            # text (which would be fed back to the agent with the same
            # broken history — #2170).  Interrupt the agent first, then
            # clear the adapter's pending queue so the stale "/reset" text
            # doesn't get re-processed as a user message after the
            # interrupt completes.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_NEW:
                # Clear any pending messages so the old text doesn't replay
                await self._interrupt_and_clear_session(
                    _quick_key,
                    source,
                    interrupt_reason=_INTERRUPT_REASON_RESET,
                    invalidation_reason="new_command",
                )
                # Clean up the running agent entry so the reset handler
                # doesn't think an agent is still active.
                return await self._handle_reset_command(event)

            # /queue <prompt> — queue without interrupting.
            # Semantics: each /queue invocation produces its own full agent
            # turn, processed in FIFO order after the current run (and any
            # earlier /queue items) finishes.  Messages are NOT merged.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_QUEUE:
                queued_text = event.get_command_args().strip()
                if not queued_text:
                    return "Usage: /queue <prompt>"
                adapter = self.adapters.get(source.platform)
                if adapter:
                    queued_event = MessageEvent(
                        text=queued_text,
                        message_type=MessageType.TEXT,
                        source=event.source,
                        message_id=event.message_id,
                        channel_prompt=event.channel_prompt,
                    )
                    self._enqueue_fifo(_quick_key, queued_event, adapter)
                depth = self._queue_depth(_quick_key, adapter=self.adapters.get(source.platform))
                if depth <= 1:
                    return "Queued for the next turn."
                return f"Queued for the next turn. ({depth} queued)"

            # /steer <prompt> — inject mid-run after the next tool call.
            # Unlike /queue (turn boundary), /steer lands BETWEEN tool-call
            # iterations inside the same agent run, by appending to the
            # last tool result's content. No interrupt, no new user turn,
            # no role-alternation violation.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_STEER:
                steer_text = event.get_command_args().strip()
                if not steer_text:
                    return "Usage: /steer <prompt>"
                running_agent = self._running_agents.get(_quick_key)
                if running_agent is _AGENT_PENDING_SENTINEL:
                    # Agent hasn't started yet — queue as turn-boundary fallback.
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        queued_event = MessageEvent(
                            text=steer_text,
                            message_type=MessageType.TEXT,
                            source=event.source,
                            message_id=event.message_id,
                            channel_prompt=event.channel_prompt,
                        )
                        adapter._pending_messages[_quick_key] = queued_event
                    return "Agent still starting — /steer queued for the next turn."
                if running_agent and hasattr(running_agent, "steer"):
                    try:
                        accepted = running_agent.steer(steer_text)
                    except Exception as exc:
                        logger.warning("Steer failed for session %s: %s", _quick_key, exc)
                        return f"⚠️ Steer failed: {exc}"
                    if accepted:
                        preview = steer_text[:60] + ("..." if len(steer_text) > 60 else "")
                        return f"⏩ Steer queued — arrives after the next tool call: '{preview}'"
                    return "Steer rejected (empty payload)."
                # Running agent is missing or lacks steer() — fall back to queue.
                adapter = self.adapters.get(source.platform)
                if adapter:
                    queued_event = MessageEvent(
                        text=steer_text,
                        message_type=MessageType.TEXT,
                        source=event.source,
                        message_id=event.message_id,
                        channel_prompt=event.channel_prompt,
                    )
                    adapter._pending_messages[_quick_key] = queued_event
                return "No active agent — /steer queued for the next turn."

            # /approve and /deny must bypass the running-agent interrupt path.
            # The agent thread is blocked on a threading.Event inside
            # tools/approval.py — sending an interrupt won't unblock it.
            # Route directly to the approval handler so the event is signalled.
            if _active_cmd_decision.action in {ACTIVE_SESSION_ACTION_APPROVE, ACTIVE_SESSION_ACTION_DENY}:
                if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_APPROVE:
                    return await self._handle_approve_command(event)
                return await self._handle_deny_command(event)

            # /agents (/tasks alias) should be query-only and never interrupt.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_AGENTS:
                return await self._handle_agents_command(event)

            # /background must bypass the running-agent guard — it starts a
            # parallel task and must never interrupt the active conversation.
            # /btw is an alias of /background and resolves to the same canonical
            # name, so this branch handles both commands.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_BACKGROUND:
                return await self._handle_background_command(event)

            # /kanban must bypass the guard. It writes to a profile-agnostic
            # DB (kanban.db), not to the running agent's state. In fact
            # /kanban unblock is often the only way to free a worker that
            # has blocked waiting for a peer — letting that be dispatched
            # mid-run is the whole point of the board.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_KANBAN:
                return await self._handle_kanban_command(event)

            # /goal is safe mid-run for status/pause/clear (inspection and
            # control-plane only — doesn't interrupt the running turn).
            # Setting a new goal text mid-run is rejected with the same
            # "wait or /stop" message as /model so we don't race a second
            # continuation prompt against the current turn.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_GOAL:
                return await self._handle_goal_command(event)

            # /subgoal is safe mid-run — it only modifies the goal's
            # subgoals list, which the judge reads at the next turn
            # boundary. No race with the running turn.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_SUBGOAL:
                return await self._handle_subgoal_command(event)

            # Session-level toggles that are safe to run mid-agent —
            # /yolo can unblock a pending approval prompt, /verbose cycles
            # the tool-progress display mode for the ongoing stream.
            # Both modify session state without needing agent interaction
            # and must not be queued (the safety net would discard them).
            # /fast and /reasoning are config-only and take effect next
            # message, so they fall through to the catch-all busy response
            # below — users should wait and set them between turns.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_YOLO:
                return await self._handle_yolo_command(event)
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_VERBOSE:
                return await self._handle_verbose_command(event)

            # Gateway-handled info/control commands with dedicated
            # running-agent handlers.
            if _active_cmd_decision.action == ACTIVE_SESSION_ACTION_DEDICATED:
                if _cmd_def_inner.name == "help":
                    return await self._handle_help_command(event)
                if _cmd_def_inner.name == "commands":
                    return await self._handle_commands_command(event)
                if _cmd_def_inner.name == "profile":
                    return await self._handle_profile_command(event)
                if _cmd_def_inner.name == "update":
                    return await self._handle_update_command(event)

            # Any other recognized slash command already returned the
            # NOTE: The new MessageRouter architecture provides an alternative path for
        # command dispatch. As we migrate, cold commands can be routed through:
        #   result = await self._message_router.handle_message(event_dict)
        # This decouples GatewayRunner from specific command implementations.
        # See gateway/message_router.py for the new handler implementations.
        # MessageRouter catch-all response above. Plain text and media
            # follow-ups continue into the active-session queue/interrupt path.
            _telegram_followup_grace = float(
                os.getenv("HERMES_TELEGRAM_FOLLOWUP_GRACE_SECONDS", "3.0")
            )
            _started_at = self._running_agents_ts.get(_quick_key, 0)
            _now = time.time()
            running_agent = self._running_agents.get(_quick_key)
            _queue_during_drain = (
                self._queue_during_drain_enabled() if self._draining else False
            )
            _followup_decision = resolve_active_session_followup_decision(
                platform=source.platform,
                message_type=event.message_type,
                command=event.get_command(),
                started_at=_started_at,
                now=_now,
                telegram_followup_grace_seconds=_telegram_followup_grace,
                running_agent_is_pending=running_agent is _AGENT_PENDING_SENTINEL,
                draining=self._draining,
                queue_during_drain=_queue_during_drain,
                busy_input_mode=self._busy_input_mode,
            )

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_PHOTO:
                logger.debug("PRIORITY photo follow-up for session %s — queueing without interrupt", _quick_key)
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(adapter._pending_messages, _quick_key, event)
                return None

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_TELEGRAM_GRACE:
                logger.debug(
                    "Telegram follow-up arrived %.2fs after run start for %s — queueing without interrupt",
                    _now - _started_at,
                    _quick_key,
                )
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(
                        adapter._pending_messages,
                        _quick_key,
                        event,
                        merge_text=True,
                    )
                return None

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_STOP_PENDING:
                self._release_running_agent_state(_quick_key)
                logger.info("HARD STOP (pending) for session %s — sentinel cleared", _quick_key)
                return EphemeralReply("⚡ Force-stopped. The agent was still starting — session unlocked.")

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_PENDING:
                # Agent is being set up but not ready yet.
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(
                        adapter._pending_messages,
                        _quick_key,
                        event,
                        merge_text=True,
                    )
                return None

            if _followup_decision.action in {
                ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE,
                ACTIVE_SESSION_FOLLOWUP_DRAIN_REJECT,
            }:
                if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE:
                    self._queue_or_replace_pending_event(_quick_key, event)
                return (
                    f"⏳ Gateway {self._status_action_gerund()} — queued for the next turn after it comes back."
                    if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_DRAIN_QUEUE
                    else f"⏳ Gateway is {self._status_action_gerund()} and is not accepting another turn right now."
                )

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_QUEUE_BUSY:
                logger.debug("PRIORITY queue follow-up for session %s", _quick_key)
                self._queue_or_replace_pending_event(_quick_key, event)
                return None

            if _followup_decision.action == ACTIVE_SESSION_FOLLOWUP_STEER_BUSY:
                # Steer mode: inject text into the running agent mid-run via
                # agent.steer().  Falls back to queue semantics if the payload
                # is empty, the agent lacks steer(), or steer() rejects.
                steer_text = (event.text or "").strip()
                steered = False
                if steer_text and hasattr(running_agent, "steer"):
                    try:
                        steered = bool(running_agent.steer(steer_text))
                    except Exception as exc:
                        logger.warning("PRIORITY steer failed for session %s: %s", _quick_key, exc)
                        steered = False
                if steered:
                    logger.debug("PRIORITY steer for session %s", _quick_key)
                    return None
                logger.debug("PRIORITY steer-fallback-to-queue for session %s", _quick_key)
                self._queue_or_replace_pending_event(_quick_key, event)
                return None

            if _followup_decision.action != ACTIVE_SESSION_FOLLOWUP_INTERRUPT:
                logger.debug(
                    "Unknown active-session follow-up action %s for %s; interrupting",
                    _followup_decision.action,
                    _quick_key,
                )
            logger.debug("PRIORITY interrupt for session %s", _quick_key)
            running_agent.interrupt(event.text)
            # NOTE: self._pending_messages was write-only (never consumed).
            # The actual interrupt message is delivered via adapter._pending_messages
            # which is read by _run_agent. Removed to prevent unbounded growth.
            return None

        # Check for commands
        command = event.get_command()

        from hermes_cli.commands import (
            CommandSurface,
            is_gateway_known_command,
            resolve_command_invocation,
            resolve_plugin_command_dispatch,
        )
        from gateway.cold_command_router import (
            COMMAND_HOOK_DENY,
            COMMAND_HOOK_HANDLED,
            COMMAND_HOOK_REWRITE,
            build_bundle_invocation,
            build_skill_invocation_decision,
            execute_plugin_command,
            execute_quick_command,
            resolve_builtin_precedence_quick_alias,
            resolve_cold_command_dispatch,
            resolve_command_hook_decision,
            should_return_unknown_slash_command,
            unavailable_gateway_command_response,
            unknown_slash_command_response,
        )

        # Resolve aliases to canonical handler keys through the shared seam so
        # dispatch, hooks, and access checks do not depend on the typed alias.
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

        # Expand alias quick commands before built-in dispatch so targets like
        # /model openai/gpt-5.5 --provider openrouter reach the /model handler.
        # Preserve built-in precedence; aliases only need early handling when
        # the typed command is not already known.
        _alias_rewrite = resolve_builtin_precedence_quick_alias(
            config=self.config,
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

        # Per-platform slash command access control. Only kicks in when the
        # operator has set ``allow_admin_from`` for the source's scope (DM
        # vs group). When unset → backward-compat: every allowed user can
        # run every command. When set → non-admins can run only commands in
        # ``user_allowed_commands`` (plus the always-allowed floor: /help,
        # /whoami). Plain chat is unaffected — only slash commands gate.
        if command:
            _hook_dispatch = resolve_plugin_command_dispatch(
                name=command,
                args=event.get_command_args().strip(),
                surface=CommandSurface.GATEWAY,
            )
            if _hook_dispatch.route == "plugin":
                canonical = _hook_dispatch.handler_key

        if command and canonical and is_gateway_known_command(canonical):
            _denied = self._check_slash_access(source, canonical)
            if _denied is not None:
                return _denied

        # Fire the ``command:<canonical>`` hook for any recognized slash
        # command — built-in OR plugin-registered. Handlers can return a
        # dict with ``{"decision": "deny" | "handled" | "rewrite", ...}``
        # to intercept dispatch before core handling runs. This replaces
        # the previous fire-and-forget emit(): return values are now
        # honored, but handlers that return nothing behave exactly as
        # before (telemetry-style hooks keep working).
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
                hook_results = await self.hooks.emit_collect(
                    f"command:{canonical}", hook_ctx
                )
            except Exception as _hook_err:
                logger.debug(
                    "command:%s hook dispatch failed (non-fatal): %s",
                    canonical, _hook_err,
                )
                hook_results = []

            hook_decision = resolve_command_hook_decision(
                command=command,
                hook_results=hook_results,
            )
            if hook_decision.action == COMMAND_HOOK_DENY:
                return hook_decision.response
            if hook_decision.action == COMMAND_HOOK_HANDLED:
                return hook_decision.response
            if hook_decision.action == COMMAND_HOOK_REWRITE:
                event.text = f"/{hook_decision.command_name} {hook_decision.raw_args}".strip()
                command = event.get_command()
                if command:
                    _cmd_invocation = (
                        resolve_command_invocation(
                            name=command,
                            args=hook_decision.raw_args,
                            surface=CommandSurface.GATEWAY,
                        )
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

        from gateway.command_registry import (
            get_gateway_command_handler,
            resolve_special_cold_command,
        )

        _special_telegram_root_lobby = (
            self._is_telegram_topic_root_lobby(source) if canonical == "new" else False
        )
        _topic_new_uses_reset_handler = (
            canonical == "new"
            and not _special_telegram_root_lobby
            and getattr(source, "platform", None) == Platform.TELEGRAM
            and bool(getattr(source, "thread_id", None))
        )
        if _topic_new_uses_reset_handler:
            return await self._handle_reset_command(event)
        _reset_override = vars(self).get("_handle_reset_command")
        if canonical == "new" and callable(_reset_override):
            return await _reset_override(event)
        else:
            special_command_decision = resolve_special_cold_command(
                canonical,
                command_args=event.get_command_args().strip(),
                telegram_root_lobby=_special_telegram_root_lobby,
                telegram_root_new_message=(
                    self._telegram_topic_root_new_message()
                    if _special_telegram_root_lobby
                    else ""
                ),
            )
        if special_command_decision is not None:
            if special_command_decision.response is not None:
                return special_command_decision.response
            if special_command_decision.rewrite_text is not None:
                try:
                    event.text = special_command_decision.rewrite_text
                except Exception:
                    pass
            if special_command_decision.confirm_command is not None:
                async def _do_special_command():
                    if special_command_decision.confirm_command == "new":
                        return await self._handle_reset_command(event)
                    if special_command_decision.confirm_command == "undo":
                        return await self._handle_undo_command(event)
                    return None

                return await self._maybe_confirm_destructive_slash(
                    event=event,
                    command=special_command_decision.confirm_command,
                    title=special_command_decision.confirm_title or "",
                    detail=special_command_decision.confirm_detail or "",
                    execute=_do_special_command,
                )

        gateway_handler = get_gateway_command_handler(
            self._gateway_commands.handler_map(),
            canonical,
        )
        if gateway_handler is not None:
            return await gateway_handler(event)

        if self._draining:
            return f"⏳ Gateway is {self._status_action_gerund()} and is not accepting new work right now."

        _cold_dispatch = resolve_cold_command_dispatch(
            config=self.config,
            command=command,
            command_args=event.get_command_args().strip(),
        )
        quick_commands = _cold_dispatch.quick_commands if _cold_dispatch else {}
        skill_cmds = _cold_dispatch.skill_commands if _cold_dispatch else {}
        command_dispatch = _cold_dispatch.command_dispatch if _cold_dispatch else None

        if command:
            if command_dispatch and command_dispatch.route == "quick_exec":
                qcmd = quick_commands.get(command_dispatch.handler_key, {})
                return await execute_quick_command(
                    command_name=command,
                    exec_cmd=qcmd.get("command", ""),
                    env=os.environ.copy(),
                )
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
                    return f"Quick command '/{command}' has no target defined."
            elif command_dispatch and command_dispatch.route == "quick_unsupported":
                return (
                    f"Quick command '/{command}' has unsupported type "
                    "(supported: 'exec', 'alias')."
                )
            elif command_dispatch and command_dispatch.route == "unavailable":
                return unavailable_gateway_command_response(
                    command_dispatch.invocation.canonical_name
                )

        # Plugin-registered slash commands
        if command and command_dispatch and command_dispatch.route == "plugin":
            try:
                result = await execute_plugin_command(
                    handler_key=command_dispatch.handler_key,
                    raw_args=event.get_command_args().strip(),
                )
                return result
            except Exception as e:
                logger.debug("Plugin command dispatch failed (non-fatal): %s", e)

        # Skill slash commands: /skill-name loads the skill and sends to agent.
        # resolve_skill_command_key() handles the Telegram underscore/hyphen
        # round-trip so /claude_code from Telegram autocomplete still resolves
        # to the claude-code skill.
        if command and command_dispatch and command_dispatch.route == "skill_bundle":
            # Skill bundles take precedence over individual skill commands —
            # /<bundle> loads multiple skills at once. Mirrors CLI dispatch.
            _bundle_handled = False
            try:
                bundle_result = build_bundle_invocation(
                    bundle_key=command_dispatch.handler_slash_key,
                    user_instruction=event.get_command_args().strip(),
                    task_id=_quick_key,
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
                    # Fall through to normal message processing with bundle
                    # content.
            except Exception as exc:
                logger.debug("Bundle dispatch failed (non-fatal): %s", exc)

        if (
            command
            and command_dispatch
            and command_dispatch.route in {"skill", "unknown"}
            and not locals().get("_bundle_handled", False)
        ):
            try:
                skill_decision = build_skill_invocation_decision(
                    command_dispatch=command_dispatch,
                    command=command,
                    skill_commands=skill_cmds,
                    platform_value=source.platform.value if source.platform else None,
                    user_instruction=event.get_command_args().strip(),
                    task_id=_quick_key,
                    unavailable_skill_checker=_check_unavailable_skill,
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
                    return skill_decision.response
                if skill_decision.message:
                    event.text = skill_decision.message
                    # Fall through to normal message processing with skill content
            except Exception as e:
                logger.debug("Skill command check failed (non-fatal): %s", e)

        # Pending exec approvals are handled by /approve and /deny commands above.
        # No bare text matching — "yes" in normal conversation must not trigger
        # execution of a dangerous command.

        if self._is_telegram_topic_root_lobby(source):
            # Debounce the lobby reminder so a user who forgets about
            # topic mode and fires ten prompts doesn't get ten copies.
            if self._should_send_telegram_lobby_reminder(source):
                return self._telegram_topic_root_lobby_message()
            return None

        # ── Claim this session before any await ───────────────────────
        # Between here and _run_agent registering the real AIAgent, there
        # are numerous await points (hooks, vision enrichment, STT,
        # session hygiene compression).  Without this sentinel a second
        # message arriving during any of those yields would pass the
        # "already running" guard and spin up a duplicate agent for the
        # same session — corrupting the transcript.
        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL
        self._running_agents_ts[_quick_key] = time.time()
        _run_generation = self._begin_session_run_generation(_quick_key)

        try:
            _agent_result = await self._handle_message_with_agent(event, source, _quick_key, _run_generation)
            # Goal continuation: after the agent returns a final response
            # for this turn, check any standing /goal — the judge will
            # either mark it done, pause it (budget), or enqueue a
            # continuation prompt back through the adapter FIFO so the
            # next turn makes more progress. Wrapped in try/except so a
            # broken judge never breaks normal message handling.
            try:
                _final_text = ""
                if isinstance(_agent_result, dict):
                    _final_text = str(_agent_result.get("final_response") or "")
                elif isinstance(_agent_result, str):
                    _final_text = _agent_result
                # Skip for empty responses (interrupted / errored) — the
                # judge would almost always say "continue" and we'd loop
                # on error. Let the user drive the next turn.
                if _final_text.strip():
                    try:
                        session_entry = self.session_store.get_or_create_session(source)
                    except Exception:
                        session_entry = None
                    if session_entry is not None:
                        await self._post_turn_goal_continuation(
                            session_entry=session_entry,
                            source=source,
                            final_response=_final_text,
                        )
            except Exception as _goal_exc:
                logger.debug("goal continuation hook failed: %s", _goal_exc)
            return _agent_result
        finally:
            # If _run_agent replaced the sentinel with a real agent and
            # then cleaned it up, this is a no-op.  If we exited early
            # (exception, command fallthrough, etc.) the sentinel must
            # not linger or the session would be permanently locked out.
            if self._running_agents.get(_quick_key) is _AGENT_PENDING_SENTINEL:
                self._release_running_agent_state(_quick_key)
            else:
                # Agent path already cleaned _running_agents; make sure
                # the paired metadata dicts are gone too.
                self._running_agents_ts.pop(_quick_key, None)
                if hasattr(self, "_busy_ack_ts"):
                    self._busy_ack_ts.pop(_quick_key, None)

    async def _prepare_inbound_message_text(
        self,
        *,
        event: MessageEvent,
        source: SessionSource,
        history: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Prepare inbound event text for the agent."""
        return await prepare_inbound_message_text(
            event=event,
            source=source,
            history=history,
            config=self.config,
            session_key_for_source=self._session_key_for_source,
            consume_pending_native_image_paths=self._consume_pending_native_image_paths,
            decide_image_input_mode=self._decide_image_input_mode,
            enrich_message_with_vision=self._enrich_message_with_vision,
            enrich_message_with_transcription=self._enrich_message_with_transcription,
            adapters=self.adapters,
            thread_metadata_for_source=self._thread_metadata_for_source,
            reply_anchor_for_event=self._reply_anchor_for_event,
            has_setup_skill=self._has_setup_skill,
            model=getattr(self, "_model", ""),
            base_url=getattr(self, "_base_url", ""),
            resolve_runtime_agent_kwargs=_resolve_runtime_agent_kwargs,
            load_gateway_config=_load_gateway_config,
            pending_native_image_paths_by_session=getattr(
                self,
                "_pending_native_image_paths_by_session",
                None,
            ),
            set_pending_native_image_paths=lambda pending: setattr(
                self,
                "_pending_native_image_paths_by_session",
                pending,
            ),
            logger_=logger,
        )

    def _consume_pending_native_image_paths(self, session_key: str) -> List[str]:
        pending_native = getattr(self, "_pending_native_image_paths_by_session", None)
        if not pending_native:
            return []
        return list(pending_native.pop(session_key, []) or [])

    def _cache_session_source(self, session_key: str, source) -> None:
        cached_sources = cache_session_source(
            getattr(self, "_session_sources", None),
            session_key,
            source,
            max_size=getattr(self, "_session_sources_max", 512),
            logger=logger,
        )
        if cached_sources is not None:
            self._session_sources = cached_sources

    def _get_cached_session_source(self, session_key: str):
        return get_cached_session_source(getattr(self, "_session_sources", None), session_key)

    async def _handle_message_with_agent(self, event, source, _quick_key: str, run_generation: int):
        """Inner handler that runs under the _running_agents sentinel guard."""
        _msg_start_time = time.time()
        _platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
        _msg_preview = (event.text or "")[:80].replace("\n", " ")
        logger.info(
            "inbound message: platform=%s user=%s chat=%s msg=%r",
            _platform_name, source.user_name or source.user_id or "unknown",
            source.chat_id or "unknown", _msg_preview,
        )

        # Get or create session
        # Topic-mode DMs: rewrite a stale/foreign thread_id to the user's
        # last-active topic so a cross-topic Reply or stripped plain reply
        # doesn't fragment the conversation across sessions.
        recovered = self._recover_telegram_topic_thread_id(source)
        if recovered is not None:
            logger.info(
                "telegram topic recovery: chat=%s user=%s %r -> %s",
                source.chat_id, source.user_id, source.thread_id, recovered,
            )
            source = dataclasses.replace(source, thread_id=recovered)
            try:
                event.source = source
            except Exception:
                pass

        session_entry = self.session_store.get_or_create_session(source)
        session_key = session_entry.session_key
        self._cache_session_source(session_key, source)
        if self._is_telegram_topic_lane(source):
            try:
                binding = self._session_db.get_telegram_topic_binding(
                    chat_id=str(source.chat_id),
                    thread_id=str(source.thread_id),
                ) if self._session_db else None
            except Exception:
                logger.debug("Failed to read Telegram topic binding", exc_info=True)
                binding = None
            if binding:
                bound_session_id = str(binding.get("session_id") or "")
                if bound_session_id and bound_session_id != session_entry.session_id:
                    # Route the override through session_lifecycle + SessionStore:
                    # lifecycle owns SQLite invariants, and SessionStore persists
                    # the session_key → session_id mapping. Mutating session_entry
                    # in place here created a split-brain state where the JSON
                    # index pointed at one id but code downstream used another.
                    try:
                        from session_lifecycle import activate_session
                        activate_session(
                            self._session_db,
                            bound_session_id,
                            current_session_id=session_entry.session_id,
                            end_current_reason="session_switch",
                        )
                    except Exception:
                        logger.debug("Failed to activate Telegram topic session", exc_info=True)
                    else:
                        switched = self.session_store.switch_session(session_key, bound_session_id)
                        if switched is not None:
                            session_entry = switched
            else:
                try:
                    self._record_telegram_topic_binding(source, session_entry)
                except Exception:
                    logger.debug("Failed to record Telegram topic binding", exc_info=True)
        if getattr(session_entry, "was_auto_reset", False):
            # Treat auto-reset as a full conversation boundary — drop every
            # session-scoped transient state so the fresh session does not
            # inherit the previous conversation's model/reasoning overrides
            # or a queued "/model switched" note.
            self._session_model_overrides.pop(session_key, None)
            self._set_session_reasoning_override(session_key, None)
            if hasattr(self, "_pending_model_notes"):
                self._pending_model_notes.pop(session_key, None)

        # Emit session:start for new or auto-reset sessions
        _is_new_session = (
            session_entry.created_at == session_entry.updated_at
            or getattr(session_entry, "was_auto_reset", False)
            or getattr(session_entry, "is_fresh_reset", False)
        )
        # Consume the is_fresh_reset flag immediately so it doesn't leak
        # onto subsequent messages in the same session (issue #6508).
        if getattr(session_entry, "is_fresh_reset", False):
            session_entry.is_fresh_reset = False
        if _is_new_session:
            await self.hooks.emit("session:start", {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "session_id": session_entry.session_id,
                "session_key": session_key,
            })

        # Build session context
        context = build_session_context(source, self.config, session_entry)

        # Set session context variables for tools (task-local, concurrency-safe)
        _session_env_tokens = self._set_session_env(context)

        # Read privacy.redact_pii from config (re-read per message)
        _redact_pii = False
        try:
            _pcfg = _load_gateway_config()
            _redact_pii = bool((_pcfg.get("privacy") or {}).get("redact_pii", False))
        except Exception:
            pass

        # Build the context prompt to inject
        context_prompt = build_session_context_prompt(context, redact_pii=_redact_pii)

        # If the previous session expired and was auto-reset, prepend a notice
        # so the agent knows this is a fresh conversation (not an intentional /reset).
        if getattr(session_entry, 'was_auto_reset', False):
            reset_reason = getattr(session_entry, 'auto_reset_reason', None) or 'idle'
            if reset_reason == "suspended":
                context_note = "[System note: The user's previous session was stopped and suspended. This is a fresh conversation with no prior context.]"
            elif reset_reason == "daily":
                context_note = "[System note: The user's session was automatically reset by the daily schedule. This is a fresh conversation with no prior context.]"
            else:
                context_note = "[System note: The user's previous session expired due to inactivity. This is a fresh conversation with no prior context.]"
            context_prompt = context_note + "\n\n" + context_prompt

            # Send a user-facing notification explaining the reset, unless:
            # - notifications are disabled in config
            # - the platform is excluded (e.g. api_server, webhook)
            # - the expired session had no activity (nothing was cleared)
            try:
                policy = self.session_store.config.get_reset_policy(
                    platform=source.platform,
                    session_type=getattr(source, 'chat_type', 'dm'),
                )
                platform_name = source.platform.value if source.platform else ""
                had_activity = getattr(session_entry, 'reset_had_activity', False)
                # Suspended sessions always notify (they were explicitly stopped
                # or crashed mid-operation) — skip the policy check.
                should_notify = reset_reason == "suspended" or (
                    policy.notify
                    and had_activity
                    and platform_name not in policy.notify_exclude_platforms
                )
                if should_notify:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        if reset_reason == "suspended":
                            reason_text = "previous session was stopped or interrupted"
                        elif reset_reason == "daily":
                            reason_text = f"daily schedule at {policy.at_hour}:00"
                        else:
                            hours = policy.idle_minutes // 60
                            mins = policy.idle_minutes % 60
                            duration = f"{hours}h" if not mins else f"{hours}h {mins}m" if hours else f"{mins}m"
                            reason_text = f"inactive for {duration}"
                        notice = (
                            f"◐ Session automatically reset ({reason_text}). "
                            f"Conversation history cleared.\n"
                            f"Use /resume to browse and restore a previous session.\n"
                            f"Adjust reset timing in config.yaml under session_reset."
                        )
                        try:
                            session_info = self._format_session_info()
                            if session_info:
                                notice = f"{notice}\n\n{session_info}"
                        except Exception:
                            pass
                        await adapter.send(
                            source.chat_id, notice,
                            metadata=self._thread_metadata_for_source(source),
                        )
            except Exception as e:
                logger.debug("Auto-reset notification failed (non-fatal): %s", e)

            session_entry.was_auto_reset = False
            session_entry.auto_reset_reason = None

        # Auto-load skill(s) for topic/channel bindings (Telegram DM Topics,
        # Discord channel_skill_bindings).  Supports a single name or ordered list.
        # Only inject on NEW sessions — ongoing conversations already have the
        # skill content in their conversation history from the first message.
        _auto = getattr(event, "auto_skill", None)
        if _is_new_session and _auto:
            _skill_names = [_auto] if isinstance(_auto, str) else list(_auto)
            try:
                from agent.skill_commands import _load_skill_payload, _build_skill_message
                _combined_parts: list[str] = []
                _loaded_names: list[str] = []
                for _sname in _skill_names:
                    _loaded = _load_skill_payload(_sname, task_id=_quick_key)
                    if _loaded:
                        _loaded_skill, _skill_dir, _display_name = _loaded
                        _note = (
                            f'[IMPORTANT: The "{_display_name}" skill is auto-loaded. '
                            f"Follow its instructions for this session.]"
                        )
                        _part = _build_skill_message(_loaded_skill, _skill_dir, _note)
                        if _part:
                            _combined_parts.append(_part)
                            _loaded_names.append(_sname)
                    else:
                        logger.warning("[Gateway] Auto-skill '%s' not found", _sname)
                if _combined_parts:
                    # Append the user's original text after all skill payloads
                    _combined_parts.append(event.text)
                    event.text = "\n\n".join(_combined_parts)
                    logger.info(
                        "[Gateway] Auto-loaded skill(s) %s for session %s",
                        _loaded_names, session_key,
                    )
            except Exception as e:
                logger.warning("[Gateway] Failed to auto-load skill(s) %s: %s", _skill_names, e)

        # Load conversation history from transcript
        history = self.session_store.load_transcript(session_entry.session_id)

        # -----------------------------------------------------------------
        # Session hygiene: auto-compress pathologically large transcripts
        #
        # Long-lived gateway sessions can accumulate enough history that
        # every new message rehydrates an oversized transcript, causing
        # repeated truncation/context failures.  Detect this early and
        # compress proactively — before the agent even starts.  (#628)
        #
        # Token source priority:
        # 1. Actual API-reported prompt_tokens from the last turn
        #    (stored in session_entry.last_prompt_tokens)
        # 2. Rough char-based estimate (str(msg)//4). Overestimates
        #    by 30-50% on code/JSON-heavy sessions, but that just
        #    means hygiene fires a bit early — safe and harmless.
        # -----------------------------------------------------------------
        if history and len(history) >= 4:
            from agent.model_metadata import (
                estimate_messages_tokens_rough,
                get_model_context_length,
            )

            # Read model + compression config from config.yaml.
            # NOTE: hygiene threshold is intentionally HIGHER than the agent's
            # own compressor (0.85 vs 0.50).  Hygiene is a safety net for
            # sessions that grew too large between turns — it fires pre-agent
            # to prevent API failures.  The agent's own compressor handles
            # normal context management during its tool loop with accurate
            # real token counts.  Having hygiene at 0.50 caused premature
            # compression on every turn in long gateway sessions.
            _hyg_model = "anthropic/claude-sonnet-4.6"
            _hyg_threshold_pct = 0.85
            _hyg_compression_enabled = True
            _hyg_hard_msg_limit = 400
            _hyg_config_context_length = None
            _hyg_provider = None
            _hyg_base_url = None
            _hyg_api_key = None
            _hyg_data = {}
            try:
                _hyg_data = _load_gateway_config()
                if _hyg_data:
                    # Resolve model name (same logic as run_sync)
                    _model_cfg = _hyg_data.get("model", {})
                    if isinstance(_model_cfg, str):
                        _hyg_model = _model_cfg
                    elif isinstance(_model_cfg, dict):
                        _hyg_model = _model_cfg.get("default") or _model_cfg.get("model") or _hyg_model
                        # Read explicit context_length override from model config
                        # (same as run_agent.py lines 995-1005)
                        _raw_ctx = _model_cfg.get("context_length")
                        if _raw_ctx is not None:
                            try:
                                _hyg_config_context_length = int(_raw_ctx)
                            except (TypeError, ValueError):
                                pass
                        # Read provider for accurate context detection
                        _hyg_provider = _model_cfg.get("provider") or None
                        _hyg_base_url = _model_cfg.get("base_url") or None

                    # Read compression settings — only use enabled flag.
                    # The threshold is intentionally separate from the agent's
                    # compression.threshold (hygiene runs higher).
                    _comp_cfg = _hyg_data.get("compression", {})
                    if isinstance(_comp_cfg, dict):
                        _hyg_compression_enabled = str(
                            _comp_cfg.get("enabled", True)
                        ).lower() in {"true", "1", "yes"}
                        _raw_hard_limit = _comp_cfg.get("hygiene_hard_message_limit")
                        if _raw_hard_limit is not None:
                            try:
                                _parsed = int(_raw_hard_limit)
                                if _parsed > 0:
                                    _hyg_hard_msg_limit = _parsed
                            except (TypeError, ValueError):
                                pass

                try:
                    _hyg_model, _hyg_runtime = self._resolve_session_agent_runtime(
                        source=source,
                        session_key=session_key,
                        user_config=_hyg_data if isinstance(_hyg_data, dict) else None,
                    )
                    _hyg_provider = _hyg_runtime.get("provider") or _hyg_provider
                    _hyg_base_url = _hyg_runtime.get("base_url") or _hyg_base_url
                    _hyg_api_key = _hyg_runtime.get("api_key") or _hyg_api_key
                except Exception:
                    pass

                # Check custom_providers per-model context_length
                # (same fallback as run_agent.py lines 1171-1189).
                # Must run after runtime resolution so _hyg_base_url is set.
                if _hyg_config_context_length is None and _hyg_base_url:
                    try:
                        try:
                            from hermes_cli.config import get_compatible_custom_providers as _gw_gcp
                            _hyg_custom_providers = _gw_gcp(_hyg_data)
                        except Exception:
                            _hyg_custom_providers = _hyg_data.get("custom_providers")
                            if not isinstance(_hyg_custom_providers, list):
                                _hyg_custom_providers = []
                        for _cp in _hyg_custom_providers:
                            if not isinstance(_cp, dict):
                                continue
                            _cp_url = (_cp.get("base_url") or "").rstrip("/")
                            if _cp_url and _cp_url == _hyg_base_url.rstrip("/"):
                                _cp_models = _cp.get("models", {})
                                if isinstance(_cp_models, dict):
                                    _cp_model_cfg = _cp_models.get(_hyg_model, {})
                                    if isinstance(_cp_model_cfg, dict):
                                        _cp_ctx = _cp_model_cfg.get("context_length")
                                        if _cp_ctx is not None:
                                            _hyg_config_context_length = int(_cp_ctx)
                                break
                    except (TypeError, ValueError):
                        pass
            except Exception:
                pass

            if _hyg_compression_enabled:
                _hyg_context_length = get_model_context_length(
                    _hyg_model,
                    base_url=_hyg_base_url or "",
                    api_key=_hyg_api_key or "",
                    config_context_length=_hyg_config_context_length,
                    provider=_hyg_provider or "",
                )
                _compress_token_threshold = int(
                    _hyg_context_length * _hyg_threshold_pct
                )
                _warn_token_threshold = int(_hyg_context_length * 0.95)

                _msg_count = len(history)

                # Prefer actual API-reported tokens from the last turn
                # (stored in session entry) over the rough char-based estimate.
                _stored_tokens = session_entry.last_prompt_tokens
                if _stored_tokens > 0:
                    _approx_tokens = _stored_tokens
                    _token_source = "actual"
                else:
                    _approx_tokens = estimate_messages_tokens_rough(history)
                    _token_source = "estimated"
                    # Note: rough estimates overestimate by 30-50% for code/JSON-heavy
                    # sessions, but that just means hygiene fires a bit early — which
                    # is safe and harmless.  The 85% threshold already provides ample
                    # headroom (agent's own compressor runs at 50%).  A previous 1.4x
                    # multiplier tried to compensate by inflating the threshold, but
                    # 85% * 1.4 = 119% of context — which exceeds the model's limit
                    # and prevented hygiene from ever firing for ~200K models (GLM-5).

                # Hard safety valve: force compression if message count is
                # extreme, regardless of token estimates.  This breaks the
                # death spiral where API disconnects prevent token data
                # collection, which prevents compression, which causes more
                # disconnects.  400 messages is well above normal sessions
                # but catches runaway growth before it becomes unrecoverable.
                # Threshold is configurable via
                # compression.hygiene_hard_message_limit.
                # (#2153)
                _HARD_MSG_LIMIT = _hyg_hard_msg_limit
                _needs_compress = (
                    _approx_tokens >= _compress_token_threshold
                    or _msg_count >= _HARD_MSG_LIMIT
                )

                if _needs_compress:
                    logger.info(
                        "Session hygiene: %s messages, ~%s tokens (%s) — auto-compressing "
                        "(threshold: %s%% of %s = %s tokens)",
                        _msg_count, f"{_approx_tokens:,}", _token_source,
                        int(_hyg_threshold_pct * 100),
                        f"{_hyg_context_length:,}",
                        f"{_compress_token_threshold:,}",
                    )

                    _hyg_meta = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))

                    try:
                        from run_agent import AIAgent

                        _hyg_model, _hyg_runtime = self._resolve_session_agent_runtime(
                            source=source,
                            session_key=session_key,
                            user_config=_hyg_data if isinstance(_hyg_data, dict) else None,
                        )
                        if _hyg_runtime.get("api_key"):
                            _hyg_msgs = [
                                {"role": m.get("role"), "content": m.get("content")}
                                for m in history
                                if m.get("role") in {"user", "assistant"}
                                and m.get("content")
                            ]

                            if len(_hyg_msgs) >= 4:
                                _hyg_agent = AIAgent(
                                    **_hyg_runtime,
                                    model=_hyg_model,
                                    max_iterations=4,
                                    quiet_mode=True,
                                    skip_memory=True,
                                    enabled_toolsets=["memory"],
                                    session_id=session_entry.session_id,
                                )
                                try:
                                    _hyg_agent._print_fn = lambda *a, **kw: None

                                    loop = asyncio.get_running_loop()
                                    _compressed, _ = await loop.run_in_executor(
                                        None,
                                        lambda: _hyg_agent._compress_context(
                                            _hyg_msgs, "",
                                            approx_tokens=_approx_tokens,
                                        ),
                                    )

                                    # _compress_context ends the old session and creates
                                    # a new session_id.  Write compressed messages into
                                    # the NEW session so the old transcript stays intact
                                    # and searchable via session_search.
                                    _hyg_new_sid = _hyg_agent.session_id
                                    if _hyg_new_sid != session_entry.session_id:
                                        session_entry.session_id = _hyg_new_sid
                                        self.session_store._save()

                                    self.session_store.rewrite_transcript(
                                        session_entry.session_id, _compressed
                                    )
                                    # Reset stored token count — transcript was rewritten
                                    session_entry.last_prompt_tokens = 0
                                    history = _compressed
                                    _new_count = len(_compressed)
                                    _new_tokens = estimate_messages_tokens_rough(
                                        _compressed
                                    )

                                    logger.info(
                                        "Session hygiene: compressed %s → %s msgs, "
                                        "~%s → ~%s tokens",
                                        _msg_count, _new_count,
                                        f"{_approx_tokens:,}", f"{_new_tokens:,}",
                                    )

                                    if _new_tokens >= _warn_token_threshold:
                                        logger.warning(
                                            "Session hygiene: still ~%s tokens after "
                                            "compression",
                                            f"{_new_tokens:,}",
                                        )

                                    # If summary generation failed, the
                                    # compressor aborts entirely and returns
                                    # messages unchanged — nothing is dropped.
                                    # Surface a visible warning to the gateway
                                    # user — agent.log alone is invisible on
                                    # TG/Discord/etc. — so they know the chat
                                    # is "frozen" at the current size and can
                                    # /compress to retry or /reset to start
                                    # fresh.
                                    _comp = getattr(_hyg_agent, "context_compressor", None)
                                    if _comp is not None and getattr(_comp, "_last_compress_aborted", False):
                                        _err = getattr(_comp, "_last_summary_error", None) or "unknown error"
                                        _warn_msg = (
                                            "⚠️ Context compression aborted "
                                            f"({_err}). No messages were dropped — "
                                            "conversation is unchanged. Run /compress "
                                            "to retry, /reset for a clean session, or "
                                            "check your auxiliary.compression model "
                                            "configuration."
                                        )
                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            if _adapter and source.chat_id:
                                                await _adapter.send(source.chat_id, _warn_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
                                            logger.warning(
                                                "Failed to deliver compression-failure warning to user: %s",
                                                _werr,
                                            )
                                    # Separately: if the user's CONFIGURED aux
                                    # model failed and we recovered by falling
                                    # back to the main model, tell them — a
                                    # misconfigured auxiliary.compression.model
                                    # is something only they can fix, and
                                    # silent recovery would hide it.
                                    elif _comp is not None and getattr(_comp, "_last_aux_model_failure_model", None):
                                        _aux_model = getattr(_comp, "_last_aux_model_failure_model", "")
                                        _aux_err = getattr(_comp, "_last_aux_model_failure_error", None) or "unknown error"
                                        _aux_msg = (
                                            f"ℹ️ Configured compression model `{_aux_model}` "
                                            f"failed ({_aux_err}). Recovered using your main "
                                            "model — context is intact — but you may want to "
                                            "check `auxiliary.compression.model` in config.yaml."
                                        )
                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            if _adapter and source.chat_id:
                                                await _adapter.send(source.chat_id, _aux_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
                                            logger.warning(
                                                "Failed to deliver aux-model-fallback notice to user: %s",
                                                _werr,
                                            )
                                finally:
                                    # Evict the cached agent so the next turn
                                    # rebuilds its system prompt from current
                                    # SOUL.md, memory, and skills.
                                    self._evict_cached_agent(session_key)
                                    self._cleanup_agent_resources(_hyg_agent)

                    except Exception as e:
                        logger.warning(
                            "Session hygiene auto-compress failed: %s", e
                        )

        # First-message onboarding -- only on the very first interaction ever
        if not history and not self.session_store.has_any_sessions():
            context_prompt += (
                "\n\n[System note: This is the user's very first message ever. "
                "Briefly introduce yourself and mention that /help shows available commands. "
                "Keep the introduction concise -- one or two sentences max.]"
            )

        # One-time prompt if no home channel is set for this platform
        # Skip for webhooks - they deliver directly to configured targets (github_comment, etc.)
        if not history and source.platform and source.platform != Platform.LOCAL and source.platform != Platform.WEBHOOK:
            platform_name = source.platform.value
            env_key = _home_target_env_var(platform_name)
            if not os.getenv(env_key):
                # Slack dispatches all Hermes commands through a single
                # parent slash command `/hermes`; bare `/sethome` is not
                # registered and would fail with "app did not respond".
                sethome_cmd = (
                    "/hermes sethome"
                    if source.platform == Platform.SLACK
                    else "/sethome"
                )
                notice = (
                    f"📬 No home channel is set for {platform_name.title()}. "
                    f"A home channel is where Hermes delivers cron job results "
                    f"and cross-platform messages.\n\n"
                    f"Type {sethome_cmd} to make this chat your home channel, "
                    f"or ignore to skip."
                )
                await self._deliver_platform_notice(source, notice)

        # -----------------------------------------------------------------
        # Voice channel awareness — inject current voice channel state
        # into context so the agent knows who is in the channel and who
        # is speaking, without needing a separate tool call.
        # -----------------------------------------------------------------
        if source.platform == Platform.DISCORD:
            adapter = self.adapters.get(Platform.DISCORD)
            guild_id = self._get_guild_id(event)
            if guild_id and adapter and hasattr(adapter, "get_voice_channel_context"):
                vc_context = adapter.get_voice_channel_context(guild_id)
                if vc_context:
                    context_prompt += f"\n\n{vc_context}"

        # -----------------------------------------------------------------
        # Auto-analyze images sent by the user
        #
        # If the user attached image(s), we run the vision tool eagerly so
        # the conversation model always receives a text description.  The
        # local file path is also included so the model can re-examine the
        # image later with a more targeted question via vision_analyze.
        #
        # We filter to image paths only (by media_type) so that non-image
        # attachments (documents, audio, etc.) are not sent to the vision
        # tool even when they appear in the same message.
        # -----------------------------------------------------------------
        message_text = await self._prepare_inbound_message_text(
            event=event,
            source=source,
            history=history,
        )
        if message_text is None:
            return

        # Bind this gateway run generation to the adapter's active-session
        # event so deferred post-delivery callbacks can be released by the
        # same run that registered them.
        self._bind_adapter_run_generation(
            self.adapters.get(source.platform),
            session_key,
            run_generation,
        )

        try:
            # Emit agent:start hook
            hook_ctx = {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "chat_id": source.chat_id or "",
                "session_id": session_entry.session_id,
                "message": message_text[:500],
            }
            await self.hooks.emit("agent:start", hook_ctx)

            # Run the agent
            agent_result = await self._run_agent(
                message=message_text,
                context_prompt=context_prompt,
                history=history,
                source=source,
                session_id=session_entry.session_id,
                session_key=session_key,
                run_generation=run_generation,
                event_message_id=self._reply_anchor_for_event(event),
                channel_prompt=event.channel_prompt,
            )

            # Stop persistent typing indicator now that the agent is done
            try:
                _typing_adapter = self.adapters.get(source.platform)
                if _typing_adapter and hasattr(_typing_adapter, "stop_typing"):
                    await _typing_adapter.stop_typing(source.chat_id)
            except Exception:
                pass

            if not self._is_session_run_current(_quick_key, run_generation):
                logger.info(
                    "Discarding stale agent result for %s — generation %d is no longer current",
                    _quick_key or "?",
                    run_generation,
                )
                _stale_adapter = self.adapters.get(source.platform)
                if getattr(type(_stale_adapter), "pop_post_delivery_callback", None) is not None:
                    _stale_adapter.pop_post_delivery_callback(
                        _quick_key,
                        generation=run_generation,
                    )
                elif _stale_adapter and hasattr(_stale_adapter, "_post_delivery_callbacks"):
                    _stale_adapter._post_delivery_callbacks.pop(_quick_key, None)
                return None

            response = agent_result.get("final_response") or ""

            # Convert the agent's internal "(empty)" sentinel into a
            # user-friendly message.  "(empty)" means the model failed to
            # produce visible content after exhausting all retries (nudge,
            # prefill, empty-retry, fallback).  Sending the raw sentinel
            # looks like a bug; a short explanation is more helpful.
            if response == "(empty)":
                response = (
                    "⚠️ The model returned no response after processing tool "
                    "results. This can happen with some models — try again or "
                    "rephrase your question."
                )
            agent_messages = agent_result.get("messages", [])
            _response_time = time.time() - _msg_start_time
            _api_calls = agent_result.get("api_calls", 0)
            _resp_len = len(response)
            logger.info(
                "response ready: platform=%s chat=%s time=%.1fs api_calls=%d response=%d chars",
                _platform_name, source.chat_id or "unknown",
                _response_time, _api_calls, _resp_len,
            )

            # Successful turn — clear any stuck-loop counter for this session.
            # This ensures the counter only accumulates across CONSECUTIVE
            # restarts where the session was active (never completed).
            #
            # Also clear the resume_pending flag (set by drain-timeout
            # shutdown) — the turn ran to completion, so recovery
            # succeeded and subsequent messages should no longer receive
            # the restart-interruption system note.
            if session_key and _should_clear_resume_pending_after_turn(agent_result):
                self._clear_restart_failure_count(session_key)
                try:
                    self.session_store.clear_resume_pending(session_key)
                except Exception as _e:
                    logger.debug(
                        "clear_resume_pending failed for %s: %s",
                        session_key, _e,
                    )

            # Normalize empty responses: surface errors, partial failures, and
            # the case where agent did work but returned no text. Fix for #18765.
            response = _normalize_empty_agent_response(
                agent_result, response, history_len=len(history),
            )
            response = _sanitize_gateway_final_response(source.platform, response)

            # If the agent's session_id changed during compression, update
            # session_entry so transcript writes below go to the right session.
            if agent_result.get("session_id") and agent_result["session_id"] != session_entry.session_id:
                session_entry.session_id = agent_result["session_id"]

            # Prepend reasoning/thinking if display is enabled (per-platform)
            try:
                from gateway.display_config import resolve_display_setting as _rds
                _show_reasoning_effective = _rds(
                    _load_gateway_config(),
                    _platform_config_key(source.platform),
                    "show_reasoning",
                    getattr(self, "_show_reasoning", False),
                )
            except Exception:
                _show_reasoning_effective = getattr(self, "_show_reasoning", False)
            if _show_reasoning_effective and response:
                last_reasoning = agent_result.get("last_reasoning")
                if last_reasoning:
                    # Collapse long reasoning to keep messages readable
                    lines = last_reasoning.strip().splitlines()
                    if len(lines) > 15:
                        display_reasoning = "\n".join(lines[:15])
                        display_reasoning += f"\n_... ({len(lines) - 15} more lines)_"
                    else:
                        display_reasoning = last_reasoning.strip()
                    response = f"💭 **Reasoning:**\n```\n{display_reasoning}\n```\n\n{response}"

            # Runtime-metadata footer — only on the FINAL message of the turn.
            # Off by default (display.runtime_footer.enabled=false).  When
            # streaming already delivered the body, we can't mutate the sent
            # text, so we fire a separate trailing send below.
            _footer_line = ""
            try:
                from gateway.runtime_footer import build_footer_line as _bfl
                _footer_line = _bfl(
                    user_config=_load_gateway_config(),
                    platform_key=_platform_config_key(source.platform),
                    model=agent_result.get("model"),
                    context_tokens=agent_result.get("last_prompt_tokens", 0) or 0,
                    context_length=agent_result.get("context_length") or None,
                    cwd=os.environ.get("TERMINAL_CWD", ""),
                )
            except Exception as _footer_err:
                logger.debug("runtime_footer build failed: %s", _footer_err)
                _footer_line = ""
            if _footer_line and response and not agent_result.get("already_sent"):
                response = f"{response}\n\n{_footer_line}"

            # Emit agent:end hook
            await self.hooks.emit("agent:end", {
                **hook_ctx,
                "response": (response or "")[:500],
            })

            # Check for pending process watchers (check_interval on background processes)
            try:
                from tools.process_registry import process_registry
                while process_registry.pending_watchers:
                    watcher = process_registry.pending_watchers.pop(0)
                    asyncio.create_task(self._run_process_watcher(watcher))
            except Exception as e:
                logger.error("Process watcher setup error: %s", e)

            # Drain watch pattern notifications that arrived during the agent run.
            # Watch events and completions share the same queue; completions are
            # already handled by the per-process watcher task above, so we only
            # inject watch-type events here.
            try:
                from tools.process_registry import process_registry as _pr
                _watch_events = []
                while not _pr.completion_queue.empty():
                    evt = _pr.completion_queue.get_nowait()
                    evt_type = evt.get("type", "completion")
                    if evt_type in {"watch_match", "watch_disabled"}:
                        _watch_events.append(evt)
                    # else: completion events are handled by the watcher task
                for evt in _watch_events:
                    synth_text = _format_gateway_process_notification(evt)
                    if synth_text:
                        try:
                            await self._inject_watch_notification(synth_text, evt)
                        except Exception as e2:
                            logger.error("Watch notification injection error: %s", e2)
            except Exception as e:
                logger.debug("Watch queue drain error: %s", e)

            # NOTE: Dangerous command approvals are now handled inline by the
            # blocking gateway approval mechanism in tools/approval.py.  The agent
            # thread blocks until the user responds with /approve or /deny, so by
            # the time we reach here the approval has already been resolved.  The
            # old post-loop pop_pending + approval_hint code was removed in favour
            # of the blocking approach that mirrors CLI's synchronous input().

            # Save the full conversation to the transcript, including tool calls.
            # This preserves the complete agent loop (tool_calls, tool results,
            # intermediate reasoning) so sessions can be resumed with full context
            # and transcripts are useful for debugging and training data.
            #
            # IMPORTANT: For context-overflow failures (compression exhausted,
            # generic 400 on large sessions) we must NOT persist the user's
            # message — doing so would grow the session further and cause the
            # same failure on the next attempt, an infinite loop. (#1630, #9893)
            #
            # Transient failures (429, timeout, connection error, provider 5xx)
            # are different: the session is not oversized, and silently dropping
            # the user message causes severe context loss on retry — the agent
            # forgets what was just asked.  Persist the user turn so the
            # conversation is preserved. (#7100)
            agent_failed_early = bool(agent_result.get("failed"))
            _err_str_for_classify = str(agent_result.get("error", "")).lower()
            # Use specific multi-word phrases (not bare "exceed" or "token")
            # to avoid false positives on transient errors like "rate limit
            # exceeded" or "invalid auth token". Matches run_agent.py's
            # own context-length classifier.
            is_context_overflow_failure = agent_failed_early and (
                bool(agent_result.get("compression_exhausted"))
                or any(p in _err_str_for_classify for p in (
                    "context length", "context size", "context window",
                    "maximum context", "token limit", "too many tokens",
                    "reduce the length", "exceeds the limit",
                    "request entity too large", "prompt is too long",
                    "payload too large", "input is too long",
                ))
                or ("400" in _err_str_for_classify and len(history) > 50)
            )
            if is_context_overflow_failure:
                logger.info(
                    "Skipping transcript persistence for context-overflow "
                    "failure in session %s to prevent session growth loop.",
                    session_entry.session_id,
                )
            elif agent_failed_early:
                logger.info(
                    "Transient agent failure in session %s — persisting user "
                    "message so conversation context is preserved on retry.",
                    session_entry.session_id,
                )

            # When compression is exhausted, the session is permanently too
            # large to process.  Auto-reset it so the next message starts
            # fresh instead of replaying the same oversized context in an
            # infinite fail loop.  (#9893)
            if agent_result.get("compression_exhausted") and session_entry and session_key:
                logger.info(
                    "Auto-resetting session %s after compression exhaustion.",
                    session_entry.session_id,
                )
                self.session_store.reset_session(session_key)
                self._evict_cached_agent(session_key)
                self._session_model_overrides.pop(session_key, None)
                self._set_session_reasoning_override(session_key, None)
                if hasattr(self, "_pending_model_notes"):
                    self._pending_model_notes.pop(session_key, None)
                response = (response or "") + (
                    "\n\n🔄 Session auto-reset — the conversation exceeded the "
                    "maximum context size and could not be compressed further. "
                    "Your next message will start a fresh session."
                )

            ts = datetime.now().isoformat()

            # If this is a fresh session (no history), write the full tool
            # definitions as the first entry so the transcript is self-describing
            # -- the same list of dicts sent as tools=[...] in the API request.
            if is_context_overflow_failure:
                pass  # Skip all transcript writes — don't grow a broken session
            elif not history:
                tool_defs = agent_result.get("tools", [])
                self.session_store.append_to_transcript(
                    session_entry.session_id,
                    {
                        "role": "session_meta",
                        "tools": tool_defs or [],
                        "model": _resolve_gateway_model(),
                        "platform": source.platform.value if source.platform else "",
                        "timestamp": ts,
                    }
                )

            # Find only the NEW messages from this turn (skip history we loaded).
            # Use the filtered history length (history_offset) that was actually
            # passed to the agent, not len(history) which includes session_meta
            # entries that were stripped before the agent saw them.
            if is_context_overflow_failure:
                pass  # handled above — skip all transcript writes
            elif agent_failed_early:
                # Transient failure (429/timeout/5xx): persist only the user
                # message so the next message can load a transcript that
                # reflects what was said.  Skip the assistant error text since
                # it's a gateway-generated hint, not model output. (#7100)
                _user_entry = {"role": "user", "content": message_text, "timestamp": ts}
                if event.message_id:
                    _user_entry["message_id"] = str(event.message_id)
                self.session_store.append_to_transcript(
                    session_entry.session_id,
                    _user_entry,
                )
            else:
                history_len = agent_result.get("history_offset", len(history))
                new_messages = agent_messages[history_len:] if len(agent_messages) > history_len else []

                # If no new messages found (edge case), fall back to simple user/assistant
                if not new_messages:
                    _user_entry = {"role": "user", "content": message_text, "timestamp": ts}
                    if event.message_id:
                        _user_entry["message_id"] = str(event.message_id)
                    self.session_store.append_to_transcript(
                        session_entry.session_id,
                        _user_entry,
                    )
                    if response:
                        self.session_store.append_to_transcript(
                            session_entry.session_id,
                            {"role": "assistant", "content": response, "timestamp": ts}
                        )
                else:
                    # The agent already persisted these messages to SQLite via
                    # _flush_messages_to_session_db(), so skip the DB write here
                    # to prevent the duplicate-write bug (#860).  We still write
                    # to JSONL for backward compatibility and as a backup.
                    agent_persisted = self._session_db is not None
                    # Attach the inbound platform message_id to the first user
                    # entry written this turn so platform-level quote-resolution
                    # (e.g. Yuanbao QuoteContextMiddleware's transcript fallback)
                    # can find earlier @bot messages by their original message_id.
                    _user_msg_id_attached = False
                    for msg in new_messages:
                        # Skip system messages (they're rebuilt each run)
                        if msg.get("role") == "system":
                            continue
                        # Add timestamp to each message for debugging
                        entry = {**msg, "timestamp": ts}
                        if (
                            not _user_msg_id_attached
                            and msg.get("role") == "user"
                            and event.message_id
                            and "message_id" not in entry
                        ):
                            entry["message_id"] = str(event.message_id)
                            _user_msg_id_attached = True
                        self.session_store.append_to_transcript(
                            session_entry.session_id, entry,
                            skip_db=agent_persisted,
                        )

            # Token counts and model are now persisted by the agent directly.
            # Keep only last_prompt_tokens here for context-window tracking and
            # compression decisions.
            self.session_store.update_session(
                session_entry.session_key,
                last_prompt_tokens=agent_result.get("last_prompt_tokens", 0),
            )

            # Auto voice reply: send TTS audio before the text response
            _already_sent = bool(agent_result.get("already_sent"))
            if self._should_send_voice_reply(event, response, agent_messages, already_sent=_already_sent):
                await self._send_voice_reply(event, response)

            # If streaming already delivered the response, extract and
            # deliver any MEDIA: files before returning None.  Streaming
            # sends raw text chunks that include MEDIA: tags — the normal
            # post-processing in _process_message_background is skipped
            # when already_sent is True, so media files would never be
            # delivered without this.
            #
            # Never skip when the agent failed — the error message is new
            # content the user hasn't seen (streaming only sent earlier
            # partial output before the failure).  Without this guard,
            # users see the agent "stop responding without explanation."
            if agent_result.get("already_sent") and not agent_result.get("failed"):
                if response:
                    _media_adapter = self.adapters.get(source.platform)
                    if _media_adapter:
                        await self._deliver_media_from_response(
                            response, event, _media_adapter,
                        )
                # Streaming already delivered the body text, but the footer was
                # intentionally held back (see the `not already_sent` gate above).
                # Send it now as a small trailing message so Telegram/Discord/etc.
                # still surface the runtime metadata on the final reply.
                if _footer_line:
                    try:
                        _foot_adapter = self.adapters.get(source.platform)
                        if _foot_adapter:
                            await _foot_adapter.send(
                                source.chat_id,
                                _footer_line,
                                metadata=self._thread_metadata_for_source(source, self._reply_anchor_for_event(event)),
                            )
                    except Exception as _e:
                        logger.debug("trailing footer send failed: %s", _e)
                return None

            return response

        except Exception as e:
            # Stop typing indicator on error too
            try:
                _err_adapter = self.adapters.get(source.platform)
                if _err_adapter and hasattr(_err_adapter, "stop_typing"):
                    await _err_adapter.stop_typing(source.chat_id)
            except Exception:
                pass
            logger.exception("Agent error in session %s", session_key)
            error_type = type(e).__name__
            error_detail = str(e)[:300] if str(e) else "no details available"
            status_hint = ""
            status_code = getattr(e, "status_code", None)
            _hist_len = len(history) if 'history' in locals() else 0
            if status_code == 401:
                status_hint = " Check your API key or run `claude /login` to refresh OAuth credentials."
            elif status_code == 402:
                status_hint = " Your API balance or quota is exhausted. Check your provider dashboard."
            elif status_code == 429:
                # Check if this is a plan usage limit (resets on a schedule) vs a transient rate limit
                _err_body = getattr(e, "response", None)
                _err_json = {}
                try:
                    if _err_body is not None:
                        _err_json = _err_body.json().get("error", {})
                        if not isinstance(_err_json, dict):
                            _err_json = {}
                except Exception:
                    pass
                if _err_json.get("type") == "usage_limit_reached":
                    _resets_in = _err_json.get("resets_in_seconds")
                    if _resets_in and _resets_in > 0:
                        import math
                        _hours = math.ceil(_resets_in / 3600)
                        status_hint = f" Your plan's usage limit has been reached. It resets in ~{_hours}h."
                    else:
                        status_hint = " Your plan's usage limit has been reached. Please wait until it resets."
                else:
                    status_hint = " You are being rate-limited. Please wait a moment and try again."
            elif status_code == 529:
                status_hint = " The API is temporarily overloaded. Please try again shortly."
            elif status_code in {400, 500}:
                # 400 with a large session is context overflow.
                # 500 with a large session often means the payload is too large
                # for the API to process — treat it the same way.
                if _hist_len > 50:
                    return (
                        "⚠️ Session too large for the model's context window.\n"
                        "Use /compact to compress the conversation, or "
                        "/reset to start fresh."
                    )
                elif status_code == 400:
                    status_hint = " The request was rejected by the API."
            return (
                f"Sorry, I encountered an error ({error_type}).\n"
                f"{error_detail}\n"
                f"{status_hint}"
                "Try again or use /reset to start a fresh session."
            )
        finally:
            # Restore session context variables to their pre-handler state
            self._clear_session_env(_session_env_tokens)

    def _format_session_info(self) -> str:
        """Resolve current model config and return a formatted info block."""
        return format_session_info(
            resolve_gateway_model=_resolve_gateway_model,
            load_gateway_config=_load_gateway_config,
            resolve_runtime_agent_kwargs=_resolve_runtime_agent_kwargs,
        )

    @property
    def _gateway_commands(self):
        service = getattr(self, "_gateway_command_service", None)
        if service is None:
            from gateway.command_handlers import GatewayCommandService

            service = GatewayCommandService(self)
            self._gateway_command_service = service
        return service

    async def _handle_reset_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        return await self._gateway_commands._handle_reset_command(event)

    async def _handle_profile_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_profile_command(event)

    def _check_slash_access(self, source: SessionSource, canonical_cmd: str) -> Optional[str]:
        return self._gateway_commands._check_slash_access(source, canonical_cmd)

    async def _handle_whoami_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_whoami_command(event)

    async def _handle_kanban_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_kanban_command(event)

    async def _handle_status_command(self, event: MessageEvent) -> str:
        event_bus = getattr(self, "_event_bus", None)
        if event_bus is not None:
            await event_bus.emit("status_command_received", {
                "event": event,
                "session_key": getattr(event, "source", None) and self._session_key_for_source(event.source),
                "timestamp": time.time(),
            })
        # Delegate to existing service while we migrate
        return await self._gateway_commands._handle_status_command(event)

    async def _handle_agents_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_agents_command(event)

    async def _handle_stop_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        return await self._gateway_commands._handle_stop_command(event)

    async def _handle_platform_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_platform_command(event)

    async def _handle_restart_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        return await self._gateway_commands._handle_restart_command(event)

    def _is_stale_restart_redelivery(self, event: MessageEvent) -> bool:
        return self._gateway_commands._is_stale_restart_redelivery(event)

    async def _handle_help_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_help_command(event)

    async def _handle_commands_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_commands_command(event)

    async def _handle_model_command(self, event: MessageEvent) -> Optional[str]:
        return await self._gateway_commands._handle_model_command(event)

    async def _handle_codex_runtime_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_codex_runtime_command(event)

    async def _handle_personality_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_personality_command(event)

    async def _handle_retry_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_retry_command(event)

    def _goal_max_turns_from_config(self) -> int:
        return self._gateway_commands._goal_max_turns_from_config()

    def _get_goal_manager_for_event(self, event: 'MessageEvent'):
        return self._gateway_commands._get_goal_manager_for_event(event)

    async def _handle_goal_command(self, event: 'MessageEvent') -> str:
        return await self._gateway_commands._handle_goal_command(event)

    async def _handle_subgoal_command(self, event: 'MessageEvent') -> str:
        return await self._gateway_commands._handle_subgoal_command(event)

    async def _send_goal_status_notice(self, source: Any, message: str) -> None:
        return await self._gateway_commands._send_goal_status_notice(source, message)

    async def _defer_goal_status_notice_after_delivery(self, source: Any, message: str) -> None:
        return await self._gateway_commands._defer_goal_status_notice_after_delivery(source, message)

    async def _post_turn_goal_continuation(self, *, session_entry: Any, source: Any, final_response: str) -> None:
        return await self._gateway_commands._post_turn_goal_continuation(session_entry=session_entry, source=source, final_response=final_response)

    async def _handle_undo_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_undo_command(event)

    async def _handle_set_home_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_set_home_command(event)

    def _get_guild_id(self, event: MessageEvent) -> Optional[int]:
        return self._gateway_commands._get_guild_id(event)

    async def _handle_voice_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_voice_command(event)

    async def _handle_voice_channel_join(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_voice_channel_join(event)

    async def _handle_voice_channel_leave(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_voice_channel_leave(event)

    def _handle_voice_timeout_cleanup(self, chat_id: str) -> None:
        return self._gateway_commands._handle_voice_timeout_cleanup(chat_id)

    def _is_duplicate_voice_transcript(self, guild_id: int, user_id: int, transcript: str) -> bool:
        return self._gateway_commands._is_duplicate_voice_transcript(guild_id, user_id, transcript)

    async def _handle_voice_channel_input(self, guild_id: int, user_id: int, transcript: str):
        return await self._gateway_commands._handle_voice_channel_input(guild_id, user_id, transcript)

    def _should_send_voice_reply(self, event: MessageEvent, response: str, agent_messages: list, already_sent: bool=False) -> bool:
        return self._gateway_commands._should_send_voice_reply(event, response, agent_messages, already_sent)

    async def _send_voice_reply(self, event: MessageEvent, text: str) -> None:
        return await self._gateway_commands._send_voice_reply(event, text)

    async def _deliver_media_from_response(self, response: str, event: MessageEvent, adapter) -> None:
        return await deliver_media_from_response(
            response=response,
            event=event,
            adapter=adapter,
            thread_metadata_for_source=self._thread_metadata_for_source,
            reply_anchor_for_event=self._reply_anchor_for_event,
            log=logger,
        )

    async def _handle_rollback_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_rollback_command(event)

    async def _handle_background_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_background_command(event)

    async def _run_background_task(self, prompt: str, source: 'SessionSource', task_id: str, event_message_id: Optional[str]=None, media_urls: Optional[List[str]]=None, media_types: Optional[List[str]]=None) -> None:
        return await self._gateway_commands._run_background_task(prompt, source, task_id, event_message_id, media_urls, media_types)

    async def _handle_reasoning_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_reasoning_command(event)

    async def _handle_fast_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_fast_command(event)

    async def _handle_yolo_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        return await self._gateway_commands._handle_yolo_command(event)

    async def _handle_verbose_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_verbose_command(event)

    async def _handle_footer_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_footer_command(event)

    async def _handle_compress_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_compress_command(event)

    async def _get_telegram_topic_capabilities(self, source: SessionSource) -> dict:
        return await self._gateway_commands._get_telegram_topic_capabilities(source)

    async def _ensure_telegram_system_topic(self, source: SessionSource) -> None:
        return await self._gateway_commands._ensure_telegram_system_topic(source)

    async def _send_telegram_topic_setup_image(self, source: SessionSource) -> None:
        return await self._gateway_commands._send_telegram_topic_setup_image(source)

    def _sanitize_telegram_topic_title(self, title: str) -> str:
        return self._gateway_commands._sanitize_telegram_topic_title(title)

    async def _rename_telegram_topic_for_session_title(self, source: SessionSource, session_id: str, title: str) -> None:
        return await self._gateway_commands._rename_telegram_topic_for_session_title(source, session_id, title)

    def _telegram_topic_auto_rename_disabled(self, source: SessionSource) -> bool:
        return self._gateway_commands._telegram_topic_auto_rename_disabled(source)

    def _schedule_telegram_topic_title_rename(self, source: SessionSource, session_id: str, title: str) -> None:
        return self._gateway_commands._schedule_telegram_topic_title_rename(source, session_id, title)

    def _should_send_telegram_capability_hint(self, source: SessionSource) -> bool:
        return self._gateway_commands._should_send_telegram_capability_hint(source)

    def _telegram_topic_help_text(self) -> str:
        return self._gateway_commands._telegram_topic_help_text()

    def _disable_telegram_topic_mode_for_chat(self, source: SessionSource) -> str:
        return self._gateway_commands._disable_telegram_topic_mode_for_chat(source)

    async def _handle_topic_command(self, event: MessageEvent, args: str='') -> str:
        return await self._gateway_commands._handle_topic_command(event, args)

    def _telegram_topic_root_status_message(self, source: SessionSource) -> str:
        return self._gateway_commands._telegram_topic_root_status_message(source)

    async def _restore_telegram_topic_session(self, event: MessageEvent, raw_session_id: str) -> str:
        return await self._gateway_commands._restore_telegram_topic_session(event, raw_session_id)

    async def _handle_title_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_title_command(event)

    async def _handle_resume_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_resume_command(event)

    async def _handle_branch_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_branch_command(event)

    async def _handle_usage_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_usage_command(event)

    async def _handle_insights_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_insights_command(event)

    async def _handle_reload_mcp_command(self, event: MessageEvent) -> Optional[str]:
        return await self._gateway_commands._handle_reload_mcp_command(event)

    async def _execute_mcp_reload(self, event: MessageEvent) -> str:
        return await self._gateway_commands._execute_mcp_reload(event)

    async def _handle_reload_skills_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_reload_skills_command(event)

    async def _handle_bundles_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_bundles_command(event)

    async def _maybe_confirm_destructive_slash(self, *, event: MessageEvent, command: str, title: str, detail: str, execute) -> Union[str, 'EphemeralReply', None]:
        return await self._gateway_commands._maybe_confirm_destructive_slash(event=event, command=command, title=title, detail=detail, execute=execute)

    async def _request_slash_confirm(self, *, event: MessageEvent, command: str, title: str, message: str, handler) -> Optional[str]:
        return await self._gateway_commands._request_slash_confirm(event=event, command=command, title=title, message=message, handler=handler)

    def _read_user_config(self) -> Dict[str, Any]:
        return self._gateway_commands._read_user_config()

    def _thread_metadata_for_source(self, source, reply_to_message_id: Optional[str]=None) -> Optional[Dict[str, Any]]:
        return self._gateway_commands._thread_metadata_for_source(source, reply_to_message_id)

    def _reply_anchor_for_event(self, event: MessageEvent) -> Optional[str]:
        return self._gateway_commands._reply_anchor_for_event(event)

    async def _handle_approve_command(self, event: MessageEvent) -> Optional[str]:
        return await self._gateway_commands._handle_approve_command(event)

    async def _handle_deny_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_deny_command(event)

    async def _handle_debug_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_debug_command(event)

    async def _handle_update_command(self, event: MessageEvent) -> str:
        return await self._gateway_commands._handle_update_command(event)

    def _schedule_update_notification_watch(self) -> None:
        return self._gateway_commands._schedule_update_notification_watch()

    async def _watch_update_progress(self, poll_interval: float=2.0, stream_interval: float=4.0, timeout: float=1800.0) -> None:
        return await self._gateway_commands._watch_update_progress(poll_interval, stream_interval, timeout)

    async def _send_update_notification(self) -> bool:
        """If an update finished, notify the user.

        Returns False when the update is still running so a caller can retry
        later. Returns True after a definitive send/skip decision.

        This is the legacy notification path used when the streaming watcher
        cannot resolve the adapter (e.g. after a gateway restart where the
        platform hasn't reconnected yet).
        """
        return await send_update_notification(
            _hermes_home,
            adapters=self.adapters,
            logger=logger,
        )

    async def _send_restart_notification(self) -> Optional[tuple[str, str, Optional[str]]]:
        """Notify the chat that initiated /restart that the gateway is back."""
        return await send_restart_notification(
            _hermes_home,
            adapters=self.adapters,
            config=self.config,
            logger=logger,
        )

    async def _send_home_channel_startup_notifications(
        self,
        *,
        skip_targets: Optional[set[tuple[str, str, Optional[str]]]] = None,
    ) -> set[tuple[str, str, Optional[str]]]:
        """Notify configured home channels that the gateway is back online.

        The notification is best-effort and sent once per connected platform
        home channel. ``skip_targets`` lets startup avoid duplicate messages
        when a more specific restart notification is queued for the same chat.
        """
        return await send_home_channel_startup_notifications(
            adapters=self.adapters,
            config=self.config,
            skip_targets=skip_targets,
            logger=logger,
        )

    def _set_session_env(self, context: SessionContext) -> list:
        """Set session context variables for the current async task.

        Uses ``contextvars`` instead of ``os.environ`` so that concurrent
        gateway messages cannot overwrite each other's session state.

        Returns a list of reset tokens; pass them to ``_clear_session_env``
        in a ``finally`` block.
        """
        from gateway.session_context import set_session_vars
        return set_session_vars(
            platform=context.source.platform.value,
            chat_id=context.source.chat_id,
            chat_name=context.source.chat_name or "",
            thread_id=str(context.source.thread_id) if context.source.thread_id else "",
            user_id=str(context.source.user_id) if context.source.user_id else "",
            user_name=str(context.source.user_name) if context.source.user_name else "",
            session_key=context.session_key,
            message_id=str(context.source.message_id) if context.source.message_id else "",
        )

    def _clear_session_env(self, tokens: list) -> None:
        """Restore session context variables to their pre-handler values."""
        from gateway.session_context import clear_session_vars
        clear_session_vars(tokens)

    async def _run_in_executor_with_context(self, func, *args):
        """Run blocking work in the thread pool while preserving session contextvars."""
        loop = asyncio.get_running_loop()
        ctx = copy_context()
        return await loop.run_in_executor(None, ctx.run, func, *args)

    def _decide_image_input_mode(self) -> str:
        """Resolve the image-input routing for the currently active model.

        Returns ``"native"`` (attach pixels on the user turn) or ``"text"``
        (pre-analyze with vision_analyze and prepend the description). See
        agent/image_routing.py for the full decision table.

        The active provider/model are read from config.yaml so the decision
        tracks ``/model`` switches automatically on the next message.
        """
        return resolve_image_input_mode(logger=logger)

    async def _enrich_message_with_vision(
        self,
        user_text: str,
        image_paths: List[str],
    ) -> str:
        """
        Auto-analyze user-attached images with the vision tool and prepend
        the descriptions to the message text.

        Each image is analyzed with a general-purpose prompt.  The resulting
        description *and* the local cache path are injected so the model can:
          1. Immediately understand what the user sent (no extra tool call).
          2. Re-examine the image with vision_analyze if it needs more detail.

        Args:
            user_text:   The user's original caption / message text.
            image_paths: List of local file paths to cached images.

        Returns:
            The enriched message string with vision descriptions prepended.
        """
        return await enrich_message_with_vision(
            user_text,
            image_paths,
            logger=logger,
        )

    async def _enrich_message_with_transcription(
        self,
        user_text: str,
        audio_paths: List[str],
    ) -> str:
        """
        Auto-transcribe user voice/audio messages using the configured STT provider
        and prepend the transcript to the message text.

        Args:
            user_text:   The user's original caption / message text.
            audio_paths: List of local file paths to cached audio files.

        Returns:
            The enriched message string with transcriptions prepended.
        """
        return await enrich_message_with_transcription(
            user_text,
            audio_paths,
            stt_enabled=getattr(self.config, "stt_enabled", True),
            probe_audio_duration=_probe_audio_duration,
            has_setup_skill=self._has_setup_skill,
            logger=logger,
        )

    def _build_process_event_source(self, evt: dict):
        """Resolve the canonical source for a synthetic background-process event.

        Prefer the persisted session-store origin for the event's session key.
        Falling back to the currently active foreground event is what causes
        cross-topic bleed, so don't do that.
        """
        return build_process_event_source(
            evt,
            session_store=self.session_store,
            get_cached_session_source=self._get_cached_session_source,
            logger=logger,
        )

    async def _inject_watch_notification(self, synth_text: str, evt: dict) -> None:
        """Inject a watch-pattern notification as a synthetic message event.

        Routing must come from the queued watch event itself, not from whatever
        foreground message happened to be active when the queue was drained.
        """
        await inject_watch_notification(
            synth_text,
            evt,
            source_resolver=self._build_process_event_source,
            adapters=self.adapters,
            logger=logger,
        )

    async def _run_process_watcher(self, watcher: dict) -> None:
        """
        Periodically check a background process and push updates to the user.

        Runs as an asyncio task. Stays silent when nothing changed.
        Auto-removes when the process exits or is killed.

        Notification mode (from ``display.background_process_notifications``):
          - ``all``    — running-output updates + final message
          - ``result`` — final completion message only
          - ``error``  — final message only when exit code != 0
          - ``off``    — no messages at all
        """
        await run_process_watcher(
            watcher,
            adapters=self.adapters,
            notify_mode=self._load_background_notifications_mode(),
            source_resolver=self._build_process_event_source,
            logger=logger,
        )

    _MAX_INTERRUPT_DEPTH = 3  # Cap recursive interrupt handling (#816)

    # Config keys whose values MUST invalidate the gateway's cached agent
    # when they change.  The agent bakes these into its compressor / context
    # handling at construction time, so a mid-running-gateway config edit
    # would otherwise be silently ignored until the user triggers a
    # different cache eviction (model switch, /reset, etc.).
    #
    # Each entry is a tuple of (section, key) read from the raw config dict.
    # Add more here as new baked-at-construction config settings are added.
    _CACHE_BUSTING_CONFIG_KEYS: tuple = CACHE_BUSTING_CONFIG_KEYS

    @classmethod
    def _extract_cache_busting_config(cls, user_config: dict | None) -> dict:
        """Pull values that must bust the cached agent.

        Returns a flat dict keyed by 'section.key'.  Missing config keys and
        non-dict sections yield None values, which still contribute to the
        signature (so 'absent' vs 'present-and-null' differ).

        The live tool registry generation is included too.  MCP reloads and
        dynamic MCP tool-list changes mutate the registry without necessarily
        changing config.yaml.  Cached AIAgent instances freeze their tool
        schemas at construction time, so a registry generation change must
        rebuild the agent before the next turn.
        """
        return extract_cache_busting_config(user_config)

    @staticmethod
    def _agent_config_signature(
        model: str,
        runtime: dict,
        enabled_toolsets: list,
        ephemeral_prompt: str,
        cache_keys: dict | None = None,
    ) -> str:
        """Compute a stable string key from agent config values.

        When this signature changes between messages, the cached AIAgent is
        discarded and rebuilt.  When it stays the same, the cached agent is
        reused — preserving the frozen system prompt and tool schemas for
        prompt cache hits.

        ``cache_keys`` is an optional flat dict of additional config values
        that should invalidate the cache when they change.  Callers pass
        the output of ``_extract_cache_busting_config(user_config)`` so
        edits to model.context_length / compression.* in config.yaml are
        picked up on the next gateway message without a manual restart.
        """
        return compute_agent_config_signature(
            model,
            runtime,
            enabled_toolsets,
            ephemeral_prompt,
            cache_keys=cache_keys,
        )

    def _apply_session_model_override(
        self, session_key: str, model: str, runtime_kwargs: dict
    ) -> tuple:
        """Apply /model session overrides if present, returning (model, runtime_kwargs).

        The gateway /model command stores per-session overrides in
        ``_session_model_overrides``.  These must take precedence over
        config.yaml defaults so the switched model is actually used for
        subsequent messages.  Fields with ``None`` values are skipped so
        partial overrides don't clobber valid config defaults.
        """
        return apply_session_model_override(
            self._session_model_overrides,
            session_key,
            model,
            runtime_kwargs,
        )

    def _is_intentional_model_switch(self, session_key: str, agent_model: str) -> bool:
        """Return True if *agent_model* matches an active /model session override."""
        return is_intentional_model_switch(
            self._session_model_overrides,
            session_key,
            agent_model,
        )

    def _release_running_agent_state(
        self,
        session_key: str,
        *,
        run_generation: Optional[int] = None,
    ) -> bool:
        """Pop ALL per-running-agent state entries for ``session_key``.

        Replaces ad-hoc ``del self._running_agents[key]`` calls scattered
        across the gateway.  Those sites had drifted: some popped only
        ``_running_agents``; some also ``_running_agents_ts``; only one
        path also cleared ``_busy_ack_ts``.  Each missed entry was a
        small, persistent leak — a (str_key → float) tuple per session
        per gateway lifetime.

        Use this at every site that ends a running turn, regardless of
        cause (normal completion, /stop, /reset, /resume, sentinel
        cleanup, stale-eviction).  Per-session state that PERSISTS
        across turns (``_session_model_overrides``, ``_voice_mode``,
        ``_pending_approvals``, ``_update_prompt_pending``) is NOT
        touched here — those have their own lifecycles.

        When ``run_generation`` is provided, only clear the slot if that
        generation is still current for the session.  This prevents an
        older async run whose generation was bumped by /stop or /new from
        clobbering a newer run's state during its own unwind.  Returns
        True when the slot was cleared, False when an ownership guard
        blocked it.
        """
        return release_running_agent_state(
            session_key,
            running_agents=self._running_agents,
            running_agents_ts=self._running_agents_ts,
            busy_ack_ts=getattr(self, "_busy_ack_ts", None),
            run_generation=run_generation,
            is_session_run_current=self._is_session_run_current,
        )

    def _clear_session_boundary_security_state(self, session_key: str) -> None:
        """Clear per-session control state that must not survive a boundary switch."""
        clear_session_boundary_security_state(
            session_key,
            pending_skills_reload_notes=getattr(self, "_pending_skills_reload_notes", None),
            pending_approvals=getattr(self, "_pending_approvals", None),
            update_prompt_pending=getattr(self, "_update_prompt_pending", None),
            logger=logger,
        )

    def _begin_session_run_generation(self, session_key: str) -> int:
        """Claim a fresh run generation token for ``session_key``.

        Every top-level gateway turn gets a monotonically increasing token.
        If a later command like /stop or /new invalidates that token while the
        old worker is still unwinding, the late result can be recognized and
        dropped instead of bleeding into the fresh session.
        """
        generations = self.__dict__.get("_session_run_generation")
        if generations is None:
            generations = {}
            self._session_run_generation = generations
        return begin_session_run_generation(generations, session_key)

    def _invalidate_session_run_generation(self, session_key: str, *, reason: str = "") -> int:
        """Invalidate any in-flight run token for ``session_key``."""
        generations = self.__dict__.get("_session_run_generation")
        if generations is None:
            generations = {}
            self._session_run_generation = generations
        generation = invalidate_session_run_generation(generations, session_key)
        if reason:
            logger.info(
                "Invalidated run generation for %s → %d (%s)",
                session_key,
                generation,
                reason,
            )
        return generation

    def _is_session_run_current(self, session_key: str, generation: int) -> bool:
        """Return True when ``generation`` is still current for ``session_key``."""
        return is_session_run_current(
            self.__dict__.get("_session_run_generation"),
            session_key,
            generation,
        )

    def _bind_adapter_run_generation(
        self,
        adapter: Any,
        session_key: str,
        generation: int | None,
    ) -> None:
        """Bind a gateway run generation to the adapter's active-session event."""
        if not adapter or not session_key or generation is None:
            return
        try:
            interrupt_event = getattr(adapter, "_active_sessions", {}).get(session_key)
            if interrupt_event is not None:
                setattr(interrupt_event, "_hermes_run_generation", int(generation))
        except Exception:
            pass

    async def _interrupt_and_clear_session(
        self,
        session_key: str,
        source: SessionSource,
        *,
        interrupt_reason: str,
        invalidation_reason: str,
        release_running_state: bool = True,
    ) -> None:
        """Interrupt the current run and clear queued session state consistently."""
        await interrupt_and_clear_session(
            session_key,
            source,
            running_agents=self._running_agents,
            pending_sentinel=_AGENT_PENDING_SENTINEL,
            adapters=self.adapters,
            pending_messages=self._pending_messages,
            invalidate_session=self._invalidate_session_run_generation,
            release_running_state=self._release_running_agent_state,
            interrupt_reason=interrupt_reason,
            invalidation_reason=invalidation_reason,
            release_running_state_enabled=release_running_state,
        )

    def _evict_cached_agent(self, session_key: str) -> None:
        """Remove a cached agent for a session (called on /new, /model, etc)."""
        evict_cached_agent(
            getattr(self, "_agent_cache", None),
            getattr(self, "_agent_cache_lock", None),
            session_key,
        )

    @staticmethod
    def _init_cached_agent_for_turn(agent: Any, interrupt_depth: int) -> None:
        """Reset per-turn state on a cached agent before a new turn starts.

        Both _last_activity_ts and _last_activity_desc are only reset for
        fresh external turns (depth 0); they are semantically paired —
        desc describes the activity *at* ts, so updating one without the
        other would make get_activity_summary() misleading.
        For interrupt-recursive turns both are preserved so the inactivity
        watchdog can accumulate stuck-turn idle time and fire the 30-min
        timeout (#15654).  The depth-0 reset is still needed: a session
        idle for 29 min would otherwise trip the watchdog before the new
        turn makes its first API call (#9051).
        """
        init_cached_agent_for_turn(
            agent,
            interrupt_depth,
            now=time.time() if interrupt_depth == 0 else None,
        )

    def _release_evicted_agent_soft(self, agent: Any) -> None:
        """Soft cleanup for cache-evicted agents — preserves session tool state.

        Called from _enforce_agent_cache_cap and _sweep_idle_cached_agents.
        Distinct from _cleanup_agent_resources (full teardown) because a
        cache-evicted session may resume at any time — its terminal
        sandbox, browser daemon, and tracked bg processes must outlive
        the Python AIAgent instance so the next agent built for the
        same task_id inherits them.
        """
        release_evicted_agent_soft(
            agent,
            cleanup_agent_resources=self._cleanup_agent_resources,
        )

    def _enforce_agent_cache_cap(self) -> None:
        """Evict oldest cached agents when cache exceeds _AGENT_CACHE_MAX_SIZE.

        Must be called with _agent_cache_lock held.  Resource cleanup
        (memory provider shutdown, tool resource close) is scheduled
        on a daemon thread so the caller doesn't block on slow teardown
        while holding the cache lock.

        Agents currently in _running_agents are SKIPPED — their clients,
        terminal sandboxes, background processes, and child subagents
        are all in active use by the running turn.  Evicting them would
        tear down those resources mid-turn and crash the request.  If
        every candidate in the LRU order is active, we simply leave the
        cache over the cap; it will be re-checked on the next insert.
        """
        def _schedule_release(key: str, agent: Any, reason: str) -> None:
            threading.Thread(
                target=self._release_evicted_agent_soft,
                args=(agent,),
                daemon=True,
                name=f"agent-cache-{reason}-{str(key)[:24]}",
            ).start()

        enforce_agent_cache_cap(
            getattr(self, "_agent_cache", None),
            running_agents=getattr(self, "_running_agents", {}),
            pending_sentinel=_AGENT_PENDING_SENTINEL,
            max_size=_AGENT_CACHE_MAX_SIZE,
            schedule_release=_schedule_release,
            logger=logger,
        )

    def _sweep_idle_cached_agents(self) -> int:
        """Evict cached agents whose AIAgent has been idle > _AGENT_CACHE_IDLE_TTL_SECS.

        Safe to call from the session expiry watcher without holding the
        cache lock — acquires it internally.  Returns the number of entries
        evicted.  Resource cleanup is scheduled on daemon threads.

        Agents currently in _running_agents are SKIPPED for the same reason
        as _enforce_agent_cache_cap: tearing down an active turn's clients
        mid-flight would crash the request.
        """
        def _schedule_release(key: str, agent: Any, reason: str) -> None:
            threading.Thread(
                target=self._release_evicted_agent_soft,
                args=(agent,),
                daemon=True,
                name=f"agent-cache-{reason}-{str(key)[:24]}",
            ).start()

        return sweep_idle_cached_agents(
            getattr(self, "_agent_cache", None),
            cache_lock=getattr(self, "_agent_cache_lock", None),
            running_agents=getattr(self, "_running_agents", {}),
            pending_sentinel=_AGENT_PENDING_SENTINEL,
            idle_ttl_secs=_AGENT_CACHE_IDLE_TTL_SECS,
            schedule_release=_schedule_release,
            logger=logger,
        )

    # ------------------------------------------------------------------
    # Proxy mode: forward messages to a remote Hermes API server
    # ------------------------------------------------------------------

    def _get_proxy_url(self) -> Optional[str]:
        """Return the proxy URL if proxy mode is configured, else None.

        Checks GATEWAY_PROXY_URL env var first (convenient for Docker),
        then ``gateway.proxy_url`` in config.yaml.
        """
        url = os.getenv("GATEWAY_PROXY_URL", "").strip()
        if url:
            return url.rstrip("/")
        cfg = _load_gateway_config()
        url = (cfg.get("gateway") or {}).get("proxy_url", "").strip()
        if url:
            return url.rstrip("/")
        return None

    async def _run_agent(
        self,
        message: str,
        context_prompt: str,
        history: List[Dict[str, Any]],
        source: SessionSource,
        session_id: str,
        session_key: str = None,
        run_generation: Optional[int] = None,
        _interrupt_depth: int = 0,
        event_message_id: Optional[str] = None,
        channel_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the agent with the given message and context.

        Returns the full result dict from run_conversation, including:
          - "final_response": str (the text to send back)
          - "messages": list (full conversation including tool calls)
          - "api_calls": int
          - "completed": bool

        This is run in a thread pool to not block the event loop.
        Supports interruption via new messages.
        """
        # ---- Proxy mode: delegate to remote API server ----
        _proxy_url = self._get_proxy_url()
        if _proxy_url:
            from gateway.proxy_runner import run_agent_via_proxy
            return await run_agent_via_proxy(
                proxy_url=_proxy_url,
                message=message,
                context_prompt=context_prompt,
                history=history,
                source=source,
                session_id=session_id,
                session_key=session_key,
                run_generation=run_generation,
                event_message_id=event_message_id,
                adapter=self.adapters.get(source.platform),
                is_session_run_current=self._is_session_run_current,
                thread_metadata=self._thread_metadata_for_source(source, event_message_id),
                streaming_config=getattr(getattr(self, "config", None), "streaming", None),
            )

        from run_agent import AIAgent
        import queue

        def _run_still_current() -> bool:
            if run_generation is None or not session_key:
                return True
            return self._is_session_run_current(session_key, run_generation)

        user_config = _load_gateway_config()
        platform_key = _platform_config_key(source.platform)

        from hermes_cli.tools_config import _get_platform_tools
        enabled_toolsets = sorted(_get_platform_tools(user_config, platform_key))
        agent_cfg_local = user_config.get("agent") or {}
        disabled_toolsets = agent_cfg_local.get("disabled_toolsets") or None

        display_config = user_config.get("display", {})
        if not isinstance(display_config, dict):
            display_config = {}

        # Per-platform display settings — resolve via display_config module
        # which checks display.platforms.<platform>.<key> first, then
        # display.<key> global, then built-in platform defaults.
        from gateway.display_config import resolve_display_setting

        # Apply tool preview length config (0 = no limit)
        try:
            from agent.display import set_tool_preview_max_len
            _tpl = resolve_display_setting(user_config, platform_key, "tool_preview_length", 0)
            set_tool_preview_max_len(int(_tpl) if _tpl else 0)
        except Exception:
            pass

        # Tool progress mode — resolved per-platform with env var fallback
        _resolved_tp = resolve_display_setting(user_config, platform_key, "tool_progress")
        _env_tp = os.getenv("HERMES_TOOL_PROGRESS_MODE")
        _display_cfg = display_config if isinstance(display_config, dict) else {}
        _platforms_cfg = _display_cfg.get("platforms") or {}
        _platform_cfg = _platforms_cfg.get(platform_key) or {}
        _legacy_tp_overrides = _display_cfg.get("tool_progress_overrides") or {}
        _tool_progress_configured = (
            "tool_progress" in _display_cfg
            or (
                isinstance(_platform_cfg, dict)
                and "tool_progress" in _platform_cfg
            )
            or (
                isinstance(_legacy_tp_overrides, dict)
                and platform_key in _legacy_tp_overrides
            )
        )
        progress_mode = (
            _env_tp
            if _env_tp and not _tool_progress_configured
            else (_resolved_tp or _env_tp or "all")
        )
        # Disable tool progress for webhooks - they don't support message editing,
        # so each progress line would be sent as a separate message.
        from gateway.config import Platform
        tool_progress_enabled = progress_mode != "off" and source.platform != Platform.WEBHOOK
        # Natural assistant status messages are intentionally independent from
        # tool progress and token streaming. Users can keep tool_progress quiet
        # in chat platforms while opting into concise mid-turn updates.
        interim_assistant_messages_enabled = (
            source.platform != Platform.WEBHOOK
            and is_truthy_value(
                display_config.get("interim_assistant_messages"),
                default=True,
            )
        )

        # Queue for progress messages (thread-safe)
        progress_queue = queue.Queue() if tool_progress_enabled else None
        last_tool = [None]  # Mutable container for tracking in closure
        last_progress_msg = [None]  # Track last message for dedup
        repeat_count = [0]  # How many times the same message repeated

        # Auto-cleanup of temporary progress bubbles (Telegram + any adapter
        # that implements ``delete_message``). When enabled via
        # ``display.platforms.<platform>.cleanup_progress: true``, message IDs
        # from the tool-progress / "Still working..." / status-callback bubbles
        # are collected here and deleted after the final response lands.
        # Failed runs skip cleanup so the bubbles remain as breadcrumbs.
        _cleanup_progress = bool(
            resolve_display_setting(user_config, platform_key, "cleanup_progress")
        )
        _cleanup_adapter = self.adapters.get(source.platform) if _cleanup_progress else None
        if _cleanup_adapter is not None and (
            type(_cleanup_adapter).delete_message is BasePlatformAdapter.delete_message
        ):
            # Adapter doesn't support deletion — silently disable.
            _cleanup_progress = False
            _cleanup_adapter = None
        _cleanup_msg_ids: List[str] = []
        # First-touch onboarding latch: fires at most once per run, even if
        # several tools exceed the threshold.
        long_tool_hint_fired = [False]
        _LONG_TOOL_THRESHOLD_S = 30.0

        def progress_callback(event_type: str, tool_name: str = None, preview: str = None, args: dict = None, **kwargs):
            """Callback invoked by agent on tool lifecycle events."""
            if not progress_queue or not _run_still_current():
                return

            # First-touch onboarding: the first time a tool takes longer than
            # _LONG_TOOL_THRESHOLD_S during a run that's streaming every tool
            # (progress_mode == "all"), append a one-time hint suggesting
            # /verbose.  We only fire when (a) the user hasn't seen the hint
            # before and (b) /verbose is actually usable on this platform
            # (gateway gate must be open).  The CLI has its own trigger.
            if event_type == "tool.completed" and not long_tool_hint_fired[0]:
                try:
                    duration = kwargs.get("duration") or 0
                    if duration >= _LONG_TOOL_THRESHOLD_S and progress_mode == "all":
                        from agent.onboarding import (
                            TOOL_PROGRESS_FLAG,
                            is_seen,
                            mark_seen,
                            tool_progress_hint_gateway,
                        )
                        _cfg = _load_gateway_config()
                        gate_on = is_truthy_value(
                            cfg_get(_cfg, "display", "tool_progress_command"),
                            default=False,
                        )
                        if gate_on and not is_seen(_cfg, TOOL_PROGRESS_FLAG):
                            long_tool_hint_fired[0] = True
                            progress_queue.put(tool_progress_hint_gateway())
                            mark_seen(_hermes_home / "config.yaml", TOOL_PROGRESS_FLAG)
                except Exception as _hint_err:
                    logger.debug("tool-progress onboarding hint failed: %s", _hint_err)
                return


            # Only act on tool.started events (ignore tool.completed, reasoning.available, etc.)
            if event_type not in {"tool.started",}:
                return

            # Suppress tool-progress bubbles once the user has sent `stop`.
            # When the LLM response carries N parallel tool calls, the agent
            # fires N "tool.started" events back-to-back before checking for
            # interrupts — without this guard, a late `stop` still renders
            # all N as 🔍 bubbles, making the interrupt feel ignored.
            # (agent lives in run_sync's scope; agent_holder[0] is the shared
            # handle across nested scopes — see line ~9607.)
            try:
                _agent_for_interrupt = agent_holder[0] if agent_holder else None
                if _agent_for_interrupt is not None and getattr(
                    _agent_for_interrupt, "is_interrupted", False
                ):
                    return
            except Exception:
                pass

            # "new" mode: only report when tool changes
            if progress_mode == "new" and tool_name == last_tool[0]:
                return
            last_tool[0] = tool_name

            # Build progress message with primary argument preview
            from agent.display import get_tool_emoji
            emoji = get_tool_emoji(tool_name, default="⚙️")

            # Verbose mode: show detailed arguments, respects tool_preview_length
            if progress_mode == "verbose":
                if args:
                    from agent.display import get_tool_preview_max_len
                    _pl = get_tool_preview_max_len()
                    args_str = json.dumps(args, ensure_ascii=False, default=str)
                    # When tool_preview_length is 0 (default), don't truncate
                    # in verbose mode — the user explicitly asked for full
                    # detail.  Platform message-length limits handle the rest.
                    if _pl > 0 and len(args_str) > _pl:
                        args_str = args_str[:_pl - 3] + "..."
                    msg = f"{emoji} {tool_name}({list(args.keys())})\n{args_str}"
                elif preview:
                    msg = f"{emoji} {tool_name}: \"{preview}\""
                else:
                    msg = f"{emoji} {tool_name}..."
                progress_queue.put(msg)
                return

            # "all" / "new" modes: short preview, respects tool_preview_length
            # config (defaults to 40 chars when unset to keep gateway messages
            # compact — unlike CLI spinners, these persist as permanent messages).
            if preview:
                from agent.display import get_tool_preview_max_len
                _pl = get_tool_preview_max_len()
                _cap = _pl if _pl > 0 else 40
                if len(preview) > _cap:
                    preview = preview[:_cap - 3] + "..."
                msg = f"{emoji} {tool_name}: \"{preview}\""
            else:
                msg = f"{emoji} {tool_name}..."

            # Dedup: collapse consecutive identical progress messages.
            # Common with execute_code where models iterate with the same
            # code (same boilerplate imports → identical previews).
            if msg == last_progress_msg[0]:
                repeat_count[0] += 1
                # Update the last line in progress_lines with a counter
                # via a special "dedup" queue message.
                progress_queue.put(("__dedup__", msg, repeat_count[0]))
                return
            last_progress_msg[0] = msg
            repeat_count[0] = 0

            progress_queue.put(msg)

        # Background task to send progress messages
        # Accumulates tool lines into a single message that gets edited.
        #
        # Threading metadata is platform-specific:
        # - Slack DM threading needs event_message_id fallback (reply thread)
        # - Telegram forum topics use message_thread_id; Hermes-created private
        #   DM topic lanes require both thread metadata and a reply anchor
        # - Feishu only honors reply_in_thread when sending a reply, so topic
        #   progress uses the triggering event message as the reply target
        # - Other platforms should use explicit source.thread_id only
        if source.platform == Platform.SLACK:
            _progress_thread_id = source.thread_id or event_message_id
        else:
            _progress_thread_id = source.thread_id
        _progress_metadata = (
            self._thread_metadata_for_source(source, event_message_id)
            if _progress_thread_id == source.thread_id
            else {"thread_id": _progress_thread_id}
        ) if _progress_thread_id else None
        _progress_reply_to = (
            event_message_id
            if source.platform in (Platform.FEISHU, Platform.MATTERMOST) and source.thread_id and event_message_id
            else None
        )

        async def send_progress_messages():
            if not progress_queue:
                return
            await send_agent_progress_messages(
                progress_queue,
                source=source,
                adapter=self.adapters.get(source.platform),
                progress_metadata=_progress_metadata,
                progress_reply_to=_progress_reply_to,
                cleanup_progress=_cleanup_progress,
                cleanup_message_ids=_cleanup_msg_ids,
                run_still_current=_run_still_current,
                agent_holder=agent_holder,
                last_progress_msg=last_progress_msg,
                repeat_count=repeat_count,
                log=logger,
            )

        # We need to share the agent instance for interrupt support
        agent_holder = [None]  # Mutable container for the agent instance
        result_holder = [None]  # Mutable container for the result
        tools_holder = [None]   # Mutable container for the tool definitions
        stream_consumer_holder = [None]  # Mutable container for stream consumer

        # Bridge sync step_callback → async hooks.emit for agent:step events
        _loop_for_step = asyncio.get_running_loop()
        _hooks_ref = self.hooks

        def _step_callback_sync(iteration: int, prev_tools: list) -> None:
            if not _run_still_current():
                return
            # prev_tools may be list[str] or list[dict] with "name"/"result"
            # keys.  Normalise to keep "tool_names" backward-compatible for
            # user-authored hooks that do ', '.join(tool_names)'.
            _names: list[str] = []
            for _t in (prev_tools or []):
                if isinstance(_t, dict):
                    _names.append(_t.get("name") or "")
                else:
                    _names.append(str(_t))
            safe_schedule_threadsafe(
                _hooks_ref.emit("agent:step", {
                    "platform": source.platform.value if source.platform else "",
                    "user_id": source.user_id,
                    "session_id": session_id,
                    "iteration": iteration,
                    "tool_names": _names,
                    "tools": prev_tools,
                }),
                _loop_for_step,
                logger=logger,
                log_message="agent:step hook scheduling error",
            )

        # Bridge sync status_callback → async adapter.send for context pressure
        _status_adapter = self.adapters.get(source.platform)
        _status_chat_id = source.chat_id
        if source.platform == Platform.FEISHU and source.thread_id and event_message_id:
            # Feishu topics only keep messages inside the topic when they are
            # sent via the reply API with reply_in_thread=true. Status/interim,
            # approval, and stream-consumer paths usually only receive metadata,
            # so carry the triggering message id as a Feishu-specific fallback.
            _status_thread_metadata: Optional[Dict[str, Any]] = {
                "thread_id": _progress_thread_id,
                "reply_to_message_id": event_message_id,
            }
        else:
            _status_thread_metadata = self._thread_metadata_for_source(source, event_message_id) if _progress_thread_id else None

        def _status_callback_sync(event_type: str, message: str) -> None:
            if not _status_adapter or not _run_still_current():
                return
            prepared_message = _prepare_gateway_status_message(
                source.platform,
                event_type,
                message,
            )
            if prepared_message is None:
                logger.debug(
                    "status_callback suppressed for %s/%s: %s",
                    source.platform.value if source.platform else "unknown",
                    event_type,
                    _redact_gateway_user_facing_secrets(str(message or ""))[:160],
                )
                return
            _fut = safe_schedule_threadsafe(
                _status_adapter.send(
                    _status_chat_id,
                    prepared_message,
                    metadata=_status_thread_metadata,
                ),
                _loop_for_step,
                logger=logger,
                log_message=f"status_callback ({event_type}) scheduling error",
            )
            if _fut is None:
                return
            if _cleanup_progress:
                def _track_status_id(fut) -> None:
                    try:
                        res = fut.result()
                    except Exception:
                        return
                    mid = getattr(res, "message_id", None)
                    if getattr(res, "success", False) and mid:
                        _cleanup_msg_ids.append(str(mid))
                _fut.add_done_callback(_track_status_id)

        def run_sync():
            # The conditional re-assignment of `message` further below
            # (prepending model-switch notes) makes Python treat it as a
            # local variable in the entire function.  `nonlocal` lets us
            # read *and* reassign the outer `_run_agent` parameter without
            # triggering an UnboundLocalError on the earlier read at
            # `_resolve_turn_agent_config(message, …)`.
            nonlocal message

            # session_key is now set via contextvars in _set_session_env()
            # (concurrency-safe). Keep os.environ as fallback for CLI/cron.
            os.environ["HERMES_SESSION_KEY"] = session_key or ""

            # Read from env var or use default (same as CLI)
            max_iterations = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))

            # Map platform enum to the platform hint key the agent understands.
            # Platform.LOCAL ("local") maps to "cli"; others pass through as-is.
            platform_key = "cli" if source.platform == Platform.LOCAL else source.platform.value

            # Combine platform context, per-channel context, and the user-configured
            # ephemeral system prompt.
            combined_ephemeral = context_prompt or ""
            event_channel_prompt = (channel_prompt or "").strip()
            if event_channel_prompt:
                combined_ephemeral = (combined_ephemeral + "\n\n" + event_channel_prompt).strip()
            if self._ephemeral_system_prompt:
                combined_ephemeral = (combined_ephemeral + "\n\n" + self._ephemeral_system_prompt).strip()

            # Re-read .env and config for fresh credentials (gateway is long-lived,
            # keys may change without restart). Keep config.yaml authoritative for
            # runtime budget settings bridged into env vars.
            _reload_runtime_env_preserving_config_authority()

            try:
                model, runtime_kwargs = self._resolve_session_agent_runtime(
                    source=source,
                    session_key=session_key,
                    user_config=user_config,
                )
                logger.debug(
                    "run_agent resolved: model=%s provider=%s session=%s",
                    model, runtime_kwargs.get("provider"), session_key or "",
                )
            except Exception as exc:
                return {
                    "final_response": f"⚠️ Provider authentication failed: {exc}",
                    "messages": [],
                    "api_calls": 0,
                    "tools": [],
                }

            pr = self._provider_routing
            reasoning_config = self._resolve_session_reasoning_config(
                source=source,
                session_key=session_key,
            )
            self._reasoning_config = reasoning_config
            self._service_tier = self._load_service_tier()
            # Set up stream consumer for token streaming or interim commentary.
            _stream_consumer = None
            _stream_delta_cb = None
            _scfg = getattr(getattr(self, 'config', None), 'streaming', None)
            if _scfg is None:
                from gateway.config import StreamingConfig
                _scfg = StreamingConfig()

            # Per-platform streaming gate: display.platforms.<plat>.streaming
            # can disable streaming for specific platforms even when the global
            # streaming config is enabled.
            _plat_streaming = resolve_display_setting(
                user_config, platform_key, "streaming"
            )
            # None = no per-platform override → follow global config
            _streaming_enabled = (
                _scfg.enabled and _scfg.transport != "off"
                if _plat_streaming is None
                else bool(_plat_streaming)
            )
            _want_stream_deltas = _streaming_enabled
            _want_interim_messages = interim_assistant_messages_enabled
            _want_interim_consumer = _want_interim_messages
            if _want_stream_deltas or _want_interim_consumer:
                try:
                    from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig
                    _adapter = self.adapters.get(source.platform)
                    if _adapter:
                        # Platforms that don't support editing sent messages
                        # (e.g. QQ, WeChat) should skip streaming entirely —
                        # without edit support, the consumer sends a partial
                        # first message that can never be updated, resulting in
                        # duplicate messages (partial + final).
                        _adapter_supports_edit = getattr(_adapter, "SUPPORTS_MESSAGE_EDITING", True)
                        if not _adapter_supports_edit:
                            raise RuntimeError("skip streaming for non-editable platform")
                        _effective_cursor = _scfg.cursor
                        # Some Matrix clients render the streaming cursor
                        # as a visible tofu/white-box artifact.  Keep
                        # streaming text on Matrix, but suppress the cursor.
                        _buffer_only = False
                        if source.platform == Platform.MATRIX:
                            _effective_cursor = ""
                            _buffer_only = True
                        # Fresh-final applies to Telegram only — other
                        # platforms either edit in place cheaply or don't
                        # have the edit-timestamp-stays-stale problem.
                        # (Ported from openclaw/openclaw#72038.)
                        _fresh_final_secs = (
                            float(getattr(_scfg, "fresh_final_after_seconds", 0.0) or 0.0)
                            if source.platform == Platform.TELEGRAM
                            else 0.0
                        )
                        _consumer_cfg = StreamConsumerConfig(
                            edit_interval=_scfg.edit_interval,
                            buffer_threshold=_scfg.buffer_threshold,
                            cursor=_effective_cursor,
                            buffer_only=_buffer_only,
                            fresh_final_after_seconds=_fresh_final_secs,
                            transport=_scfg.transport or "edit",
                            chat_type=getattr(source, "chat_type", "") or "",
                        )
                        _stream_consumer = GatewayStreamConsumer(
                            adapter=_adapter,
                            chat_id=source.chat_id,
                            config=_consumer_cfg,
                            metadata=_status_thread_metadata,
                            on_new_message=(
                                (lambda: progress_queue.put(("__reset__",)))
                                if progress_queue is not None
                                else None
                            ),
                            initial_reply_to_id=event_message_id,
                        )
                        if _want_stream_deltas:
                            def _stream_delta_cb(text: str) -> None:
                                if _run_still_current():
                                    _stream_consumer.on_delta(text)
                        stream_consumer_holder[0] = _stream_consumer
                except Exception as _sc_err:
                    logger.debug("Could not set up stream consumer: %s", _sc_err)

            def _interim_assistant_cb(text: str, *, already_streamed: bool = False) -> None:
                if not _run_still_current():
                    return
                if _stream_consumer is not None:
                    if already_streamed:
                        _stream_consumer.on_segment_break()
                    else:
                        _stream_consumer.on_commentary(text)
                    return
                if already_streamed or not _status_adapter or not str(text or "").strip():
                    return
                safe_schedule_threadsafe(
                    _status_adapter.send(
                        _status_chat_id,
                        text,
                        metadata=_status_thread_metadata,
                    ),
                    _loop_for_step,
                    logger=logger,
                    log_message="interim_assistant_callback scheduling error",
                )

            turn_route = self._resolve_turn_agent_config(message, model, runtime_kwargs)

            # Check agent cache — reuse the AIAgent from the previous message
            # in this session to preserve the frozen system prompt and tool
            # schemas for prompt cache hits.
            _sig = self._agent_config_signature(
                turn_route["model"],
                turn_route["runtime"],
                enabled_toolsets,
                combined_ephemeral,
                cache_keys=self._extract_cache_busting_config(user_config),
            )
            agent = None
            _cache_lock = getattr(self, "_agent_cache_lock", None)
            _cache = getattr(self, "_agent_cache", None)
            if _cache_lock and _cache is not None:
                with _cache_lock:
                    cached = _cache.get(session_key)
                    if cached and cached[1] == _sig:
                        agent = cached[0]
                        # Refresh LRU order so the cap enforcement evicts
                        # truly-oldest entries, not the one we just used.
                        if hasattr(_cache, "move_to_end"):
                            try:
                                _cache.move_to_end(session_key)
                            except KeyError:
                                pass
                        self._init_cached_agent_for_turn(agent, _interrupt_depth)
                        logger.debug("Reusing cached agent for session %s", session_key)

            if agent is None:
                # Config changed or first message — create fresh agent
                agent = AIAgent(
                    model=turn_route["model"],
                    **turn_route["runtime"],
                    max_iterations=max_iterations,
                    quiet_mode=True,
                    verbose_logging=False,
                    enabled_toolsets=enabled_toolsets,
                    disabled_toolsets=disabled_toolsets,
                    ephemeral_system_prompt=combined_ephemeral or None,
                    prefill_messages=self._prefill_messages or None,
                    reasoning_config=reasoning_config,
                    service_tier=self._service_tier,
                    request_overrides=turn_route.get("request_overrides"),
                    providers_allowed=pr.get("only"),
                    providers_ignored=pr.get("ignore"),
                    providers_order=pr.get("order"),
                    provider_sort=pr.get("sort"),
                    provider_require_parameters=pr.get("require_parameters", False),
                    provider_data_collection=pr.get("data_collection"),
                    session_id=session_id,
                    platform=platform_key,
                    user_id=source.user_id,
                    user_name=source.user_name,
                    chat_id=source.chat_id,
                    chat_name=source.chat_name,
                    chat_type=source.chat_type,
                    thread_id=source.thread_id,
                    gateway_session_key=session_key,
                    session_db=self._session_db,
                    fallback_model=self._fallback_model,
                )
                if _cache_lock and _cache is not None:
                    with _cache_lock:
                        _cache[session_key] = (agent, _sig)
                        self._enforce_agent_cache_cap()
                logger.debug("Created new agent for session %s (sig=%s)", session_key, _sig)

            # Per-message state — callbacks and reasoning config change every
            # turn and must not be baked into the cached agent constructor.
            agent.tool_progress_callback = progress_callback if tool_progress_enabled else None
            agent.step_callback = _step_callback_sync if _hooks_ref.loaded_hooks else None
            agent.stream_delta_callback = _stream_delta_cb
            agent.interim_assistant_callback = _interim_assistant_cb if _want_interim_messages else None
            agent.status_callback = _status_callback_sync
            agent.reasoning_config = reasoning_config
            agent.service_tier = self._service_tier
            agent.request_overrides = turn_route.get("request_overrides") or {}

            _bg_review_release = threading.Event()
            _bg_review_pending: list[str] = []
            _bg_review_pending_lock = threading.Lock()

            def _deliver_bg_review_message(message: str) -> None:
                if not _status_adapter or not _run_still_current():
                    return
                safe_schedule_threadsafe(
                    _status_adapter.send(
                        _status_chat_id,
                        message,
                        metadata=_status_thread_metadata,
                    ),
                    _loop_for_step,
                    logger=logger,
                    log_message="background_review_callback scheduling error",
                )

            def _release_bg_review_messages() -> None:
                _bg_review_release.set()
                with _bg_review_pending_lock:
                    pending = list(_bg_review_pending)
                    _bg_review_pending.clear()
                for queued in pending:
                    _deliver_bg_review_message(queued)

            # Background review delivery — send "💾 Memory updated" etc. to user
            def _bg_review_send(message: str) -> None:
                if not _status_adapter or not _run_still_current():
                    return
                if not _bg_review_release.is_set():
                    with _bg_review_pending_lock:
                        if not _bg_review_release.is_set():
                            _bg_review_pending.append(message)
                            return
                _deliver_bg_review_message(message)

            agent.background_review_callback = _bg_review_send
            # Register the release hook on the adapter so base.py's finally
            # block can fire it after delivering the main response.
            if _status_adapter and session_key:
                if getattr(type(_status_adapter), "register_post_delivery_callback", None) is not None:
                    _status_adapter.register_post_delivery_callback(
                        session_key,
                        _release_bg_review_messages,
                        generation=run_generation,
                    )
                else:
                    _pdc = getattr(_status_adapter, "_post_delivery_callbacks", None)
                    if _pdc is not None:
                        _pdc[session_key] = _release_bg_review_messages

            # ------------------------------------------------------------------
            # Clarify callback: present a clarify prompt and block on a response.
            #
            # Runs on the agent's worker thread (see clarify_tool's synchronous
            # callback contract).  Bridges sync→async by scheduling the
            # adapter's send_clarify on the gateway event loop, then blocks on
            # the clarify primitive's threading.Event with a configurable
            # timeout.  Returns the user's response string, or a sentinel
            # explaining that no response arrived (so the agent can adapt
            # rather than hang forever).
            # ------------------------------------------------------------------
            def _clarify_callback_sync(question: str, choices) -> str:
                from tools import clarify_gateway as _clarify_mod
                import uuid as _uuid

                if not _status_adapter:
                    return ""

                clarify_id = _uuid.uuid4().hex[:10]
                _clarify_mod.register(
                    clarify_id=clarify_id,
                    session_key=session_key or "",
                    question=question,
                    choices=list(choices) if choices else None,
                )

                # Pause typing — like approval, we don't want a "thinking..."
                # status to obscure the prompt or block the user from typing
                # an "Other" response on platforms that disable input while
                # typing is active (Slack Assistant API).
                try:
                    _status_adapter.pause_typing_for_chat(_status_chat_id)
                except Exception:
                    pass

                send_ok = False
                fut = safe_schedule_threadsafe(
                    _status_adapter.send_clarify(
                        chat_id=_status_chat_id,
                        question=question,
                        choices=list(choices) if choices else None,
                        clarify_id=clarify_id,
                        session_key=session_key or "",
                        metadata=_status_thread_metadata,
                    ),
                    _loop_for_step,
                    logger=logger,
                    log_message="Clarify send failed to schedule",
                )
                if fut is None:
                    send_ok = False
                else:
                    try:
                        result = fut.result(timeout=15)
                        send_ok = bool(getattr(result, "success", False))
                    except Exception as exc:
                        logger.warning("Clarify send failed: %s", exc)
                        send_ok = False

                if not send_ok:
                    # Couldn't deliver the prompt — clean up and return
                    # sentinel so the agent can fall back to a sensible
                    # default rather than hanging.
                    _clarify_mod.clear_session(session_key or "")
                    return "[clarify prompt could not be delivered]"

                timeout = _clarify_mod.get_clarify_timeout()
                response = _clarify_mod.wait_for_response(clarify_id, timeout=float(timeout))
                if response is None or response == "":
                    # Timeout or session-boundary cancellation
                    return f"[user did not respond within {int(timeout / 60)}m]"
                return response

            agent.clarify_callback = _clarify_callback_sync

            # Store agent reference for interrupt support
            agent_holder[0] = agent
            # Capture the full tool definitions for transcript logging
            tools_holder[0] = agent.tools if hasattr(agent, 'tools') else None

            # Convert history to agent format.
            # Two cases:
            #   1. Normal path (from transcript): simple {role, content, timestamp} dicts
            #      - Strip timestamps, keep role+content
            #   2. Interrupt path (from agent result["messages"]): full agent messages
            #      that may include tool_calls, tool_call_id, reasoning, etc.
            #      - These must be passed through intact so the API sees valid
            #        assistant→tool sequences (dropping tool_calls causes 500 errors)
            agent_history = []
            for msg in history:
                role = msg.get("role")
                if not role:
                    continue

                # Skip metadata entries (tool definitions, session info)
                # -- these are for transcript logging, not for the LLM
                if role in {"session_meta",}:
                    continue

                # Skip system messages -- the agent rebuilds its own system prompt
                if role == "system":
                    continue

                # Rich agent messages (tool_calls, tool results) must be passed
                # through intact so the API sees valid assistant→tool sequences
                has_tool_calls = "tool_calls" in msg
                has_tool_call_id = "tool_call_id" in msg
                is_tool_message = role == "tool"

                if has_tool_calls or has_tool_call_id or is_tool_message:
                    clean_msg = {k: v for k, v in msg.items() if k != "timestamp"}
                    agent_history.append(clean_msg)
                else:
                    # Simple text message - just need role and content
                    content = msg.get("content")
                    if content:
                        # Tag cross-platform mirror messages so the agent knows their origin
                        if msg.get("mirror"):
                            mirror_src = msg.get("mirror_source", "another session")
                            content = f"[Delivered from {mirror_src}] {content}"
                        # Preserve assistant reasoning + Codex replay fields so
                        # multi-turn reasoning context, prefix-cache hits, and
                        # provider-specific echo requirements survive session
                        # reload.  See ``_ASSISTANT_REPLAY_FIELDS`` for the full
                        # whitelist and rationale.
                        entry = _build_replay_entry(role, content, msg)
                        agent_history.append(entry)

            # Collect MEDIA paths already in history so we can exclude them
            # from the current turn's extraction. This is compression-safe:
            # even if the message list shrinks, we know which paths are old.
            _history_media_paths: set = set()
            for _hm in agent_history:
                if _hm.get("role") in {"tool", "function"}:
                    _hc = _hm.get("content", "")
                    if "MEDIA:" in _hc:
                        _TOOL_MEDIA_RE = re.compile(
                            r'MEDIA:((?:/|~\/)\S+\.(?:png|jpe?g|gif|webp|'
                            r'mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|'
                            r'flac|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|'
                            r'txt|csv|apk|ipa))',
                            re.IGNORECASE
                        )
                        for _match in _TOOL_MEDIA_RE.finditer(_hc):
                            _p = _match.group(1).strip().rstrip('",}')
                            if _p:
                                _history_media_paths.add(_p)

            # Register per-session gateway approval callback so dangerous
            # command approval blocks the agent thread (mirrors CLI input()).
            # The callback bridges sync→async to send the approval request
            # to the user immediately.
            from tools.approval import (
                register_gateway_notify,
                reset_current_session_key,
                set_current_session_key,
                unregister_gateway_notify,
            )

            def _approval_notify_sync(approval_data: dict) -> None:
                """Send the approval request to the user from the agent thread.

                If the adapter supports interactive button-based approvals
                (e.g. Discord's ``send_exec_approval``), use that for a richer
                UX.  Otherwise fall back to a plain text message with
                ``/approve`` instructions.
                """
                # Pause the typing indicator while the agent waits for
                # user approval.  Critical for Slack's Assistant API where
                # assistant_threads_setStatus disables the compose box — the
                # user literally cannot type /approve while "is thinking..."
                # is active.  The approval message send auto-clears the Slack
                # status; pausing prevents _keep_typing from re-setting it.
                # Typing resumes in _handle_approve_command/_handle_deny_command.
                _status_adapter.pause_typing_for_chat(_status_chat_id)

                cmd = approval_data.get("command", "")
                desc = approval_data.get("description", "dangerous command")

                # Prefer button-based approval when the adapter supports it.
                # Check the *class* for the method, not the instance — avoids
                # false positives from MagicMock auto-attribute creation in tests.
                if getattr(type(_status_adapter), "send_exec_approval", None) is not None:
                    try:
                        _approval_fut = safe_schedule_threadsafe(
                            _status_adapter.send_exec_approval(
                                chat_id=_status_chat_id,
                                command=cmd,
                                session_key=_approval_session_key,
                                description=desc,
                                metadata=_status_thread_metadata,
                            ),
                            _loop_for_step,
                            logger=logger,
                            log_message="send_exec_approval scheduling error",
                        )
                        if _approval_fut is None:
                            raise RuntimeError("send_exec_approval: loop unavailable")
                        _approval_result = _approval_fut.result(timeout=15)
                        if _approval_result.success:
                            return
                        logger.warning(
                            "Button-based approval failed (send returned error), falling back to text: %s",
                            _approval_result.error,
                        )
                    except Exception as _e:
                        logger.warning(
                            "Button-based approval failed, falling back to text: %s", _e
                        )

                # Fallback: plain text approval prompt
                cmd_preview = cmd[:200] + "..." if len(cmd) > 200 else cmd
                msg = (
                    f"⚠️ **Dangerous command requires approval:**\n"
                    f"```\n{cmd_preview}\n```\n"
                    f"Reason: {desc}\n\n"
                    f"Reply `/approve` to execute, `/approve session` to approve this pattern "
                    f"for the session, `/approve always` to approve permanently, or `/deny` to cancel."
                )
                try:
                    _approval_send_fut = safe_schedule_threadsafe(
                        _status_adapter.send(
                            _status_chat_id,
                            msg,
                            metadata=_status_thread_metadata,
                        ),
                        _loop_for_step,
                        logger=logger,
                        log_message="Approval text-send scheduling error",
                    )
                    if _approval_send_fut is not None:
                        _approval_send_fut.result(timeout=15)
                except Exception as _e:
                    logger.error("Failed to send approval request: %s", _e)

            # Prepend pending model switch note so the model knows about the switch
            _pending_notes = getattr(self, '_pending_model_notes', {})
            _msn = _pending_notes.pop(session_key, None) if session_key else None
            if _msn:
                message = _msn + "\n\n" + message

            # Auto-continue: if the loaded history ends with a tool result,
            # the previous agent turn was interrupted mid-work (gateway
            # restart, crash, SIGTERM).  Prepend a system note so the model
            # finishes processing the pending tool results before addressing
            # the user's new message.  (#4493)
            #
            # Session-level resume_pending (set on drain-timeout shutdown)
            # escalates the wording — the transcript's last role may be
            # anything (tool, assistant with unfinished work, etc.), so we
            # give a stronger, reason-aware instruction that subsumes the
            # tool-tail case.
            #
            # Freshness gate (#16802): both branches are gated on the age
            # of the last persisted transcript row.  That is the correct
            # "when did we last do anything here" signal for both the
            # resume_pending path (restart watchdog) and the tool-tail
            # path (in-flight tool loop killed).  We read ``history[-1]``
            # here because ``agent_history`` has already stripped the
            # ``timestamp`` field off tool/tool_call rows for API purity
            # (see the `k != "timestamp"` filter above).  Rows without a
            # timestamp (legacy transcripts) are treated as fresh so the
            # historical auto-continue behaviour is preserved.
            _freshness_window = _auto_continue_freshness_window()
            _interruption_is_fresh = _is_fresh_gateway_interruption(
                _last_transcript_timestamp(history),
                window_secs=_freshness_window,
            )

            _resume_entry = None
            if session_key:
                try:
                    _resume_entry = self.session_store._entries.get(session_key)
                except Exception:
                    _resume_entry = None
            _is_resume_pending = bool(
                _resume_entry is not None
                and getattr(_resume_entry, "resume_pending", False)
                and _interruption_is_fresh
            )
            _has_fresh_tool_tail = bool(
                agent_history
                and agent_history[-1].get("role") == "tool"
                and _interruption_is_fresh
            )

            if _is_resume_pending:
                _reason = getattr(_resume_entry, "resume_reason", None) or "restart_timeout"
                _reason_phrase = (
                    "a gateway restart"
                    if _reason == "restart_timeout"
                    else "a gateway shutdown"
                    if _reason == "shutdown_timeout"
                    else "a gateway interruption"
                )
                message = (
                    f"[System note: Your previous turn in this session was interrupted "
                    f"by {_reason_phrase}. The conversation history below is intact. "
                    f"If it contains unfinished tool result(s), process them first and "
                    f"summarize what was accomplished, then address the user's new "
                    f"message below.]\n\n"
                    + message
                )
            elif _has_fresh_tool_tail:
                message = (
                    "[System note: Your previous turn was interrupted before you could "
                    "process the last tool result(s). The conversation history contains "
                    "tool outputs you haven't responded to yet. Please finish processing "
                    "those results and summarize what was accomplished, then address the "
                    "user's new message below.]\n\n"
                    + message
                )

            # Consume one-shot /reload-skills note (if the user ran
            # /reload-skills since their last turn in this session). Same
            # queue pattern as CLI: prepend to the NEXT user message, then
            # clear. Nothing was written to the transcript out-of-band, so
            # message alternation stays intact.
            _pending_notes = getattr(self, "_pending_skills_reload_notes", None)
            if _pending_notes and session_key and session_key in _pending_notes:
                _srn = _pending_notes.pop(session_key, None)
                if _srn:
                    message = _srn + "\n\n" + message

            _approval_session_key = session_key or ""
            _approval_session_token = set_current_session_key(_approval_session_key)
            register_gateway_notify(_approval_session_key, _approval_notify_sync)
            try:
                # If _prepare_inbound_message_text buffered image paths for native
                # attachment, wrap the user turn as an OpenAI-style multimodal
                # content list. Consume-and-clear so subsequent turns on the same
                # runner instance don't re-attach stale images.
                _native_imgs = self._consume_pending_native_image_paths(session_key)
                if _native_imgs:
                    try:
                        from agent.image_routing import build_native_content_parts
                        _parts, _skipped = build_native_content_parts(
                            message,
                            _native_imgs,
                        )
                        if _skipped:
                            logger.warning(
                                "Native image attachment: skipped %d unreadable path(s): %s",
                                len(_skipped), _skipped,
                            )
                        if any(p.get("type") == "image_url" for p in _parts):
                            _run_message: Any = _parts
                        else:
                            # All images failed to read — fall back to plain text.
                            _run_message = message
                    except Exception as _img_exc:
                        logger.warning(
                            "Native image attachment failed, falling back to text: %s",
                            _img_exc,
                        )
                        _run_message = message
                else:
                    _run_message = message

                result = agent.run_conversation(_run_message, conversation_history=agent_history, task_id=session_id)
            finally:
                unregister_gateway_notify(_approval_session_key)
                # Cancel any pending clarify entries so blocked agent
                # threads don't hang past the end of the run (interrupt,
                # completion, gateway shutdown).  Idempotent.
                try:
                    from tools.clarify_gateway import clear_session as _clear_clarify_session
                    _clear_clarify_session(_approval_session_key)
                except Exception:
                    pass
                reset_current_session_key(_approval_session_token)
            result_holder[0] = result

            # Signal the stream consumer that the agent is done
            if _stream_consumer is not None:
                _stream_consumer.finish()

            # Return final response, or a message if something went wrong
            final_response = result.get("final_response")

            # Extract actual token counts from the agent instance used for this run
            _last_prompt_toks = 0
            _input_toks = 0
            _output_toks = 0
            _context_length = 0
            _agent = agent_holder[0]
            if _agent and hasattr(_agent, "context_compressor"):
                _last_prompt_toks = getattr(_agent.context_compressor, "last_prompt_tokens", 0)
                _input_toks = getattr(_agent, "session_prompt_tokens", 0)
                _output_toks = getattr(_agent, "session_completion_tokens", 0)
                _context_length = getattr(_agent.context_compressor, "context_length", 0) or 0
            _resolved_model = getattr(_agent, "model", None) if _agent else None

            if not final_response:
                error_msg = f"⚠️ {result['error']}" if result.get("error") else ""
                return {
                    "final_response": error_msg,
                    "messages": result.get("messages", []),
                    "api_calls": result.get("api_calls", 0),
                    "failed": result.get("failed", False),
                    "partial": result.get("partial", False),
                    "completed": result.get("completed"),
                    "interrupted": result.get("interrupted", False),
                    "interrupt_message": result.get("interrupt_message"),
                    "error": result.get("error"),
                    "compression_exhausted": result.get("compression_exhausted", False),
                    "tools": tools_holder[0] or [],
                    "history_offset": len(agent_history),
                    "last_prompt_tokens": _last_prompt_toks,
                    "input_tokens": _input_toks,
                    "output_tokens": _output_toks,
                    "model": _resolved_model,
                    "context_length": _context_length,
                }

            # Scan tool results for MEDIA:<path> tags that need to be delivered
            # as native audio/file attachments.  The TTS tool embeds MEDIA: tags
            # in its JSON response, but the model's final text reply usually
            # doesn't include them.  We collect unique tags from tool results and
            # append any that aren't already present in the final response, so the
            # adapter's extract_media() can find and deliver the files exactly once.
            #
            # Uses path-based deduplication against _history_media_paths (collected
            # before run_conversation) instead of index slicing. This is safe even
            # when context compression shrinks the message list. (Fixes #160)
            if "MEDIA:" not in final_response:
                media_tags = []
                has_voice_directive = False
                for msg in result.get("messages", []):
                    if msg.get("role") in {"tool", "function"}:
                        content = msg.get("content", "")
                        if "MEDIA:" in content:
                            _TOOL_MEDIA_RE = re.compile(
                                r'MEDIA:((?:/|~\/)\S+\.(?:png|jpe?g|gif|webp|'
                                r'mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|'
                                r'flac|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|'
                                r'txt|csv|apk|ipa))',
                                re.IGNORECASE
                            )
                            for match in _TOOL_MEDIA_RE.finditer(content):
                                path = match.group(1).strip().rstrip('",}')
                                if path and path not in _history_media_paths:
                                    media_tags.append(f"MEDIA:{path}")
                            if "[[audio_as_voice]]" in content:
                                has_voice_directive = True

                if media_tags:
                    seen = set()
                    unique_tags = []
                    for tag in media_tags:
                        if tag not in seen:
                            seen.add(tag)
                            unique_tags.append(tag)
                    if has_voice_directive:
                        unique_tags.insert(0, "[[audio_as_voice]]")
                    final_response = final_response + "\n" + "\n".join(unique_tags)

            # Sync session_id: the agent may have created a new session during
            # mid-run context compression (_compress_context splits sessions).
            # If so, update the session store entry so the NEXT message loads
            # the compressed transcript, not the stale pre-compression one.
            agent = agent_holder[0]
            _session_was_split = False
            if agent and session_key and hasattr(agent, 'session_id') and agent.session_id != session_id:
                _session_was_split = True
                logger.info(
                    "Session split detected: %s → %s (compression)",
                    session_id, agent.session_id,
                )
                entry = self.session_store._entries.get(session_key)
                if entry:
                    entry.session_id = agent.session_id
                    self.session_store._save()

                # If this is a Telegram DM and source.thread_id was lost during
                # the session split (synthetic / recovered event), restore it
                # from the binding so _thread_metadata_for_source produces the
                # correct message_thread_id instead of routing to the General
                # thread.  Failure here is non-fatal — we log and continue;
                # worst case the message lands in General, which is the
                # pre-fix behaviour.
                if (
                    getattr(source, "platform", None) == Platform.TELEGRAM
                    and getattr(source, "chat_type", None) == "dm"
                    and getattr(source, "thread_id", None) is None
                    and self._session_db is not None
                ):
                    try:
                        _binding = self._session_db.get_telegram_topic_binding_by_session(
                            session_id=agent.session_id,
                        )
                        if _binding and _binding.get("thread_id"):
                            source.thread_id = str(_binding["thread_id"])
                            logger.debug(
                                "Restored source.thread_id=%s from binding after session split %s → %s",
                                source.thread_id,
                                session_id,
                                agent.session_id,
                            )
                    except Exception:
                        logger.debug(
                            "Failed to restore thread_id from binding after session split",
                            exc_info=True,
                        )

            effective_session_id = getattr(agent, 'session_id', session_id) if agent else session_id

            # When compression created a new session, the messages list was
            # shortened.  Using the original history offset would produce an
            # empty new_messages slice, causing the gateway to write only a
            # user/assistant pair — losing the compressed summary and tail.
            # Reset to 0 so the gateway writes ALL compressed messages.
            _effective_history_offset = 0 if _session_was_split else len(agent_history)

            # Auto-generate session title after first exchange (non-blocking)
            if final_response and self._session_db:
                try:
                    from agent.title_generator import maybe_auto_title
                    all_msgs = result_holder[0].get("messages", []) if result_holder[0] else []
                    # In Gateway mode, auto-title failures must NOT be
                    # surfaced as user-visible messages (fixes #23246).
                    # Log them at debug level only — they are not actionable
                    # to the end user. CLI mode keeps the existing behaviour
                    # via the agent's _emit_auxiliary_failure path.
                    def _title_failure_cb(task: str, exc: BaseException) -> None:
                        logger.debug(
                            "Gateway auto-title failure suppressed (not user-visible): %s: %s",
                            task, exc,
                        )
                    maybe_auto_title_kwargs = {
                        "failure_callback": _title_failure_cb,
                        "main_runtime": {
                            "model": getattr(agent, "model", None),
                            "provider": getattr(agent, "provider", None),
                            "base_url": getattr(agent, "base_url", None),
                            "api_key": getattr(agent, "api_key", None),
                            "api_mode": getattr(agent, "api_mode", None),
                        } if agent else None,
                    }
                    if self._is_telegram_topic_lane(source):
                        maybe_auto_title_kwargs["title_callback"] = lambda title: self._schedule_telegram_topic_title_rename(
                            source,
                            effective_session_id,
                            title,
                        )
                    maybe_auto_title(
                        self._session_db,
                        effective_session_id,
                        message,
                        final_response,
                        all_msgs,
                        **maybe_auto_title_kwargs,
                    )
                except Exception:
                    pass

            return {
                "final_response": final_response,
                "last_reasoning": result.get("last_reasoning"),
                "messages": result_holder[0].get("messages", []) if result_holder[0] else [],
                "api_calls": result_holder[0].get("api_calls", 0) if result_holder[0] else 0,
                "completed": result_holder[0].get("completed") if result_holder[0] else None,
                "interrupted": result_holder[0].get("interrupted", False) if result_holder[0] else False,
                "partial": result_holder[0].get("partial", False) if result_holder[0] else False,
                "error": result_holder[0].get("error") if result_holder[0] else None,
                "interrupt_message": result_holder[0].get("interrupt_message") if result_holder[0] else None,
                "tools": tools_holder[0] or [],
                "history_offset": _effective_history_offset,
                "last_prompt_tokens": _last_prompt_toks,
                "input_tokens": _input_toks,
                "output_tokens": _output_toks,
                "model": _resolved_model,
                "context_length": _context_length,
                "session_id": effective_session_id,
                "response_previewed": result.get("response_previewed", False),
            }

        # Start progress message sender if enabled
        progress_task = None
        if tool_progress_enabled:
            progress_task = asyncio.create_task(send_progress_messages())

        # Start stream consumer task — polls for consumer creation since it
        # happens inside run_sync (thread pool) after the agent is constructed.
        stream_task = None

        async def _start_stream_consumer():
            """Wait for the stream consumer to be created, then run it."""
            for _ in range(200):  # Up to 10s wait
                if stream_consumer_holder[0] is not None:
                    await stream_consumer_holder[0].run()
                    return
                await asyncio.sleep(0.05)

        stream_task = asyncio.create_task(_start_stream_consumer())

        # Track this agent as running for this session (for interrupt support)
        # We do this in a callback after the agent is created
        async def track_agent():
            # Wait for agent to be created
            while agent_holder[0] is None:
                await asyncio.sleep(0.05)
            if not session_key:
                return
            # Only promote the sentinel to the real agent if this run is still
            # current.  If /stop or /new bumped the generation while we were
            # spinning up, leave the newer run's slot alone — we'll be
            # discarded by the stale-result check in _handle_message_with_agent.
            if run_generation is not None and not self._is_session_run_current(
                session_key, run_generation
            ):
                logger.info(
                    "Skipping stale agent promotion for %s — generation %s is no longer current",
                    session_key or "",
                    run_generation,
                )
                return
            self._running_agents[session_key] = agent_holder[0]
            if self._draining:
                self._update_runtime_status("draining")

        tracking_task = asyncio.create_task(track_agent())

        # Monitor for interrupts from the adapter (new messages arriving).
        # This is the PRIMARY interrupt path for regular text messages —
        # Level 1 (base.py) catches them before _handle_message() is reached,
        # so the Level 2 running_agent.interrupt() path never fires.
        # The inactivity poll loop below has a BACKUP check in case this
        # task dies (no error handling = silent death = lost interrupts).
        _interrupt_detected = asyncio.Event()  # shared with backup check

        async def monitor_for_interrupt():
            if not session_key:
                return

            while True:
                await asyncio.sleep(0.2)  # Check every 200ms
                try:
                    # Re-resolve adapter each iteration so reconnects don't
                    # leave us holding a stale reference.
                    _adapter = self.adapters.get(source.platform)
                    if not _adapter:
                        continue
                    # Check if adapter has a pending interrupt for this session.
                    # Must use session_key (build_session_key output) — NOT
                    # source.chat_id — because the adapter stores interrupt events
                    # under the full session key.
                    if hasattr(_adapter, 'has_pending_interrupt') and _adapter.has_pending_interrupt(session_key):
                        agent = agent_holder[0]
                        if agent:
                            # Peek at the pending message text WITHOUT consuming it.
                            # The message must remain in _pending_messages so the
                            # post-run dequeue at _dequeue_pending_event() can
                            # retrieve the full MessageEvent (with media metadata).
                            # If we pop here, a race exists: the agent may finish
                            # before checking _interrupt_requested, and the message
                            # is lost — neither the interrupt path nor the dequeue
                            # path finds it.
                            _peek_event = _adapter._pending_messages.get(session_key)
                            pending_text = _peek_event.text if _peek_event else None
                            logger.debug("Interrupt detected from adapter, signaling agent...")
                            agent.interrupt(pending_text)
                            _interrupt_detected.set()
                            break
                except asyncio.CancelledError:
                    raise
                except Exception as _mon_err:
                    logger.debug("monitor_for_interrupt error (will retry): %s", _mon_err)

        interrupt_monitor = asyncio.create_task(monitor_for_interrupt())

        # Periodic "still working" notifications for long-running tasks.
        # Fires every N seconds so the user knows the agent hasn't died.
        # Config: agent.gateway_notify_interval in config.yaml, or
        # HERMES_AGENT_NOTIFY_INTERVAL env var.  Default 180s (3 min).
        # 0 = disable notifications.
        _NOTIFY_INTERVAL_RAW = _float_env("HERMES_AGENT_NOTIFY_INTERVAL", 180)
        _NOTIFY_INTERVAL = _NOTIFY_INTERVAL_RAW if _NOTIFY_INTERVAL_RAW > 0 else None
        _notify_start = time.time()

        async def _notify_long_running():
            if _NOTIFY_INTERVAL is None:
                return  # Notifications disabled (gateway_notify_interval: 0)
            _notify_adapter = self.adapters.get(source.platform)
            if not _notify_adapter:
                return
            while True:
                await asyncio.sleep(_NOTIFY_INTERVAL)
                _elapsed_mins = int((time.time() - _notify_start) // 60)
                # Include agent activity context if available.
                _agent_ref = agent_holder[0]
                _status_detail = ""
                if _agent_ref and hasattr(_agent_ref, "get_activity_summary"):
                    try:
                        _a = _agent_ref.get_activity_summary()
                        _parts = [f"iteration {_a['api_call_count']}/{_a['max_iterations']}"]
                        if _a.get("current_tool"):
                            _parts.append(f"running: {_a['current_tool']}")
                        else:
                            _parts.append(_a.get("last_activity_desc", ""))
                        _status_detail = " — " + ", ".join(_parts)
                    except Exception:
                        pass
                try:
                    _notify_res = await _notify_adapter.send(
                        source.chat_id,
                        f"⏳ Still working... ({_elapsed_mins} min elapsed{_status_detail})",
                        metadata=_status_thread_metadata,
                    )
                    if (
                        _cleanup_progress
                        and getattr(_notify_res, "success", False)
                        and getattr(_notify_res, "message_id", None)
                    ):
                        _cleanup_msg_ids.append(str(_notify_res.message_id))
                except Exception as _ne:
                    logger.debug("Long-running notification error: %s", _ne)

        _notify_task = asyncio.create_task(_notify_long_running())

        try:
            # Run in thread pool to not block.  Use an *inactivity*-based
            # timeout instead of a wall-clock limit: the agent can run for
            # hours if it's actively calling tools / receiving stream tokens,
            # but a hung API call or stuck tool with no activity for the
            # configured duration is caught and killed.  (#4815)
            #
            # Config: agent.gateway_timeout in config.yaml, or
            # HERMES_AGENT_TIMEOUT env var (env var takes precedence).
            # Default 1800s (30 min inactivity).  0 = unlimited.
            _agent_timeout_raw = _float_env("HERMES_AGENT_TIMEOUT", 1800)
            _agent_timeout = _agent_timeout_raw if _agent_timeout_raw > 0 else None
            _agent_warning_raw = _float_env("HERMES_AGENT_TIMEOUT_WARNING", 900)
            _agent_warning = _agent_warning_raw if _agent_warning_raw > 0 else None
            _warning_fired = False
            _executor_task = asyncio.ensure_future(
                self._run_in_executor_with_context(run_sync)
            )

            _inactivity_timeout = False
            _POLL_INTERVAL = 5.0

            if _agent_timeout is None:
                # Unlimited — still poll periodically for backup interrupt
                # detection in case monitor_for_interrupt() silently died.
                response = None
                while True:
                    done, _ = await asyncio.wait(
                        {_executor_task}, timeout=_POLL_INTERVAL
                    )
                    if done:
                        response = _executor_task.result()
                        break
                    # Backup interrupt check: if the monitor task died or
                    # missed the interrupt, catch it here.
                    if not _interrupt_detected.is_set() and session_key:
                        _backup_adapter = self.adapters.get(source.platform)
                        _backup_agent = agent_holder[0]
                        if (_backup_adapter and _backup_agent
                                and hasattr(_backup_adapter, 'has_pending_interrupt')
                                and _backup_adapter.has_pending_interrupt(session_key)):
                            _bp_event = _backup_adapter._pending_messages.get(session_key)
                            _bp_text = _bp_event.text if _bp_event else None
                            logger.info(
                                "Backup interrupt detected for session %s "
                                "(monitor task state: %s)",
                                session_key,
                                "done" if interrupt_monitor.done() else "running",
                            )
                            _backup_agent.interrupt(_bp_text)
                            _interrupt_detected.set()
            else:
                # Poll loop: check the agent's built-in activity tracker
                # (updated by _touch_activity() on every tool call, API
                # call, and stream delta) every few seconds.
                response = None
                while True:
                    done, _ = await asyncio.wait(
                        {_executor_task}, timeout=_POLL_INTERVAL
                    )
                    if done:
                        response = _executor_task.result()
                        break
                    # Agent still running — check inactivity.
                    _agent_ref = agent_holder[0]
                    _idle_secs = 0.0
                    if _agent_ref and hasattr(_agent_ref, "get_activity_summary"):
                        try:
                            _act = _agent_ref.get_activity_summary()
                            _idle_secs = _act.get("seconds_since_activity", 0.0)
                        except Exception:
                            pass
                    # Staged warning: fire once before escalating to full timeout.
                    if (not _warning_fired and _agent_warning is not None
                            and _idle_secs >= _agent_warning):
                        _warning_fired = True
                        _warn_adapter = self.adapters.get(source.platform)
                        if _warn_adapter:
                            _elapsed_warn = int(_agent_warning // 60) or 1
                            _remaining_mins = int((_agent_timeout - _agent_warning) // 60) or 1
                            try:
                                await _warn_adapter.send(
                                    source.chat_id,
                                    f"⚠️ No activity for {_elapsed_warn} min. "
                                    f"If the agent does not respond soon, it will "
                                    f"be timed out in {_remaining_mins} min. "
                                    f"You can continue waiting or use /reset.",
                                    metadata=_status_thread_metadata,
                                )
                            except Exception as _warn_err:
                                logger.debug("Inactivity warning send error: %s", _warn_err)
                    if _idle_secs >= _agent_timeout:
                        _inactivity_timeout = True
                        break
                    # Backup interrupt check (same as unlimited path).
                    if not _interrupt_detected.is_set() and session_key:
                        _backup_adapter = self.adapters.get(source.platform)
                        _backup_agent = agent_holder[0]
                        if (_backup_adapter and _backup_agent
                                and hasattr(_backup_adapter, 'has_pending_interrupt')
                                and _backup_adapter.has_pending_interrupt(session_key)):
                            _bp_event = _backup_adapter._pending_messages.get(session_key)
                            _bp_text = _bp_event.text if _bp_event else None
                            logger.info(
                                "Backup interrupt detected for session %s "
                                "(monitor task state: %s)",
                                session_key,
                                "done" if interrupt_monitor.done() else "running",
                            )
                            _backup_agent.interrupt(_bp_text)
                            _interrupt_detected.set()

            if _inactivity_timeout:
                # Build a diagnostic summary from the agent's activity tracker.
                _timed_out_agent = agent_holder[0]
                _activity = {}
                if _timed_out_agent and hasattr(_timed_out_agent, "get_activity_summary"):
                    try:
                        _activity = _timed_out_agent.get_activity_summary()
                    except Exception:
                        pass

                _last_desc = _activity.get("last_activity_desc", "unknown")
                _secs_ago = _activity.get("seconds_since_activity", 0)
                _cur_tool = _activity.get("current_tool")
                _iter_n = _activity.get("api_call_count", 0)
                _iter_max = _activity.get("max_iterations", 0)

                logger.error(
                    "Agent idle for %.0fs (timeout %.0fs) in session %s "
                    "| last_activity=%s | iteration=%s/%s | tool=%s",
                    _secs_ago, _agent_timeout, session_key,
                    _last_desc, _iter_n, _iter_max,
                    _cur_tool or "none",
                )

                # Interrupt the agent if it's still running so the thread
                # pool worker is freed.
                if _timed_out_agent and hasattr(_timed_out_agent, "interrupt"):
                    _timed_out_agent.interrupt(_INTERRUPT_REASON_TIMEOUT)

                _timeout_mins = int(_agent_timeout // 60) or 1

                # Construct a user-facing message with diagnostic context.
                _diag_lines = [
                    f"⏱️ Agent inactive for {_timeout_mins} min — no tool calls "
                    f"or API responses."
                ]
                if _cur_tool:
                    _diag_lines.append(
                        f"The agent appears stuck on tool `{_cur_tool}` "
                        f"({_secs_ago:.0f}s since last activity, "
                        f"iteration {_iter_n}/{_iter_max})."
                    )
                else:
                    _diag_lines.append(
                        f"Last activity: {_last_desc} ({_secs_ago:.0f}s ago, "
                        f"iteration {_iter_n}/{_iter_max}). "
                        "The agent may have been waiting on an API response."
                    )
                _diag_lines.append(
                    "To increase the limit, set agent.gateway_timeout in config.yaml "
                    "(value in seconds, 0 = no limit) and restart the gateway.\n"
                    "Try again, or use /reset to start fresh."
                )

                response = {
                    "final_response": "\n".join(_diag_lines),
                    "messages": result_holder[0].get("messages", []) if result_holder[0] else [],
                    "api_calls": _iter_n,
                    "tools": tools_holder[0] or [],
                    "history_offset": 0,
                    "failed": True,
                }

            # Track fallback model state: if the agent switched to a
            # fallback model during this run, persist it so /model shows
            # the actually-active model instead of the config default.
            # Skip eviction when the run failed — evicting a failed agent
            # forces MCP reinit on the next message for no benefit (the
            # same error will recur).  This was the root cause of #7130:
            # a bad model ID triggered fallback → eviction → recreation →
            # MCP reinit → same 400 → loop, burning 91% CPU for hours.
            _agent = agent_holder[0]
            _result_for_fb = result_holder[0]
            _run_failed = _result_for_fb.get("failed") if _result_for_fb else False
            if _agent is not None and hasattr(_agent, 'model') and not _run_failed:
                _cfg_model = _resolve_gateway_model()
                if _agent.model != _cfg_model and not self._is_intentional_model_switch(session_key, _agent.model):
                    # Fallback activated on a successful run — evict cached
                    # agent so the next message retries the primary model.
                    self._evict_cached_agent(session_key)

            # Check if we were interrupted OR have a queued message (/queue).
            result = result_holder[0]
            adapter = self.adapters.get(source.platform)

            # Get pending message from adapter.
            # Use session_key (not source.chat_id) to match adapter's storage keys.
            pending_event = None
            pending = None
            if result and adapter and session_key:
                pending_event = _dequeue_pending_event(adapter, session_key)
                # /queue overflow: after consuming the adapter's "next-up"
                # slot, promote the next queued event into it so the
                # recursive run's drain will see it.  This keeps the slot
                # occupied for the full FIFO chain, which (a) preserves
                # order, and (b) causes any mid-chain /queue to correctly
                # route to overflow rather than jumping the queue.
                pending_event = self._promote_queued_event(session_key, adapter, pending_event)
                if result.get("interrupted") and not pending_event and result.get("interrupt_message"):
                    interrupt_message = result.get("interrupt_message")
                    if _is_control_interrupt_message(interrupt_message):
                        logger.info(
                            "Ignoring control interrupt message for session %s: %s",
                            session_key or "?",
                            interrupt_message,
                        )
                    else:
                        pending = interrupt_message
                elif pending_event:
                    pending = pending_event.text or _build_media_placeholder(pending_event)
                    logger.debug("Processing queued message after agent completion: '%s...'", pending[:40])

            # Leftover /steer: if a steer arrived after the last tool batch
            # (e.g. during the final API call), the agent couldn't inject it
            # and returned it in result["pending_steer"]. Deliver it as the
            # next user turn so it isn't silently dropped.
            if result and not pending and not pending_event:
                _leftover_steer = result.get("pending_steer")
                if _leftover_steer:
                    pending = _leftover_steer
                    logger.debug("Delivering leftover /steer as next turn: '%s...'", pending[:40])

            # Safety net: if the pending text is a slash command (e.g. "/stop",
            # "/new"), discard it — commands should never be passed to the agent
            # as user input.  The primary fix is in base.py (commands bypass the
            # active-session guard), but this catches edge cases where command
            # text leaks through the interrupt_message fallback.
            if pending and pending.strip().startswith("/"):
                _pending_parts = pending.strip().split(None, 1)
                _pending_cmd_word = _pending_parts[0][1:].lower() if _pending_parts else ""
                if _pending_cmd_word:
                    try:
                        from hermes_cli.commands import resolve_command as _rc_pending
                        if _rc_pending(_pending_cmd_word):
                            logger.info(
                                "Discarding command '/%s' from pending queue — "
                                "commands must not be passed as agent input",
                                _pending_cmd_word,
                            )
                            pending_event = None
                            pending = None
                    except Exception:
                        pass

            if self._draining and (pending_event or pending):
                logger.info(
                    "Discarding pending follow-up for session %s during gateway %s",
                    session_key or "?",
                    self._status_action_label(),
                )
                pending_event = None
                pending = None

            if pending_event or pending:
                logger.debug("Processing pending message: '%s...'", pending[:40])

                # Clear the adapter's interrupt event so the next _run_agent call
                # doesn't immediately re-trigger the interrupt before the new agent
                # even makes its first API call (this was causing an infinite loop).
                if adapter and hasattr(adapter, '_active_sessions') and session_key and session_key in adapter._active_sessions:
                    adapter._active_sessions[session_key].clear()

                # Cap recursion depth to prevent resource exhaustion when the
                # user sends multiple messages while the agent keeps failing. (#816)
                if _interrupt_depth >= self._MAX_INTERRUPT_DEPTH:
                    logger.warning(
                        "Interrupt recursion depth %d reached for session %s — "
                        "queueing message instead of recursing.",
                        _interrupt_depth, session_key,
                    )
                    adapter = self.adapters.get(source.platform)
                    if adapter and pending_event:
                        merge_pending_message_event(adapter._pending_messages, session_key, pending_event)
                    elif adapter and hasattr(adapter, 'queue_message'):
                        adapter.queue_message(session_key, pending)
                    return result_holder[0] or {"final_response": response, "messages": history}

                was_interrupted = result.get("interrupted")
                if not was_interrupted:
                    # Queued message after normal completion — deliver the first
                    # response before processing the queued follow-up.
                    # Skip if streaming already delivered it.
                    _sc = stream_consumer_holder[0]
                    if _sc and stream_task:
                        try:
                            await asyncio.wait_for(stream_task, timeout=5.0)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            stream_task.cancel()
                            try:
                                await stream_task
                            except asyncio.CancelledError:
                                pass
                        except Exception as e:
                            logger.debug("Stream consumer wait before queued message failed: %s", e)
                    _previewed = bool(result.get("response_previewed"))
                    _already_streamed = bool(
                        (_sc and getattr(_sc, "final_response_sent", False))
                        or _previewed
                        or (_sc and getattr(_sc, "final_content_delivered", False))
                    )
                    first_response = result.get("final_response", "")
                    if first_response and not _already_streamed:
                        try:
                            logger.info(
                                "Queued follow-up for session %s: final stream delivery not confirmed; sending first response before continuing.",
                                session_key or "?",
                            )
                            await adapter.send(
                                source.chat_id,
                                first_response,
                                metadata=_status_thread_metadata,
                            )
                        except Exception as e:
                            logger.warning("Failed to send first response before queued message: %s", e)
                    elif first_response:
                        logger.info(
                            "Queued follow-up for session %s: skipping resend because final streamed delivery was confirmed.",
                            session_key or "?",
                        )
                    # Release deferred bg-review notifications now that the
                    # first response has been delivered.  Pop from the
                    # adapter's callback dict (prevents double-fire in
                    # base.py's finally block) and call it.
                    if getattr(type(adapter), "pop_post_delivery_callback", None) is not None:
                        _bg_cb = adapter.pop_post_delivery_callback(
                            session_key,
                            generation=run_generation,
                        )
                        if callable(_bg_cb):
                            try:
                                _bg_result = _bg_cb()
                                if inspect.isawaitable(_bg_result):
                                    await _bg_result
                            except Exception:
                                pass
                    elif adapter and hasattr(adapter, "_post_delivery_callbacks"):
                        _bg_cb = adapter._post_delivery_callbacks.pop(session_key, None)
                        if callable(_bg_cb):
                            try:
                                _bg_result = _bg_cb()
                                if inspect.isawaitable(_bg_result):
                                    await _bg_result
                            except Exception:
                                pass
                # else: interrupted — discard the interrupted response ("Operation
                # interrupted." is just noise; the user already knows they sent a
                # new message).

                updated_history = result.get("messages", history)
                next_source = source
                next_message = pending
                next_message_id = None
                next_channel_prompt = None
                if pending_event is not None:
                    next_source = getattr(pending_event, "source", None) or source
                    if self._is_goal_continuation_event(pending_event) and not self._goal_still_active_for_session(session_id):
                        logger.info(
                            "Discarding stale goal continuation for session %s — goal is no longer active",
                            session_key or "?",
                        )
                        return result
                    next_message = await self._prepare_inbound_message_text(
                        event=pending_event,
                        source=next_source,
                        history=updated_history,
                    )
                    if next_message is None:
                        return result
                    next_message_id = self._reply_anchor_for_event(pending_event)
                    next_channel_prompt = getattr(pending_event, "channel_prompt", None)

                # Restart typing indicator so the user sees activity while
                # the follow-up turn runs.  The outer _process_message_background
                # typing task is still alive but may be stale.
                _followup_adapter = self.adapters.get(source.platform)
                if _followup_adapter:
                    try:
                        await _followup_adapter.send_typing(
                            source.chat_id,
                            metadata=_status_thread_metadata,
                        )
                    except Exception:
                        pass

                followup_result = await self._run_agent(
                    message=next_message,
                    context_prompt=context_prompt,
                    history=updated_history,
                    source=next_source,
                    session_id=session_id,
                    session_key=session_key,
                    run_generation=run_generation,
                    _interrupt_depth=_interrupt_depth + 1,
                    event_message_id=next_message_id,
                    channel_prompt=next_channel_prompt,
                )
                return _preserve_queued_followup_history_offset(result, followup_result)
        finally:
            # Stop progress sender, interrupt monitor, and notification task
            if progress_task:
                progress_task.cancel()
            interrupt_monitor.cancel()
            _notify_task.cancel()

            # Wait for stream consumer to finish its final edit
            if stream_task:
                # If the agent never created a stream consumer (e.g. non-
                # streaming code path, or a test stub returning synchronously)
                # there is nothing to flush — cancel immediately instead of
                # waiting out the 5s timeout on a task that's just polling for
                # a consumer that will never arrive.  This was a 5-second
                # cost per non-streaming test run.
                _has_stream_consumer = (
                    stream_consumer_holder
                    and stream_consumer_holder[0] is not None
                )
                if not _has_stream_consumer:
                    stream_task.cancel()
                    try:
                        await stream_task
                    except asyncio.CancelledError:
                        pass
                else:
                    try:
                        await asyncio.wait_for(stream_task, timeout=5.0)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass

            # Clean up tracking
            tracking_task.cancel()
            if session_key:
                # Only release the slot if this run's generation still owns
                # it.  A /stop or /new that bumped the generation while we
                # were unwinding has already installed its own state; this
                # guard prevents an old run from clobbering it on the way
                # out.
                self._release_running_agent_state(
                    session_key, run_generation=run_generation
                )
            if self._draining:
                self._update_runtime_status("draining")

            # Wait for cancelled tasks
            for task in [progress_task, interrupt_monitor, tracking_task, _notify_task]:
                if task:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        # If streaming already delivered the response, mark it so the
        # caller's send() is skipped (avoiding duplicate messages).
        # BUT: never suppress delivery when the agent failed — the error
        # message is new content the user hasn't seen, and it must reach
        # them even if streaming had sent earlier partial output.
        #
        # Also never suppress when the final response is "(empty)" — this
        # means the model failed to produce content after tool calls (common
        # with mimo-v2-pro, GLM-5, etc.).  The stream consumer may have
        # sent intermediate text ("Let me search for that…") alongside the
        # tool call, setting already_sent=True, but that text is NOT the
        # final answer.  Suppressing delivery here leaves the user staring
        # at silence.  (#10xxx — "agent stops after web search")
        _sc = stream_consumer_holder[0]
        if isinstance(response, dict) and not response.get("failed"):
            _final = response.get("final_response") or ""
            _is_empty_sentinel = not _final or _final == "(empty)"
            _streamed = bool(
                _sc and getattr(_sc, "final_response_sent", False)
            )
            # response_previewed means the interim_assistant_callback already
            # sent the final text via the adapter (non-streaming path).
            _previewed = bool(response.get("response_previewed"))
            _content_delivered = bool(
                _sc and getattr(_sc, "final_content_delivered", False)
            )
            if not _is_empty_sentinel and (_streamed or _previewed or _content_delivered):
                logger.info(
                    "Suppressing normal final send for session %s: final delivery already confirmed (streamed=%s previewed=%s content_delivered=%s).",
                    session_key or "?",
                    _streamed,
                    _previewed,
                    _content_delivered,
                )
                response["already_sent"] = True

        # Schedule deletion of tracked temporary progress bubbles after the
        # final response lands. Failed runs skip this so bubbles remain as
        # breadcrumbs for the user to see what work happened. Only fires on
        # adapters that support ``delete_message`` (see init above); failures
        # are swallowed — deletion is best-effort.
        if (
            _cleanup_progress
            and _cleanup_adapter is not None
            and _cleanup_msg_ids
            and session_key
            and isinstance(response, dict)
            and not response.get("failed")
            and hasattr(_cleanup_adapter, "register_post_delivery_callback")
        ):
            _ids_snapshot = list(_cleanup_msg_ids)
            _chat_id_snapshot = source.chat_id
            _adapter_snapshot = _cleanup_adapter
            _loop_snapshot = asyncio.get_running_loop()

            def _cleanup_temp_bubbles() -> None:
                async def _delete_all() -> None:
                    for _mid in _ids_snapshot:
                        try:
                            await _adapter_snapshot.delete_message(
                                _chat_id_snapshot, _mid
                            )
                        except Exception:
                            pass
                try:
                    safe_schedule_threadsafe(
                        _delete_all(), _loop_snapshot,
                        logger=logger,
                        log_message="Temp bubble cleanup scheduling error",
                    )
                except Exception:
                    pass

            try:
                _cleanup_adapter.register_post_delivery_callback(
                    session_key,
                    _cleanup_temp_bubbles,
                    generation=run_generation,
                )
            except Exception as _rpe:
                logger.debug("Post-delivery cleanup registration failed: %s", _rpe)

        return response


def _start_cron_ticker(stop_event: threading.Event, adapters=None, loop=None, interval: int = 60):
    """
    Background thread that ticks the cron scheduler at a regular interval.

    Runs inside the gateway process so cronjobs fire automatically without
    needing a separate `hermes cron daemon` or system cron entry.

    When ``adapters`` and ``loop`` are provided, passes them through to the
    cron delivery path so live adapters can be used for E2EE rooms.

    Also refreshes the channel directory every 5 minutes and prunes the
    image/audio/document cache + expired ``hermes debug share`` pastes
    once per hour.
    """
    from cron.scheduler import tick as cron_tick
    from gateway.platforms.base import cleanup_image_cache, cleanup_document_cache
    from hermes_cli.debug import _sweep_expired_pastes

    IMAGE_CACHE_EVERY = 60   # ticks — once per hour at default 60s interval
    CHANNEL_DIR_EVERY = 5    # ticks — every 5 minutes
    PASTE_SWEEP_EVERY = 60   # ticks — once per hour
    CURATOR_EVERY = 60       # ticks — poll hourly (inner gate handles the real cadence)

    logger.info("Cron ticker started (interval=%ds)", interval)
    tick_count = 0
    while not stop_event.is_set():
        try:
            cron_tick(verbose=False, adapters=adapters, loop=loop)
        except Exception as e:
            logger.debug("Cron tick error: %s", e)

        tick_count += 1

        if tick_count % CHANNEL_DIR_EVERY == 0 and adapters:
            try:
                from gateway.channel_directory import build_channel_directory
                if loop is not None:
                    # build_channel_directory is async (Slack web calls), and
                    # this ticker runs in a background thread. Schedule onto
                    # the gateway event loop and wait briefly for completion
                    # so refresh failures are still logged via the except.
                    fut = safe_schedule_threadsafe(
                        build_channel_directory(adapters), loop,
                        logger=logger,
                        log_message="Channel directory refresh scheduling error",
                    )
                    if fut is not None:
                        fut.result(timeout=30)
            except Exception as e:
                logger.debug("Channel directory refresh error: %s", e)

        if tick_count % IMAGE_CACHE_EVERY == 0:
            try:
                removed = cleanup_image_cache(max_age_hours=24)
                if removed:
                    logger.info("Image cache cleanup: removed %d stale file(s)", removed)
            except Exception as e:
                logger.debug("Image cache cleanup error: %s", e)
            try:
                removed = cleanup_document_cache(max_age_hours=24)
                if removed:
                    logger.info("Document cache cleanup: removed %d stale file(s)", removed)
            except Exception as e:
                logger.debug("Document cache cleanup error: %s", e)

        if tick_count % PASTE_SWEEP_EVERY == 0:
            try:
                deleted, remaining = _sweep_expired_pastes()
                if deleted:
                    logger.info(
                        "Paste sweep: deleted %d expired paste(s), %d pending",
                        deleted, remaining,
                    )
            except Exception as e:
                logger.debug("Paste sweep error: %s", e)

        # Curator — piggy-back on the existing cron ticker so long-running
        # gateways get weekly skill maintenance without needing restarts.
        # maybe_run_curator() is internally gated by config.interval_hours
        # (7 days by default), so CURATOR_EVERY is just the poll rate — the
        # real work only fires once per config interval.
        if tick_count % CURATOR_EVERY == 0:
            try:
                from agent.curator import maybe_run_curator
                maybe_run_curator(
                    idle_for_seconds=float("inf"),
                    on_summary=lambda msg: logger.info("curator: %s", msg),
                )
            except Exception as e:
                logger.debug("Curator tick error: %s", e)

        stop_event.wait(timeout=interval)
    logger.info("Cron ticker stopped")


async def start_gateway(config: Optional[GatewayConfig] = None, replace: bool = False, verbosity: Optional[int] = 0) -> bool:
    """
    Start the gateway and run until interrupted.

    This is the main entry point for running the gateway.
    Returns True if the gateway ran successfully, False if it failed to start.
    A False return causes a non-zero exit code so systemd can auto-restart.

    Args:
        config: Optional gateway configuration override.
        replace: If True, kill any existing gateway instance before starting.
                 Useful for systemd services to avoid restart-loop deadlocks
                 when the previous process hasn't fully exited yet.
    """
    # ── Duplicate-instance guard ──────────────────────────────────────
    # Prevent two gateways from running under the same HERMES_HOME.
    # The PID file is scoped to HERMES_HOME, so future multi-profile
    # setups (each profile using a distinct HERMES_HOME) will naturally
    # allow concurrent instances without tripping this guard.
    from gateway.status import (
        acquire_gateway_runtime_lock,
        get_running_pid,
        get_process_start_time,
        release_gateway_runtime_lock,
        remove_pid_file,
        terminate_pid,
    )
    existing_pid = get_running_pid()
    if existing_pid is not None and existing_pid != os.getpid():
        if replace:
            existing_start_time = get_process_start_time(existing_pid)
            logger.info(
                "Replacing existing gateway instance (PID %d) with --replace.",
                existing_pid,
            )
            # Record a takeover marker so the target's shutdown handler
            # recognises its SIGTERM as a planned takeover and exits 0
            # (rather than exit 1, which would trigger systemd's
            # Restart=on-failure and start a flap loop against us).
            # Best-effort — proceed even if the write fails.
            try:
                from gateway.status import write_takeover_marker
                write_takeover_marker(existing_pid)
            except Exception as e:
                logger.debug("Could not write takeover marker: %s", e)
            try:
                terminate_pid(existing_pid, force=False)
            except ProcessLookupError:
                pass  # Already gone
            except (PermissionError, OSError):
                logger.error(
                    "Permission denied killing PID %d. Cannot replace.",
                    existing_pid,
                )
                # Marker is scoped to a specific target; clean it up on
                # give-up so it doesn't grief an unrelated future shutdown.
                try:
                    from gateway.status import clear_takeover_marker
                    clear_takeover_marker()
                except Exception:
                    pass
                return False
            # Wait up to 10 seconds for the old process to exit.
            # ``os.kill(pid, 0)`` on Windows is NOT a no-op — use the
            # handle-based existence check instead.
            from gateway.status import _pid_exists
            for _ in range(20):
                if not _pid_exists(existing_pid):
                    break  # Process is gone
                time.sleep(0.5)
            else:
                # Still alive after 10s — force kill
                logger.warning(
                    "Old gateway (PID %d) did not exit after SIGTERM, sending SIGKILL.",
                    existing_pid,
                )
                try:
                    terminate_pid(existing_pid, force=True)
                    time.sleep(0.5)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            remove_pid_file()
            # remove_pid_file() is a no-op when the PID doesn't match.
            # Force-unlink to cover the old-process-crashed case.
            try:
                (get_hermes_home() / "gateway.pid").unlink(missing_ok=True)
            except Exception:
                pass
            # Clean up any takeover marker the old process didn't consume
            # (e.g. SIGKILL'd before its shutdown handler could read it).
            try:
                from gateway.status import clear_takeover_marker
                clear_takeover_marker()
            except Exception:
                pass
            # Also release all scoped locks left by the old process.
            # Stopped (Ctrl+Z) processes don't release locks on exit,
            # leaving stale lock files that block the new gateway from starting.
            try:
                from gateway.status import release_all_scoped_locks
                _released = release_all_scoped_locks(
                    owner_pid=existing_pid,
                    owner_start_time=existing_start_time,
                )
                if _released:
                    logger.info("Released %d stale scoped lock(s) from old gateway.", _released)
            except Exception:
                pass
        else:
            hermes_home = str(get_hermes_home())
            logger.error(
                "Another gateway instance is already running (PID %d, HERMES_HOME=%s). "
                "Use 'hermes gateway restart' to replace it, or 'hermes gateway stop' first.",
                existing_pid, hermes_home,
            )
            print(
                f"\n❌ Gateway already running (PID {existing_pid}).\n"
                f"   Use 'hermes gateway restart' to replace it,\n"
                f"   or 'hermes gateway stop' to kill it first.\n"
                f"   Or use 'hermes gateway run --replace' to auto-replace.\n"
            )
            return False

    # Sync bundled skills on gateway start (fast -- skips unchanged)
    try:
        from tools.skills_sync import sync_skills
        sync_skills(quiet=True)
    except Exception:
        pass

    # Centralized logging — agent.log (INFO+), errors.log (WARNING+),
    # and gateway.log (INFO+, gateway-component records only).
    # Idempotent, so repeated calls from AIAgent.__init__ won't duplicate.
    from hermes_logging import setup_logging
    setup_logging(hermes_home=_hermes_home, mode="gateway")

    # Periodic process memory usage logging (gateway only) — emits a
    # grep-friendly "[MEMORY] rss=...MB ..." line every N minutes so
    # slow leaks in the long-lived gateway process show up as a time
    # series in agent.log / gateway.log.  Ported from cline/cline#10343.
    # Controlled by the logging.memory_monitor section in config.yaml.
    try:
        from gateway import memory_monitor as _memory_monitor

        _mm_cfg = {}
        try:
            # config is loaded a few lines up; re-read the logging section
            # here so we pick up user overrides without coupling to local
            # variable names inside the start_gateway body.
            from hermes_cli.config import load_config as _load_cli_config

            _mm_cfg = (_load_cli_config() or {}).get("logging", {}).get("memory_monitor", {}) or {}
        except Exception:
            _mm_cfg = {}
        if _mm_cfg.get("enabled", True):
            try:
                _mm_interval = float(_mm_cfg.get("interval_seconds", 300))
            except (TypeError, ValueError):
                _mm_interval = 300.0
            _memory_monitor.start_memory_monitoring(interval_seconds=_mm_interval)
    except Exception as _mm_exc:
        logger.debug("Failed to start memory monitor: %s", _mm_exc)

    # Optional stderr handler — level driven by -v/-q flags on the CLI.
    # verbosity=None (-q/--quiet): no stderr output
    # verbosity=0    (default):    WARNING and above
    # verbosity=1    (-v):         INFO and above
    # verbosity=2+   (-vv/-vvv):   DEBUG
    if verbosity is not None:
        from agent.redact import RedactingFormatter

        _stderr_level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
        _stderr_handler = logging.StreamHandler()
        _stderr_handler.setLevel(_stderr_level)
        _stderr_handler.setFormatter(RedactingFormatter('%(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(_stderr_handler)
        # Lower root logger level if needed so DEBUG records can reach the handler
        if _stderr_level < logging.getLogger().level:
            logging.getLogger().setLevel(_stderr_level)

    runner = GatewayRunner(config)

    # Track whether an unexpected signal initiated the shutdown. When an
    # unexpected SIGTERM kills the gateway, we exit non-zero so service
    # managers can revive the process. Planned stop paths write a marker
    # before signalling us so they can exit cleanly instead.
    _signal_initiated_shutdown = False

    # Set up signal handlers
    def shutdown_signal_handler(received_signal=None):
        nonlocal _signal_initiated_shutdown
        # Planned --replace takeover check: when a sibling gateway is
        # taking over via --replace, it wrote a marker naming this PID
        # before sending SIGTERM. If present, treat the signal as a
        # planned shutdown and exit 0 so systemd's Restart=on-failure
        # doesn't revive us (which would flap-fight the replacer when
        # both services are enabled, e.g. hermes.service + hermes-
        # gateway.service from pre-rename installs).
        planned_takeover = False
        try:
            from gateway.status import consume_takeover_marker_for_self
            planned_takeover = consume_takeover_marker_for_self()
        except Exception as e:
            logger.debug("Takeover marker check failed: %s", e)

        # Planned stop check: service managers and `hermes gateway stop`
        # also send SIGTERM, which is indistinguishable from an unexpected
        # external kill unless the CLI marks it first. SIGINT comes from an
        # interactive Ctrl+C and is likewise an intentional foreground stop.
        planned_stop = False
        if received_signal == signal.SIGINT:
            planned_stop = True
        elif not planned_takeover:
            try:
                from gateway.status import consume_planned_stop_marker_for_self
                planned_stop = consume_planned_stop_marker_for_self()
            except Exception as e:
                logger.debug("Planned stop marker check failed: %s", e)

        # Fast (<10ms) snapshot of who's asking us to shut down — runs
        # synchronously inside the asyncio signal handler, so we keep it
        # purely stdlib + /proc reads, no subprocesses.  See PR #15826
        # (May 2026): the previous implementation called `ps aux` here
        # synchronously, blocking the event loop for up to 3s while
        # adapter teardown couldn't begin.
        try:
            from gateway.shutdown_forensics import (
                format_context_for_log,
                snapshot_shutdown_context,
                spawn_async_diagnostic,
            )
            _shutdown_ctx = snapshot_shutdown_context(received_signal)
        except Exception as _e:
            _shutdown_ctx = None
            logger.debug("snapshot_shutdown_context failed: %s", _e)

        if planned_takeover:
            logger.info(
                "Received %s as a planned --replace takeover — exiting cleanly",
                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM",
            )
        elif planned_stop:
            logger.info(
                "Received %s as a planned gateway stop — exiting cleanly",
                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM/SIGINT",
            )
        else:
            _signal_initiated_shutdown = True
            logger.info(
                "Received %s — initiating shutdown",
                _shutdown_ctx["signal"] if _shutdown_ctx else "SIGTERM/SIGINT",
            )

        # Always log who/what triggered the signal — most useful single
        # line when diagnosing "the gateway keeps dying" tickets.  Format
        # is one line, key=value, parent_cmdline last (often long).
        if _shutdown_ctx is not None:
            try:
                logger.warning(
                    "Shutdown context: %s", format_context_for_log(_shutdown_ctx)
                )
            except Exception as _e:
                logger.debug("format_context_for_log failed: %s", _e)

            # Spawn the heavyweight diagnostic (ps auxf, pstree, dmesg) in
            # a detached subprocess so it can finish writing to disk even
            # if our cgroup is being torn down.  Bounded by an internal
            # timeout; never blocks the event loop here.
            try:
                _diag_log = _hermes_home / "logs" / "gateway-shutdown-diag.log"
                spawn_async_diagnostic(
                    _diag_log, _shutdown_ctx["signal"], timeout_seconds=5.0
                )
            except Exception as _e:
                logger.debug("spawn_async_diagnostic failed: %s", _e)
        asyncio.create_task(runner.stop())

    def restart_signal_handler():
        runner.request_restart(detached=False, via_service=True)

    loop = asyncio.get_running_loop()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, shutdown_signal_handler, sig)  # windows-footgun: ok — wrapped in try/except NotImplementedError for Windows
            except NotImplementedError:
                pass
        if hasattr(signal, "SIGUSR1"):
            try:
                loop.add_signal_handler(signal.SIGUSR1, restart_signal_handler)  # windows-footgun: ok — POSIX signal, guarded by hasattr above + try/except NotImplementedError
            except NotImplementedError:
                pass
    else:
        logger.info("Skipping signal handlers (not running in main thread).")

    # Claim the PID file BEFORE bringing up any platform adapters.
    # This closes the --replace race window: two concurrent `gateway run
    # --replace` invocations both pass the termination-wait above, but
    # only the winner of the O_CREAT|O_EXCL race below will ever open
    # Telegram polling, Discord gateway sockets, etc. The loser exits
    # cleanly before touching any external service.
    import atexit
    from gateway.status import write_pid_file, remove_pid_file, get_running_pid
    _current_pid = get_running_pid()
    if _current_pid is not None and _current_pid != os.getpid():
        logger.error(
            "Another gateway instance (PID %d) started during our startup. "
            "Exiting to avoid double-running.", _current_pid
        )
        return False
    if not acquire_gateway_runtime_lock():
        logger.error(
            "Gateway runtime lock is already held by another instance. Exiting."
        )
        return False
    try:
        write_pid_file()
    except FileExistsError:
        release_gateway_runtime_lock()
        logger.error(
            "PID file race lost to another gateway instance. Exiting."
        )
        return False
    atexit.register(remove_pid_file)
    atexit.register(release_gateway_runtime_lock)

    # MCP tool discovery — run in an executor so the asyncio event loop
    # stays responsive even when a configured MCP server is slow or
    # unreachable.  discover_mcp_tools() uses a blocking 120s wait
    # internally; calling it from the loop thread would freeze platform
    # heartbeats (Discord shard, Telegram polling) until it returned.
    # See #16856.
    try:
        from tools.mcp_tool import discover_mcp_tools
        _loop = asyncio.get_running_loop()
        await _loop.run_in_executor(None, discover_mcp_tools)
    except Exception as e:
        logger.debug("MCP tool discovery failed: %s", e)

    # Start the gateway
    success = await runner.start()
    if not success:
        return False
    if runner.should_exit_cleanly:
        if runner.exit_reason:
            logger.error("Gateway exiting cleanly: %s", runner.exit_reason)
        return True

    # Start background cron ticker so scheduled jobs fire automatically.
    # Pass the event loop so cron delivery can use live adapters (E2EE support).
    cron_stop = threading.Event()
    cron_thread = threading.Thread(
        target=_start_cron_ticker,
        args=(cron_stop,),
        kwargs={"adapters": runner.adapters, "loop": asyncio.get_running_loop()},
        daemon=True,
        name="cron-ticker",
    )
    cron_thread.start()

    # Wait for shutdown
    await runner.wait_for_shutdown()

    if runner.should_exit_with_failure:
        if runner.exit_reason:
            logger.error("Gateway exiting with failure: %s", runner.exit_reason)
        return False

    # Stop cron ticker cleanly
    cron_stop.set()
    cron_thread.join(timeout=5)

    # Close MCP server connections
    try:
        from tools.mcp_tool import shutdown_mcp_servers
        shutdown_mcp_servers()
    except Exception:
        pass

    # Stop the periodic memory monitor (if it was started above).
    # This also emits one final "[MEMORY] shutdown rss=..." line so the
    # last RSS reading before gateway exit is always in the log.
    try:
        from gateway import memory_monitor as _memory_monitor

        _memory_monitor.stop_memory_monitoring(timeout=2.0)
    except Exception:
        pass

    if runner.exit_code is not None:
        raise SystemExit(runner.exit_code)

    # When an unexpected SIGTERM caused the shutdown and it wasn't a planned
    # restart (/restart, /update, SIGUSR1), exit non-zero so systemd's
    # Restart=on-failure revives the process.  This covers:
    #   - hermes update killing the gateway mid-work
    #   - External kill commands
    #   - WSL2/container runtime sending unexpected signals
    # `hermes gateway stop` and interactive Ctrl+C are handled above as
    # planned stops and should not trigger service-manager revival.
    if _signal_initiated_shutdown and not runner._restart_requested:
        logger.info(
            "Exiting with code 1 (signal-initiated shutdown without restart "
            "request) so systemd Restart=on-failure can revive the gateway."
        )
        return False  # → sys.exit(1) in the caller

    # When the gateway is restarting via the service manager (SIGUSR1 →
    # launchd_restart or /restart / /update commands), exit with code 75 so
    # that launchd's ``KeepAlive → SuccessfulExit → false`` policy treats
    # the exit as *unsuccessful* and relaunches the service.  This mirrors
    # the systemd ``RestartForceExitStatus=75`` convention already used by
    # the systemd unit template.
    if runner._restart_via_service:
        logger.info(
            "Exiting with code 75 (service-restart requested) so "
            "launchd KeepAlive relaunches the gateway."
        )
        raise SystemExit(75)

    return True


def main():
    """CLI entry point for the gateway."""
    # Force UTF-8 stdio on Windows — gateway logs and startup banner would
    # otherwise UnicodeEncodeError on cp1252 consoles.  No-op on POSIX.
    try:
        from hermes_cli.stdio import configure_windows_stdio
        configure_windows_stdio()
    except Exception:
        pass

    import argparse

    parser = argparse.ArgumentParser(description="Hermes Gateway - Multi-platform messaging")
    parser.add_argument("--config", "-c", help="Path to gateway config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    config = None
    if args.config:
        import yaml
        with open(args.config, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            config = GatewayConfig.from_dict(data)

    # Run the gateway - exit with code 1 if no platforms connected,
    # so systemd Restart=on-failure will retry on transient errors (e.g. DNS)
    success = asyncio.run(start_gateway(config))
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
