"""Fail closed until an OS backend has passed the complete policy contract.

Tool filtering is not an OS sandbox. Detecting a candidate executable or kernel
feature does not establish that it faithfully enforces a Policy.
"""

from collections.abc import Sequence
from pathlib import Path
import platform
import shutil
import subprocess

from .policy import Policy
from .records import Grant, ProtectionStatus


class ProtectionUnavailable(RuntimeError):
    def __init__(self, status: ProtectionStatus) -> None:
        self.status = status
        super().__init__(f"BLOCKED: {status.backend}: {status.reason}")


class Backend:
    """The same launch gate on every platform; no unsafe fallback or override."""

    def check(self, policy: Policy, grant: Grant) -> ProtectionStatus:
        policy.validate_grant(grant)
        system = platform.system()
        if system == "Linux":
            candidate = "Bubblewrap detected" if shutil.which("bwrap") else "Bubblewrap not found"
            reason = (
                f"{candidate}; no validated backend enforces dynamic deny patterns, "
                "link semantics, descendants, and inherited descriptors. "
                "Landlock tree allowances alone cannot subtract denied name patterns."
            )
            backend = "linux"
        elif system == "Darwin":
            reason = (
                "No validated macOS confinement backend. The deprecated sandbox-exec "
                "interface has not passed this policy contract and is not enabled."
            )
            backend = "macos"
        elif system == "Windows":
            reason = (
                "No validated Windows path-policy backend. A restricted token or "
                "AppContainer identity alone does not establish the declared path policy."
            )
            backend = "windows"
        else:
            reason = f"Unsupported operating system: {system or 'unknown'}"
            backend = "unsupported"
        return ProtectionStatus(False, backend, reason)

    def run(
        self,
        policy: Policy,
        grant: Grant,
        argv: Sequence[str],
        *,
        timeout: float,
        runtime_paths: Sequence[Path] = (),
    ) -> subprocess.CompletedProcess[str]:
        """Refuse before execution until confinement is implemented and verified.

        ``runtime_paths`` describes proposed resources; supplying it is not a grant.
        No supplied command, even a diagnostic or evaluator, runs unconfined.
        """
        if not argv or any(not isinstance(part, str) or not part for part in argv):
            raise ValueError("A non-empty argument vector is required")
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        status = self.check(policy, grant)
        raise ProtectionUnavailable(status)
