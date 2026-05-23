"""hermes dump command."""


def cmd_dump(args):
    """Dump setup summary for support/debugging."""
    from hermes_cli.dump import run_dump

    run_dump(args)


