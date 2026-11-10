from contextlib import contextmanager
import json
import socket
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from planner.broker_server import BrokerServer
from planner.connector import REQUIRED_HOOKS
from planner.mediation import WorkerBroker
from planner.policy import Policy
from planner.provenance import Provenance
from planner.records import Grant
from planner.supervisor import Supervisor


class BrokerServerTests(unittest.TestCase):
    def test_only_authenticated_worker_protocol_reaches_the_broker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            state = root / "control.sqlite"
            control = Supervisor(state)
            try:
                task = control.propose(Policy(project, read=("**",)))
                control.approve_task(task)
                control.approve_worker(Grant(task, "worker", ("**",)))
            finally:
                control.close()

            @contextmanager
            def factory():
                supervisor = Supervisor(state)
                store = Provenance(root / "memory.sqlite")
                broker = WorkerBroker(supervisor, store, task, "worker")
                try:
                    yield broker
                finally:
                    broker.close()
                    store.close()
                    supervisor.close()

            with BrokerServer(factory, max_request_bytes=1024, timeout_seconds=1) as server:
                body = json.dumps({"protocol": 1, "sequence": 1, "route": "hello", "session_id": None,
                                   "payload": {"release": "2.2.5", "registered": list(REQUIRED_HOOKS)}}).encode()
                wrong = Request(server.endpoint, body, headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"})
                address = urlsplit(server.endpoint)
                with socket.create_connection((address.hostname, address.port), timeout=3) as stalled:
                    stalled.sendall(b"POST /admit HTTP/1.1\r\n")
                    # Incomplete unauthenticated headers must not occupy the
                    # single serving thread indefinitely.
                    with self.assertRaises(HTTPError) as denied:
                        urlopen(wrong, timeout=3)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                valid = Request(server.endpoint, body, headers={"Authorization": f"Bearer {server.token}", "Content-Type": "application/json"})
                with urlopen(valid, timeout=3) as response:
                    self.assertTrue(json.load(response)["allowed"])
                large = Request(server.endpoint, b"x" * 1025, headers={"Authorization": f"Bearer {server.token}", "Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as denied:
                    urlopen(large, timeout=3)
                self.assertEqual(denied.exception.code, 413)
                denied.exception.close()


if __name__ == "__main__":
    unittest.main()
