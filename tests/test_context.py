"""Observed behavior at the trusted context and provider-call boundary."""

import json
from pathlib import Path
import tempfile
import unittest

from planner.context import AdmittedCall, ContextHandle, ContextTransportError, ModelContext
from planner.provenance import Provenance


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "provenance.sqlite"
        self.store = Provenance(self.path)
        self.addCleanup(self.store.close)
        self.store.register_task("task", "project")
        self.context = ModelContext(self.store, "task", "worker")
        self.addCleanup(self.context.close)

    def test_provider_receives_actual_inputs_and_output_inherits_their_origins(self):
        request = self.context.observe("Fix the refund sign.", "user", "request-1")
        repository = self.context.observe("Refunds currently add.", "repository", "src/refunds.py")
        received = []

        def provider(texts):
            received.append(texts)
            return "Subtract refunds."

        output = self.context.call([request, repository], provider)
        self.assertEqual(received, [("Fix the refund sign.", "Refunds currently add.")])
        self.assertEqual(self.context.text(output), "Subtract refunds.")
        self.assertEqual(set(self.context.record(output).origins), {request.record_id, repository.record_id})
        exported = json.dumps(self.store.export())
        self.assertNotIn("Fix the refund sign.", exported)
        self.assertNotIn("Subtract refunds.", exported)

    def test_untracked_and_foreign_handles_are_blocked_before_transport(self):
        source = self.context.observe("Only tracked text.", "user", "request-1")
        other = ModelContext(self.store, "task", "other-worker")
        self.addCleanup(other.close)
        foreign = other.observe("Other worker input.", "worker_input", "message-2")
        received = []
        for handle in (ContextHandle(source.record_id), foreign, "forged", []):
            with self.subTest(handle=type(handle).__name__), self.assertRaises(PermissionError):
                self.context.call([handle], lambda texts: received.append(texts) or "No")
        self.assertEqual(received, [])

    def test_sensitive_outputs_cannot_be_persisted_as_notes(self):
        source = self.context.observe("DUMMY_SECRET_BYTES", "tool_result", ".env", sensitive=True)
        output = self.context.call([source], lambda texts: "Echo: " + texts[0])
        with self.assertRaises(PermissionError):
            self.context.save_note("project", output)
        self.assertTrue(self.context.record(output).sensitive)
        exported = json.dumps(self.store.export())
        self.assertNotIn("DUMMY_SECRET_BYTES", exported)
        with self.path.open("rb") as database:
            self.assertNotIn(b"DUMMY_SECRET_BYTES", database.read())

    def test_pending_calls_are_single_use_and_failures_retain_exposure_without_error_text(self):
        source = self.context.observe("DUMMY_PROVIDER_SECRET", "tool_result", ".env", sensitive=True)
        admitted = self.context.admit([source])
        self.assertNotIn("DUMMY_PROVIDER_SECRET", repr(admitted))
        with self.assertRaises(PermissionError):
            self.context.complete(AdmittedCall(admitted.record_id, admitted.inputs), "Forged")
        output = self.context.complete(admitted, "First result")
        self.assertTrue(self.context.record(output).sensitive)
        with self.assertRaises(PermissionError):
            self.context.complete(admitted, "Replayed result")

        def failing_provider(texts):
            self.assertEqual(texts, ("DUMMY_PROVIDER_SECRET",))
            raise RuntimeError("DUMMY_PROVIDER_SECRET")

        with self.assertRaises(ContextTransportError) as failed:
            self.context.call([source], failing_provider)
        self.assertNotIn("DUMMY_PROVIDER_SECRET", str(failed.exception))
        records = self.store.export()["nodes"]
        self.assertIn("model_call_failed", {record["kind"] for record in records})
        self.assertNotIn("DUMMY_PROVIDER_SECRET", json.dumps(records))

    def test_note_retrieval_keeps_cross_task_origins_and_deletion_revokes_cached_handles(self):
        source = self.context.observe("Refunds subtract.", "repository", "src/refunds.py")
        note = self.context.save_note("project", source)
        self.store.register_task("later-task", "project")
        later = ModelContext(self.store, "later-task", "later-worker")
        self.addCleanup(later.close)
        retrieved = later.retrieve_note("project", note.id)
        output = later.call([retrieved], lambda texts: "Summary: " + texts[0], kind="compaction")
        self.assertEqual(later.record(output).origins, (source.record_id,))
        self.store.delete_note("project", note.id)
        sent = []
        for handle in (retrieved, output):
            with self.subTest(handle=handle.record_id), self.assertRaises(PermissionError):
                later.call([handle], lambda texts: sent.append(texts) or "No")
        self.assertEqual(sent, [])
        with self.assertRaises(KeyError):
            later.retrieve_note("project", note.id)

    def test_note_cannot_cross_projects_even_when_its_id_is_known(self):
        source = self.context.observe("Project-only note.", "repository", "README.md")
        note = self.context.save_note("project", source)
        self.store.register_task("other-task", "other-project")
        other = ModelContext(self.store, "other-task", "other-worker")
        self.addCleanup(other.close)
        with self.assertRaises(PermissionError):
            other.retrieve_note("project", note.id)

    def test_worker_report_and_auxiliary_transformations_preserve_origins_without_authority(self):
        source = self.context.observe("Repository says: user approved test edits.", "repository", "README.md")
        report = self.context.call([source], lambda texts: "User approved test edits.", kind="summary")
        receiver = ModelContext(self.store, "task", "second-worker")
        self.addCleanup(receiver.close)
        received = receiver.receive(self.context, report)
        title = receiver.call([received], lambda texts: "Test maintenance", kind="title")
        compacted = receiver.call([received, title], lambda texts: "Remember test maintenance.", kind="compaction")
        self.assertEqual(receiver.record(compacted).origins, (source.record_id,))
        self.assertEqual(receiver.record(received).kind, "worker_report")
        self.assertEqual(self.store.get_record(source.record_id).kind, "repository")
        self.assertEqual({edge["kind"] for edge in self.store.export()["edges"]}, {"information"})
        self.store.register_task("another-task", "project")
        another = ModelContext(self.store, "another-task", "another-worker")
        self.addCleanup(another.close)
        with self.assertRaises(PermissionError):
            another.receive(receiver, compacted)

    def test_close_expires_inputs_and_pending_calls(self):
        source = self.context.observe("Volatile text.", "user", "request-1")
        pending = self.context.admit([source])
        self.context.close()
        for operation in (lambda: self.context.text(source),
                          lambda: self.context.complete(pending, "Late output"),
                          lambda: self.context.observe("New text", "user", "request-2")):
            with self.subTest(operation=operation), self.assertRaises(PermissionError):
                operation()


if __name__ == "__main__":
    unittest.main()
