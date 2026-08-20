"""Regression cover for the real-checkout git guard in conftest.

A full ``tests/hermes_cli`` run on 2026-08-19 left the working checkout
shallow: a depth-limited fetch ran against it and ``git log`` collapsed
from 23,926 commits to 1. The guard makes that a loud test failure instead
of silent damage; these tests pin its behaviour in both directions."""
import subprocess
from pathlib import Path

import pytest

REPO = str(Path(__file__).resolve().parents[1])


def test_shallow_fetch_against_real_checkout_is_refused():
    """The exact shape that truncated history to one commit."""
    with pytest.raises(RuntimeError, match="real_repo_git_guard"):
        subprocess.run(
            ["git", "fetch", "origin", "main", "--depth", "1"],
            cwd=REPO, capture_output=True,
        )


def test_dash_c_form_is_also_refused():
    with pytest.raises(RuntimeError, match="real_repo_git_guard"):
        subprocess.run(
            ["git", "-C", REPO, "reset", "--hard", "HEAD~1"], capture_output=True
        )


def test_readonly_calls_against_the_checkout_still_work():
    out = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert out.stdout.strip() in {"true", "false"}


def test_git_init_into_tmp_is_allowed(tmp_path):
    dest = tmp_path / "repo"
    out = subprocess.run(
        ["git", "init", "-b", "main", str(dest)], capture_output=True
    )
    assert out.returncode == 0
    assert (dest / ".git").is_dir()


@pytest.mark.live_system_guard_bypass
def test_bypass_marker_disables_the_guard():
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
    )
    assert out.returncode == 0
