"""Run disposable macOS candidate probes and save sanitized outcome metadata."""

import argparse
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest
from typing import Any
import re
import tempfile

from planner.enforcement import ProtectionUnavailable
from planner.policy import Policy
from planner.records import Grant, ProtectionStatus


def _macos_glob(root: Path, pattern: str, *, ancestors: bool = False) -> str:
    """Compile only the small glob subset tested against the candidate kernel."""
    if "[" in pattern or "]" in pattern:
        raise ValueError("macOS candidate does not enforce bracket glob patterns")
    if pattern == "**":
        return "^" + re.escape(str(root)) + "(/.*)?$"
    parts = pattern.split("/")
    expression = ""
    for index, part in enumerate(parts):
        if part == "**":
            expression += "(/.*)?" if index == len(parts) - 1 and index else (
                ".*" if len(parts) == 1 else "([^/]+/)*")
            continue
        expression += "".join("[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char)
                              for char in part)
        if index + 1 < len(parts) and parts[index + 1] != "**":
            expression += "/"
        elif index + 1 < len(parts) - 1:
            expression += "/"
    suffix = "(/.*)?" if ancestors and parts[-1] != "**" else ""
    return "^" + re.escape(str(root)) + "/" + expression + suffix + "$"


class MacOSFixture:
    """Explicit diagnostic prototype, never a substitute for Backend.run.

    Seatbelt's private/deprecated interface and this profile have not passed the
    full policy contract. Only disposable, dummy-data experiments may use it.
    """

    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="planner-macos-probe-")
        self.root = Path(self.temporary.name).resolve() / "project"
        self.root.mkdir()

    def close(self) -> None:
        self.temporary.cleanup()

    def run_probe(self, script: str, *, timeout: float = 5) -> subprocess.CompletedProcess[str]:
        # No caller project, task grant, executable or runtime mount is accepted.
        # The fixture always owns its fresh temporary project and static policy.
        policy = Policy(self.root, read=("**",), write=("src/**",),
                        deny_read=(".env*", "**/.env*"), deny_write=("tests/**",), tools=("shell",))
        grant = Grant("probe", "worker", policy.read, policy.write, policy.tools)
        argv = [sys.executable, "-I", "-c", script]
        runtime_paths = [Path(sys.base_prefix)]
        gettext = Path("/opt/homebrew/opt/gettext")
        if gettext.exists():
            runtime_paths.append(gettext)
        if platform.system() != "Darwin":
            raise ValueError("macOS diagnostic requires Darwin")
        policy.validate_grant(grant)
        if "shell" not in grant.tools:
            raise ValueError("The diagnostic worker must be assigned shell access")
        if not argv or timeout <= 0:
            raise ValueError("Arguments and a positive timeout are required")
        if any(path.is_file() and path.stat().st_nlink > 1 for path in policy.root.rglob("*")):
            raise ProtectionUnavailable(ProtectionStatus(
                False, "macos-candidate", "Project contains hardlinked files; refusing diagnostic launch"))
        runtime = tuple(dict.fromkeys(path for p in runtime_paths
                                      for path in (Path(p).absolute(), Path(p).resolve(strict=True))))
        if any(p == Path(p.anchor) or p == Path.home() for p in runtime):
            raise ValueError("Runtime resources must not expose a filesystem root or home")

        def selector(kind: str, path: str) -> str:
            return f"({kind} {json.dumps(path, ensure_ascii=False)})"

        runtime_rules = [selector("subpath", p) for p in ("/usr", "/System", "/Library", "/bin", "/sbin")]
        runtime_rules += [selector("literal", p) for p in ("/", "/dev", "/dev/null")]
        runtime_rules += [selector("subpath" if p.is_dir() else "literal", str(p)) for p in runtime]
        read_rules = runtime_rules + [selector("regex", _macos_glob(policy.root, p)) for p in grant.read]
        write_rules = [selector("literal", "/dev/null")] + [
            selector("regex", _macos_glob(policy.root, p)) for p in grant.write]
        metadata = list(read_rules)
        for path in (policy.root, *runtime):
            metadata += [selector("literal", str(p)) for p in (path, *path.parents)]
        profile = ["(version 1)", "(allow default)"]
        for operation, rules in (("file-read-data", read_rules), ("file-read-metadata", metadata),
                                 ("file-write*", write_rules), ("process-exec", read_rules)):
            profile.append(f"(deny {operation} (require-not (require-any {' '.join(rules)})))")
        for operation, patterns in (("file-read*", policy.deny_read), ("file-write*", policy.deny_write)):
            for pattern in patterns:
                profile.append(f"(deny {operation} {selector('regex', _macos_glob(policy.root, pattern, ancestors=True))})")
        # Conservative diagnostic restriction: a read-denied inode cannot be
        # moved to an allowed name. This is stricter than the policy write scope.
        for pattern in policy.deny_read:
            profile.append(f"(deny file-write-unlink {selector('regex', _macos_glob(policy.root, pattern, ancestors=True))})")
        profile += ["(deny file-link)", "(deny mach-lookup)", "(deny network*)",
                    "(deny process-info* (target others))", "(deny signal (target others))",
                    "(deny appleevent-send)", "(deny authorization-right-obtain)"]
        return subprocess.run(
            ["/usr/bin/sandbox-exec", "-p", "\n".join(profile), *argv],
            cwd=policy.root, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
            close_fds=True, start_new_session=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("These diagnostics require a real macOS host")
    os.environ["PLANNER_RUN_MACOS_PROBES"] = "1"
    from planner.tests.test_enforcement import MacOSCandidateTests

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(MacOSCandidateTests)
    result = unittest.TestResult()
    started = datetime.now(timezone.utc).isoformat()
    suite.run(result)
    failures = {test.id() for test, _ in result.failures}
    errors = {test.id() for test, _ in result.errors}
    skipped = {test.id() for test, _ in result.skipped}
    ids = unittest.defaultTestLoader.getTestCaseNames(MacOSCandidateTests)
    prefix = "planner.tests.test_enforcement.MacOSCandidateTests."
    record = {
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "host": {"system": platform.system(), "release": platform.release(),
                 "machine": platform.machine(), "macos_version": platform.mac_ver()[0],
                 "build": subprocess.check_output(["/usr/bin/sw_vers", "-buildVersion"], text=True).strip()},
        "candidate": "private/deprecated sandbox-exec SBPL diagnostic",
        "candidate_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "tests_sha256": hashlib.sha256((Path(__file__).parents[1] / "tests/test_enforcement.py").read_bytes()).hexdigest(),
        "python_version": platform.python_version(),
        "production_backend": "blocked; candidate is not enabled",
        "command": "python3 -m planner.native.macos_contract --output planner/evidence/macos-candidate.json",
        "results": [
            {"test_id": prefix + name,
             "outcome": "failure" if prefix + name in failures else
                        "error" if prefix + name in errors else
                        "skipped" if prefix + name in skipped else "passed"}
            for name in ids
        ],
        "tests_run": result.testsRun, "failures": len(failures), "errors": len(errors),
        "skipped": len(skipped), "exit_code": 0 if result.wasSuccessful() else 1,
        "scope": "Disposable dummy files only; no raw stdout/stderr or secret contents persisted.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {"schema": 1, "attempts": []}
    if args.output.exists():
        evidence = json.loads(args.output.read_text(encoding="utf-8"))
    evidence["attempts"].append(record)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests_run": result.testsRun, "failures": len(failures),
                      "errors": len(errors), "evidence": str(args.output)}, indent=2))
    raise SystemExit(record["exit_code"])


if __name__ == "__main__":
    main()
