"""Fixed-destination Unix transport, never a general host network proxy."""

from contextlib import AbstractContextManager, closing
from http.server import BaseHTTPRequestHandler
import http.client
from pathlib import Path
import secrets
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit


def pump(left: socket.socket, right: socket.socket) -> None:
    """Relay until either endpoint closes; closing the owner stops both sides."""
    try:
        while True:
            ready, _, _ = select.select([left, right], [], [], 0.2)
            for source in ready:
                chunk = source.recv(65536)
                if not chunk:
                    return
                (right if source is left else left).sendall(chunk)
    except OSError:
        return


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.active: set[socket.socket] = set()
        self.lock = threading.Lock()
        self.stopping = False
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def get_request(self) -> tuple[socket.socket, object]:
        connection, address = super().get_request()
        connection.settimeout(30)
        self.track(connection)
        return connection, address

    def track(self, connection: socket.socket) -> None:
        with self.lock:
            if self.stopping:
                connection.close()
                raise OSError("Transport stopped")
            self.active.add(connection)

    def untrack(self, connection: socket.socket) -> None:
        with self.lock:
            self.active.discard(connection)

    def close_request(self, request: socket.socket | tuple[bytes, socket.socket]) -> None:
        if isinstance(request, socket.socket):
            self.untrack(request)
        super().close_request(request)

    def server_close(self) -> None:
        with self.lock:
            self.stopping = True
            for connection in self.active:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            self.active.clear()
        super().server_close()

    def handle_error(self, request: socket.socket | tuple[bytes, socket.socket], client_address: object) -> None:
        pass  # Transport errors never become ordinary traceback/provider logs.


class UnixRelay(AbstractContextManager["UnixRelay"]):
    def __init__(self, path: Path, target: tuple[str, int]) -> None:
        self.path, self.target = path, target

    def __enter__(self) -> "UnixRelay":
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                try:
                    with socket.create_connection(owner.target, timeout=10) as upstream:
                        owner.server.track(upstream)
                        try:
                            pump(self.request, upstream)
                        finally:
                            owner.server.untrack(upstream)
                except OSError:
                    return

        self.server = _UnixServer(str(self.path), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)
        self.path.unlink(missing_ok=True)


class ProviderRelay(AbstractContextManager["ProviderRelay"]):
    """A fresh worker credential opens exactly the configured upstream HTTP base.

    The provider's real credential remains outside the worker and its children.
    This relay does not replace the model/context hooks or certify their coverage.
    """

    def __init__(self, path: Path, base_url: str, *, api_key: str | None = None,
                 limit: int = 4 * 1024 * 1024, timeout: float = 30) -> None:
        self.path, self.base_url, self.api_key = path, base_url, api_key
        self.limit, self.timeout = limit, timeout
        self.token = secrets.token_urlsafe(32)
        self.requests = 0

    def __enter__(self) -> "ProviderRelay":
        owner, base = self, urlsplit(self.base_url)
        if base.scheme not in ("http", "https") or not base.hostname:
            raise ValueError("Explicit fixed HTTP(S) provider required")
        hostname = base.hostname
        prefix = base.path.rstrip("/")

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(owner.timeout)
                super().setup()

            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                self.connection.settimeout(owner.timeout)
                if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner.token):
                    self.send_error(403)
                    return
                parsed = urlsplit(self.path)
                if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or
                        not parsed.path.startswith(prefix + "/") or
                        any(part in (".", "..") for part in parsed.path.split("/")) or
                        "%" in parsed.path or self.headers.get("Transfer-Encoding")):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                    if length < 0 or length > owner.limit:
                        self.send_error(413)
                        return
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ValueError("Incomplete provider request")
                    kind = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
                    with closing(kind(hostname, base.port, timeout=min(owner.timeout, 10))) as upstream:
                        upstream.connect()
                        assert upstream.sock is not None
                        connection = upstream.sock
                        connection.settimeout(owner.timeout)
                        owner.server.track(connection)
                        try:
                            headers = {"Content-Type": "application/json"}
                            if owner.api_key:
                                headers["Authorization"] = "Bearer " + owner.api_key
                            owner.requests += 1
                            upstream.request("POST", parsed.path, body, headers)
                            response = upstream.getresponse()
                            raw = response.read(owner.limit + 1)
                            if len(raw) > owner.limit:
                                raise ValueError("Oversized provider response")
                            self.send_response(response.status)
                            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                            self.send_header("Content-Length", str(len(raw)))
                            self.end_headers()
                            self.wfile.write(raw)
                        finally:
                            owner.server.untrack(connection)
                except (ValueError, OSError, http.client.HTTPException):
                    self.send_error(502)

        self.server = _UnixServer(str(self.path), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)
        self.path.unlink(missing_ok=True)
