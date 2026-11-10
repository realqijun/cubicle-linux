"""Pinned OpenCode preflight. Task execution stays blocked until mediation is verified."""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Mapping

from planner.records import ProtectionStatus

PINNED_VERSION = "2.2.5"
REQUIRED_HOOKS = (
    "session.prompt", "tool.execute.before", "tool.execute.after", "session.context",
    "session.compaction", "session.generate", "session.title", "session.model.request",
    "session.http.request", "session.http.response", "session.experimental.ws.handshake",
    "session.experimental.ws.send", "session.experimental.ws.receive", "aisdk.sdk", "aisdk.language",
    "permission.evaluate", "shell.create.before",
)


@dataclass(frozen=True)
class BridgeConfig:
    endpoint: str
    token: str = field(repr=False)
    timeout_seconds: float = 10
    max_body_bytes: int = 4194304

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1")
                or parsed.path != "/admit" or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Broker endpoint must be a private loopback /admit URL")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", self.token):
            raise ValueError("A supervisor-generated broker token is required")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0 or type(self.max_body_bytes) is not int or self.max_body_bytes < 1:
            raise ValueError("Bridge timeout and body limit must be positive")


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key_env: str | None = None
    context_tokens: int = 32768
    output_tokens: int = 4096

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        local = parsed.hostname in ("127.0.0.1", "::1")
        if (not parsed.hostname or parsed.scheme not in ("http", "https") or
                (parsed.scheme == "http" and not local) or parsed.username or parsed.password or
                parsed.query or parsed.fragment):
            raise ValueError("Provider URL must use HTTPS or a loopback HTTP fixture")
        if self.api_key_env is not None and (not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.api_key_env) or
                self.api_key_env in ("HOME", "PATH", "TMPDIR", "SYSTEMROOT") or
                self.api_key_env.startswith(("PLANNER_", "OPENCODE_", "XDG_", "LD_", "DYLD_", "PYTHON", "NODE_", "BUN_"))):
            raise ValueError("Provider credential environment name is reserved or invalid")
        if self.output_tokens < 1 or self.context_tokens < self.output_tokens:
            raise ValueError("Provider token limits must be positive and fit its context")


@dataclass(frozen=True)
class ConnectorConfig:
    model: str
    executable: str = "opencode"
    timeout_seconds: float = 10
    provider: ProviderConfig | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[^/\s]+/\S+", self.model):
            raise ValueError("model must be an explicit provider/model identifier")
        if not self.executable or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("executable and a finite positive timeout are required")


@dataclass(frozen=True)
class LaunchSpec:
    """Inspectable proposal only; not evidence that starting this command is safe."""

    argv: tuple[str, ...] = field(repr=False)
    cwd: Path
    env: dict[str, str] = field(repr=False)


class Connector:
    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    def prepare_server(self, runtime_root: Path, *, project: Path, bridge: BridgeConfig,
                       server_password: str, credentials: Mapping[str, str] | None = None) -> LaunchSpec:
        """Boot proposal without task data; require authenticated hook readiness first.

        The owner must retain the stdio input lease and verify the bridge before
        submitting a task through the private server API. This is not execution.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", server_password):
            raise ValueError("A fresh private server password is required")
        proposal = self.prepare(runtime_root, "", project=project, bridge=bridge, credentials=credentials)
        proposal.env["OPENCODE_PASSWORD"] = server_password
        return replace(proposal, argv=(proposal.argv[0], "serve", "--stdio", "--hostname", "127.0.0.1", "--port", "0"))

    def prepare(self, runtime_root: Path, prompt: str, *, project: Path | None = None,
                bridge: BridgeConfig | None = None,
                credentials: Mapping[str, str] | None = None) -> LaunchSpec:
        """Prepare isolated runtime data without starting a server or model call.

        The caller must not execute the proposal while check() is blocking.
        An OS backend must also confine the server, its tools, and descendants.
        """
        executable = shutil.which(self.config.executable)
        if executable is None:
            raise RuntimeError("OpenCode executable unavailable")
        runtime = Path(tempfile.mkdtemp(prefix="opencode-", dir=runtime_root))
        environment = isolated_environment(runtime)
        config: dict[str, object] = {
            "model": self.config.model,
            "snapshots": False,
            "update": "disable",
            "warming": False,
            "formatter": False,
            "instructions": [],
            "mcp": {},
        }
        if bridge is not None:
            config["plugins"] = [str(Path(__file__).parent / "opencode")]
            environment.update(PLANNER_BROKER_URL=bridge.endpoint, PLANNER_BROKER_TOKEN=bridge.token,
                               PLANNER_BROKER_TIMEOUT_MS=str(bridge.timeout_seconds * 1000),
                               PLANNER_MAX_BODY_BYTES=str(bridge.max_body_bytes))
        provider = self.config.provider
        expected = {provider.api_key_env} if provider and provider.api_key_env else set()
        if set(credentials or {}) != expected:
            raise ValueError("Provide exactly the explicitly configured provider credentials")
        if bridge is not None:
            environment["PLANNER_CREDENTIAL_VARS"] = json.dumps(sorted(expected))
        if provider is not None:
            provider_id, model_id = self.config.model.split("/", 1)
            config["providers"] = {provider_id: {
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": {"baseURL": provider.base_url},
                "env": sorted(expected),
                "models": {model_id: {"capabilities": {"tools": True},
                                      "limit": {"context": provider.context_tokens, "output": provider.output_tokens}}},
            }}
        for name, value in (credentials or {}).items():
            if not value:
                raise ValueError("Provider credential must not be empty")
            environment[name] = value
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
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
            "OpenCode HTTP bridge implemented; full-route provenance, configuration isolation, "
            "and sensitive persistence require runtime acceptance; task launch blocked",
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
