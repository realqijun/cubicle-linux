"""The guided run on a user's project: scripts/run.sh and planner.guided."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from planner.guided import Console, Guide, Planner, Settings, Stop, locate, project_directory
from planner.launcher import _request_reconsideration
from planner.records import Action, ProtectionStatus
from planner.scripted_provider import ScriptedProvider
from planner.supervisor import Supervisor
from planner.tests.runtime_fixtures import press_ctrl_c, shell

ROOT = Path(__file__).resolve().parents[2]
GATE = (sys.platform == "linux" and os.environ.get("PLANNER_RUN_GUIDED") == "1"
        and bool(os.environ.get("PLANNER_OPENCODE_BINARY")) and bool(os.environ.get("PLANNER_FUSE_BRIDGE")))
ORIGINAL = {"README.md": "Example service.\n", "src/app.py": "VALUE = 1\n",
            "tests/test_app.py": "from src.app import VALUE\nassert VALUE == 2\n", ".env": "TOKEN=DUMMY_SECRET\n"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
VERIFIED = ProtectionStatus(True, "fixture-only", "Not OS evidence", verified=True)


class GuidedCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="planner-guided-test-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.project = self.base / "project"
        for name, text in ORIGINAL.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)
        self.home = self.base / "home"
        self.binaries = self.base / "bin"
        self.binaries.mkdir()
        (self.binaries / "python3").symlink_to(sys.executable)
        self.environment = {**os.environ, "PATH": str(self.binaries) + os.pathsep + os.environ.get("PATH", ""),
                            "PLANNER_HOME": str(self.home), "PLANNER_API_KEY_ENV": "GUIDED_TEST_KEY",
                            "NO_COLOR": "1"}
        self.environment.pop("GUIDED_TEST_KEY", None)

    def script(self, *args, input="", env=None, timeout=60):
        command = ["bash", str(ROOT / "scripts/run.sh"), *args]
        process = subprocess.Popen(command, cwd=self.base, env=env or self.environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            for stop in (signal.SIGINT, signal.SIGKILL):  # Ctrl-C first: the planner stops its worker.
                try:
                    os.killpg(process.pid, stop)
                except ProcessLookupError:
                    break
                try:
                    process.communicate(timeout=60)
                    break
                except subprocess.TimeoutExpired:
                    continue
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def unchanged(self, *names):
        for name in names:
            self.assertEqual((self.project / name).read_text(), ORIGINAL[name], name)


class FakePlanner(Planner):
    """The real planner CLI on real state, except for commands that need the Linux runtime.

    ``runs`` stand in for the confined worker, one per run. Each records its
    decisions in the real supervisor and returns what ``run_task`` would.
    """

    def __init__(self, state, runs, *, resume=None):
        super().__init__(state)
        self.runs, self.resume = list(runs), resume
        self.commands, self.run_args = [], []

    def call(self, *args):
        command = self.command(*args)
        self.commands.append(command)
        name = args[2] if args[0] == "--task-seconds" else args[0]
        if name == "resume":
            return self.resume(self)
        if name == "apply":
            return apply_held(self, args[1])
        return super().call(*args)

    def run(self, args, output, console):
        self.run_args.append(args)
        output.write_text(json.dumps(self.runs.pop(0)(self, args[1], "--mode" in args)))
        return 0

    def supervisor(self):
        return Supervisor(self.state)

    def named(self, name):
        """The commands run for one planner command name, such as ``resume``."""
        def command_name(command):
            args = command[command.index("--state") + 2:]
            return args[2] if args[0] == "--task-seconds" else args[0]

        return [command for command in self.commands if command_name(command) == name]


def worker_run(*, refuse=(), denied=(), write=None, suspend=None, check=None):
    """A stand-in run: refusals, then an edit, recorded as the real runner would.

    ``refuse`` are tool refusals; ``denied`` are refusals by the mediated filesystem.
    """
    def run(planner, task, review):
        supervisor = planner.supervisor()
        try:
            root = supervisor.policy(task).root
            attempt = supervisor.prepare_attempt(task, "worker")["id"]
            if check is not None:
                check(supervisor, task)
            for operation, resource, tool in refuse:
                supervisor.authorize(Action(task, "worker", operation, resource, tool))
            if suspend is not None:
                for _ in range(3):  # The denial limit: identical refusals suspend the worker.
                    supervisor.authorize(Action(task, "worker", *suspend))
            denials = [(operation, str(root / name)) for operation, name in denied]
            supervisor.record_filesystem_denials(task, "worker", denials)
            requests = _request_reconsideration(supervisor, task, "worker", attempt, denials)
            if supervisor.attempt(attempt)["state"] == "prepared":
                supervisor.abandon_attempt(attempt, "stand-in run")
            changes, application = [], {"state": "applied", "reason": "complete"}
            if write is not None:
                name, content = write
                changes = [{"path": name, "operation": "modify", "digest": None}]
                if review:
                    journal = planner.state.parent / "runs" / attempt / "journal.jsonl"
                    target = journal.parent / "fs" / "backing" / name
                    application = {"state": "retained", "reason": "awaiting_user_review", "journal": str(journal)}
                else:
                    target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            elif review:
                application = {"state": "retained", "reason": "awaiting_user_review", "journal": ""}
        finally:
            supervisor.close()
        return {"task": task, "worker": "worker", "attempt": attempt, "mode": "review" if review else "auto",
                "outcome": "authority_lost" if suspend else "completed", "error": None,
                "attempt_state": "cancelled" if suspend else "finished", "cleanup_verified": True,
                "requests": requests, "application": {**application, "changes": changes}}
    return run


def apply_held(planner, attempt):
    journal = planner.state.parent / "runs" / attempt / "journal.jsonl"
    backing = journal.parent / "fs" / "backing"
    supervisor = planner.supervisor()
    try:
        root = supervisor.policy(supervisor.attempt(attempt)["task"]).root
    finally:
        supervisor.close()
    for path in backing.rglob("*"):
        if path.is_file():
            (root / path.relative_to(backing)).write_text(path.read_text())
    return {"state": "applied", "reason": "complete"}


class GuideTests(GuidedCase):
    def guide(self, answers, runs, *, resume=None, provider="https://provider.invalid/v1", **options):
        settings = Settings(self.home, "fixture/model", provider, "GUIDED_TEST_KEY",
                            self.base / "opencode", self.base / "fuse-bridge")
        records = project_directory(self.home, self.project)
        planner = FakePlanner(records / "state.sqlite", runs, resume=resume)
        stdout, stderr = io.StringIO(), io.StringIO()
        console = Console(io.StringIO(answers), stdout, stderr)
        with patch.dict(os.environ, {"NO_COLOR": "1", "GUIDED_TEST_KEY": "dummy-fixture-credential"}):
            guide = Guide(settings, self.project, "Change VALUE to 2 in src/app.py.", planner=planner,
                          console=console, **options)
            status = guide.main()
        self.planner, self.session = planner, guide.session
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return status

    def task(self):
        return json.loads((self.session / "session.json").read_text())["task"]

    def status(self):
        supervisor = self.planner.supervisor()
        try:
            return supervisor.status(self.task())["state"]
        finally:
            supervisor.close()

    def test_plan_shows_the_model_proposal_and_allows_a_slow_model(self):
        proposal = {"template": "source", "read": ["src/**"], "write": ["src/app.py"], "tools": ["file"]}

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                data = json.dumps({"choices": [{"message": {"content": json.dumps(proposal)}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status = self.guide("y\n", [worker_run(write=("src/app.py", "VALUE = 2\n"))], plan=True,
                                provider=f"http://127.0.0.1:{server.server_port}/v1")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertEqual(status, 0, self.stderr)
        self.assertIn("The model narrowed the trusted source template.", self.stdout)
        self.assertIn("  write  src/app.py", self.stdout)
        [command] = self.planner.named("plan")
        self.assertEqual(command[command.index("--timeout") + 1], "180")
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")

    def test_ctrl_c_ends_the_session_without_asking_about_requests(self):
        def stopped(planner, task, review):
            result = worker_run(denied=[("write", "tests/test_app.py")])(planner, task, review)
            return {**result, "outcome": "stopped", "error": "Stopped by the operator", "attempt_state": "failed",
                    "application": {"state": "not_applied", "reason": "attempt_failed:interrupted", "changes": []}}

        self.assertEqual(self.guide("y\n", [stopped]), 130)
        self.assertIn("  stopped    you pressed Ctrl-C, so nothing from this run was applied", self.stdout)
        self.assertNotIn("wanted to change", self.stdout)
        self.assertNotIn("ERROR", self.stderr)
        self.assertEqual(self.status(), "cancelled")
        self.unchanged("src/app.py", "tests/test_app.py")

    def approvals(self):
        supervisor = self.planner.supervisor()
        try:
            return supervisor.export(self.task())["approvals"]
        finally:
            supervisor.close()

    def test_declining_the_permissions_cancels_and_runs_nothing(self):
        status = self.guide("n\n", [])
        self.assertEqual(status, 0, self.stderr)
        self.assertIn("Permissions for the worker, in " + str(self.project), self.stdout)
        self.assertIn("write  src/**, outputs/**  except tests/**", self.stdout)
        self.assertIn("Approve? [y/N] n\nNot approved, so nothing ran. Your project is unchanged.", self.stdout)
        self.assertEqual(self.planner.run_args, [])
        self.assertEqual(self.status(), "cancelled")
        self.assertEqual(self.planner.named("approve-task"), [])
        self.unchanged(*ORIGINAL)
        self.assertIn("Not approved", (self.session / "console.log").read_text())

    def test_allowed_fix_is_applied_shown_and_saved(self):
        status = self.guide("y\n", [worker_run(write=("src/app.py", "VALUE = 2\n"))])
        self.assertEqual(status, 0, self.stderr)
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")
        result = self.stdout[self.stdout.index("\nResult\n"):]
        self.assertIn("  changed    src/app.py\n", result)
        self.assertIn("  blocked    nothing - the worker stayed within its permissions", result)
        self.assertIn("  -VALUE = 1\n  +VALUE = 2\n", result)
        [args] = self.planner.run_args
        self.assertIn("--alert", args)
        self.assertNotIn("--mode", args)
        self.assertEqual(args[args.index("--api-key-env") + 1], "GUIDED_TEST_KEY")
        self.assertEqual(Path(args[args.index("--runs") + 1]), project_directory(self.home, self.project) / "runs")
        self.assertFalse(Path(args[args.index("--runs") + 1]).is_relative_to(self.project))
        for name in ("session.json", "console.log", "proposal.json", "audit.json", "report.html", "run-1/run.json"):
            self.assertTrue((self.session / name).is_file(), name)
        self.assertEqual(self.status(), "cancelled")  # Closed once the session ended.
        self.assertIn("Full log:  scripts/run.sh log " + str(self.project), self.stdout)
        self.assertEqual((self.session / "console.log").read_text(), self.stdout + self.stderr)

    def test_yes_approves_the_permissions_but_never_an_access_request(self):
        run = worker_run(refuse=[("write", "tests/test_app.py", "file")])
        status = self.guide("", [run], yes=True)  # No input: every question is a no.
        self.assertEqual(status, 0, self.stderr)
        self.assertIn("Approve? [y/N] y (--yes)", self.stdout)
        self.assertIn("The worker wanted to change tests/test_app.py.\n  Approve? [y/N] \n  Refused.", self.stdout)
        self.assertEqual(self.planner.named("approve-exception"), [])
        self.assertEqual(len(self.planner.named("refuse-exception")), 1)
        self.assertEqual(len(self.planner.run_args), 1)  # No rerun was offered or taken.
        self.assertNotIn("Run again", self.stdout)
        self.unchanged("tests/test_app.py")

    def test_approved_request_reruns_a_fresh_worker_that_can_use_it(self):
        seen = []

        def approved(supervisor, task):
            seen.extend(item["state"] for item in supervisor.export(task)["approvals"])

        runs = [worker_run(refuse=[("write", "tests/test_app.py", "file")], denied=[("write", "tests/test_app.py")]),
                worker_run(write=("tests/test_app.py", "APPROVED\n"), check=approved)]
        status = self.guide("y\ny\n\n", runs)
        self.assertEqual(status, 0, self.stderr)
        self.assertEqual(seen, ["approved", "approved"])  # One question covered both tools.
        self.assertEqual(self.stdout.count("The worker wanted to change tests/test_app.py."), 1)
        self.assertIn("Run again with these approvals? [Y/n] \nThe next run starts a fresh worker; "
                      "it does not remember this one.", self.stdout)
        self.assertEqual(len(self.planner.run_args), 2)
        self.assertEqual(self.planner.run_args[0], self.planner.run_args[1])  # Same task, worker and prompt.
        self.assertEqual((self.project / "tests/test_app.py").read_text(), "APPROVED\n")
        second = self.stdout[self.stdout.index("\nRun 2\n"):]
        self.assertIn("  -assert VALUE == 2\n  +APPROVED", second)  # The approved file was copied before the run.
        self.assertIn("  blocked    2 attempts outside the permissions", self.stdout)

    def test_sensitive_read_needs_a_typed_confirmation(self):
        # Approve the permissions, approve the request, then type the confirmation (and decline a rerun).
        for answers, expected in (("y\ny\nno\n", "refused"), ("y\ny\nyes\nn\n", "approved")):
            with self.subTest(answers=answers):
                status = self.guide(answers, [worker_run(refuse=[("read", ".env", "file")])])
                self.assertEqual(status, 0, self.stderr)
                self.assertIn("Warning: .env is protected because it may hold passwords, keys or tokens.", self.stdout)
                self.assertIn("Approving sends its contents to the model provider at https://provider.invalid/v1",
                              self.stdout)
                self.assertIn("Type yes to send .env to the model provider: ", self.stdout)
                [request] = json.loads((self.session / "session.json").read_text())["runs"][0]["requests"]
                self.assertEqual((request["answer"], request["sensitive"]), (expected, True))
                approvals = self.planner.named("approve-exception")
                if expected == "approved":
                    [command] = approvals
                    self.assertEqual(command[-1], "--confirm-sensitive-provider-disclosure")
                else:
                    self.assertEqual(approvals, [])
                self.assertNotIn("DUMMY_SECRET", self.stdout + self.stderr)

    def test_suspended_worker_is_resumed_through_the_resume_command(self):
        def resume(planner):
            command = planner.named("resume")[-1]
            supervisor = planner.supervisor()
            try:  # The real resume, with the runtime checks it makes stubbed as passed.
                supervisor.resume(command[command.index("resume") + 1], "worker", VERIFIED, VERIFIED)
            finally:
                supervisor.close()
            return {"state": "resumed"}

        runs = [worker_run(suspend=("write", "tests/test_app.py", "file")),
                worker_run(write=("src/app.py", "VALUE = 2\n"))]
        status = self.guide("y\ny\n", runs, resume=resume)
        self.assertEqual(status, 0, self.stderr)
        self.assertIn("The worker was suspended because it kept repeating the same blocked operation.", self.stdout)
        self.assertNotIn("Access requests", self.stdout)
        [command] = self.planner.named("resume")
        self.assertEqual(command, [sys.executable, "-m", "planner", "--state", str(self.planner.state), "resume",
                                   self.task(), "worker", "--opencode", str(self.base / "opencode"),
                                   "--fuse-helper", str(self.base / "fuse-bridge")])
        self.assertEqual(len(self.planner.run_args), 2)
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")
        log = self.script("log", str(self.project)).stdout
        first = log[log.index("Run 1"):log.index("Run 2")]
        decisions = first[:first.index("  Result")]
        self.assertEqual(decisions.count("BLOCKED  write tests/test_app.py"), 3)
        self.assertNotIn("resume", decisions)  # The resume is not a file access.
        self.assertRegex(first, r"    The worker was suspended for repeating a blocked operation\.\n"
                                r"    \d\d:\d\d:\d\d  you resumed the worker\n")

    def test_declining_to_resume_ends_the_session_as_a_failure(self):
        status = self.guide("y\n\n", [worker_run(suspend=("write", "tests/test_app.py", "file"))])
        self.assertEqual(status, 1)
        self.assertIn("Resume the worker and run again? [y/N] \n", self.stdout)
        self.assertEqual(self.planner.named("resume"), [])
        self.assertEqual(self.stderr, "")  # The suspension is explained, not reported as an error.

    def test_failed_resume_is_reported(self):
        def broken(planner):
            raise Stop("linux-connected: FUSE bridge helper unavailable")  # As Planner.call reports it.

        status = self.guide("y\ny\n", [worker_run(suspend=("write", "tests/test_app.py", "file"))], resume=broken)
        self.assertEqual(status, 1)
        self.assertIn("ERROR: Could not resume the worker: linux-connected: FUSE bridge helper unavailable", self.stderr)
        self.assertEqual(len(self.planner.run_args), 1)

    def test_review_mode_holds_changes_until_applied(self):
        status = self.guide("y\ny\n", [worker_run(write=("src/app.py", "VALUE = 2\n"))], review=True)
        self.assertEqual(status, 0, self.stderr)
        [args] = self.planner.run_args
        self.assertEqual(args[args.index("--mode") + 1], "review")
        self.assertIn("  held       src/app.py  (not in your project yet)", self.stdout)
        self.assertIn("  -VALUE = 1\n  +VALUE = 2\n", self.stdout)
        self.assertIn("Apply them now? [y/N] y\nApplied to your project.", self.stdout)
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")
        self.assertEqual(self.status(), "cancelled")

    def test_declined_review_keeps_the_project_and_the_task_for_later(self):
        status = self.guide("y\nn\n", [worker_run(write=("src/app.py", "VALUE = 2\n"))], review=True)
        self.assertEqual(status, 0, self.stderr)
        self.unchanged("src/app.py")
        [args] = self.planner.run_args
        attempt = json.loads((self.session / "run-1/run.json").read_text())["attempt"]
        [hint] = [line for line in self.stdout.splitlines() if line.endswith(" apply " + attempt)]
        self.assertEqual(hint, f"  cd {shlex.quote(str(ROOT))} && python3 -m planner --state "
                               f"{shlex.quote(str(self.planner.state))} apply {attempt}")
        self.assertEqual(self.status(), "approved")
        self.assertEqual(self.planner.named("apply"), [])
        # The same command prefix works from another folder.
        status = subprocess.run(["bash", "-c", hint.rsplit(" apply ", 1)[0] + " status " + self.task()],
                                cwd=self.base, capture_output=True, text=True, timeout=60)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["state"], "approved")

    def test_a_run_without_an_attempt_closes_the_task_and_an_unfinished_one_keeps_it(self):
        def lost(planner, task, review):
            raise Stop("The planner stopped before the worker started.")

        def unfinished(planner, task, review):
            supervisor = planner.supervisor()
            try:
                attempt = supervisor.prepare_attempt(task, "worker")["id"]
                supervisor.verify_attempt(attempt, VERIFIED, VERIFIED)
                supervisor.start_attempt(attempt)
            finally:
                supervisor.close()
            raise Stop("The planner stopped while the worker ran.")

        for run, state in ((lost, "cancelled"), (unfinished, "approved")):
            with self.subTest(state=state):
                self.assertEqual(self.guide("y\n", [run]), 1)
                self.assertEqual(self.status(), state)
                entry = json.loads((self.session / "session.json").read_text())["runs"][-1]
                self.assertIsNone(entry["attempt_state"])
                kept = "The task stays open because a run's records are not closed." in self.stdout
                self.assertEqual(kept, state == "approved")

    def test_worker_chosen_names_are_escaped(self):
        name = "src/\x1b[2Jevil\a.py"
        status = self.guide("y\nn\n", [worker_run(refuse=[("write", "tests/\x1b[31mx.py", "file")],
                                                  write=(name, "x\x1b[0m\n"))])
        self.assertEqual(status, 0, self.stderr)
        self.assertNotIn("\x1b", self.stdout + self.stderr)
        self.assertNotIn("\a", self.stdout + self.stderr)
        self.assertIn("src/\\x1b[2Jevil\\x07.py", self.stdout)
        self.assertIn("The worker wanted to change tests/\\x1b[31mx.py.", self.stdout)

    def test_log_shows_the_latest_session_in_detail(self):
        runs = [worker_run(refuse=[("write", "tests/test_app.py", "file"), ("read", ".env", "file")]),
                worker_run(write=("tests/test_app.py", "APPROVED\n"))]
        self.assertEqual(self.guide("y\ny\ny\nno\n\n", runs), 0, self.stderr)
        result = self.script("log", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        log = result.stdout
        self.assertIn("Change VALUE to 2 in src/app.py.", log)
        self.assertIn("Permissions - the built-in source template, approved", log)
        first = log[log.index("Run 1"):log.index("Run 2")]
        self.assertRegex(first, r"\d\d:\d\d:\d\d  BLOCKED  write tests/test_app.py  \(file: ")
        self.assertIn("change tests/test_app.py - approved", first)
        self.assertIn("read .env - refused  (sensitive)", first)
        second = log[log.index("Run 2"):]
        self.assertIn("changed    tests/test_app.py", second)
        self.assertIn("+APPROVED", second)
        self.assertNotIn("BLOCKED", second)
        self.assertIn("console.log  (everything the run printed)", log)
        latest = self.script("log")
        self.assertEqual(latest.stdout, log)
        other = self.base / "other"
        other.mkdir()
        missing = self.script("log", str(other))
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("No runs found for " + str(other), missing.stderr)

    def test_records_stay_outside_the_project_in_a_stable_place(self):
        project, records = locate(self.home, str(self.project) + "/src/..")
        self.assertEqual(project, self.project)
        self.assertEqual(records, project_directory(self.home, self.project))
        self.assertEqual(records.parent, self.home / "projects")
        self.assertTrue(records.name.startswith("project-"))
        self.assertFalse(records.is_relative_to(self.project))
        with self.assertRaisesRegex(Stop, "is not a directory"):
            locate(self.home, str(self.project / "README.md"))
        with self.assertRaisesRegex(Stop, "inside"):
            locate(self.project / "planner-home", str(self.project))
        with self.assertRaisesRegex(Stop, "inside"):
            locate(self.home, str(self.base))  # The default home under a project folder.
        for inside in (records / "runs", self.home / "opencode-2.2.5", self.home):
            inside.mkdir(parents=True, exist_ok=True)
            with self.subTest(inside=inside), self.assertRaisesRegex(Stop, "inside"):
                locate(self.home, str(inside))
        with self.assertRaisesRegex(Stop, "holds the planner's runtime and records, not a project"):
            locate(self.home, str(self.home / "opencode-2.2.5"))


class ScriptTests(GuidedCase):
    def test_argument_errors_fail_before_anything_is_created(self):
        cases = [((), "Give a project folder and a task"),
                 ((str(self.project),), "Give a project folder and a task"),
                 ((str(self.project), "task", "extra"), "Give a project folder and a task"),
                 ((str(self.project), "task", "--force"), "Unknown option: --force"),
                 ((str(self.base / "missing"), "task"), "is not a directory"),
                 (("project/README.md", "task"), "is not a directory"),
                 ((str(self.project), "  "), "Say what the worker should do"),
                 (("log", "a", "b"), "Usage: scripts/run.sh log [PROJECT]")]
        for args, message in cases:
            with self.subTest(args=args):
                result = self.script(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn("API key", result.stderr)
        inside = self.script(str(self.project), "task", env={**self.environment,
                                                             "PLANNER_HOME": str(self.project / "home")})
        self.assertIn("inside", inside.stderr)
        self.assertFalse(self.home.exists())
        self.assertFalse((self.project / "home").exists())
        bad = self.script(str(self.project), "task", env={**self.environment, "PLANNER_API_KEY_ENV": "key; rm"})
        self.assertIn("PLANNER_API_KEY_ENV must be an upper-case variable name.", bad.stderr)
        model = self.script(str(self.project), "task", env={**self.environment, "PLANNER_MODEL": "no-provider"})
        self.assertIn("Check PLANNER_MODEL and PLANNER_PROVIDER_URL", model.stderr)
        self.assertFalse(self.home.exists())

    def test_help(self):
        result = self.script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("access requests are always asked", result.stdout)

    def test_missing_runtime_points_at_the_installer(self):
        result = self.script("project", "Fix it", "--yes")  # A relative project, from the caller's folder.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The planner runtime is not installed in " + str(self.home), result.stdout)
        self.assertIn("scripts/install-ubuntu.sh", result.stderr)

    def test_the_key_is_asked_hidden_exported_and_never_on_a_command_line(self):
        runtime = self.home / "opencode-2.2.5"
        runtime.mkdir(parents=True)
        for path in (runtime / "opencode", self.home / "fuse-bridge"):
            path.write_text("#!/bin/sh\nexit 1\n")  # Present but unusable: the startup check refuses it.
            path.chmod(0o700)
        calls = self.base / "python-calls.txt"
        wrapper = self.binaries / "python3"
        wrapper.unlink()
        wrapper.write_text("#!/bin/sh\nprintf 'ARGV %s\\n' \"$*\" >> " + shlex.quote(str(calls)) + "\n"
                           "if [ -n \"${GUIDED_TEST_KEY:-}\" ]; then echo 'KEY set' >> " + shlex.quote(str(calls))
                           + "; fi\nexec " + shlex.quote(sys.executable) + " \"$@\"\n")
        wrapper.chmod(0o700)
        key = "guided-secret-key-4711"
        result = self.script(str(self.project), "Fix it", input=key + "\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The planner cannot run safely yet", result.stderr)
        self.assertNotIn(key, result.stdout + result.stderr)
        log = calls.read_text()
        self.assertNotIn(key, log)
        self.assertRegex(log, r"ARGV -m planner\.guided check .*\nARGV -m planner\.guided run .*\nKEY set\n$")
        for path in self.base.rglob("*"):
            if path.is_file():
                self.assertNotIn(key.encode(), path.read_bytes(), path)
        missing = self.script(str(self.project), "Fix it")
        self.assertIn("No API key", missing.stderr)


class InterruptTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Interrupt delivery requires POSIX signals")
    def test_a_run_waits_for_the_planner_after_ctrl_c_and_relays_its_output(self):
        # The interrupt goes to a separate waiter process, never to this test runner.
        planner = ("import signal, sys, time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(3); "
                   "sys.stderr.write('BLOCKED while stopping\\n'); print('{\"stopped\": true}')")
        with tempfile.TemporaryDirectory(prefix="planner-guided-interrupt-") as directory:
            output = Path(directory) / "run.json"
            waiter = subprocess.Popen(
                [sys.executable, "-c", "import sys\nfrom pathlib import Path\n"
                 "from planner.guided import Console, Planner\n"
                 "class Stub(Planner):\n"
                 f"    def command(self, *args): return [sys.executable, '-c', {planner!r}]\n"
                 "print('ready', flush=True)\n"
                 "sys.exit(Stub(Path('unused')).run([], Path(sys.argv[1]), Console(sys.stdin, sys.stdout, sys.stderr)))\n",
                 str(output)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(waiter.stdout.readline().strip(), "ready")
                time.sleep(0.5)  # Let the waiter reach wait(); its planner runs for 3 seconds.
                waiter.send_signal(signal.SIGINT)
                _, stderr = waiter.communicate(timeout=30)
            finally:
                if waiter.poll() is None:
                    waiter.kill()
                    waiter.communicate()
            self.assertEqual(waiter.returncode, 0, stderr)  # The planner finished its own shutdown.
            self.assertEqual(json.loads(output.read_text()), {"stopped": True})
        self.assertIn("Still waiting for the planner to stop the worker and clean up", stderr)
        self.assertIn("BLOCKED while stopping", stderr)


def post(url, messages=()):
    body = json.dumps({"tools": [{}], "messages": list(messages)}).encode()
    with urlopen(Request(url + "/chat/completions", data=body, method="POST",
                         headers={"Content-Type": "application/json"}), timeout=10) as response:
        return response.read().decode()


class ScriptedProviderTests(unittest.TestCase):
    def test_a_none_step_ends_one_run_and_the_next_steps_serve_the_next(self):
        with ScriptedProvider([("read", {"path": "a"}), None, ("write", {"path": "b"})], final="END") as provider:
            replies = [post(provider.url) for _ in range(4)]
        self.assertIn('"name": "read"', replies[0])
        self.assertIn('"content": "END"', replies[1])
        self.assertIn('"name": "write"', replies[2])
        self.assertIn('"content": "END"', replies[3])

    def test_isolated_runs_skip_the_steps_a_stopped_run_left(self):
        steps = [("read", {"path": "a"}), ("read", {"path": "b"}), None, ("write", {"path": "c"}), None,
                 ("write", {"path": "d"}), None]
        result = [{"role": "tool", "tool_call_id": "call_0", "content": "refused"}]
        with ScriptedProvider(steps, final="END", isolate_runs=True) as provider:
            replies = [post(provider.url),  # Run 1 starts; it is stopped before asking again.
                       post(provider.url),  # Run 2 starts after the rest of run 1's steps.
                       post(provider.url, result),  # Run 2 ends at its own None.
                       post(provider.url)]  # Run 3 starts right after it.
        self.assertIn('"path\\": \\"a\\"', replies[0])
        self.assertIn('"path\\": \\"c\\"', replies[1])
        self.assertIn('"content": "END"', replies[2])
        self.assertIn('"path\\": \\"d\\"', replies[3])


@unittest.skipUnless(GATE, "Actual Linux connected runtime required for the guided script")
class GuidedScriptRuntimeTests(GuidedCase):
    """scripts/run.sh on the real confined runtime with a scripted model."""

    def setUp(self):
        super().setUp()
        runtime = self.home / "opencode-2.2.5"
        runtime.mkdir(parents=True)
        (runtime / "opencode").symlink_to(Path(os.environ["PLANNER_OPENCODE_BINARY"]))
        (self.home / "fuse-bridge").symlink_to(Path(os.environ["PLANNER_FUSE_BRIDGE"]))

    def guided(self, steps, answers, *options, isolate_runs=False):
        with ScriptedProvider(steps, isolate_runs=isolate_runs) as provider:
            environment = {**self.environment, "PLANNER_PROVIDER_URL": provider.url,
                           "PLANNER_MODEL": "fixture/model", "GUIDED_TEST_KEY": "dummy-fixture-credential"}
            # Confined startup has exceeded 90 seconds on a loaded guest; each run may take minutes.
            result = self.script(str(self.project), "Change VALUE to 2 in src/app.py.", *options, input=answers,
                                 env=environment, timeout=1800)
        [session] = project_directory(self.home, self.project).glob("sessions/*")
        console = ANSI.sub("", (session / "console.log").read_text())
        return result, provider, session, console

    def test_ctrl_c_stops_the_run_records_it_and_applies_nothing(self):
        script = ("import time; from pathlib import Path\n"
                  "Path('/workspace/src/app.py').write_text('VALUE = 2\\n')\n"
                  "Path('/workspace/src/started.txt').write_text('1')\n"
                  "time.sleep(300)\n")
        with ScriptedProvider([shell(script), None]) as provider:
            environment = {**self.environment, "PLANNER_PROVIDER_URL": provider.url,
                           "PLANNER_MODEL": "fixture/model", "GUIDED_TEST_KEY": "dummy-fixture-credential"}
            started = lambda _: bool(list(self.home.glob("projects/*/runs/*/fs/backing/src/started.txt")))
            code, output = press_ctrl_c(["bash", str(ROOT / "scripts/run.sh"), str(self.project),
                                         "Change VALUE to 2 in src/app.py.", "--yes"], self.base, environment, started)
        self.assertEqual(code, 130, output[-5000:])
        self.assertIn("stopped    you pressed Ctrl-C, so nothing from this run was applied", output)
        self.assertNotIn("Traceback", output)
        self.assertNotIn("Run again", output)
        self.unchanged("src/app.py")
        [session] = project_directory(self.home, self.project).glob("sessions/*")
        run = json.loads((session / "run-1/run.json").read_text())
        self.assertEqual((run["outcome"], run["attempt_state"], run["application"]["state"]),
                         ("stopped", "failed", "not_applied"))
        self.assertTrue(run["cleanup_verified"])
        self.assertIn("you pressed Ctrl-C", (session / "console.log").read_text())

    def test_happy_path_applies_an_allowed_fix(self):
        steps = [("write", {"path": "/workspace/src/app.py", "content": "VALUE = 2\n"}), None]
        result, provider, session, console = self.guided(steps, "y\n")
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr)
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")
        self.unchanged("tests/test_app.py", "README.md", ".env")
        run = json.loads((session / "run-1/run.json").read_text())
        self.assertEqual((run["outcome"], run["application"]["state"]), ("completed", "applied"))
        self.assertIn("  changed    src/app.py", console)
        self.assertIn("+VALUE = 2", console)
        for name in ("audit.json", "report.html"):
            self.assertTrue((session / name).is_file(), name)
        self.assertNotIn("DUMMY_SECRET", provider.tool_results())
        log = self.script("log", str(self.project))
        self.assertEqual(log.returncode, 0, log.stderr)
        self.assertIn("allowed  write src/app.py", log.stdout)

    def test_refused_write_becomes_a_request_and_the_approved_rerun_applies_it(self):
        attempt = ("try: open('/workspace/tests/test_app.py', 'a')\n"
                   "except PermissionError: print('FIRST_DENIED')\n")
        approved = "open('/workspace/tests/test_app.py', 'w').write('APPROVED\\n')\n"
        steps = [shell(attempt), None, shell(approved), None]
        result, provider, session, console = self.guided(steps, "y\ny\n\n")
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr)
        self.assertIn("FIRST_DENIED", provider.tool_results())
        live = console[console.index("\nRun 1\n"):console.index("\nResult\n")]
        self.assertIn("[BLOCKED] The worker tried to write tests/test_app.py - refused", live)  # NO_COLOR is set.
        self.assertIn("The worker wanted to change tests/test_app.py.", console)
        self.assertIn("The next run starts a fresh worker", console)
        second = json.loads((session / "run-2/run.json").read_text())
        self.assertEqual(second["application"]["state"], "applied", second)
        self.assertEqual((self.project / "tests/test_app.py").read_text(), "APPROVED\n")
        self.unchanged("src/app.py", ".env")

    def test_suspended_worker_is_resumed_through_the_real_resume_and_the_rerun_applies_the_fix(self):
        # Run 1 repeats one refused read until the supervisor suspends the worker; it may be
        # stopped before it asks for its last step, so each run gets only its own steps.
        refused = ("read", {"path": "/workspace/.env"})
        steps = [refused, refused, refused, None,
                 ("write", {"path": "/workspace/src/app.py", "content": "VALUE = 2\n"}), None]
        # Approve the permissions, then resume. A suspended run asks about no access requests,
        # and run 2 has none, so nothing else is asked.
        result, provider, session, console = self.guided(steps, "y\ny\n", isolate_runs=True)
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr)
        self.assertIn("The worker was suspended because it kept repeating the same blocked operation.", console)
        self.assertIn("Resume the worker and run again? [y/N] y\nThe next run starts a fresh worker", console)
        self.assertNotIn("Access requests", console)
        self.assertNotIn("Could not resume", console)
        first = json.loads((session / "run-1/run.json").read_text())
        self.assertEqual(first["attempt_state"], "cancelled", first)  # Stopped by the suspension.
        second = json.loads((session / "run-2/run.json").read_text())
        self.assertEqual((second["outcome"], second["attempt_state"], second["application"]["state"]),
                         ("completed", "finished", "applied"), second)
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")
        self.unchanged(".env", "tests/test_app.py", "README.md")
        self.assertNotIn("DUMMY_SECRET", provider.tool_results())
        data = json.loads((session / "session.json").read_text())
        self.assertTrue(data["runs"][0]["resumed"])
        supervisor = Supervisor(Path(data["state"]))
        try:
            history = supervisor.history(data["task"])
            attempts = supervisor.attempts(data["task"])
        finally:
            supervisor.close()
        [resume] = [row for row in history if row["operation"] == "resume"]  # Recorded by the real CLI.
        self.assertEqual((resume["worker"], resume["tool"], resume["allowed"]), ("worker", "operator", 1))
        before = [row for row in history if row["id"] < resume["id"] and row["tool"] == "file"]
        self.assertEqual([(row["operation"], row["allowed"]) for row in before], [("read", 0)] * 3, before)
        self.assertTrue(any(row["operation"] == "write" and row["allowed"] and row["tool"] == "file"
                            for row in history if row["id"] > resume["id"]))
        self.assertEqual([item["id"] for item in attempts], [first["attempt"], second["attempt"]])
        self.assertTrue(any(event["reason"] == "repeated_denials" for event in attempts[0]["events"]))
        log = self.script("log", str(self.project))
        self.assertEqual(log.returncode, 0, log.stderr)
        self.assertRegex(log.stdout, r"\d\d:\d\d:\d\d  you resumed the worker\n")
        self.assertNotIn("allowed  resume", log.stdout)

    def test_review_mode_holds_the_fix_until_applied(self):
        steps = [("write", {"path": "/workspace/src/app.py", "content": "VALUE = 2\n"}), None]
        result, _, session, console = self.guided(steps, "y\ny\n", "--review")
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr)
        run = json.loads((session / "run-1/run.json").read_text())
        self.assertEqual(run["application"]["state"], "retained")
        self.assertIn("  held       src/app.py  (not in your project yet)", console)
        self.assertIn("+VALUE = 2", console)
        self.assertIn("Applied to your project.", console)
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")

    def test_declined_approval_changes_nothing(self):
        result, provider, session, console = self.guided([("write", {"path": "src/app.py", "content": "x"})], "n\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(provider.bodies, [])
        self.assertIn("Not approved, so nothing ran.", console)
        self.assertFalse((session / "run-1").exists())
        self.unchanged(*ORIGINAL)


if __name__ == "__main__":
    unittest.main()
