"""CLI configuration loading and persistence.

Extracted from the 14k-line ``cli.py`` god-file so the YAML parse / merge /
deep-merge / legacy-format-normalization logic is pure and unit-testable
without booting the prompt_toolkit TUI, loading a live ``~/.hermes``, or
touching the network. The decision logic (``default_cli_config``,
``resolve_config_path``, ``merge_file_config``) is side-effect free; the
public entry points orchestrate it with real I/O.

This module is the single owner of:
  * the built-in CLI default config tree,
  * resolving which config file wins (user vs project, ``--ignore-user-config``),
  * deep-merging a user/project YAML file onto the defaults, and
  * persisting a single dotted key back to the active config file.

``load_cli_config`` additionally bridges terminal/browser/auxiliary settings
to the ``TERMINAL_*`` / ``BROWSER_*`` / ``AUXILIARY_*`` environment variables
that downstream tools read — that env-bridging is part of its long-standing
contract, not an incidental side effect.

Public symbols:
  * ``load_cli_config()`` — load + merge + env-bridge, returns the config dict.
  * ``save_config_value()`` — atomic round-trip write of one dotted key.
  * ``default_cli_config()`` — fresh copy of the built-in defaults.
  * ``resolve_config_path()`` — pure user-vs-project path decision.
  * ``merge_file_config()`` — pure deep-merge of a file config onto defaults.

Dependency direction is one-way: this module must NOT import ``cli.py`` and
must not import prompt_toolkit. It has no import-time side effects beyond
``logger.debug``.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Placeholder cwd values that mean "resolve per-backend at runtime" rather than
# an explicit directory.
_CWD_PLACEHOLDERS = (".", "auto", "cwd")

# config_key → env var, for bridging terminal config to terminal_tool.
_TERMINAL_ENV_MAPPINGS = {
    "env_type": "TERMINAL_ENV",
    "cwd": "TERMINAL_CWD",
    "timeout": "TERMINAL_TIMEOUT",
    "lifetime_seconds": "TERMINAL_LIFETIME_SECONDS",
    "docker_image": "TERMINAL_DOCKER_IMAGE",
    "docker_forward_env": "TERMINAL_DOCKER_FORWARD_ENV",
    "singularity_image": "TERMINAL_SINGULARITY_IMAGE",
    "modal_image": "TERMINAL_MODAL_IMAGE",
    "daytona_image": "TERMINAL_DAYTONA_IMAGE",
    "vercel_runtime": "TERMINAL_VERCEL_RUNTIME",
    # SSH config
    "ssh_host": "TERMINAL_SSH_HOST",
    "ssh_user": "TERMINAL_SSH_USER",
    "ssh_port": "TERMINAL_SSH_PORT",
    "ssh_key": "TERMINAL_SSH_KEY",
    # Container resource config (docker, singularity, modal, daytona,
    # vercel_sandbox -- ignored for local/ssh)
    "container_cpu": "TERMINAL_CONTAINER_CPU",
    "container_memory": "TERMINAL_CONTAINER_MEMORY",
    "container_disk": "TERMINAL_CONTAINER_DISK",
    "container_persistent": "TERMINAL_CONTAINER_PERSISTENT",
    "docker_volumes": "TERMINAL_DOCKER_VOLUMES",
    "docker_env": "TERMINAL_DOCKER_ENV",
    "docker_mount_cwd_to_workspace": "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE",
    "docker_run_as_host_user": "TERMINAL_DOCKER_RUN_AS_HOST_USER",
    "sandbox_dir": "TERMINAL_SANDBOX_DIR",
    # Persistent shell (non-local backends)
    "persistent_shell": "TERMINAL_PERSISTENT_SHELL",
    # Sudo support (works with all backends)
    "sudo_password": "SUDO_PASSWORD",
}

_BROWSER_ENV_MAPPINGS = {
    "inactivity_timeout": "BROWSER_INACTIVITY_TIMEOUT",
}

# auxiliary task → {config key → env var}
_AUXILIARY_TASK_ENV = {
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


def default_cli_config() -> Dict[str, Any]:
    """Return a fresh, deep copy of the built-in CLI default config tree.

    A fresh copy each call keeps callers (and the deep-merge below) from
    mutating shared state.
    """
    return copy.deepcopy(_DEFAULTS)


def resolve_config_path(
    *,
    user_config_path: Path,
    project_config_path: Path,
    ignore_user_config: bool,
) -> Path:
    """Pick the config file that wins, mirroring the CLI lookup order.

    Use the user config (``{HERMES_HOME}/config.yaml``) when it exists, unless
    ``ignore_user_config`` is set (``hermes chat --ignore-user-config`` /
    ``HERMES_IGNORE_USER_CONFIG=1``), in which case the project-level
    ``cli-config.yaml`` is used as a fallback so defaults stay sensible.
    """
    if user_config_path.exists() and not ignore_user_config:
        return user_config_path
    return project_config_path


def merge_file_config(
    defaults: Dict[str, Any],
    file_config: Dict[str, Any],
    *,
    return_terminal_flag: bool = False,
):
    """Deep-merge a parsed config file onto ``defaults`` (mutates + returns it).

    Handles the historical format quirks:
      * ``model`` may be a string (new) or a dict (old); a dict that sets
        ``model`` but not ``default`` promotes ``model`` → ``default``.
      * root-level ``provider`` / ``base_url`` are used only as a fallback when
        ``model.provider`` / ``model.base_url`` aren't already set.
      * known dict sections are deep-merged (scalars overwrite); unknown
        top-level keys are carried over verbatim.
      * legacy root-level ``max_turns`` is copied to ``agent.max_turns`` when
        the nested key is absent.

    With ``return_terminal_flag=True`` returns ``(defaults, has_terminal)``
    where ``has_terminal`` indicates the file explicitly set a ``terminal``
    section — the caller uses that to decide whether config is authoritative
    over pre-existing ``.env`` terminal vars.
    """
    has_terminal = "terminal" in file_config

    # Handle model config - can be string (new format) or dict (old format)
    if "model" in file_config:
        if isinstance(file_config["model"], str):
            # New format: model is just a string, convert to dict structure
            defaults["model"]["default"] = file_config["model"]
        elif isinstance(file_config["model"], dict):
            # Old format: model is a dict with default/base_url
            defaults["model"].update(file_config["model"])
            # If the user config sets model.model but not model.default,
            # promote model.model to model.default so the user's explicit
            # choice isn't shadowed by the hardcoded default.  Without this,
            # profile configs that only set "model:" (not "default:") silently
            # fall back to claude-opus because the merge preserves the
            # hardcoded default and HermesCLI.__init__ checks "default" first.
            if "model" in file_config["model"] and "default" not in file_config["model"]:
                defaults["model"]["default"] = file_config["model"]["model"]

    # Legacy root-level provider/base_url fallback.
    # Some users (or old code) put provider: / base_url: at the
    # config root instead of inside the model: section.  These are
    # only used as a FALLBACK when model.provider / model.base_url
    # is not already set — never as an override.  The canonical
    # location is model.provider (written by `hermes model`).
    if not defaults["model"].get("provider"):
        root_provider = file_config.get("provider")
        if root_provider:
            defaults["model"]["provider"] = root_provider
    if not defaults["model"].get("base_url"):
        root_base_url = file_config.get("base_url")
        if root_base_url:
            defaults["model"]["base_url"] = root_base_url

    # Deep merge file_config into defaults.
    # First: merge keys that exist in both (deep-merge dicts, overwrite scalars)
    for key in defaults:
        if key == "model":
            continue  # Already handled above
        if key in file_config:
            if isinstance(defaults[key], dict) and isinstance(file_config[key], dict):
                defaults[key].update(file_config[key])
            else:
                defaults[key] = file_config[key]

    # Second: carry over keys from file_config that aren't in defaults
    # (e.g. platform_toolsets, provider_routing, memory, honcho, etc.)
    for key in file_config:
        if key not in defaults and key != "model":
            defaults[key] = file_config[key]

    # Handle legacy root-level max_turns (backwards compat) - copy to
    # agent.max_turns whenever the nested key is missing.
    agent_file_config = file_config.get("agent")
    if "max_turns" in file_config and not (
        isinstance(agent_file_config, dict)
        and agent_file_config.get("max_turns") is not None
    ):
        defaults["agent"]["max_turns"] = file_config["max_turns"]

    if return_terminal_flag:
        return defaults, has_terminal
    return defaults


def load_cli_config(hermes_home: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load CLI configuration from config files.

    Config lookup order:
    1. ~/.hermes/config.yaml (user config - preferred)
    2. ./cli-config.yaml (project config - fallback)

    Environment variables take precedence over config file values.
    Returns default values if no config file exists.

    If HERMES_IGNORE_USER_CONFIG=1 is set (via ``hermes chat --ignore-user-config``),
    the user config at ``~/.hermes/config.yaml`` is skipped entirely and only the
    built-in defaults plus the project-level ``cli-config.yaml`` (if any) are used.
    Credentials in ``.env`` are still loaded — this flag only suppresses
    behavioral/config settings.

    ``hermes_home`` defaults to ``get_hermes_home()`` (profile-aware); tests
    inject a tmp path so loading never touches a real ``~/.hermes``.
    """
    home = hermes_home if hermes_home is not None else get_hermes_home()

    # Check user config first ({HERMES_HOME}/config.yaml)
    user_config_path = Path(home) / "config.yaml"
    # cli-config.yaml lives at the repo root, one level up from hermes_cli/.
    project_config_path = Path(__file__).resolve().parent.parent / "cli-config.yaml"

    # --ignore-user-config: force-skip the user config.yaml (still honor project
    # config as a fallback so defaults stay sensible).
    ignore_user_config = os.environ.get("HERMES_IGNORE_USER_CONFIG") == "1"

    config_path = resolve_config_path(
        user_config_path=user_config_path,
        project_config_path=project_config_path,
        ignore_user_config=ignore_user_config,
    )

    # Default configuration
    defaults = default_cli_config()

    # Track whether the config file explicitly set terminal config.
    # When using defaults (no config file / no terminal section), we should NOT
    # overwrite env vars that were already set by .env -- only a user's config
    # file should be authoritative.
    _file_has_terminal_config = False

    # Load from file if exists
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}

            defaults, _file_has_terminal_config = merge_file_config(
                defaults, file_config, return_terminal_flag=True
            )
        except Exception as e:
            logger.warning("Failed to load cli-config.yaml: %s", e)

    # Expand ${ENV_VAR} references in config values before bridging to env vars.
    from hermes_cli.config import _expand_env_vars

    defaults = _expand_env_vars(defaults)

    # Apply terminal config to environment variables (so terminal_tool picks them up)
    terminal_config = defaults.get("terminal", {})

    # Normalize config key: the new config system (hermes_cli/config.py) and all
    # documentation use "backend", the legacy cli-config.yaml uses "env_type".
    # Accept both, with "backend" taking precedence (it's the documented key).
    if "backend" in terminal_config:
        terminal_config["env_type"] = terminal_config["backend"]

    # CWD resolution for CLI/TUI. The gateway has its own config bridge in
    # gateway/run.py but may lazily import cli.py (triggering this code).
    # Local backend: always os.getcwd(). Use `cd /dir && hermes` to control it.
    # Non-local with placeholder: pop so terminal_tool uses its per-backend default.
    # Non-local with explicit path: keep as-is.
    effective_backend = terminal_config.get("env_type", "local")

    if effective_backend == "local":
        terminal_config["cwd"] = os.getcwd()
        defaults["terminal"]["cwd"] = terminal_config["cwd"]
    elif terminal_config.get("cwd") in _CWD_PLACEHOLDERS:
        terminal_config.pop("cwd", None)

    # Bridge config → env vars for terminal_tool. TERMINAL_CWD is force-exported
    # UNLESS we're inside a gateway process (detected by _HERMES_GATEWAY marker)
    # where it was already set correctly by gateway/run.py's config bridge.
    _is_gateway = os.environ.get("_HERMES_GATEWAY") == "1"
    for config_key, env_var in _TERMINAL_ENV_MAPPINGS.items():
        if config_key in terminal_config:
            if env_var == "TERMINAL_CWD":
                if _is_gateway:
                    continue
                # CLI: always export (overrides stale .env or inherited values)
                os.environ[env_var] = str(terminal_config[config_key])
                continue
            if _file_has_terminal_config or env_var not in os.environ:
                val = terminal_config[config_key]
                if isinstance(val, (list, dict)):
                    os.environ[env_var] = json.dumps(val)
                else:
                    os.environ[env_var] = str(val)

    # Apply browser config to environment variables
    browser_config = defaults.get("browser", {})
    for config_key, env_var in _BROWSER_ENV_MAPPINGS.items():
        if config_key in browser_config:
            os.environ[env_var] = str(browser_config[config_key])

    # Apply auxiliary model/direct-endpoint overrides to environment variables.
    # Vision and web_extract each have their own provider/model/base_url/api_key tuple.
    # Compression config is read directly from config.yaml by run_agent.py and
    # auxiliary_client.py — no env var bridging needed.
    # Only set env vars for non-empty / non-default values so auto-detection
    # still works.
    auxiliary_config = defaults.get("auxiliary", {})
    for task_key, env_map in _AUXILIARY_TASK_ENV.items():
        task_cfg = auxiliary_config.get(task_key, {})
        if not isinstance(task_cfg, dict):
            continue
        prov = str(task_cfg.get("provider", "")).strip()
        model = str(task_cfg.get("model", "")).strip()
        base_url = str(task_cfg.get("base_url", "")).strip()
        api_key = str(task_cfg.get("api_key", "")).strip()
        if prov and prov != "auto":
            os.environ[env_map["provider"]] = prov
        if model:
            os.environ[env_map["model"]] = model
        if base_url:
            os.environ[env_map["base_url"]] = base_url
        if api_key:
            os.environ[env_map["api_key"]] = api_key

    # Security settings
    security_config = defaults.get("security", {})
    if isinstance(security_config, dict):
        redact = security_config.get("redact_secrets")
        if redact is not None:
            os.environ["HERMES_REDACT_SECRETS"] = str(redact).lower()

    return defaults


def save_config_value(key_path: str, value: Any, hermes_home: Optional[Path] = None) -> bool:
    """
    Save a value to the active config file at the specified key path.

    Respects the same lookup order as load_cli_config():
    1. ~/.hermes/config.yaml (user config - preferred, used if it exists)
    2. ./cli-config.yaml (project config - fallback)

    Args:
        key_path: Dot-separated path like "agent.system_prompt"
        value: Value to save
        hermes_home: Override for the HERMES_HOME directory (defaults to
            ``get_hermes_home()``); tests inject a tmp path.

    Returns:
        True if successful, False otherwise
    """
    home = hermes_home if hermes_home is not None else get_hermes_home()

    # Use the same precedence as load_cli_config: user config first, then project config
    user_config_path = Path(home) / "config.yaml"
    project_config_path = Path(__file__).resolve().parent.parent / "cli-config.yaml"
    config_path = user_config_path if user_config_path.exists() else project_config_path

    try:
        # Ensure parent directory exists (for ~/.hermes/config.yaml on first use)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Save back atomically while preserving comments, ordering, quotes, and
        # readable Unicode in user-edited config.yaml.
        from utils import atomic_roundtrip_yaml_update

        atomic_roundtrip_yaml_update(config_path, key_path, value)

        # Enforce owner-only permissions on config files (contain API keys)
        try:
            os.chmod(config_path, 0o600)
        except (OSError, NotImplementedError):
            pass

        return True
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        return False


# Built-in CLI default config tree. Kept module-private; callers get fresh
# deep copies via ``default_cli_config()`` so nothing mutates this template.
_DEFAULTS: Dict[str, Any] = {
    "model": {
        "default": "",
        "base_url": "",
        "provider": "auto",
    },
    "terminal": {
        "env_type": "local",
        "cwd": ".",  # "." is resolved to os.getcwd() at runtime
        "timeout": 60,
        "lifetime_seconds": 300,
        "docker_image": "nikolaik/python-nodejs:python3.11-nodejs20",
        "docker_forward_env": [],
        "singularity_image": "docker://nikolaik/python-nodejs:python3.11-nodejs20",
        "modal_image": "nikolaik/python-nodejs:python3.11-nodejs20",
        "daytona_image": "nikolaik/python-nodejs:python3.11-nodejs20",
        "docker_volumes": [],  # host:container volume mounts for Docker backend
        "docker_mount_cwd_to_workspace": False,  # explicit opt-in only; default off for sandbox isolation
    },
    "browser": {
        "inactivity_timeout": 120,  # Auto-cleanup inactive browser sessions after 2 min
        "record_sessions": False,  # Auto-record browser sessions as WebM videos
        "engine": "auto",  # Browser engine: auto (Chrome), lightpanda, chrome
    },
    "compression": {
        "enabled": True,      # Auto-compress when approaching context limit
        "threshold": 0.50,    # Compress at 50% of model's context limit
    },
    "agent": {
        "max_turns": 90,  # Default max tool-calling iterations (shared with subagents)
        "verbose": False,
        "system_prompt": "",
        "prefill_messages_file": "",
        "reasoning_effort": "",
        "service_tier": "",
        "personalities": {
            "helpful": "You are a helpful, friendly AI assistant.",
            "concise": "You are a concise assistant. Keep responses brief and to the point.",
            "technical": "You are a technical expert. Provide detailed, accurate technical information.",
            "creative": "You are a creative assistant. Think outside the box and offer innovative solutions.",
            "teacher": "You are a patient teacher. Explain concepts clearly with examples.",
            "kawaii": "You are a kawaii assistant! Use cute expressions like (◕‿◕), ★, ♪, and ~! Add sparkles and be super enthusiastic about everything! Every response should feel warm and adorable desu~! ヽ(>∀<☆)ノ",
            "catgirl": "You are Neko-chan, an anime catgirl AI assistant, nya~! Add 'nya' and cat-like expressions to your speech. Use kaomoji like (=^･ω･^=) and ฅ^•ﻌ•^ฅ. Be playful and curious like a cat, nya~!",
            "pirate": "Arrr! Ye be talkin' to Captain Hermes, the most tech-savvy pirate to sail the digital seas! Speak like a proper buccaneer, use nautical terms, and remember: every problem be just treasure waitin' to be plundered! Yo ho ho!",
            "shakespeare": "Hark! Thou speakest with an assistant most versed in the bardic arts. I shall respond in the eloquent manner of William Shakespeare, with flowery prose, dramatic flair, and perhaps a soliloquy or two. What light through yonder terminal breaks?",
            "surfer": "Duuude! You're chatting with the chillest AI on the web, bro! Everything's gonna be totally rad. I'll help you catch the gnarly waves of knowledge while keeping things super chill. Cowabunga!",
            "noir": "The rain hammered against the terminal like regrets on a guilty conscience. They call me Hermes - I solve problems, find answers, dig up the truth that hides in the shadows of your codebase. In this city of silicon and secrets, everyone's got something to hide. What's your story, pal?",
            "uwu": "hewwo! i'm your fwiendwy assistant uwu~ i wiww twy my best to hewp you! *nuzzles your code* OwO what's this? wet me take a wook! i pwomise to be vewy hewpful >w<",
            "philosopher": "Greetings, seeker of wisdom. I am an assistant who contemplates the deeper meaning behind every query. Let us examine not just the 'how' but the 'why' of your questions. Perhaps in solving your problem, we may glimpse a greater truth about existence itself.",
            "hype": "YOOO LET'S GOOOO!!! I am SO PUMPED to help you today! Every question is AMAZING and we're gonna CRUSH IT together! This is gonna be LEGENDARY! ARE YOU READY?! LET'S DO THIS!",
        },
    },

    "display": {
        "compact": False,
        "resume_display": "full",
        "show_reasoning": False,
        "streaming": True,
        "busy_input_mode": "interrupt",
        "persistent_output": True,
        "persistent_output_max_lines": 200,

        "skin": "default",
    },
    "clarify": {
        "timeout": 120,  # Seconds to wait for a clarify answer before auto-proceeding
    },
    "code_execution": {
        "timeout": 300,    # Max seconds a sandbox script can run before being killed (5 min)
        "max_tool_calls": 50,  # Max RPC tool calls per execution
    },
    "auxiliary": {
        "vision": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
        },
        "web_extract": {
            "provider": "auto",
            "model": "",
            "base_url": "",
            "api_key": "",
        },
    },
    "delegation": {
        "max_iterations": 45,  # Max tool-calling turns per child agent
        "model": "",       # Subagent model override (empty = inherit parent model)
        "provider": "",    # Subagent provider override (empty = inherit parent provider)
        "base_url": "",    # Direct OpenAI-compatible endpoint for subagents
        "api_key": "",     # API key for delegation.base_url (falls back to OPENAI_API_KEY)
    },
    "onboarding": {
        # First-touch hint flags (see agent/onboarding.py).  Each hint is
        # shown once per install then latched here.
        "seen": {},
    },
}
