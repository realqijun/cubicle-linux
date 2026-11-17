"""Exception reservation, settlement and application binding at the supervisor API."""

import json
from pathlib import Path
import os
import tempfile
import unittest

from planner.policy import Policy
from planner.records import Action, Grant, ProtectionStatus
from planner.supervisor import Supervisor

VERIFIED = ProtectionStatus(True, "fixture", "verified fixture", True)


class _Fixture(unittest.TestCase):
    """Shared task, worker and helpers; defines no tests."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="planner-reservation-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.project = base / "project"
        (self.project / "tests").mkdir(parents=True)
        (self.project / "tests/test_app.py").write_text("old")
        (self.project / "tests/other.py").write_text("other")
        self.now = 1000.0
        self.supervisor = Supervisor(base / "state.sqlite", clock=lambda: self.now, task_seconds=600)
        self.addCleanup(self.supervisor.close)
        self.policy = Policy(self.project, read=("**",), write=("src/**",), deny_write=("tests/**",),
                             tools=("shell",))
        self.task = self.supervisor.propose(self.policy)
        self.supervisor.approve_task(self.task)
        self.supervisor.approve_worker(Grant(self.task, "worker", ("**",), ("src/**",), ("shell",)))

    def approval(self, resource: str = "tests/test_app.py", lifetime: str = "once") -> str:
        approval = self.supervisor.request_exception(
            Action(self.task, "worker", "write", resource, "shell"), lifetime=lifetime)
        self.supervisor.approve_exception(approval)
        return approval

    def attempt(self) -> str:
        attempt = self.supervisor.prepare_attempt(self.task, "worker")["id"]
        self.supervisor.verify_attempt(attempt, VERIFIED, VERIFIED)
        return attempt

    def run_attempt(self) -> tuple[str, list[dict]]:
        attempt = self.attempt()
        bound = self.supervisor.reserve_exceptions(attempt)
        self.supervisor.start_attempt(attempt)
        return attempt, bound

    def finish(self, attempt: str) -> None:
        self.supervisor.finish_attempt(attempt, successful=True, cleanup_verified=True)
        self.supervisor.settle_exceptions(attempt)

    def write(self, resource: str = "tests/test_app.py") -> bool:
        return self.supervisor.authorize(Action(self.task, "worker", "write", resource, "shell")).allowed


class ReservationTests(_Fixture):
    def test_once_approval_binds_one_attempt_and_is_spent_after_dispatch(self) -> None:
        approval = self.approval()
        attempt, bound = self.run_attempt()
        self.assertEqual([item["id"] for item in bound], [approval])
        self.assertTrue(self.write())
        self.assertTrue(self.write())  # The mediated filesystem, not the broker, counts the operation.
        self.finish(attempt)
        self.assertEqual(self.supervisor.exception(approval)["state"], "executed")
        self.assertFalse(self.write())
        later, bound = self.run_attempt()
        self.assertEqual(bound, [])
        self.assertFalse(self.write())

    def test_approval_granted_during_a_run_waits_for_a_replacement_worker(self) -> None:
        attempt, _ = self.run_attempt()
        approval = self.approval()
        self.assertFalse(self.write())
        self.assertEqual(self.supervisor.exception(approval)["state"], "approved")
        self.finish(attempt)
        replacement, bound = self.run_attempt()
        self.assertEqual([item["id"] for item in bound], [approval])
        self.assertTrue(self.write())

    def test_undispatched_attempt_returns_reservation(self) -> None:
        approval = self.approval()
        attempt = self.attempt()
        self.supervisor.reserve_exceptions(attempt)
        self.supervisor.abandon_attempt(attempt, "runtime_error")
        self.supervisor.settle_exceptions(attempt)
        self.assertEqual(self.supervisor.exception(approval)["state"], "approved")

    def test_application_binding_rejects_substitution_and_replay(self) -> None:
        approval = self.approval()
        attempt, _ = self.run_attempt()
        target = self.supervisor.policy(self.task).root / "tests/test_app.py"
        with self.assertRaises(PermissionError):  # Not executed yet.
            self.supervisor.check_application_exception(approval, attempt, target, consume=False)
        self.finish(attempt)
        with self.assertRaises(PermissionError):
            self.supervisor.check_application_exception(approval, attempt, target.with_name("other.py"),
                                                        consume=True)
        self.supervisor.check_application_exception(approval, attempt, target, consume=True)
        self.assertEqual(self.supervisor.exception(approval)["state"], "applied")
        with self.assertRaises(PermissionError):
            self.supervisor.check_application_exception(approval, attempt, target, consume=True)

    def test_application_binding_rejects_other_attempt(self) -> None:
        approval = self.approval()
        attempt, _ = self.run_attempt()
        self.finish(attempt)
        other, _ = self.run_attempt()
        self.finish(other)
        target = self.supervisor.policy(self.task).root / "tests/test_app.py"
        with self.assertRaises(PermissionError):
            self.supervisor.check_application_exception(approval, other, target, consume=True)

    def test_expired_task_cannot_reserve_or_apply(self) -> None:
        approval = self.approval()
        attempt, _ = self.run_attempt()
        self.finish(attempt)
        self.now += 601
        target = self.supervisor.policy(self.task).root / "tests/test_app.py"
        with self.assertRaises((PermissionError, ValueError)):
            self.supervisor.check_application_exception(approval, attempt, target, consume=True)
        self.assertEqual(self.supervisor.exception(approval)["state"], "executed")

    def test_revision_and_replaced_resource_are_not_bound(self) -> None:
        replaced = self.approval()
        os.replace(self.project / "tests/other.py", self.project / "tests/test_app.py")
        attempt, bound = self.run_attempt()
        self.assertEqual(bound, [])
        self.assertEqual(self.supervisor.exception(replaced)["state"], "approved")
        self.finish(attempt)
        stale = self.approval()
        self.supervisor.revise(self.task, self.policy)
        self.assertEqual(self.supervisor.exception(stale)["state"], "revoked")

    def test_owner_loss_interrupts_reservation_without_revival(self) -> None:
        approval = self.approval()
        attempt, _ = self.run_attempt()
        self.supervisor.reconcile(self.task)
        self.assertEqual(self.supervisor.exception(approval)["state"], "interrupted")
        self.supervisor.recover_attempt(attempt, VERIFIED, VERIFIED, cleanup_verified=True)
        later, bound = self.run_attempt()
        self.assertEqual(bound, [])
        self.assertFalse(self.write())

    def test_task_lifetime_approval_stays_usable_until_task_end(self) -> None:
        approval = self.approval(lifetime="task")
        first, bound = self.run_attempt()
        self.assertEqual(bound[0]["state"], "approved")
        self.finish(first)
        second, bound = self.run_attempt()
        self.assertEqual([item["id"] for item in bound], [approval])
        self.assertTrue(self.write())


class ReviewRegressionTests(_Fixture):
    """Findings from the independent review, checked at the supervisor boundary."""

    def test_refusing_a_held_approval_stops_the_running_attempt(self) -> None:
        approval = self.approval()
        attempt, _ = self.run_attempt()
        self.supervisor.refuse_exception(approval)
        self.assertEqual(self.supervisor.attempt(attempt)["state"], "stopping")
        self.assertFalse(self.write())

    def test_refusal_before_dispatch_blocks_the_reserved_attempt(self) -> None:
        approval = self.approval()
        attempt = self.attempt()
        [bound] = self.supervisor.reserve_exceptions(attempt)
        self.assertEqual(bound["id"], approval)
        self.supervisor.refuse_exception(approval)
        self.assertEqual(self.supervisor.attempt(attempt)["state"], "blocked")
        with self.assertRaises(ValueError):
            self.supervisor.start_attempt(attempt)
        self.assertFalse(self.write())

    def test_staged_write_can_be_withdrawn_until_application_starts(self) -> None:
        approval = self.approval()
        attempt, _ = self.run_attempt()
        self.finish(attempt)
        self.supervisor.refuse_exception(approval)
        self.assertEqual(self.supervisor.exception(approval)["state"], "withdrawn")
        with self.assertRaises(PermissionError):
            self.supervisor.check_application_exception(
                approval, attempt, self.project / "tests/test_app.py", consume=False)
        with self.assertRaises(ValueError):
            self.supervisor.refuse_exception(approval)

    def test_staged_write_cannot_be_withdrawn_once_application_started(self) -> None:
        approval = self.approval()
        attempt, _ = self.run_attempt()
        self.finish(attempt)
        self.supervisor.record_application(attempt, mode="auto", state="applying", reason="checked")
        with self.assertRaises(ValueError):
            self.supervisor.refuse_exception(approval)
        self.assertEqual(self.supervisor.exception(approval)["state"], "executed")

    def test_spent_read_history_cannot_be_rewritten_by_refusal(self) -> None:
        approval = self.supervisor.request_exception(
            Action(self.task, "worker", "read", "tests/other.py", "shell"), lifetime="once")
        self.supervisor.approve_exception(approval)
        attempt, _ = self.run_attempt()
        self.finish(attempt)
        self.assertEqual(self.supervisor.exception(approval)["state"], "executed")
        with self.assertRaises(ValueError):
            self.supervisor.refuse_exception(approval)
        self.assertEqual(self.supervisor.exception(approval)["state"], "executed")

    def test_repeated_identical_filesystem_denials_suspend_the_worker(self) -> None:
        attempt, _ = self.run_attempt()
        secret = str(self.supervisor.policy(self.task).root / ".env")
        for _ in range(3):
            self.supervisor.record_filesystem_denials(self.task, "worker", [("read", secret)])
        self.assertEqual(self.supervisor.worker_status(self.task, "worker")["state"], "suspended")
        self.assertEqual(self.supervisor.attempt(attempt)["state"], "stopping")

    def test_owner_death_before_the_journal_is_recovered_as_no_write(self) -> None:
        from planner.launcher import Runtime, recover

        attempt, _ = self.run_attempt()
        self.finish(attempt)
        runs = Path(tempfile.mkdtemp(prefix="planner-runs-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(runs))
        self.supervisor.record_application(attempt, mode="auto", state="applying", reason="checked",
                                           journal=str(runs / attempt / "journal.jsonl"))
        [entry] = recover(self.supervisor.path, self.task, "worker",
                          Runtime(Path("/missing/opencode"), Path("/missing/helper"), runs))
        self.assertEqual(entry["application"]["state"], "interrupted")
        self.assertIn("no_write_started", entry["application"]["reason"])

    @unittest.skipUnless(Path("/proc/self/stat").exists(), "Process start times come from /proc")
    def test_recovery_waits_for_a_live_reviewed_apply(self) -> None:
        from planner.launcher import APPLIER, Runtime, _start_time, recover

        attempt, _ = self.run_attempt()
        self.finish(attempt)
        runs = Path(tempfile.mkdtemp(prefix="planner-runs-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(runs))
        (runs / attempt).mkdir()
        (runs / attempt / APPLIER).write_text(json.dumps({"pid": os.getpid(), "start": _start_time(os.getpid())}))
        self.supervisor.record_application(attempt, mode="review", state="applying", reason="checked",
                                           journal=str(runs / attempt / "journal.jsonl"))
        with self.assertRaises(ValueError):
            recover(self.supervisor.path, self.task, "worker",
                    Runtime(Path("/missing/opencode"), Path("/missing/helper"), runs))
        self.assertEqual(self.supervisor.application(attempt)["state"], "applying")

    def test_unreadable_journal_is_not_reported_as_no_write(self) -> None:
        from planner.launcher import Runtime, recover

        attempt, _ = self.run_attempt()
        self.finish(attempt)
        runs = Path(tempfile.mkdtemp(prefix="planner-runs-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(runs))
        journal = runs / attempt / "journal.jsonl"
        journal.mkdir(parents=True)  # Present but unreadable as a journal.
        self.supervisor.record_application(attempt, mode="auto", state="applying", reason="checked",
                                           journal=str(journal))
        [entry] = recover(self.supervisor.path, self.task, "worker",
                          Runtime(Path("/missing/opencode"), Path("/missing/helper"), runs))
        self.assertEqual(entry["application"]["state"], "interrupted")
        self.assertIn("unverifiable:", entry["application"]["reason"])
        self.assertNotIn("no_write_started", entry["application"]["reason"])


if __name__ == "__main__":
    unittest.main()
