"""Security regressions at the trusted supervisor and terminal boundaries."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from planner.policy import Policy
from planner.provenance import Provenance
from planner.records import Action, Grant
from planner.supervisor import Supervisor


class ControlAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.state = self.base / "control.sqlite"
        self.supervisor = Supervisor(self.state)
        self.addCleanup(self.supervisor.close)

    def test_worker_exception_cannot_authorize_modifying_control_state(self):
        policy = Policy(self.root, read=("**",), external_inputs=(self.state,))
        try:
            task = self.supervisor.propose(policy)
            self.supervisor.approve_task(task)
            self.supervisor.approve_worker(Grant(task, "worker", read=("**",)))
            action = Action(task, "worker", "write", str(self.state))
            request = self.supervisor.request_exception(action)
            self.supervisor.approve_exception(request)
        except ValueError:
            return  # Refusing the protected resource before installation is also valid.
        self.assertFalse(self.supervisor.authorize(action).allowed)

    def test_repeated_requests_do_not_downgrade_revocation_to_suspension(self):
        task = self.supervisor.propose(Policy(self.root, read=("**",)))
        self.supervisor.approve_task(task)
        self.supervisor.approve_worker(Grant(task, "worker", read=("**",)))
        self.supervisor.revoke_worker(task, "worker")
        for _ in range(3):
            self.assertFalse(self.supervisor.authorize(Action(task, "worker", "read", "README.md")).allowed)
        self.assertEqual(self.supervisor.worker_status(task, "worker")["state"], "revoked")

    def test_worker_exception_cannot_read_control_state_or_write_code_hardlink(self):
        policy = Policy(self.root, read=("**",), write=("**",), external_inputs=(self.state,))
        task = self.supervisor.propose(policy)
        self.supervisor.approve_task(task)
        self.supervisor.approve_worker(Grant(task, "worker", read=("**",), write=("**",)))
        with self.assertRaises(ValueError):
            self.supervisor.request_exception(Action(task, "worker", "read", str(self.state)))
        alias = self.root / "code-alias.py"
        source = Path(__file__).resolve().parents[1] / "records.py"
        try:
            os.link(source, alias)
        except OSError:
            self.skipTest("Filesystem does not permit a cross-directory hardlink")
        self.addCleanup(alias.unlink)
        with self.assertRaises(ValueError):
            self.supervisor.request_exception(Action(task, "worker", "write", str(alias)))

    def test_task_report_does_not_include_another_projects_memory(self):
        task = self.supervisor.propose(Policy(self.root, read=("**",)))
        self.supervisor.approve_task(task)
        other_project = self.base / "other-project"
        other_project.mkdir()
        memory = Provenance(self.state.with_suffix(".memory.sqlite"))
        try:
            memory.register_task(task, str(self.root.resolve()))
            memory.register_task("other-task", str(other_project.resolve()))
            unrelated = memory.record_input(
                "other-task", "other-worker", "repository", "OTHER_PROJECT_PRIVATE_REFERENCE")
        finally:
            memory.close()
        output = self.base / "report.html"
        result = subprocess.run(
            [sys.executable, "-m", "planner", "--state", str(self.state), "--json",
             "report", task, str(output)], text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["report"], str(output.resolve()))
        report = output.read_text()
        self.assertFalse(unrelated.id in report, "Report leaked another project's record")
        self.assertFalse("OTHER_PROJECT_PRIVATE_REFERENCE" in report, "Report leaked another project's reference")

    def test_concurrent_refusal_is_not_overwritten_by_stale_approval(self):
        task = self.supervisor.propose(Policy(self.root, read=("**",), deny_read=(".env",)))
        self.supervisor.approve_task(task)
        self.supervisor.approve_worker(Grant(task, "worker", read=("**",)))
        (self.root / ".env").write_text("DUMMY_SECRET")
        request = self.supervisor.request_exception(Action(task, "worker", "read", ".env"))
        approval_checked = threading.Event()
        resume_approval = threading.Event()
        refusal_finished = threading.Event()
        errors = []

        def clock():
            if not approval_checked.is_set():
                approval_checked.set()
                if not resume_approval.wait(5):
                    raise RuntimeError("Timed out coordinating concurrent approval")
            return time.time()

        def approve():
            control = Supervisor(self.state, clock=clock)
            try:
                control.approve_exception(request, disclose_sensitive=True)
            except Exception as error:
                errors.append(error)
            finally:
                control.close()

        def refuse():
            control = Supervisor(self.state)
            try:
                control.refuse_exception(request)
            except Exception as error:
                errors.append(error)
            finally:
                control.close()
                refusal_finished.set()

        approval = threading.Thread(target=approve)
        refusal = threading.Thread(target=refuse)
        approval.start()
        try:
            self.assertTrue(approval_checked.wait(5))
            refusal.start()
            # With correct locking refusal waits for approval, then revokes it.
            # Without locking it completes first and the stale approval revives it.
            refusal_finished.wait(0.2)
        finally:
            resume_approval.set()
            approval.join(5)
            if refusal.ident is not None:
                refusal.join(5)
        self.assertFalse(approval.is_alive() or refusal.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.supervisor.exception(request)["state"], "revoked")

    def test_cancellation_and_revocation_cover_concurrent_child_assignments(self):
        for operation in ("cancel", "revoke"):
            with self.subTest(operation=operation):
                task = self.supervisor.propose(Policy(self.root, read=("**",)))
                self.supervisor.approve_task(task)
                self.supervisor.approve_worker(Grant(task, "parent", read=("**",)))
                assignment_checked = threading.Event()
                resume_assignment = threading.Event()
                stop_finished = threading.Event()
                errors = []

                def clock():
                    if not assignment_checked.is_set():
                        assignment_checked.set()
                        if not resume_assignment.wait(5):
                            raise RuntimeError("Timed out coordinating concurrent assignment")
                    return time.time()

                def assign():
                    control = Supervisor(self.state, clock=clock)
                    try:
                        control.approve_worker(Grant(task, "child", read=("**",), parent_id="parent"))
                    except Exception as error:
                        errors.append(error)
                    finally:
                        control.close()

                def stop():
                    control = Supervisor(self.state)
                    try:
                        if operation == "cancel":
                            control.cancel(task)
                        else:
                            control.revoke_worker(task, "parent")
                    except Exception as error:
                        errors.append(error)
                    finally:
                        control.close()
                        stop_finished.set()

                assignment = threading.Thread(target=assign)
                stopping = threading.Thread(target=stop)
                assignment.start()
                try:
                    self.assertTrue(assignment_checked.wait(5))
                    stopping.start()
                    stop_finished.wait(0.2)
                finally:
                    resume_assignment.set()
                    assignment.join(5)
                    if stopping.ident is not None:
                        stopping.join(5)
                self.assertFalse(assignment.is_alive() or stopping.is_alive())
                self.assertEqual(errors, [])
                self.assertFalse(self.supervisor.authorize(Action(task, "child", "read", "README.md")).allowed)
                with self.assertRaises(ValueError):
                    self.supervisor.approve_worker(Grant(task, "later-child", read=("**",), parent_id="parent"))


if __name__ == "__main__":
    unittest.main()
