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

    import hermes_cli

    # Drop the cached module so the import below really re-executes, and let
    # monkeypatch put it back at teardown: both the sys.modules entry and the
    # ``hermes_cli.main`` package attribute the fresh import rebinds, so
    # ``from hermes_cli import main`` and ``import hermes_cli.main`` keep
    # naming the same object. A bare ``del`` would leave every later test in
    # the session holding a stale module while a different object sits in
    # sys.modules, so their patches land on an object the code under test
    # never sees.
    cached = sys.modules.get('hermes_cli.main')
    if cached is not None:
        monkeypatch.delitem(sys.modules, 'hermes_cli.main')
        monkeypatch.setattr(hermes_cli, 'main', cached, raising=False)

    try:
        import hermes_cli.main  # noqa: F401
    except argparse.ArgumentError as e:
        if "conflicting subparser" in str(e):
            raise AssertionError(
                f"Duplicate subparser detected: {e}. "
                "See issue #898 for details."
            ) from e
        raise
