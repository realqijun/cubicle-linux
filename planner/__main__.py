"""Trusted terminal interface; supervised workers must never access its state."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence, cast

from .policy import Policy
from .records import Action, Grant, Operation
from .supervisor import Supervisor


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--state", type=Path, required=True, help="Trusted SQLite file outside the project")
    cli.add_argument("--json", action="store_true", help="Machine-readable results")
    cli.add_argument("--task-seconds", type=float, default=3600, help="New task lifetime, seconds")
    cli.add_argument("--denial-limit", type=int, default=3, help="Identical denied attempts before suspension")
    commands = cli.add_subparsers(dest="command", required=True)
    application = commands.add_parser("application-status", help="Inspect a trusted application journal without replay")
    application.add_argument("journal", type=Path)
    planning = commands.add_parser("plan", help="Ask an explicit provider for a reviewable task proposal")
    planning.add_argument("project", type=Path)
    planning.add_argument("--prompt", required=True)
    planning.add_argument("--model", required=True)
    planning.add_argument("--provider-url", required=True)
    planning.add_argument("--api-key-env")
    planning.add_argument("--worker", default="worker")
    planning.add_argument("--timeout", type=float, default=10)
    planning.add_argument("--max-response-bytes", type=int, default=1024 * 1024)
    proposal = commands.add_parser("propose", help="Create a policy proposal without starting work")
    proposal.add_argument("project", type=Path)
    selection = proposal.add_mutually_exclusive_group(required=True)
    selection.add_argument("--policy", type=Path)
    selection.add_argument("--template", choices=("source", "inspection", "tests", "documentation"))
    for name in ("review", "status", "history", "cancel", "export", "workers", "templates", "attempts", "reconcile",
                 "applications"):
        commands.add_parser(name).add_argument("task")
    approval = commands.add_parser("approve-task")
    approval.add_argument("task")
    approval.add_argument("--revision", type=int, required=True)
    approval.add_argument("--assign", metavar="WORKER",
                          help="Also approve WORKER with exactly the reviewed policy's scopes and tools")
    revision = commands.add_parser("revise")
    revision.add_argument("task")
    revision.add_argument("--policy", type=Path, required=True)
    assignment = commands.add_parser("assign", help="Approve an exact worker assignment")
    assignment.add_argument("task")
    assignment.add_argument("worker")
    assignment.add_argument("--parent")
    assignment.add_argument("--read", action="append", default=[])
    assignment.add_argument("--write", action="append", default=[])
    assignment.add_argument("--tool", action="append", default=None)
    delegation = commands.add_parser("delegate", help="Use a previously user-approved delegation template")
    delegation.add_argument("task")
    delegation.add_argument("worker")
    delegation.add_argument("--parent", required=True)
    delegation.add_argument("--template", required=True)
    for scope in ("read", "write", "tool"):
        delegation.add_argument(f"--{scope}", action="append", default=[])
    template = commands.add_parser("approve-template", help="Approve a task- and parent-scoped assignment preset")
    template.add_argument("task")
    template.add_argument("name")
    template.add_argument("parent")
    for scope in ("read", "write", "tool"):
        template.add_argument(f"--{scope}", action="append", default=[])
    commands.add_parser("attempt").add_argument("id")
    for name in ("worker", "prepare-attempt"):
        inspect = commands.add_parser(name)
        inspect.add_argument("task")
        inspect.add_argument("worker")
    for name in ("decide", "request-exception"):
        action = commands.add_parser(name)
        action.add_argument("task")
        action.add_argument("worker")
        action.add_argument("operation", choices=("read", "write"))
        action.add_argument("resource")
        action.add_argument("--tool", default="file")
        if name == "request-exception":
            action.add_argument("--lifetime", choices=("once", "task"), default="once")
            action.add_argument("--source", action="append", default=[])
    for name in ("exception", "approve-exception", "refuse-exception"):
        request = commands.add_parser(name)
        request.add_argument("approval")
        if name == "approve-exception":
            request.add_argument("--confirm-sensitive-provider-disclosure", action="store_true")
    for name in ("preflight", "resume", "revoke"):
        worker = commands.add_parser(name)
        worker.add_argument("task")
        worker.add_argument("worker")
        if name != "revoke":
            worker.add_argument("--model", required=True, help="Explicit provider/model")
        if name == "resume":
            worker.add_argument("--cleanup-verified", action="store_true",
                                help="Trusted operator confirms previous processes have stopped")
    memory = commands.add_parser("memory", help="Inspect/delete project notes; no implicit authorization")
    memory.add_argument("project", help="Approved absolute project root")
    memory.add_argument("operation", choices=("list", "inspect", "delete"))
    memory.add_argument("--note")
    memory.add_argument("--query", default="")
    launch = commands.add_parser("run", help="Run one confined worker attempt (Linux connected runtime)")
    launch.add_argument("task")
    launch.add_argument("worker")
    launch.add_argument("--prompt", required=True)
    launch.add_argument("--model", required=True, help="Explicit provider/model")
    launch.add_argument("--provider-url", required=True)
    launch.add_argument("--api-key-env")
    launch.add_argument("--opencode", type=Path, required=True, help="Pinned OpenCode binary")
    launch.add_argument("--fuse-helper", type=Path, required=True, help="Compiled fuse_bridge.c")
    launch.add_argument("--runs", type=Path, required=True, help="Trusted run storage outside the project")
    launch.add_argument("--mode", choices=("auto", "review"), default="auto",
                        help="auto applies authorized conflict-free edits; review retains them")
    launch.add_argument("--run-seconds", type=float, default=900)
    launch.add_argument("--timeout", type=float, default=120, help="Provider and control timeout, seconds")
    hook = commands.add_parser("hook", help="OpenCode with direct file-tool checks only; no OS confinement")
    hook.add_argument("task")
    hook.add_argument("worker")
    hook.add_argument("--model", required=True, help="Explicit provider/model")
    hook.add_argument("--provider-url", required=True)
    hook.add_argument("--api-key-env")
    hook.add_argument("--opencode", type=Path, required=True, help="Pinned OpenCode 2.2.5 binary")
    hook.add_argument("--prompt", help="Run one prompt; omit to open the interactive CLI")
    hook.add_argument("--run-seconds", type=float, default=3600)
    hook.add_argument("--timeout", type=float, default=30)
    commands.add_parser("apply", help="Apply a retained review-only changeset").add_argument("attempt")
    recovery = commands.add_parser("recover", help="Verify cleanup after owner loss and reconcile; never replays")
    recovery.add_argument("task")
    recovery.add_argument("worker")
    recovery.add_argument("--opencode", type=Path, required=True)
    recovery.add_argument("--fuse-helper", type=Path, required=True)
    recovery.add_argument("--runs", type=Path, required=True)
    recovery.add_argument("--operator-confirms-cleanup", action="store_true",
                          help="Trusted user attests that every previous worker process has stopped")
    report = commands.add_parser("report")
    report.add_argument("task")
    report.add_argument("output", type=Path)
    return cli


def execute(args: argparse.Namespace, supervisor: Supervisor) -> Any:
    command = args.command
    if command == "application-status":
        from .changeset import application_status

        return application_status(supervisor, args.journal)
    if command == "plan":
        from .connector import ConnectorConfig, ProviderConfig
        from .workflow import plan

        provider = ProviderConfig(args.provider_url, api_key_env=args.api_key_env)
        credential = os.environ.get(args.api_key_env) if args.api_key_env else None
        return plan(supervisor, args.project, args.prompt,
                    ConnectorConfig(args.model, timeout_seconds=args.timeout, provider=provider),
                    credential=credential, worker_id=args.worker,
                    max_response_bytes=args.max_response_bytes)
    if command == "propose":
        path = args.policy if args.policy else Path(__file__).parent / "templates" / f"{args.template}.toml"
        task_id = supervisor.propose(Policy.load(args.project, path))
        return supervisor.status(task_id)
    if command in ("review", "status"):
        return supervisor.status(args.task)
    if command == "approve-task":
        supervisor.approve_task(args.task, revision=args.revision)
        if args.assign:
            policy = supervisor.policy(args.task)
            supervisor.approve_worker(Grant(args.task, args.assign, policy.read, policy.write, policy.tools))
        return supervisor.status(args.task)
    if command == "applications":
        return supervisor.applications(args.task)
    if command == "hook":
        from .connector import ConnectorConfig, ProviderConfig
        from .pretool import run_hook

        provider = ProviderConfig(args.provider_url, api_key_env=args.api_key_env)
        credential = os.environ.get(args.api_key_env) if args.api_key_env else None
        supervisor.close()
        print("Hook-only mode: direct file edits; shell/search disabled; no OS confinement or context provenance.",
              file=sys.stderr)
        return run_hook(args.state, args.task, args.worker,
                        ConnectorConfig(args.model, executable=str(args.opencode),
                                        timeout_seconds=args.timeout, provider=provider),
                        prompt=args.prompt, credential=credential, run_seconds=args.run_seconds,
                        denial_limit=args.denial_limit)
    if command in ("run", "apply", "recover"):
        from .launcher import Runtime, apply_retained, recover, run_task

        if command == "apply":
            return apply_retained(args.state, args.attempt)
        if command == "recover":
            supervisor.close()
            return recover(args.state, args.task, args.worker, Runtime(args.opencode, args.fuse_helper, args.runs),
                           operator_confirmed=args.operator_confirms_cleanup)
        from .connector import ConnectorConfig, ProviderConfig

        provider = ProviderConfig(args.provider_url, api_key_env=args.api_key_env)
        credential = os.environ.get(args.api_key_env) if args.api_key_env else None
        supervisor.close()  # The runner owns its own connections and threads.
        return run_task(args.state, args.task, args.worker, args.prompt,
                        ConnectorConfig(args.model, timeout_seconds=args.timeout, provider=provider),
                        Runtime(args.opencode, args.fuse_helper, args.runs, run_seconds=args.run_seconds,
                                denial_limit=args.denial_limit),
                        credential=credential, mode=args.mode)
    if command == "revise":
        root = Path(supervisor.status(args.task)["policy"]["root"])
        supervisor.revise(args.task, Policy.load(root, args.policy))
        return supervisor.status(args.task)
    if command == "assign":
        grant = Grant(args.task, args.worker, tuple(args.read), tuple(args.write),
                      tuple(args.tool or ["file"]), args.parent)
        supervisor.approve_worker(grant)
        return asdict(grant)
    if command == "approve-template":
        return supervisor.approve_delegation_template(args.task, args.name, args.parent,
                   read=tuple(args.read), write=tuple(args.write), tools=tuple(args.tool or ["file"]))
    if command == "delegate":
        grant = Grant(args.task, args.worker, tuple(args.read), tuple(args.write),
                      tuple(args.tool or ["file"]), args.parent)
        supervisor.delegate(grant, args.template)
        return supervisor.worker_status(args.task, args.worker)
    if command == "workers":
        return supervisor.workers(args.task)
    if command == "worker":
        return supervisor.worker_status(args.task, args.worker)
    if command == "templates":
        return supervisor.templates(args.task)
    if command == "prepare-attempt":
        return supervisor.prepare_attempt(args.task, args.worker)
    if command == "attempt":
        return supervisor.attempt(args.id)
    if command == "attempts":
        return supervisor.attempts(args.task)
    if command == "reconcile":
        return supervisor.reconcile(args.task)
    if command in ("decide", "request-exception"):
        action = Action(args.task, args.worker, cast(Operation, args.operation), args.resource, args.tool)
        if command == "decide":
            return asdict(supervisor.authorize(action))
        request = supervisor.request_exception(action, lifetime=args.lifetime, sources=tuple(args.source))
        return supervisor.exception(request)
    if command == "exception":
        return supervisor.exception(args.approval)
    if command == "approve-exception":
        supervisor.approve_exception(args.approval,
                                     disclose_sensitive=args.confirm_sensitive_provider_disclosure)
        return supervisor.exception(args.approval)
    if command == "refuse-exception":
        supervisor.refuse_exception(args.approval)
        return supervisor.exception(args.approval)
    if command == "history":
        return supervisor.history(args.task)
    if command == "cancel":
        supervisor.cancel(args.task)
        return supervisor.status(args.task)
    if command == "revoke":
        supervisor.revoke_worker(args.task, args.worker)
        return {"task": args.task, "worker": args.worker, "state": "revoked"}
    if command == "preflight":
        statuses = supervisor.preflight(args.task, args.worker, args.model)
        return {"ready": all(s["available"] and s["verified"] for s in statuses), "checks": statuses}
    if command == "resume":
        supervisor.resume(args.task, args.worker, args.model, cleanup_verified=args.cleanup_verified)
        return {"state": "resumed"}
    if command == "export":
        return supervisor.export(args.task)
    from .provenance import Provenance

    memory = Provenance(supervisor.path.with_suffix(".memory.sqlite"))
    try:
        if command == "memory":
            project = str(Path(args.project).resolve(strict=True))
            if args.operation == "list":
                return [asdict(note) for note in memory.list_notes(project, args.query)]
            if not args.note:
                raise ValueError("--note is required for inspect/delete")
            if args.operation == "inspect":
                return asdict(memory.get_note(project, args.note))
            memory.delete_note(project, args.note)
            return {"id": args.note, "state": "deleted"}
        if command == "report":
            from .report import generate

            data = supervisor.export(args.task)
            # Empty provenance is explicit when there have been no admitted calls.
            root = supervisor.status(args.task)["policy"]["root"]
            memory.register_task(args.task, root)
            data["provenance"] = memory.export(args.task)
            generate(data, args.output)
            return {"report": str(args.output.resolve())}
        raise ValueError("Unknown command")
    finally:
        memory.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    supervisor = None
    try:
        supervisor = Supervisor(args.state, task_seconds=args.task_seconds, denial_limit=args.denial_limit)
        result = execute(args, supervisor)
        print(json.dumps(result, indent=2, ensure_ascii=True) if args.json else json.dumps(result, indent=2))
        return 2 if isinstance(result, dict) and result.get("ready") is False else 0
    except (ValueError, KeyError, OSError, RuntimeError, sqlite3.Error) as error:
        result = {"blocked": True, "reason": str(error)}
        print(json.dumps(result) if args.json else f"BLOCKED: {error}", file=sys.stderr)
        return 2
    finally:
        if supervisor is not None:
            supervisor.close()


if __name__ == "__main__":
    raise SystemExit(main())
