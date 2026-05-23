"""hermes import_cmd command."""

def cmd_import(args):
    """Restore a Hermes backup from a zip file."""
    from hermes_cli.backup import run_import

    run_import(args)



