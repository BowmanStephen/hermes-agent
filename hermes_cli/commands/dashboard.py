"""hermes dashboard command."""

def cmd_dashboard(args):
    """Start the web UI server, or (with --stop/--status) manage running ones."""
    # --status: report running dashboards and exit, no deps needed.
    if getattr(args, "status", False):
        count = _report_dashboard_status()
        sys.exit(0 if count == 0 else 0)  # status is informational, always 0

    # --stop: kill any running dashboards and exit, no deps needed.
    if getattr(args, "stop", False):
        pids = _find_stale_dashboard_pids()
        if not pids:
            print("No hermes dashboard processes running.")
            sys.exit(0)
        # Reuse the same SIGTERM-grace-SIGKILL path used after `hermes update`.
        _kill_stale_dashboard_processes(reason="requested via --stop")
        # _kill_stale_dashboard_processes prints outcomes itself.  Exit 0 if
        # we killed at least one, 1 if they were all unkillable.
        remaining = _find_stale_dashboard_pids()
        sys.exit(1 if remaining else 0)

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as e:
        print("Web UI dependencies not installed (need fastapi + uvicorn).")
        print(
            f"Re-install the package into this interpreter so metadata updates apply:\n"
            f"  cd {PROJECT_ROOT}\n"
            f"  {sys.executable} -m pip install -e .\n"
            "If `pip` is missing in this venv, use:  uv pip install -e ."
        )
        print(f"Import error: {e}")
        sys.exit(1)

    if "HERMES_WEB_DIST" not in os.environ and not getattr(args, "skip_build", False):
        if not _build_web_ui(PROJECT_ROOT / "web", fatal=True):
            sys.exit(1)
    elif getattr(args, "skip_build", False):
        # --skip-build trusts the caller to have pre-built the web UI.
        # Verify the dist actually exists; otherwise the server will start
        # and serve 404s with no obvious cause (issue #23817).
        _dist_root = (
            Path(os.environ["HERMES_WEB_DIST"])
            if "HERMES_WEB_DIST" in os.environ
            else PROJECT_ROOT / "hermes_cli" / "web_dist"
        )
        if not (_dist_root / "index.html").exists():
            print(f"✗ --skip-build was passed but no web dist found at: {_dist_root}")
            print("  Pre-build first:  cd web && npm install && npm run build")
            print("  Or drop --skip-build to build automatically.")
            sys.exit(1)
        print(f"→ Skipping web UI build (--skip-build); using dist at {_dist_root}")

    from hermes_cli.web_server import start_server

    embedded_chat = args.tui or os.environ.get("HERMES_DASHBOARD_TUI") == "1"
    start_server(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        allow_public=getattr(args, "insecure", False),
        embedded_chat=embedded_chat,
    )



