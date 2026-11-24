"""Operator alerts: escaped terminal warnings, rate-limited sound and desktop notification."""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import planner
from planner.alerts import SIGN, Alarm
from planner.policy import Policy
from planner.records import Action, Grant
from planner.supervisor import Supervisor

ROOT = Path("/srv/project")


def denial(resource: str, operation: str = "read", tool: str = "filesystem",
           reason: str = "Worker operation denied") -> dict:
    return {"id": 1, "allowed": 0, "worker": "worker", "operation": operation, "resource": resource,
            "tool": tool, "reason": reason}


class AlarmTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.dict(os.environ, {"NO_COLOR": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION"):
            os.environ.pop(name, None)
        self.stream = io.StringIO()
        self.now = 0.0
        self.alarm = Alarm(ROOT, self.stream, clock=lambda: self.now)
        self.addCleanup(self.alarm.close)

    def test_each_refusal_is_a_warning_line_with_a_project_relative_path(self) -> None:
        self.alarm(denial(str(ROOT / ".env")))
        self.alarm(denial(str(ROOT / "tests/test_app.py"), "write", "write", "Explicit file denial"))
        self.alarm(denial("model.request", "mediation", "opencode", "Unadmitted context"))
        self.alarm(denial("/etc/passwd"))
        output = self.stream.getvalue()
        self.assertEqual(output.count(SIGN[1]), 1)  # One sign for the burst.
        self.assertTrue(output.startswith("\a\n" + "\n".join(SIGN) + "\n[BLOCKED]"))
        lines = [line for line in output.splitlines() if line.startswith("[BLOCKED]")]
        self.assertEqual(lines, [
            "[BLOCKED] The worker tried to read .env - refused (filesystem: Worker operation denied)",
            "[BLOCKED] The worker tried to write tests/test_app.py - refused (write: Explicit file denial)",
            "[BLOCKED] The worker tried to use model.request - refused (opencode: Unadmitted context)",
            "[BLOCKED] The worker tried to read /etc/passwd - refused (filesystem: Worker operation denied)",
        ])
        self.assertEqual(self.alarm.count, 4)

    def test_worker_chosen_names_cannot_inject_terminal_control_sequences(self) -> None:
        self.alarm(denial(str(ROOT / "\x1b]0;owned\x07\x1b[2J\u202e.env\nfake"), reason="bad\rreason"))
        output = self.stream.getvalue()
        self.assertEqual(output.count("\x1b"), 0)
        self.assertEqual(output.count("\a"), 1)  # Only the alarm's own bell.
        self.assertEqual(output.count("\n"), 2 + len(SIGN))  # Bell line, sign, one warning line.
        self.assertIn("\\x1b]0;owned\\x07\\x1b[2J\\u202e.env\\nfake", output)
        self.assertIn("bad\\rreason", output)

    def test_color_highlights_only_the_alarm_text(self) -> None:
        os.environ.pop("NO_COLOR")
        alarm = Alarm(ROOT, self.stream, clock=lambda: self.now)
        alarm(denial(str(ROOT / "\x1b[31m.env")))
        output = self.stream.getvalue()
        self.assertTrue(output.startswith("\a\n" + "".join(f"\033[1;33m{row}\033[0m\n" for row in SIGN)
                                          + "\033[1;97;41m BLOCKED \033[0m"))
        self.assertIn("\033[1mread \\x1b[31m.env\033[0m", output)
        self.assertEqual(output.count("\x1b"), 4 + 2 * len(SIGN))

    def test_bursts_sound_once_but_every_refusal_is_printed(self) -> None:
        for moment in (0.0, 0.5, 2.9, 3.0, 3.1):
            self.now = moment
            self.alarm(denial(str(ROOT / f".env{moment}")))
        lines = self.stream.getvalue().splitlines()
        warnings = [index for index, line in enumerate(lines) if line.startswith("[BLOCKED]")]
        self.assertEqual(len(warnings), 5)
        self.assertEqual([lines[index - 1] == SIGN[-1] for index in warnings], [True, False, False, True, False])
        self.assertEqual(self.stream.getvalue().count("\a"), 2)

    def test_companion_records_of_one_refusal_are_shown_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            (project / "tests").mkdir(parents=True)
            (project / "tests/test_app.py").write_text("assert True\n")
            supervisor = Supervisor(Path(directory) / "state.sqlite")
            self.addCleanup(supervisor.close)
            task = supervisor.propose(Policy.load(project, Path(planner.__file__).parent / "templates/source.toml"))
            supervisor.approve_task(task)
            policy = supervisor.policy(task)
            supervisor.approve_worker(Grant(task, "worker", policy.read, policy.write, policy.tools))
            # The broker's shape for a refused write tool call: read and write of one file, then its hook record.
            supervisor.authorize_many([Action(task, "worker", "read", "tests/test_app.py"),
                                       Action(task, "worker", "write", "tests/test_app.py")])
            supervisor.record_mediation_denial(task, "worker", "tool.before", "file_access_denied")
            supervisor.record_mediation_denial(task, "worker", "chat.params", "unadmitted_context")
            refused = [row for row in supervisor.history(task) if not row["allowed"]]
            self.assertEqual(len(refused), 4)  # Both companions are recorded, and kept.
            alarm = Alarm(project.resolve(), self.stream, clock=lambda: self.now)
            for row in refused:
                alarm(row)
        lines = [line for line in self.stream.getvalue().splitlines() if line.startswith("[BLOCKED]")]
        self.assertEqual(lines, [
            "[BLOCKED] The worker tried to write tests/test_app.py - refused (file: Explicit file denial)",
            "[BLOCKED] The worker tried to use chat.params - refused (opencode: unadmitted_context)"])
        self.assertEqual(alarm.count, 2)

    def test_a_closed_terminal_does_not_raise(self) -> None:
        self.stream.close()
        self.alarm(denial(str(ROOT / ".env")))
        self.assertEqual(self.alarm.count, 1)


@unittest.skipUnless(sys.platform == "linux", "Desktop alerts use the freedesktop sound and notification tools")
class DesktopAlertTests(unittest.TestCase):
    """Real child processes stand in for the sound player and notification client."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="planner-alerts-")
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        tools = self.base / "bin"
        tools.mkdir()
        for name in ("canberra-gtk-play", "notify-send"):
            script = tools / name
            script.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                              f"open({str(self.base / name)!r}, 'a').write("
                              "json.dumps({'argv': sys.argv[1:], 'env': dict(os.environ)}) + '\\n')\n")
            script.chmod(0o700)
        patcher = mock.patch.dict(os.environ, {"PATH": str(tools), "DISPLAY": ":0", "NO_COLOR": "1",
                                               "DEMO_PROVIDER_KEY": "dummy-credential"})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("SSH_CONNECTION", None)

    def calls(self, name: str) -> list[dict]:
        path = self.base / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_local_session_plays_the_warning_sound_and_notifies_once_per_burst(self) -> None:
        alarm = Alarm(ROOT, io.StringIO(), clock=lambda: 0.0)
        alarm(denial(str(ROOT / "<b>.env</b>")))
        alarm(denial(str(ROOT / "second")))
        alarm.close()
        sounds, notes = self.calls("canberra-gtk-play"), self.calls("notify-send")
        self.assertEqual([call["argv"][:2] for call in sounds], [["--id", "dialog-warning"]])
        self.assertEqual(len(notes), 1)
        self.assertIn("--urgency=critical", notes[0]["argv"])
        self.assertEqual(notes[0]["argv"][-1],
                         "The worker tried to read &lt;b&gt;.env&lt;/b&gt;. The supervisor refused it.")
        for call in sounds + notes:
            self.assertEqual(call["env"].get("DISPLAY"), ":0")
            self.assertNotIn("DEMO_PROVIDER_KEY", call["env"])

    def test_remote_session_rings_the_terminal_bell_only(self) -> None:
        os.environ["SSH_CONNECTION"] = "10.0.2.2 50000 10.0.2.15 22"
        stream = io.StringIO()
        alarm = Alarm(ROOT, stream)
        alarm(denial(str(ROOT / ".env")))
        alarm.close()
        self.assertTrue(stream.getvalue().startswith("\a"))
        self.assertEqual(self.calls("canberra-gtk-play") + self.calls("notify-send"), [])


if __name__ == "__main__":
    unittest.main()
