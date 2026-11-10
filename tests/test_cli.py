import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
            run("cancel", task)
            self.assertEqual(run("status", task)["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
