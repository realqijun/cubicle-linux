"""Blocking behavior is testable even when platform confinement is unavailable."""

import sys
import subprocess
import tempfile
import unittest
import os
import json
import time
import socket
import threading
from pathlib import Path

from planner.enforcement import Backend, ProtectionUnavailable
from planner.policy import Policy
from planner.records import Grant


class EnforcementTests(unittest.TestCase):
    def test_unverified_backend_refuses_before_executing_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            marker = root / "effect.txt"
            policy = Policy(root=root, read=("**",), write=("**",))
            grant = Grant("task", "worker", read=("**",), write=("**",))
            backend = Backend()
            command = [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('effect.txt').write_text('effect')",
            ]
            # Independent positive control: the identical command really has an
            # observable effect when it runs. An absent marker alone proves little.
            subprocess.run(command, cwd=root, check=True, timeout=5)
            self.assertEqual(marker.read_text(), "effect")
            marker.unlink()
            with self.assertRaises(ProtectionUnavailable) as caught:
                backend.run(
                    policy,
                    grant,
                    command,
                    timeout=5,
                )
            self.assertFalse(marker.exists())
            self.assertFalse(caught.exception.status.verified)
            self.assertIn("BLOCKED", str(caught.exception))


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("PLANNER_RUN_MACOS_PROBES") == "1",
                     "Explicit local macOS confinement diagnostic only")
class MacOSCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        from planner.native.macos_contract import MacOSFixture
        self.fixture = MacOSFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root
        for name in ("src", "tests", "config"):
            (self.root / name).mkdir()
        (self.root / ".env").write_text("DUMMY_SECRET")
        (self.root / "config/.env.production").write_text("NESTED_DUMMY_SECRET")
        (self.root / "README.md").write_text("public")
        (self.root / "tests/protected.txt").write_text("protected")

    def probe(self, script: str) -> subprocess.CompletedProcess[str]:
        return self.fixture.run_probe(script)

    def test_candidate_denies_existing_sensitive_paths_and_symlinks(self) -> None:
        (self.root / "src/alias").symlink_to(self.root / ".env")
        outside = self.root.parent / "outside.txt"
        outside.write_text("OUTSIDE_DUMMY")
        (self.root / "src/outside").symlink_to(outside)
        names = [".env", "config/.env.production", "src/../.env", "src/alias", "src/outside",
                 str(self.root / ".env")]
        self.assertEqual((self.root / ".env").read_text(), "DUMMY_SECRET")
        result = self.probe("from pathlib import Path\nimport json\nresults=[]\n"
                            f"for name in {names!r}:\n"
                            " try: Path(name).read_bytes(); results.append(False)\n"
                            " except PermissionError: results.append(True)\n"
                            "print(json.dumps(results))\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [True] * len(names))

    def test_candidate_denies_protected_write_rename_delete_and_truncate(self) -> None:
        result = self.probe("from pathlib import Path\nimport os,json\n"
                            "Path('src/movable').write_text('allowed')\nresults=[]\n"
                            "actions=[lambda:Path('tests/protected.txt').write_text('bad'),"
                            "lambda:os.rename('src/movable','tests/replaced.txt'),"
                            "lambda:Path('tests/protected.txt').unlink(),"
                            "lambda:os.truncate('tests/protected.txt',0)]\n"
                            "for action in actions:\n"
                            " try: action(); results.append(False)\n"
                            " except PermissionError: results.append(True)\n"
                            "print(json.dumps(results))")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [True, True, True, True])
        self.assertEqual((self.root / "tests/protected.txt").read_text(), "protected")
        self.assertFalse((self.root / "tests/replaced.txt").exists())

    def test_candidate_denies_new_matching_secret_and_allows_source_replacement(self) -> None:
        result = self.probe("from pathlib import Path\nimport os\n"
                            "Path('src/.env.new').write_text('NEW_DUMMY')\n"
                            "try: Path('src/.env.new').read_bytes()\n"
                            "except PermissionError: pass\n"
                            "else: raise AssertionError('new secret readable')\n"
                            "Path('src/new').write_text('replacement')\n"
                            "Path('src/old').write_text('old')\n"
                            "os.replace('src/new','src/old')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "src/old").read_text(), "replacement")

    def test_candidate_denies_sensitive_read_in_children_and_grandchildren(self) -> None:
        leaf = "from pathlib import Path; Path('.env').read_bytes()"
        middle = ("import subprocess,sys; result=subprocess.run([sys.executable,'-I','-c'," + repr(leaf) +
                  "],capture_output=True); raise SystemExit(0 if result.returncode else 1)")
        result = self.probe("import subprocess,sys\n"
                            f"child=subprocess.run([sys.executable,'-I','-c',{leaf!r}],capture_output=True)\n"
                            "assert child.returncode != 0\n"
                            f"grandchild=subprocess.run([sys.executable,'-I','-c',{middle!r}],capture_output=True)\n"
                            "assert grandchild.returncode == 0\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_closes_existing_inheritable_secret_descriptor(self) -> None:
        descriptor = os.open(self.root / ".env", os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        os.set_inheritable(descriptor, True)
        result = self.probe(f"import os\ntry: os.read({descriptor},100)\n"
                            "except OSError: pass\nelse: raise AssertionError('descriptor inherited')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_refuses_startup_with_preexisting_hardlinks(self) -> None:
        os.link(self.root / ".env", self.root / "src/secret-alias")
        with self.assertRaises(ProtectionUnavailable):
            self.probe("from pathlib import Path; Path('src/should-not-run').write_text('bad')")
        self.assertFalse((self.root / "src/should-not-run").exists())

    def test_candidate_denies_hardlink_created_externally_during_execution(self) -> None:
        def mutate() -> None:
            deadline = time.monotonic() + 3
            while not (self.root / "src/ready").exists():
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.01)
            os.link(self.root / ".env", self.root / "src/live-alias")
            (self.root / "src/go").write_text("ready")

        actor = threading.Thread(target=mutate, daemon=True)
        actor.start()
        self.addCleanup(actor.join, 4)
        result = self.probe("from pathlib import Path\nimport time\n"
                            "Path('src/ready').write_text('ready')\n"
                            "while not Path('src/go').exists(): time.sleep(0.01)\n"
                            "try: Path('src/live-alias').read_bytes()\n"
                            "except PermissionError: pass\nelse: raise AssertionError('live alias readable')\n")
        actor.join(1)
        self.assertTrue((self.root / "src/live-alias").exists())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_denies_sensitive_symlink_name_to_public_target(self) -> None:
        (self.root / "src/.env.alias").symlink_to(self.root / "README.md")
        result = self.probe("from pathlib import Path\n"
                            "try: Path('src/.env.alias').read_bytes()\n"
                            "except PermissionError: pass\nelse: raise AssertionError('sensitive alias readable')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_denies_read_after_moving_sensitive_inode_to_allowed_name(self) -> None:
        (self.root / "src/.env.movable").write_text("MOVABLE_DUMMY_SECRET")
        result = self.probe("import os\nfrom pathlib import Path\n"
                            "try: os.rename('src/.env.movable','src/visible')\n"
                            "except PermissionError: pass\n"
                            "else:\n"
                            " try: Path('src/visible').read_bytes()\n"
                            " except PermissionError: pass\n"
                            " else: raise AssertionError('renamed secret readable')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_denies_worker_creation_of_hardlinks(self) -> None:
        result = self.probe("import os\ntry: os.link('.env','src/new-alias')\n"
                            "except PermissionError: pass\nelse: raise AssertionError('link created')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "src/new-alias").exists())

    def test_candidate_denies_case_alias_and_trusted_state_access(self) -> None:
        trusted = self.root.parent / "state.sqlite"
        trusted.write_text("TRUSTED_STATE")
        self.assertEqual((self.root / ".ENV").read_text(), "DUMMY_SECRET")
        result = self.probe("from pathlib import Path\n"
                            f"for name in ['.ENV',{str(trusted)!r}]:\n"
                            " try: Path(name).read_bytes()\n"
                            " except PermissionError: pass\n else: raise AssertionError('access allowed')\n")
        # Assert each access independently rather than interpreting a worker claim.
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_blocks_parent_process_and_unix_control_socket(self) -> None:
        address = str(self.root.parent / "control.sock")
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(address)
        server.listen(2)
        # Positive controls: same uid can signal-check this process and connect.
        os.kill(os.getpid(), 0)
        with socket.socket(socket.AF_UNIX) as control:
            control.connect(address)
        result = self.probe(f"import os,socket\ntry: os.kill({os.getpid()},0)\n"
                            "except PermissionError: pass\nelse: raise AssertionError('parent signal allowed')\n"
                            "with socket.socket(socket.AF_UNIX) as connection:\n"
                            f" try: connection.connect({address!r})\n"
                            " except PermissionError: pass\n else: raise AssertionError('control connected')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_candidate_does_not_leave_detached_descendant_after_owner_exit(self) -> None:
        result = self.probe("import os,time\nfrom pathlib import Path\nchild=os.fork()\n"
                            "if child == 0:\n"
                            " os.setsid(); os.close(1); os.close(2); time.sleep(0.2)\n"
                            " Path('src/after-exit').write_text('late effect'); os._exit(0)\n"
                            "print(child)\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        time.sleep(0.4)
        self.assertFalse((self.root / "src/after-exit").exists(), "Detached descendant retained authority")

    def test_candidate_runs_legitimate_read_and_write(self) -> None:
        result = self.probe("from pathlib import Path; assert Path('README.md').read_text() == 'public'; Path('src/result.txt').write_text('authorized')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "src/result.txt").read_text(), "authorized")


if __name__ == "__main__":
    unittest.main()
