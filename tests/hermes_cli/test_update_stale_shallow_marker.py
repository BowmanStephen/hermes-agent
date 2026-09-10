"""A stale ``.git/shallow`` entry must not turn the update fetches into ``--depth 1``.

``git rev-parse --is-shallow-repository`` is "true" whenever ``.git/shallow`` lists any commit,
including one left behind by an old ``fetch --depth`` of some other ref while the full history
behind HEAD is intact. Both fetch paths trusted that flag, and a ``fetch --depth 1`` on such a
repo adds the fetched tip as a boundary: an up-to-date full clone collapses to a single commit.
"""
import os
import subprocess

import pytest

from hermes_cli import banner


def _git(*args, cwd):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    done = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *map(str, args)],
        cwd=cwd, env=env, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _commit_count(repo):
    return int(_git("rev-list", "--count", "HEAD", cwd=repo))


@pytest.fixture
def origin(tmp_path):
    """Bare-ish origin: four commits on ``main`` plus a side branch whose tip is not on main."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    for i in range(3):
        _git("commit", "-q", "--allow-empty", "-m", f"c{i}", cwd=repo)
    _git("checkout", "-q", "-b", "old", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "old tip", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)
    _git("commit", "-q", "--allow-empty", "-m", "c3", cwd=repo)
    return repo


@pytest.fixture
def stale_marked_clone(origin, tmp_path):
    """Full clone of ``origin`` whose ``.git/shallow`` names a commit unrelated to HEAD."""
    clone = tmp_path / "clone"
    _git("clone", "-q", origin.as_uri(), clone, cwd=tmp_path)
    (clone / ".git" / "shallow").write_text(_git("rev-parse", "old", cwd=origin) + "\n", encoding="utf-8")
    assert _git("rev-parse", "--is-shallow-repository", cwd=clone) == "true"
    assert _commit_count(clone) == 4
    return clone


@pytest.fixture
def shallow_clone(origin, tmp_path):
    clone = tmp_path / "shallow"
    _git("clone", "-q", "--depth", "1", origin.as_uri(), clone, cwd=tmp_path)
    assert _commit_count(clone) == 1
    return clone


def _spy_fetches(monkeypatch, module, name):
    fetches = []
    real = getattr(module, name)

    def spy(*args, **kwargs):
        argv = next(a for a in args if isinstance(a, list) and a and a[0] != "git")
        if argv[0] == "fetch":
            fetches.append(argv)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return fetches


def test_banner_check_keeps_history_behind_stale_shallow_marker(stale_marked_clone, monkeypatch):
    fetches = _spy_fetches(monkeypatch, banner, "_git_ok")

    assert banner._check_via_local_git(stale_marked_clone) == 0

    assert fetches and all("--depth" not in argv for argv in fetches), fetches
    assert _commit_count(stale_marked_clone) == 4, "depth fetch truncated a full clone"


def test_banner_check_still_depth_fetches_a_genuine_shallow_clone(shallow_clone, monkeypatch):
    fetches = _spy_fetches(monkeypatch, banner, "_git_ok")

    assert banner._check_via_local_git(shallow_clone) == 0

    assert fetches and all("--depth" in argv for argv in fetches), fetches
    assert _commit_count(shallow_clone) == 1


def test_update_check_keeps_history_behind_stale_shallow_marker(stale_marked_clone, origin, monkeypatch, capsys):
    from hermes_cli import main, update_cmd

    _git("commit", "-q", "--allow-empty", "-m", "c4", cwd=origin)
    monkeypatch.setattr(main, "PROJECT_ROOT", stale_marked_clone)
    fetches = _spy_fetches(monkeypatch, update_cmd, "_git_run")

    assert update_cmd._is_shallow_checkout(["git"]) is False
    update_cmd._cmd_update_check()

    assert "1 commit behind origin/main" in capsys.readouterr().out
    assert fetches and all("--depth" not in argv for argv in fetches), fetches
    assert _commit_count(stale_marked_clone) == 4


def test_update_check_still_treats_a_genuine_shallow_clone_as_shallow(shallow_clone, monkeypatch):
    from hermes_cli import main, update_cmd

    monkeypatch.setattr(main, "PROJECT_ROOT", shallow_clone)
    assert update_cmd._is_shallow_checkout(["git"]) is True
