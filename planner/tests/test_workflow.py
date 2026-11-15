"""Planning reaches an actual provider using only prompt and trusted templates."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import gc
import sqlite3
import warnings
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from planner.__main__ import execute, parser
from planner.provenance import Provenance
from planner.supervisor import Supervisor
from planner.connector import ConnectorConfig, ProviderConfig
from planner.workflow import plan


class PlanningTests(unittest.TestCase):
    def test_provenance_initialization_failure_cancels_the_unapproved_task(self):
        class ObservedSupervisor(Supervisor):
            def propose(self, policy):
                self.proposed = super().propose(policy)
                return self.proposed

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            supervisor = ObservedSupervisor(root / 'control.sqlite')
            self.addCleanup(supervisor.close)
            supervisor.path.with_suffix('.memory.sqlite').write_bytes(b'not a SQLite database')
            with warnings.catch_warnings(record=True) as emitted:
                warnings.simplefilter('always', ResourceWarning)
                with self.assertRaises(sqlite3.DatabaseError):
                    plan(supervisor, project, 'Inspect', ConnectorConfig(
                        'fixture/model', provider=ProviderConfig('http://127.0.0.1:1/v1')))
                gc.collect()
            self.assertFalse([warning for warning in emitted if warning.category is ResourceWarning])
            self.assertEqual(supervisor.status(supervisor.proposed)['state'], 'cancelled')

    @contextmanager
    def provider(self, proposal):
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                observed.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                data = json.dumps({'choices': [{'message': {'content': json.dumps(proposal)}}]}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}/v1', observed
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_creates_reviewable_model_proposal_without_repository_input_or_approval(self):
        proposal = {'template': 'source', 'read': ['src/**'], 'write': ['src/**'], 'tools': ['file', 'shell']}
        with tempfile.TemporaryDirectory() as directory, self.provider(proposal) as (url, observed):
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            (project / 'README.md').write_text('REPOSITORY_CONTENT_MUST_NOT_REACH_PLANNER')
            supervisor = Supervisor(root / 'control.sqlite')
            try:
                args = parser().parse_args(['--state', str(supervisor.path), 'plan', str(project),
                                            '--prompt', 'Fix source behavior', '--model', 'fixture/model',
                                            '--provider-url', url])
                result = execute(args, supervisor)
                self.assertEqual(result['state'], 'proposed')
                self.assertEqual(result['policy']['write'], ['src/**'])
                self.assertEqual(supervisor.workers(result['id']), [])
                self.assertEqual(result['worker_proposal']['tools'], ['file', 'shell'])
                self.assertEqual(observed[0]['messages'][1]['content'], 'Fix source behavior')
                self.assertNotIn('REPOSITORY_CONTENT_MUST_NOT_REACH_PLANNER', json.dumps(observed))
                store = Provenance(supervisor.path.with_suffix('.memory.sqlite'))
                try:
                    metadata = json.dumps(store.export(result['id']))
                    self.assertNotIn('Fix source behavior', metadata)
                    self.assertIn('planner_model_call', metadata)
                finally:
                    store.close()
            finally:
                supervisor.close()

    def test_invalid_model_fields_are_not_rendered_as_operator_errors(self):
        proposal = {'template': 'source', 'read': ['../DUMMY_PRIVATE_TEXT'], 'write': [], 'tools': ['file']}
        with tempfile.TemporaryDirectory() as directory, self.provider(proposal) as (url, _):
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            supervisor = Supervisor(root / 'control.sqlite')
            try:
                args = parser().parse_args(['--state', str(supervisor.path), 'plan', str(project),
                                            '--prompt', 'Inspect', '--model', 'fixture/model', '--provider-url', url])
                with self.assertRaises(ValueError) as caught:
                    execute(args, supervisor)
                self.assertNotIn('DUMMY_PRIVATE_TEXT', str(caught.exception))
            finally:
                supervisor.close()

    def test_model_cannot_widen_trusted_template(self):
        proposal = {'template': 'inspection', 'read': ['**'], 'write': ['src/**'], 'tools': ['shell']}
        with tempfile.TemporaryDirectory() as directory, self.provider(proposal) as (url, _):
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            supervisor = Supervisor(root / 'control.sqlite')
            try:
                args = parser().parse_args(['--state', str(supervisor.path), 'plan', str(project),
                                            '--prompt', 'Inspect', '--model', 'fixture/model', '--provider-url', url])
                with self.assertRaises(ValueError):
                    execute(args, supervisor)
            finally:
                supervisor.close()
