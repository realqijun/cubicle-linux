"""Trusted Linux task runner: one confined worker attempt, then checked application.

The runner owns attempt records and decides outcomes from observed process,
cleanup and filesystem state. Worker or model text never marks work successful.
Application uses the worker's current ordinary authority; changes outside it,
conflicts and uncertain cleanup leave the original project untouched.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any

from .broker_server import BrokerServer
from .changeset import Changeset
from .connector import BridgeConfig, Connector, ConnectorConfig
from .mediation import WorkerBroker
from .provenance import Provenance
from .records import Action
from .supervisor import Supervisor

_SUMMARY_LIMIT = 50


@dataclass(frozen=True)
class Runtime:
    """Operator-chosen resources; ``runs`` holds trusted per-attempt state."""

    opencode: Path
    fuse_helper: Path
    runs: Path
    run_seconds: float = 900
    poll_seconds: float = 0.5


class _Authority:
    """Monitor-thread view: the worker grant and its attempt must stay live."""

    def __init__(self, state: Path, task: str, worker: str, attempt: str) -> None:
        self.supervisor = Supervisor(state)
        self.task, self.worker, self.attempt = task, worker, attempt

    def check(self) -> None:
        self.supervisor.check_worker(self.task, self.worker)
        item = self.supervisor.attempt(self.attempt)
        if item["state"] != "running" or item["expires"] <= self.supervisor.clock():
            raise PermissionError("Execution attempt is no longer running")


def _start_time(pid: int) -> str | None:
    """Kernel start time distinguishes a live process from a reused PID."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _namespace(pid: int) -> str | None:
    """PID namespace of the jail init, i.e. the first child of the outer bwrap."""
    try:
        child = int(Path(f"/proc/{pid}/task/{pid}/children").read_text().split()[0])
        return os.readlink(f"/proc/{child}/ns/pid")
    except (OSError, IndexError, ValueError):
        return None


class _Processes:
    """Trusted per-attempt record so a restarted operator can verify cleanup."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"processes": {}, "namespace": None, "mount": None}
        self.add("launcher", os.getpid())

    def add(self, name: str, pid: int | None) -> None:
        if pid is not None:
            self.data["processes"][name] = {"pid": pid, "start": _start_time(pid)}
        self.write()

    def write(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w") as stream:
            json.dump(self.data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


def _alive(record: dict[str, Any]) -> bool:
    return record["start"] is not None and _start_time(record["pid"]) == record["start"]


def _cleanup_observed(run: Path) -> tuple[bool, str]:
    """Verify recorded processes, the worker PID namespace and the mount are gone."""
    try:
        data = json.loads((run / "processes.json").read_text())
    except (OSError, ValueError):
        return False, "process_record_missing"
    processes = data["processes"]
    if "worker" not in processes or data.get("namespace") is None:
        return False, "worker_identity_unrecorded"
    if any(_alive(record) for record in processes.values()):
        return False, "recorded_process_alive"
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if os.readlink(entry / "ns" / "pid") == data["namespace"]:
                    return False, "worker_namespace_alive"
            except OSError:
                continue
    mount = data.get("mount")
    if mount:
        with open("/proc/self/mountinfo", encoding="utf-8") as table:
            if any(line.split()[4] == mount for line in table):
                subprocess.run(["fusermount3", "-u", mount], capture_output=True, timeout=30, check=False)
                with open("/proc/self/mountinfo", encoding="utf-8") as again:
                    if any(line.split()[4] == mount for line in again):
                        return False, "mount_remains"
    return True, "cleanup_observed"


def recover(state: Path, task: str, worker: str, runtime: Runtime, *,
            operator_confirmed: bool = False) -> list[dict[str, Any]]:
    """After owner loss: verify cleanup, reconcile records, never replay edits.

    ``operator_confirmed`` is the trusted user's attestation when automated
    evidence is incomplete; it never revives expired or revoked authority.
    """
    from .changeset import application_status
    from .native.confined_opencode import runtime_status

    supervisor = Supervisor(state)
    try:
        runs = runtime.runs.resolve()
        pending = [item for item in supervisor.attempts(task)
                   if item["state"] in ("prepared", "verified", "running", "stopping")]
        for item in pending:
            try:
                launcher = json.loads((runs / item["id"] / "processes.json").read_text())["processes"]["launcher"]
            except (OSError, ValueError, KeyError):
                continue
            if _alive(launcher):
                raise ValueError("An execution owner for this task is still running")
        supervisor.reconcile(task)
        backend, connector = runtime_status(runtime.opencode, runtime.fuse_helper)
        results = []
        for item in supervisor.attempts(task):
            if item["worker"] != worker or item["state"] != "interrupted":
                continue
            run = runs / item["id"]
            observed, evidence = _cleanup_observed(run)
            try:
                application = supervisor.application(item["id"])
            except ValueError:
                application = None
            if application and application["state"] == "applying":
                status = application_status(supervisor, Path(application["journal"]))
                supervisor.record_application(item["id"], mode=application["mode"], state="interrupted",
                                              reason="owner_lost_during_application:" + status["state"],
                                              changes=application["changes"], journal=application["journal"])
            elif application is None:
                supervisor.record_application(item["id"], mode="auto", state="not_applied", reason="owner_lost")
            recovered = supervisor.recover_attempt(item["id"], backend, connector,
                                                   cleanup_verified=observed or operator_confirmed)
            if observed:
                _purge(run)
            results.append({"attempt": item["id"], "state": recovered["state"], "reason": recovered["reason"],
                            "cleanup_evidence": evidence, "operator_confirmed": operator_confirmed,
                            "application": supervisor.application(item["id"])})
        return results
    finally:
        supervisor.close()


def _purge(run: Path) -> None:
    """The private copy includes denied files; keep only digest metadata once final."""
    backing = run / "fs" / "backing"
    if backing.exists():
        shutil.rmtree(backing)


def _request_reconsideration(supervisor: Supervisor, task: str, worker: str, attempt: str,
                             denials: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Turn the exact denied operations of this attempt into inert pending requests.

    The request names the supervisor-observed operation and normalized resource,
    never model-chosen text. Only the user's separate approval can make it usable.
    """
    started = supervisor.attempt(attempt)["created"]
    observed: list[tuple[str, str, str]] = [(operation, name, "shell") for operation, name in denials]
    observed += [(row["operation"], row["resource"], row["tool"]) for row in supervisor.history(task)
                 if row["worker"] == worker and not row["allowed"] and row["created"] >= started
                 and row["tool"] in ("file", "search") and row["operation"] in ("read", "write")]
    existing = {(item["operation"], item["resource"], item["tool"])
                for item in supervisor.export(task)["approvals"]
                if item["worker"] == worker and item["state"] == "requested"}
    created = []
    for operation, resource, tool in dict.fromkeys(observed):
        if len(created) >= _SUMMARY_LIMIT:
            break
        try:
            path, _ = supervisor.policy(task).normalize(resource)
            if (operation, str(path), tool) in existing:
                continue
            approval = supervisor.request_exception(Action(task, worker, operation, resource, tool),  # type: ignore[arg-type]
                                                    sources=(f"attempt:{attempt}", "observed_denied_operation"))
        except (ValueError, OSError):
            continue  # Protected, external-write, inactive-worker or vanished resources stay denied.
        existing.add((operation, str(path), tool))
        item = supervisor.exception(approval)
        created.append({key: item[key] for key in ("id", "operation", "resource", "tool", "lifetime", "sensitive")})
    return created


def _classify(error: BaseException) -> str:
    if isinstance(error, (PermissionError,)):
        return "blocked"
    text = str(error)
    if "changed" in text or "collides" in text or "appeared" in text:
        return "conflicted"
    return "blocked"


def apply_retained(state: Path, attempt_id: str) -> dict[str, Any]:
    """Apply a review-only changeset after the user inspected it."""
    supervisor = Supervisor(state)
    try:
        record = supervisor.application(attempt_id)
        if record["state"] != "retained":
            raise ValueError("Only a retained changeset can be applied")
        run = Path(record["journal"]).parent
        changes = Changeset.restore(supervisor, run / "baseline.json")
        outcome = _apply(supervisor, attempt_id, changes, "review")
        _purge(run)
        return outcome
    finally:
        supervisor.close()


def _apply(supervisor: Supervisor, attempt_id: str, changes: Changeset, mode: str) -> dict[str, Any]:
    journal = str(changes.journal)
    try:
        preview = [asdict(change) for change in changes.preview()]
    except (ValueError, OSError) as error:
        return supervisor.record_application(attempt_id, mode=mode, state=_classify(error),
                                             reason=str(error), journal=journal)
    supervisor.record_application(attempt_id, mode=mode, state="applying", reason="checked",
                                  changes=preview, journal=journal)
    try:
        applied = [asdict(change) for change in changes.apply()]
    except (ValueError, OSError) as error:
        # No journal means preflight refused before any write to the original.
        state = "interrupted" if changes.journal.exists() else _classify(error)
        return supervisor.record_application(attempt_id, mode=mode, state=state, reason=str(error),
                                             changes=preview, journal=journal)
    return supervisor.record_application(attempt_id, mode=mode, state="applied", reason="complete",
                                         changes=applied, journal=journal)


def run_task(state: Path, task: str, worker: str, prompt: str, config: ConnectorConfig,
             runtime: Runtime, *, credential: str | None = None, mode: str = "auto") -> dict[str, Any]:
    """Run one supervised attempt and report observed outcomes.

    ``mode='auto'`` applies conflict-free authorized changes after verified
    cleanup; ``mode='review'`` retains them for ``apply_retained``.
    """
    from .native.confined_opencode import ConfinedOpenCode, runtime_status
    from .native.mediatedfs import MediatedWorkspace

    if mode not in ("auto", "review"):
        raise ValueError("Application mode must be auto or review")
    provider = config.provider
    if provider is None or bool(provider.api_key_env) != bool(credential):
        raise ValueError("An explicit provider and its configured credential are required")
    credentials = {provider.api_key_env: credential} if provider.api_key_env and credential else {}
    supervisor = Supervisor(state)
    try:
        policy = supervisor.policy(task)
        grant = supervisor.check_worker(task, worker)
        runs = runtime.runs.resolve()
        if runs.is_relative_to(policy.root) or policy.root.is_relative_to(runs):
            raise ValueError("Run storage must be outside the project")
        attempt = supervisor.prepare_attempt(task, worker)["id"]
        backend, connector = runtime_status(runtime.opencode, runtime.fuse_helper)
        verified = supervisor.verify_attempt(attempt, backend, connector)
        result: dict[str, Any] = {"task": task, "worker": worker, "attempt": attempt, "mode": mode}
        if verified["state"] != "verified":
            supervisor.record_application(attempt, mode=mode, state="not_applied", reason=verified["reason"])
            return {**result, "outcome": "blocked", "reason": verified["reason"],
                    "checks": verified["checks"], "application": supervisor.application(attempt)}
        runs.mkdir(mode=0o700, parents=True, exist_ok=True)
        run = runs / attempt
        run.mkdir(mode=0o700)
        bindings = supervisor.reserve_exceptions(attempt)
        inside = [item for item in bindings if Path(item["resource"]).is_relative_to(policy.root)]
        relative = {item["id"]: Path(item["resource"]).relative_to(policy.root).as_posix() for item in inside}
        fs_exceptions = [(item["operation"], relative[item["id"]], item["lifetime"], bool(item["sensitive"]))
                         for item in inside]
        write_exceptions = {relative[item["id"]]: item["id"] for item in inside if item["operation"] == "write"}
        ready = threading.Event()
        holder: dict[str, Any] = {}

        def lost() -> None:
            confined = holder.get("runtime")
            if confined is not None and confined.authority_lost:
                raise PermissionError("Worker authority lost")

        class Broker(WorkerBroker):
            def handle_request(self, envelope: dict[str, Any]) -> dict[str, Any]:
                response = super().handle_request(envelope)
                if envelope.get("route") == "hello" and response.get("allowed"):
                    ready.set()
                return response

        @contextmanager
        def broker_factory() -> Iterator[WorkerBroker]:
            owner, store = Supervisor(state), Provenance(state.with_suffix(".memory.sqlite"))
            broker = Broker(owner, store, task, worker, worker_project="/workspace",
                            provider_origins=("http://127.0.0.1:18182",),
                            sensitive_exposure=filesystem.sensitive_exposure)
            try:
                yield broker
            finally:
                broker.close()
                store.close()
                owner.close()

        @contextmanager
        def authority_factory() -> Iterator[_Authority]:
            authority = _Authority(state, task, worker, attempt)
            try:
                yield authority
            finally:
                authority.supervisor.close()

        outcome, cleaned, unmounted, failure = "runtime_error", False, False, ""
        filesystem = MediatedWorkspace(policy, grant, run / "fs", helper=runtime.fuse_helper, check_authority=lost,
                                       exceptions=fs_exceptions)
        changes: Changeset | None = None
        processes = _Processes(run / "processes.json")
        try:
            with filesystem:
                processes.add("filesystem", filesystem.pid)
                processes.data["mount"] = str(filesystem.mount)
                changes = Changeset(supervisor, task, worker, filesystem.backing, run / "journal.jsonl",
                                    attempt=attempt, exceptions=write_exceptions)
                changes.save(run / "baseline.json")
                with BrokerServer(broker_factory) as broker:
                    confined = ConfinedOpenCode(
                        filesystem, Connector(replace(config, executable=str(runtime.opencode))), run,
                        bridge=BridgeConfig(broker.endpoint, broker.token), hooks_ready=ready.is_set,
                        credentials=credentials, authority_factory=authority_factory)
                    holder["runtime"] = confined
                    supervisor.start_attempt(attempt)
                    try:
                        with confined:
                            assert confined.server is not None and confined.process is not None
                            processes.data["namespace"] = _namespace(confined.process.pid)
                            processes.add("worker", confined.process.pid)
                            session = confined.server.submit(prompt, config.model)
                            deadline = time.monotonic() + runtime.run_seconds
                            while True:
                                time.sleep(runtime.poll_seconds)
                                if confined.authority_lost:
                                    outcome = "authority_lost"
                                elif confined.process.poll() is not None:
                                    outcome = "worker_exited"
                                elif not confined.server.active(session):
                                    outcome = "completed"
                                elif time.monotonic() >= deadline:
                                    outcome = "timeout"
                                else:
                                    continue
                                break
                    except Exception as error:  # Every failure still reaches finish_attempt.
                        outcome = "authority_lost" if confined.authority_lost else f"runtime_error:{type(error).__name__}"
                        failure = f"{type(error).__name__}: {error}"[:500]
                    cleaned = confined.cleaned
        except Exception as error:
            if outcome == "runtime_error":
                outcome = f"runtime_error:{type(error).__name__}"
            failure = failure or f"{type(error).__name__}: {error}"[:500]
        finally:
            unmounted = not filesystem.mounted
        if supervisor.attempt(attempt)["state"] in ("running", "stopping"):
            supervisor.finish_attempt(attempt, successful=outcome == "completed",
                                      cleanup_verified=cleaned and unmounted)
        else:
            supervisor.abandon_attempt(attempt, outcome)
        supervisor.settle_exceptions(attempt)
        supervisor.record_filesystem_denials(task, worker, filesystem.denials, filesystem.denials_dropped)
        finished = supervisor.attempt(attempt)
        result["exceptions"] = [{"id": item["id"], "operation": item["operation"], "resource": item["resource"],
                                 "lifetime": item["lifetime"], "state": supervisor.exception(item["id"])["state"]}
                                for item in bindings]
        result["requests"] = _request_reconsideration(supervisor, task, worker, attempt, filesystem.denials)
        result.update(outcome=outcome, error=failure, attempt_state=finished["state"],
                      cleanup_verified=finished["cleanup_verified"],
                      filesystem_denials=[{"operation": op, "resource": name}
                                          for op, name in filesystem.denials[:_SUMMARY_LIMIT]])
        if finished["state"] != "finished" or changes is None:
            application = supervisor.record_application(
                attempt, mode=mode, state="not_applied", reason=f"attempt_{finished['state']}:{outcome}")
        elif mode == "review":
            try:
                preview = [asdict(change) for change in changes.preview()]
                application = supervisor.record_application(attempt, mode=mode, state="retained",
                    reason="awaiting_user_review", changes=preview, journal=str(changes.journal))
            except (ValueError, OSError) as error:
                application = supervisor.record_application(attempt, mode=mode, state=_classify(error),
                                                            reason=str(error), journal=str(changes.journal))
        else:
            application = _apply(supervisor, attempt, changes, mode)
        if application["state"] != "retained" and unmounted:
            _purge(run)
        return {**result, "application": application}
    finally:
        supervisor.close()


def summary(result: dict[str, Any]) -> str:
    """Plain-text outcome that keeps authorization, execution and application separate."""
    application = result.get("application", {})
    lines = [f"Attempt {result['attempt']}: {result.get('outcome')} ({result.get('attempt_state', 'blocked')})",
             f"Application ({result['mode']}): {application.get('state')} - {application.get('reason')}"]
    lines += [f"  {change['operation']} {change['path']}" for change in application.get("changes", [])]
    for denial in result.get("filesystem_denials", []):
        lines.append(f"  denied {denial['operation']} {denial['resource']}")
    return "\n".join(lines)
