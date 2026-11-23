"""Explicit OpenCode tool-hook mode; no OS confinement or context provenance.

Only direct read/write/edit tools are exposed. The full connected launcher and
its acceptance gates remain separate. Hook checks cannot prevent filesystem
races or effects through unmediated code in the harness itself.
"""

from contextlib import contextmanager
import json
import math
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterator

from .broker_server import BrokerServer
from .connector import BridgeConfig, Connector, ConnectorConfig, PINNED_VERSION
from .mediation import MediationDenied, WorkerBroker
from .private_server import PrivateServer
from .provenance import Provenance
from .supervisor import Supervisor

HOOKS = ("session.prompt", "tool.execute.before", "tool.execute.after",
         "permission.evaluate", "shell.create.before")


class PreToolBroker(WorkerBroker):
    """Reuse authorization/ticket checks; accept no model or output contents."""

    required_hooks = HOOKS

    def __init__(self, supervisor: Supervisor, store: Provenance, task_id: str, worker_id: str,
                 *, ready: threading.Event | None = None) -> None:
        super().__init__(supervisor, store, task_id, worker_id)
        self._ready = ready

    def handle_request(self, envelope: dict[str, Any]) -> dict[str, Any]:
        reply = super().handle_request(envelope)
        if self._ready is not None and self.hooks_ready:
            self._ready.set()
        return reply

    def close(self) -> None:
        super().close()
        if self._ready is not None:
            self._ready.clear()

    def before_tool(self, call_id: str, tool: str, arguments: dict[str, Any]) -> None:
        if tool not in ("read", "write", "edit"):
            raise MediationDenied("hook_only_unsupported_tool")
        resource = arguments.get("path")
        if not isinstance(resource, str) or not resource:
            raise MediationDenied("missing_resource")
        policy = self.supervisor.policy(self.task_id)
        path = Path(resource) if Path(resource).is_absolute() else policy.root / resource
        if path.is_dir():
            raise MediationDenied("hook_only_directory_access")
        super().before_tool(call_id, tool, arguments)

    def _route(self, route: str, payload: dict[str, Any]) -> str | None:
        if route == "prompt":
            return None  # Prompt bytes are never sent by the hook-only adapter.
        if route == "tool.after":
            ticket = self._tools.get(payload.get("call_id", ""))
            if ticket is None or payload.get("tool") != ticket.name:
                raise MediationDenied("substituted_tool_result")
            del self._tools[payload["call_id"]]
            return None  # Completion releases the ticket without admitting output.
        if route not in ("tool.before", "permission.evaluate"):
            raise MediationDenied("hook_only_unsupported_route")
        return super()._route(route, payload)


def run_hook(state: Path, task: str, worker: str, config: ConnectorConfig, *,
             prompt: str | None = None, credential: str | None = None,
             run_seconds: float = 3600, denial_limit: int = 3) -> dict[str, Any]:
    """Verify a no-task server, then submit a prompt or open its terminal UI.

    Edits affect the approved original project directly. Existing task approval
    is required; this entry point never approves or widens a grant.
    """
    if not math.isfinite(run_seconds) or run_seconds <= 0:
        raise ValueError("Run lifetime must be positive and finite")
    ready = threading.Event()
    with tempfile.TemporaryDirectory(prefix="planner-hook-") as directory:
        root = Path(directory)
        with_control = Supervisor(state, denial_limit=denial_limit)
        try:
            with_control.check_worker(task, worker)
            project = with_control.policy(task).root
            if root.resolve().is_relative_to(project):
                raise ValueError("Hook runtime storage must be outside the project")
            connector = Connector(config)
            # Probe with private configuration; never inherit the user's sessions.
            probe = connector.prepare(root, "", project=project,
                                      credentials=_credentials(config, credential))
            version = subprocess.run([probe.argv[0], "--version"], env=probe.env, cwd=root,
                                     capture_output=True, text=True, timeout=config.timeout_seconds)
            if version.returncode or version.stdout.strip() != f"opencode v{PINNED_VERSION}":
                raise RuntimeError(f"Hook mode requires OpenCode {PINNED_VERSION}")

            @contextmanager
            def factory() -> Iterator[WorkerBroker]:
                control = Supervisor(state, denial_limit=denial_limit)
                store = Provenance(root / "hook-metadata.sqlite")
                try:
                    broker = PreToolBroker(control, store, task, worker, ready=ready)
                    try:
                        yield broker
                    finally:
                        broker.close()
                finally:
                    store.close()
                    control.close()

            with BrokerServer(factory) as transport:
                password = secrets.token_urlsafe()
                launch = connector.prepare_server(
                    root, project=project, bridge=BridgeConfig(transport.endpoint, transport.token),
                    server_password=password, credentials=_credentials(config, credential))
                settings = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])
                settings["plugins"] = [str(Path(__file__).parent / "opencode" / "pretool")]
                launch.env["OPENCODE_CONFIG_CONTENT"] = json.dumps(settings)

                def spawn() -> subprocess.Popen[str]:
                    return subprocess.Popen(launch.argv, cwd=project, env=launch.env,
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, close_fds=True)

                with PrivateServer(spawn, password=password, hooks_ready=ready.is_set,
                                   timeout_seconds=config.timeout_seconds) as server:
                    server.initialize(str(project))
                    session = server.submit(prompt, config.model) if prompt is not None else None
                    client = None
                    try:
                        if prompt is None:
                            environment = {k: v for k, v in launch.env.items()
                                           if not k.startswith("PLANNER_")}
                            client = subprocess.Popen([launch.argv[0], "--server", server.url, str(project)],
                                                      cwd=project, env=environment, close_fds=True)
                        deadline = time.monotonic() + run_seconds
                        while True:
                            with_control.check_worker(task, worker)
                            if not transport.healthy:
                                raise RuntimeError("Hook supervisor connection lost")
                            if client is not None and client.poll() is not None:
                                return _result("client_exited", client.returncode == 0)
                            if session is not None and not server.active(session):
                                return _result("session_idle", True)
                            if time.monotonic() >= deadline:
                                return _result("timeout", False)
                            time.sleep(0.05)
                    finally:
                        if client is not None and client.poll() is None:
                            client.terminate()
                            try:
                                client.wait(timeout=config.timeout_seconds)
                            except subprocess.TimeoutExpired:
                                client.kill()
                                client.wait(timeout=config.timeout_seconds)
        except KeyboardInterrupt:
            return _result("interrupted", False)
        finally:
            with_control.close()


def _credentials(config: ConnectorConfig, credential: str | None) -> dict[str, str]:
    name = config.provider.api_key_env if config.provider else None
    return {name: credential or ""} if name else {}


def _result(status: str, ready: bool) -> dict[str, Any]:
    return {"mode": "pretool", "status": status, "ready": ready,
            "os_confinement": False, "context_provenance": False,
            "tools": ["read", "write", "edit"], "edits": "direct"}
