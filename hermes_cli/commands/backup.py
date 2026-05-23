"""hermes backup command."""

def cmd_backup(args):
    """Back up Hermes home directory to a zip file."""
    if getattr(args, "quick", False):
        from hermes_cli.backup import run_quick_backup

        run_quick_backup(args)
    else:
        from hermes_cli.backup import run_backup

        run_backup(args)



