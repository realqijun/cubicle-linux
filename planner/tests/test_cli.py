from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
import unittest
from unittest.mock import patch

from planner.__main__ import main
from planner.records import ProtectionStatus
from planner.supervisor import Supervisor

VERIFIED = (ProtectionStatus(True, "linux-connected", "fixture", True),
            ProtectionStatus(True, "opencode-confined", "fixture", True))


class CLITests(unittest.TestCase):
    def test_reviewed_proposal_and_status_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            state = base / "state" / "control.sqlite"

            def run(*args):
                result = subprocess.run([sys.executable, "-m", "planner", "--state", str(state),
                                         "--json", *args], text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            task = run("propose", str(project), "--template", "source")["id"]
            self.assertEqual(run("review", task)["state"], "proposed")
            run("approve-task", task, "--revision", "1")
            run("assign", task, "worker", "--read", "src/**", "--write", "src/**")
            self.assertTrue(run("decide", task, "worker", "write", "src/app.py")["allowed"])
            self.assertFalse(run("decide", task, "worker", "write", "tests/test_app.py")["allowed"])
            run("approve-template", task, "source-child", "worker", "--read", "src/**", "--write", "src/**")
            run("delegate", task, "child", "--parent", "worker", "--template", "source-child",
                "--read", "src/app.py", "--write", "src/app.py")
            self.assertEqual(run("worker", task, "child")["template"], "source-child")
            self.assertEqual(len(run("templates", task)), 1)
            attempt = run("prepare-attempt", task, "child")
            self.assertEqual(run("attempt", attempt["id"])["state"], "prepared")
            self.assertEqual(len(run("reconcile", task)), 1)
            self.assertEqual(run("attempts", task)[0]["state"], "interrupted")
            run("cancel", task)
            self.assertEqual(run("status", task)["state"], "cancelled")


class ResumeTests(unittest.TestCase):
    """`resume` and `preflight` verify the connected runtime exactly as `run` does.

    The runtime statuses are injected: they exercise the control-state rules,
    not OS confinement. The real runtime is covered by `test_launcher`.
    """

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        project = base / "project"
        project.mkdir()
        (project / ".env").write_text("TOKEN=DUMMY_SECRET\n")
        (project / "README.md").write_text("Example.\n")
        self.state = base / "state" / "control.sqlite"
        self.paths = (base / "opencode", base / "fuse-bridge")
        self.runtime = ["--opencode", str(self.paths[0]), "--fuse-helper", str(self.paths[1])]
        self.statuses = VERIFIED
        self.probed: list[tuple[Path, Path]] = []
        self.task = self.cli("propose", str(project), "--template", "source")[1]["id"]
        self.cli("approve-task", self.task, "--revision", "1", "--assign", "worker")

    def cli(self, *args: str) -> tuple[int, Any]:
        def runtime_status(opencode: Path, helper: Path) -> tuple[ProtectionStatus, ProtectionStatus]:
            self.probed.append((opencode, helper))
            return self.statuses
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("planner.native.confined_opencode.runtime_status", runtime_status), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--state", str(self.state), "--json", *args])
        return code, json.loads(stdout.getvalue() or stderr.getvalue())

    def deny(self, times: int = 3) -> None:
        for _ in range(times):
            self.assertFalse(self.cli("decide", self.task, "worker", "read", ".env")[1]["allowed"])

    def state_of(self, worker: str = "worker") -> str:
        return str(self.cli("worker", self.task, worker)[1]["state"])

    def refused(self, *extra: str) -> str:
        code, result = self.cli("resume", self.task, "worker", *self.runtime, *extra)
        self.assertEqual(code, 2, result)
        self.assertTrue(result["blocked"], result)
        return str(result["reason"])

    def test_worker_suspended_by_repeated_denials_resumes_after_runtime_verification(self) -> None:
        self.deny()
        self.assertEqual(self.state_of(), "suspended")
        self.assertFalse(self.cli("decide", self.task, "worker", "read", "README.md")[1]["allowed"])
        self.assertEqual(self.cli("resume", self.task, "worker", *self.runtime), (0, {"state": "resumed"}))
        self.assertEqual(self.probed, [self.paths])
        self.assertEqual(self.state_of(), "active")
        self.assertEqual(self.cli("worker", self.task, "worker")[1]["grant"]["read"], ["**"])
        resumed = self.cli("history", self.task)[1][-1]
        self.assertEqual({key: resumed[key] for key in ("worker", "operation", "tool", "allowed", "grant_revision")},
                         {"worker": "worker", "operation": "resume", "tool": "operator", "allowed": 1,
                          "grant_revision": 1})
        self.assertEqual(self.cli("export", self.task)[1]["decisions"][-1], resumed)
        # A resumed worker starts a new retry sequence; the earlier refusals do not count.
        self.deny(2)
        self.assertEqual(self.state_of(), "active")
        self.assertTrue(self.cli("decide", self.task, "worker", "read", "README.md")[1]["allowed"])
        self.deny()
        self.assertEqual(self.state_of(), "suspended")

    def test_unavailable_or_unverified_runtime_blocks_resume(self) -> None:
        self.deny()
        for backend, connector in ((ProtectionStatus(False, "linux-connected", "Connected runtime requires Linux"),
                                    ProtectionStatus(False, "opencode-confined", "Runtime unavailable")),
                                   (VERIFIED[0], ProtectionStatus(True, "opencode-confined", "unverified"))):
            with self.subTest(reason=connector.reason):
                self.statuses = (backend, connector)
                self.assertIn("BLOCKED: ", self.refused())
                self.assertEqual(self.state_of(), "suspended")
        self.assertEqual([row["operation"] for row in self.cli("history", self.task)[1]], ["read"] * 3)

    def test_resume_never_revives_revoked_cancelled_or_superseded_authority(self) -> None:
        self.deny()
        self.cli("revoke", self.task, "worker")
        self.assertIn("revoked", self.refused())
        self.assertEqual(self.state_of(), "revoked")
        self.cli("assign", self.task, "other", "--read", "**")
        self.cli("revise", self.task, "--policy", str(Path(__file__).parents[1] / "templates" / "source.toml"))
        self.cli("approve-task", self.task, "--revision", "2")
        code, result = self.cli("resume", self.task, "other", *self.runtime)
        self.assertEqual(code, 2, result)
        self.assertEqual(self.state_of("other"), "revoked")
        self.cli("cancel", self.task)
        self.assertIn("not approved", self.refused())

    def test_only_a_suspended_worker_can_be_resumed(self) -> None:
        self.assertIn("suspended", self.refused())
        self.assertEqual(self.cli("history", self.task)[1], [])

    def test_unresolved_attempt_blocks_resume_until_recovery_verifies_cleanup(self) -> None:
        supervisor = Supervisor(self.state)
        self.addCleanup(supervisor.close)
        attempt = supervisor.prepare_attempt(self.task, "worker")["id"]
        supervisor.verify_attempt(attempt, *VERIFIED)
        supervisor.start_attempt(attempt)
        self.deny()
        self.assertEqual(self.cli("attempt", attempt)[1]["state"], "stopping")
        self.assertIn("Reconcile", self.refused("--cleanup-verified"))
        self.cli("reconcile", self.task)
        self.assertIn("cleanup_unverified", self.refused())
        self.assertEqual(self.state_of(), "suspended")
        self.assertEqual(self.cli("resume", self.task, "worker", *self.runtime, "--cleanup-verified"),
                         (0, {"state": "resumed"}))
        recovered = self.cli("attempt", attempt)[1]
        self.assertEqual((recovered["state"], recovered["reason"]), ("reconciled", "recovery_verified"))
        self.assertTrue(recovered["cleanup_verified"])
        self.assertEqual(self.state_of(), "active")
        self.assertEqual(self.cli("history", self.task)[1][-1]["operation"], "resume")

    def test_expired_task_cannot_be_resumed(self) -> None:
        self.deny()
        supervisor = Supervisor(self.state)
        self.addCleanup(supervisor.close)
        supervisor.clock = lambda: float(supervisor.status(self.task)["expires"]) + 1
        with self.assertRaisesRegex(ValueError, "expired"):
            supervisor.resume(self.task, "worker", *VERIFIED)
        self.assertEqual(self.state_of(), "suspended")

    def test_preflight_reports_the_connected_runtime_status(self) -> None:
        code, result = self.cli("preflight", self.task, "worker", *self.runtime)
        self.assertEqual((code, result["ready"]), (0, True))
        self.assertEqual([check["backend"] for check in result["checks"]], ["linux-connected", "opencode-confined"])
        self.statuses = (ProtectionStatus(False, "linux-connected", "Connected runtime requires Linux"),
                         ProtectionStatus(False, "opencode-confined", "Runtime unavailable"))
        code, result = self.cli("preflight", self.task, "worker", *self.runtime)
        self.assertEqual((code, result["ready"]), (2, False))
        self.assertEqual(result["checks"][0]["reason"], "Connected runtime requires Linux")
        self.assertEqual(self.probed, [self.paths, self.paths])


if __name__ == "__main__":
    unittest.main()
