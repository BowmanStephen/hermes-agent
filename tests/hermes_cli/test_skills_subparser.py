"""Test that skills subparser doesn't conflict (regression test for #898)."""

import argparse


def test_no_duplicate_skills_subparser(monkeypatch):
    """Ensure 'skills' subparser is only registered once to avoid Python 3.11+ crash.

    Python 3.11 changed argparse to raise an exception on duplicate subparser
    names instead of silently overwriting (see CPython #94331).

    This test will fail with:
        argparse.ArgumentError: argument command: conflicting subparser: skills

    if the duplicate 'skills' registration is reintroduced.
    """
    # Force fresh import of the module where parser is constructed
    # If there are duplicate 'skills' subparsers, this import will raise
    # argparse.ArgumentError at module load time
    import sys

    # Drop the cached module so the import below really re-executes, and put
    # BOTH bindings back at teardown. sys.modules is only half of it: the
    # re-import also rebinds `main` as an attribute on the `hermes_cli`
    # package, and that is what `from hermes_cli import main` resolves.
    # Restoring sys.modules alone left `update_cmd._m()` — which uses exactly
    # that form — handing every later test the throwaway module, whose
    # PROJECT_ROOT no test had patched. That was worth ~40 failures across
    # the update suites, all of which passed in isolation.
    import hermes_cli

    monkeypatch.delitem(sys.modules, 'hermes_cli.main', raising=False)
    if hasattr(hermes_cli, 'main'):
        monkeypatch.setattr(hermes_cli, 'main', hermes_cli.main, raising=False)

    try:
        import hermes_cli.main  # noqa: F401
    except argparse.ArgumentError as e:
        if "conflicting subparser" in str(e):
            raise AssertionError(
                f"Duplicate subparser detected: {e}. "
                "See issue #898 for details."
            ) from e
        raise
