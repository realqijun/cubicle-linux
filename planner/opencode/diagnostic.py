"""Exercise the pinned binary with a fixed local provider and dummy files only."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import subprocess
import tempfile
from threading import Thread
from typing import Any, Iterator

from planner.connector import BridgeConfig, Connector, ConnectorConfig, PINNED_VERSION, ProviderConfig
from planner.broker_server import BrokerServer
from planner.mediation import WorkerBroker
from planner.policy import Policy
from planner.provenance import Provenance
from planner.records import Grant
from planner.supervisor import Supervisor

CASES = ("permitted_read", "permitted_write", "denied_write", "denied_provider")


def run_fixture(case: str, *, executable: str = "opencode", timeout: float = 30) -> dict[str, Any]:
    if case not in CASES:
        raise ValueError("Unknown fixed diagnostic case")
    routes: list[str] = []
    requests: list[dict[str, Any]] = []
    issued = False
    denied = False
    hello_admitted = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            nonlocal issued, denied
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/admit":
                requests.append(body)
                if body.get("tools") and not issued:
                    issued = True
                    tool = "read" if case == "permitted_read" else "write"
                    arguments = {"path": "README.md"} if tool == "read" else {
                        "path": "protected.txt", "content": "DUMMY_UPDATE"}
                    delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_fixture",
                             "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"role": "assistant", "content": "FIXTURE_OK"}, "stop"
                chunks = [{"id": "chatcmpl_fixture", "object": "chat.completion.chunk", "created": 0,
                           "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {"id": "chatcmpl_fixture", "object": "chat.completion.chunk", "created": 0,
                           "model": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}]
                response = ("".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) +
                            "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    with tempfile.TemporaryDirectory(prefix="planner-opencode-fixture-") as directory:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        (project / "README.md").write_text("DUMMY_PUBLIC_FIXTURE")
        protected = project / "protected.txt"
        protected.write_text("DUMMY_PROTECTED_FIXTURE")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        state = root / "control.sqlite"
        control = Supervisor(state)
        try:
            policy = Policy(project, read=("**",), write=("protected.txt",) if case == "permitted_write" else ("outputs/**",))
            task_id = control.propose(policy)
            control.approve_task(task_id)
            control.approve_worker(Grant(task_id, "fixture", policy.read, policy.write))
        finally:
            control.close()

        class ObservedBroker(WorkerBroker):
            def handle_request(self, envelope: dict[str, Any]) -> dict[str, Any]:
                nonlocal denied, hello_admitted
                routes.append(envelope.get("route", "invalid"))
                reply = super().handle_request(envelope)
                if envelope.get("route") == "hello":
                    hello_admitted = bool(reply["allowed"])
                denied = denied or not reply["allowed"]
                return reply

        @contextmanager
        def factory() -> Iterator[WorkerBroker]:
            supervisor = Supervisor(state)
            store = Provenance(state.with_suffix(".memory.sqlite"))
            broker = ObservedBroker(supervisor, store, task_id, "fixture",
                                   provider_origins=() if case == "denied_provider" else (origin,))
            try:
                yield broker
            finally:
                broker.close()
                store.close()
                supervisor.close()

        connector = Connector(ConnectorConfig("fixture/model", executable=executable,
                                             provider=ProviderConfig(origin + "/v1")))
        try:
            with BrokerServer(factory) as bridge:
                launch = connector.prepare(root, "Use the fixture tool, then say FIXTURE_OK.", project=project,
                                           bridge=BridgeConfig(bridge.endpoint, bridge.token))
                config = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])
                # Fixture-only positive controls; the supervisor hook still vetoes.
                config["permissions"] = [{"action": "*", "resource": "*", "effect": "allow"}]
                launch.env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
                launch.env.update(OPENCODE_FILEWATCHER_DISABLE="1", OPENCODE_DISABLE_FFF="1")
                result = subprocess.run(launch.argv, cwd=launch.cwd, env=launch.env,
                                        capture_output=True, text=True, timeout=timeout, close_fds=True)
            public = "DUMMY_PUBLIC_FIXTURE" in json.dumps(requests)
            changed = protected.read_text() != "DUMMY_PROTECTED_FIXTURE"
            completed = '"FIXTURE_OK"' in result.stdout
            mediated = hello_admitted and "prompt" in routes and routes.count("provider.http.request") == len(requests)
            passed = mediated and ((public and completed and not denied) if case == "permitted_read" else
                      (changed and completed and not denied) if case == "permitted_write" else
                      (issued and denied and not changed and completed) if case == "denied_write" else
                      (denied and not requests))
            return {"case": case, "passed": passed, "exit_code": result.returncode,
                    "routes": routes, "provider_requests": len(requests),
                    "public_fixture_observed": public, "write_effect": changed,
                    "denial_observed": denied, "bridge_registered": hello_admitted,
                    "native_completion": completed, "all_provider_requests_admitted": mediated}
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable", default="opencode")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    assets = {name: Path(__file__).with_name(name) for name in ("bridge.mjs", "diagnostic.py")}
    assets.update({name: Path(__file__).parents[1] / name for name in (
        "connector.py", "mediation.py", "broker_server.py", "context.py", "supervisor.py", "provenance.py")})
    sources = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in assets.items()}
    results = [run_fixture(case, executable=args.executable, timeout=args.timeout) for case in CASES]
    stable = sources == {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in assets.items()}
    attempt = {"at": datetime.now(timezone.utc).isoformat(), "release": PINNED_VERSION,
               "host": platform.platform(), "sources": sources, "sources_stable": stable, "results": results,
               "scope": "Pinned binary, actual supervisor/broker, fixed loopback dummy provider; not OS confinement or full-route acceptance."}
    evidence: dict[str, Any] = {"schema": 1, "attempts": []}
    if args.output.exists():
        evidence = json.loads(args.output.read_text())
    evidence["attempts"].append(attempt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"passed": sum(item["passed"] for item in results), "cases": len(results)}))
    raise SystemExit(0 if stable and all(item["passed"] for item in results) else 1)


if __name__ == "__main__":
    main()
