"""The standalone report contains only recorded metadata and local assets."""

import json
from pathlib import Path
import re
import tempfile
import unittest

from planner.provenance import Provenance
from planner.report import generate
from planner.policy import Policy
from planner.records import Action, Grant, ProtectionStatus
from planner.supervisor import Supervisor
from planner.context import ModelContext


class ReportTests(unittest.TestCase):
    def test_actual_context_and_transport_failure_records_keep_lineage_without_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Provenance(root / "provenance.sqlite")
            self.addCleanup(store.close)
            store.register_task("task", "project")
            context = ModelContext(store, "task", "worker")
            self.addCleanup(context.close)
            source = context.observe("DO-NOT-EMBED-MODEL-BYTES", "tool_result", ".env", sensitive=True)
            admitted = context.admit([source], kind="provider_call")
            failed = context.fail(admitted)
            output = root / "report.html"
            generate({"provenance": store.export()}, output)
            html = output.read_text()
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
            rendered = json.loads(embedded.group(1))
            self.assertIn({"source": source.record_id, "target": admitted.record_id,
                           "kind": "information"}, rendered["edges"])
            self.assertIn({"source": admitted.record_id, "target": failed.id,
                           "kind": "information"}, rendered["edges"])
            failure = next(node for node in rendered["nodes"] if node["id"] == failed.id)
            self.assertEqual(failure["kind"], "model_call_failed")
            self.assertTrue(failure["details"]["sensitive"])
            self.assertNotIn("DO-NOT-EMBED-MODEL-BYTES", html)

    def test_attempt_timeline_uses_real_transitions_without_claiming_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            supervisor = Supervisor(root / "control.sqlite")
            self.addCleanup(supervisor.close)
            task = supervisor.propose(Policy(project, read=("**",)))
            supervisor.approve_task(task)
            supervisor.approve_worker(Grant(task, "worker", read=("**",)))
            attempt = supervisor.prepare_attempt(task, "worker")
            fixture = ProtectionStatus(True, "fixture-only", "Not OS evidence", verified=True)
            supervisor.verify_attempt(attempt["id"], fixture, fixture)
            supervisor.start_attempt(attempt["id"])
            supervisor.reconcile(task)
            data = supervisor.export(task)
            for event in data["attempts"][0]["events"]:
                event["raw_body"] = "DO-NOT-EMBED-LIFECYCLE-BODY"
            output = root / "report.html"
            generate(data, output)
            html = output.read_text()
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
            rendered = json.loads(embedded.group(1))
            events = [node for node in rendered["nodes"] if node["kind"] == "attempt_event"]
            self.assertEqual([node["details"]["state"] for node in events],
                             ["prepared", "verified", "running", "interrupted"])
            self.assertTrue(all("cleanup unverified" in node["status"] for node in events))
            self.assertTrue(all(node["time"] is not None for node in events))
            snapshot = next(node for node in rendered["nodes"] if node["id"] == f"attempt:{attempt['id']}")
            self.assertEqual(snapshot["time"], data["attempts"][0]["updated"])
            self.assertEqual(len([edge for edge in rendered["edges"] if edge["kind"] == "lifecycle"]), 4)
            self.assertNotIn("DO-NOT-EMBED-LIFECYCLE-BODY", html)
            self.assertIn("Recorded lifecycle", html)

    def test_mismatched_attempt_and_event_bindings_are_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            generate({"attempts": [{"id": "attempt-1", "task": "task-1", "worker": "worker-1",
                                    "revision": 2, "grant": {}, "state": "interrupted",
                                    "cleanup_verified": False, "events": [
                                        {"id": 1, "attempt": "other-attempt", "state": "running",
                                         "created": 1, "checks": [], "cleanup_verified": False}]}]}, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            rendered = json.loads(embedded.group(1))
            self.assertEqual(rendered["edges"], [])
            self.assertTrue(any("grant snapshot missing or mismatched" in item for item in rendered["missing"]))
            self.assertTrue(any("Lifecycle event attempt binding mismatch" in item for item in rendered["missing"]))

    def test_delegated_worker_links_only_to_its_recorded_approved_template(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            supervisor = Supervisor(root / "control.sqlite")
            self.addCleanup(supervisor.close)
            task = supervisor.propose(Policy(project, read=("**",), write=("src/**",)))
            supervisor.approve_task(task)
            supervisor.approve_worker(Grant(task, "parent", read=("src/**",)))
            supervisor.approve_delegation_template(task, "reader", "parent", read=("src/**",))
            supervisor.delegate(Grant(task, "child", read=("src/refunds.py",), parent_id="parent"), "reader")
            data = supervisor.export(task)
            data["delegation_templates"][0]["raw_body"] = "DO-NOT-EMBED-TEMPLATE-BODY"
            output = root / "report.html"
            generate(data, output)
            html = output.read_text()
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
            rendered = json.loads(embedded.group(1))
            self.assertIn({"source": f"template:{task}:1:reader", "target": f"worker:{task}:child",
                           "kind": "authorization"}, rendered["edges"])
            self.assertNotIn("DO-NOT-EMBED-TEMPLATE-BODY", html)
            data["delegation_templates"][0]["parent"] = "some-other-parent"
            generate(data, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            rendered = json.loads(embedded.group(1))
            self.assertFalse(any(edge["source"].startswith("template:") and edge["target"] == f"worker:{task}:child"
                                 for edge in rendered["edges"]))
            self.assertTrue(any("template" in message.lower() and "mismatch" in message.lower()
                                for message in rendered["missing"]))

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

    def test_resume_is_an_operator_action_not_a_file_access_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            supervisor = Supervisor(root / "control.sqlite")
            self.addCleanup(supervisor.close)
            task = supervisor.propose(Policy(project, read=("**",), deny_read=(".env",)))
            supervisor.approve_task(task)
            supervisor.approve_worker(Grant(task, "worker", read=("**",)))
            for _ in range(3):  # Identical refusals suspend the worker.
                supervisor.authorize(Action(task, "worker", "read", ".env"))
            fixture = ProtectionStatus(True, "fixture-only", "Not OS evidence", verified=True)
            supervisor.resume(task, "worker", fixture, fixture)
            data = supervisor.export(task)
            output = root / "report.html"
            generate(data, output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', output.read_text(), re.S)
            rendered = json.loads(embedded.group(1))
            resume = data["decisions"][-1]
            node = next(node for node in rendered["nodes"] if node["id"] == f"decision:{task}:{resume['id']}")
            self.assertEqual((node["kind"], node["label"], node["status"]),
                             ("operator", "Resume worker", "operator action; runtime verified"))
            self.assertEqual(node["details"]["reason"], resume["reason"])  # The recorded row, unchanged.
            self.assertEqual([edge for edge in rendered["edges"] if edge["target"] == node["id"]],
                             [{"source": f"worker:{task}:worker", "target": node["id"], "kind": "lifecycle"}])
            decisions = [node for node in rendered["nodes"] if node["kind"] == "decision"]
            self.assertEqual([node["status"] for node in decisions], ["denied"] * 3)

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


class ApplicationReportTests(unittest.TestCase):
    def test_applied_retained_and_conflicted_outcomes_come_from_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project").mkdir()
            supervisor = Supervisor(root / "state.sqlite")
            self.addCleanup(supervisor.close)
            policy = Policy(root / "project", read=("**",), write=("src/**",), tools=("shell",))
            task = supervisor.propose(policy)
            supervisor.approve_task(task)
            verified = ProtectionStatus(True, "fixture", "fixture", True)
            outcomes = {}
            for worker, state in (("applied", "applied"), ("retained", "retained"), ("conflict", "conflicted")):
                supervisor.approve_worker(Grant(task, worker, ("**",), ("src/**",), ("shell",)))
                attempt = supervisor.prepare_attempt(task, worker)["id"]
                supervisor.verify_attempt(attempt, verified, verified)
                supervisor.start_attempt(attempt)
                supervisor.finish_attempt(attempt, successful=True, cleanup_verified=True)
                mode = "review" if state == "retained" else "auto"
                supervisor.record_application(attempt, mode=mode, state=state, reason="fixture",
                                              changes=[{"path": "src/a.py", "operation": "modify"}])
                outcomes[attempt] = state
            output = root / "report.html"
            generate(supervisor.export(task), output)
            embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>',
                                 output.read_text(), re.S)
            rendered = json.loads(embedded.group(1))
            statuses = {node["details"]["attempt"]: node["status"] for node in rendered["nodes"]
                        if node["kind"] == "application"}
            self.assertEqual({attempt: statuses[attempt] for attempt in outcomes},
                             {attempt: {"applied": "applied to source",
                                        "retained": "executed in copy; retained for review",
                                        "conflicted": "conflicted; source preserved"}[state]
                              for attempt, state in outcomes.items()})
            for attempt in outcomes:
                self.assertIn({"source": f"attempt:{attempt}", "target": f"application:{attempt}",
                               "kind": "lifecycle"}, rendered["edges"])
            with self.assertRaises(ValueError):  # A final outcome cannot be replayed into "applied".
                supervisor.record_application(next(iter(outcomes)), mode="auto", state="applied", reason="replay")
