"""Real-runtime test gates and helpers; not a test module."""

from collections.abc import Callable, Mapping
import os
from pathlib import Path
import re
import select
import shlex
import sys
import time

from planner.scripted_provider import ScriptedProvider

__all__ = ["RUNTIME_GATE", "RUNTIME_REASON", "ScriptedProvider", "press_ctrl_c", "runtime_paths", "shell"]

RUNTIME_GATE = (sys.platform == "linux" and os.environ.get("PLANNER_RUN_CONFINED_OPENCODE") == "1")
RUNTIME_REASON = "Actual Linux, FUSE helper, loaded profile and pinned OpenCode binary are required"


def shell(script: str, description: str = "Run fixture script") -> tuple[str, dict[str, str]]:
    return "shell", {"command": "python3 -I -c " + shlex.quote(script), "description": description}


def runtime_paths() -> dict[str, Path]:
    return {"opencode": Path(os.environ["PLANNER_OPENCODE_BINARY"]),
            "fuse_helper": Path(os.environ["PLANNER_FUSE_BRIDGE"])}


def press_ctrl_c(command: list[str], cwd: Path, env: Mapping[str, str], ready: Callable[[str], bool],
                 *, delay: float = 1, timeout: float = 1800) -> tuple[int, str]:
    """Run ``command`` in a terminal and press Ctrl-C once ``ready`` sees what it waits for.

    The terminal delivers SIGINT to the whole foreground process group, as it
    does for a person. Returns the exit status and the uncoloured output.
    """
    import pty  # POSIX only; imported here so the module still loads elsewhere.

    pid, terminal = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(command[0], command, dict(env))
    output, seen, pressed = b"", None, False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if select.select([terminal], [], [], 0.5)[0]:
                try:
                    chunk = os.read(terminal, 65536)
                except OSError:  # The terminal closes when the command exits.
                    break
                if not chunk:
                    break
                output += chunk
            if seen is None and ready(output.decode(errors="replace")):
                seen = time.monotonic()
            if not pressed and seen is not None and time.monotonic() - seen >= delay:
                os.write(terminal, b"\x03")
                pressed = True
    finally:
        if time.monotonic() >= deadline:
            os.kill(pid, 9)
        _, status = os.waitpid(pid, 0)
        os.close(terminal)
    text = re.sub(r"\x1b\[[0-9;]*m", "", output.decode(errors="replace")).replace("\r\n", "\n")
    if not pressed:
        raise AssertionError("Ctrl-C was never pressed:\n" + text[-3000:])
    return os.waitstatus_to_exitcode(status), text
