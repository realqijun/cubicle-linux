"""Ctrl-C in the task runner, without the Linux runtime: the stop request and the records it leaves."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest

from planner.launcher import _apply, _StopRequest
from planner.policy import Policy
from planner.records import Grant, ProtectionStatus
from planner.supervisor import Supervisor

ROOT = Path(__file__).resolve().parents[2]
VERIFIED = ProtectionStatus(True, "fixture", "verified fixture", True)
# The planner CLI, with Ctrl-C pressed while it checks the runtime, before any worker starts.
PRESSED_DURING_CHECK = """
import signal, sys
from planner.__main__ import main
from planner.native import confined_opencode
from planner.records import ProtectionStatus

def checked(opencode, helper):
    signal.raise_signal(signal.SIGINT)
    return (ProtectionStatus(True, "linux-connected", "fixture", True),
            ProtectionStatus(True, "opencode-confined", "fixture", True))

confined_opencode.runtime_status = checked
sys.exit(main(sys.argv[1:]))
"""


@unittest.skipUnless(os.name == "posix", "Interrupt delivery requires POSIX signals")
class StopRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        # A SIGINT that reaches this guard instead of the runner's handler is recorded, never raised.
        self.guarded: list[int] = []

        def guard(signum: int, frame: object) -> None:
            self.guarded.append(signum)

        self.guard = guard
        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, guard))

    def test_every_press_is_recorded_and_none_raises(self) -> None:
        stop = _StopRequest()
        stop.install()
        try:
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)  # A second press cannot interrupt the cleanup either.
        finally:
            stop.restore()
        self.assertTrue(stop.requested)
        self.assertEqual(self.guarded, [])
        with self.assertRaises(KeyboardInterrupt):
            stop.check()  # Acted on only where the runner checks.

    def test_halt_is_called_for_each_press_inside_halting_only(self) -> None:
        stop = _StopRequest()
        halts: list[int] = []
        stop.install()
        try:
            signal.raise_signal(signal.SIGINT)
            with stop.halting(lambda: halts.append(1)):
                signal.raise_signal(signal.SIGINT)
                signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
        finally:
            stop.restore()
        self.assertEqual(halts, [1, 1])
        self.assertTrue(stop.requested)

    def test_the_previous_handler_is_restored(self) -> None:
        stop = _StopRequest()
        stop.install()
        self.assertIsNot(signal.getsignal(signal.SIGINT), self.guard)
        stop.restore()
        self.assertIs(signal.getsignal(signal.SIGINT), self.guard)
        signal.raise_signal(signal.SIGINT)
        self.assertEqual(self.guarded, [signal.SIGINT])
        self.assertFalse(stop.requested)

    def test_python_handling_stays_outside_the_main_thread_and_when_ignored(self) -> None:
        elsewhere = _StopRequest()
        thread = threading.Thread(target=elsewhere.install)
        thread.start()
        thread.join()
        self.assertIs(signal.getsignal(signal.SIGINT), self.guard)
        elsewhere.restore()
        self.assertIs(signal.getsignal(signal.SIGINT), self.guard)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        ignored = _StopRequest()
        ignored.install()
        self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
        ignored.restore()
        self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)


@unittest.skipUnless(os.name == "posix", "Interrupt delivery requires POSIX signals")
class PreparationInterruptTests(unittest.TestCase):
    def test_ctrl_c_while_the_runtime_is_checked_withdraws_the_attempt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planner-interrupt-") as directory:
            base = Path(directory)
            project = base / "project"
            (project / "src").mkdir(parents=True)
            (project / "src/app.py").write_text("VALUE = 1\n")
            state = base / "operator" / "state.sqlite"

            def cli(*args: str, script: str | None = None) -> subprocess.CompletedProcess[str]:
                command = [sys.executable, *(["-c", script] if script else ["-m", "planner"])]
                return subprocess.run([*command, "--json", "--state", str(state), *args], cwd=ROOT,
                                      capture_output=True, text=True, timeout=60)

            task = json.loads(cli("propose", str(project), "--template", "source").stdout)
            cli("approve-task", task["id"], "--revision", str(task["revision"]), "--assign", "worker")
            run = ["run", task["id"], "worker", "--prompt", "Change VALUE to 2.", "--model", "fixture/model",
                   "--provider-url", "http://127.0.0.1:9/v1", "--opencode", str(base / "opencode"),
                   "--fuse-helper", str(base / "fuse-bridge"), "--runs", str(base / "operator" / "runs")]
            interrupted = cli(*run, script=PRESSED_DURING_CHECK)
            self.assertEqual(interrupted.returncode, 0, interrupted.stderr)
            self.assertNotIn("Traceback", interrupted.stderr)
            result = json.loads(interrupted.stdout)
            self.assertEqual((result["outcome"], result["attempt_state"], result["application"]["state"]),
                             ("stopped", "blocked", "not_applied"))
            self.assertTrue(result["stop_requested"])
            # The next run is not refused for an unresolved attempt; this host's own runtime check decides it.
            again = cli(*run)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertNotEqual(json.loads(again.stdout)["attempt"], result["attempt"])
            self.assertEqual((project / "src/app.py").read_text(), "VALUE = 1\n")


class ApplicationInterruptTests(unittest.TestCase):
    def test_an_interrupted_application_is_recorded_before_it_stops(self) -> None:
        with tempfile.TemporaryDirectory(prefix="planner-interrupt-") as directory:
            base = Path(directory)
            (base / "project").mkdir()
            supervisor = Supervisor(base / "state.sqlite")
            try:
                task = supervisor.propose(Policy(base / "project", read=("**",), write=("**",), tools=("shell",)))
                supervisor.approve_task(task)
                supervisor.approve_worker(Grant(task, "worker", ("**",), ("**",), ("shell",)))
                attempt = supervisor.prepare_attempt(task, "worker")["id"]
                supervisor.verify_attempt(attempt, VERIFIED, VERIFIED)
                supervisor.start_attempt(attempt)
                supervisor.finish_attempt(attempt, successful=True, cleanup_verified=True)

                class Interrupted:
                    """A changeset whose application is interrupted after its journal was written."""
                    journal = base / "journal.jsonl"

                    def preview(self) -> tuple[()]:
                        return ()

                    def apply(self, checked: object) -> list[object]:
                        self.journal.write_text("")
                        raise KeyboardInterrupt

                with self.assertRaises(KeyboardInterrupt):
                    _apply(supervisor, attempt, Interrupted(), "auto")  # type: ignore[arg-type]
                record = supervisor.application(attempt)
            finally:
                supervisor.close()
        self.assertEqual(record["state"], "interrupted")  # Never left 'applying'; recover replays nothing.
        self.assertEqual(record["reason"], "KeyboardInterrupt during application")


if __name__ == "__main__":
    unittest.main()
