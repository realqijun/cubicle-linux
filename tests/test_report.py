"""The standalone report contains only recorded metadata and local assets."""

import json
from pathlib import Path
import re
import tempfile
import unittest

from planner.provenance import Provenance
from planner.report import generate


class ReportTests(unittest.TestCase):
    def test_report_preserves_real_origins_without_embedding_note_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Provenance(root / "state.sqlite")
            self.addCleanup(store.close)
            store.register_task("task-1", "project-1")
            source = store.record_input("task-1", "worker-1", "repository", "README.md")
            report = store.derive("task-1", "worker-1", "summary", [source.id])
            store.add_note("project-1", "task-1", "worker-1", "DO-NOT-EMBED-NOTE-TEXT", [report.id])
            output = root / "report.html"
            generate({"provenance": store.export()}, output)
            html = output.read_text()
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>',
                                 html, re.S)
            self.assertIsNotNone(embedded)
            data = json.loads(embedded.group(1))
            self.assertIn(source.id, {node["id"] for node in data["nodes"]})
            self.assertIn({"source": source.id, "target": report.id, "kind": "information"}, data["edges"])
            self.assertNotIn("DO-NOT-EMBED-NOTE-TEXT", html)
            self.assertNotRegex(html, r'<(?:script|link)[^>]+(?:src|href)=["\']https?://')

    def test_untrusted_labels_cannot_escape_embedded_data(self) -> None:
        payload = '</script><img src=x onerror="alert(1)">&'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            generate({"nodes": [{"id": "observed-1", "task_id": "task-1", "worker_id": "worker-1",
                                  "kind": "repository", "reference": payload, "origins": ["observed-1"],
                                  "sensitive": False, "created_at": 1}], "edges": []}, output)
            html = output.read_text()
            self.assertNotIn(payload, html)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
            self.assertEqual(json.loads(embedded.group(1))["nodes"][0]["label"], payload)
            self.assertNotIn("innerHTML", html)

    def test_substituted_approval_is_missing_evidence_not_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            generate({"approvals": [{"id": "approval-1", "task": "other-task", "worker": "worker-1",
                                     "operation": "write", "resource": "tests/file.py", "tool": "file",
                                     "state": "approved"}],
                      "decisions": [{"id": 1, "task": "task-1", "worker": "worker-1", "operation": "write",
                                     "resource": "src/file.py", "tool": "file", "approval": "approval-1",
                                     "allowed": True, "created": 1}]}, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            data = json.loads(embedded.group(1))
            self.assertEqual(data["edges"], [])
            self.assertTrue(any("mismatch" in item.lower() for item in data["missing"]))
            self.assertEqual(data["nodes"][1]["status"], "allowed; effect unverified")

    def test_deleted_note_is_only_metadata_in_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Provenance(root / "state.sqlite")
            self.addCleanup(store.close)
            store.register_task("task-1", "project-1")
            source = store.record_input("task-1", "worker-1", "tool", "result-1")
            note = store.add_note("project-1", "task-1", "worker-1", "deleted-text", [source.id])
            store.delete_note("project-1", note.id)
            generate({"provenance": store.export()}, root / "report.html")
            html = (root / "report.html").read_text()
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
            node = next(item for item in json.loads(embedded.group(1))["nodes"] if item["id"] == note.id)
            self.assertEqual(node["status"], "deleted note")
            self.assertIsNotNone(node["details"]["deleted_at"])
            self.assertNotIn("deleted-text", html)

    def test_exact_approval_and_grant_links_use_supplied_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            binding = {"task": "task-1", "worker": "worker-1", "operation": "read",
                       "resource": "/project/.env", "tool": "file"}
            generate({"tasks": [{"id": "task-1", "revision": 1, "state": "approved", "expires": 100}],
                      "policy_revisions": [{"task": "task-1", "revision": 1, "policy": {"read": ["src/**"]},
                                            "created": 1, "approved": 2}],
                      "workers": [{"task": "task-1", "id": "worker-1", "state": "active", "revision": 1,
                                   "grant": {"read": ["src/**"]}}],
                      "approvals": [{**binding, "id": "approval-1", "state": "consumed", "expires": 100}],
                      "decisions": [{**binding, "id": 1, "approval": "approval-1", "allowed": True, "created": 1}],
                      "untrusted_raw_output": "DO-NOT-EMBED-RAW"}, output)
            html = output.read_text()
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
            data = json.loads(embedded.group(1))
            self.assertEqual(data["edges"], [
                {"source": "policy:task-1:1", "target": "worker:task-1:worker-1", "kind": "authorization"},
                {"source": "approval:approval-1", "target": "decision:task-1:1", "kind": "authorization"}])
            self.assertNotIn("DO-NOT-EMBED-RAW", html)

    def test_missing_source_record_is_explained_without_invented_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            generate({"nodes": [{"id": "summary-1", "task_id": "task-1", "worker_id": "worker-1",
                                  "kind": "summary", "origins": ["missing-1"]}],
                      "edges": [{"source": "missing-1", "target": "summary-1"}]}, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            data = json.loads(embedded.group(1))
            self.assertEqual(data["edges"], [])
            self.assertTrue(any("Missing origin record missing-1" in item for item in data["missing"]))

    def test_revoked_worker_remains_bound_to_its_original_policy_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            revisions = [{"task": "task-1", "revision": number, "policy": {},
                          "created": number * 2, "approved": number * 2 + 1} for number in (1, 2)]
            generate({"tasks": [{"id": "task-1", "revision": 2, "state": "approved"}],
                      "policy_revisions": revisions,
                      "workers": [{"task": "task-1", "id": "worker-1", "state": "revoked",
                                   "revision": 1, "grant": {}}]}, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            data = json.loads(embedded.group(1))
            self.assertEqual(data["edges"], [{"source": "policy:task-1:1", "target": "worker:task-1:worker-1",
                                              "kind": "authorization"}])
            first_policy = next(node for node in data["nodes"] if node["id"] == "policy:task-1:1")
            self.assertEqual(first_policy["time"], 2)
            self.assertEqual(first_policy["details"]["approved"], 3)

    def test_ordinary_allowed_decision_links_to_its_recorded_installed_grant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            generate({"tasks": [{"id": "task-1", "revision": 1, "state": "approved"}],
                      "policy_revisions": [{"task": "task-1", "revision": 1, "policy": {},
                                            "created": 1, "approved": 2}],
                      "workers": [{"task": "task-1", "id": "worker-1", "state": "active",
                                   "revision": 1, "grant": {"write": ["src/**"]}}],
                      "decisions": [{"id": 1, "task": "task-1", "worker": "worker-1", "operation": "write",
                                     "resource": "src/refunds.py", "tool": "file", "allowed": True,
                                     "grant_revision": 1, "created": 3}]}, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            data = json.loads(embedded.group(1))
            self.assertIn({"source": "worker:task-1:worker-1", "target": "decision:task-1:1",
                           "kind": "authorization"}, data["edges"])

    def test_mismatched_and_legacy_decision_revisions_do_not_invent_grant_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            for decision_revision in (None, 2):
                with self.subTest(grant_revision=decision_revision):
                    generate({"policy_revisions": [{"task": "task-1", "revision": 1, "policy": {},
                                                    "created": 1, "approved": 2}],
                              "workers": [{"task": "task-1", "id": "worker-1", "state": "active",
                                           "revision": 1, "grant": {}}],
                              "decisions": [{"id": 1, "task": "task-1", "worker": "worker-1", "operation": "write",
                                             "resource": "src/refunds.py", "tool": "file", "allowed": True,
                                             "grant_revision": decision_revision}]}, output)
                    embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
                    data = json.loads(embedded.group(1))
                    self.assertFalse(any(edge["target"] == "decision:task-1:1" for edge in data["edges"]))
                    self.assertTrue(any("grant revision missing or mismatched" in text for text in data["missing"]))

    def test_claimed_approval_sources_are_not_promoted_to_information_edges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            generate({"approvals": [{"id": "approval-1", "task": "task-1", "worker": "worker-1",
                                     "operation": "write", "resource": "tests/file.py", "tool": "file",
                                     "state": "requested", "sources": '["claimed-source"]'}]}, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            data = json.loads(embedded.group(1))
            self.assertEqual(data["edges"], [])
            self.assertIn("claimed-source", data["nodes"][0]["details"]["sources"])
            self.assertTrue(any("source provenance" in text for text in data["missing"]))


if __name__ == "__main__":
    unittest.main()
