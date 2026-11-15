"""Owned pinned-server protocol, separate from OS confinement acceptance.

The trusted caller supplies a confined spawn operation. Exiting this owner proves
only that its child exited; descendant cleanup belongs to the OS launch contract.
No task is submitted until the project's authenticated hook handshake is ready.
"""

import base64
from collections.abc import Callable
import json
import math
import queue
import re
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class PrivateServer:
    """Keep the no-task stdio lease alive and gate dispatch on actual hooks."""

    def __init__(self, spawn: Callable[[], subprocess.Popen[str]], *, password: str,
                 hooks_ready: Callable[[], bool], timeout_seconds: float = 10,
                 max_response_bytes: int = 4 * 1024 * 1024) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", password):
            raise ValueError("A fresh private-server password is required")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("Positive finite timeout and response limit required")
        self._spawn, self._ready = spawn, hooks_ready
        self._timeout, self._limit = timeout_seconds, max_response_bytes
        self._authorization = "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._url = ""
        self._initialized = False

    def __enter__(self) -> "PrivateServer":
        if self._process is not None:
            raise RuntimeError("Private server cannot be restarted")
        self._process = self._spawn()
        if self._process.stdin is None or self._process.stdout is None:
            self.close()
            raise ValueError("The server needs a private stdin lease and stdout pipe")
        lines: queue.Queue[str] = queue.Queue(maxsize=1)
        output = self._process.stdout
        self._reader = threading.Thread(target=lambda: lines.put(output.readline(self._limit + 1)), daemon=True)
        self._reader.start()
        try:
            try:
                line = lines.get(timeout=self._timeout)
            except queue.Empty:
                raise TimeoutError("Private server endpoint unavailable") from None
            if len(line) > self._limit:
                raise ValueError("Oversized server endpoint")
            url = json.loads(line)["url"]
            parsed = urlsplit(url)
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                    or parsed.username or parsed.password or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment):
                raise ValueError("Private server must bind an explicit loopback port")
            self._url = url.rstrip("/")
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._url or self._process is None or self._process.poll() is not None:
            raise RuntimeError("Private server is not running")
        request = Request(self._url + path, data=None if payload is None else json.dumps(payload).encode(),
                          headers={"Authorization": self._authorization, "Content-Type": "application/json"})
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=self._timeout) as response:
            raw = response.read(self._limit + 1)
        if len(raw) > self._limit:
            raise ValueError("Oversized server response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid private server response")
        return result

    def initialize(self, directory: str) -> None:
        """Boot a location/plugin without submitting any user prompt or model call."""
        self._request("/api/location?" + urlencode({"location[directory]": directory}))
        deadline = time.monotonic() + self._timeout
        while not self._ready():
            if self._process is None or self._process.poll() is not None:
                raise RuntimeError("Private server exited before hook readiness")
            if time.monotonic() >= deadline:
                raise TimeoutError("Authenticated hook registration unavailable")
            time.sleep(min(0.01, self._timeout))
        self._initialized = True

    def submit(self, prompt: str, model: str) -> str:
        """Create one session and dispatch only after successful initialization."""
        if not self._initialized or not self._ready():
            raise RuntimeError("Task dispatch requires authenticated hook readiness")
        provider, separator, model_id = model.partition("/")
        if not provider or not separator or not model_id:
            raise ValueError("Explicit provider/model required")
        session = self._request("/api/session", {"model": {"providerID": provider, "id": model_id}})
        session_id = session["data"]["id"]
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            raise ValueError("Invalid session identity")
        self._request(f"/api/session/{session_id}/prompt", {"text": prompt})
        return session_id

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self._timeout)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            if self._reader is not None:
                self._reader.join(timeout=self._timeout)
                if self._reader.is_alive():
                    raise RuntimeError("Endpoint reader did not stop")
