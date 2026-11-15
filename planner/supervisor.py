"""Trusted control plane. Never expose this API or its database to workers.

Authorization decisions are not filesystem confinement. Execution additionally
requires a verified OS backend and connector, before any worker is launched.
"""

from dataclasses import asdict
from collections.abc import Sequence
from pathlib import Path
import json
import math
import sqlite3
import time
from typing import Any, Callable
from uuid import uuid4

from .policy import Policy, canonical_path
from .records import Action, Decision, Grant, ProtectionStatus
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
                template TEXT,
                PRIMARY KEY(task,id));
            CREATE TABLE IF NOT EXISTS delegation_templates (
                task TEXT NOT NULL REFERENCES tasks(id), name TEXT NOT NULL,
                parent TEXT NOT NULL, revision INTEGER NOT NULL, grant_json TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY(task,name,revision));
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, task TEXT NOT NULL REFERENCES tasks(id), worker TEXT NOT NULL,
                revision INTEGER NOT NULL, grant_json TEXT NOT NULL, expires REAL NOT NULL,
                state TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, checks TEXT NOT NULL, cleanup_verified INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS attempt_events (
                id INTEGER PRIMARY KEY, attempt TEXT NOT NULL REFERENCES attempts(id),
                state TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL,
                checks TEXT NOT NULL, cleanup_verified INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS policies (
                task TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL,
                policy TEXT NOT NULL, created REAL NOT NULL, approved REAL,
                PRIMARY KEY(task,revision));
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, worker TEXT NOT NULL,
                operation TEXT NOT NULL, resource TEXT NOT NULL, tool TEXT NOT NULL,
                allowed INTEGER NOT NULL, reason TEXT NOT NULL, approval TEXT,
                created REAL NOT NULL, grant_revision INTEGER);
            CREATE TABLE IF NOT EXISTS access_requests (
                id INTEGER PRIMARY KEY, task TEXT NOT NULL, worker TEXT NOT NULL,
                resources TEXT NOT NULL, allowed INTEGER NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, task TEXT NOT NULL REFERENCES tasks(id),
                worker TEXT NOT NULL, operation TEXT NOT NULL, resource TEXT NOT NULL,
                tool TEXT NOT NULL, lifetime TEXT NOT NULL, expires REAL NOT NULL,
                state TEXT NOT NULL, sensitive INTEGER NOT NULL, identity TEXT NOT NULL,
                revision INTEGER NOT NULL, sources TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS applications (
                attempt TEXT PRIMARY KEY REFERENCES attempts(id), task TEXT NOT NULL,
                worker TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
                changes TEXT NOT NULL, journal TEXT NOT NULL, updated REAL NOT NULL);
        """)
        # Preserve earlier records without inventing historic grant evidence.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(decisions)")}
            if "grant_revision" not in columns:
                self.db.execute("ALTER TABLE decisions ADD COLUMN grant_revision INTEGER")
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(workers)")}
            if "template" not in columns:
                self.db.execute("ALTER TABLE workers ADD COLUMN template TEXT")

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
            self.db.execute("UPDATE delegation_templates SET state='revoked' WHERE task=?", (task_id,))
            self._stop_attempts(task_id, None, "policy_revised")

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

    def worker_status(self, task_id: str, worker_id: str) -> dict[str, Any]:
        self.status(task_id)
        row = self.db.execute("SELECT * FROM workers WHERE task=? AND id=?", (task_id, worker_id)).fetchone()
        if row is None:
            raise ValueError("Unknown worker")
        return {"task": row["task"], "id": row["id"], "grant": json.loads(row["grant_json"]),
                "state": row["state"], "revision": row["revision"], "template": row["template"]}

    def workers(self, task_id: str) -> list[dict[str, Any]]:
        self.status(task_id)
        return [self.worker_status(task_id, row["id"]) for row in self.db.execute(
            "SELECT id FROM workers WHERE task=? ORDER BY rowid", (task_id,))]

    def check_worker(self, task_id: str, worker_id: str, tool: str | None = None) -> Grant:
        """Validate current authority without consuming approvals or recording access."""
        if self.db.in_transaction:
            return self._check_worker(task_id, worker_id, tool)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._check_worker(task_id, worker_id, tool)

    def _check_worker(self, task_id: str, worker_id: str, tool: str | None) -> Grant:
        self._active(task_id)
        revision = self.status(task_id)["revision"]
        policy = self.policy(task_id)
        grant = self.grant(task_id, worker_id)
        if tool is not None and tool not in grant.tools:
            raise ValueError("Tool is not assigned")
        current = grant
        while True:
            if self.worker_status(task_id, current.worker_id)["revision"] != revision:
                raise ValueError("Worker grant belongs to an obsolete policy revision")
            parent = self.grant(task_id, current.parent_id) if current.parent_id else None
            policy.validate_grant(current, parent)
            if parent is None:
                break
            current = parent
        return grant

    def _install_worker(self, grant: Grant, template: str | None = None) -> None:
        self._active(grant.task_id)
        parent = self.check_worker(grant.task_id, grant.parent_id) if grant.parent_id else None
        self.policy(grant.task_id).validate_grant(grant, parent)
        if not grant.worker_id or grant.parent_id == grant.worker_id:
            raise ValueError("Invalid worker identity")
        self.db.execute("INSERT INTO workers VALUES (?,?,?,'active',?,?)",
                        (grant.task_id, grant.worker_id, json.dumps(asdict(grant)),
                         self.status(grant.task_id)["revision"], template))

    def approve_worker(self, grant: Grant) -> None:
        """User-approved exact assignment; worker text cannot invoke this channel."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._install_worker(grant)

    def approve_delegation_template(self, task_id: str, name: str, parent_id: str, *,
                                    read: tuple[str, ...] = (), write: tuple[str, ...] = (),
                                    tools: tuple[str, ...] = ("file",)) -> dict[str, Any]:
        """Trusted operator approval of a parent-bound, revision-scoped preset."""
        if not name or name.strip() != name:
            raise ValueError("Template name must be non-empty without surrounding whitespace")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            parent = self.check_worker(task_id, parent_id)
            ceiling = Grant(task_id, f"template:{name}", read, write, tools, parent_id)
            self.policy(task_id).validate_grant(ceiling, parent)
            revision = self.status(task_id)["revision"]
            self.db.execute("INSERT INTO delegation_templates VALUES (?,?,?,?,?,'active',?)",
                            (task_id, name, parent_id, revision, json.dumps(asdict(ceiling)), self.clock()))
        return next(item for item in self.templates(task_id) if item["name"] == name and item["revision"] == revision)

    def templates(self, task_id: str) -> list[dict[str, Any]]:
        self.status(task_id)
        return [{"task": row["task"], "name": row["name"], "parent": row["parent"],
                 "revision": row["revision"], "grant": json.loads(row["grant_json"]),
                 "state": row["state"], "created": row["created"]} for row in self.db.execute(
                    "SELECT * FROM delegation_templates WHERE task=? ORDER BY rowid", (task_id,))]

    def delegate(self, grant: Grant, template_name: str) -> None:
        """Automatic assignment is possible only under an exact approved preset."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._active(grant.task_id)
            row = self.db.execute("""SELECT * FROM delegation_templates WHERE task=? AND name=?
                AND revision=? AND state='active'""", (grant.task_id, template_name,
                                                       self.status(grant.task_id)["revision"])).fetchone()
            if row is None or row["parent"] != grant.parent_id:
                raise ValueError("No approved delegation template for this parent and revision")
            ceiling = _decode_grant(row["grant_json"])
            self.policy(grant.task_id).validate_grant(
                Grant(grant.task_id, grant.worker_id, grant.read, grant.write, grant.tools, ceiling.worker_id), ceiling)
            self._install_worker(grant, template_name)

    def attempt(self, attempt_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown execution attempt")
        item = dict(row)
        item["grant"] = json.loads(item.pop("grant_json"))
        item["checks"] = json.loads(item["checks"])
        item["cleanup_verified"] = bool(item["cleanup_verified"])
        item["events"] = [{**dict(event), "checks": json.loads(event["checks"]),
                           "cleanup_verified": bool(event["cleanup_verified"])} for event in self.db.execute(
            "SELECT * FROM attempt_events WHERE attempt=? ORDER BY id", (attempt_id,))]
        return item

    def _audit_attempt(self, attempt_id: str) -> None:
        self.db.execute("""INSERT INTO attempt_events SELECT NULL,id,state,reason,updated,checks,cleanup_verified
            FROM attempts WHERE id=?""", (attempt_id,))

    def attempts(self, task_id: str) -> list[dict[str, Any]]:
        self.status(task_id)
        return [self.attempt(row["id"]) for row in self.db.execute(
            "SELECT id FROM attempts WHERE task=? ORDER BY rowid", (task_id,))]

    def prepare_attempt(self, task_id: str, worker_id: str) -> dict[str, Any]:
        """Record an intended run; this API starts no process."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            grant = self.check_worker(task_id, worker_id)
            pending = self.db.execute("""SELECT id FROM attempts WHERE task=? AND worker=?
                AND state IN ('prepared','verified','running','stopping','interrupted')""",
                (task_id, worker_id)).fetchone()
            if pending is not None:
                raise ValueError("Worker already has an unresolved execution attempt")
            task = self.status(task_id)
            attempt_id = uuid4().hex
            now = self.clock()
            self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,?,?,'prepared','awaiting_verification',?,?,?,0)",
                            (attempt_id, task_id, worker_id, task["revision"], json.dumps(asdict(grant)),
                             task["expires"], now, now, "[]"))
            self._audit_attempt(attempt_id)
        return self.attempt(attempt_id)

    def _attempt_authority(self, item: dict[str, Any]) -> None:
        grant = self.check_worker(item["task"], item["worker"])
        if (self.status(item["task"])["revision"] != item["revision"] or
                grant != _decode_grant(json.dumps(item["grant"])) or item["expires"] <= self.clock()):
            raise ValueError("Execution attempt authority is obsolete or expired")

    def verify_attempt(self, attempt_id: str, backend: ProtectionStatus,
                       connector: ProtectionStatus) -> dict[str, Any]:
        """Trusted runner records actual preflight results; no worker channel calls this."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.attempt(attempt_id)
            if item["state"] != "prepared":
                raise ValueError("Only a fresh prepared attempt can be verified")
            checks = [{"backend": status.backend, "available": status.available, "verified": status.verified}
                      for status in (backend, connector)]
            reason = "protection_verified"
            state = "verified"
            try:
                self._attempt_authority(item)
            except ValueError:
                state, reason = "blocked", "authority_invalid"
            if state == "verified" and not all(status.available and status.verified for status in (backend, connector)):
                state, reason = "blocked", "protection_unavailable"
            self.db.execute("UPDATE attempts SET state=?,reason=?,updated=?,checks=? WHERE id=?",
                            (state, reason, self.clock(), json.dumps(checks), attempt_id))
            self._audit_attempt(attempt_id)
        return self.attempt(attempt_id)

    def start_attempt(self, attempt_id: str) -> dict[str, Any]:
        """Record a verified dispatch owned by the confined runner; never launch here."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.attempt(attempt_id)
            if item["state"] != "verified":
                raise ValueError("Execution attempt has not passed verification")
            self._attempt_authority(item)
            self.db.execute("UPDATE attempts SET state='running',reason='dispatched',updated=? WHERE id=?",
                            (self.clock(), attempt_id))
            self._audit_attempt(attempt_id)
        return self.attempt(attempt_id)

    def abandon_attempt(self, attempt_id: str, reason: str) -> dict[str, Any]:
        """Close an attempt the runner never dispatched; no worker process was started."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.attempt(attempt_id)["state"] not in ("prepared", "verified"):
                raise ValueError("Only an undispatched attempt can be abandoned")
            self.db.execute("UPDATE attempts SET state='blocked',reason=?,updated=? WHERE id=?",
                            (reason[:200], self.clock(), attempt_id))
            self._audit_attempt(attempt_id)
        return self.attempt(attempt_id)

    def reconcile(self, task_id: str) -> list[dict[str, Any]]:
        """Record unresolved work after owner loss; never assume descendants stopped."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            pending = [item for item in self.attempts(task_id)
                       if item["state"] in ("prepared", "verified", "running", "stopping")]
            for item in pending:
                self.db.execute("""UPDATE attempts SET state='interrupted',reason='owner_lost',
                    cleanup_verified=0,updated=? WHERE id=?""", (self.clock(), item["id"]))
                self._audit_attempt(item["id"])
                self.db.execute("UPDATE workers SET state='suspended' WHERE task=? AND id=? AND state!='revoked'",
                                (task_id, item["worker"]))
                self.db.execute("""UPDATE approvals SET state='revoked' WHERE task=? AND worker=?
                    AND state IN ('requested','approved')""", (task_id, item["worker"]))
        return [self.attempt(item["id"]) for item in pending]

    def recover_attempt(self, attempt_id: str, backend: ProtectionStatus, connector: ProtectionStatus,
                        *, cleanup_verified: bool = False) -> dict[str, Any]:
        """Trusted recovery evidence closes an old attempt; replacement needs a new one."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.attempt(attempt_id)
            if item["state"] != "interrupted":
                raise ValueError("Only an interrupted attempt can be reconciled")
            reason = "cleanup_unverified"
            recovered = False
            if cleanup_verified:
                reason = "protection_unavailable"
                if all(status.available and status.verified for status in (backend, connector)):
                    reason = "authority_invalid"
                    worker = self.worker_status(item["task"], item["worker"])
                    if worker["state"] != "revoked":
                        self.db.execute("SAVEPOINT recovery")
                        self.db.execute("UPDATE workers SET state='active' WHERE task=? AND id=?",
                                        (item["task"], item["worker"]))
                        try:
                            self._attempt_authority(item)
                            recovered = True
                        except ValueError:
                            self.db.execute("ROLLBACK TO recovery")
                        self.db.execute("RELEASE recovery")
            checks = [{"backend": status.backend, "available": status.available, "verified": status.verified}
                      for status in (backend, connector)]
            self.db.execute("UPDATE attempts SET state=?,reason=?,updated=?,checks=?,cleanup_verified=? WHERE id=?",
                            ("reconciled" if recovered else "interrupted",
                             "recovery_verified" if recovered else reason, self.clock(), json.dumps(checks),
                             bool(cleanup_verified), attempt_id))
            self._audit_attempt(attempt_id)
        return self.attempt(attempt_id)

    def finish_attempt(self, attempt_id: str, *, successful: bool, cleanup_verified: bool) -> dict[str, Any]:
        """Record a runner's outcome and cleanup evidence without trusting worker text."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.attempt(attempt_id)
            if item["state"] not in ("running", "stopping"):
                raise ValueError("Only a dispatched attempt can finish")
            state = ("cancelled" if item["state"] == "stopping" else
                     "finished" if successful else "failed") if cleanup_verified else "interrupted"
            self.db.execute("UPDATE attempts SET state=?,reason=?,updated=?,cleanup_verified=? WHERE id=?",
                            (state, "runner_finished" if cleanup_verified else "cleanup_unverified",
                             self.clock(), bool(cleanup_verified), attempt_id))
            self._audit_attempt(attempt_id)
            if not cleanup_verified:
                self.db.execute("UPDATE workers SET state='suspended' WHERE task=? AND id=? AND state!='revoked'",
                                (item["task"], item["worker"]))
                self.db.execute("""UPDATE approvals SET state='revoked' WHERE task=? AND worker=?
                    AND state IN ('requested','approved')""", (item["task"], item["worker"]))
        return self.attempt(attempt_id)

    APPLICATION_STATES = ("retained", "applying", "applied", "conflicted", "blocked", "interrupted", "not_applied")

    def record_application(self, attempt_id: str, *, mode: str, state: str, reason: str,
                           changes: Sequence[dict[str, str]] = (), journal: str = "") -> dict[str, Any]:
        """Trusted runner records what actually happened to the original project.

        Only the runner that owns the stopped workspace calls this. An applying or
        applied record can never be restarted, so interruption is not replayed.
        """
        if mode not in ("auto", "review") or state not in self.APPLICATION_STATES:
            raise ValueError("Unknown application mode or state")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            item = self.attempt(attempt_id)
            current = self.db.execute("SELECT state FROM applications WHERE attempt=?", (attempt_id,)).fetchone()
            if current is not None and (current["state"] in ("applied", "interrupted", "conflicted", "blocked")
                                        or (current["state"] == "applying" and state == "applying")):
                raise ValueError("Application outcome is final and cannot be replayed")
            if state in ("applying", "applied") and (item["state"] != "finished" or not item["cleanup_verified"]):
                raise ValueError("Only a finished attempt with verified cleanup can be applied")
            self.db.execute("INSERT OR REPLACE INTO applications VALUES (?,?,?,?,?,?,?,?,?)",
                            (attempt_id, item["task"], item["worker"], mode, state, reason,
                             json.dumps(list(changes)), journal, self.clock()))
        return self.application(attempt_id)

    def application(self, attempt_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM applications WHERE attempt=?", (attempt_id,)).fetchone()
        if row is None:
            raise ValueError("No application record")
        return {**dict(row), "changes": json.loads(row["changes"])}

    def applications(self, task_id: str) -> list[dict[str, Any]]:
        self.status(task_id)
        return [{**dict(row), "changes": json.loads(row["changes"])} for row in self.db.execute(
            "SELECT * FROM applications WHERE task=? ORDER BY rowid", (task_id,))]

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
            grant = self.check_worker(action.task_id, action.worker_id)
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
            self.check_worker(item["task"], item["worker"])
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
        return self.authorize_many((action,))[0]

    def _decide(self, action: Action) -> tuple[Decision, int | None]:
        grant_revision = None
        try:
            grant = self.check_worker(action.task_id, action.worker_id)
            grant_revision = self.worker_status(action.task_id, action.worker_id)["revision"]
            policy = self.policy(action.task_id)
            decision = policy.decide(action, grant)
            path, _ = policy.normalize(action.resource)
            if self._protected(path, action.operation):
                decision = Decision(False, "Supervisor code and state are protected", str(path))
            elif action.tool in grant.tools:
                decision = self._use_exception(action, decision)
        except (ValueError, OSError, RuntimeError) as error:
            decision = Decision(False, str(error), action.resource)
        return decision, grant_revision

    def _record_decision(self, action: Action, decision: Decision, revision: int | None) -> None:
        self.db.execute("INSERT INTO decisions VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
                        (action.task_id, action.worker_id, action.operation, decision.resource,
                         action.tool, decision.allowed, decision.reason, decision.approval_id,
                         self.clock(), revision))

    def _record_request(self, actions: tuple[Action, ...], decisions: tuple[Decision, ...]) -> None:
        task, worker = actions[0].task_id, actions[0].worker_id
        resources = json.dumps(sorted((action.operation, decision.resource, action.tool)
                                      for action, decision in zip(actions, decisions)))
        allowed = all(decision.allowed for decision in decisions)
        self.db.execute("INSERT INTO access_requests VALUES (NULL,?,?,?,?,?)",
                        (task, worker, resources, allowed, self.clock()))
        if not allowed:
            last = self.db.execute("""SELECT resources,allowed FROM access_requests
                WHERE task=? AND worker=? ORDER BY id DESC LIMIT ?""",
                (task, worker, self.denial_limit)).fetchall()
            if len(last) == self.denial_limit and all(
                not row["allowed"] and row["resources"] == resources for row in last
            ):
                self.db.execute("UPDATE workers SET state='suspended' WHERE task=? AND id=? AND state='active'", (task, worker))
                for affected in self._worker_tree(task, worker):
                    self._stop_attempts(task, affected, "repeated_denials")

    def authorize_many(self, actions: Sequence[Action]) -> tuple[Decision, ...]:
        """Authorize one multi-resource dispatch atomically, including exceptions."""
        actions = tuple(actions)
        if not actions or len({(action.task_id, action.worker_id) for action in actions}) != 1:
            raise ValueError("An operation needs one or more resources for the same task and worker")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("SAVEPOINT permissions")
            assessed = [self._decide(action) for action in actions]
            if not all(decision.allowed for decision, _ in assessed):
                self.db.execute("ROLLBACK TO permissions")
                assessed = [(Decision(False, "Another resource in this operation was denied", decision.resource,
                                      decision.sensitive) if decision.allowed else decision, revision)
                            for decision, revision in assessed]
            self.db.execute("RELEASE permissions")
            for action, (decision, revision) in zip(actions, assessed):
                self._record_decision(action, decision, revision)
            self._record_request(actions, tuple(decision for decision, _ in assessed))
        return tuple(decision for decision, _ in assessed)

    def history(self, task_id: str) -> list[dict[str, Any]]:
        self.status(task_id)
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM decisions WHERE task=? ORDER BY id", (task_id,))]

    def record_mediation_denial(self, task_id: str, worker_id: str, route: str, reason: str) -> None:
        """Trusted broker records sanitized non-filesystem refusals, without payload bytes."""
        with self.db:
            self.status(task_id)
            self.db.execute("INSERT INTO decisions VALUES (NULL,?,?,?,?,?,0,?,NULL,?,NULL)",
                            (task_id, worker_id, "mediation", route, "opencode", reason, self.clock()))

    def record_filesystem_denials(self, task_id: str, worker_id: str,
                                  denials: Sequence[tuple[str, str]], dropped: int = 0) -> None:
        """Trusted runner records refusals the mediated filesystem enforced in real time."""
        with self.db:
            self.status(task_id)
            for operation, resource in denials:
                self.db.execute("INSERT INTO decisions VALUES (NULL,?,?,?,?,'filesystem',0,?,NULL,?,NULL)",
                                (task_id, worker_id, operation, resource, "Worker operation denied", self.clock()))
            if dropped:
                self.db.execute("INSERT INTO decisions VALUES (NULL,?,?,'mediation','filesystem','filesystem',0,?,NULL,?,NULL)",
                                (task_id, worker_id, f"{dropped} further denials not itemized", self.clock()))

    def access_requests(self, task_id: str) -> list[dict[str, Any]]:
        self.status(task_id)
        return [{**dict(row), "resources": json.loads(row["resources"])} for row in self.db.execute(
            "SELECT * FROM access_requests WHERE task=? ORDER BY id", (task_id,))]

    def _worker_tree(self, task_id: str, worker_id: str) -> set[str]:
        affected = {worker_id}
        rows = self.db.execute("SELECT id,grant_json FROM workers WHERE task=?", (task_id,)).fetchall()
        while True:
            children = {row["id"] for row in rows if json.loads(row["grant_json"])["parent_id"] in affected}
            if children <= affected:
                return affected
            affected.update(children)

    def _stop_attempts(self, task_id: str, worker_id: str | None, reason: str) -> None:
        affected = self.db.execute("""SELECT id FROM attempts WHERE task=? AND (? IS NULL OR worker=?)
            AND state IN ('prepared','verified','running','stopping','interrupted')""",
            (task_id, worker_id, worker_id)).fetchall()
        self.db.execute("""UPDATE attempts SET state=CASE
                WHEN state IN ('prepared','verified') THEN 'blocked' ELSE 'stopping' END,
            reason=?,updated=? WHERE task=? AND (? IS NULL OR worker=?)
            AND state IN ('prepared','verified','running','stopping','interrupted')""",
            (reason, self.clock(), task_id, worker_id, worker_id))
        for row in affected:
            self._audit_attempt(row["id"])

    def revoke_worker(self, task_id: str, worker_id: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.status(task_id)
            affected = self._worker_tree(task_id, worker_id)
            for worker in affected:
                self.db.execute("UPDATE workers SET state='revoked' WHERE task=? AND id=?", (task_id, worker))
                self.db.execute("UPDATE approvals SET state='revoked' WHERE task=? AND worker=?",
                                (task_id, worker))
                self.db.execute("UPDATE delegation_templates SET state='revoked' WHERE task=? AND parent=?",
                                (task_id, worker))
                self._stop_attempts(task_id, worker, "worker_revoked")

    def cancel(self, task_id: str) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.status(task_id)
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE id=?", (task_id,))
            self.db.execute("UPDATE workers SET state='revoked' WHERE task=?", (task_id,))
            self.db.execute("UPDATE approvals SET state='revoked' WHERE task=?", (task_id,))
            self.db.execute("UPDATE delegation_templates SET state='revoked' WHERE task=?", (task_id,))
            self._stop_attempts(task_id, None, "task_cancelled")

    def preflight(self, task_id: str, worker_id: str, model: str) -> list[dict[str, Any]]:
        grant = self.check_worker(task_id, worker_id)
        return [asdict(Backend().check(self.policy(task_id), grant)),
                asdict(Connector(ConnectorConfig(model)).check())]

    def resume(self, task_id: str, worker_id: str, model: str, *, cleanup_verified: bool = False) -> None:
        """Recovery cannot re-enable work without verifying every protection."""
        self._active(task_id)
        row = self.db.execute("SELECT grant_json,state FROM workers WHERE task=? AND id=?",
                              (task_id, worker_id)).fetchone()
        if row is None or row["state"] == "revoked":
            raise ValueError("Worker grant was revoked or is missing")
        grant = _decode_grant(row["grant_json"])
        policy = self.policy(task_id)
        backend, connector = Backend().check(policy, grant), Connector(ConnectorConfig(model)).check()
        for status in (backend, connector):
            if not status.available or not status.verified:
                raise ProtectionUnavailable(status)
        pending = [item for item in self.attempts(task_id) if item["worker"] == worker_id and
                   item["state"] in ("prepared", "verified", "running", "stopping", "interrupted")]
        if any(item["state"] != "interrupted" for item in pending):
            raise ValueError("Reconcile unresolved execution attempts before resuming")
        for item in pending:
            recovered = self.recover_attempt(item["id"], backend, connector, cleanup_verified=cleanup_verified)
            if recovered["state"] != "reconciled":
                raise ValueError(f"Recovery blocked: {recovered['reason']}")
        if not pending:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                self._active(task_id)
                current = self.worker_status(task_id, worker_id)
                if current["state"] == "revoked" or current["revision"] != self.status(task_id)["revision"]:
                    raise ValueError("Worker grant was revoked or its revision changed")
                self.db.execute("UPDATE workers SET state='active' WHERE task=? AND id=?", (task_id, worker_id))
                self.check_worker(task_id, worker_id)

    def export(self, task_id: str) -> dict[str, Any]:
        return {"tasks": [self.status(task_id)],
                "policy_revisions": [{"task": row["task"], "revision": row["revision"],
                                      "policy": json.loads(row["policy"]), "created": row["created"],
                                      "approved": row["approved"]} for row in self.db.execute(
                    "SELECT * FROM policies WHERE task=? ORDER BY revision", (task_id,))],
                "workers": self.workers(task_id),
                "delegation_templates": self.templates(task_id),
                "attempts": self.attempts(task_id),
                "applications": self.applications(task_id),
                "access_requests": self.access_requests(task_id),
                "decisions": self.history(task_id),
                "approvals": [dict(row) for row in self.db.execute(
                    "SELECT * FROM approvals WHERE task=? ORDER BY rowid", (task_id,))]}
