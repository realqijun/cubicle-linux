"""Deterministic loopback model provider that issues fixed tool calls.

It stands in for a real model in the runtime tests and the scripted ``alarm``
demo. Only the model's decisions are scripted: the confined worker, mediation,
supervisor decisions and application are the real ones.
"""

from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any


class ScriptedProvider:
    """OpenAI-compatible SSE server: issues ``steps`` as tool calls, then stops.

    A ``None`` step answers with the final text instead, which ends that run;
    the steps after it serve the next run against the same provider.
    With ``isolate_runs``, a request that starts a new conversation (no tool
    results yet) first skips what is left of the previous run's steps, through
    its ``None``, so a run the supervisor stopped early leaves none to the next.
    ``on_request(index, body)`` runs before each response so tests can change
    host state at an exact point of the conversation.
    """

    def __init__(self, steps: Sequence[tuple[str, dict[str, Any]] | None], *,
                 on_request: Callable[[int, dict[str, Any]], None] | None = None,
                 final: str = "FIXTURE_DONE", isolate_runs: bool = False) -> None:
        self.steps, self.on_request, self.isolate_runs = list(steps), on_request, isolate_runs
        self.bodies: list[dict[str, Any]] = []
        self.raw: list[bytes] = []
        self._issued = 0
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.loads(raw)
                with owner._lock:
                    owner.raw.append(raw)
                    owner.bodies.append(body)
                    index = len(owner.bodies) - 1
                    step = None
                    if body.get("tools") and owner._unfinished(body):
                        rest = owner.steps[owner._issued:]
                        owner._issued += rest.index(None) + 1 if None in rest else len(rest)
                    if body.get("tools") and owner._issued < len(owner.steps):
                        step = owner.steps[owner._issued]
                        owner._issued += 1
                if owner.on_request:
                    owner.on_request(index, body)
                if step is not None:
                    name, arguments = step
                    delta: dict[str, Any] = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": f"call_{index}", "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"role": "assistant", "content": final}, "stop"
                chunks = [{"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
                           "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": "fixture",
                           "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}]
                data = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def _unfinished(self, body: dict[str, Any]) -> bool:
        """Whether ``body`` starts a new run while the previous run's steps are not used up."""
        started = 0 < self._issued < len(self.steps) and self.steps[self._issued - 1] is not None
        return self.isolate_runs and started and not any(
            isinstance(message, dict) and message.get("role") == "tool" for message in body.get("messages", []))

    def __enter__(self) -> "ScriptedProvider":
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def tool_results(self) -> str:
        """Everything the worker sent back, for checking observed tool output."""
        return b"\n".join(self.raw).decode("utf-8", "replace")
