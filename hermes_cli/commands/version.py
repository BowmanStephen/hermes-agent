"""hermes version command."""


def cmd_version(args):
    """Show version."""
    # Lazy import to avoid circular dependency
    from hermes_cli.main import _print_version_info
    _print_version_info(check_updates=True)
