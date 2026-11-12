"""Public workspace and real Linux process boundaries; dummy files only."""

import tempfile
import unittest
import os
import subprocess
import sys
import time
import threading
import socket
from pathlib import Path

from planner.policy import Policy
from planner.records import Grant
from planner.native.linux_sandbox import LinuxSandbox


class WorkspaceTests(unittest.TestCase):
    def test_only_assigned_regular_inputs_are_copied_and_edits_stay_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            (project / "src").mkdir()
            (project / "src/main.py").write_text("original")
            (project / "src/.env.local").write_text("DUMMY_SECRET")
            (project / "private.txt").write_text("UNASSIGNED")
            (project / "src/alias").symlink_to(project / "src/.env.local")
            policy = Policy(project, read=("**",), write=("src/**",),
                            deny_read=("**/.env*",), tools=("shell",))
            grant = Grant("task", "worker", ("src/**",), ("src/**",), ("shell",))
            sandbox = LinuxSandbox(policy, grant, base / "run")
            sandbox.prepare()
            self.assertEqual((sandbox.workspace / "src/main.py").read_text(), "original")
            self.assertFalse((sandbox.workspace / "src/.env.local").exists())
            self.assertFalse((sandbox.workspace / "private.txt").exists())
            self.assertFalse((sandbox.workspace / "src/alias").exists())
            (sandbox.workspace / "src/main.py").write_text("edited")
            self.assertEqual((project / "src/main.py").read_text(), "original")

    def test_hardlinked_inputs_are_omitted_and_existing_output_is_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            (project / ".env").write_text("DUMMY_SECRET")
            os.link(project / ".env", project / "public.txt")
            policy = Policy(project, read=("**",), deny_read=("**/.env*",), tools=("shell",))
            sandbox = LinuxSandbox(policy, Grant("t", "w", policy.read, tools=policy.tools), base / "run")
            sandbox.prepare()
            self.assertFalse((sandbox.workspace / "public.txt").exists())
            with self.assertRaises(FileExistsError):
                sandbox.prepare()

    def test_unsupported_scope_syntax_is_refused_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            policy = Policy(project, read=("src/[ab]*",), tools=("shell",))
            with self.assertRaises(ValueError):
                LinuxSandbox(policy, Grant("t", "w", policy.read, tools=policy.tools), root / "run")
            self.assertFalse((root / "run").exists())


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("PLANNER_RUN_LINUX_PROBES") == "1",
                     "Requires Linux and the generated enforcing AppArmor profile")
class LinuxProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="planner-linux-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.project.mkdir()
        (self.project / "src").mkdir()
        (self.project / "tests").mkdir()
        (self.project / "README.md").write_text("public")
        (self.project / ".env").write_text("DUMMY_SECRET")
        (self.project / "tests/protected.txt").write_text("protected")
        self.policy = Policy(self.project, read=("**",), write=("src/**",),
                             deny_read=("**/.env*", "blocked/**"), deny_write=("tests/**",), tools=("shell",))
        grant = Grant("task", "worker", self.policy.read, self.policy.write, self.policy.tools)
        self.sandbox = LinuxSandbox(self.policy, grant, self.base / "run")
        self.probe_timeout = float(os.environ.get("PLANNER_PROBE_TIMEOUT", "20"))

    def probe(self, script: str, *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return self.sandbox.run(["/usr/bin/python3", "-I", "-c", script],
                                timeout=self.probe_timeout if timeout is None else timeout)

    def test_real_process_can_read_and_edit_assigned_files(self) -> None:
        result = self.probe("from pathlib import Path; assert Path('README.md').read_text() == 'public'; "
                            "Path('src/result').write_text('authorized')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sandbox.workspace / "src/result").read_text(), "authorized")
        self.assertFalse((self.project / "src/result").exists())

    def test_kernel_denies_actual_secret_bytes_including_descendants(self) -> None:
        leaf = "from pathlib import Path; print(Path('.env').read_text())"
        positive = subprocess.run([sys.executable, "-I", "-c", leaf], cwd=self.project,
                                  capture_output=True, text=True, check=True)
        self.assertEqual(positive.stdout.strip(), "DUMMY_SECRET")
        self.sandbox.prepare()
        # Deliberately insert actual bytes after filtering: absence alone cannot
        # establish that AppArmor denies reading an existing secret inode.
        (self.sandbox.workspace / ".env").write_text("DUMMY_SECRET")
        middle = ("import subprocess,sys; p=subprocess.run([sys.executable,'-I','-c'," + repr(leaf)
                  + "],capture_output=True); assert p.returncode != 0 and b'PermissionError' in p.stderr")
        grandchild = f"import subprocess,sys; subprocess.run([sys.executable,'-I','-c',{middle!r}],check=True)"
        result = self.probe("from pathlib import Path\nimport subprocess,sys\n"
                            "for name in ['.env','src/../.env','/workspace/.env',"
                            "'/proc/self/root/workspace/.env','/proc/1/root/workspace/.env']:\n"
                            " try: Path(name).read_bytes()\n"
                            " except PermissionError: pass\n else: raise AssertionError('secret readable')\n"
                            f"for script in {[middle, grandchild]!r}:\n"
                            " subprocess.run([sys.executable,'-I','-c',script],check=True)\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("DUMMY_SECRET", result.stdout)

    def test_kernel_denies_protected_write_rename_unlink_and_truncate(self) -> None:
        result = self.probe("from pathlib import Path\nimport os\nPath('src/movable').write_text('good')\n"
                            "actions=[lambda:Path('tests/protected.txt').write_text('bad'),"
                            "lambda:os.rename('src/movable','tests/replaced'),"
                            "lambda:Path('tests/protected.txt').unlink(),"
                            "lambda:os.truncate('tests/protected.txt',0)]\n"
                            "for action in actions:\n"
                            " try: action()\n except PermissionError: pass\n"
                            " else: raise AssertionError('protected write allowed')\n"
                            "Path('src/new').write_text('replacement'); os.replace('src/new','src/movable')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sandbox.workspace / "tests/protected.txt").read_text(), "protected")
        self.assertEqual((self.sandbox.workspace / "src/movable").read_text(), "replacement")

    def test_new_sensitive_names_and_link_aliases_are_denied_continuously(self) -> None:
        result = self.probe("from pathlib import Path\nimport os\n"
                            "actions=[lambda:Path('src/.env.new').write_text('new'),"
                            "lambda:os.symlink('../README.md','src/.env.alias'),"
                            "lambda:os.link('README.md','src/alias')]\n"
                            "for action in actions:\n"
                            " try: action()\n except PermissionError: pass\n"
                            " else: raise AssertionError('new sensitive name or link allowed')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_secret_inserted_after_start_is_unreadable_and_cannot_be_renamed(self) -> None:
        def insert() -> None:
            deadline = time.monotonic() + self.probe_timeout
            while not (self.sandbox.workspace / "src/ready").exists():
                if time.monotonic() > deadline:
                    return
                time.sleep(0.01)
            (self.sandbox.workspace / "src/.env.incoming").write_text("LATE_DUMMY_SECRET")
            (self.sandbox.workspace / "src/go").touch()

        actor = threading.Thread(target=insert, daemon=True)
        actor.start()
        self.addCleanup(actor.join, self.probe_timeout)
        result = self.probe("from pathlib import Path\nimport os,time\nPath('src/ready').touch()\n"
                            "while not Path('src/go').exists(): time.sleep(0.01)\n"
                            "for action in [lambda:Path('src/.env.incoming').read_bytes(),"
                            "lambda:os.rename('src/.env.incoming','src/visible')]:\n"
                            " try: action()\n except PermissionError: pass\n"
                            " else: raise AssertionError('late secret escaped')\n")
        actor.join(self.probe_timeout)
        self.assertTrue((self.sandbox.workspace / "src/.env.incoming").exists())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tree_denial_covers_a_file_at_the_tree_root(self) -> None:
        self.sandbox.prepare()
        (self.sandbox.workspace / "blocked").write_text("TREE_ROOT_DUMMY")
        result = self.probe("from pathlib import Path\ntry: Path('blocked').read_bytes()\n"
                            "except PermissionError: pass\nelse: raise AssertionError('tree root readable')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_profile_cannot_execute_the_command(self) -> None:
        import uuid

        grant = Grant("task", "worker", ("missing-" + uuid.uuid4().hex,), self.policy.write, self.policy.tools)
        sandbox = LinuxSandbox(self.policy, grant, self.base / "missing-profile-run")
        result = sandbox.run(["/usr/bin/python3", "-I", "-c",
                              "from pathlib import Path; Path('src/effect').write_text('bad')"], timeout=self.probe_timeout)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((sandbox.workspace / "src/effect").exists())

    def test_worker_cannot_leave_its_apparmor_profile_or_open_network(self) -> None:
        result = self.probe("import subprocess,socket\n"
                            "p=subprocess.run(['/usr/bin/aa-exec','-p','unconfined','--',"
                            "'/usr/bin/touch','src/escaped'],capture_output=True)\n"
                            "assert p.returncode != 0\n"
                            "try: socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
                            "except PermissionError: pass\nelse: raise AssertionError('network allowed')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.sandbox.workspace / "src/escaped").exists())

    def test_revocation_stops_the_running_process(self) -> None:
        from planner.supervisor import Supervisor

        supervisor = Supervisor(self.base / "control.sqlite")
        self.addCleanup(supervisor.close)
        task = supervisor.propose(self.policy)
        supervisor.approve_task(task, revision=1)
        supervisor.approve_worker(Grant(task, "worker", self.policy.read, self.policy.write, self.policy.tools))

        def authority() -> None:
            if (self.sandbox.workspace / "src/ready").exists():
                supervisor.revoke_worker(task, "worker")
            supervisor.check_worker(task, "worker", "shell")

        with self.assertRaises(ValueError):
            self.sandbox.run(["/usr/bin/python3", "-I", "-c",
                              "from pathlib import Path; import time; Path('src/ready').touch(); "
                              "time.sleep(0.8); Path('src/after-revoke').write_text('bad')"],
                             timeout=self.probe_timeout, check_authority=authority)
        time.sleep(1)
        self.assertFalse((self.sandbox.workspace / "src/after-revoke").exists())

    def test_operator_cli_uses_approved_grant_and_refuses_a_revoked_worker(self) -> None:
        from planner.supervisor import Supervisor

        state = self.base / "cli.sqlite"
        supervisor = Supervisor(state)
        self.addCleanup(supervisor.close)
        task = supervisor.propose(self.policy)
        supervisor.approve_task(task, revision=1)
        supervisor.approve_worker(Grant(task, "worker", self.policy.read, self.policy.write, self.policy.tools))
        command = [sys.executable, "-m", "planner.native.linux_sandbox", "--state", str(state),
                   "--task", task, "--worker", "worker", "--output", str(self.sandbox.output)]
        profile = subprocess.run([*command, "profile"], capture_output=True, text=True,
                                 cwd=Path(__file__).parents[2], timeout=self.probe_timeout)
        self.assertEqual(profile.returncode, 0, profile.stderr)
        self.assertEqual(profile.stdout, self.sandbox.profile_text())
        result = subprocess.run([*command, "run", "--", "/usr/bin/python3", "-c",
                                 "from pathlib import Path; Path('src/cli-result').write_text('approved')"],
                                capture_output=True, text=True, cwd=Path(__file__).parents[2],
                                timeout=self.probe_timeout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.sandbox.workspace / "src/cli-result").read_text(), "approved")
        self.assertFalse((self.project / "src/cli-result").exists())
        supervisor.revoke_worker(task, "worker")
        denied = subprocess.run([*command, "profile"], capture_output=True, text=True,
                                cwd=Path(__file__).parents[2], timeout=self.probe_timeout)
        self.assertEqual(denied.returncode, 2)

    def test_live_changes_to_original_project_cannot_inject_secret_alias(self) -> None:
        def mutate() -> None:
            ready = self.sandbox.workspace / "src/ready"
            deadline = time.monotonic() + self.probe_timeout
            while not ready.exists():
                if time.monotonic() > deadline:
                    return
                time.sleep(0.01)
            os.link(self.project / ".env", self.project / "src/live-alias")
            (self.sandbox.workspace / "src/go").touch()

        actor = threading.Thread(target=mutate, daemon=True)
        actor.start()
        self.addCleanup(actor.join, self.probe_timeout)
        result = self.probe("from pathlib import Path\nimport time\nPath('src/ready').touch()\n"
                            "while not Path('src/go').exists(): time.sleep(0.01)\n"
                            "assert not Path('src/live-alias').exists()\n")
        actor.join(self.probe_timeout)
        self.assertTrue((self.project / "src/live-alias").exists())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inherited_secret_descriptor_and_environment_are_removed(self) -> None:
        import fcntl

        with (self.project / ".env").open() as secret:
            descriptor = fcntl.fcntl(secret.fileno(), fcntl.F_DUPFD, 100)
        self.addCleanup(os.close, descriptor)
        os.set_inheritable(descriptor, True)
        os.environ["PLANNER_TEST_CREDENTIAL"] = "DUMMY_CREDENTIAL"
        self.addCleanup(os.environ.pop, "PLANNER_TEST_CREDENTIAL")
        result = self.probe(f"import os\nassert 'PLANNER_TEST_CREDENTIAL' not in os.environ\n"
                            f"try: os.read({descriptor},100)\nexcept OSError: pass\n"
                            "else: raise AssertionError('secret descriptor inherited')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_host_files_processes_and_control_socket_are_inaccessible(self) -> None:
        trusted = self.base / "trusted.sqlite"
        trusted.write_text("TRUSTED_DUMMY")
        address = str(self.base / "control.sock")
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(address)
            server.listen()
            with socket.socket(socket.AF_UNIX) as positive:
                positive.connect(address)
            result = self.probe("from pathlib import Path\nimport os,socket\n"
                                f"for name in [{str(trusted)!r},{str(self.project / '.env')!r}]:\n"
                                " try: Path(name).read_bytes()\n except OSError: pass\n"
                                " else: raise AssertionError('host file visible')\n"
                                "with socket.socket(socket.AF_UNIX) as connection:\n"
                                f" try: connection.connect({address!r})\n except OSError: pass\n"
                                " else: raise AssertionError('host socket connected')\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_detached_descendant_loses_authority_after_owner_exit(self) -> None:
        result = self.probe("import os,time\nfrom pathlib import Path\n"
                            "if os.fork()==0:\n"
                            " os.setsid(); os.close(1); os.close(2); Path('src/ready').touch()\n"
                            " while not Path('src/go').exists(): time.sleep(0.01)\n"
                            " Path('src/after-exit').write_text('late effect'); os._exit(0)\n"
                            "else:\n while not Path('src/ready').exists(): time.sleep(0.01)\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.sandbox.workspace / "src/ready").exists())
        (self.sandbox.workspace / "src/go").touch()
        time.sleep(0.6)
        self.assertFalse((self.sandbox.workspace / "src/after-exit").exists())

    def test_timeout_stops_detached_descendants(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired):
            self.probe("import os,time\nfrom pathlib import Path\n"
                       "if os.fork()==0:\n"
                       " os.setsid(); os.close(1); os.close(2); Path('src/ready').touch()\n"
                       " while not Path('src/go').exists(): time.sleep(0.01)\n"
                       " Path('src/after-timeout').write_text('late effect'); os._exit(0)\n"
                       "time.sleep(300)\n", timeout=self.probe_timeout / 2)
        self.assertTrue((self.sandbox.workspace / "src/ready").exists())
        (self.sandbox.workspace / "src/go").touch()
        time.sleep(1)
        self.assertFalse((self.sandbox.workspace / "src/after-timeout").exists())

    def test_launcher_death_stops_detached_descendants(self) -> None:
        script = ("import os,time\nfrom pathlib import Path\n"
                  "if os.fork()==0:\n"
                  " os.setsid(); os.close(1); os.close(2); Path('src/ready').touch()\n"
                  " while not Path('src/go').exists(): time.sleep(0.01)\n"
                  " Path('src/after-crash').write_text('late effect'); os._exit(0)\n"
                  "time.sleep(30)\n")
        driver = (f"import sys; sys.path.insert(0,{str(Path(__file__).parents[2])!r}); "
                  "from pathlib import Path; from planner.native.linux_sandbox import LinuxSandbox; "
                  "from planner.policy import Policy; from planner.records import Grant; "
                  f"p=Policy(Path({str(self.project)!r}),read=('**',),write=('src/**',),"
                  "deny_read=('**/.env*','blocked/**'),deny_write=('tests/**',),tools=('shell',)); "
                  f"s=LinuxSandbox(p,Grant('task','worker',p.read,p.write,p.tools),Path({str(self.sandbox.output)!r})); "
                  f"s.run(['/usr/bin/python3','-I','-c',{script!r}],timeout={self.probe_timeout!r})")
        with subprocess.Popen([sys.executable, "-I", "-c", driver], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as owner:
            deadline = time.monotonic() + self.probe_timeout
            while not (self.sandbox.workspace / "src/ready").exists():
                if owner.poll() is not None or time.monotonic() > deadline:
                    owner.kill()
                    self.fail("Worker did not reach crash-test checkpoint")
                time.sleep(0.01)
            owner.kill()
            owner.wait(timeout=3)
        time.sleep(1)
        (self.sandbox.workspace / "src/go").touch()
        time.sleep(0.5)
        self.assertFalse((self.sandbox.workspace / "src/after-crash").exists())


if __name__ == "__main__":
    unittest.main()
