"""hermes chat command."""

def cmd_chat(args):
    """Run interactive chat CLI."""
    from hermes_cli.main import _resolve_session_by_name_or_id, _resolve_last_session
    use_tui = getattr(args, "tui", False) or os.environ.get("HERMES_TUI") == "1"

    # Resolve --continue into --resume with the latest session or by name
    continue_val = getattr(args, "continue_last", None)
    if continue_val and not getattr(args, "resume", None):
        if isinstance(continue_val, str):
            # -c "session name" — resolve by title or ID
            resolved = _resolve_session_by_name_or_id(continue_val)
            if resolved:
                args.resume = resolved
            else:
                print(f"No session found matching '{continue_val}'.")
                print("Use 'hermes sessions list' to see available sessions.")
                sys.exit(1)
        else:
            # -c with no argument — continue the most recent session
            source = "tui" if use_tui else "cli"
            last_id = _resolve_last_session(source=source)
            if not last_id and source == "tui":
                last_id = _resolve_last_session(source="cli")
            if last_id:
                args.resume = last_id
            else:
                kind = "TUI" if use_tui else "CLI"
                print(f"No previous {kind} session found to continue.")
                sys.exit(1)

    # Resolve --resume by title if it's not a direct session ID
    resume_val = getattr(args, "resume", None)
    if resume_val:
        resolved = _resolve_session_by_name_or_id(resume_val)
        if resolved:
            args.resume = resolved
        # If resolution fails, keep the original value — _init_agent will
        # report "Session not found" with the original input

    # xAI retirement warning — one-shot, non-blocking, never fails startup
    try:
        from hermes_cli.xai_retirement import (
            MIGRATION_GUIDE_URL,
            RETIREMENT_DATE,
            find_retired_xai_refs,
            format_issue,
        )
        from hermes_cli.config import load_config as _load_config_for_xai_check

        _retired_xai_refs = find_retired_xai_refs(_load_config_for_xai_check())
        if _retired_xai_refs:
            sys.stderr.write(
                f"\033[33m⚠ xAI retires {len(_retired_xai_refs)} model(s) "
                f"in your config on {RETIREMENT_DATE}:\033[0m\n"
            )
            for _ref in _retired_xai_refs:
                sys.stderr.write(f"  \033[33m⚠\033[0m {format_issue(_ref)}\n")
            sys.stderr.write(f"  \033[2mMigration guide: {MIGRATION_GUIDE_URL}\033[0m\n")
            sys.stderr.write("  \033[2mRun 'hermes doctor' for details.\033[0m\n\n")
    except Exception:
        pass

    # First-run guard: check if any provider is configured before launching
    if not _has_any_provider_configured():
        print()
        print(
            "It looks like Hermes isn't configured yet -- no API keys or providers found."
        )
        print()
        print("  Run:  hermes setup")
        print()

        from hermes_cli.setup import (
            is_interactive_stdin,
            print_noninteractive_setup_guidance,
        )

        if not is_interactive_stdin():
            print_noninteractive_setup_guidance(
                "No interactive TTY detected for the first-run setup prompt."
            )
            sys.exit(1)

        try:
            reply = input("Run setup now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            reply = "n"
        if reply in {"", "y", "yes"}:
            cmd_setup(args)
            return
        print()
        print("You can run 'hermes setup' at any time to configure.")
        sys.exit(1)

    # Start update check in background (runs while other init happens).
    # On Termux this imports rich/prompt_toolkit in the foreground and then
    # competes for CPU on single-core devices, so keep it opt-in there.
    if _termux_should_prefetch_update_check():
        try:
            from hermes_cli.banner import prefetch_update_check

            prefetch_update_check()
        except Exception:
            pass

    # Sync bundled skills on every CLI launch (fast -- skips unchanged skills)
    try:
        _sync_bundled_skills_for_startup()
    except Exception:
        pass

    # --yolo: bypass all dangerous command approvals
    if getattr(args, "yolo", False):
        os.environ["HERMES_YOLO_MODE"] = "1"

    # --ignore-user-config: make load_cli_config() / load_config() skip the
    # user's ~/.hermes/config.yaml and return built-in defaults. Set BEFORE
    # importing cli (which runs `CLI_CONFIG = load_cli_config()` at module
    # import time). Credentials in .env are still loaded — this flag only
    # ignores behavioral/config settings.
    if getattr(args, "ignore_user_config", False):
        os.environ["HERMES_IGNORE_USER_CONFIG"] = "1"

    # --ignore-rules: skip auto-injection of AGENTS.md/SOUL.md/.cursorrules
    # (rules), memory entries, and any preloaded skills coming from user config.
    # Maps to AIAgent(skip_context_files=True, skip_memory=True).
    if getattr(args, "ignore_rules", False):
        os.environ["HERMES_IGNORE_RULES"] = "1"

    # --source: tag session source for filtering (e.g. 'tool' for third-party integrations)
    if getattr(args, "source", None):
        os.environ["HERMES_SESSION_SOURCE"] = args.source

    _pin_kanban_board_env()

    if use_tui:
        _launch_tui(
            getattr(args, "resume", None),
            tui_dev=getattr(args, "tui_dev", False),
            model=getattr(args, "model", None),
            provider=getattr(args, "provider", None),
            toolsets=getattr(args, "toolsets", None),
            skills=getattr(args, "skills", None),
            verbose=getattr(args, "verbose", False),
            quiet=getattr(args, "quiet", False),
            query=getattr(args, "query", None),
            image=getattr(args, "image", None),
            worktree=getattr(args, "worktree", False),
            checkpoints=getattr(args, "checkpoints", False),
            pass_session_id=getattr(args, "pass_session_id", False),
            max_turns=getattr(args, "max_turns", None),
            accept_hooks=getattr(args, "accept_hooks", False),
        )

    # Import and run the CLI
    from cli import main as cli_main

    # Build kwargs from args
    kwargs = {
        "model": args.model,
        "provider": getattr(args, "provider", None),
        "toolsets": args.toolsets,
        "skills": getattr(args, "skills", None),
        "verbose": args.verbose,
        "quiet": getattr(args, "quiet", False),
        "query": args.query,
        "image": getattr(args, "image", None),
        "resume": getattr(args, "resume", None),
        "worktree": getattr(args, "worktree", False),
        "checkpoints": getattr(args, "checkpoints", False),
        "pass_session_id": getattr(args, "pass_session_id", False),
        "max_turns": getattr(args, "max_turns", None),
        "ignore_rules": getattr(args, "ignore_rules", False),
        "ignore_user_config": getattr(args, "ignore_user_config", False),
        "compact": getattr(args, "compact", False),
    }
    # Filter out None values
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    try:
        cli_main(**kwargs)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)



