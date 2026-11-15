"""Worker-bound decisions and context admission for the trusted connector.

This is not an OS sandbox. A normal launcher must establish OS confinement
before allowing OpenCode to execute any prepared operation.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
import sqlite3
import threading
from typing import Any
from uuid import uuid4

from .context import AdmittedCall, ContextHandle, ModelContext
from .connector import PINNED_VERSION, REQUIRED_HOOKS
from .native.mediatedfs import INPUTS
from .provenance import Note, Provenance
from .records import Action, Decision, Operation
from .supervisor import Supervisor

ROUTES = frozenset(("hello", "session.bind", "prompt", "tool.before", "tool.after", "permission.evaluate",
                   "shell.before", "context", "compaction", "generate", "title", "model.request",
                   "provider.http.request", "provider.http.response", "bridge.denial"))


class MediationDenied(PermissionError):
    """A fixed reason code suitable for worker responses and ordinary logs."""


@dataclass
class _Tool:
    name: str
    arguments: dict[str, Any] = field(repr=False)
    decisions: tuple[Decision, ...] = ()


@dataclass
class _Request:
    call: AdmittedCall
    kind: str


class WorkerBroker:
    def __init__(self, supervisor: Supervisor, store: Provenance,
                 task_id: str, worker_id: str, *, provider_origins: tuple[str, ...] = (),
                 worker_project: str | None = None, sensitive_exposure: threading.Event | None = None,
                 external_inputs: dict[str, str] | None = None) -> None:
        supervisor.check_worker(task_id, worker_id)
        self.supervisor, self.task_id, self.worker_id = supervisor, task_id, worker_id
        self.project = str(supervisor.policy(task_id).root)
        if worker_project is not None and (not worker_project.startswith("/") or
                ".." in PurePosixPath(worker_project).parts):
            raise ValueError("Worker project must be an absolute normalized POSIX path")
        self.worker_project = PurePosixPath(worker_project) if worker_project else None
        # Shell tools hide which file they read; once the confined filesystem
        # served an approved sensitive read, all later tool output is sensitive.
        self.sensitive_exposure = sensitive_exposure
        # Worker-visible copy path -> approved external source, so decisions and
        # provenance name the true resource rather than a project-looking path.
        self.external_inputs = dict(external_inputs or {})
        store.register_task(task_id, self.project)
        self.context = ModelContext(store, task_id, worker_id)
        self._tools: dict[str, _Tool] = {}
        self._handles: dict[str, ContextHandle] = {}
        self._calls: dict[str, _Request] = {}
        self._seen_tools: set[str] = set()
        self._closed = False
        self.provider_origins = provider_origins
        self._hello = False
        self._session: str | None = None
        self._sequence = 0
        self._tool_outputs: dict[str, ContextHandle] = {}
        self._snapshots: dict[str, str] = {}
        self._last_output: ContextHandle | None = None
        self._messages: dict[str, ContextHandle] = {}
        self._output_receipts: dict[str, str] = {}

    @property
    def hooks_ready(self) -> bool:
        """True only after an authenticated compatible hook inventory arrived."""
        return self._hello and not self._closed

    def _resource(self, resource: str) -> str:
        if self.worker_project is None:
            return resource
        path = PurePosixPath(resource)
        if ".." in path.parts:
            raise MediationDenied("outside_worker_project")
        if not path.is_absolute():
            path = self.worker_project / path
        if str(path) in self.external_inputs:
            return self.external_inputs[str(path)]
        if path.parts[:len(self.worker_project.parts) + 1] == (*self.worker_project.parts, INPUTS):
            raise MediationDenied("unbound_external_input")
        if path.is_absolute():
            try:
                return path.relative_to(self.worker_project).as_posix()
            except ValueError:
                raise MediationDenied("outside_worker_project") from None
        return resource

    def _active(self, tool: str | None = None) -> None:
        if self._closed:
            raise MediationDenied("expired_broker")
        try:
            self.supervisor.check_worker(self.task_id, self.worker_id, tool)
        except (ValueError, OSError):
            raise MediationDenied("inactive_worker") from None

    def _keep(self, handle: ContextHandle) -> str:
        receipt = uuid4().hex
        self._handles[receipt] = handle
        return receipt

    def _handle(self, receipt: str) -> ContextHandle:
        self._active()
        if receipt not in self._handles:
            raise MediationDenied("unknown_input_receipt")
        return self._handles[receipt]

    def before_tool(self, call_id: str, tool: str, arguments: dict[str, Any]) -> None:
        if not call_id or call_id in self._seen_tools:
            raise MediationDenied("duplicate_tool_call")
        if tool not in ("read", "write", "edit", "glob", "grep", "shell"):
            raise MediationDenied("unsupported_tool")
        category = "shell" if tool == "shell" else "search" if tool in ("glob", "grep") else "file"
        self._active(category)
        # Snapshot arguments; subsequent mutation must not substitute an operation.
        copied = json.loads(json.dumps(arguments))
        actions: list[Action] = []
        if tool != "shell":
            resource = copied.get("path", "." if category == "search" else None)
            if not isinstance(resource, str) or not resource:
                raise MediationDenied("missing_resource")
            # Pinned OpenCode write reads existing content for previews/BOM.
            operations: tuple[Operation, ...] = ("read", "write") if tool in ("edit", "write") else ("read",)
            for operation in operations:
                actions.append(Action(self.task_id, self.worker_id, operation, self._resource(resource), category))
        decisions = self.supervisor.authorize_many(actions) if actions else ()
        if any(not decision.allowed for decision in decisions):
            self._seen_tools.add(call_id)
            feedback = self.context.observe("file_access_denied", "supervisor_denial", f"opencode:{tool}")
            if self._last_output is not None:
                call = self.context.admit([self._last_output, feedback], kind="denial_feedback")
                feedback = self.context.complete(call, "file_access_denied", kind="supervisor_denial")
            self._tool_outputs[call_id] = feedback
            raise MediationDenied("file_access_denied")
        self._seen_tools.add(call_id)
        self._tools[call_id] = _Tool(tool, copied, decisions)

    def after_tool(self, call_id: str, result: Any, *, successful: bool = True) -> str:
        self._active()
        ticket = self._tools.pop(call_id, None)
        if ticket is None:
            raise MediationDenied("unknown_tool_call")
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=True)
        reference = ticket.name + ":" + ",".join(d.resource for d in ticket.decisions)
        sensitive = any(d.sensitive for d in ticket.decisions) or bool(
            self.sensitive_exposure is not None and self.sensitive_exposure.is_set())
        handle = self.context.observe(text, "tool_result" if successful else "tool_error",
                                      reference, sensitive)
        if self._last_output is not None:
            call = self.context.admit([self._last_output, handle], kind="tool_execution")
            handle = self.context.complete(call, text, kind="tool_result")
        self._tool_outputs[call_id] = handle
        return self._keep(handle)

    def dispatch_tool(self, call_id: str, tool: str, arguments: dict[str, Any],
                      executor: Callable[[dict[str, Any]], Any]) -> str:
        """For trusted adapters/tests; executor must run under the installed OS grant."""
        self.before_tool(call_id, tool, arguments)
        ticket = self._tools[call_id]
        try:
            result = executor(ticket.arguments)
        except Exception:
            self.after_tool(call_id, "tool_execution_failed", successful=False)
            raise MediationDenied("tool_execution_failed") from None
        return self.after_tool(call_id, result)

    def call_model(self, receipts: Sequence[str], transport: Callable[[tuple[str, ...]], str]) -> str:
        handles = [self._handle(receipt) for receipt in receipts]
        self._last_output = self.context.call(handles, transport)
        return self._keep(self._last_output)

    def text(self, receipt: str) -> str:
        return self.context.text(self._handle(receipt))

    def save_note(self, receipt: str) -> Note:
        return self.context.save_note(self.project, self._handle(receipt))

    def handle_request(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Worker-only protocol: no user approval, grant installation, or recovery routes."""
        try:
            self._active()
            if set(envelope) != {"protocol", "sequence", "route", "session_id", "payload"}:
                raise MediationDenied("invalid_envelope")
            sequence = envelope["sequence"]
            if envelope["protocol"] != 1 or type(sequence) is not int or sequence != self._sequence + 1:
                raise MediationDenied("protocol_replay_or_gap")
            self._sequence = sequence
            route, payload = envelope["route"], envelope["payload"]
            if not isinstance(route, str) or not isinstance(payload, dict):
                raise MediationDenied("invalid_payload")
            if route == "hello":
                registered = payload.get("registered")
                if (self._hello or payload.get("release") != PINNED_VERSION or
                        not isinstance(registered, list) or
                        any(not isinstance(name, str) for name in registered) or
                        len(registered) != len(REQUIRED_HOOKS) or set(registered) != set(REQUIRED_HOOKS)):
                    raise MediationDenied("incompatible_bridge")
                self._hello = True
                return {"allowed": True, "reason": "bridge_registered"}
            if not self._hello:
                raise MediationDenied("bridge_not_registered")
            if route == "bridge.denial":
                raise MediationDenied("unsupported_connector_path")
            session = envelope["session_id"]
            if route == "session.bind":
                if not isinstance(session, str) or not session or self._session not in (None, session):
                    raise MediationDenied("unassigned_session")
                self._session = session
                return {"allowed": True, "reason": "session_bound"}
            if not self._session or session != self._session:
                raise MediationDenied("unassigned_session")
            receipt = self._route(route, payload)
            return {"allowed": True, "reason": "admitted", **({"receipt": receipt} if receipt else {})}
        except MediationDenied as error:
            return self._record_denial(envelope, str(error))
        except PermissionError:
            return self._record_denial(envelope, "context_unavailable")
        except (ValueError, TypeError, KeyError):
            return self._record_denial(envelope, "invalid_request")
        except (sqlite3.Error, OSError, RuntimeError):
            self.close()
            return {"allowed": False, "reason": "protection_unavailable"}

    def _record_denial(self, envelope: dict[str, Any], reason: str) -> dict[str, Any]:
        route = envelope.get("route")
        # The route itself is worker-controlled. Persist only known protocol names.
        safe_route = route if isinstance(route, str) and route in ROUTES else "unsupported_route"
        try:
            self.supervisor.record_mediation_denial(self.task_id, self.worker_id, safe_route, reason)
        except (sqlite3.Error, OSError, ValueError):
            self.close()
            return {"allowed": False, "reason": "protection_unavailable"}
        return {"allowed": False, "reason": reason}

    def _route(self, route: str, payload: dict[str, Any]) -> str | None:
        if route == "tool.before":
            if not isinstance(payload.get("input"), dict):
                raise MediationDenied("invalid_tool_arguments")
            self.before_tool(payload["call_id"], payload["tool"], payload["input"])
            return None
        if route == "tool.after":
            ticket = self._tools.get(payload["call_id"])
            if ticket is None or payload["tool"] != ticket.name or payload["status"] not in ("completed", "error"):
                raise MediationDenied("substituted_tool_result")
            return self.after_tool(payload["call_id"], payload.get("result", payload.get("error")),
                                   successful=payload["status"] == "completed")
        if route == "permission.evaluate":
            source = payload.get("source")
            if not isinstance(source, dict) or source.get("id") not in self._tools:
                raise MediationDenied("untracked_permission_request")
            ticket = self._tools[source["id"]]
            action, resources = payload.get("action"), payload.get("resources")
            if not isinstance(resources, list) or any(not isinstance(resource, str) for resource in resources):
                raise MediationDenied("invalid_permission_resources")
            if ticket.name in ("read", "write", "edit"):
                if action not in (("read",) if ticket.name == "read" else ("read", "edit")):
                    raise MediationDenied("substituted_permission_action")
                approved = {decision.resource for decision in ticket.decisions}
                policy = self.supervisor.policy(self.task_id)
                if not resources or any(str(policy.normalize(self._resource(resource))[0]) not in approved for resource in resources):
                    raise MediationDenied("substituted_permission_resource")
            elif action != ticket.name or (ticket.name != "shell" and resources != [ticket.arguments.get("pattern")]):
                raise MediationDenied("substituted_permission_action")
            # The trusted before hook already checked the exact input; native
            # permission evaluation must not consume a single-use exception twice.
            return None
        if route == "shell.before":
            self._active("shell")
            if not any(ticket.name == "shell" and ticket.arguments.get("command") == payload.get("command")
                       for ticket in self._tools.values()):
                raise MediationDenied("untracked_shell_execution")
            self.supervisor.policy(self.task_id).normalize(self._resource(payload["cwd"]))
            return None
        if route == "prompt":
            handle = self.context.observe(json.dumps(payload, ensure_ascii=True), "worker_input", "opencode:prompt")
            receipt = self._keep(handle)
            self._output_receipts[receipt] = "prompt"
            return receipt
        if route in ("context", "compaction", "generate", "title"):
            kind = "primary" if route == "context" else route
            messages = payload.get("messages")
            if not isinstance(messages, list) or not isinstance(payload.get("system"), list):
                raise MediationDenied("untracked_context_shape")
            inputs = []
            supplied = payload.get("message_sources", [])
            if not isinstance(supplied, list):
                raise MediationDenied("invalid_message_sources")
            current_ids = {message.get("id") for message in messages if isinstance(message, dict)
                           and isinstance(message.get("id"), str)}
            for source in supplied:
                if not isinstance(source, dict) or source.get("message_id") not in current_ids:
                    raise MediationDenied("unbound_message_source")
                source_receipt = source.get("receipt")
                if not isinstance(source_receipt, str) or self._output_receipts.get(source_receipt) != source.get("kind"):
                    raise MediationDenied("unknown_model_output_source")
                self._messages[source["message_id"]] = self._handle(source_receipt)
            for message in messages:
                if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                    raise MediationDenied("untracked_message_shape")
                message_id = message.get("id")
                if isinstance(message_id, str) and message_id in self._messages:
                    inputs.append(self._messages[message_id])
                elif message.get("role") == "assistant":
                    raise MediationDenied("untracked_assistant_message")
                for part in message["content"]:
                    if isinstance(part, dict) and part.get("type") == "tool-result":
                        source_id = part.get("id")
                        tool_handle = self._tool_outputs.get(source_id) if isinstance(source_id, str) else None
                        if tool_handle is None:
                            raise MediationDenied("untracked_tool_result")
                        inputs.append(tool_handle)
            observed = self.context.observe(json.dumps(payload, ensure_ascii=True),
                                            "harness_context", f"opencode:{kind}")
            inputs.append(observed)
            call = self.context.admit(inputs, kind="context_transform")
            snapshot = self.context.complete(call, self.context.text(observed), kind=f"{kind}_context")
            receipt = self._keep(snapshot)
            self._snapshots[receipt] = kind
            for message in messages:
                if isinstance(message.get("id"), str) and message["id"] not in self._messages:
                    self._messages[message["id"]] = snapshot
            return receipt
        if route in ("model.request", "provider.http.request"):
            kind = payload["kind"]
            context_receipt = payload["context_receipt"]
            if self._snapshots.get(context_receipt) != kind:
                raise MediationDenied("missing_context_admission")
            origin = payload.get("url_origin")
            if origin not in self.provider_origins:
                raise MediationDenied("unreviewed_provider_origin")
            if route == "model.request":
                return None
            if payload.get("method") != "POST" or not isinstance(payload.get("body"), str):
                raise MediationDenied("unsupported_provider_transport")
            raw = self.context.observe(payload["body"], "provider_body", "opencode:http-request")
            lowering = self.context.admit([self._handle(context_receipt), raw], kind="provider_lowering")
            body = self.context.complete(lowering, payload["body"], kind="provider_request")
            call = self.context.admit([body], kind="provider_call")
            receipt = uuid4().hex
            self._calls[receipt] = _Request(call, kind)
            return receipt
        if route == "provider.http.response":
            pending = self._calls.get(payload["request_receipt"])
            if pending is None or pending.kind != payload.get("kind") or not isinstance(payload.get("body"), str):
                raise MediationDenied("untracked_provider_response")
            completed = self.context.complete(pending.call, payload["body"], kind="provider_output")
            if pending.kind == "primary":
                self._last_output = completed
            del self._calls[payload["request_receipt"]]
            receipt = self._keep(completed)
            self._output_receipts[receipt] = pending.kind
            return receipt
        raise MediationDenied("unsupported_route")

    def close(self) -> None:
        for request in self._calls.values():
            try:
                self.context.fail(request.call)
            except (PermissionError, sqlite3.Error):
                # A storage failure is already blocking; never persist raw error text.
                pass
        self._tools.clear()
        self._handles.clear()
        self._calls.clear()
        self._tool_outputs.clear()
        self._snapshots.clear()
        self._last_output = None
        self._messages.clear()
        self._output_receipts.clear()
        self._closed = True
        self.context.close()
