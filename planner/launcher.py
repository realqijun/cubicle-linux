"""Trusted Linux task runner: one confined worker attempt, then checked application.

The runner owns attempt records and decides outcomes from observed process,
cleanup and filesystem state. Worker or model text never marks work successful.
Application uses the worker's current ordinary authority; changes outside it,
conflicts and uncertain cleanup leave the original project untouched.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
import threading
import time
from typing import Any

from .broker_server import BrokerServer
from .changeset import Changeset
from .connector import BridgeConfig, Connector, ConnectorConfig
from .mediation import WorkerBroker
from .provenance import Provenance
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
        return _apply(supervisor, attempt_id, changes, "review")
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
                            provider_origins=("http://127.0.0.1:18182",))
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
        filesystem = MediatedWorkspace(policy, grant, run / "fs", helper=runtime.fuse_helper, check_authority=lost)
        changes: Changeset | None = None
        try:
            with filesystem:
                changes = Changeset(supervisor, task, worker, filesystem.backing, run / "journal.jsonl")
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
        supervisor.record_filesystem_denials(task, worker, filesystem.denials, filesystem.denials_dropped)
        finished = supervisor.attempt(attempt)
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
