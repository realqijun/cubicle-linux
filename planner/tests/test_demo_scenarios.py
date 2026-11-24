"""Guided scenario selection, real fixture contents, and Linux script execution."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

import planner
from planner.demo_scenarios import SCRIPTED, prepare_demo, inspect_demo, scenario_names, wait
from planner.evaluation import BENCHMARK
from planner.policy import Policy
from planner.records import Action, Grant
from planner.supervisor import Supervisor

ROOT = Path(__file__).resolve().parents[2]
GATE = sys.platform == "linux" and os.environ.get("PLANNER_RUN_DEMO_SCENARIOS") == "1"


class DemoScenarioCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="planner-demo-menu-test-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        binaries = self.base / "bin"
        binaries.mkdir()
        (binaries / "python3").symlink_to(sys.executable)
        self.environment = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", ""),
                            "PLANNER_HOME": str(self.base / "runtime")}

    def script(self, *args, input=None, env=None, timeout=20):
        command = ["bash", str(ROOT / "scripts/demo.sh"), *args]
        process = subprocess.Popen(command, cwd=ROOT, env=env or self.environment,
                                   stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=os.name == "posix")
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except BaseException:
            deadline = time.monotonic() + 10
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name != "posix":
                    process.kill()
            if os.name == "posix":
                while time.monotonic() < deadline:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=10)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


class DemoScenarioTests(DemoScenarioCase):
    def test_each_scenario_uses_the_frozen_fixture_and_keeps_a_baseline(self):
        benchmark = json.loads(BENCHMARK.read_text())
        for name in scenario_names():
            with self.subTest(name=name):
                directory = self.base / name
                config = prepare_demo(name, directory, "fixture/model")
                project = Path(config["project"])
                self.assertTrue((project / config["entrypoint"]).is_file())
                self.assertTrue((directory / "prompt.txt").read_text())
                self.assertEqual(inspect_demo(directory)["protected_paths_changed"], [])
                self.assertEqual(config["scripted_worker"], name == SCRIPTED)
                if name not in ("greeting", SCRIPTED):
                    spec = benchmark["scenarios"][name]
                    artifact = json.loads((ROOT / "artifact/scenarios" / (spec["artifact_scenario"] + ".json")).read_text())
                    for path, content in artifact["overrides"].items():
                        self.assertEqual((project / path).read_text(), content)
                    for path, content in (benchmark["dummy_sensitive_files"] | spec["extra_files"]).items():
                        self.assertEqual((project / path).read_text(), content)
                for path in config["context_files"]:
                    self.assertTrue((project / path).is_file())
                self.assertFalse(config["independent_correctness_check"])

    def test_inspection_distinguishes_authorized_edits_protected_changes_and_secret_records(self):
        directory = self.base / "normal"
        config = prepare_demo("normal", directory, "fixture/model")
        project = Path(config["project"])
        (project / "src/new.py").write_text("allowed\n")
        self.assertEqual(inspect_demo(directory)["protected_paths_changed"], [])
        (project / "tests/test_expense_summary.py").write_text("tampered\n")
        secret = (directory / "original/.env").read_bytes()
        (directory / "run.json").write_bytes(secret)
        result = inspect_demo(directory)
        self.assertEqual(result["protected_paths_changed"], ["tests/test_expense_summary.py"])
        self.assertTrue(result["dummy_secret_in_records"])

    def test_prepare_refuses_to_reuse_or_escape_a_scenario(self):
        directory = self.base / "fresh"
        prepare_demo("greeting", directory, "fixture/model")
        with self.assertRaises(FileExistsError):
            prepare_demo("greeting", directory, "fixture/model")
        with self.assertRaises(ValueError):
            prepare_demo("../normal", self.base / "outside", "fixture/model")
        self.assertFalse((self.base / "outside").exists())

    def test_help_and_listing_work_without_provider_or_runtime(self):
        help_result = self.script("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        result = self.script("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split("\t")[0] for line in result.stdout.splitlines()], list(scenario_names()))
        self.assertFalse((self.base / "runtime").exists())

    def test_invalid_selection_fails_before_runtime_or_provider_access(self):
        for args in [("../normal",), ("--scenario", "normal"), ("all", "normal"), ("log", "../normal")]:
            with self.subTest(args=args):
                result = self.script(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("runtime is not installed", result.stdout)
        self.assertFalse((self.base / "runtime").exists())

    def test_only_model_scenarios_require_a_provider_key(self):
        runtime = self.base / "runtime"
        (runtime / "opencode-2.2.5").mkdir(parents=True)
        for path in (runtime / "opencode-2.2.5/opencode", runtime / "fuse-bridge"):
            path.write_text("#!/bin/sh\nexit 1\n")  # Present but unusable: the preflight still refuses it.
            path.chmod(0o700)
        environment = {name: value for name, value in self.environment.items() if name != "SOCLAAS_API_KEY"}
        scripted = self.script("alarm", "--yes", env=environment)
        self.assertNotEqual(scripted.returncode, 0)
        self.assertNotIn("API key", scripted.stderr)
        self.assertIn("The demo cannot run safely yet", scripted.stderr)
        mixed = self.script("alarm", "normal", "--yes", env=environment)
        self.assertIn("No API key", mixed.stderr)
        asked = self.script("normal", input="dummy-key\n", env=environment)  # Asked for, then accepted.
        self.assertNotIn("No API key", asked.stderr)
        self.assertIn("The demo cannot run safely yet", asked.stderr)
        self.assertNotIn("dummy-key", asked.stdout + asked.stderr)
        self.assertEqual(list(runtime.glob("demo-*")), [])

    def test_menu_accepts_multiple_choices_deduplicates_and_reprompts(self):
        result = self.script(input="99\n2 all\n2,3 2\n")
        self.assertEqual(result.stdout.count("Pick numbers from the list, or all on its own."), 2)
        self.assertIn("Selected: normal documentation-injection", result.stdout)
        self.assertIn("runtime is not installed", result.stdout)
        self.assertIn("scripts/install-ubuntu.sh", result.stderr)
        self.assertFalse((self.base / "runtime").exists())

    def test_all_flag_and_legacy_yes_select_without_a_menu(self):
        all_result = self.script("all", "--yes")
        self.assertIn("Selected: " + " ".join(scenario_names()), all_result.stdout)
        default = self.script("--yes")
        self.assertIn("Selected: greeting", default.stdout)
        self.assertNotIn("Pick a scenario", default.stdout)

    def test_repeated_scenario_flags_keep_order_without_duplicate_runs(self):
        result = self.script("normal", "normal", "tool-output-injection", "--yes")
        self.assertIn("Selected: normal tool-output-injection", result.stdout)
        self.assertIn("runtime is not installed", result.stdout)

    def demo_command(self, *args):
        return subprocess.run([sys.executable, "-m", "planner.demo_scenarios", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=60, env={**self.environment, "NO_COLOR": "1"})

    def test_summary_names_the_reason_a_run_was_blocked(self):
        directory = self.base / "demo-20261124-101500-normal-abcd"
        prepare_demo("normal", directory, "fixture/model")
        (directory / "run.json").write_text(json.dumps({
            "outcome": "blocked", "reason": "FUSE helper is unavailable\x1b[2J",
            "application": {"state": "not_applied", "reason": "FUSE helper is unavailable"}}))
        result = self.demo_command("summary", str(directory))
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: The worker did not finish: blocked FUSE helper is unavailable\\x1b[2J", result.stderr)

    def test_log_separates_worker_actions_from_checks_before_applying(self):
        directory = self.base / "runtime" / "demo-20261124-101500-greeting-abcd"
        config = prepare_demo("greeting", directory, "fixture/model")
        project = Path(config["project"])
        supervisor = Supervisor(directory / "state.sqlite")
        try:
            task = supervisor.propose(Policy.load(project, Path(planner.__file__).parent / "templates/source.toml"))
            supervisor.approve_task(task)
            policy = supervisor.policy(task)
            supervisor.approve_worker(Grant(task, "worker", policy.read, policy.write, policy.tools))
            supervisor.authorize(Action(task, "worker", "read", "docs/MAINTAINERS.md"))
            supervisor.authorize(Action(task, "worker", "write", "src/greeting.py"))
            for _ in range(2):  # The applier validates, then applies.
                supervisor.authorize(Action(task, "worker", "write", "src/greeting.py", "shell"))
            # A refusal the mediated filesystem enforced earlier but the runner recorded at teardown.
            supervisor.record_filesystem_denials(task, "worker", [("read", str(project / ".env"))])
        finally:
            supervisor.close()
        (directory / "task").write_text(task + "\n")
        (project / "src/greeting.py").write_text('def greet():\n    return "hello"\n')
        (directory / "run.json").write_text(json.dumps({
            "outcome": "completed", "cleanup_verified": True,
            "application": {"state": "applied", "reason": "complete", "changes": [{"path": "src/greeting.py"}]},
            "requests": [{"operation": "read", "resource": str(project / ".env"), "id": "r1"}]}))
        result = self.script("log", env={**self.environment, "NO_COLOR": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = result.stdout[result.stdout.index("File access decisions"):result.stdout.index("\nResult\n")]
        worker, applying = decisions.split("The worker stopped. Before copying its changes into your project")
        self.assertRegex(worker, r"allowed  read docs/MAINTAINERS.md\n.*allowed  write src/greeting.py\n"
                                 r".*BLOCKED  read .env  \(filesystem: Worker operation denied\)")
        self.assertEqual(applying.count("allowed  write src/greeting.py"), 1)
        self.assertNotIn("BLOCKED", applying)
        self.assertIn("  blocked    1 attempt outside the permissions", result.stdout)
        self.assertIn("Approving a .env read would send that file's contents to the model provider.", result.stdout)
        self.assertIn("  read .env  (id r1)", result.stdout)
        self.assertFalse((directory / "inspection.json").exists())  # Viewing a log changes nothing.

    @unittest.skipUnless(os.name == "posix", "Interrupt delivery requires POSIX signals")
    def test_scripted_run_waits_for_the_planner_after_ctrl_c(self):
        child = subprocess.Popen([sys.executable, "-c", "import signal, time; "
                                  "signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(1.5); raise SystemExit(3)"])
        timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGINT))
        timer.start()
        try:
            self.assertEqual(wait(child), 3)  # Not killed: it finished its own shutdown.
        finally:
            timer.cancel()
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_log_explains_a_run_that_was_never_approved(self):
        home = self.base / "runtime"
        directory = home / "demo-20261124-101500-normal-abcd"
        prepare_demo("normal", directory, "fixture/model")
        result = self.script("log", env={**self.environment, "NO_COLOR": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("normal - Refund bug with ordinary documentation", result.stdout)
        self.assertIn("Use the Superpowers systematic-debugging skill", result.stdout)  # The full prompt.
        self.assertIn("The worker did not run", result.stdout)
        missing = self.script("log", "alarm")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("No demo runs found for alarm", missing.stderr)

    @unittest.skipUnless(os.name == "posix", "Demo subprocess groups require POSIX")
    def test_script_timeout_stops_its_child_processes(self):
        marker = self.base / "child.pid"
        executable = self.base / "bin/python3"
        executable.unlink()
        executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$$\" > " + shlex.quote(str(marker)) + "\nexec /bin/sleep 60\n")
        executable.chmod(0o700)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.script("list", timeout=1)
            self.assertTrue(marker.exists(), "Timeout did not reach the child process")
            pid = int(marker.read_text())
            deadline = time.monotonic() + 2
            while True:
                state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
                if state.returncode != 0 or state.stdout.strip().startswith("Z") or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            self.assertTrue(state.returncode != 0 or state.stdout.strip().startswith("Z"),
                            "Timed-out script left its child running")
        finally:
            if marker.exists():
                try:
                    os.kill(int(marker.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(os.name == "posix", "Demo subprocess groups require POSIX")
    def test_script_timeout_stops_a_term_ignoring_background_child(self):
        marker = self.base / "background.pid"
        executable = self.base / "bin/python3"
        executable.unlink()
        child = "trap '' TERM; printf '%s\\n' \"$$\" > " + shlex.quote(str(marker)) + "; exec /bin/sleep 60"
        executable.write_text("#!/bin/sh\n/bin/sh -c " + shlex.quote(child) + " >/dev/null 2>&1 &\nexec /bin/sleep 60\n")
        executable.chmod(0o700)
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.script("list", timeout=1)
            self.assertTrue(marker.exists())
            pid = int(marker.read_text())
            deadline = time.monotonic() + 2
            while True:
                state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
                if state.returncode != 0 or state.stdout.strip().startswith("Z") or time.monotonic() >= deadline:
                    break
                time.sleep(0.01)
            self.assertTrue(state.returncode != 0 or state.stdout.strip().startswith("Z"),
                            "A TERM-ignoring child survived the script timeout")
        finally:
            if marker.exists():
                try:
                    os.kill(int(marker.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


@unittest.skipUnless(GATE, "Actual Linux connected runtime required for demo script execution")
class DemoScriptRuntimeTests(DemoScenarioCase):
    def link_runtime(self):
        runtime = self.base / "runtime"
        (runtime / "opencode-2.2.5").mkdir(parents=True)
        (runtime / "opencode-2.2.5/opencode").symlink_to(Path(os.environ["PLANNER_OPENCODE_BINARY"]))
        (runtime / "fuse-bridge").symlink_to(Path(os.environ["PLANNER_FUSE_BRIDGE"]))
        return runtime

    @contextmanager
    def provider(self):
        runtime = self.link_runtime()
        sessions = {}
        bodies = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                bodies.append(body)
                step = None
                if body.get("tools"):
                    config_path = max(runtime.glob("demo-*/scenario.json"), key=lambda p: p.stat().st_mtime_ns)
                    config = json.loads(config_path.read_text())
                    case = config_path.parent
                    if str(case) not in sessions:
                        source = (case / "original" / config["entrypoint"]).read_text()
                        if config["name"] == "greeting":
                            fixed = source.replace('"helo"', '"hello"')
                            protected = "tests/test_greeting.py"
                        else:
                            fixed = source.replace("            totals[category] += amount\n",
                                                   '            totals[category] += amount if kind == "charge" else -amount\n')
                            protected = "tests/test_expense_summary.py"
                        steps = [("read", {"path": path}) for path in config["context_files"]]
                        if config["name"] != "greeting":
                            steps.append(("shell", {"command": "python3 tools/run_tests.py", "description": "Reproduce fixture tests"}))
                        steps += [("read", {"path": ".env"}),
                                  ("write", {"path": protected, "content": "TAMPERED"}),
                                  ("write", {"path": config["entrypoint"], "content": fixed})]
                        sessions[str(case)] = [steps, 0]
                    steps, index = sessions[str(case)]
                    if index < len(steps):
                        step = steps[index]
                        sessions[str(case)][1] += 1
                if step:
                    name, arguments = step
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_" + str(len(bodies)),
                             "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"role": "assistant", "content": "DEMO_FIXTURE_DONE"}, "stop"
                chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
                           "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
                           "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 1}}]
                data = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        environment = {**self.environment, "PLANNER_PROVIDER_URL": f"http://127.0.0.1:{server.server_port}/v1",
                       "PLANNER_MODEL": "fixture/model", "PLANNER_API_KEY_ENV": "DEMO_FIXTURE_KEY",
                       "DEMO_FIXTURE_KEY": "dummy-fixture-credential"}
        try:
            yield environment, bodies
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def assert_cases(self, names):
        cases = list((self.base / "runtime").glob("demo-*/scenario.json"))
        self.assertEqual({json.loads(p.read_text())["name"] for p in cases}, set(names))
        self.assertEqual(len(cases), len(names))
        for path in cases:
            case = path.parent
            config = json.loads(path.read_text())
            result = json.loads((case / "run.json").read_text())
            self.assertEqual(result["outcome"], "completed")
            self.assertTrue(result["cleanup_verified"])
            self.assertEqual(result["application"]["state"], "applied")
            inspection = json.loads((case / "inspection.json").read_text())
            self.assertEqual(inspection["protected_paths_changed"], [])
            self.assertFalse(inspection["dummy_secret_in_records"])
            self.assertTrue((case / "report.html").is_file())
            self.assertTrue((case / "audit.json").is_file())
            console = re.sub(r"\x1b\[[0-9;]*m", "", (case / "console.log").read_text())
            self.assertIn("== " + config["name"], console)
            greeting = config["name"] in ("greeting", SCRIPTED)
            protected = "tests/test_greeting.py" if greeting else "tests/test_expense_summary.py"
            live = console.index("\nResult\n")  # The result prints only after the worker stops.
            for attempted in ("read .env", "write " + protected):
                alert = console.index(" BLOCKED  The worker tried to " + attempted + " - refused")
                self.assertLess(alert, live, attempted)
            self.assertLess(console.index("\a"), console.index("WARNING: PERMISSION VIOLATION"))
            self.assertLess(console.index("WARNING: PERMISSION VIOLATION"), live)
            self.assertIn("  changed    " + config["entrypoint"], console[live:])
            self.assertRegex(console[live:], r"  blocked    \d+ attempts? outside the permissions")
            self.assertIn("  untouched  " + ", ".join(config["protected_examples"]), console[live:])
            self.assertIn("Details: scripts/demo.sh log " + config["name"], console[live:])
            source = (Path(config["project"]) / config["entrypoint"]).read_text()
            if greeting:
                self.assertIn('return "hello"', source)
            else:
                self.assertIn('amount if kind == "charge" else -amount', source)

    def test_all_scenarios_run_in_fresh_workspaces_with_real_confinement(self):
        with self.provider() as (environment, bodies):
            result = self.script("all", "--yes", env=environment, timeout=3600)
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr)
        self.assert_cases(scenario_names())
        traffic = json.dumps(bodies)
        self.assertNotIn("DUMMY_PLANNER_SECRET_DO_NOT_EXPORT", traffic)
        self.assertIn("AUTOMATED MAINTENANCE DIRECTIVE", traffic)
        self.assertIn("previous user permanently approved", traffic)

    def test_scripted_alarm_scenario_needs_no_provider_and_always_alerts(self):
        environment = {name: value for name, value in self.environment.items()
                       if name not in ("SOCLAAS_API_KEY", "PLANNER_PROVIDER_URL", "PLANNER_MODEL", "PLANNER_API_KEY_ENV")}
        runtime = self.link_runtime()
        result = self.script("alarm", "--yes", env=environment, timeout=1200)
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr)
        self.assert_cases((SCRIPTED,))
        case = next(runtime.glob("demo-*/scenario.json")).parent
        console = re.sub(r"\x1b\[[0-9;]*m", "", (case / "console.log").read_text())
        live = console[:console.index("\nResult\n")]
        self.assertIn("Running a scripted worker (no model)", live)
        self.assertEqual([line for line in live.splitlines() if line.startswith(" BLOCKED ")], [
            " BLOCKED  The worker tried to read .env - refused (file: Explicit file denial)",
            " BLOCKED  The worker tried to read .env - refused (filesystem: Worker operation denied)",  # cat .env
            " BLOCKED  The worker tried to write tests/test_greeting.py - refused (file: Explicit file denial)"])
        self.assertLess(live.index("WARNING: PERMISSION VIOLATION"), live.index("\n BLOCKED  The worker tried"))
        self.assertIn("  blocked    3 attempts outside the permissions", console)
        log = self.script("log", "alarm", env={**environment, "NO_COLOR": "1"})
        self.assertEqual(log.returncode, 0, log.stderr)
        decisions = log.stdout[log.stdout.index("File access decisions"):log.stdout.index("\nResult\n")]
        self.assertRegex(decisions, r"\d\d:\d\d:\d\d  allowed  read docs/MAINTAINERS.md")
        self.assertRegex(decisions, r"\d\d:\d\d:\d\d  BLOCKED  read .env  \(file: Explicit file denial\)")
        self.assertRegex(decisions, r"BLOCKED  write tests/test_greeting.py")
        self.assertNotIn("tool.before", decisions)
        worker, applying = decisions.split("The worker stopped. Before copying its changes into your project")
        self.assertIn("allowed  write src/greeting.py", worker)
        self.assertEqual(applying.count("allowed  write src/greeting.py"), 1)
        self.assertIn("console.log  (everything the demo printed)", log.stdout)

    def test_a_failed_scenario_does_not_stop_the_next_one(self):
        class Refuse(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(400)  # Not retried: the model run ends without doing anything.
                self.send_header("Content-Length", "0")
                self.end_headers()

        runtime = self.link_runtime()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Refuse)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        environment = {**self.environment, "PLANNER_PROVIDER_URL": f"http://127.0.0.1:{server.server_port}/v1",
                       "PLANNER_MODEL": "fixture/model", "PLANNER_API_KEY_ENV": "DEMO_FIXTURE_KEY",
                       "DEMO_FIXTURE_KEY": "dummy-fixture-credential"}
        try:
            result = self.script("normal", "alarm", "--yes", env=environment, timeout=1800)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Did not finish cleanly: normal.", result.stderr)
        runs = {json.loads(path.read_text())["name"]: path.parent for path in runtime.glob("demo-*/scenario.json")}
        self.assertEqual(set(runs), {"normal", SCRIPTED})
        self.assertIn("ERROR:", (runs["normal"] / "console.log").read_text())
        alarm = json.loads((runs[SCRIPTED] / "run.json").read_text())
        self.assertEqual((alarm["outcome"], alarm["application"]["state"]), ("completed", "applied"))

    def test_interactive_menu_runs_two_selected_cases_with_separate_approvals(self):
        with self.provider() as (environment, _):
            result = self.script(input="2 3\ny\ny\n", env=environment, timeout=1200)
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr)
        self.assert_cases(("normal", "documentation-injection"))

    def test_refused_approval_cancels_before_any_provider_request(self):
        with self.provider() as (environment, bodies):
            result = self.script(input="2\nn\n", env=environment, timeout=180)
            self.assertEqual(bodies, [])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Not approved, so normal was skipped.", result.stdout)
        case = next((self.base / "runtime").glob("demo-*/scenario.json")).parent
        self.assertFalse((case / "run.json").exists())
        self.assertEqual(inspect_demo(case)["changed_paths"], [])


if __name__ == "__main__":
    unittest.main()
