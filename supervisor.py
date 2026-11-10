"""Trusted control plane. Never expose this API or its database to workers.

Authorization decisions are not filesystem confinement. Execution additionally
requires a verified OS backend and connector, before any worker is launched.
"""

from dataclasses import asdict
from pathlib import Path
import json
import math
import sqlite3
import time
from typing import Any, Callable
from uuid import uuid4

from .policy import Policy, canonical_path
from .records import Action, Decision, Grant
from .connector import Connector, ConnectorConfig
from .enforcement import Backend, ProtectionUnavailable


def _decode_grant(encoded: str) -> Grant:
    item = json.loads(encoded)
    return Grant(item["task_id"], item["worker_id"], tuple(item["read"]),
                 tuple(item["write"]), tuple(item["tools"]), item["parent_id"])


class Supervisor:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time,
                 task_seconds: float = 3600, denial_limit: int = 3) -> None:
        if not math.isfinite(task_seconds) or task_seconds <= 0 or denial_limit < 1:
            raise ValueError("Task lifetime and denial limit must be positive")
        self.path = canonical_path(path)
        self.clock, self.task_seconds, self.denial_limit = clock, task_seconds, denial_limit
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, policy TEXT NOT NULL, state TEXT NOT NULL,
                revision INTEGER NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS workers (
                task TEXT NOT NULL REFERENCES tasks(id), id TEXT NOT NULL,
                grant_json TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY(task,id));
            CREATE TABLE IF NOT EXISTS policies (
                task TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL,
                policy TEXT NOT NULL, created REAL NOT NULL, approved REAL,
                PRIMARY KEY(task,revision));
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, worker TEXT NOT NULL,
                operation TEXT NOT NULL, resource TEXT NOT NULL, tool TEXT NOT NULL,
                allowed INTEGER NOT NULL, reason TEXT NOT NULL, approval TEXT,
                created REAL NOT NULL, grant_revision INTEGER);
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, task TEXT NOT NULL REFERENCES tasks(id),
                worker TEXT NOT NULL, operation TEXT NOT NULL, resource TEXT NOT NULL,
                tool TEXT NOT NULL, lifetime TEXT NOT NULL, expires REAL NOT NULL,
                state TEXT NOT NULL, sensitive INTEGER NOT NULL, identity TEXT NOT NULL,
                revision INTEGER NOT NULL, sources TEXT NOT NULL);
        """)
        # Preserve earlier records without inventing historic grant evidence.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(decisions)")}
            if "grant_revision" not in columns:
                self.db.execute("ALTER TABLE decisions ADD COLUMN grant_revision INTEGER")

    def close(self) -> None:
        self.db.close()

    def propose(self, policy: Policy) -> str:
        if self.path.is_relative_to(policy.root):
            raise ValueError("Supervisor state must be outside the project")
        task_id = uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES (?,?,?,1,?)",
                            (task_id, json.dumps(policy.as_dict()), "proposed",
                             self.clock() + self.task_seconds))
            self.db.execute("INSERT INTO policies VALUES (?,1,?,?,NULL)",
                            (task_id, json.dumps(policy.as_dict()), self.clock()))
        return task_id

    def revise(self, task_id: str, policy: Policy) -> None:
        """Replace the proposal, revoke assignments, and require fresh user review."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            status = self.status(task_id)
            if status["state"] == "cancelled" or status["expires"] <= self.clock():
                raise ValueError("Cancelled or expired task cannot be revised")
            if str(policy.root) != status["policy"]["root"] or self.path.is_relative_to(policy.root):
                raise ValueError("Policy revision cannot change project root")
            revision = status["revision"] + 1
            encoded = json.dumps(policy.as_dict())
            self.db.execute("UPDATE tasks SET policy=?,revision=?,state='proposed' WHERE id=?",
                            (encoded, revision, task_id))
            self.db.execute("INSERT INTO policies VALUES (?,?,?,?,NULL)",
                            (task_id, revision, encoded, self.clock()))
            self.db.execute("UPDATE workers SET state='revoked' WHERE task=?", (task_id,))
            self.db.execute("UPDATE approvals SET state='revoked' WHERE task=?", (task_id,))

    def status(self, task_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown task")
        return {"id": row["id"], "state": row["state"], "revision": row["revision"],
                "expires": row["expires"], "policy": json.loads(row["policy"])}

    def policy(self, task_id: str) -> Policy:
        config = self.status(task_id)["policy"]
        policy = Policy(Path(config["root"]), read=tuple(config["read"]),
                      write=tuple(config["write"]), deny_read=tuple(config["deny_read"]),
                      deny_write=tuple(config["deny_write"]), tools=tuple(config["tools"]),
                      external_inputs=tuple(Path(p) for p in config["external_inputs"]))
        if list(policy.root_identity) != config["root_identity"]:
            raise ValueError("Approved project root was replaced")
        return policy

    def _active(self, task_id: str) -> None:
        status = self.status(task_id)
        if status["state"] != "approved" or status["expires"] <= self.clock():
            raise ValueError("Task is not approved or has expired")

    def approve_task(self, task_id: str, *, revision: int | None = None) -> None:
        """Called only by the trusted user channel after displaying this revision."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            status = self.status(task_id)
            if revision is not None and status["revision"] != revision:
                raise ValueError("Reviewed policy revision does not match")
            if status["state"] != "proposed" or status["expires"] <= self.clock():
                raise ValueError("Only a live proposal can be approved")
            self.db.execute("UPDATE tasks SET state='approved' WHERE id=?", (task_id,))
            self.db.execute("UPDATE policies SET approved=? WHERE task=? AND revision=?",
                            (self.clock(), task_id, status["revision"]))

    def grant(self, task_id: str, worker_id: str) -> Grant:
        row = self.db.execute("SELECT grant_json,state FROM workers WHERE task=? AND id=?",
                              (task_id, worker_id)).fetchone()
        if row is None or row["state"] != "active":
            raise ValueError("Worker has no active grant")
        return _decode_grant(row["grant_json"])

    def approve_worker(self, grant: Grant) -> None:
        """User-approved exact assignment; worker text cannot invoke this channel."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._active(grant.task_id)
            parent = self.grant(grant.task_id, grant.parent_id) if grant.parent_id else None
            self.policy(grant.task_id).validate_grant(grant, parent)
            if not grant.worker_id or grant.parent_id == grant.worker_id:
                raise ValueError("Invalid worker identity")
            self.db.execute("INSERT INTO workers VALUES (?,?,?,'active',?)",
                            (grant.task_id, grant.worker_id, json.dumps(asdict(grant)),
                             self.status(grant.task_id)["revision"]))

    def _protected(self, path: Path, operation: str) -> bool:
        state_files = (self.path, self.path.with_suffix(".memory.sqlite"))
        for state in state_files:
            if str(path) in (str(state), *(str(state) + suffix for suffix in ("-journal", "-wal", "-shm"))):
                return True
            if path.exists() and state.exists() and path.samefile(state):
                return True
        code = Path(__file__).parent.resolve()
        return operation == "write" and (path.is_relative_to(code) or code.is_relative_to(path))

    def request_exception(self, action: Action, *, lifetime: str = "once",
                          sources: tuple[str, ...] = ()) -> str:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._active(action.task_id)
            grant = self.grant(action.task_id, action.worker_id)
            if action.operation not in ("read", "write") or action.tool not in grant.tools:
                raise ValueError("Invalid operation or unassigned tool")
            if lifetime not in ("once", "task"):
                raise ValueError("Lifetime must be once or task")
            policy = self.policy(action.task_id)
            path, names = policy.normalize(action.resource)
            if self._protected(path, action.operation):
                raise ValueError("Supervisor code and state cannot be granted to workers")
            if action.operation == "write" and not path.is_relative_to(policy.root):
                raise ValueError("External inputs are read-only")
            if action.operation == "write" and path.is_file() and path.stat().st_nlink > 1:
                raise ValueError("Hard-linked write exceptions cannot protect other aliases")
            approval_id = uuid4().hex
            status = self.status(action.task_id)
            self.db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (approval_id, action.task_id, action.worker_id, action.operation,
                             str(path), action.tool, lifetime, status["expires"], "requested",
                                 policy.is_sensitive(names) or (path.is_file() and path.stat().st_nlink > 1),
                                 self._identity(path), status["revision"],
                             json.dumps(sources)))
        return approval_id

    def exception(self, approval_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown approval request")
        return dict(row)

    @staticmethod
    def _identity(path: Path) -> str:
        if path.exists():
            stat = path.stat()
            return f"file:{stat.st_dev}:{stat.st_ino}:{stat.st_nlink}"
        parent = path.parent.resolve(strict=True).stat()
        return f"new:{parent.st_dev}:{parent.st_ino}:{path.name}"

    def approve_exception(self, approval_id: str, *, disclose_sensitive: bool = False) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.exception(approval_id)
            self._active(item["task"])
            self.grant(item["task"], item["worker"])
            if item["state"] != "requested" or item["expires"] <= self.clock():
                raise ValueError("Approval request is no longer valid")
            if item["sensitive"] and item["operation"] == "read" and not disclose_sensitive:
                raise ValueError("Sensitive read may expose data to the model provider; explicit disclosure required")
            if item["revision"] != self.status(item["task"])["revision"]:
                raise ValueError("Policy revision has changed")
            if self._identity(Path(item["resource"])) != item["identity"]:
                raise ValueError("Resource changed since request")
            self.db.execute("UPDATE approvals SET state='approved' WHERE id=?", (approval_id,))

    def refuse_exception(self, approval_id: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.exception(approval_id)
            self.db.execute("UPDATE approvals SET state='revoked' WHERE id=?", (approval_id,))

    def _use_exception(self, action: Action, decision: Decision) -> Decision:
        if decision.allowed or action.operation not in ("read", "write"):
            return decision
        policy = self.policy(action.task_id)
        path, _ = policy.normalize(action.resource)
        rows = self.db.execute("""SELECT * FROM approvals WHERE task=? AND worker=?
            AND operation=? AND resource=? AND tool=? AND state='approved' AND expires>?
            ORDER BY rowid""", (action.task_id, action.worker_id, action.operation,
                                 str(path), action.tool, self.clock()))
        for item in rows:
            if item["revision"] != self.status(action.task_id)["revision"]:
                continue
            if item["identity"] != self._identity(path):
                continue
            if item["lifetime"] == "once":
                self.db.execute("UPDATE approvals SET state='consumed' WHERE id=?", (item["id"],))
            return Decision(True, "Exact user-approved exception", str(path),
                            decision.sensitive or bool(item["sensitive"]), item["id"])
        return decision

    def authorize(self, action: Action) -> Decision:
        # Serialize checking, single-use consumption, and the decision record.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            grant_revision = None
            try:
                self._active(action.task_id)
                grant = self.grant(action.task_id, action.worker_id)
                grant_revision = self.db.execute("SELECT revision FROM workers WHERE task=? AND id=?",
                                                  (action.task_id, action.worker_id)).fetchone()[0]
                policy = self.policy(action.task_id)
                decision = policy.decide(action, grant)
                path, _ = policy.normalize(action.resource)
                if self._protected(path, action.operation):
                    decision = Decision(False, "Supervisor code and state are protected", str(path))
                elif action.tool in grant.tools:
                    decision = self._use_exception(action, decision)
            except (ValueError, OSError, RuntimeError) as error:
                decision = Decision(False, str(error), action.resource)
            self.db.execute("INSERT INTO decisions VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                            (action.task_id, action.worker_id, action.operation, decision.resource,
                             action.tool, decision.allowed, decision.reason, decision.approval_id,
                             self.clock(), grant_revision))
            if not decision.allowed:
                last = self.db.execute("""SELECT allowed,operation,resource,tool FROM decisions
                    WHERE task=? AND worker=? ORDER BY id DESC LIMIT ?""",
                    (action.task_id, action.worker_id, self.denial_limit)).fetchall()
                if len(last) == self.denial_limit and all(
                    not item["allowed"] and (item["operation"], item["resource"], item["tool"]) ==
                    (action.operation, decision.resource, action.tool) for item in last
                ):
                    self.db.execute("UPDATE workers SET state='suspended' WHERE task=? AND id=?",
                                    (action.task_id, action.worker_id))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return decision

    def history(self, task_id: str) -> list[dict[str, Any]]:
        self.status(task_id)
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM decisions WHERE task=? ORDER BY id", (task_id,))]

    def revoke_worker(self, task_id: str, worker_id: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.status(task_id)
            affected = {worker_id}
            rows = self.db.execute("SELECT id,grant_json FROM workers WHERE task=?", (task_id,)).fetchall()
            while True:
                children = {row["id"] for row in rows if json.loads(row["grant_json"])["parent_id"] in affected}
                if children <= affected:
                    break
                affected.update(children)
            for worker in affected:
                self.db.execute("UPDATE workers SET state='revoked' WHERE task=? AND id=?", (task_id, worker))
                self.db.execute("UPDATE approvals SET state='revoked' WHERE task=? AND worker=?",
                                (task_id, worker))

    def cancel(self, task_id: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.status(task_id)
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE id=?", (task_id,))
            self.db.execute("UPDATE workers SET state='revoked' WHERE task=?", (task_id,))
            self.db.execute("UPDATE approvals SET state='revoked' WHERE task=?", (task_id,))

    def preflight(self, task_id: str, worker_id: str, model: str) -> list[dict[str, Any]]:
        self._active(task_id)
        grant = self.grant(task_id, worker_id)
        return [asdict(Backend().check(self.policy(task_id), grant)),
                asdict(Connector(ConnectorConfig(model)).check())]

    def resume(self, task_id: str, worker_id: str, model: str) -> None:
        """Recovery cannot re-enable work without verifying every protection."""
        self._active(task_id)
        row = self.db.execute("SELECT grant_json,state FROM workers WHERE task=? AND id=?",
                              (task_id, worker_id)).fetchone()
        if row is None or row["state"] == "revoked":
            raise ValueError("Worker grant was revoked or is missing")
        grant = _decode_grant(row["grant_json"])
        policy = self.policy(task_id)
        for status in (Backend().check(policy, grant), Connector(ConnectorConfig(model)).check()):
            if not status.available or not status.verified:
                raise ProtectionUnavailable(status)
        # No execution integration is installed yet. Do not reset suspended grants.
        raise RuntimeError("BLOCKED: worker replacement and connector execution are not implemented")

    def export(self, task_id: str) -> dict[str, Any]:
        return {"tasks": [self.status(task_id)],
                "policy_revisions": [{"task": row["task"], "revision": row["revision"],
                                      "policy": json.loads(row["policy"]), "created": row["created"],
                                      "approved": row["approved"]} for row in self.db.execute(
                    "SELECT * FROM policies WHERE task=? ORDER BY revision", (task_id,))],
                "workers": [{"task": row["task"], "id": row["id"],
                             "grant": json.loads(row["grant_json"]), "state": row["state"],
                             "revision": row["revision"]}
                            for row in self.db.execute("SELECT * FROM workers WHERE task=?", (task_id,))],
                "decisions": self.history(task_id),
                "approvals": [dict(row) for row in self.db.execute(
                    "SELECT * FROM approvals WHERE task=? ORDER BY rowid", (task_id,))]}
