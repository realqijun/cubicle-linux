"""Prompt-to-application through the public CLI and the actual confined runtime."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from planner.tests.runtime_fixtures import RUNTIME_GATE, RUNTIME_REASON, ScriptedProvider, runtime_paths, shell

POLICY = """
[files]
read = ["**"]
write = ["src/**"]
deny_read = [".env*", "**/.env*"]
deny_write = ["tests/**"]

[tools]
allow = ["file", "search", "shell"]
"""
ORIGINAL = {"README.md": "Example service.\n", "src/app.py": "VALUE = 1\n",
            "tests/test_app.py": "from src.app import VALUE\nassert VALUE == 2\n", ".env": "TOKEN=DUMMY_SECRET\n"}


@unittest.skipUnless(RUNTIME_GATE, RUNTIME_REASON)
class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="planner-launcher-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / "project"
        for name, text in ORIGINAL.items():
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text(text)
        (self.base / "policy.toml").write_text(POLICY)
        self.state = self.base / "operator" / "state.sqlite"
        self.runs = self.base / "operator" / "runs"

    def cli(self, *args: str, check: bool = True, seconds: float = 3600) -> dict:
        result = subprocess.run([sys.executable, "-m", "planner", "--json", "--state", str(self.state),
                                 "--task-seconds", str(seconds), *args],
                                capture_output=True, text=True, timeout=600, cwd=Path(__file__).parents[2])
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        return {"returncode": result.returncode, "stderr": result.stderr}

    def approved_task(self, seconds: float = 3600) -> str:
        task = self.cli("propose", str(self.project), "--policy", str(self.base / "policy.toml"), seconds=seconds)
        self.cli("approve-task", task["id"], "--revision", str(task["revision"]), "--assign", "worker")
        return task["id"]

    def run_args(self, task: str, provider: ScriptedProvider, *extra: str) -> list[str]:
        paths = runtime_paths()
        return ["run", task, "worker", "--prompt", "Change VALUE to 2 in src/app.py.",
                "--model", "fixture/model", "--provider-url", provider.url,
                "--opencode", str(paths["opencode"]), "--fuse-helper", str(paths["fuse_helper"]),
                "--runs", str(self.runs), "--timeout", "30", *extra]

    def unchanged(self, *names: str) -> None:
        for name in names:
            self.assertEqual((self.project / name).read_text(), ORIGINAL[name], name)

    def test_prompt_to_applied_edit_with_child_denials_recorded(self) -> None:
        task = self.approved_task()
        checks = ("from pathlib import Path\n"
                  "for name, mode in (('/workspace/.env', 'r'), ('/workspace/tests/test_app.py', 'a')):\n"
                  "    try: open(name, mode).close()\n"
                  "    except PermissionError: continue\n"
                  "    raise SystemExit('ALLOWED ' + name)\n"
                  "assert Path('/workspace/README.md').read_text()\n"
                  "print('CHILD_CHECKS_OK')\n")
        steps = [("write", {"path": "/workspace/src/app.py", "content": "VALUE = 2\n"}), shell(checks)]
        with ScriptedProvider(steps) as provider:
            result = self.cli(*self.run_args(task, provider))
        self.assertEqual((result["outcome"], result["attempt_state"]), ("completed", "finished"))
        self.assertTrue(result["cleanup_verified"])
        self.assertIn("CHILD_CHECKS_OK", provider.tool_results())
        application = result["application"]
        self.assertEqual((application["state"], application["changes"]),
                         ("applied", [{"path": "src/app.py", "operation": "modify"}]))
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")
        self.unchanged("README.md", "tests/test_app.py", ".env")
        denied = {(d["operation"], d["resource"]) for d in self.cli("history", task)
                  if d["tool"] == "filesystem" and not d["allowed"]}
        self.assertEqual(denied, {("read", ".env"), ("write", "tests/test_app.py")})
        self.assertNotIn("DUMMY_SECRET", provider.tool_results())

    def test_review_mode_retains_until_explicit_apply_and_never_replays(self) -> None:
        task = self.approved_task()
        steps = [("write", {"path": "/workspace/src/app.py", "content": "VALUE = 2\n"})]
        with ScriptedProvider(steps) as provider:
            result = self.cli(*self.run_args(task, provider, "--mode", "review"))
        self.assertEqual(result["application"]["state"], "retained")
        self.unchanged("src/app.py")
        applied = self.cli("apply", result["attempt"])
        self.assertEqual(applied["state"], "applied")
        self.assertEqual((self.project / "src/app.py").read_text(), "VALUE = 2\n")
        (self.project / "src/app.py").write_text("USER_LATER\n")
        replay = self.cli("apply", result["attempt"], check=False)
        self.assertEqual(replay["returncode"], 2)
        self.assertEqual((self.project / "src/app.py").read_text(), "USER_LATER\n")

    def test_concurrent_user_edit_is_preserved_as_conflict(self) -> None:
        task = self.approved_task()
        def user_edit(index: int, body: dict) -> None:
            if any(message.get("role") == "tool" for message in body.get("messages", [])):
                (self.project / "src/app.py").write_text("USER_EDIT\n")
        steps = [("write", {"path": "/workspace/src/app.py", "content": "VALUE = 2\n"})]
        with ScriptedProvider(steps, on_request=user_edit) as provider:
            result = self.cli(*self.run_args(task, provider))
        self.assertEqual(result["attempt_state"], "finished")
        self.assertEqual(result["application"]["state"], "conflicted")
        self.assertEqual((self.project / "src/app.py").read_text(), "USER_EDIT\n")

    def stop_during_run(self, stop: list[str], *, seconds: float = 3600) -> tuple[dict, str]:
        task = self.approved_task(seconds)
        marker = f"planner-stop-{os.getpid()}-{time.monotonic_ns()}"
        script = ("import time; from pathlib import Path\n"
                  "Path('/workspace/src/started.txt').write_text('1')\n"
                  f"time.sleep(120)  # {marker}\n"
                  "Path('/workspace/src/late.txt').write_text('1')\n")
        def stopper() -> None:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and not list(self.runs.glob("*/fs/backing/src/started.txt")):
                time.sleep(0.2)
            if stop:
                self.cli(*[task if part == "TASK" else part for part in stop])
        thread = threading.Thread(target=stopper)
        with ScriptedProvider([shell(script)]) as provider:
            thread.start()
            started = time.monotonic()
            result = self.cli(*self.run_args(task, provider))
            thread.join()
        self.assertLess(time.monotonic() - started, 100, "Worker was not stopped promptly")
        survivors = subprocess.run(["pgrep", "-f", marker], capture_output=True)
        self.assertEqual(survivors.returncode, 1, "A worker descendant survived")
        self.assertFalse((self.project / "src/started.txt").exists())
        self.assertFalse((self.project / "src/late.txt").exists())
        self.assertEqual(result["application"]["state"], "not_applied")
        return result, task

    def test_cancellation_stops_descendants_and_prevents_application(self) -> None:
        result, task = self.stop_during_run(["cancel", "TASK"])
        self.assertEqual((result["outcome"], result["attempt_state"]), ("authority_lost", "cancelled"))
        self.assertEqual(self.cli("status", task)["state"], "cancelled")

    def test_revocation_stops_descendants_and_prevents_application(self) -> None:
        result, _ = self.stop_during_run(["revoke", "TASK", "worker"])
        self.assertEqual((result["outcome"], result["attempt_state"]), ("authority_lost", "cancelled"))

    def test_expiry_stops_descendants_and_prevents_application(self) -> None:
        result, _ = self.stop_during_run([], seconds=30)
        self.assertEqual(result["outcome"], "authority_lost")
        self.assertEqual(result["attempt_state"], "failed")

    def test_unavailable_runtime_blocks_before_any_provider_request(self) -> None:
        task = self.approved_task()
        with ScriptedProvider([]) as provider:
            args = self.run_args(task, provider)
            args[args.index("--fuse-helper") + 1] = str(self.base / "missing-helper")
            result = self.cli(*args)
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(provider.bodies, [])
        self.assertEqual(result["application"]["state"], "not_applied")
