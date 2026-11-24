"""Real-runtime test gates and helpers; not a test module."""

import os
from pathlib import Path
import shlex
import sys

from planner.scripted_provider import ScriptedProvider

__all__ = ["RUNTIME_GATE", "RUNTIME_REASON", "ScriptedProvider", "runtime_paths", "shell"]

RUNTIME_GATE = (sys.platform == "linux" and os.environ.get("PLANNER_RUN_CONFINED_OPENCODE") == "1")
RUNTIME_REASON = "Actual Linux, FUSE helper, loaded profile and pinned OpenCode binary are required"


def shell(script: str, description: str = "Run fixture script") -> tuple[str, dict[str, str]]:
    return "shell", {"command": "python3 -I -c " + shlex.quote(script), "description": description}


def runtime_paths() -> dict[str, Path]:
    return {"opencode": Path(os.environ["PLANNER_OPENCODE_BINARY"]),
            "fuse_helper": Path(os.environ["PLANNER_FUSE_BRIDGE"])}
