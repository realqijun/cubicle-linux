"""Plain-language terminal output shared by the guided scripts.

``scripts/demo.sh`` and ``scripts/run.sh`` show permissions, supervisor
decisions and diffs the same way. Every worker- or model-chosen string is
escaped with ``printable`` before it reaches the terminal.
"""

from datetime import datetime
import difflib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .alerts import companion, describe, printable
from .records import operator_action

# The planner records a run stopped with Ctrl-C as outcome "stopped" and applies none of its edits.
STOPPED = "  stopped    you pressed Ctrl-C, so nothing from this run was applied"


def style(text: str, code: str) -> str:
    return text if os.environ.get("NO_COLOR") else f"\033[{code}m{text}\033[0m"


def wait(process: subprocess.Popen[Any]) -> int:
    """Wait for the planner, including after Ctrl-C, which reaches it too.

    ``subprocess.run`` would kill it 0.25 seconds after an interrupt, before
    it has stopped the worker, unmounted the workspace and closed its records.
    """
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            print("Still waiting for the planner to stop the worker and clean up...", file=sys.stderr)


def show_policy(proposal: dict[str, Any]) -> str:
    """The exact proposed permissions, one line per kind of access."""
    policy = proposal["policy"]

    def patterns(key: str) -> str:
        return ", ".join(printable(pattern) for pattern in policy[key]) or "nothing"

    lines = ["Permissions for the worker, in " + printable(policy["root"]) + ":",
             f"  read   {patterns('read')}" + (f"  except {patterns('deny_read')}" if policy["deny_read"] else ""),
             f"  write  {patterns('write')}" + (f"  except {patterns('deny_write')}" if policy["deny_write"] else ""),
             f"  tools  {patterns('tools')}"]
    if policy.get("external_inputs"):
        lines.append(f"  also read outside the project: {patterns('external_inputs')}")
    return "\n".join(lines)


def application_check(row: dict[str, Any]) -> bool:
    """A check the changeset applier made before writing to the project, not a worker action.

    In a run only the applier records ``shell`` writes: the broker records no
    decision for shell commands, and the mediated filesystem records ``filesystem``.
    """
    return bool(row["tool"] == "shell" and row["operation"] == "write")


def shown(path: str, root: Path) -> str:
    """A path for display, relative to the project when it is inside it."""
    candidate = Path(path)
    return printable(candidate.relative_to(root).as_posix() if candidate.is_relative_to(root) else path)


def _clock(row: dict[str, Any]) -> str:
    return datetime.fromtimestamp(row["created"]).strftime("%H:%M:%S")


def decision_line(row: dict[str, Any], root: Path) -> str:
    at = _clock(row)
    attempted, reason = describe(row, root)
    if row["allowed"]:
        return f"  {at}  allowed  {attempted}"
    return f"  {at}  {style('BLOCKED', '1;31')}  {attempted}  ({reason})"


def decision_lines(history: list[dict[str, Any]], root: Path) -> list[str]:
    """The worker's file accesses, then the applier's checks, one line each."""
    rows = [row for row in history if not companion(row) and not operator_action(row)]
    worker = [row for row in rows if not application_check(row)]
    # The applier checks each change twice (validate, then apply); one line says the same.
    applying = list({(row["operation"], row["resource"], row["allowed"]): row
                     for row in rows if application_check(row)}.values())
    lines = [decision_line(row, root) for row in worker] or ["  none"]
    if applying:
        lines.append("  The worker stopped. Before copying its changes into your project, the supervisor checked again:")
        lines += [decision_line(row, root) for row in applying]
    return lines


def operator_lines(history: list[dict[str, Any]]) -> list[str]:
    """What the operator did, one line each; the supervisor records it beside the file accesses."""
    return [f"  {_clock(row)}  you resumed the worker" for row in history if operator_action(row)]


def diff_lines(before: Path, after: Path, paths: list[str]) -> list[str]:
    """A coloured unified diff of ``paths`` between two trees; a missing file is empty."""
    lines: list[str] = []
    for name in paths:
        old_file, new_file = before / name, after / name
        try:
            old = old_file.read_text().splitlines() if old_file.is_file() else []
            new = new_file.read_text().splitlines() if new_file.is_file() else []
        except UnicodeDecodeError:
            lines.append(f"(binary file {printable(name)} changed)")
            continue
        for line in difflib.unified_diff(old, new, f"{name} (before)", f"{name} (after)", lineterm=""):
            line = printable(line, limit=200)
            if line.startswith("+") and not line.startswith("+++"):
                line = style(line, "32")
            elif line.startswith("-") and not line.startswith("---"):
                line = style(line, "31")
            lines.append(line)
    return lines
