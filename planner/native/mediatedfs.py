"""Evaluated FUSE candidate. Python owns file operations and shared-policy checks.

The private backing tree and bridge descriptor must stay outside worker access.
This is a candidate, not acceptance of the complete backend contract.
"""

from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import replace
import errno
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

from planner.policy import Policy
from planner.records import Action, Grant, Operation

_REQUEST = struct.Struct("=IIIIQQ4096s4096s")
_RESPONSE = struct.Struct("=iI")


class MediatedWorkspace:
    """One worker's single-threaded filesystem; mount only ``mount`` in its jail."""

    def __init__(self, policy: Policy, grant: Grant, output: Path, *, helper: Path,
                 check_authority: Callable[[], object] | None = None, timeout: float = 10,
                 exceptions: Sequence[tuple[str, str, str, bool]] = ()) -> None:
        """``exceptions`` are approvals reserved for this attempt, as
        (operation, relative path, lifetime, sensitive). A once-only approval
        permits exactly one open of that file; a later open is refused."""
        policy.validate_grant(grant)
        if "shell" not in grant.tools:
            raise ValueError("This candidate requires an explicit shell grant; file-only workers are not accepted")
        self.original_policy, self.grant = policy, grant
        self.output, self.helper = output.resolve(), helper.resolve(strict=True)
        if self.output.is_relative_to(policy.root) or policy.root.is_relative_to(self.output):
            raise ValueError("Mediator output must be separate from project")
        if policy.external_inputs:
            raise ValueError("External-input operation binding is not implemented by this candidate")
        self.backing, self.mount = self.output / "backing", self.output / "mount"
        self.check_authority, self.timeout = check_authority, timeout
        self.exceptions = {(operation, name): {"lifetime": lifetime, "sensitive": sensitive, "state": "unused"}
                           for operation, name, lifetime, sensitive in exceptions}
        if any(operation not in ("read", "write") or lifetime not in ("once", "task")
               for operation, _, lifetime, _ in exceptions):
            raise ValueError("Unsupported exception binding")
        # Set when an approved sensitive read is served; derived output is sensitive.
        self.sensitive_exposure = threading.Event()
        self.handles: dict[int, tuple[int, str, int, frozenset[str]]] = {}
        self._next_handle = 1
        self._process: subprocess.Popen[bytes] | None = None
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._mounted = False
        # Refusals observed by the serving thread; the runner records them after
        # cleanup. Bounded so a looping worker cannot exhaust supervisor memory.
        self.denials: list[tuple[str, str]] = []
        self.denials_dropped = 0

    def __enter__(self) -> "MediatedWorkspace":
        try:
            return self._start()
        except BaseException:
            self.close()
            raise

    def _start(self) -> "MediatedWorkspace":
        self.output.mkdir(mode=0o700)
        # Refuse aliases before copying: copying a hardlink under a permitted
        # name must not erase the fact that it aliases a protected input.
        for path in self.original_policy.root.rglob("*"):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError("Alias/special-file support is not validated")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise ValueError("Hardlinked input requires explicit handling")
        shutil.copytree(self.original_policy.root, self.backing)
        self.mount.mkdir()
        self.policy = replace(self.original_policy, root=self.backing)
        owner, bridge = socket.socketpair()
        self._socket = owner
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        try:
            self._process = subprocess.Popen(
                [str(self.helper), str(bridge.fileno()), str(self.mount), "-f", "-s", "-o",
                 "entry_timeout=0,attr_timeout=0,negative_timeout=0"],
                pass_fds=(bridge.fileno(),), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True)
        finally:
            bridge.close()
        deadline = time.monotonic() + self.timeout
        while not self._is_mounted():
            if self._process.poll() is not None or time.monotonic() > deadline:
                self.close()
                raise RuntimeError("Filesystem mediator did not mount")
            time.sleep(0.01)
        self._mounted = True
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        # Run all cleanup even if unmount fails; propagate that failure rather
        # than claiming an unresolved mount was cleaned up.
        with ExitStack() as cleanup:
            cleanup.callback(self._close_handles)
            cleanup.callback(self._join_thread)
            cleanup.callback(self._close_socket)
            cleanup.callback(self._stop_process)
            if self._is_mounted():
                subprocess.run(["fusermount3", "-u", str(self.mount)], check=True,
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=self.timeout)
                self._mounted = False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def mounted(self) -> bool:
        return self.mount.exists() and self._is_mounted()

    def _is_mounted(self) -> bool:
        if sys.platform != "linux":
            return os.path.ismount(self.mount)
        # A disconnected FUSE mount can fail stat(), while libfuse may also
        # unmount itself on SIGTERM. Consult the kernel table rather than a
        # stale flag or a successful filesystem lookup.
        name = str(self.mount).replace("\\", r"\134").replace(" ", r"\040")
        name = name.replace("\t", r"\011").replace("\n", r"\012")
        with open("/proc/self/mountinfo", encoding="utf-8") as mounts:
            return any(line.split()[4] == name for line in mounts)

    def _stop_process(self) -> None:
        if self._process:
            if self._process.poll() is None:
                self._process.terminate()
            try:
                self._process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=self.timeout)

    def _close_socket(self) -> None:
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()

    def _join_thread(self) -> None:
        if self._thread:
            self._thread.join(self.timeout)
            if self._thread.is_alive():
                raise RuntimeError("Filesystem policy thread did not stop")

    def _close_handles(self) -> None:
        for fd, _, _, _ in tuple(self.handles.values()):
            os.close(fd)
        self.handles.clear()

    def _receive(self, size: int) -> bytes:
        assert self._socket is not None
        data = bytearray()
        while len(data) < size:
            part = self._socket.recv(size - len(data))
            if not part:
                raise EOFError
            data.extend(part)
        return bytes(data)

    def _serve(self) -> None:
        assert self._socket is not None
        try:
            while True:
                op, flags, mode, count, handle, offset, raw, other = _REQUEST.unpack(self._receive(_REQUEST.size))
                if count > 1024 * 1024:
                    raise ValueError("Oversized filesystem request")
                path = os.fsdecode(raw.split(b"\0")[0]).lstrip("/")
                target = os.fsdecode(other.split(b"\0")[0]).lstrip("/")
                payload = self._receive(count) if op == 5 else b""
                try:
                    if self.check_authority:
                        self.check_authority()
                    data = self._operation(op, path, target, flags, mode, count, handle, offset, payload)
                    if len(data) > 1024 * 1024:
                        raise OSError(errno.E2BIG, "Filesystem response too large")
                    response = _RESPONSE.pack(0, len(data)) + data
                except OSError as error:
                    response = _RESPONSE.pack(error.errno or errno.EIO, 0)
                except (ValueError, RuntimeError, KeyError, OverflowError, struct.error):
                    response = _RESPONSE.pack(errno.EACCES, 0)
                self._socket.sendall(response)
        except (EOFError, OSError, ValueError):
            return  # A dead mediator leaves failed filesystem requests, not raw access.

    def _path(self, name: str) -> Path:
        if any(part == ".." for part in PurePosixPath(name).parts):
            raise PermissionError(errno.EACCES, "Traversal denied")
        path = self.backing / name
        if not path.resolve().is_relative_to(self.backing):
            raise PermissionError(errno.EACCES, "Outside backing tree")
        return path

    def _allowed(self, operation: Operation, name: str) -> bool:
        return self.policy.decide(Action(self.grant.task_id, self.grant.worker_id,
                                        operation, name, "shell"), self.grant).allowed

    def _excepted(self, operation: Operation, name: str) -> bool:
        entry = self.exceptions.get((operation, name))
        if entry is None or (entry["lifetime"] == "once" and entry["state"] != "unused"):
            return False
        entry["state"] = "open"
        if operation == "read" and entry["sensitive"]:
            self.sensitive_exposure.set()
        return True

    def _require(self, operation: Operation, name: str) -> None:
        if not self._allowed(operation, name):
            if len(self.denials) < 1000:
                self.denials.append((operation, name))
            else:
                self.denials_dropped += 1
            raise PermissionError(errno.EACCES, "Worker operation denied")

    def _operation(self, op: int, name: str, other: str, flags: int, mode: int,
                   count: int, handle: int, offset: int, payload: bytes) -> bytes:
        path = self._path(name)
        if op == 1:
            info = path.lstat()
            return struct.pack("=10Q", info.st_mode, info.st_ino, info.st_nlink, info.st_uid, info.st_gid,
                               info.st_size, info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns, info.st_blocks)
        if op == 2:
            return b"".join(os.fsencode(child.name) + b"\0" for child in path.iterdir())
        if op == 3:
            access = flags & os.O_ACCMODE
            needed: list[Operation] = []
            if access != os.O_WRONLY:
                needed.append("read")
            if access != os.O_RDONLY or flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                needed.append("write")
            denied = [operation for operation in needed if not self._allowed(operation, name)]
            for operation in denied:
                if self.exceptions.get((operation, name)) is None:
                    self._require(operation, name)
            if not all(self._excepted(operation, name) for operation in denied):
                self._require(denied[0], name)
            descriptor = os.open(path, flags | os.O_NOFOLLOW, mode & 0o777)
            self._next_handle += 1
            self.handles[self._next_handle] = (descriptor, name, access, frozenset(denied))
            return struct.pack("=Q", self._next_handle)
        if op == 11 and handle in self.handles:
            fd, bound, access, excepted = self.handles[handle]
            if bound != name or access == os.O_RDONLY:
                raise OSError(errno.EBADF, "Handle does not permit truncation")
            if "write" not in excepted:
                self._require("write", bound)
            os.ftruncate(fd, offset)
            return b""
        if op in (4, 5, 6, 12):
            fd, bound, access, excepted = self.handles[handle]
            if op == 6:
                os.close(fd)
                del self.handles[handle]
                return b""
            current = self._path(bound).stat()
            original = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                raise OSError(errno.ESTALE, "Opened file was removed or replaced")
            operation: Operation = "read" if op == 4 else "write"
            if operation not in excepted:
                self._require(operation, bound)
            if op == 4:
                if access == os.O_WRONLY:
                    raise OSError(errno.EBADF, "Write-only handle")
                return os.pread(fd, count, offset)
            if op == 12:
                os.fsync(fd)
                return b""
            if access == os.O_RDONLY:
                raise OSError(errno.EBADF, "Read-only handle")
            return struct.pack("=I", os.pwrite(fd, payload, offset))
        if op == 10:
            if flags:
                raise OSError(errno.EOPNOTSUPP, "Rename flags not validated")
            target = self._path(other)
            descendants = [path, *path.rglob("*")] if path.is_dir() else [path]
            for item in descendants:
                before = item.relative_to(self.backing).as_posix()
                after = (target / item.relative_to(path)).relative_to(self.backing).as_posix()
                self._require("write", before)
                self._require("write", after)
                if not self._allowed("read", before) and self._allowed("read", after):
                    raise PermissionError(errno.EACCES, "Move would disclose protected input")
            os.rename(path, target)
            for key, (fd, bound, access, excepted) in tuple(self.handles.items()):
                if bound == name or bound.startswith(name + "/"):
                    self.handles[key] = (fd, other + bound[len(name):], access, excepted)
            return b""
        self._require("write", name)
        if op == 7:
            path.mkdir(mode=mode & 0o777)
        elif op == 8:
            path.unlink()
        elif op == 9:
            path.rmdir()
        elif op == 11:
            os.truncate(path, offset)
        else:
            raise OSError(errno.ENOSYS, "Unsupported filesystem operation")
        return b""
