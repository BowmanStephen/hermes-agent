"""Kanban workers must not be able to `kill` their own process.

Observed failure: a dispatcher-spawned worker's model read its own pid from
the run record (`hermes kanban show <task> --json` / `ps`) and ran
`kill <pid>` to "release" the task. The worker died, the dispatcher recorded
the run as ``crashed``, and with max-retries=1 the task landed in ``blocked``
with no deliverable. The terminal tool blocks literal-pid self-kill shapes in
kanban workers (``HERMES_KANBAN_TASK`` set) and points the model at
``kanban_complete`` / ``kanban_block`` instead.
"""

import os

from tools.terminal_tool import contains_kanban_worker_self_kill

OWN = {4242, 4200}  # worker pid, parent/pgid


class TestSelfKillDetection:
    def test_kill_own_pid(self):
        assert contains_kanban_worker_self_kill("kill 4242", OWN)

    def test_kill_own_pid_with_signal_flag(self):
        assert contains_kanban_worker_self_kill("kill -9 4242", OWN)
        assert contains_kanban_worker_self_kill("kill -TERM 4242", OWN)

    def test_kill_parent_pid(self):
        assert contains_kanban_worker_self_kill("kill 4200", OWN)

    def test_kill_zero_nukes_process_group(self):
        assert contains_kanban_worker_self_kill("kill 0", OWN)

    def test_kill_negative_own_pgid(self):
        assert contains_kanban_worker_self_kill("kill -- -4200", OWN)

    def test_kill_in_compound_command(self):
        assert contains_kanban_worker_self_kill(
            "ps -p 4242 && kill 4242; echo done", OWN
        )

    def test_absolute_path_kill(self):
        assert contains_kanban_worker_self_kill("/bin/kill 4242", OWN)

    def test_kill_unrelated_pid_allowed(self):
        assert not contains_kanban_worker_self_kill("kill 9999", OWN)
        assert not contains_kanban_worker_self_kill("kill -9 9999", OWN)

    def test_signal_number_not_confused_with_pgid(self):
        # `-4242` in flag position is a (nonsense) signal spec, not a target;
        # only `kill -- -4242` targets the group.
        assert not contains_kanban_worker_self_kill("kill -4242 9999", OWN)

    def test_pkill_left_alone(self):
        # Pattern kills are out of scope (gateway lifecycle guard covers the
        # dangerous ones); this guard is literal-pid only.
        assert not contains_kanban_worker_self_kill("pkill -f hermes", OWN)

    def test_prose_and_other_commands_allowed(self):
        assert not contains_kanban_worker_self_kill("echo kill 4242", OWN)
        assert not contains_kanban_worker_self_kill(
            "hermes kanban complete t_123 --result done", OWN
        )

    def test_defaults_use_real_process_ids(self):
        assert contains_kanban_worker_self_kill(f"kill {os.getpid()}")
        assert not contains_kanban_worker_self_kill("kill 999999999")
