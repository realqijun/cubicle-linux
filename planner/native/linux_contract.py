"""Run dummy-data Ubuntu contract tests and save outcome metadata only."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print-profile", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("These tests require an actual Linux kernel")
    os.environ["PLANNER_RUN_LINUX_PROBES"] = "1"
    from planner.tests.test_linux_sandbox import LinuxProcessTests, WorkspaceTests

    if args.print_profile:
        fixture = LinuxProcessTests()
        try:
            fixture.setUp()
            print(fixture.sandbox.profile_text(), end="")
        finally:
            fixture.doCleanups()
        return 0
    if not args.output:
        parser.error("--output is required unless printing the test profile")
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(test)
                               for test in (LinuxProcessTests, WorkspaceTests))
    ids = [test.id() for group in suite for test in group]
    result = unittest.TestResult()
    started = datetime.now(timezone.utc).isoformat()
    suite.run(result)
    failures = {test.id() for test, _ in result.failures}
    errors = {test.id() for test, _ in result.errors}
    skipped = {test.id() for test, _ in result.skipped}
    base = Path(__file__).parents[1]
    record = {
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "host": {"system": platform.system(), "release": platform.release(),
                 "machine": platform.machine(), "python": platform.python_version()},
        "packages": subprocess.check_output(["dpkg-query", "-W", "-f=${Package} ${Version}\\n",
                                              "apparmor", "bubblewrap", "libseccomp2"], text=True).splitlines(),
        "source_sha256": {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
                          for name in ("native/linux_sandbox.py", "native/linux_contract.py",
                                       "tests/test_linux_sandbox.py", "policy.py")},
        "mode": "offline private workspace; no host-project mounts or automatic merge",
        "results": [{"test_id": name, "outcome": "failure" if name in failures else
                     "error" if name in errors else "skipped" if name in skipped else "passed"}
                    for name in ids],
        "tests_run": result.testsRun, "failures": len(failures), "errors": len(errors),
        "skipped": len(skipped), "exit_code": 0 if result.wasSuccessful() and not skipped else 1,
        "scope": "Dummy files only. No raw worker stdout/stderr, secret bytes or tracebacks saved.",
    }
    evidence: dict[str, Any] = {"schema": 1, "attempts": []}
    if args.output.exists():
        evidence = json.loads(args.output.read_text())
    evidence["attempts"].append(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({key: record[key] for key in ("tests_run", "failures", "errors", "skipped", "exit_code")}))
    return int(record["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
