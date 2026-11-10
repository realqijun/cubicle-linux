from pathlib import Path
import tempfile
import unittest

from planner.policy import Policy
from planner.records import Action, Grant
from planner.supervisor import Supervisor


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "project"
        self.root.mkdir()
        self.policy = Policy(self.root, read=("**",), write=("src/**",), deny_read=(".env*",))
        self.supervisor = Supervisor(base / "state.sqlite")
        self.addCleanup(self.supervisor.close)

    def active_worker(self):
        task = self.supervisor.propose(self.policy)
        self.supervisor.approve_task(task)
        self.supervisor.approve_worker(Grant(task, "worker", ("**",), ("src/**",)))
        return task

    def test_unreviewed_proposal_cannot_install_worker(self):
        task = self.supervisor.propose(self.policy)
        with self.assertRaisesRegex(ValueError, "approved"):
            self.supervisor.approve_worker(Grant(task, "worker", ("**",)))

    def test_reviewed_worker_can_read_only_its_task(self):
        task = self.active_worker()
        decision = self.supervisor.authorize(Action(task, "worker", "read", "README.md"))
        self.assertTrue(decision.allowed)
        self.assertEqual(self.supervisor.status(task)["state"], "approved")
        self.assertEqual(self.supervisor.history(task)[0]["grant_revision"], 1)

    def test_sensitive_exception_is_exact_single_use_and_does_not_remove_denial(self):
        task = self.active_worker()
        (self.root / ".env").write_text("DUMMY_SECRET")
        request = self.supervisor.request_exception(Action(task, "worker", "read", ".env"))
        with self.assertRaisesRegex(ValueError, "provider"):
            self.supervisor.approve_exception(request)
        self.supervisor.approve_exception(request, disclose_sensitive=True)
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", ".env.other")).allowed)
        self.assertTrue(self.supervisor.authorize(Action(task, "worker", "read", ".env")).allowed)
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", ".env")).allowed)
        self.assertFalse(self.supervisor.policy(task).decide(
            Action(task, "worker", "read", ".env"), self.supervisor.grant(task, "worker")).allowed)

    def test_sensitive_hardlink_approval_remains_sensitive_for_provenance(self):
        import os
        from planner.provenance import Provenance

        task = self.active_worker()
        (self.root / ".env").write_text("DUMMY_SECRET")
        os.link(self.root / ".env", self.root / "alias")
        action = Action(task, "worker", "read", "alias")
        request = self.supervisor.request_exception(action)
        self.assertTrue(self.supervisor.exception(request)["sensitive"])
        self.supervisor.approve_exception(request, disclose_sensitive=True)
        decision = self.supervisor.authorize(action)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.sensitive)
        memory = Provenance(self.supervisor.path.with_suffix(".memory.sqlite"))
        self.addCleanup(memory.close)
        memory.register_task(task, str(self.root))
        source = memory.record_input(task, "worker", "file", decision.resource, decision.sensitive)
        with self.assertRaises(PermissionError):
            memory.add_note(str(self.root), task, "worker", "DUMMY_SECRET", [source.id])

    def test_denial_retry_limit_suspends_worker_until_review(self):
        task = self.active_worker()
        denied = Action(task, "worker", "read", ".env")
        for _ in range(3):
            self.assertFalse(self.supervisor.authorize(denied).allowed)
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", "README.md")).allowed)
        self.assertEqual(len(self.supervisor.history(task)), 4)

    def test_revocation_stops_descendants_and_invalidates_exceptions(self):
        task = self.active_worker()
        self.supervisor.approve_worker(Grant(task, "child", ("src/**",), (), parent_id="worker"))
        approval = self.supervisor.request_exception(Action(task, "child", "read", ".env"))
        self.supervisor.approve_exception(approval, disclose_sensitive=True)
        self.supervisor.revoke_worker(task, "worker")
        self.assertFalse(self.supervisor.authorize(Action(task, "child", "read", "src/app.py")).allowed)
        self.assertFalse(self.supervisor.authorize(Action(task, "child", "read", ".env")).allowed)

    def test_cancelled_task_cannot_be_resumed_or_replayed(self):
        task = self.active_worker()
        self.supervisor.cancel(task)
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", "README.md")).allowed)
        with self.assertRaises(ValueError):
            self.supervisor.approve_task(task)

    def test_exception_does_not_survive_expiry_replacement_or_another_task(self):
        now = [100.0]
        self.supervisor.clock = lambda: now[0]
        task = self.active_worker()
        secret = self.root / ".env"
        secret.write_text("DUMMY_SECRET")
        request = self.supervisor.request_exception(Action(task, "worker", "read", ".env"))
        self.supervisor.approve_exception(request, disclose_sensitive=True)
        replacement = self.root / "replacement"
        replacement.write_text("DIFFERENT_DUMMY_SECRET")
        replacement.replace(secret)
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", ".env")).allowed)
        other = self.active_worker()
        self.assertFalse(self.supervisor.authorize(Action(other, "worker", "read", ".env")).allowed)
        now[0] = 10000.0
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", "README.md")).allowed)

    def test_state_cannot_be_stored_under_worker_project(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            self.supervisor.propose(Policy(self.supervisor.path.parent, read=("**",)))

    def test_single_use_approval_is_atomic_across_supervisors(self):
        from concurrent.futures import ThreadPoolExecutor

        task = self.active_worker()
        request = self.supervisor.request_exception(Action(task, "worker", "write", "README.md"))
        self.supervisor.approve_exception(request)

        def dispatch(_):
            supervisor = Supervisor(self.supervisor.path)
            try:
                return supervisor.authorize(Action(task, "worker", "write", "README.md")).allowed
            finally:
                supervisor.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(dispatch, range(2))), [False, True])

    def test_policy_revision_requires_new_review_and_invalidates_old_grants(self):
        task = self.active_worker()
        self.supervisor.revise(task, Policy(self.root, read=("src/**",)))
        self.assertEqual(self.supervisor.status(task)["revision"], 2)
        self.assertEqual(self.supervisor.status(task)["state"], "proposed")
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", "README.md")).allowed)
        self.supervisor.approve_task(task)
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", "src/app.py")).allowed)
        self.assertEqual(len(self.supervisor.export(task)["policy_revisions"]), 2)

    def test_replaced_project_root_does_not_silently_change_the_approved_boundary(self):
        task = self.active_worker()
        self.root.rename(self.root.with_name("original-project"))
        self.root.mkdir()
        self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", "README.md")).allowed)

    def test_earlier_decision_storage_remains_usable_after_restart(self):
        import sqlite3
        from contextlib import closing

        path = self.supervisor.path.with_name("earlier.sqlite")
        with closing(sqlite3.connect(path)) as legacy:
            legacy.execute("""CREATE TABLE decisions (
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, worker TEXT NOT NULL,
                operation TEXT NOT NULL, resource TEXT NOT NULL, tool TEXT NOT NULL,
                allowed INTEGER NOT NULL, reason TEXT NOT NULL, approval TEXT,
                created REAL NOT NULL)""")
        restored = Supervisor(path)
        self.addCleanup(restored.close)
        task = restored.propose(self.policy)
        restored.approve_task(task)
        restored.approve_worker(Grant(task, "worker", read=("**",)))
        self.assertTrue(restored.authorize(Action(task, "worker", "read", "README.md")).allowed)
        self.assertEqual(restored.history(task)[0]["grant_revision"], 1)


if __name__ == "__main__":
    unittest.main()
