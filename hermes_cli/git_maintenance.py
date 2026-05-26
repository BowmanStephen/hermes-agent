"""Guided git-maintenance state report (issue #10).

Slice 1: read-only inventory + classification + planning, isolated behind a
narrow injected git runner so states are reproducible in tests without a real
repo. No write actions live here yet.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

# A git runner: called as run("status", "--porcelain") -> stdout string.
GitRunner = Callable[..., str]


@dataclass
class RemoteRoles:
    """Which configured remote is the user's fork vs upstream Hermes."""

    fork_remote: Optional[str]
    upstream_remote: Optional[str]


def _owner_from_url(url: str) -> Optional[str]:
    """Extract the owner segment from an https or scp-style git URL."""
    # git@github.com:OWNER/repo.git  or  https://github.com/OWNER/repo.git
    m = re.search(r"[:/]([^/:]+)/[^/]+?(?:\.git)?/?$", url.strip())
    return m.group(1) if m else None


def classify_remotes(
    remotes: dict[str, str], github_login: Optional[str]
) -> RemoteRoles:
    """Identify fork vs upstream by matching the GitHub login to URL owners.

    Never assumes ``origin`` is the fork — the owner match is authoritative.
    """
    fork_remote: Optional[str] = None
    upstream_remote: Optional[str] = None
    for name, url in remotes.items():
        owner = _owner_from_url(url)
        if github_login and owner and owner.lower() == github_login.lower():
            fork_remote = name
        else:
            upstream_remote = name
    return RemoteRoles(fork_remote=fork_remote, upstream_remote=upstream_remote)


@dataclass
class GitFacts:
    """Raw, classified inventory of the current repository state."""

    branch: str
    detached: bool
    remotes: dict[str, str]
    roles: RemoteRoles
    ahead: int = 0          # commits HEAD has that upstream/<default> lacks
    behind: int = 0         # commits upstream/<default> has that HEAD lacks
    dirty_files: int = 0    # modified + untracked entries in the working tree
    stashes: int = 0


@dataclass
class StateCategory:
    """A user-facing description of one aspect of the repo state."""

    key: str
    label: str
    explanation: str


def classify(facts: GitFacts) -> list["StateCategory"]:
    """Translate raw facts into plain-language categories.

    Returns a single ``clean`` category when there is nothing to act on.
    """
    cats: list[StateCategory] = []
    if facts.dirty_files:
        cats.append(StateCategory(
            "dirty",
            "Unsaved local changes",
            f"{facts.dirty_files} file(s) changed but not committed. They exist "
            "only on this computer — not saved to GitHub yet.",
        ))
    if facts.ahead:
        cats.append(StateCategory(
            "local_commits",
            "Local commits not pushed",
            f"{facts.ahead} commit(s) exist here but haven't been pushed to your "
            "fork yet.",
        ))
    if facts.behind:
        cats.append(StateCategory(
            "behind_upstream",
            "Behind upstream Hermes",
            f"Upstream Hermes has {facts.behind} newer commit(s) you don't have — "
            "you're not on the latest code.",
        ))
    if facts.stashes:
        cats.append(StateCategory(
            "stashes",
            "Set-aside changes (stashes)",
            f"{facts.stashes} stash(es): edits saved aside earlier, recoverable "
            "later. Not lost.",
        ))
    if not cats:
        cats.append(StateCategory(
            "clean",
            "All work saved, up to date",
            "Working tree is clean and level with upstream. Nothing to save now.",
        ))
    return cats


@dataclass
class NextAction:
    """One recommended next step, ordered safest-and-most-important first."""

    key: str
    title: str
    risk: str        # "safe" | "caution" | "destructive"
    why: str
    reversible: bool


def plan_next_actions(
    facts: GitFacts, categories: list[StateCategory]
) -> list[NextAction]:
    """Order next steps by the save-first principle: save, then update, then merge.

    Read-only slice: never emits a destructive action — a safe alternative
    (push a backup branch to the fork) always comes first.
    """
    keys = {c.key for c in categories}
    actions: list[NextAction] = []
    if "dirty" in keys:
        actions.append(NextAction(
            "save_work",
            "Save your work to your fork",
            "safe",
            "Push a backup branch to your fork so unsaved changes are protected "
            "on GitHub — without merging anything.",
            True,
        ))
    if "local_commits" in keys:
        actions.append(NextAction(
            "push_branch",
            "Push your local commits to your fork",
            "safe",
            "Back up commits that currently exist only on this computer.",
            True,
        ))
    if "behind_upstream" in keys:
        actions.append(NextAction(
            "update",
            "Update Hermes from upstream",
            "caution",
            "Pull the newer upstream commits. Save your work (above) first so "
            "nothing local is disturbed.",
            True,
        ))
    if not actions:
        actions.append(NextAction(
            "none",
            "Nothing to do",
            "safe",
            "Your work is saved and you're on the latest code.",
            True,
        ))
    return actions


@dataclass
class Report:
    """Bundled read-only report: raw facts + categories + recommended actions."""

    facts: GitFacts
    categories: list[StateCategory]
    actions: list[NextAction]


def report_from_facts(facts: GitFacts) -> Report:
    categories = classify(facts)
    return Report(facts, categories, plan_next_actions(facts, categories))


def build_report(
    run: GitRunner, github_login: Optional[str], default_branch: str = "main"
) -> Report:
    """Collect facts through the injected runner and assemble the full report."""
    return report_from_facts(collect_facts(run, github_login, default_branch))


def render_text(report: Report) -> str:
    """Plain-language report a non-technical operator can act on."""
    f = report.facts
    where = "a detached HEAD (no branch)" if f.detached else f"branch '{f.branch}'"
    lines = [f"Repository state — on {where}."]
    if f.roles.fork_remote or f.roles.upstream_remote:
        lines.append(
            f"  Your fork: '{f.roles.fork_remote}'    "
            f"Upstream Hermes: '{f.roles.upstream_remote}'"
        )
    lines += ["", "What's going on:"]
    for c in report.categories:
        lines.append(f"  • {c.label} — {c.explanation}")
    top = report.actions[0]
    lines += [
        "",
        f"Recommended next step: {top.title}",
        f"  {top.why}",
        f"  (risk: {top.risk}; reversible: {'yes' if top.reversible else 'no'})",
    ]
    return "\n".join(lines)


def subprocess_git_runner(cwd: str = ".") -> GitRunner:
    """Real git runner: shells `git -C <cwd> <args>`, "" on any failure."""

    def run(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return proc.stdout
        except Exception:
            return ""

    return run


def detect_github_login(cwd: str = ".") -> Optional[str]:
    """Best-effort GitHub login via gh; None when gh is absent/unauthed."""
    if not shutil.which("gh"):
        return None
    try:
        proc = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=cwd,
        )
        return proc.stdout.strip() or None
    except Exception:
        return None


def run_status(
    cwd: str = ".",
    as_json: bool = False,
    runner: Optional[GitRunner] = None,
    github_login: Optional[str] = None,
) -> str:
    """Entry point for the read-only state report (human or machine output).

    ``runner``/``github_login`` are injectable for tests; in real use they
    default to a subprocess git runner and a gh-detected login.
    """
    if runner is None:
        runner = subprocess_git_runner(cwd)
        if github_login is None:
            github_login = detect_github_login(cwd)
    report = build_report(runner, github_login)
    if as_json:
        return json.dumps(report_to_dict(report), indent=2)
    return render_text(report)


def cmd_git_status(args) -> int:  # noqa: ANN001 — argparse Namespace
    """CLI entry for `hermes git`: print the read-only state report."""
    as_json = bool(getattr(args, "json", False))
    cwd = getattr(args, "cwd", None) or os.getcwd()
    print(run_status(cwd=cwd, as_json=as_json))
    return 0


def report_to_dict(report: Report) -> dict:
    """Stable machine-readable view for agents (story #34)."""
    f = report.facts
    return {
        "branch": f.branch,
        "detached": f.detached,
        "ahead": f.ahead,
        "behind": f.behind,
        "dirty_files": f.dirty_files,
        "stashes": f.stashes,
        "fork_remote": f.roles.fork_remote,
        "upstream_remote": f.roles.upstream_remote,
        "categories": [
            {"key": c.key, "label": c.label, "explanation": c.explanation}
            for c in report.categories
        ],
        "actions": [
            {
                "key": a.key,
                "title": a.title,
                "risk": a.risk,
                "reversible": a.reversible,
                "why": a.why,
            }
            for a in report.actions
        ],
        "recommended": report.actions[0].key if report.actions else None,
    }


def _parse_remotes(output: str) -> dict[str, str]:
    remotes: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            remotes.setdefault(parts[0], parts[1])
    return remotes


def _count_nonblank(output: str) -> int:
    return sum(1 for line in output.splitlines() if line.strip())


def collect_facts(
    run: GitRunner, github_login: Optional[str], default_branch: str = "main"
) -> GitFacts:
    """Build a GitFacts snapshot through an injected git runner (read-only)."""
    branch = run("branch", "--show-current").strip()
    remotes = _parse_remotes(run("remote", "-v"))
    roles = classify_remotes(remotes, github_login)

    ahead = behind = 0
    if roles.upstream_remote:
        spec = f"{roles.upstream_remote}/{default_branch}...HEAD"
        counts = run("rev-list", "--left-right", "--count", spec).split()
        if len(counts) == 2:
            behind, ahead = int(counts[0]), int(counts[1])

    return GitFacts(
        branch=branch,
        detached=(branch == ""),
        remotes=remotes,
        roles=roles,
        ahead=ahead,
        behind=behind,
        dirty_files=_count_nonblank(run("status", "--porcelain")),
        stashes=_count_nonblank(run("stash", "list")),
    )
