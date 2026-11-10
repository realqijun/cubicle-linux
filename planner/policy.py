"""Filesystem policy decisions. OS enforcement is separately required for execution.

Patterns use slash-separated components: * and ? stay within a component;
** matches zero or more components. Denials cover matching ancestors too.
"""

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path, PurePosixPath
import os
import tomllib
from typing import Any

from .records import Action, Decision, Grant


def canonical_path(path: Path) -> Path:
    """Use on-disk spelling, including case/Unicode aliases, when it exists."""
    resolved = path.resolve(strict=False)
    current = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        candidate = current / part
        if candidate.exists():
            names = tuple(current.iterdir())
            if not any(entry.name == part for entry in names):
                candidate = next(entry for entry in names if entry.samefile(candidate))
        current = candidate
    return current


def matches(path: str, pattern: str) -> bool:
    parts, rules = PurePosixPath(path).parts, PurePosixPath(pattern).parts

    @lru_cache(None)
    def visit(i: int, j: int) -> bool:
        if j == len(rules):
            return i == len(parts)
        if rules[j] == "**":
            return visit(i, j + 1) or (i < len(parts) and visit(i + 1, j))
        return i < len(parts) and fnmatchcase(parts[i], rules[j]) and visit(i + 1, j + 1)

    return visit(0, 0)


def validate_patterns(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) for v in values):
        raise ValueError("Scopes must be lists of relative path patterns")
    result = tuple(str(v) for v in values)
    for value in result:
        if not value or value.startswith("/") or "\\" in value or ":" in value:
            raise ValueError(f"Invalid relative scope: {value!r}")
        if any(p in ("", ".", "..") for p in value.split("/")):
            raise ValueError(f"Invalid relative scope: {value!r}")
        if any("**" in p and p != "**" for p in value.split("/")):
            raise ValueError("** must occupy a whole path component")
    return result


def covered(scopes: tuple[str, ...], ceilings: tuple[str, ...]) -> bool:
    """Conservative containment: equal scopes or literal descendants of tree scopes.

    We deliberately do not guess whether arbitrary glob languages overlap.
    """
    for scope in scopes:
        if scope in ceilings or "**" in ceilings:
            continue
        if any(c.endswith("/**") and scope.startswith(c[:-2]) for c in ceilings):
            continue
        if not any(c in scope for c in "*?[") and any(matches(scope, c) for c in ceilings):
            continue
        return False
    return True


@dataclass(frozen=True)
class Policy:
    root: Path
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    deny_read: tuple[str, ...] = ()
    deny_write: tuple[str, ...] = ()
    tools: tuple[str, ...] = ("file",)
    external_inputs: tuple[Path, ...] = ()
    root_identity: tuple[int, int] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", canonical_path(self.root))
        if not self.root.is_dir():
            raise ValueError("Project root must be a directory")
        stat = self.root.stat()
        object.__setattr__(self, "root_identity", (stat.st_dev, stat.st_ino))
        for name in ("read", "write", "deny_read", "deny_write"):
            object.__setattr__(self, name, validate_patterns(getattr(self, name)))
        if any(not isinstance(t, str) or not t for t in self.tools):
            raise ValueError("Tool names must be non-empty strings")
        object.__setattr__(self, "external_inputs", tuple(canonical_path(p) for p in self.external_inputs))
        if any(not p.is_file() for p in self.external_inputs):
            raise ValueError("External inputs must be individual existing files")

    @classmethod
    def load(cls, root: Path, path: Path) -> "Policy":
        with path.open("rb") as stream:
            config = tomllib.load(stream)
        if set(config) - {"files", "tools", "external"}:
            raise ValueError("Unknown policy section")
        files = config.get("files", {})
        if not isinstance(files, dict):
            raise ValueError("files must be a TOML table")
        if set(files) - {"read", "write", "deny_read", "deny_write"}:
            raise ValueError("Unknown file policy setting")
        tools = config.get("tools", {})
        external = config.get("external", {})
        if not isinstance(tools, dict) or not isinstance(external, dict):
            raise ValueError("tools and external must be TOML tables")
        if set(tools) - {"allow"} or set(external) - {"read"}:
            raise ValueError("Unknown tools or external setting")
        allowed_tools = tools.get("allow", ["file"])
        if not isinstance(allowed_tools, list) or any(not isinstance(t, str) for t in allowed_tools):
            raise ValueError("tools.allow must be a list of names")
        inputs = external.get("read", [])
        if not isinstance(inputs, list) or any(not isinstance(p, str) or not Path(p).is_absolute() for p in inputs):
            raise ValueError("External inputs must be absolute file paths")
        return cls(root, **{k: validate_patterns(v) for k, v in files.items()},
                   tools=tuple(allowed_tools), external_inputs=tuple(Path(p) for p in inputs))

    def as_dict(self) -> dict[str, Any]:
        return {"root": str(self.root), "read": list(self.read), "write": list(self.write),
                "deny_read": list(self.deny_read), "deny_write": list(self.deny_write),
                "tools": list(self.tools), "external_inputs": [str(p) for p in self.external_inputs],
                "root_identity": list(self.root_identity)}

    def normalize(self, resource: str) -> tuple[Path, tuple[str, ...]]:
        raw = Path(resource)
        joined = raw if raw.is_absolute() else self.root / raw
        # Resolve symlinks before removing ..; the kernel follows that same order.
        resolved = canonical_path(joined)
        logical = Path(os.path.abspath(joined))
        names = []
        for path in (logical, resolved):
            try:
                names.append(path.relative_to(self.root).as_posix())
            except ValueError:
                pass
        if not resolved.is_relative_to(self.root) and resolved not in self.external_inputs:
            raise ValueError("Resource is outside the approved project")
        return resolved, tuple(names)

    def is_sensitive(self, names: tuple[str, ...]) -> bool:
        return self.denied(names, self.deny_read)

    def denied(self, names: tuple[str, ...], patterns: tuple[str, ...]) -> bool:
        for name in names:
            parts = PurePosixPath(name).parts
            if any(matches("/".join(parts[:i]), pattern)
                   for i in range(1, len(parts) + 1) for pattern in patterns):
                return True
        return False

    def validate_grant(self, grant: Grant, parent: Grant | None = None) -> None:
        validate_patterns(grant.read)
        validate_patterns(grant.write)
        if not covered(grant.read, self.read) or not covered(grant.write, self.write):
            raise ValueError("Grant exceeds task policy")
        if not set(grant.tools) <= set(self.tools):
            raise ValueError("Grant exceeds allowed tools")
        if parent is not None:
            if grant.task_id != parent.task_id or grant.parent_id != parent.worker_id:
                raise ValueError("Delegation must bind to its parent task and worker")
            if not covered(grant.read, parent.read) or not covered(grant.write, parent.write):
                raise ValueError("Delegation cannot increase authority")
            if not set(grant.tools) <= set(parent.tools):
                raise ValueError("Delegation cannot add tools")

    def decide(self, action: Action, grant: Grant) -> Decision:
        if action.operation not in ("read", "write"):
            return Decision(False, "Unknown operation", action.resource)
        if (action.task_id, action.worker_id) != (grant.task_id, grant.worker_id):
            return Decision(False, "Action does not belong to grant", action.resource)
        try:
            self.validate_grant(grant)
            path, names = self.normalize(action.resource)
        except (ValueError, OSError, RuntimeError) as error:
            return Decision(False, str(error), action.resource)
        sensitive = self.is_sensitive(names)
        if action.tool not in grant.tools:
            return Decision(False, "Tool is not assigned", str(path), sensitive)
        if path.exists() and path.is_file() and path.stat().st_nlink > 1:
            return Decision(False, "Hard-linked resources require explicit review", str(path), sensitive)
        deny = self.deny_read if action.operation == "read" else self.deny_write
        if self.denied(names, deny):
            return Decision(False, "Explicit file denial", str(path), sensitive)
        if path in self.external_inputs:
            return Decision(False, "External input requires an exact worker exception", str(path), sensitive)
        scopes = grant.read if action.operation == "read" else grant.write
        if not names or not all(any(matches(name, scope) for scope in scopes) for name in names):
            return Decision(False, "Resource is not assigned", str(path), sensitive)
        return Decision(True, "Allowed by task policy and worker grant", str(path), sensitive)
