"""Trusted, provider-independent admission of actual in-memory model inputs.

This module records exposure and derivation, not authority or causal influence.
Its caller must be the supervisor's trusted connector, never a worker tool.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .provenance import Note, Provenance, Record


class ContextTransportError(RuntimeError):
    """A sanitized failure; provider exception text can contain sensitive inputs."""


@dataclass(frozen=True, eq=False)
class ContextHandle:
    """Context-local identity; knowing a record ID does not mint a valid handle."""

    record_id: str


@dataclass(frozen=True, eq=False)
class AdmittedCall:
    record_id: str
    inputs: tuple[str, ...] = field(repr=False)


@dataclass
class _Entry:
    record: Record
    text: str = field(repr=False)
    notes: frozenset[tuple[str, str]] = frozenset()


@dataclass
class _Pending:
    record: Record
    notes: frozenset[tuple[str, str]]


class ModelContext:
    def __init__(self, store: Provenance, task_id: str, worker_id: str) -> None:
        if not task_id or not worker_id:
            raise ValueError("Task and worker identifiers are required")
        self.store, self.task_id, self.worker_id = store, task_id, worker_id
        self._entries: dict[ContextHandle, _Entry] = {}
        self._pending: dict[AdmittedCall, _Pending] = {}
        self._closed = False

    def _active(self) -> None:
        if self._closed:
            raise PermissionError("Context has expired")

    def _entry(self, handle: ContextHandle) -> _Entry:
        self._active()
        if not isinstance(handle, ContextHandle) or handle not in self._entries:
            raise PermissionError("Unknown or foreign context handle")
        entry = self._entries[handle]
        self._check_notes(entry.notes)
        return entry

    def _check_notes(self, notes: frozenset[tuple[str, str]]) -> None:
        for project_id, note_id in notes:
            try:
                self.store.get_note(project_id, note_id)
            except KeyError:
                raise PermissionError("A source note has been deleted") from None

    def _keep(self, record: Record, text: str,
              notes: frozenset[tuple[str, str]] = frozenset()) -> ContextHandle:
        handle = ContextHandle(record.id)
        self._entries[handle] = _Entry(record, text, notes)
        return handle

    def observe(self, text: str, kind: str, reference: str, sensitive: bool = False) -> ContextHandle:
        self._active()
        if not isinstance(text, str):
            raise ValueError("Context text must be a string")
        record = self.store.record_input(self.task_id, self.worker_id, kind, reference, sensitive)
        return self._keep(record, text)

    def record(self, handle: ContextHandle) -> Record:
        return self._entry(handle).record

    def text(self, handle: ContextHandle) -> str:
        return self._entry(handle).text

    def save_note(self, project_id: str, handle: ContextHandle) -> Note:
        entry = self._entry(handle)
        return self.store.add_note(project_id, self.task_id, self.worker_id,
                                   entry.text, [entry.record.id])

    def retrieve_note(self, project_id: str, note_id: str) -> ContextHandle:
        self._active()
        record = self.store.retrieve_note(project_id, note_id, self.task_id, self.worker_id)
        note = self.store.get_note(project_id, note_id)
        return self._keep(record, note.text, frozenset(((project_id, note_id),)))

    def receive(self, source: "ModelContext", handle: ContextHandle) -> ContextHandle:
        """Transfer a report only after the trusted supervisor approves its exposure."""
        self._active()
        if source.store is not self.store or source.task_id != self.task_id:
            raise PermissionError("Worker reports must remain within the same task and provenance store")
        entry = source._entry(handle)
        record = self.store.derive(self.task_id, self.worker_id, "worker_report", [entry.record.id])
        return self._keep(record, entry.text, entry.notes)

    def admit(self, handles: Sequence[ContextHandle], *, kind: str = "model_call") -> AdmittedCall:
        self._active()
        entries = [self._entry(handle) for handle in handles]
        record = self.store.derive(self.task_id, self.worker_id, kind,
                                   [entry.record.id for entry in entries])
        call = AdmittedCall(record.id, tuple(entry.text for entry in entries))
        self._pending[call] = _Pending(record, frozenset(note for entry in entries for note in entry.notes))
        return call

    def complete(self, call: AdmittedCall, output: str, *, kind: str = "model_output") -> ContextHandle:
        self._active()
        if not isinstance(call, AdmittedCall) or call not in self._pending:
            raise PermissionError("Unknown, foreign, or completed model call")
        if not isinstance(output, str):
            raise ValueError("Model output must be a string")
        pending = self._pending[call]
        self._check_notes(pending.notes)
        record = self.store.derive(self.task_id, self.worker_id, kind, [call.record_id])
        del self._pending[call]
        return self._keep(record, output, pending.notes)

    def call(self, handles: Sequence[ContextHandle], transport: Callable[[tuple[str, ...]], str],
             *, kind: str = "model_call") -> ContextHandle:
        call = self.admit(handles, kind=kind)
        try:
            output = transport(call.inputs)
        except Exception:
            self.fail(call)
            raise ContextTransportError("Model transport failed; input exposure remains recorded") from None
        return self.complete(call, output)

    def fail(self, call: AdmittedCall) -> Record:
        self._active()
        if not isinstance(call, AdmittedCall) or call not in self._pending:
            raise PermissionError("Unknown, foreign, or completed model call")
        record = self.store.derive(self.task_id, self.worker_id, "model_call_failed", [call.record_id])
        del self._pending[call]
        return record

    def close(self) -> None:
        self._entries.clear()
        self._pending.clear()
        self._closed = True
