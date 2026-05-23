"""hermes setup command."""

def cmd_setup(args):
    """Interactive setup wizard."""
    from hermes_cli.setup import run_setup_wizard

    run_setup_wizard(args)



