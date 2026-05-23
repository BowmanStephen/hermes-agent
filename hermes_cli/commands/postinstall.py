"""hermes postinstall command."""

def cmd_postinstall(args):
    """One-shot bootstrap for pip users: install non-Python deps + run setup."""
    from hermes_cli.config import stamp_install_method
    from hermes_cli.dep_ensure import ensure_dependency

    stamp_install_method("pip")

    print("⚕ Hermes post-install bootstrap")
    print()

    for dep in ("node", "browser", "ripgrep", "ffmpeg"):
        ensure_dependency(dep)

    if not _has_any_provider_configured():
        print()
        cmd_setup(args)
    else:
        print()
        print("✓ Post-install complete.")



