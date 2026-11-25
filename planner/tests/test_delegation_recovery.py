from pathlib import Path
import tempfile
import unittest

from planner.policy import Policy
from planner.records import Action, Grant, ProtectionStatus
from planner.supervisor import Supervisor


class DelegationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "project"
        self.root.mkdir()
        self.supervisor = Supervisor(base / "control.sqlite")
        self.addCleanup(self.supervisor.close)
        self.policy = Policy(self.root, read=("**",), write=("src/**",), tools=("file", "shell"))
        self.task = self.supervisor.propose(self.policy)
        self.supervisor.approve_task(self.task)
        self.supervisor.approve_worker(Grant(self.task, "parent", ("src/**",), ("src/**",)))

    def test_user_reviewed_template_allows_only_its_parent_bound_subset(self):
        self.supervisor.approve_delegation_template(
            self.task, "source-reader", "parent", read=("src/**",))
        child = Grant(self.task, "child", ("src/refunds.py",), (), parent_id="parent")
        self.supervisor.delegate(child, "source-reader")
        self.assertTrue(self.supervisor.authorize(Action(self.task, "child", "read", "src/refunds.py")).allowed)
        self.assertFalse(self.supervisor.authorize(Action(self.task, "child", "write", "src/refunds.py")).allowed)
        self.assertEqual(self.supervisor.worker_status(self.task, "child")["template"], "source-reader")

    def test_template_cannot_expand_scope_change_parent_or_survive_revision(self):
        self.supervisor.approve_delegation_template(self.task, "reader", "parent", read=("src/**",))
        self.supervisor.approve_worker(Grant(self.task, "other", ("src/**",)))
        for child in (Grant(self.task, "excess", ("**",), parent_id="parent"),
                      Grant(self.task, "writer", ("src/**",), ("src/**",), parent_id="parent"),
                      Grant(self.task, "wrong-parent", ("src/**",), parent_id="other")):
            with self.subTest(child=child.worker_id), self.assertRaises(ValueError):
                self.supervisor.delegate(child, "reader")
        with self.assertRaises(ValueError):
            self.supervisor.delegate(Grant(self.task, "unknown", parent_id="parent"), "worker says approved")
        self.supervisor.revise(self.task, self.policy)
        self.supervisor.approve_task(self.task)
        with self.assertRaises(ValueError):
            self.supervisor.delegate(Grant(self.task, "after-revision", parent_id="parent"), "reader")
        self.assertEqual(self.supervisor.templates(self.task)[0]["state"], "revoked")

    def test_worker_check_validates_ancestry_without_consuming_exception(self):
        child = Grant(self.task, "child", ("src/**",), parent_id="parent")
        self.supervisor.approve_worker(child)
        request = self.supervisor.request_exception(Action(self.task, "child", "write", "README.md"))
        self.supervisor.approve_exception(request)
        self.assertEqual(self.supervisor.check_worker(self.task, "child", "file"), child)
        self.assertEqual(self.supervisor.exception(request)["state"], "approved")
        for _ in range(3):
            self.supervisor.authorize(Action(self.task, "parent", "read", ".env"))
        with self.assertRaises(ValueError):
            self.supervisor.check_worker(self.task, "child")

    def test_denied_multi_resource_operation_does_not_consume_an_exact_exception(self):
        request = self.supervisor.request_exception(Action(self.task, "parent", "read", "README.md"))
        self.supervisor.approve_exception(request)
        decisions = self.supervisor.authorize_many((
            Action(self.task, "parent", "read", "README.md"),
            Action(self.task, "parent", "write", "tests/protected.py"),
        ))
        self.assertEqual([decision.allowed for decision in decisions], [False, False])
        self.assertEqual(self.supervisor.exception(request)["state"], "approved")
        self.assertTrue(self.supervisor.authorize(Action(self.task, "parent", "read", "README.md")).allowed)
        self.assertEqual(self.supervisor.exception(request)["state"], "consumed")

    def test_allowed_batch_consumes_approval_once_and_rejects_mixed_workers(self):
        request = self.supervisor.request_exception(Action(self.task, "parent", "read", "README.md"))
        self.supervisor.approve_exception(request)
        actions = (Action(self.task, "parent", "read", "README.md"),
                   Action(self.task, "parent", "write", "src/fixed.py"))
        self.assertTrue(all(item.allowed for item in self.supervisor.authorize_many(actions)))
        self.assertEqual(self.supervisor.exception(request)["state"], "consumed")
        self.assertFalse(self.supervisor.authorize(actions[0]).allowed)
        with self.assertRaises(ValueError):
            self.supervisor.authorize_many((actions[0], Action(self.task, "different", "read", "README.md")))

    def test_repeated_identical_multi_resource_denials_stop_the_request_sequence(self):
        for _ in range(3):
            self.supervisor.authorize_many((Action(self.task, "parent", "read", "README.md"),
                                            Action(self.task, "parent", "write", "tests/protected.py")))
        self.assertEqual(self.supervisor.worker_status(self.task, "parent")["state"], "suspended")

    def test_attempt_records_failed_verification_without_claiming_execution(self):
        attempt = self.supervisor.prepare_attempt(self.task, "parent")
        self.assertEqual(attempt["state"], "prepared")
        self.assertEqual(attempt["revision"], 1)
        verified = self.supervisor.verify_attempt(
            attempt["id"], ProtectionStatus(False, "macos", "dummy diagnostic"),
            ProtectionStatus(True, "fixture-connector", "verified fixture", verified=True))
        self.assertEqual(verified["state"], "blocked")
        self.assertEqual(verified["reason"], "protection_unavailable")
        with self.assertRaises(ValueError):
            self.supervisor.start_attempt(attempt["id"])

    def test_interrupted_attempt_requires_cleanup_and_fresh_checks_without_restoring_approval(self):
        fixture = ProtectionStatus(True, "fixture", "metadata-only fixture", verified=True)
        request = self.supervisor.request_exception(Action(self.task, "parent", "read", "README.md"))
        self.supervisor.approve_exception(request)
        attempt = self.supervisor.prepare_attempt(self.task, "parent")
        self.supervisor.verify_attempt(attempt["id"], fixture, fixture)
        self.supervisor.start_attempt(attempt["id"])
        self.supervisor.close()
        restored = Supervisor(self.supervisor.path)
        self.addCleanup(restored.close)
        interrupted = restored.reconcile(self.task)
        self.assertEqual(interrupted[0]["state"], "interrupted")
        self.assertEqual(restored.worker_status(self.task, "parent")["state"], "suspended")
        self.assertEqual(restored.exception(request)["state"], "revoked")
        unresolved = restored.recover_attempt(attempt["id"], fixture, fixture, cleanup_verified=False)
        self.assertEqual(unresolved["reason"], "cleanup_unverified")
        blocked = ProtectionStatus(False, "fixture", "unavailable")
        unresolved = restored.recover_attempt(attempt["id"], blocked, fixture, cleanup_verified=True)
        self.assertEqual(unresolved["state"], "interrupted")
        recovered = restored.recover_attempt(attempt["id"], fixture, fixture, cleanup_verified=True)
        self.assertEqual(recovered["state"], "reconciled")
        self.assertTrue(recovered["cleanup_verified"])
        self.assertEqual([event["reason"] for event in recovered["events"][-3:]],
                         ["cleanup_unverified", "protection_unavailable", "recovery_verified"])
        self.assertEqual(restored.check_worker(self.task, "parent").worker_id, "parent")
        self.assertFalse(restored.authorize(Action(self.task, "parent", "read", "README.md")).allowed)
        self.assertNotEqual(restored.prepare_attempt(self.task, "parent")["id"], attempt["id"])

    def test_revocation_records_required_cleanup_and_cannot_be_recovered_as_authority(self):
        fixture = ProtectionStatus(True, "fixture", "metadata-only fixture", verified=True)
        self.supervisor.approve_delegation_template(self.task, "reader", "parent", read=("src/**",))
        attempt = self.supervisor.prepare_attempt(self.task, "parent")
        self.supervisor.verify_attempt(attempt["id"], fixture, fixture)
        self.supervisor.start_attempt(attempt["id"])
        self.supervisor.revoke_worker(self.task, "parent")
        self.assertEqual(self.supervisor.attempt(attempt["id"])["state"], "stopping")
        self.assertFalse(self.supervisor.attempt(attempt["id"])["cleanup_verified"])
        self.assertEqual(self.supervisor.templates(self.task)[0]["state"], "revoked")
        finished = self.supervisor.finish_attempt(attempt["id"], successful=False, cleanup_verified=True)
        self.assertEqual(finished["state"], "cancelled")
        with self.assertRaises(ValueError):
            self.supervisor.check_worker(self.task, "parent")

    def test_expired_or_changed_attempt_cannot_dispatch_or_restore_its_grant(self):
        fixture = ProtectionStatus(True, "fixture", "metadata-only fixture", verified=True)
        attempt = self.supervisor.prepare_attempt(self.task, "parent")
        self.supervisor.verify_attempt(attempt["id"], fixture, fixture)
        self.supervisor.clock = lambda: attempt["expires"] + 1
        with self.assertRaises(ValueError):
            self.supervisor.start_attempt(attempt["id"])
        self.supervisor.reconcile(self.task)
        recovered = self.supervisor.recover_attempt(attempt["id"], fixture, fixture, cleanup_verified=True)
        self.assertEqual(recovered["state"], "interrupted")
        self.assertEqual(recovered["reason"], "authority_invalid")
        with self.assertRaises(ValueError):
            self.supervisor.check_worker(self.task, "parent")

    def test_finished_attempt_requires_cleanup_and_cannot_be_started_twice(self):
        fixture = ProtectionStatus(True, "fixture", "metadata-only fixture", verified=True)
        attempt = self.supervisor.prepare_attempt(self.task, "parent")
        self.supervisor.verify_attempt(attempt["id"], fixture, fixture)
        self.supervisor.start_attempt(attempt["id"])
        with self.assertRaises(ValueError):
            self.supervisor.start_attempt(attempt["id"])
        finished = self.supervisor.finish_attempt(attempt["id"], successful=True, cleanup_verified=False)
        self.assertEqual(finished["state"], "interrupted")
        self.assertEqual(finished["reason"], "cleanup_unverified")
        with self.assertRaises(ValueError):
            self.supervisor.prepare_attempt(self.task, "parent")

    def test_resume_only_restores_control_state_after_backend_and_connector_checks(self):
        # These boundary fixtures test state recovery, not actual OS protection.
        fixture = ProtectionStatus(True, "fixture", "metadata-only fixture", verified=True)
        unavailable = ProtectionStatus(False, "fixture", "not available")
        for _ in range(3):
            self.supervisor.authorize(Action(self.task, "parent", "read", ".env"))
        with self.assertRaises(RuntimeError):
            self.supervisor.resume(self.task, "parent", unavailable, fixture)
        self.assertEqual(self.supervisor.worker_status(self.task, "parent")["state"], "suspended")
        self.supervisor.resume(self.task, "parent", fixture, fixture)
        self.assertEqual(self.supervisor.check_worker(self.task, "parent").worker_id, "parent")

    def test_resumed_parent_restores_its_children_without_widening_them(self):
        fixture = ProtectionStatus(True, "fixture", "metadata-only fixture", verified=True)
        child = Grant(self.task, "child", ("src/**",), parent_id="parent")
        self.supervisor.approve_worker(child)
        for _ in range(3):
            self.supervisor.authorize(Action(self.task, "parent", "read", ".env"))
        with self.assertRaisesRegex(ValueError, "suspended"):
            self.supervisor.resume(self.task, "child", fixture, fixture)
        with self.assertRaises(ValueError):
            self.supervisor.check_worker(self.task, "child")
        self.supervisor.resume(self.task, "parent", fixture, fixture)
        self.assertEqual(self.supervisor.check_worker(self.task, "child"), child)
        self.assertFalse(self.supervisor.authorize(Action(self.task, "child", "write", "src/app.py")).allowed)


if __name__ == "__main__":
    unittest.main()
