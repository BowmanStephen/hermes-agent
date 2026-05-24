"""Tests for the worktree-lifecycle deep module.

The pure deciders encode the *destructive* logic (which branches to delete,
which worktrees are stale, whether a path escapes the repo root) so it can be
tested without a real repo, real ``git``, or filesystem mutation.
"""

from pathlib import Path

from hermes_cli.worktree_manager import (
    GitResult,
    WorktreeManager,
    classify_stale_worktree,
    path_is_within_root,
    select_orphaned_branches,
)


class FakeGitRunner:
    """Scripted git runner: matches calls by a predicate, records every call."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self._handlers: list[tuple] = []

    def add(self, predicate, result: GitResult):
        self._handlers.append((predicate, result))
        return self

    def run(self, args, *, cwd, timeout) -> GitResult:
        self.calls.append(list(args))
        for predicate, result in self._handlers:
            if predicate(args):
                return result
        return GitResult(returncode=0, stdout="")


# ── path_is_within_root (safety guard) ───────────────────────────────────────


def test_path_within_root_accepts_child():
    assert path_is_within_root(Path("/repo/sub/file"), Path("/repo")) is True


def test_path_within_root_rejects_outside():
    assert path_is_within_root(Path("/etc/passwd"), Path("/repo")) is False


def test_path_within_root_rejects_sibling_prefix():
    # /repo-evil must NOT be considered within /repo
    assert path_is_within_root(Path("/repo-evil/x"), Path("/repo")) is False


# ── select_orphaned_branches (branch-delete decision) ─────────────────────────


def test_selects_only_hermes_and_pr_branches():
    all_branches = ["main", "hermes/hermes-abc", "pr-42", "feature/keep", "develop"]
    active = {"main"}
    assert select_orphaned_branches(all_branches, active) == ["hermes/hermes-abc", "pr-42"]


def test_never_selects_active_or_main():
    all_branches = ["main", "hermes/hermes-active", "hermes/hermes-old", "pr-7"]
    active = {"main", "hermes/hermes-active"}
    assert select_orphaned_branches(all_branches, active) == ["hermes/hermes-old", "pr-7"]


def test_no_orphans_returns_empty():
    assert select_orphaned_branches(["main", "feature/x"], {"main"}) == []


# ── classify_stale_worktree (age tiers) ───────────────────────────────────────

NOW = 1_000_000.0
HOUR = 3600.0


def test_recent_worktree_is_skipped():
    # younger than 24h
    assert classify_stale_worktree(mtime=NOW - 1 * HOUR, now=NOW, max_age_hours=24,
                                   has_unpushed=False) == "skip"


def test_mid_age_clean_worktree_is_removed():
    # 24h–72h and no unpushed commits
    assert classify_stale_worktree(mtime=NOW - 30 * HOUR, now=NOW, max_age_hours=24,
                                   has_unpushed=False) == "remove"


def test_mid_age_with_unpushed_is_skipped():
    assert classify_stale_worktree(mtime=NOW - 30 * HOUR, now=NOW, max_age_hours=24,
                                   has_unpushed=True) == "skip"


def test_over_hard_cutoff_is_force_removed_even_with_unpushed():
    # older than 72h — force regardless of unpushed
    assert classify_stale_worktree(mtime=NOW - 100 * HOUR, now=NOW, max_age_hours=24,
                                   has_unpushed=True) == "force"


# ── WorktreeManager.has_unpushed_commits (over GitRunner) ─────────────────────


def test_has_unpushed_false_when_no_remote_refs():
    git = FakeGitRunner().add(lambda a: "for-each-ref" in a, GitResult(0, ""))
    assert WorktreeManager(git).has_unpushed_commits("/wt") is False


def test_has_unpushed_true_when_log_has_commits():
    git = (
        FakeGitRunner()
        .add(lambda a: "for-each-ref" in a, GitResult(0, "refs/remotes/origin/main\n"))
        .add(lambda a: "log" in a, GitResult(0, "abc123 unpushed work\n"))
    )
    assert WorktreeManager(git).has_unpushed_commits("/wt") is True


def test_has_unpushed_true_when_git_fails():
    git = FakeGitRunner().add(lambda a: "for-each-ref" in a, GitResult(128, ""))
    assert WorktreeManager(git).has_unpushed_commits("/wt") is True


# ── WorktreeManager.prune_orphaned_branches (over GitRunner) ──────────────────


def test_prune_orphaned_deletes_only_orphans_and_protects_active():
    git = (
        FakeGitRunner()
        .add(
            lambda a: a[:2] == ["branch", "--format=%(refname:short)"],
            GitResult(0, "main\nhermes/hermes-active\nhermes/hermes-old\npr-5\nfeature/keep\n"),
        )
        .add(
            lambda a: a[:2] == ["worktree", "list"],
            GitResult(0, "worktree /repo/.worktrees/x\nbranch refs/heads/hermes/hermes-active\n"),
        )
        .add(lambda a: a[:2] == ["branch", "--show-current"], GitResult(0, "main\n"))
    )
    mgr = WorktreeManager(git)
    orphaned = mgr.prune_orphaned_branches("/repo")

    assert orphaned == ["hermes/hermes-old", "pr-5"]
    # the actual delete call ran with exactly the orphans
    delete_calls = [c for c in git.calls if c[:2] == ["branch", "-D"]]
    assert delete_calls == [["branch", "-D", "hermes/hermes-old", "pr-5"]]


def test_prune_orphaned_bails_when_worktree_list_fails():
    # Conservative: if we can't enumerate active worktrees, delete nothing
    # (otherwise we might delete a branch that IS checked out).
    git = (
        FakeGitRunner()
        .add(
            lambda a: a[:2] == ["branch", "--format=%(refname:short)"],
            GitResult(0, "main\nhermes/hermes-old\npr-9\n"),
        )
        .add(lambda a: a[:2] == ["worktree", "list"], GitResult(128, ""))
    )
    mgr = WorktreeManager(git)
    assert mgr.prune_orphaned_branches("/repo") == []
    assert not [c for c in git.calls if c[:2] == ["branch", "-D"]]


def test_prune_orphaned_noop_when_none():
    git = (
        FakeGitRunner()
        .add(lambda a: a[:2] == ["branch", "--format=%(refname:short)"], GitResult(0, "main\nfeature/x\n"))
        .add(lambda a: a[:2] == ["worktree", "list"], GitResult(0, ""))
        .add(lambda a: a[:2] == ["branch", "--show-current"], GitResult(0, "main\n"))
    )
    mgr = WorktreeManager(git)
    assert mgr.prune_orphaned_branches("/repo") == []
    assert not [c for c in git.calls if c[:2] == ["branch", "-D"]]
