"""Tests for the guided git-maintenance state report (issue #10, slice 1).

Slice 1 = read-only inventory + classifier + planner + rendering. No write
actions, no shelling out to a real repo: the inventory takes an injected git
runner so every state is reproducible from fake output.
"""

from hermes_cli.git_maintenance import (
    GitFacts,
    RemoteRoles,
    classify,
    classify_remotes,
    collect_facts,
    plan_next_actions,
    render_text,
    report_from_facts,
    report_to_dict,
    run_status,
)


def _facts(**kw) -> GitFacts:
    base = dict(
        branch="main",
        detached=False,
        remotes={},
        roles=RemoteRoles(fork_remote="fork", upstream_remote="origin"),
        ahead=0,
        behind=0,
        dirty_files=0,
        stashes=0,
    )
    base.update(kw)
    return GitFacts(**base)


class FakeGit:
    """Models a repo state by mapping git arg-tuples to canned stdout.

    Tests assert the resulting GitFacts (external behavior), not the call
    sequence — unknown commands return "" so the inventory degrades safely.
    """

    def __init__(self, responses: dict[tuple, str]):
        self._responses = responses
        self.calls: list[tuple] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        return self._responses.get(args, "")


def test_classify_remotes_distinguishes_fork_from_upstream_when_origin_is_upstream():
    """origin can point at upstream and a separate 'fork' remote at the user.

    The fork is identified by matching the authenticated GitHub login against
    the remote URL owner — never by assuming origin == fork.
    """
    remotes = {
        "origin": "https://github.com/NousResearch/hermes-agent.git",
        "fork": "https://github.com/BowmanStephen/hermes-agent.git",
    }

    roles = classify_remotes(remotes, github_login="BowmanStephen")

    assert roles.fork_remote == "fork"
    assert roles.upstream_remote == "origin"


def test_collect_facts_builds_inventory_for_dirty_branch_behind_upstream():
    git = FakeGit({
        ("branch", "--show-current"): "feat/x\n",
        ("remote", "-v"): (
            "origin\thttps://github.com/NousResearch/hermes-agent.git (fetch)\n"
            "origin\thttps://github.com/NousResearch/hermes-agent.git (push)\n"
            "fork\thttps://github.com/BowmanStephen/hermes-agent.git (fetch)\n"
            "fork\thttps://github.com/BowmanStephen/hermes-agent.git (push)\n"
        ),
        ("rev-list", "--left-right", "--count", "origin/main...HEAD"): "5\t2\n",
        ("status", "--porcelain"): " M a.py\n M b.py\n?? c.py\n",
        ("stash", "list"): "stash@{0}: WIP on feat/x\n",
    })

    facts = collect_facts(git, github_login="BowmanStephen")

    assert facts.branch == "feat/x"
    assert facts.detached is False
    assert facts.roles.fork_remote == "fork"
    assert facts.roles.upstream_remote == "origin"
    assert facts.behind == 5
    assert facts.ahead == 2
    assert facts.dirty_files == 3
    assert facts.stashes == 1


def test_collect_facts_marks_detached_head_when_no_branch():
    git = FakeGit({("branch", "--show-current"): "\n"})

    facts = collect_facts(git, github_login=None)

    assert facts.detached is True
    assert facts.branch == ""


def test_classify_clean_uptodate_repo_is_a_single_clean_category():
    keys = {c.key for c in classify(_facts())}

    assert keys == {"clean"}


def test_classify_dirty_behind_and_ahead_reports_each_concern():
    facts = _facts(dirty_files=3, behind=5, ahead=2)

    keys = {c.key for c in classify(facts)}

    assert "dirty" in keys
    assert "behind_upstream" in keys
    assert "local_commits" in keys
    assert "clean" not in keys


def test_classify_every_category_has_plain_language_text():
    for category in classify(_facts(dirty_files=1, behind=1, ahead=1, stashes=1)):
        assert category.label
        assert category.explanation


def test_plan_saves_work_first_when_dirty_and_behind():
    facts = _facts(dirty_files=3, behind=5)

    actions = plan_next_actions(facts, classify(facts))

    # Save-first principle: protect unsaved work before any update/merge.
    assert actions[0].key == "save_work"
    assert actions[0].reversible is True


def test_plan_never_recommends_destructive_action_when_safe_path_exists():
    facts = _facts(dirty_files=3, behind=5, ahead=2)

    actions = plan_next_actions(facts, classify(facts))

    assert all(a.risk != "destructive" for a in actions)


def test_plan_recommends_update_when_clean_but_behind():
    facts = _facts(behind=5)

    actions = plan_next_actions(facts, classify(facts))

    assert actions[0].key == "update"


def test_plan_reports_nothing_to_do_when_clean_and_current():
    actions = plan_next_actions(_facts(), classify(_facts()))

    assert [a.key for a in actions] == ["none"]


def test_render_text_shows_branch_top_action_and_a_plain_language_label():
    report = report_from_facts(_facts(branch="feat/x", dirty_files=3, behind=5))

    text = render_text(report)

    assert "feat/x" in text
    assert "Save your work to your fork" in text   # the recommended next step
    assert "Unsaved local changes" in text          # plain-language category


def test_report_to_dict_exposes_stable_machine_fields():
    report = report_from_facts(_facts(dirty_files=3, behind=5, ahead=2))

    data = report_to_dict(report)

    assert data["branch"] == "main"
    assert data["ahead"] == 2
    assert data["behind"] == 5
    assert data["dirty_files"] == 3
    assert data["fork_remote"] == "fork"
    assert data["upstream_remote"] == "origin"
    assert "save_work" in [a["key"] for a in data["actions"]]
    assert data["recommended"] == "save_work"
    assert "dirty" in [c["key"] for c in data["categories"]]


def test_run_status_json_output_parses_and_has_machine_fields():
    import json

    git = FakeGit({
        ("branch", "--show-current"): "main\n",
        ("remote", "-v"): "origin\thttps://github.com/NousResearch/hermes-agent.git (fetch)\n",
        ("status", "--porcelain"): "",
        ("stash", "list"): "",
    })

    out = run_status(as_json=True, runner=git, github_login=None)
    data = json.loads(out)

    assert data["branch"] == "main"
    assert data["recommended"]  # always recommends something, even "none"


def test_run_status_text_output_is_human_readable():
    git = FakeGit({
        ("branch", "--show-current"): "main\n",
        ("remote", "-v"): "",
        ("status", "--porcelain"): " M a.py\n",
        ("stash", "list"): "",
    })

    out = run_status(as_json=False, runner=git, github_login=None)

    assert "Recommended next step:" in out


def test_cmd_git_status_honors_json_flag_and_returns_zero(monkeypatch, capsys):
    from argparse import Namespace

    import hermes_cli.git_maintenance as gm

    monkeypatch.setattr(
        gm, "run_status", lambda cwd=".", as_json=False, **k: f"OUT json={as_json}"
    )

    rc = gm.cmd_git_status(Namespace(json=True, cwd=None))

    assert rc == 0
    assert "json=True" in capsys.readouterr().out
