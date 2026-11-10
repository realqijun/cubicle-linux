"""Private loopback transport for one worker's trusted connector hooks."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import secrets
import threading
from typing import Any

from .mediation import WorkerBroker


class BrokerServer:
    def __init__(self, factory: Callable[[], AbstractContextManager[WorkerBroker]], *,
                 max_request_bytes: int = 4 * 1024 * 1024, timeout_seconds: float = 10) -> None:
        if max_request_bytes <= 0 or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Request size and timeout must be positive and finite")
        self.token = secrets.token_urlsafe()
        self.endpoint = ""
        self._factory, self._limit, self._timeout = factory, max_request_bytes, timeout_seconds
        self._ready = threading.Event()
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._failed = False

    def __enter__(self) -> "BrokerServer":
        if self._thread is not None:
            raise RuntimeError("Broker server cannot be restarted")
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        if not self._ready.wait(self._timeout) or self._failed:
            self.close()
            raise RuntimeError("Broker transport unavailable")
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _serve(self) -> None:
        owner = self
        try:
            # SQLite and all context state stay on this single serving thread.
            with self._factory() as broker:
                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, format: str, *args: Any) -> None:
                        pass  # Request bodies, tokens, and provider errors are not ordinary logs.

                    def do_POST(self) -> None:
                        self.connection.settimeout(owner._timeout)
                        supplied = self.headers.get("Authorization", "")
                        if not secrets.compare_digest(supplied.encode("utf-8"), ("Bearer " + owner.token).encode("ascii")):
                            self.send_error(403)
                            return
                        if self.path != "/admit" or self.headers.get("Transfer-Encoding"):
                            self.send_error(404)
                            return
                        if self.headers.get_content_type() != "application/json":
                            self.send_error(415)
                            return
                        try:
                            length = int(self.headers.get("Content-Length", "-1"))
                            if length < 0 or length > owner._limit:
                                self.send_error(413)
                                return
                            raw = self.rfile.read(length)
                            if len(raw) != length:
                                raise ValueError("Incomplete request")
                            envelope = json.loads(raw)
                            if not isinstance(envelope, dict):
                                raise ValueError("Envelope must be an object")
                            result = broker.handle_request(envelope)
                        except (ValueError, OSError):
                            self.send_error(400)
                            return
                        response = json.dumps(result, ensure_ascii=True).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Content-Length", str(len(response)))
                        self.end_headers()
                        self.wfile.write(response)

                class ReadyServer(HTTPServer):
                    def service_actions(self) -> None:
                        owner._ready.set()

                with ReadyServer(("127.0.0.1", 0), Handler) as server:
                    self._server = server
                    self.endpoint = f"http://127.0.0.1:{server.server_port}/admit"
                    server.serve_forever(poll_interval=min(self._timeout, 0.1))
        except Exception:
            self._failed = True
            self._ready.set()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(self._timeout)
            if self._thread.is_alive():
                raise RuntimeError("Broker transport did not stop")
