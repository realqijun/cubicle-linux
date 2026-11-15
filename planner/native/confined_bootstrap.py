"""Trusted jail bootstrap. Runs directly with -I; imports no project code."""

import http.client
import json
import os
import select
import socket
import socketserver
import subprocess
import sys
import threading
from typing import Any
from urllib.parse import urlsplit


def pump(left: socket.socket, right: socket.socket) -> None:
    try:
        while True:
            ready, _, _ = select.select([left, right], [], [], 0.2)
            for source in ready:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except OSError:
        return


class TCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True


def handler_factory(service: str) -> type[socketserver.BaseRequestHandler]:
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            with socket.socket(socket.AF_UNIX) as upstream:
                upstream.connect("/transport/" + service + ".sock")
                pump(self.request, upstream)
    return Handler


def control_loop(channel: socket.socket, port: int) -> None:
    # One private connection is accepted by the owner before it publishes the
    # server endpoint. The listening socket is then closed, so tools cannot join.
    with channel.makefile("rwb") as stream:
        while line := stream.readline(8 * 1024 * 1024):
            request: dict[str, Any] = json.loads(line)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            try:
                connection.request(request["method"], request["path"],
                                   request["body"], request["headers"])
                response = connection.getresponse()
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("Oversized private API response")
                result = {"status": response.status, "body": raw.decode("utf-8")}
                stream.write(json.dumps(result).encode() + b"\n")
                stream.flush()
            finally:
                connection.close()


def main() -> None:
    environment = dict(os.environ)
    config = json.loads(sys.stdin.readline())
    environment.update(config["environment"])
    services = []
    for name, port in (("broker", 18181), ("provider", 18182)):
        server = TCP(("127.0.0.1", port), handler_factory(name))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        services.append(server)
    child = subprocess.Popen(config["argv"], env=environment, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, close_fds=True)
    try:
        assert child.stdout is not None
        endpoint = urlsplit(json.loads(child.stdout.readline(4097))["url"])
        if endpoint.hostname != "127.0.0.1" or not endpoint.port:
            raise ValueError("Invalid private worker endpoint")
        channel = socket.socket(socket.AF_UNIX)
        channel.connect("/transport/control.sock")
        threading.Thread(target=control_loop, args=(channel, endpoint.port), daemon=True).start()
        print(json.dumps({"url": config["outer_url"]}), flush=True)
        sys.stdin.read()
    finally:
        if child.stdin:
            child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        for server in services:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
