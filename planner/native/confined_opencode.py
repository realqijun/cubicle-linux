"""Linux OpenCode integration candidate; does not clear runtime acceptance gates.

Only the mediated mount enters the jail. Host HTTP services are available through
fixed Unix relays, with a separate short-lived credential for provider transport.
The caller owns the broker and filesystem and must finish worker cleanup before
inspecting/applying backing-tree changes.
"""

from contextlib import AbstractContextManager, ExitStack
from dataclasses import replace
import ctypes
import errno
import hashlib
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import platform
import secrets
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import urlsplit

from planner.connector import BridgeConfig, Connector
from planner.native.confined_transport import ProviderRelay, UnixRelay
from planner.native.mediatedfs import MediatedWorkspace
from planner.private_server import PrivateServer


class _ControlServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    block_on_close = False


class Authority(Protocol):
    def check(self) -> object: ...


def _candidate_filter(descriptor: int) -> None:
    """A separate candidate contract; the offline runner's filter is untouched."""
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    context = library.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("Cannot initialize candidate seccomp")
    try:
        for syscall in ("symlink", "symlinkat", "link", "linkat", "ptrace", "process_vm_readv",
                        "process_vm_writev", "io_uring_setup", "io_uring_enter", "io_uring_register",
                        "pidfd_getfd", "open_by_handle_at", "name_to_handle_at", "kcmp", "bpf",
                        "perf_event_open"):
            number = library.seccomp_syscall_resolve_name(syscall.encode())
            if number >= 0 and library.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0):
                raise RuntimeError("Cannot install candidate syscall restriction")
        if library.seccomp_export_bpf(context, descriptor):
            raise RuntimeError("Cannot export candidate seccomp")
        import os
        os.lseek(descriptor, 0, os.SEEK_SET)
    finally:
        library.seccomp_release(context)


def profile_name() -> str:
    digest = hashlib.sha256()
    for name in ("confined_opencode.py", "confined_bootstrap.py", "confined_transport.py", "mediatedfs.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return "planner-connected-" + digest.hexdigest()[:24]


def profile_text() -> str:
    name = profile_name()
    return "\n".join(("abi <abi/4.0>,", f"profile {name} flags=(attach_disconnected,mediate_deleted) {{",
        "  /usr/** mrix,", "  /runtime/** mrix,", "  /workspace/ rw,", "  /workspace/** rwix,",
        "  /state/ rw,", "  /state/** rwk,", "  /tmp/ rw,", "  /tmp/** rwk,",
        "  /transport/ r,", "  /transport/*.sock rw,", "  /proc/ r,", "  /proc/** r,",
        "  deny /proc/**/environ r,", "  deny /proc/**/mem rw,", "  deny /proc/**/pagemap r,",
        "  deny /proc/**/map_files/** r,", "  deny /proc/**/attr/** w,",
        "  /dev/null rw,", "  /dev/urandom r,", "  /dev/random r,",
        "  network inet stream,", "  network unix stream,",
        f"  signal (send, receive) peer={name},", "  signal (receive) peer=unconfined,", "}", ""))


class ConfinedOpenCode:
    """Own one no-task server and PID namespace; dispatch stays explicitly gated.

    ``authority_factory`` is called on the monitoring thread and returns a context
    manager exposing a no-argument ``check()`` method. SQLite owners therefore
    never cross threads. Loss of authority kills the namespace, not just a tool.
    """

    def __init__(self, filesystem: MediatedWorkspace, connector: Connector,
                 runtime_root: Path, *, bridge: BridgeConfig,
                 hooks_ready: Callable[[], bool], credentials: Mapping[str, str] | None = None,
                 authority_factory: Callable[[], AbstractContextManager[Authority]] | None = None) -> None:
        self.filesystem, self.connector, self.runtime_root = filesystem, connector, runtime_root
        self.bridge, self.hooks_ready = bridge, hooks_ready
        self.credentials, self.authority_factory = credentials or {}, authority_factory
        self.process: subprocess.Popen[str] | None = None
        self.server: PrivateServer | None = None
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._stack = ExitStack()
        self.authority_lost = False
        self.cleaned = False
        self._channel: socket.socket | None = None

    def __enter__(self) -> "ConfinedOpenCode":
        if platform.system() != "Linux" or not shutil.which("bwrap"):
            raise RuntimeError("Confined OpenCode requires actual Linux bubblewrap")
        provider = self.connector.config.provider
        if provider is None:
            raise ValueError("Confined transport requires an explicit provider")
        expected = {provider.api_key_env} if provider.api_key_env else set()
        if set(self.credentials) != expected:
            raise ValueError("Exactly the configured provider credential is required")
        try:
            transport = Path(self._stack.enter_context(tempfile.TemporaryDirectory(
                prefix="transport-", dir=self.runtime_root)))
            transport.chmod(0o700)
            parsed = urlsplit(self.bridge.endpoint)
            assert parsed.port is not None and parsed.hostname is not None
            self._stack.enter_context(UnixRelay(transport / "broker.sock", (parsed.hostname, parsed.port)))
            self.provider_relay = self._stack.enter_context(ProviderRelay(
                transport / "provider.sock", provider.base_url,
                api_key=self.credentials.get(provider.api_key_env) if provider.api_key_env else None,
                limit=self.bridge.max_body_bytes, timeout=self.connector.config.timeout_seconds))
            inside_provider = replace(provider,
                base_url="http://127.0.0.1:18182" + urlsplit(provider.base_url).path,
                api_key_env="WORKER_PROVIDER_TOKEN")
            connector = Connector(replace(self.connector.config, provider=inside_provider))
            password = secrets.token_urlsafe(32)
            self.launch = connector.prepare_server(self.runtime_root, project=self.filesystem.mount,
                bridge=replace(self.bridge, endpoint="http://127.0.0.1:18181/admit"),
                server_password=password, credentials={"WORKER_PROVIDER_TOKEN": self.provider_relay.token})
            owner = self
            channel_listener = socket.socket(socket.AF_UNIX)
            channel_listener.bind(str(transport / "control.sock"))
            channel_listener.listen(1)
            channel_listener.settimeout(self.connector.config.timeout_seconds)
            self._stack.callback(channel_listener.close)
            channel_ready, channel_lock = threading.Event(), threading.Lock()
            def accept_channel() -> None:
                try:
                    owner._channel, _ = channel_listener.accept()
                    owner._channel.settimeout(owner.connector.config.timeout_seconds)
                except OSError:
                    pass
                finally:
                    channel_listener.close()
                    channel_ready.set()
            accept_thread = threading.Thread(target=accept_channel, daemon=True)
            accept_thread.start()
            self._stack.callback(accept_thread.join, self.connector.config.timeout_seconds)
            class Control(BaseHTTPRequestHandler):
                def setup(self) -> None:
                    self.request.settimeout(owner.connector.config.timeout_seconds)
                    super().setup()

                def log_message(self, *args: object) -> None:
                    pass

                def do_GET(self) -> None:
                    self.forward()

                def do_POST(self) -> None:
                    self.forward()

                def forward(self) -> None:
                    try:
                        if not channel_ready.wait(owner.connector.config.timeout_seconds) or owner._channel is None:
                            raise OSError("Control unavailable")
                        length = int(self.headers.get("Content-Length", "0"))
                        if not 0 <= length <= owner.bridge.max_body_bytes or self.headers.get("Transfer-Encoding"):
                            raise ValueError("Invalid private API body")
                        request = {"method": self.command, "path": self.path,
                                   "body": self.rfile.read(length).decode("utf-8"),
                                   "headers": {"Authorization": self.headers.get("Authorization", ""),
                                               "Content-Type": "application/json"}}
                        with channel_lock:
                            owner._channel.sendall(json.dumps(request).encode() + b"\n")
                            with owner._channel.makefile("rb") as stream:
                                result = json.loads(stream.readline(8 * 1024 * 1024))
                        raw = result["body"].encode("utf-8")
                        self.send_response(result["status"])
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                    except (OSError, ValueError, KeyError):
                        self.send_error(502)
            control = _ControlServer(("127.0.0.1", 0), Control)
            self._stack.callback(control.server_close)
            control_thread = threading.Thread(target=control.serve_forever, daemon=True)
            control_thread.start()
            self._stack.callback(control_thread.join, 10)
            self._stack.callback(control.shutdown)
            outer_url = f"http://127.0.0.1:{control.server_address[1]}"
            self.server = self._stack.enter_context(PrivateServer(
                lambda: owner._spawn(transport, outer_url), password=password,
                hooks_ready=self.hooks_ready, timeout_seconds=self.connector.config.timeout_seconds))
            if self.authority_factory is not None:
                self._monitor = threading.Thread(target=self._watch, daemon=True)
                self._monitor.start()
            self.server.initialize("/workspace")
            return self
        except BaseException:
            self.close()
            raise

    def _watch(self) -> None:
        try:
            factory = self.authority_factory
            assert factory is not None
            with factory() as authority:
                while not self._stop.is_set():
                    authority.check()
                    self._stop.wait(0.05)
        except BaseException:
            self.authority_lost = True
            if self.process is not None and self.process.poll() is None:
                self.process.kill()

    def _spawn(self, transport: Path, outer_url: str) -> subprocess.Popen[str]:
        spec = self.launch
        environment = dict(spec.env)
        for name in ("HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR"):
            environment[name] = "/state/" + name.lower()
        environment.update(OPENCODE_TEST_HOME=environment["HOME"],
                           OPENCODE_CONFIG_DIR=environment["XDG_CONFIG_HOME"])
        content = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
        content["plugins"] = ["/runtime/plugin"]
        # Native interactive approvals are replaced by the authenticated hook,
        # which still vetoes every tracked operation under the approved grant.
        content["permissions"] = [{"action": "*", "resource": "*", "effect": "allow"}]
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(content)
        environment.update(OPENCODE_FILEWATCHER_DISABLE="1", OPENCODE_DISABLE_FFF="1")
        environment["PATH"] = "/usr/bin:/bin"
        seccomp = tempfile.TemporaryFile()
        try:
            _candidate_filter(seccomp.fileno())
            command = ["bwrap", "--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-net",
                "--unshare-uts", "--unshare-cgroup-try", "--disable-userns", "--die-with-parent", "--new-session",
                "--cap-drop", "ALL", "--clearenv", "--setenv", "PATH", "/usr/bin:/bin",
                "--setenv", "LANG", "C.UTF-8", "--ro-bind", "/usr", "/usr",
                "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
                "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/state",
                "--bind", str(self.filesystem.mount), "/workspace",
                "--ro-bind", str(transport), "/transport",
                "--ro-bind", spec.argv[0], "/runtime/opencode",
                "--ro-bind", str(Path(__file__).parents[1] / "opencode"), "/runtime/plugin",
                "--ro-bind", str(Path(__file__).with_name("confined_bootstrap.py")), "/runtime/bootstrap.py"]
            for name in ("home", "xdg_data_home", "xdg_state_home", "xdg_config_home", "xdg_cache_home", "tmpdir"):
                command.extend(("--dir", "/state/" + name))
            bootstrap = ("import os,sys; p=sys.argv[1]; "
                         "s=open('/proc/self/attr/current').read().strip(); "
                         "sys.exit(125) if s != p+' (enforce)' else None; "
                         "os.execv('/usr/bin/python3',['python3','-I','/runtime/bootstrap.py'])")
            command.extend(("--chdir", "/workspace", "--seccomp", str(seccomp.fileno()),
                            "/usr/bin/aa-exec", "-p", profile_name(), "--",
                            "/usr/bin/python3", "-I", "-c", bootstrap, profile_name()))
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, close_fds=True, pass_fds=(seccomp.fileno(),),
                start_new_session=True, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps({"environment": environment,
                "argv": ["/runtime/opencode", *spec.argv[1:]], "outer_url": outer_url}) + "\n")
            self.process.stdin.flush()
            return self.process
        finally:
            seccomp.close()

    def close(self) -> None:
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(10)
            if self._monitor.is_alive():
                raise RuntimeError("Authority monitor did not stop")
        try:
            self._stack.close()
        finally:
            if self._channel is not None:
                self._channel.close()
            # Always terminate the PID namespace owner, even if the stdio lease
            # ended gracefully: detached descendants must not survive completion.
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=10)
            self.cleaned = True

    def __exit__(self, *args: object) -> None:
        self.close()
