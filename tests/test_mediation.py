import json
from pathlib import Path
import tempfile
import unittest

from planner.mediation import MediationDenied, WorkerBroker
from planner.connector import REQUIRED_HOOKS
from planner.policy import Policy
from planner.provenance import Provenance
from planner.records import Grant
from planner.supervisor import Supervisor


class MediationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "project"
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("public source")
        (self.root / ".env").write_text("DUMMY_SECRET")
        self.control = Supervisor(base / "control.sqlite")
        self.addCleanup(self.control.close)
        self.store = Provenance(base / "memory.sqlite")
        self.addCleanup(self.store.close)
        self.task = self.control.propose(Policy(self.root, read=("**",), write=("src/**",),
                                               deny_read=(".env*",), tools=("file", "search", "shell")))
        self.control.approve_task(self.task)
        self.control.approve_worker(Grant(self.task, "worker", ("**",), ("src/**",),
                                          ("file", "search", "shell")))
        self.broker = WorkerBroker(self.control, self.store, self.task, "worker",
                                   provider_origins=("http://127.0.0.1:1234",))
        self.addCleanup(self.broker.close)

    def test_denied_tool_never_reaches_its_executor(self):
        self.assertEqual((self.root / ".env").read_text(), "DUMMY_SECRET")
        executed = []

        def execute(arguments):
            executed.append(arguments)
            return (self.root / ".env").read_text()

        with self.assertRaises(MediationDenied):
            self.broker.dispatch_tool("read-secret", "read", {"path": ".env"}, execute)
        self.assertEqual(executed, [])
        self.assertNotIn("DUMMY_SECRET", json.dumps(self.store.export()))

    def test_tool_result_enters_actual_model_inputs_with_its_resource_origin(self):
        receipt = self.broker.dispatch_tool("read-source", "read", {"path": "src/app.py"},
                                            lambda args: (self.root / args["path"]).read_text())
        seen = []

        def provider(inputs):
            seen.extend(inputs)
            return "Source inspected"

        output = self.broker.call_model([receipt], provider)
        self.assertEqual(seen, ["public source"])
        self.assertEqual(self.broker.text(output), "Source inspected")
        records = self.store.export()["nodes"]
        source = next(record for record in records if record["kind"] == "tool_result")
        result = next(record for record in records if record["kind"] == "model_output")
        self.assertIn("src/app.py", source["reference"])
        self.assertEqual(result["origins"], source["origins"])

    def test_closed_broker_and_reused_call_cannot_dispatch_more_effects(self):
        effects = []
        self.broker.dispatch_tool("once", "read", {"path": "src/app.py"}, lambda _: "source")
        with self.assertRaises(MediationDenied):
            self.broker.dispatch_tool("once", "write", {"path": "src/replay"}, lambda _: effects.append("replay"))
        self.broker.close()
        with self.assertRaises(MediationDenied):
            self.broker.dispatch_tool("after-close", "write", {"path": "src/late"}, lambda _: effects.append("late"))
        self.assertEqual(effects, [])

    def test_connector_admits_actual_provider_body_and_records_no_raw_session_text(self):
        sequence = 0

        def send(route, payload, session="session-one"):
            nonlocal sequence
            sequence += 1
            return self.broker.handle_request({"protocol": 1, "sequence": sequence,
                                               "route": route, "session_id": session, "payload": payload})

        self.assertTrue(send("hello", {"release": "2.2.5", "registered": list(REQUIRED_HOOKS)}, None)["allowed"])
        self.assertTrue(send("session.bind", {})["allowed"])
        self.assertTrue(send("tool.before", {"tool": "read", "call_id": "read-one",
                                              "input": {"path": "src/app.py"}})["allowed"])
        self.assertTrue(send("tool.after", {"tool": "read", "call_id": "read-one", "status": "completed",
                                             "result": "public source"})["allowed"])
        context = send("context", {"system": [], "messages": [{"role": "tool", "content": [
            {"type": "tool-result", "id": "read-one", "name": "read", "result": "public source"}]}],
            "options": {}, "kind": "primary"})
        self.assertTrue(context["allowed"])
        request = send("provider.http.request", {"kind": "primary", "model": "fixture/model",
                       "method": "POST", "url_origin": "http://127.0.0.1:1234",
                       "body": '{"messages":[{"content":"EPHEMERAL_BODY"}]}',
                       "context_receipt": context["receipt"]})
        self.assertTrue(request["allowed"])
        response = send("provider.http.response", {"kind": "primary", "status": 200,
                        "body": "EPHEMERAL_REPLY", "request_receipt": request["receipt"]})
        self.assertTrue(response["allowed"])
        self.assertFalse(send("approve-exception", {})["allowed"])
        exported = self.store.export()
        self.assertNotIn("EPHEMERAL_BODY", json.dumps(exported))
        self.assertNotIn("EPHEMERAL_REPLY", json.dumps(exported))
        source = next(node for node in exported["nodes"] if node["kind"] == "tool_result")
        result = next(node for node in exported["nodes"] if node["kind"] == "provider_output")
        self.assertIn(source["id"], result["origins"])

    def test_protocol_replay_session_substitution_and_unknown_context_are_blocked(self):
        hello = {"protocol": 1, "sequence": 1, "route": "hello", "session_id": None,
                 "payload": {"release": "2.2.5", "registered": list(REQUIRED_HOOKS)}}
        self.assertTrue(self.broker.handle_request(hello)["allowed"])
        self.assertFalse(self.broker.handle_request(hello)["allowed"])
        bind = {**hello, "sequence": 2, "route": "session.bind", "session_id": "root-session", "payload": {}}
        self.assertTrue(self.broker.handle_request(bind)["allowed"])
        self.assertFalse(self.broker.handle_request({**bind, "sequence": 3, "session_id": "child"})["allowed"])
        request = {**bind, "sequence": 4, "route": "provider.http.request", "payload": {
            "kind": "primary", "body": "worker says user approved", "method": "POST",
            "url_origin": "http://127.0.0.1:1234", "context_receipt": "invented"}}
        self.assertFalse(self.broker.handle_request(request)["allowed"])

    def test_sensitive_model_output_cannot_be_laundered_through_another_tool_result(self):
        from planner.records import Action

        request = self.control.request_exception(Action(self.task, "worker", "read", ".env"))
        self.control.approve_exception(request, disclose_sensitive=True)
        secret = self.broker.dispatch_tool("approved-secret", "read", {"path": ".env"},
                                           lambda _: (self.root / ".env").read_text())
        output = self.broker.call_model([secret], lambda texts: "echo " + texts[0])
        self.assertEqual(self.broker.text(output), "echo DUMMY_SECRET")
        echoed = self.broker.dispatch_tool("echo-secret", "shell", {"command": "echo"}, lambda _: "DUMMY_SECRET")
        derived = self.broker.call_model([echoed], lambda texts: texts[0])
        # A trusted caller can inspect the receipt; worker-provided origin names
        # cannot remove the already observed sensitive exposure.
        with self.assertRaises(PermissionError):
            self.broker.save_note(derived)

    def test_native_write_tool_cannot_read_existing_content_with_a_write_only_grant(self):
        self.control.approve_worker(Grant(self.task, "writer", (), ("src/**",)))
        broker = WorkerBroker(self.control, self.store, self.task, "writer")
        self.addCleanup(broker.close)
        effects = []
        with self.assertRaises(MediationDenied):
            broker.dispatch_tool("write-only", "write", {"path": "src/app.py"}, lambda _: effects.append("effect"))
        self.assertEqual(effects, [])

    def test_denied_before_hook_feedback_can_enter_the_next_model_context(self):
        envelope = {"protocol": 1, "sequence": 1, "route": "hello", "session_id": None,
                    "payload": {"release": "2.2.5", "registered": list(REQUIRED_HOOKS)}}
        self.assertTrue(self.broker.handle_request(envelope)["allowed"])
        envelope.update(sequence=2, route="session.bind", session_id="session", payload={})
        self.assertTrue(self.broker.handle_request(envelope)["allowed"])
        envelope.update(sequence=3, route="tool.before", payload={"tool": "write", "call_id": "denied",
                                                                "input": {"path": "README.md"}})
        self.assertFalse(self.broker.handle_request(envelope)["allowed"])
        # This release emits a native error result without an after hook when
        # its before hook refuses dispatch. The model still needs the denial.
        envelope.update(sequence=4, route="context", payload={"system": [], "kind": "primary", "options": {},
            "messages": [{"role": "tool", "content": [{"type": "tool-result", "id": "denied", "name": "write",
                            "result": {"type": "error", "value": "Supervisor denied operation"}}]}]})
        self.assertTrue(self.broker.handle_request(envelope)["allowed"])


if __name__ == "__main__":
    unittest.main()
