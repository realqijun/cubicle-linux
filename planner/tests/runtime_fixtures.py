"""Deterministic loopback provider for real-runtime tests; not a test module."""

from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import sys
import threading
from typing import Any

RUNTIME_GATE = (sys.platform == "linux" and os.environ.get("PLANNER_RUN_CONFINED_OPENCODE") == "1")
RUNTIME_REASON = "Actual Linux, FUSE helper, loaded profile and pinned OpenCode binary are required"


def shell(script: str, description: str = "Run fixture script") -> tuple[str, dict[str, str]]:
    return "shell", {"command": "python3 -I -c " + shlex.quote(script), "description": description}


class ScriptedProvider:
    """OpenAI-compatible SSE fixture: issues ``steps`` as tool calls, then stops.

    ``on_request(index, body)`` runs before each response so tests can change
    host state at an exact point of the conversation.
    """

    def __init__(self, steps: list[tuple[str, dict[str, Any]]], *,
                 on_request: Callable[[int, dict[str, Any]], None] | None = None) -> None:
        self.steps, self.on_request = list(steps), on_request
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
                    delta, finish = {"role": "assistant", "content": "FIXTURE_DONE"}, "stop"
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

    def __enter__(self) -> "ScriptedProvider":
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def tool_results(self) -> str:
        """Everything the worker sent back, for checking observed tool output."""
        return b"\n".join(self.raw).decode("utf-8", "replace")


def runtime_paths() -> dict[str, Path]:
    return {"opencode": Path(os.environ["PLANNER_OPENCODE_BINARY"]),
            "fuse_helper": Path(os.environ["PLANNER_FUSE_BRIDGE"])}
