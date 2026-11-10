"""Small immutable records shared by enforcement and persistence."""

from dataclasses import dataclass
from typing import Literal

Operation = Literal["read", "write"]


@dataclass(frozen=True)
class Action:
    task_id: str
    worker_id: str
    operation: Operation
    resource: str
    tool: str = "file"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    resource: str
    sensitive: bool = False
    approval_id: str | None = None


@dataclass(frozen=True)
class Grant:
    task_id: str
    worker_id: str
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    tools: tuple[str, ...] = ("file",)
    parent_id: str | None = None


@dataclass(frozen=True)
class ProtectionStatus:
    available: bool
    backend: str
    reason: str
    verified: bool = False
