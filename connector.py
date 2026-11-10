"""Pinned OpenCode preflight. Task execution stays blocked until mediation is verified."""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from planner.records import ProtectionStatus

PINNED_VERSION = "2.2.5"


@dataclass(frozen=True)
class ConnectorConfig:
    model: str
    executable: str = "opencode"
    timeout_seconds: float = 10

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[^/\s]+/\S+", self.model):
            raise ValueError("model must be an explicit provider/model identifier")
        if not self.executable or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("executable and a finite positive timeout are required")


@dataclass(frozen=True)
class LaunchSpec:
    """Inspectable proposal only; not evidence that starting this command is safe."""

    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


class Connector:
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    def prepare(self, runtime_root: Path, prompt: str, *, project: Path | None = None) -> LaunchSpec:
        """Prepare isolated runtime data without starting a server or model call.

        The caller must not execute the proposal while check() is blocking.
        An OS backend must also confine the server, its tools, and descendants.
        """
        executable = shutil.which(self.config.executable)
        if executable is None:
            raise RuntimeError("OpenCode executable unavailable")
        runtime = Path(tempfile.mkdtemp(prefix="opencode-", dir=runtime_root))
        environment = isolated_environment(runtime)
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps({
            "model": self.config.model,
            "snapshots": False,
            "update": "disable",
        })
        return LaunchSpec(
            (str(Path(executable).resolve()), "run", "--standalone", "--format", "json",
             "--model", self.config.model, "--", prompt),
            (project or runtime).resolve(strict=True), environment,
        )

    def check(self) -> ProtectionStatus:
        executable = shutil.which(self.config.executable)
        if executable is None:
            return ProtectionStatus(False, "opencode", "OpenCode executable unavailable")
        try:
            with tempfile.TemporaryDirectory(prefix="planner-opencode-") as directory:
                root = Path(directory)
                environment = isolated_environment(root)
                result = subprocess.run(
                    [executable, "--version"], cwd=root, env=environment,
                    capture_output=True, text=True, timeout=self.config.timeout_seconds,
                    close_fds=True, check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            return ProtectionStatus(False, "opencode", "OpenCode version probe unavailable")
        if result.returncode != 0 or result.stdout.strip() != f"opencode v{PINNED_VERSION}":
            return ProtectionStatus(False, "opencode", f"OpenCode incompatible; requires {PINNED_VERSION}")
        return ProtectionStatus(
            False, "opencode",
            "OpenCode tool interception, model-input provenance, isolated configuration, "
            "and sensitive session persistence remain unverified; task launch blocked",
        )


def isolated_environment(root: Path) -> dict[str, str]:
    """Build a fresh probe environment without ambient credentials or configuration."""
    paths = {name: root / name.lower() for name in (
        "HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR",
    )}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {name: str(path) for name, path in paths.items()}
    environment.update({
        "PATH": os.defpath,
        "OPENCODE_TEST_HOME": str(paths["HOME"]),
        "OPENCODE_CONFIG_DIR": str(paths["XDG_CONFIG_HOME"]),
        "OPENCODE_CONFIG_PROJECT_DISABLE": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DB": ":memory:",
        "OPENCODE_LOG_LEVEL": "none",
    })
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return environment
