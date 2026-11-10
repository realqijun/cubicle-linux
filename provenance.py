"""Trusted metadata store: origins inform work but never authorize access."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


@dataclass(frozen=True)
class Record:
    id: str
    task_id: str
    worker_id: str
    kind: str
    reference: str | None
    origins: tuple[str, ...]
    sensitive: bool
    created_at: float


@dataclass(frozen=True)
class Note:
    id: str
    project_id: str
    task_id: str
    worker_id: str
    text: str
    source_ids: tuple[str, ...]
    created_at: float


class Provenance:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA secure_delete = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                worker_id TEXT NOT NULL, kind TEXT NOT NULL, reference TEXT,
                origins TEXT NOT NULL, sensitive INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS edges (
                source TEXT NOT NULL REFERENCES records(id),
                target TEXT NOT NULL REFERENCES records(id),
                PRIMARY KEY(source, target)
            );
            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY REFERENCES records(id),
                project_id TEXT NOT NULL, text TEXT,
                deleted_at REAL
            );
        """)

    def close(self) -> None:
        self.db.close()

    def register_task(self, task_id: str, project_id: str) -> None:
        if not task_id or not project_id:
            raise ValueError("Task and project identifiers must not be empty")
        with self.db:
            row = self.db.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is not None and row[0] != project_id:
                raise ValueError("A registered task cannot change project")
            self.db.execute("INSERT OR IGNORE INTO tasks VALUES (?, ?)", (task_id, project_id))

    def _project(self, task_id: str) -> str:
        row = self.db.execute("SELECT project_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown task")
        return str(row[0])

    def get_record(self, record_id: str) -> Record:
        row = self.db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown provenance record")
        return Record(str(row["id"]), str(row["task_id"]), str(row["worker_id"]),
                      str(row["kind"]), row["reference"],
                      tuple(str(origin) for origin in json.loads(row["origins"])),
                      bool(row["sensitive"]), float(row["created_at"]))

    def _insert(self, record: Record, input_ids: tuple[str, ...] = ()) -> None:
        self.db.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (record.id, record.task_id, record.worker_id, record.kind,
                         record.reference, json.dumps(record.origins),
                         record.sensitive, record.created_at))
        self.db.executemany("INSERT INTO edges VALUES (?, ?)",
                            ((source, record.id) for source in set(input_ids)))

    def record_input(self, task_id: str, worker_id: str, kind: str,
                     reference: str, sensitive: bool = False) -> Record:
        if not worker_id or not kind or not reference:
            raise ValueError("Worker, kind, and resource reference must not be empty")
        self._project(task_id)
        record_id = uuid4().hex
        record = Record(record_id, task_id, worker_id, kind, reference,
                        (record_id,), sensitive, time.time())
        with self.db:
            self._insert(record)
        return record

    def derive(self, task_id: str, worker_id: str, kind: str,
               input_ids: list[str] | tuple[str, ...]) -> Record:
        record = self._derivation(task_id, worker_id, kind, input_ids)
        with self.db:
            self._insert(record, tuple(input_ids))
        return record

    def _derivation(self, task_id: str, worker_id: str, kind: str,
                    input_ids: list[str] | tuple[str, ...]) -> Record:
        if not worker_id or not kind:
            raise ValueError("Worker and kind must not be empty")
        if not input_ids:
            raise ValueError("A derivation requires observed inputs")
        project_id = self._project(task_id)
        inputs = [self.get_record(record_id) for record_id in input_ids]
        if any(self._project(item.task_id) != project_id for item in inputs):
            raise PermissionError("Origins cannot cross project boundaries")
        return Record(uuid4().hex, task_id, worker_id, kind, None,
                      tuple(sorted({origin for item in inputs for origin in item.origins})),
                      any(item.sensitive for item in inputs), time.time())

    def add_note(self, project_id: str, task_id: str, worker_id: str,
                 text: str, input_ids: list[str] | tuple[str, ...]) -> Note:
        if self._project(task_id) != project_id:
            raise PermissionError("Note project differs from task project")
        record = self._derivation(task_id, worker_id, "note", input_ids)
        if record.sensitive:
            raise PermissionError("Sensitive-derived text must not be persisted")
        with self.db:
            self._insert(record, tuple(input_ids))
            self.db.execute("INSERT INTO notes VALUES (?, ?, ?, NULL)",
                            (record.id, project_id, text))
        return Note(record.id, project_id, task_id, worker_id, text,
                    tuple(sorted(set(input_ids))), record.created_at)

    def get_note(self, project_id: str, note_id: str) -> Note:
        row = self.db.execute("""SELECT * FROM notes WHERE id=? AND project_id=?
                                 AND deleted_at IS NULL""", (note_id, project_id)).fetchone()
        if row is None:
            raise KeyError("Unknown or deleted project note")
        record = self.get_record(note_id)
        sources = self.db.execute("SELECT source FROM edges WHERE target=? ORDER BY source",
                                  (note_id,)).fetchall()
        return Note(record.id, project_id, record.task_id, record.worker_id,
                    str(row["text"]), tuple(str(item[0]) for item in sources), record.created_at)

    def list_notes(self, project_id: str, query: str = "") -> list[Note]:
        rows = self.db.execute("""SELECT id FROM notes WHERE project_id=?
                                  AND deleted_at IS NULL AND instr(lower(text), lower(?)) > 0
                                  ORDER BY rowid""", (project_id, query)).fetchall()
        return [self.get_note(project_id, str(row[0])) for row in rows]

    def retrieve_note(self, project_id: str, note_id: str, task_id: str,
                      worker_id: str) -> Record:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.get_note(project_id, note_id)
            if self._project(task_id) != project_id:
                raise PermissionError("Notes cannot be retrieved into another project")
            derived = self._derivation(task_id, worker_id, "memory_retrieval", [note_id])
            record = Record(derived.id, task_id, worker_id, derived.kind, note_id,
                            derived.origins, derived.sensitive, derived.created_at)
            self._insert(record, (note_id,))
        return record

    def delete_note(self, project_id: str, note_id: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.get_note(project_id, note_id)
            self.db.execute("UPDATE notes SET text=NULL, deleted_at=? WHERE id=?",
                            (time.time(), note_id))

    def export(self, task_id: str | None = None) -> dict[str, object]:
        if task_id is not None:
            self._project(task_id)
        records = [self.get_record(str(row[0])) for row in
                   self.db.execute("SELECT id FROM records ORDER BY rowid")]
        edges = [{"source": str(row[0]), "target": str(row[1]), "kind": "information"}
                 for row in self.db.execute("SELECT source, target FROM edges ORDER BY rowid")]
        selected = {record.id for record in records
                    if task_id is None or record.task_id == task_id}
        # A task-filtered export retains source ancestors across same-project tasks.
        while True:
            ancestors = {edge["source"] for edge in edges if edge["target"] in selected}
            if ancestors <= selected:
                break
            selected.update(ancestors)
        nodes = [asdict(record) for record in records if record.id in selected]
        task_ids = {record.task_id for record in records if record.id in selected}
        if task_id is not None:
            task_ids.add(task_id)
        tasks = [{"id": str(row[0]), "project_id": str(row[1])} for row in
                 self.db.execute("SELECT id, project_id FROM tasks ORDER BY rowid")
                 if task_id is None or row[0] in task_ids]
        notes = []
        for row in self.db.execute("SELECT * FROM notes ORDER BY rowid"):
            if row["id"] in selected:
                notes.append({"id": str(row["id"]), "project_id": str(row["project_id"]),
                              "text": row["text"], "deleted_at": row["deleted_at"]})
        return {"tasks": tasks, "nodes": nodes,
                "edges": [edge for edge in edges if edge["target"] in selected],
                "notes": notes,
                "meaning": "Edges record observed exposure or derivation, not proven causal influence."}
