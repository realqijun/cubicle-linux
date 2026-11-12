"""Linux private-workspace execution. The live-project Backend stays gated."""

import os
import ctypes
import errno
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence

from planner.policy import Policy, matches
from planner.records import Grant


def _glob(pattern: str) -> str:
    """Translate the reviewed component-glob subset, rejecting profile syntax."""
    if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-*?" for character in pattern):
        raise ValueError("Linux sandbox scopes support only letters, digits, _ . / - * ?")
    parts = pattern.split("/")
    if len(parts) > 1 and parts[-1] == "**":
        return _glob("/".join(parts[:-1])) + "{,/**}"
    return "/workspace/" + "".join(
        "{,**/}" if part == "**" and index < len(parts) - 1 else
        part + ("/" if index < len(parts) - 1 else "")
        for index, part in enumerate(parts))


def _filter(descriptor: int) -> None:
    """Export a native-architecture seccomp filter using the system library."""
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Cannot initialize seccomp")
    try:
        for syscall in ("symlink", "symlinkat", "link", "linkat", "ptrace", "process_vm_readv", "process_vm_writev"):
            number = library.seccomp_syscall_resolve_name(syscall.encode())
            if number >= 0 and library.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise RuntimeError("Cannot restrict link/process syscalls")
        if library.seccomp_export_bpf(context, descriptor) != 0:
            raise RuntimeError("Cannot export seccomp filter")
        os.lseek(descriptor, 0, os.SEEK_SET)
    finally:
        library.seccomp_release(context)


class LinuxSandbox:
    """Copy approved inputs, keeping the source project outside the worker."""

    def __init__(self, policy: Policy, grant: Grant, output: Path) -> None:
        policy.validate_grant(grant)
        if "shell" not in grant.tools:
            raise ValueError("Process execution requires a shell grant")
        self.policy = policy
        self.grant = grant
        self.output = output.resolve()
        if self.output.is_relative_to(policy.root) or policy.root.is_relative_to(self.output):
            raise ValueError("Run output must be outside the source project")
        self.workspace = self.output / "workspace"
        self._prepared = False
        if policy.external_inputs:
            raise ValueError("External inputs are not supported by private-workspace execution")
        # Compile before creating any files, including unsupported syntax checks.
        self.profile_text()

    @property
    def profile_name(self) -> str:
        settings = (self.grant.read, self.grant.write, self.policy.deny_read, self.policy.deny_write)
        version = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        digest = hashlib.sha256(json.dumps((version, settings)).encode()).hexdigest()[:24]
        return "planner-" + digest

    def profile_text(self) -> str:
        """Trusted operator installs this exact profile; workers cannot load it."""
        rules = [f"profile {self.profile_name} flags=(attach_disconnected,mediate_deleted) {{",
                 "  /usr/** rix,", "  /workspace/ r,", "  /workspace/**/ r,",
                 "  /tmp/ r,", "  /tmp/** rw,", "  /dev/null rw,",
                 "  /dev/urandom r,", "  /dev/random r,", "  /proc/*/attr/current r,",
                 f"  signal (send, receive) peer={self.profile_name},",
                 "  signal (receive) peer=unconfined,"]
        for scopes, permission in ((self.grant.read, "r"), (self.grant.write, "w")):
            rules += [f'  "{_glob(scope)}" {permission},' for scope in scopes]
        # Deny writes to read-denied names too: prevents renaming protected
        # inodes to permitted names. This restriction is deliberately stronger.
        for scopes, permission in ((self.policy.deny_read, "rw"), (self.policy.deny_write, "w")):
            for scope in scopes:
                path = _glob(scope)
                rules += [f'  deny "{path}" {permission},', f'  deny "{path}/**" {permission},']
        return "\n".join([*rules, "}", ""])

    def prepare(self) -> None:
        """Create a fresh snapshot. Symlinks, hardlinks and special files are omitted.

        The source must be quiescent during preparation. Directory-relative opens
        and O_NOFOLLOW keep links from redirecting copies outside the input tree.
        No source inode is mounted in the worker's namespace.
        """
        identity = self.policy.root.stat()
        if (identity.st_dev, identity.st_ino) != self.policy.root_identity:
            raise ValueError("Project root changed since policy approval")
        self.output.mkdir(mode=0o700)
        self.workspace.mkdir(mode=0o700)
        (self.output / "profile.apparmor").write_text(self.profile_text())
        for directory, _, names, descriptor in os.fwalk(self.policy.root, follow_symlinks=False):
            relative = Path(directory).relative_to(self.policy.root)
            if self.policy.denied((relative.as_posix(),), self.policy.deny_read):
                continue
            (self.workspace / relative).mkdir(parents=True, exist_ok=True)
            for name in names:
                resource = (relative / name).as_posix()
                if (self.policy.denied((resource,), self.policy.deny_read)
                        or not any(matches(resource, scope) for scope in self.grant.read)):
                    continue
                if not stat.S_ISREG(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode):
                    continue
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        continue
                    destination = self.workspace / relative / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("xb") as target:
                        shutil.copyfileobj(stream, target)
                    destination.chmod(0o700 if info.st_mode & 0o111 else 0o600)
        self._prepared = True

    def run(self, argv: Sequence[str], *, timeout: float,
            check_authority: Callable[[], object] | None = None,
            poll_interval: float = 0.1) -> subprocess.CompletedProcess[str]:
        """Execute offline with no host-project mounts, credentials or input FDs.

        The system /usr tree is the explicit, read-only runtime. Edits remain in
        output/workspace; this API never merges them into the original project.
        """
        if platform.system() != "Linux":
            raise RuntimeError("Private-workspace execution requires Linux")
        if (not argv or any(not isinstance(part, str) or not part for part in argv)
                or not math.isfinite(timeout) or timeout <= 0
                or not math.isfinite(poll_interval) or poll_interval <= 0):
            raise ValueError("Arguments and a positive timeout are required")
        if not shutil.which("bwrap") or not Path("/usr/bin/aa-exec").is_file():
            raise RuntimeError("Install bubblewrap and apparmor before running")
        if check_authority:
            check_authority()
        if not self._prepared:
            self.prepare()
        # The trusted bootstrap checks actual kernel enforcement, including
        # complain-mode rejection, before executing the supplied command.
        bootstrap = ("import os,sys; p=sys.argv[1]; "
                     "s=open('/proc/self/attr/current').read().strip(); "
                     "sys.exit(125) if s != p+' (enforce)' else None; "
                     "os.execvp(sys.argv[2],sys.argv[2:])")
        with tempfile.TemporaryFile() as seccomp:
            _filter(seccomp.fileno())
            command = ["bwrap", "--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-net",
                       "--unshare-uts", "--unshare-cgroup-try", "--disable-userns", "--die-with-parent", "--new-session",
                       "--cap-drop", "ALL", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
                       "--setenv", "HOME", "/tmp", "--setenv", "LANG", "C.UTF-8",
                       "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                       "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                       "--symlink", "usr/sbin", "/sbin", "--symlink", "usr/lib", "/lib",
                       "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                       "--tmpfs", "/tmp", "--bind", str(self.workspace), "/workspace",
                       "--chdir", "/workspace", "--seccomp", str(seccomp.fileno()),
                       "/usr/bin/aa-exec", "-p", self.profile_name, "--",
                       "/usr/bin/python3", "-I", "-c", bootstrap, self.profile_name, *argv]
            # bwrap is PID 1's owner; killing it tears down the PID namespace,
            # including descendants that detached into separate sessions.
            if check_authority:
                check_authority()
            with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, close_fds=True,
                                  pass_fds=(seccomp.fileno(),), start_new_session=True,
                                  env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}) as process:
                try:
                    deadline = time.monotonic() + timeout
                    while True:
                        if check_authority:
                            check_authority()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(list(argv), timeout)
                        try:
                            stdout, stderr = process.communicate(timeout=min(poll_interval, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                except BaseException:
                    process.kill()
                    process.communicate()
                    raise
                return subprocess.CompletedProcess(list(argv), process.returncode, stdout, stderr)


def main() -> int:
    """Trusted operator entry point; authority comes from the supervisor database."""
    import argparse
    import sys
    from planner.supervisor import Supervisor

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh run directory outside the project")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--poll-interval", type=float, default=0.1)
    parser.add_argument("operation", choices=("profile", "run"))
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    supervisor = Supervisor(args.state)
    try:
        grant = supervisor.check_worker(args.task, args.worker, "shell")
        sandbox = LinuxSandbox(supervisor.policy(args.task), grant, args.output)
        if args.operation == "profile":
            print(sandbox.profile_text(), end="")
            return 0
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        result = sandbox.run(argv, timeout=args.timeout, poll_interval=args.poll_interval,
                             check_authority=lambda: supervisor.check_worker(args.task, args.worker, "shell"))
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        # Do not persist worker output or exception strings in the state database.
        print(f"BLOCKED: {type(error).__name__}", file=sys.stderr)
        return 2
    finally:
        supervisor.close()


if __name__ == "__main__":
    raise SystemExit(main())
