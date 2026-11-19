"""Actual kernel filesystem probes for the evaluated FUSE candidate."""

import mmap
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import threading
from unittest.mock import patch

from planner.native.mediatedfs import MediatedWorkspace
from planner.policy import Policy
from planner.records import Grant


class MediatorSetupTests(unittest.TestCase):
    def test_unsupported_worker_tool_assignment_is_refused_before_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'project'
            source.mkdir()
            helper = root / 'helper'
            helper.touch()
            policy = Policy(source, read=('**',), tools=('file',))
            grant = Grant('task', 'worker', policy.read, tools=policy.tools)
            with self.assertRaises(ValueError):
                MediatedWorkspace(policy, grant, root / 'run', helper=helper)
            self.assertFalse((root / 'run').exists())

    def test_helper_exec_failure_does_not_leave_policy_thread_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'project'
            source.mkdir()
            (source / 'public').write_text('public')
            helper = root / 'not-executable'
            helper.write_text('not executable')
            policy = Policy(source, read=('**',), tools=('shell',))
            grant = Grant('task', 'worker', policy.read, tools=policy.tools)
            before = set(threading.enumerate())
            with self.assertRaises(OSError):
                with MediatedWorkspace(policy, grant, root / 'run', helper=helper):
                    self.fail('Invalid helper started')
            self.assertFalse(set(threading.enumerate()) - before)


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("PLANNER_RUN_FUSE_PROBES") == "1",
                     "Real Linux FUSE mount and compiled bridge are required")
class MediatedFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="planner-fuse-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        source = self.base / "project"
        source.mkdir()
        (source / "src").mkdir()
        (source / "src/private.txt").write_text("DUMMY_SECRET")
        (source / "src/public.txt").write_text("public")
        policy = Policy(source, read=("**",), write=("src/**",),
                        deny_read=("src/private.txt", "**/.env*"), tools=("shell",))
        grant = Grant("test", "worker", policy.read, policy.write, policy.tools)
        self.fs = self.enterContext(MediatedWorkspace(
            policy, grant, self.base / "run", helper=Path(os.environ["PLANNER_FUSE_BRIDGE"])))
        self.mount = self.fs.mount

    def test_allowed_read_and_write_with_denied_actual_secret_read(self) -> None:
        self.assertEqual((self.mount / "src/public.txt").read_text(), "public")
        # Withheld: run storage holds an empty stand-in, never the protected bytes.
        self.assertEqual((self.fs.backing / "src/private.txt").read_text(), "")
        self.assertEqual(self.fs.placeholders, {"src/private.txt"})
        with self.assertRaises(PermissionError):
            (self.mount / "src/private.txt").read_text()
        (self.mount / "src/public.txt").write_text("updated")
        self.assertEqual((self.fs.backing / "src/public.txt").read_text(), "updated")

    def test_unmount_failure_still_stops_serving_and_can_be_reconciled(self) -> None:
        with patch('planner.native.mediatedfs.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, ['fusermount3'])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.fs.close()
        with self.assertRaises(OSError):
            (self.mount / 'src/public.txt').read_bytes()
        # Retry only cleanup of the now-dead mount, never worker operations.
        self.fs.close()

    def test_directory_create_move_and_remove_are_permitted_but_not_secret_ancestor_move(self) -> None:
        (self.mount / "src/new").mkdir()
        (self.mount / "src/new/public").write_text("allowed")
        (self.mount / "src/new").rename(self.mount / "src/moved")
        self.assertEqual((self.mount / "src/moved/public").read_text(), "allowed")
        (self.mount / "src/moved/public").unlink()
        (self.mount / "src/moved").rmdir()
        with self.assertRaises(PermissionError):
            (self.mount / "src").rename(self.mount / "visible")

    def test_blind_writer_must_replace_a_withheld_file_completely(self) -> None:
        with self.assertRaises(PermissionError):
            with (self.mount / "src/private.txt").open("a") as stream:
                stream.write("partial")
        self.assertEqual((self.fs.backing / "src/private.txt").read_text(), "")
        self.assertIn(("write", "src/private.txt"), self.fs.denials)

    def test_withheld_stand_in_cannot_be_deleted(self) -> None:
        with self.assertRaises(PermissionError):
            (self.mount / "src/private.txt").unlink()
        self.assertTrue((self.fs.backing / "src/private.txt").exists())
        self.assertIn(("write", "src/private.txt"), self.fs.denials)

    def test_blind_replacement_does_not_grant_read(self) -> None:
        (self.mount / "src/private.txt").write_text("replacement")
        self.assertEqual((self.fs.backing / "src/private.txt").read_text(), "replacement")
        with self.assertRaises(PermissionError):
            (self.mount / "src/private.txt").read_text()
        with self.assertRaises(PermissionError):
            (self.mount / "src/private.txt").rename(self.mount / "src/visible.txt")

    def test_secret_ancestor_move_is_denied_even_when_both_paths_are_writable(self) -> None:
        policy = Policy(self.fs.original_policy.root, read=("**",), write=("**",),
                        deny_read=("src/private.txt",), tools=("shell",))
        grant = Grant("wide", "worker", policy.read, policy.write, policy.tools)
        with MediatedWorkspace(policy, grant, self.base / "wide",
                               helper=Path(os.environ["PLANNER_FUSE_BRIDGE"])) as candidate:
            with self.assertRaises(PermissionError):
                (candidate.mount / "src").rename(candidate.mount / "visible")
            with self.assertRaises(PermissionError):  # Moving the stand-in would lose the original.
                (candidate.mount / "src/private.txt").rename(candidate.mount / "src/other.txt")
            self.assertEqual((candidate.backing / "src/private.txt").read_text(), "")
        self.assertEqual((self.fs.original_policy.root / "src/private.txt").read_text(), "DUMMY_SECRET")

    def test_new_sensitive_name_stays_unreadable_and_unassigned_write_is_denied(self) -> None:
        (self.mount / "src/.env.new").write_text("new secret")
        with self.assertRaises(PermissionError):
            (self.mount / "src/.env.new").read_text()
        with self.assertRaises(PermissionError):
            (self.mount / "outside").write_text("bad")

    def test_open_descriptor_is_rechecked_after_rename_and_cannot_mmap(self) -> None:
        with (self.mount / "src/public.txt").open("rb") as stream:
            with self.assertRaises((OSError, ValueError)):
                mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
            self.assertEqual(stream.read(1), b"p")
            (self.mount / "src/public.txt").rename(self.mount / "src/.env.renamed")
            with self.assertRaises(OSError):
                os.pread(stream.fileno(), 1, 1)

    def test_subprocess_reads_are_restricted_by_kernel_mount(self) -> None:
        script = ("from pathlib import Path\n"
                  "assert Path('src/public.txt').read_text()=='public'\n"
                  "try: Path('src/private.txt').read_bytes()\n"
                  "except PermissionError: pass\nelse: raise AssertionError('read permitted')\n")
        result = subprocess.run([sys.executable, "-I", "-c", script], cwd=self.mount,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
