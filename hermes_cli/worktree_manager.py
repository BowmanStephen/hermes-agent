"""Ephemeral git-worktree lifecycle.

Extracted from ``cli.py`` so the *destructive* decision logic — which branches
to delete, which worktrees are stale, whether a path escapes the repo root — is
pure and unit-testable without a real repo, real ``git``, or filesystem
mutation. The git I/O lives behind :class:`GitRunner`; ``cli.py`` delegates to
these helpers.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal, Optional, Protocol, Sequence, Set

StaleDecision = Literal["skip", "remove", "force"]


@dataclass(frozen=True)
class GitResult:
    """Outcome of one git invocation."""

    returncode: int
    stdout: str = ""


class GitRunner(Protocol):
    """Seam over git execution. Real impl shells out; tests pass a fake.

    Implementations must not raise on git failure — return a non-zero
    ``GitResult`` instead, so callers treat failure as "can't verify".
    """

    def run(self, args: Sequence[str], *, cwd: str, timeout: int) -> GitResult: ...


class SubprocessGitRunner:
    """Real :class:`GitRunner` — shells out to ``git``.

    Never raises: a subprocess failure (timeout, missing git, bad cwd) becomes
    a non-zero ``GitResult`` so callers treat it as "couldn't verify".
    """

    def run(self, args: Sequence[str], *, cwd: Optional[str] = None, timeout: int = 10) -> GitResult:
        try:
            proc = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            return GitResult(returncode=proc.returncode, stdout=proc.stdout)
        except Exception:
            return GitResult(returncode=-1, stdout="")

# Local auto-generated branches eligible for orphan pruning.
_ORPHAN_PREFIXES = ("hermes/hermes-", "pr-")


def path_is_within_root(path: Path, root: Path) -> bool:
    """Return True when ``path`` stays within ``root`` (component-wise).

    Safety guard for worktree file operations: rejects paths outside the repo
    root and sibling-prefix escapes (``/repo-evil`` is not within ``/repo``).
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def select_orphaned_branches(
    all_branches: List[str],
    active_branches: Set[str],
) -> List[str]:
    """Pick auto-generated branches safe to delete: ``hermes/hermes-*`` and
    ``pr-*`` that are not checked out in any worktree (``active_branches``,
    which the caller seeds with worktree branches, HEAD, and ``main``).

    Order is preserved from ``all_branches``.
    """
    return [
        b
        for b in all_branches
        if b not in active_branches and b.startswith(_ORPHAN_PREFIXES)
    ]


def classify_stale_worktree(
    *,
    mtime: float,
    now: float,
    max_age_hours: int,
    has_unpushed: bool,
) -> StaleDecision:
    """Decide what to do with a worktree given its mtime.

    - Younger than ``max_age_hours`` (24h default): ``skip`` (may be active).
    - ``max_age_hours``–3×: ``remove`` only if no unpushed commits, else ``skip``.
    - Older than 3×``max_age_hours`` (72h default): ``force`` regardless.
    """
    soft_cutoff = now - (max_age_hours * 3600)
    hard_cutoff = now - (max_age_hours * 3 * 3600)

    if mtime > soft_cutoff:
        return "skip"
    if mtime <= hard_cutoff:
        return "force"
    return "skip" if has_unpushed else "remove"


class WorktreeManager:
    """Git-worktree lifecycle over an injectable :class:`GitRunner`."""

    def __init__(self, git: GitRunner):
        self._git = git

    def has_unpushed_commits(self, worktree_path: str, timeout: int = 10) -> bool:
        """True if the worktree has commits not on any remote-tracking ref.

        No remote refs at all → no baseline → treat as no unpushed commits.
        Any git failure → treat as unpushed (keep, don't risk losing work).
        """
        remote_refs = self._git.run(
            ["for-each-ref", "--format=%(refname)", "refs/remotes"],
            cwd=worktree_path,
            timeout=timeout,
        )
        if remote_refs.returncode != 0:
            return True
        if not remote_refs.stdout.strip():
            return False

        log = self._git.run(
            ["log", "--oneline", "HEAD", "--not", "--remotes"],
            cwd=worktree_path,
            timeout=timeout,
        )
        if log.returncode != 0:
            return True
        return bool(log.stdout.strip())

    def prune_orphaned_branches(self, repo_root: str) -> List[str]:
        """Delete auto-generated branches with no worktree; return the deleted set.

        Protects worktree-checked-out branches, the current HEAD, and ``main``.
        Deletes in batches of 50.
        """
        branches = self._git.run(
            ["branch", "--format=%(refname:short)"], cwd=repo_root, timeout=10
        )
        if branches.returncode != 0:
            return []
        all_branches = [b.strip() for b in branches.stdout.strip().split("\n") if b.strip()]

        active: Set[str] = set()
        worktrees = self._git.run(
            ["worktree", "list", "--porcelain"], cwd=repo_root, timeout=10
        )
        if worktrees.returncode != 0:
            # Can't enumerate active worktrees → don't risk deleting a
            # checked-out branch. Bail without pruning anything.
            return []
        for line in worktrees.stdout.split("\n"):
            if line.startswith("branch refs/heads/"):
                active.add(line.split("branch refs/heads/", 1)[-1].strip())

        head = self._git.run(["branch", "--show-current"], cwd=repo_root, timeout=5)
        current = head.stdout.strip()
        if current:
            active.add(current)
        active.add("main")

        orphaned = select_orphaned_branches(all_branches, active)
        for i in range(0, len(orphaned), 50):
            self._git.run(["branch", "-D", *orphaned[i:i + 50]], cwd=repo_root, timeout=30)
        return orphaned
