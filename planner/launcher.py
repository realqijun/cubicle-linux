"""Trusted Linux task runner: one confined worker attempt, then checked application.

The runner owns attempt records and decides outcomes from observed process,
cleanup and filesystem state. Worker or model text never marks work successful.
Application uses the worker's current ordinary authority; changes outside it,
conflicts and uncertain cleanup leave the original project untouched.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, replace
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from typing import Any

from .broker_server import BrokerServer
from .changeset import ApplicationConflict, Change, Changeset
from .connector import BridgeConfig, Connector, ConnectorConfig
from .mediation import WorkerBroker
from .native.mediatedfs import mounted
from .provenance import Provenance
from .records import Action, ProtectionStatus
from .supervisor import Supervisor

_SUMMARY_LIMIT = 50
APPLIER = "applier.json"  # Owner of a reviewed apply, beside the run's journal.


@dataclass(frozen=True)
class Runtime:
    """Operator-chosen resources; ``runs`` holds trusted per-attempt state."""

    opencode: Path
    fuse_helper: Path
    runs: Path
    run_seconds: float = 900
    poll_seconds: float = 0.5
    denial_limit: int = 3  # Identical denials before suspension, as the CLI configures.


class _Authority:
    """Monitor-thread view: the worker grant and its attempt must stay live."""

    def __init__(self, state: Path, task: str, worker: str, attempt: str,
                 drain: Callable[[Supervisor], None], *, denial_limit: int = 3) -> None:
        self.supervisor = Supervisor(state, denial_limit=denial_limit)
        self.task, self.worker, self.attempt, self.drain = task, worker, attempt, drain

    def check(self) -> None:
        self.drain(self.supervisor)
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


def _cleanup_observed(run: Path, *, include_owner: bool = True) -> tuple[bool, str]:
    """Verify recorded processes, the worker PID namespace and the mount are gone."""
    try:
        data = json.loads((run / "processes.json").read_text())
    except (OSError, ValueError):
        return False, "process_record_missing"
    processes = data["processes"]
    if "worker" not in processes or data.get("namespace") is None:
        return False, "worker_identity_unrecorded"
    if any(_alive(record) for name, record in processes.items() if include_owner or name != "launcher"):
        return False, "recorded_process_alive"
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if os.readlink(entry / "ns" / "pid") == data["namespace"]:
                    return False, "worker_namespace_alive"
            except OSError:
                continue
    mount = data.get("mount")
    if mount and mounted(mount):
        subprocess.run(["fusermount3", "-u", mount], capture_output=True, timeout=30, check=False)
        if mounted(mount):
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
            if item["worker"] != worker:
                continue
            try:
                application: dict[str, Any] | None = supervisor.application(item["id"])
            except ValueError:
                application = None
            applying = application is not None and application["state"] == "applying"
            if item["state"] != "interrupted" and not applying:
                continue
            run = runs / item["id"]
            if applying and item["state"] != "interrupted":
                # The worker finished; only the application owner may have died.
                # A reviewed apply runs in its own process and records itself.
                for record, key in (("processes.json", "processes"), (APPLIER, None)):
                    try:
                        data = json.loads((run / record).read_text())
                        alive = _alive(data[key]["launcher"] if key else data)
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
                    if alive:
                        raise ValueError("An application owner for this task is still running")
            if application is not None and applying:
                journal = Path(application["journal"])
                if not application["journal"] or not journal.exists():
                    status = "no_write_started"  # The journal precedes every write to the original.
                else:
                    try:
                        status = str(application_status(supervisor, journal)["state"])
                    except (OSError, ValueError) as error:
                        status = f"unverifiable:{type(error).__name__}"
                supervisor.record_application(item["id"], mode=application["mode"], state="interrupted",
                                              reason=f"owner_lost_during_application:{status}",
                                              changes=application["changes"], journal=application["journal"])
            elif application is None:
                supervisor.record_application(item["id"], mode="auto", state="not_applied", reason="owner_lost")
            entry = {"attempt": item["id"], "operator_confirmed": operator_confirmed}
            if item["state"] == "interrupted":
                observed, evidence = _cleanup_observed(run)
                recovered = supervisor.recover_attempt(item["id"], backend, connector,
                                                       cleanup_verified=observed or operator_confirmed)
                if observed:
                    _purge(run)
                    _remove_transport(run)
                entry.update(state=recovered["state"], reason=recovered["reason"], cleanup_evidence=evidence)
            else:
                entry.update(state=item["state"], reason=item["reason"], cleanup_evidence="worker_already_finished")
            results.append({**entry, "application": supervisor.application(item["id"])})
        return results
    finally:
        supervisor.close()


def _remove_transport(run: Path) -> None:
    """A killed owner cannot remove its socket directory; recovery does, once verified."""
    try:
        transport = json.loads((run / "processes.json").read_text()).get("transport")
    except (OSError, ValueError):
        return
    if transport and Path(transport).name.startswith("planner-t-") and Path(transport).is_dir():
        shutil.rmtree(transport)


def _purge(run: Path) -> None:
    """The private copy includes denied files; keep only digest metadata once final."""
    for content in (run / "fs" / "backing", run / "inputs"):
        if content.exists():
            shutil.rmtree(content)


def _copy_bound_input(source: Path, identity: str, copy: Path) -> Path:
    """Copy exactly the approved external file; a swapped or linked file is refused."""
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or
                f"file:{info.st_dev}:{info.st_ino}:{info.st_nlink}" != identity):
            raise PermissionError("External input changed since approval")
        copy.parent.mkdir(mode=0o700, exist_ok=True)
        with open(os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "wb") as target:
            shutil.copyfileobj(stream, target)
    return copy


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
    created: list[dict[str, Any]] = []
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
    return "conflicted" if isinstance(error, (ApplicationConflict, FileExistsError)) else "blocked"


def apply_retained(state: Path, attempt_id: str) -> dict[str, Any]:
    """Apply a review-only changeset after the user inspected it."""
    supervisor = Supervisor(state)
    try:
        record = supervisor.application(attempt_id)
        if record["state"] != "retained":
            raise ValueError("Only a retained changeset can be applied")
        run = Path(record["journal"]).parent
        changes = Changeset.restore(supervisor, run / "baseline.json")
        owner = run / APPLIER
        owner.write_text(json.dumps({"pid": os.getpid(), "start": _start_time(os.getpid())}))
        try:
            reviewed = tuple(Change(**change) for change in record["changes"])
            outcome = _apply(supervisor, attempt_id, changes, "review", reviewed)
        finally:
            owner.unlink(missing_ok=True)
        _purge(run)
        return outcome
    finally:
        supervisor.close()


def _apply(supervisor: Supervisor, attempt_id: str, changes: Changeset, mode: str,
           reviewed: tuple[Change, ...] | None = None) -> dict[str, Any]:
    journal = str(changes.journal)
    try:
        checked = changes.preview()
        if reviewed is not None and checked != reviewed:
            raise ApplicationConflict("Changes differ from the reviewed changeset")
        preview = [asdict(change) for change in checked]
    except Exception as error:  # Any refusal is an outcome, never a missing record.
        return supervisor.record_application(attempt_id, mode=mode, state=_classify(error),
                                             reason=f"{type(error).__name__}: {error}", journal=journal)
    supervisor.record_application(attempt_id, mode=mode, state="applying", reason="checked",
                                  changes=preview, journal=journal)
    try:
        applied = [asdict(change) for change in changes.apply(checked)]
    except Exception as error:  # Never leave a record in 'applying' behind a live owner.
        # No journal means preflight refused before any write to the original.
        state = "interrupted" if changes.journal.exists() else _classify(error)
        return supervisor.record_application(attempt_id, mode=mode, state=state,
                                             reason=f"{type(error).__name__}: {error}",
                                             changes=preview, journal=journal)
    return supervisor.record_application(attempt_id, mode=mode, state="applied", reason="complete",
                                         changes=applied, journal=journal)


def run_task(state: Path, task: str, worker: str, prompt: str, config: ConnectorConfig,
             runtime: Runtime, *, credential: str | None = None, mode: str = "auto",
             mediated: bool = True,
             on_denial: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Run one supervised attempt and report observed outcomes.

    ``mediated=False`` exists only for the matched comparison's OS-only
    configuration: the same confinement and application, without hooks.

    ``mode='auto'`` applies conflict-free authorized changes after verified
    cleanup; ``mode='review'`` retains them for ``apply_retained``.

    ``on_denial`` receives each refusal recorded for this worker during the
    attempt, while it runs and once more after every record is complete. It is
    a display hook: every refusal has already been enforced and recorded before
    it is called. It must not raise; an exception while the worker runs stops
    the attempt like any runtime error, and one afterwards is ignored.
    """
    from .native.confined_opencode import ConfinedOpenCode, runtime_status
    from .native.mediatedfs import INPUTS, MediatedWorkspace

    if mode not in ("auto", "review"):
        raise ValueError("Application mode must be auto or review")
    provider = config.provider
    if provider is None or bool(provider.api_key_env) != bool(credential):
        raise ValueError("An explicit provider and its configured credential are required")
    credentials = {provider.api_key_env: credential} if provider.api_key_env and credential else {}
    supervisor = Supervisor(state, denial_limit=runtime.denial_limit)
    try:
        policy = supervisor.policy(task)
        grant = supervisor.check_worker(task, worker)
        runs = runtime.runs.resolve()
        if runs.is_relative_to(policy.root) or policy.root.is_relative_to(runs):
            raise ValueError("Run storage must be outside the project")
        attempt = supervisor.prepare_attempt(task, worker)["id"]
        backend, connector = runtime_status(runtime.opencode, runtime.fuse_helper)
        if not mediated:
            connector = ProtectionStatus(connector.available, "opencode-unmediated-evaluation",
                                         "Evaluation only: OS confinement without hooks", connector.verified)
        verified = supervisor.verify_attempt(attempt, backend, connector)
        result: dict[str, Any] = {"task": task, "worker": worker, "attempt": attempt, "mode": mode,
                                  "mediated": mediated}
        if verified["state"] != "verified":
            supervisor.record_application(attempt, mode=mode, state="not_applied", reason=verified["reason"])
            return {**result, "outcome": "blocked", "reason": verified["reason"],
                    "checks": verified["checks"], "application": supervisor.application(attempt)}
        runs.mkdir(mode=0o700, parents=True, exist_ok=True)
        run = runs / attempt
        run.mkdir(mode=0o700)
        ready = threading.Event()
        holder: dict[str, Any] = {"recorded": 0}
        broker_inputs: dict[str, str] = {}
        bindings: list[dict[str, Any]] = []
        filesystem: Any = None
        changes: Changeset | None = None
        processes = _Processes(run / "processes.json")
        outcome, cleaned, failure = "runtime_error", False, ""

        def original(name: str) -> str:
            """Record the logical resource, not the worker-visible copy."""
            return broker_inputs.get(f"/workspace/{name}", str(policy.root / name))

        def lost() -> None:
            confined = holder.get("runtime")
            if confined is not None and confined.authority_lost:
                raise PermissionError("Worker authority lost")

        def drain(owner: Supervisor) -> None:
            """Record new mediator refusals; repeated identical ones suspend the worker."""
            if filesystem is None:
                return
            start, end = holder["recorded"], len(filesystem.denials)
            if end > start:
                owner.record_filesystem_denials(task, worker, [(operation, original(name)) for operation, name
                                                               in filesystem.denials[start:end]])
                holder["recorded"] = end

        def announce() -> None:
            """Pass refusals recorded since the last poll to ``on_denial``."""
            if on_denial is None:
                return
            try:
                rows = supervisor.history(task, after=holder["announced"])
            except sqlite3.Error:
                return  # A busy database only delays the display to the next poll.
            for row in rows:
                holder["announced"] = row["id"]
                if not row["allowed"] and row["worker"] == worker:
                    on_denial(row)

        class Broker(WorkerBroker):
            def handle_request(self, envelope: dict[str, Any]) -> dict[str, Any]:
                response = super().handle_request(envelope)
                if envelope.get("route") == "hello" and response.get("allowed"):
                    ready.set()
                return response

        @contextmanager
        def broker_factory() -> Iterator[WorkerBroker]:
            owner = Supervisor(state, denial_limit=runtime.denial_limit)
            store = Provenance(state.with_suffix(".memory.sqlite"))
            broker = Broker(owner, store, task, worker, worker_project="/workspace",
                            provider_origins=("http://127.0.0.1:18182",),
                            sensitive_exposure=filesystem.sensitive_exposure, external_inputs=broker_inputs)
            try:
                yield broker
            finally:
                broker.close()
                store.close()
                owner.close()

        @contextmanager
        def authority_factory() -> Iterator[_Authority]:
            authority = _Authority(state, task, worker, attempt, drain, denial_limit=runtime.denial_limit)
            try:
                yield authority
            finally:
                authority.supervisor.close()

        try:
            bindings = supervisor.reserve_exceptions(attempt)
            inside = [item for item in bindings if Path(item["resource"]).is_relative_to(policy.root)]
            relative = {item["id"]: Path(item["resource"]).relative_to(policy.root).as_posix() for item in inside}
            fs_exceptions = [(item["operation"], relative[item["id"]], item["lifetime"], bool(item["sensitive"]))
                             for item in inside]
            write_exceptions = {relative[item["id"]]: item["id"] for item in inside if item["operation"] == "write"}
            inputs: dict[str, Path] = {}
            for item in bindings:
                if item in inside:
                    continue
                source = Path(item["resource"])
                name = source.name if source.name not in inputs else f"{item['id'][:12]}-{source.name}"
                inputs[name] = _copy_bound_input(source, item["identity"], run / "inputs" / name)
                fs_exceptions.append(("read", f"{INPUTS}/{name}", item["lifetime"], bool(item["sensitive"])))
                broker_inputs[f"/workspace/{INPUTS}/{name}"] = item["resource"]
            filesystem = MediatedWorkspace(policy, grant, run / "fs", helper=runtime.fuse_helper,
                                           check_authority=lost, exceptions=fs_exceptions, inputs=inputs)
            with filesystem:
                processes.add("filesystem", filesystem.pid)
                processes.data["mount"] = str(filesystem.mount)
                changes = Changeset(supervisor, task, worker, filesystem.backing, run / "journal.jsonl",
                                    attempt=attempt, exceptions=write_exceptions,
                                    placeholders=filesystem.placeholders)
                changes.save(run / "baseline.json")
                with ExitStack() as mediation:
                    bridge, broker = None, None
                    if mediated:
                        broker = mediation.enter_context(BrokerServer(broker_factory))
                        bridge = BridgeConfig(broker.endpoint, broker.token)
                    confined = ConfinedOpenCode(
                        filesystem, Connector(replace(config, executable=str(runtime.opencode))), run,
                        bridge=bridge, hooks_ready=ready.is_set if mediated else (lambda: True),
                        credentials=credentials, authority_factory=authority_factory,
                        unmediated_evaluation=not mediated)
                    holder["runtime"] = confined
                    earlier = supervisor.history(task)
                    holder["announced"] = earlier[-1]["id"] if earlier else 0  # Only this attempt.
                    supervisor.start_attempt(attempt)
                    try:
                        with confined:
                            assert confined.server is not None and confined.process is not None
                            processes.data["namespace"] = _namespace(confined.process.pid)
                            processes.data["transport"] = str(confined.transport)
                            processes.add("worker", confined.process.pid)
                            session = confined.server.submit(prompt, config.model)
                            deadline = time.monotonic() + runtime.run_seconds
                            while True:
                                time.sleep(runtime.poll_seconds)
                                announce()
                                if confined.authority_lost:
                                    outcome = "authority_lost"
                                elif broker is not None and not broker.healthy:
                                    outcome = "connector_lost"  # Leaving the loop kills the namespace.
                                elif confined.process.poll() is not None:
                                    outcome = "worker_exited"
                                elif not confined.server.active(session):
                                    outcome = "completed"
                                elif time.monotonic() >= deadline:
                                    outcome = "timeout"
                                else:
                                    continue
                                break
                    except Exception as error:  # Every failure still reaches the closing records.
                        outcome = "authority_lost" if confined.authority_lost else f"runtime_error:{type(error).__name__}"
                        failure = f"{type(error).__name__}: {error}"[:500]
                    cleaned = confined.cleaned
        except Exception as error:
            if outcome == "runtime_error":
                outcome = f"runtime_error:{type(error).__name__}"
            failure = failure or f"{type(error).__name__}: {error}"[:500]
        unmounted = filesystem is None or not filesystem.mounted
        if "worker" in processes.data["processes"]:
            observed, _ = _cleanup_observed(run, include_owner=False)  # Observed, not assumed.
        else:
            observed = cleaned  # The worker never reached a recorded start.
        current = supervisor.attempt(attempt)["state"]
        if current in ("running", "stopping"):
            supervisor.finish_attempt(attempt, successful=outcome == "completed",
                                      cleanup_verified=cleaned and unmounted and observed)
        elif current in ("prepared", "verified"):
            supervisor.abandon_attempt(attempt, outcome)
        # Otherwise cancellation, revocation or recovery already closed the record.
        supervisor.settle_exceptions(attempt)
        drain(supervisor)
        if filesystem is not None and filesystem.denials_dropped:
            supervisor.record_filesystem_denials(task, worker, [], filesystem.denials_dropped)
        denials = list(filesystem.denials) if filesystem is not None else []
        finished = supervisor.attempt(attempt)
        result["exceptions"] = [{"id": item["id"], "operation": item["operation"], "resource": item["resource"],
                                 "lifetime": item["lifetime"], "state": supervisor.exception(item["id"])["state"]}
                                for item in bindings]
        result["requests"] = _request_reconsideration(supervisor, task, worker, attempt,
                                                      [(operation, original(name)) for operation, name in denials])
        result.update(outcome=outcome, error=failure, attempt_state=finished["state"],
                      cleanup_verified=finished["cleanup_verified"],
                      filesystem_denials=[{"operation": op, "resource": name}
                                          for op, name in denials[:_SUMMARY_LIMIT]])
        if finished["state"] != "finished" or changes is None:
            application = supervisor.record_application(
                attempt, mode=mode, state="not_applied", reason=f"attempt_{finished['state']}:{outcome}")
        elif mode == "review":
            try:
                preview = [asdict(change) for change in changes.preview()]
                application = supervisor.record_application(attempt, mode=mode, state="retained",
                    reason="awaiting_user_review", changes=preview, journal=str(changes.journal))
            except Exception as error:
                application = supervisor.record_application(attempt, mode=mode, state=_classify(error),
                                                            reason=f"{type(error).__name__}: {error}",
                                                            journal=str(changes.journal))
        else:
            application = _apply(supervisor, attempt, changes, mode)
        if application["state"] != "retained" and unmounted:
            _purge(run)
        if "announced" in holder:
            try:
                announce()  # Refusals drained after the last poll; every record is already complete.
            except Exception:
                pass  # Display only: the outcome above is already recorded and must still be returned.
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
