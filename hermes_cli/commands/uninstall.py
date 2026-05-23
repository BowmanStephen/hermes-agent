"""hermes uninstall command."""

def cmd_uninstall(args):
    """Uninstall Hermes Agent."""
    from hermes_cli.main import _require_tty
    _require_tty("uninstall")
    from hermes_cli.uninstall import run_uninstall

    run_uninstall(args)



