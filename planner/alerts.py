"""Live operator alerts for refused worker operations during a run.

Alerts are a display aid for the person watching a run. They take no part in
enforcement: each refusal is enforced and recorded before it is shown, and a
missing terminal, sound player or notification service changes no decision.
"""

from collections.abc import Callable
import html
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, TextIO

SOUND = Path("/usr/share/sounds/freedesktop/stereo/dialog-warning.oga")
SIGN = ("    /\\",
        "   /  \\     WARNING: PERMISSION VIOLATION",
        "  / !! \\    The worker tried an operation outside its approved permissions.",
        " /______\\   The supervisor refused it before it took effect.")
# Only what a desktop sound or notification client needs; never provider credentials.
_SESSION_VARIABLES = ("PATH", "HOME", "LANG", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
                      "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER")


def printable(text: str, limit: int = 160) -> str:
    """Escape control and formatting characters; worker-chosen names reach the terminal."""
    shown = "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)
    return shown if len(shown) <= limit else shown[:limit - 3] + "..."


def companion(decision: dict[str, Any]) -> bool:
    """A record that repeats a refusal already itemized, so displays skip it.

    The broker records its hook refusal beside the file decisions it refused,
    and a multi-resource operation marks its other resources refused with it.
    Both stay in the decision history and audit export.
    """
    reason, operation = str(decision["reason"]), str(decision["operation"])
    return reason == "Another resource in this operation was denied" or (
        operation == "mediation" and reason == "file_access_denied")


def describe(decision: dict[str, Any], root: Path) -> tuple[str, str]:
    """The attempted operation and the supervisor's reason, with project paths made relative."""
    resource = str(decision["resource"])
    if Path(resource).is_absolute() and Path(resource).is_relative_to(root):
        resource = Path(resource).relative_to(root).as_posix() or "."
    operation = str(decision["operation"])
    attempted = f"use {resource}" if operation == "mediation" else f"{operation} {resource}"
    return printable(attempted), printable(f"{decision['tool']}: {decision['reason']}")


def _desktop_session() -> bool:
    """A sound or notification on a remote host would reach nobody; the bell travels over SSH."""
    graphical = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    return sys.platform == "linux" and graphical and not os.environ.get("SSH_CONNECTION")


def _desktop_commands(attempted: str) -> list[list[str]]:
    commands = []
    if shutil.which("canberra-gtk-play"):
        commands.append(["canberra-gtk-play", "--id", "dialog-warning", "--description", "Planner refusal"])
    elif SOUND.is_file():
        player = shutil.which("paplay") or shutil.which("pw-play")
        if player:
            commands.append([player, str(SOUND)])
    if shutil.which("notify-send"):  # The body may be rendered as markup.
        commands.append(["notify-send", "--urgency=critical", "--app-name=Planner", "--icon=dialog-warning",
                         "Planner blocked an operation",
                         html.escape(f"The worker tried to {attempted}. The supervisor refused it.")])
    return commands


class Alarm:
    """Print each refusal as a highlighted warning and sound an alert.

    Every refusal is printed. The warning sign, bell, desktop sound and
    notification repeat at most once per ``quiet_seconds`` so a burst of
    refusals is one alert.
    """

    def __init__(self, root: Path, stream: TextIO | None = None, *, quiet_seconds: float = 3.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.root, self.stream = root, stream if stream is not None else sys.stderr
        self.quiet_seconds, self.clock = quiet_seconds, clock
        self.color = not os.environ.get("NO_COLOR")
        self.count = 0
        self._sounded = -math.inf
        self._players: list[subprocess.Popen[bytes]] = []

    def __call__(self, decision: dict[str, Any]) -> None:
        if companion(decision):
            return
        self.count += 1
        attempted, reason = describe(decision, self.root)
        now = self.clock()
        sound = now - self._sounded >= self.quiet_seconds
        if sound:
            self._sounded = now
        if self.color:
            sign = "".join(f"\033[1;33m{row}\033[0m\n" for row in SIGN)
            line = f"\033[1;97;41m BLOCKED \033[0m The worker tried to \033[1m{attempted}\033[0m - refused ({reason})"
        else:
            sign = "".join(row + "\n" for row in SIGN)
            line = f"[BLOCKED] The worker tried to {attempted} - refused ({reason})"
        try:
            self.stream.write(("\a\n" + sign if sound else "") + line + "\n")
            self.stream.flush()
        except (OSError, ValueError):
            pass  # A closed terminal must not change the run.
        if sound and _desktop_session():
            self._start(_desktop_commands(attempted))

    def _start(self, commands: list[list[str]]) -> None:
        self._players = [player for player in self._players if player.poll() is None]
        environment = {name: os.environ[name] for name in _SESSION_VARIABLES if name in os.environ}
        for command in commands:
            try:
                self._players.append(subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                      stderr=subprocess.DEVNULL, env=environment))
            except OSError:
                pass

    def close(self, seconds: float = 2.0) -> None:
        """Let a playing alert finish briefly, then stop it."""
        deadline = time.monotonic() + seconds
        for player in self._players:
            try:
                player.wait(max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                player.kill()
                player.wait()
        self._players = []
