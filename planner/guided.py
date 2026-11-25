"""Guided runs on the user's own project, behind scripts/run.sh.

One command proposes the worker's permissions, asks the user to approve them,
runs the confined worker, shows the result and asks about each access request.
Every step goes through the trusted planner CLI. ``--yes`` approves only the
task permissions; access requests, sensitive disclosure, applying held changes
and resuming a suspended worker always need the user's own answer.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, TextIO

from .alerts import companion, printable
from .display import STOPPED, decision_lines, diff_lines, show_policy, shown, style, wait
from .policy import Policy

ROOT = Path(__file__).resolve().parents[1]
WORKER = "worker"
TASK_SECONDS = 4 * 3600  # Room for several runs; the task is closed when the session ends.
RUN_SECONDS, TIMEOUT = 1200, 180  # As scripts/demo.sh.
DIFF_LINES = 40  # The result shows this much of the diff; ``log`` shows all of it.
COPY_LIMIT = 1024 * 1024  # Larger files are not copied for the diff.
FINAL_ATTEMPT_STATES = ("finished", "failed", "cancelled", "blocked")


class Stop(Exception):
    """A step could not go ahead; the message says why in plain words."""


@dataclass(frozen=True)
class Settings:
    home: Path
    model: str
    provider_url: str
    key_env: str
    opencode: Path
    fuse_helper: Path


def project_directory(home: Path, project: Path) -> Path:
    """Trusted records for one project: a readable name plus a hash of its absolute path."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", project.name)[:40].lstrip(".") or "root"
    return home / "projects" / f"{name}-{hashlib.sha256(str(project).encode()).hexdigest()[:12]}"


def locate(home: Path, argument: str) -> tuple[Path, Path]:
    """The resolved project and its records directory, which must not overlap."""
    if not Path(argument).is_dir():
        raise Stop(f"{printable(argument)} is not a directory. Give the folder of the project to work on.")
    project, home = Path(argument).resolve(), home.resolve()
    if home.is_relative_to(project):
        raise Stop(f"The planner keeps its records in {printable(str(home))}, which is inside "
                   f"{printable(str(project))}. The worker must not see them: choose a smaller project "
                   "folder, or set PLANNER_HOME somewhere outside it.")
    if project.is_relative_to(home / "projects"):
        raise Stop(f"{printable(str(project))} holds the planner's own records, not a project.")
    return project, project_directory(home, project)


def check_provider(settings: Settings) -> None:
    """The model and provider settings, before anyone is asked for a key."""
    from .connector import ConnectorConfig, ProviderConfig

    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", settings.key_env):
        raise Stop("PLANNER_API_KEY_ENV must be an upper-case variable name.")
    try:
        ConnectorConfig(settings.model, provider=ProviderConfig(settings.provider_url, api_key_env=settings.key_env))
    except ValueError as error:
        raise Stop(f"Check PLANNER_MODEL and PLANNER_PROVIDER_URL: {printable(str(error))}") from None


def preflight(settings: Settings) -> str | None:
    """Why the connected runtime cannot run safely, or None when it can."""
    from .native.confined_opencode import runtime_status

    for status in runtime_status(settings.opencode, settings.fuse_helper):
        if not status.available:
            return status.reason
    return None


class Console:
    """The terminal, with everything shown or answered also kept in console.log."""

    def __init__(self, stdin: TextIO, stdout: TextIO, stderr: TextIO) -> None:
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self.log: TextIO | None = None
        self.lock = threading.Lock()  # The run's alerts arrive from a reader thread.

    def _write(self, stream: TextIO | None, text: str) -> None:
        with self.lock:
            if stream is not None:
                stream.write(text)
                stream.flush()
            if self.log is not None:
                self.log.write(text)
                self.log.flush()

    def say(self, text: str = "") -> None:
        self._write(self.stdout, text + "\n")

    def heading(self, text: str) -> None:
        self.say()
        self.say(style(text, "1"))

    def error(self, text: str) -> None:
        self._write(self.stderr, "ERROR: " + text + "\n")

    def relay(self, text: str) -> None:
        """Planner output, already escaped by the planner."""
        self._write(self.stderr, text)

    def read(self, prompt: str) -> str | None:
        """One line from the user, or None when there is no more input."""
        self._write(self.stdout, prompt)
        line = self.stdin.readline()
        if not line:
            self._write(self.stdout, "\n")
            return None
        answer = line.rstrip("\r\n")
        # A terminal has already shown the answer; piped input has not.
        self._write(None if self.stdin.isatty() else self.stdout, printable(answer) + "\n")
        return answer

    def ask(self, question: str, default: bool = False) -> bool:
        """A yes/no question; no input is never a yes."""
        answer = self.read(f"{question} [{'Y/n' if default else 'y/N'}] ")
        if answer is None:
            return False
        answer = answer.strip().lower()
        return default if not answer else answer.startswith("y")


class Planner:
    """The trusted planner CLI on one project's state, one subprocess per command."""

    def __init__(self, state: Path) -> None:
        self.state = state

    def command(self, *args: str) -> list[str]:
        return [sys.executable, "-m", "planner", "--state", str(self.state), *args]

    def call(self, *args: str) -> Any:
        """A short command's JSON result; Stop with the planner's reason if it refused."""
        process = subprocess.Popen(self.command(*args), cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
        interrupted = False
        while True:
            try:
                stdout, stderr = process.communicate()
                break
            except KeyboardInterrupt:  # Ctrl-C reaches the planner too; let it finish its transaction.
                interrupted = True
        if interrupted:
            raise KeyboardInterrupt
        if process.returncode != 0:
            reason = stderr.strip().splitlines()[-1] if stderr.strip() else f"exit status {process.returncode}"
            raise Stop(printable(reason.removeprefix("BLOCKED: "), limit=500))
        return json.loads(stdout)

    def run(self, args: list[str], output: Path, console: Console) -> int:
        """Run the worker: its result to ``output``, its live alerts to the console."""
        with output.open("w") as result:
            process = subprocess.Popen(self.command(*args), cwd=ROOT, stdin=subprocess.DEVNULL, stdout=result,
                                       stderr=subprocess.PIPE, text=True, errors="replace")
            assert process.stderr is not None
            stream = process.stderr

            def relay() -> None:
                for line in stream:
                    console.relay(line)

            reader = threading.Thread(target=relay)
            reader.start()
            try:
                return wait(process)
            finally:
                reader.join()
                stream.close()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class Guide:
    """One guided session: a task on one project, run as often as the user asks."""

    def __init__(self, settings: Settings, project: Path, prompt: str, *, plan: bool = False,
                 review: bool = False, yes: bool = False, planner: Planner | None = None,
                 console: Console | None = None) -> None:
        self.settings, self.project, self.prompt = settings, project, prompt
        self.plan, self.review, self.yes = plan, review, yes
        self.records = project_directory(settings.home, project)
        self.planner = planner or Planner(self.records / "state.sqlite")
        self.console = console or Console(sys.stdin, sys.stdout, sys.stderr)
        self.session = Path()
        self.task: str | None = None
        self.policy: Policy | None = None
        self.held: str | None = None  # A reviewed changeset the user has not applied yet.
        self.data: dict[str, Any] = {}

    # Records

    def save(self) -> None:
        (self.session / "session.json").write_text(json.dumps(self.data, indent=2) + "\n")

    def begin(self) -> None:
        self.records.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.records / "sessions").mkdir(mode=0o700, exist_ok=True)
        self.session = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=self.records / "sessions"))
        self.console.log = (self.session / "console.log").open("a", encoding="utf-8")
        self.data = {"created": time.time(), "project": str(self.project), "prompt": self.prompt,
                     "mode": "review" if self.review else "auto", "planned": self.plan,
                     "state": str(self.planner.state), "task": None, "approved": False, "runs": []}
        self.save()

    # The session

    def main(self) -> int:
        self.begin()
        try:
            return self.guide()
        except Stop as error:
            self.console.error(str(error))
            return 1
        finally:
            self.finish()

    def guide(self) -> int:
        say = self.console.say
        say(style("Planner", "1") + " - a confined coding agent works on your project")
        say("Project  " + printable(str(self.project), limit=1000))
        say("Task     " + printable(self.prompt, limit=500))
        proposal = self.propose()
        self.task = proposal["id"]
        self.data["task"] = self.task
        self.save()
        (self.session / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n")
        say()
        say(show_policy(proposal))
        if self.yes:
            say("Approve? [y/N] y (--yes)")
        elif not self.console.ask("Approve?"):
            self.planner.call("cancel", self.task)
            say("Not approved, so nothing ran. Your project is unchanged.")
            return 0
        self.planner.call("approve-task", self.task, "--revision", str(proposal["revision"]), "--assign", WORKER)
        self.data["approved"] = True
        self.save()
        status = self.planner.call("review", self.task)["policy"]
        self.policy = Policy(Path(status["root"]), read=tuple(status["read"]), write=tuple(status["write"]),
                             deny_read=tuple(status["deny_read"]), deny_write=tuple(status["deny_write"]),
                             tools=tuple(status["tools"]))
        number = 0
        while True:
            number += 1
            result, ok, suspended = self.run_worker(number)
            if result is None:
                return 1
            if result.get("outcome") == "stopped":
                return 130  # Ctrl-C ends the session.
            again = False
            if self.held is not None:
                ok = self.offer_apply() and ok
            if suspended:
                again = self.offer_resume(number)
            else:
                approved = self.answer_requests(number, result.get("requests", []))
                if approved:
                    say()
                    again = self.console.ask("Run again with these approvals?", default=True)
            if not again:
                return 0 if ok else 1
            say("The next run starts a fresh worker; it does not remember this one.")

    def propose(self) -> dict[str, Any]:
        lifetime = ("--task-seconds", str(TASK_SECONDS))
        if not self.plan:
            return dict(self.planner.call(*lifetime, "propose", str(self.project), "--template", "source"))
        self.console.say()
        self.console.say(f"Asking {printable(self.settings.model)} to propose the worker's permissions...")
        try:
            proposal = dict(self.planner.call(
                *lifetime, "plan", str(self.project), "--prompt", self.prompt, "--model", self.settings.model,
                "--provider-url", self.settings.provider_url, "--api-key-env", self.settings.key_env,
                "--timeout", str(TIMEOUT)))  # A large model can take minutes; the CLI default is 10 seconds.
        except Stop as error:
            raise Stop(f"The model's proposal was not usable: {error}. "
                       "Run again without --plan to use the built-in source template.") from None
        self.console.say(f"The model narrowed the trusted {printable(str(proposal['template']))} template. "
                         "It cannot grant more than that template allows.")
        return proposal

    def finish(self) -> None:
        """Close the task unless something still needs it, and say where the log is."""
        runs = self.data.get("runs", [])
        final = not runs or runs[-1].get("attempt_state") in FINAL_ATTEMPT_STATES
        if self.data.get("approved") and self.held is None and final:
            try:
                self.planner.call("cancel", str(self.task))  # Its approvals and grant stop working.
            except Stop:
                pass  # Already closed or expired.
        if self.held is not None:
            self.console.say()
            self.console.say("The held changes stay available until the task expires. To apply them later:")
            self.console.say(f"  python3 -m planner --state {self.planner.state} apply {self.held}")
        self.console.say()
        self.console.say("Full log:  scripts/run.sh log " + printable(shlex.quote(str(self.project)), limit=1000))
        self.console.say("Saved in   " + printable(str(self.session), limit=1000))
        if self.console.log is not None:
            self.console.log.close()
            self.console.log = None

    # One run

    def writable(self) -> list[str]:
        """Files the worker could change: its write scopes and approved write requests."""
        assert self.policy is not None and self.task is not None
        approvals = self.planner.call("export", self.task)["approvals"]
        extra = {Path(item["resource"]).relative_to(self.project).as_posix() for item in approvals
                 if item["operation"] == "write" and item["state"] == "approved"
                 and Path(item["resource"]).is_relative_to(self.project)}
        found = []
        for directory, folders, files in os.walk(self.project):
            folders[:] = [name for name in folders if name != ".git"]
            for name in files:
                relative = (Path(directory) / name).relative_to(self.project).as_posix()
                if relative in extra or self.policy.denied((relative,), self.policy.write):
                    found.append(relative)
        return found

    def copy(self, source: Path, destination: Path, paths: list[str]) -> None:
        """Copy small, regular, non-sensitive files so the diff can be shown later."""
        assert self.policy is not None
        for relative in paths:
            path = source / relative
            if (self.policy.is_sensitive((relative,)) or path.is_symlink() or not path.is_file()
                    or path.stat().st_size > COPY_LIMIT):
                continue
            (destination / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination / relative)

    def run_worker(self, number: int) -> tuple[dict[str, Any] | None, bool, bool]:
        """One confined run and its saved records: the result, whether it went well,
        and whether the supervisor suspended the worker for repeating a blocked operation."""
        assert self.task is not None
        directory = self.session / f"run-{number}"
        directory.mkdir()
        self.copy(self.project, directory / "before", self.writable())
        history = self.planner.call("history", self.task)
        entry: dict[str, Any] = {"number": number, "decisions_after": history[-1]["id"] if history else 0,
                                 "attempt": None, "attempt_state": None, "requests": [], "applied": None}
        self.data["runs"].append(entry)
        self.save()
        self.console.heading(f"Run {number}")
        self.console.say(f"Running the worker with {printable(self.settings.model)}. "
                         "This usually takes a few minutes...")
        args = ["run", self.task, WORKER, "--prompt", self.prompt, "--model", self.settings.model,
                "--provider-url", self.settings.provider_url, "--api-key-env", self.settings.key_env,
                "--opencode", str(self.settings.opencode), "--fuse-helper", str(self.settings.fuse_helper),
                "--runs", str(self.records / "runs"), "--run-seconds", str(RUN_SECONDS),
                "--timeout", str(TIMEOUT), "--alert"] + (["--mode", "review"] if self.review else [])
        self.planner.run(args, directory / "run.json", self.console)
        self.save_reports()
        result = _load(directory / "run.json")
        self.held = None
        if not isinstance(result, dict):
            self.console.error("The worker did not finish; the reason is shown above.")
            return None, False, False
        entry.update(attempt=result.get("attempt"), attempt_state=result.get("attempt_state", "blocked"))
        application = result.get("application") or {}
        source = self.project
        if application.get("state") == "retained" and application.get("journal"):
            self.held = str(result["attempt"])
            source = Path(application["journal"]).parent / "fs" / "backing"
        self.copy(source, directory / "after", [str(change["path"]) for change in application.get("changes", [])])
        self.save()
        rows = [row for row in self.planner.call("history", self.task) if row["id"] > entry["decisions_after"]]
        lines, problems = outcome(directory, result, rows, self.project, self.policy, DIFF_LINES)
        self.console.heading("Result")
        self.console.say("\n".join(lines))
        suspended = self.suspended(result)
        for problem in [] if suspended else problems:  # A suspension is explained instead.
            self.console.error(problem)
        return result, not problems and not suspended, suspended

    def save_reports(self) -> None:
        """The whole task's permission records and provenance report, after every run."""
        assert self.task is not None
        try:
            audit = self.planner.call("export", self.task)
            (self.session / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
            self.planner.call("report", self.task, str(self.session / "report.html"))
        except Stop as error:
            self.console.error("Could not save the run's records: " + str(error))

    # After a run

    def offer_apply(self) -> bool:
        assert self.held is not None
        entry = self.data["runs"][-1]
        entry["applied"] = False
        self.console.say()
        self.console.say("The changes are held for your review; your project is unchanged so far.")
        if not self.console.ask("Apply them now?"):
            self.save()
            return True
        try:
            applied = self.planner.call("apply", self.held)
        except Stop as error:
            applied = {"state": "blocked", "reason": str(error)}
        self.held = None
        entry["applied"] = applied.get("state") == "applied"
        self.save()
        if not entry["applied"]:
            self.console.error(f"They were not applied: {printable(str(applied.get('state')))} "
                               f"({printable(str(applied.get('reason')))}). Your project is unchanged.")
            return False
        self.console.say("Applied to your project.")
        return True

    def suspended(self, result: dict[str, Any]) -> bool:
        """Whether the supervisor suspended the worker for repeating a refused operation."""
        assert self.task is not None
        if self.planner.call("worker", self.task, WORKER)["state"] != "suspended":
            return False
        events = self.planner.call("attempt", str(result["attempt"]))["events"] if result.get("attempt") else []
        if any(event["reason"] == "repeated_denials" for event in events):
            return True
        self.console.error("The supervisor suspended the worker because it could not verify that the worker's "
                           "processes stopped. See 'recover' in docs/CONNECTED_RUNTIME.md.")
        return False

    def offer_resume(self, number: int) -> bool:
        assert self.task is not None
        say = self.console.say
        say()
        say(style("The worker was suspended", "1;33") + " because it kept repeating the same blocked operation.")
        say("Resuming lets a fresh worker try the task again with the same permissions.")
        if not self.console.ask("Resume the worker and run again?"):
            return False
        try:
            self.planner.call("resume", self.task, WORKER, "--opencode", str(self.settings.opencode),
                              "--fuse-helper", str(self.settings.fuse_helper))
        except Stop as error:
            self.console.error("Could not resume the worker: " + str(error))
            return False
        self.data["runs"][number - 1]["resumed"] = True
        self.save()
        return True

    def answer_requests(self, number: int, requests: list[dict[str, Any]]) -> int:
        """Ask once per file and operation; unapproved requests are refused. Returns how many were approved."""
        if not requests:
            return 0
        self.console.heading("Access requests")
        self.console.say("The worker was blocked from these. Each one you approve is allowed once, in the next run.")
        self.console.say("The ones you do not approve are refused.")
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for request in requests:  # One file can be refused to several tools, e.g. read and cat.
            groups.setdefault((str(request["operation"]), str(request["resource"])), []).append(request)
        approved = 0
        for group in groups.values():
            answer = self.answer(group)
            approved += answer == "approved"
            self.data["runs"][number - 1]["requests"].append(
                {key: group[0].get(key) for key in ("operation", "resource")}
                | {"ids": [request["id"] for request in group],
                   "sensitive": any(request.get("sensitive") for request in group), "answer": answer})
            self.save()
        return approved

    def answer(self, group: list[dict[str, Any]]) -> str:
        """Approve or refuse every request for one file and operation."""
        say, path = self.console.say, shown(str(group[0]["resource"]), self.project)
        reading = group[0]["operation"] == "read"
        say()
        say(f"  The worker wanted to {'read' if reading else 'change'} {path}.")
        disclose = reading and any(request.get("sensitive") for request in group)
        if disclose:
            say("  " + style("Warning:", "1;33") + f" {path} is protected because it may hold passwords, keys or "
                "tokens.")
            say(f"  Approving sends its contents to the model provider at {printable(self.settings.provider_url)}, "
                "which cannot be made to forget them.")
        if self.console.ask("  Approve?"):
            confirmed = not disclose or self.console.read(
                f"  Type yes to send {path} to the model provider: ") == "yes"
            if confirmed:
                try:
                    for request in group:
                        self.planner.call("approve-exception", str(request["id"]),
                                          *(["--confirm-sensitive-provider-disclosure"] if disclose else []))
                    say("  Approved for the next run.")
                    return "approved"
                except Stop as error:
                    say("  Could not approve it: " + str(error))
        for request in group:
            try:
                self.planner.call("refuse-exception", str(request["id"]))
            except Stop:
                pass  # Already closed; it stays unusable either way.
        say("  Refused.")
        return "refused"


def outcome(directory: Path, result: dict[str, Any], rows: list[dict[str, Any]], project: Path,
            policy: Policy | None, limit: int | None) -> tuple[list[str], list[str]]:
    """Plain-language result lines for one run, and the problems that make it a failure."""
    application = result.get("application") or {}
    state = application.get("state")
    changes = application.get("changes", [])
    paths = [str(change["path"]) for change in changes]
    refused = [row for row in rows if not row["allowed"] and not companion(row)]
    problems = []
    stopped = result.get("outcome") == "stopped"
    if stopped:
        pass  # Stopping on purpose is not a failure; the result says so.
    elif result.get("outcome") != "completed":
        cause = printable(str(result.get("error") or result.get("reason") or ""))
        problems.append(f"The worker did not finish: {printable(str(result.get('outcome')))} {cause}".rstrip())
    elif not result.get("cleanup_verified"):
        problems.append("The worker's processes could not be verified as stopped.")
    elif state not in ("applied", "retained"):
        problems.append(f"Changes were not applied: {printable(str(state))} "
                        f"({printable(str(application.get('reason')))}).")
    listed = ", ".join(shown(path, project) for path in paths) or "nothing"
    lines = [STOPPED] if stopped else []
    if state == "retained":
        lines.append("  held       " + listed + ("  (not in your project yet)" if paths else ""))
    else:
        lines.append("  changed    " + listed)
    if refused:
        lines.append("  blocked    " + style(f"{len(refused)} attempt{'' if len(refused) == 1 else 's'} "
                                             "outside the permissions", "1;33"))
    else:
        lines.append("  blocked    nothing - the worker stayed within its permissions")
    if not problems and not paths and not refused and not stopped:
        lines.append("  The model made no changes and tried nothing. If you expected changes, check "
                     "PLANNER_MODEL, PLANNER_PROVIDER_URL and the API key.")
    before, after = directory / "before", directory / "after"
    diff, missing = [], []
    for change in changes:
        name, operation = str(change["path"]), change["operation"]
        if operation not in ("add", "modify", "delete"):
            continue
        if ((policy is not None and policy.is_sensitive((name,)))
                or (operation != "add" and not (before / name).is_file())
                or (operation != "delete" and not (after / name).is_file())):
            missing.append(shown(name, project))
        else:
            diff += diff_lines(before, after, [name])
    if diff:
        visible = diff if limit is None else diff[:limit]
        lines += [""] + ["  " + line for line in visible]
        if len(visible) < len(diff):
            lines.append(f"  ... {len(diff) - len(visible)} more lines in the log")
    if missing:
        lines.append("  Not shown (protected, or larger than 1 MiB): " + ", ".join(missing))
    return lines, problems


def indent(lines: list[str], prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in lines)


def latest(home: Path, project: str | None) -> Path:
    """The most recent session, or the most recent one on a project."""
    home = home.resolve()
    if project is None:
        candidates = list(home.glob("projects/*/sessions/*/session.json"))
    else:
        candidates = list(project_directory(home, Path(project).resolve()).glob("sessions/*/session.json"))
    found = []
    for path in candidates:
        directory = path.parent
        data = _load(path)
        if isinstance(data, dict) and isinstance(data.get("created"), (int, float)):
            found.append((data["created"], directory))
    if not found:
        where = f" for {printable(project)}" if project else ""
        raise Stop(f"No runs found{where}. Start one with: scripts/run.sh PROJECT \"TASK\"")
    return max(found)[1]


def show_log(directory: Path, out: TextIO) -> None:
    """Everything worth knowing about one session, from its saved files."""
    from .supervisor import Supervisor

    def say(text: str = "") -> None:
        out.write(text + "\n")

    data = json.loads((directory / "session.json").read_text())
    project = Path(data["project"])
    proposal = _load(directory / "proposal.json")
    state = Path(data["state"])
    history: list[dict[str, Any]] = []
    if data.get("task") and state.is_file():
        supervisor = Supervisor(state)
        try:
            history = supervisor.history(data["task"])
        finally:
            supervisor.close()
    policy = None
    if isinstance(proposal, dict):
        config = proposal["policy"]
        policy = Policy(project, deny_read=tuple(config["deny_read"])) if project.is_dir() else None
    say(style(printable(str(project), limit=1000), "1") + ("  (review mode)" if data.get("mode") == "review" else ""))
    say("Files: " + printable(str(directory), limit=1000))
    say()
    say(style("Task", "1"))
    say("\n".join("  " + printable(line, limit=500) for line in str(data["prompt"]).splitlines()))
    say()
    if isinstance(proposal, dict):
        origin = (f"proposed by the model from the {printable(str(proposal.get('template')))} template"
                  if data.get("planned") else "the built-in source template")
        say(style("Permissions", "1") + f" - {origin}, " + ("approved" if data.get("approved") else "not approved"))
        say(indent(show_policy(proposal).splitlines(), "  "))
    else:
        say(style("Permissions", "1") + " - none were proposed; the reason is in console.log.")
    runs = data.get("runs", [])
    if data.get("approved") and not runs:
        say()
        say("  The worker did not run.")
    for index, entry in enumerate(runs):
        end = runs[index + 1]["decisions_after"] if index + 1 < len(runs) else None
        rows = [row for row in history if row["id"] > entry["decisions_after"] and (end is None or row["id"] <= end)]
        run = directory / f"run-{entry['number']}"
        say()
        say(style(f"Run {entry['number']}", "1"))
        say("  File access decisions recorded by the supervisor")
        say(indent(decision_lines(rows, project), "  "))
        say("  Result")
        result = _load(run / "run.json")
        if not isinstance(result, dict):
            say("    The worker did not finish; see console.log.")
        else:
            lines, problems = outcome(run, result, rows, project, policy, None)
            say(indent(lines + ["  problem: " + problem for problem in problems], "  "))
        if entry.get("applied") is not None:
            say("    held changes " + ("applied by you" if entry["applied"] else "not applied"))
        if entry.get("resumed"):
            say("    The worker was suspended for repeating a blocked operation; you resumed it.")
        if entry.get("requests"):
            say("  Access requests")
            for request in entry["requests"]:
                operation = "read" if request["operation"] == "read" else "change"
                say(f"    {operation} {shown(str(request['resource']), project)} - {printable(str(request['answer']))}"
                    + ("  (sensitive)" if request.get("sensitive") else ""))
    say()
    say(style("Saved files", "1"))
    for name, meaning in (("console.log", "everything the run printed"),
                          ("report.html", "provenance report; open it in a browser"),
                          ("audit.json", "complete permission records")):
        if (directory / name).is_file():
            say(f"  {printable(str(directory / name), limit=1000)}  ({meaning})")


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="scripts/run.sh", description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("check", "run"):
        command = commands.add_parser(name)
        command.add_argument("project")
        command.add_argument("prompt")
        command.add_argument("--plan", action="store_true")
        command.add_argument("--review", action="store_true")
        command.add_argument("--yes", action="store_true")
        command.add_argument("--home", type=Path, required=True)
        command.add_argument("--model", required=True)
        command.add_argument("--provider-url", required=True)
        command.add_argument("--api-key-env", required=True)
        command.add_argument("--opencode", type=Path, required=True)
        command.add_argument("--fuse-helper", type=Path, required=True)
    log = commands.add_parser("log")
    log.add_argument("project", nargs="?")
    log.add_argument("--home", type=Path, required=True)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "log":
            show_log(latest(args.home, args.project), sys.stdout)
            return 0
        if not args.prompt.strip():
            raise Stop("Say what the worker should do, e.g.: scripts/run.sh ~/code/app \"Fix the failing test\"")
        settings = Settings(args.home.resolve(), args.model, args.provider_url, args.api_key_env,
                            args.opencode, args.fuse_helper)
        check_provider(settings)
        project, _ = locate(args.home, args.project)
        if args.command == "check":
            return 0
        reason = preflight(settings)
        if reason is not None:
            print("BLOCKED: " + printable(reason, limit=500), file=sys.stderr)
            raise Stop("The planner cannot run safely yet; the reason is above. "
                       "Re-running scripts/install-ubuntu.sh usually fixes it.")
        return Guide(settings, project, args.prompt, plan=args.plan, review=args.review, yes=args.yes).main()
    except Stop as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
