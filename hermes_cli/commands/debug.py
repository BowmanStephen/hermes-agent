"""hermes debug command."""


def cmd_debug(args):
    """Debug tools (share report, etc.)."""
    from hermes_cli.debug import run_debug

    run_debug(args)


