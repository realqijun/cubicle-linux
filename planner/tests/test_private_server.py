"""Exercise private-server ownership through a real child and loopback HTTP."""

import subprocess
import sys
import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path


class PrivateServerTests(unittest.TestCase):
    def test_registered_private_server_accepts_pinned_prompt_format(self) -> None:
        from planner.private_server import PrivateServer
        import json

        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "requests"
            script = ("import json,sys,threading\nfrom http.server import BaseHTTPRequestHandler,HTTPServer\n"
                      "from pathlib import Path\n"
                      "class H(BaseHTTPRequestHandler):\n"
                      " def log_message(self,*args): pass\n"
                      " def reply(self):\n"
                      "  self.send_response(200); self.end_headers(); self.wfile.write(b'{\"data\":{\"id\":\"ses_fixture\"}}')\n"
                      " def do_GET(self): self.reply()\n"
                      " def do_POST(self):\n"
                      "  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))\n"
                      f"  with Path({str(marker)!r}).open('a') as output: output.write(json.dumps([self.path,body])+'\\n')\n"
                      "  self.reply()\n"
                      "server=HTTPServer(('127.0.0.1',0),H)\n"
                      "threading.Thread(target=server.serve_forever,daemon=True).start()\n"
                      "print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port)}),flush=True)\n"
                      "sys.stdin.read(); server.shutdown()\n")
            child = subprocess.Popen([sys.executable, "-I", "-c", script], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            with patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:1', 'no_proxy': '',
                                         'HTTP_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}), \
                    PrivateServer(lambda: child, password="a" * 32, hooks_ready=lambda: True) as server:
                with self.assertRaises(RuntimeError):
                    server.submit("not initialized", "fixture/model")
                server.initialize("/workspace")
                self.assertEqual(server.submit("Do the task", "fixture/model"), "ses_fixture")
            messages=[json.loads(line) for line in marker.read_text().splitlines()]
            self.assertEqual(messages[0], ['/api/session', {'model': {'providerID': 'fixture', 'id': 'model'}}])
            self.assertEqual(messages[-1], ["/api/session/ses_fixture/prompt", {"text": "Do the task"}])
            self.assertIsNotNone(child.poll())

    def test_missing_hooks_prevent_task_submission_and_close_owned_child(self) -> None:
        from planner.private_server import PrivateServer

        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "submitted"
            script = ("import json,sys,threading\nfrom http.server import BaseHTTPRequestHandler,HTTPServer\n"
                      "from pathlib import Path\n"
                      "class H(BaseHTTPRequestHandler):\n"
                      " def log_message(self,*args): pass\n"
                      " def do_GET(self):\n"
                      "  self.send_response(200); self.end_headers(); self.wfile.write(b'{}')\n"
                      " def do_POST(self):\n"
                      f"  Path({str(marker)!r}).write_text('bad')\n"
                      "  self.send_response(200); self.end_headers(); self.wfile.write(b'{}')\n"
                      "server=HTTPServer(('127.0.0.1',0),H)\n"
                      "threading.Thread(target=server.serve_forever,daemon=True).start()\n"
                      "print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port)}),flush=True)\n"
                      "sys.stdin.read(); server.shutdown()\n")
            child = subprocess.Popen([sys.executable, "-I", "-c", script], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            server = PrivateServer(lambda: child, password="a" * 32,
                                   hooks_ready=lambda: False, timeout_seconds=2)
            with self.assertRaises(TimeoutError):
                with server:
                    server.initialize("/workspace")
                    server.submit("DO_NOT_DISPATCH", "fixture/model")
            self.assertIsNotNone(child.poll())
            self.assertFalse(marker.exists())
