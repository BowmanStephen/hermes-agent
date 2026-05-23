"""hermes doctor command."""


def cmd_doctor(args):
    """Check configuration and dependencies."""
    from hermes_cli.doctor import run_doctor

    run_doctor(args)


