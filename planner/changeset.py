"""Checked regular-file application from a stopped worker's private workspace.

The trusted caller must keep both trees quiescent during capture and application.
This is not a runtime enforcer. No worker-facing API or exception consumption lives
here. Once-only exceptions require a separate operation binding before use here.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from .records import Action
from .supervisor import Supervisor


def application_status(supervisor: Supervisor, journal: Path) -> dict[str, object]:
    """Inspect durable effects after restart; never replay an interrupted edit.

    The journal is operator-owned metadata. Current observations are distinct
    from the recorded past effects and are unavailable after authority changes.
    """
    events = []
    incomplete = False
    with journal.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                incomplete = True
                break
    if not events or events[0].get('event') != 'manifest':
        raise ValueError('Application manifest missing')
    manifest = events[0]
    grant = manifest['grant']
    task, worker = grant['task_id'], grant['worker_id']
    policy = supervisor.policy(task)
    if journal.resolve().is_relative_to(policy.root):
        raise ValueError('Application journal must be outside the project')
    try:
        current_grant = supervisor.check_worker(task, worker, 'shell')
        # JSON arrays decode as lists, while dataclass grant scopes are tuples.
        current = (json.loads(json.dumps(asdict(current_grant))) == grant and
                   supervisor.status(task)['revision'] == manifest['revision'] and
                   policy.as_dict() == manifest['policy'])
    except ValueError:
        current = False
    files = []
    for change in manifest['changes']:
        name = change['path']
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or any(p in ('.', '..') for p in name.split('/')):
            raise ValueError('Invalid application resource')
        observed = 'unverified'
        if current:
            target, _ = policy.normalize(name)
            if target != policy.root / name:
                raise ValueError('Application resource was aliased')
            read = policy.decide(Action(task, worker, 'read', name, 'shell'), current_grant)
            if read.allowed:
                if os.path.lexists(target):
                    actual = _file(target)
                    expected = manifest['expected'].get(name)
                    before = manifest['baseline'].get(name)
                    observed = ('matches_expected' if expected and actual.digest == expected['digest']
                                and actual.mode == expected['mode'] else
                                'matches_baseline' if before and asdict(actual) == before else 'conflict')
                else:
                    observed = 'matches_expected' if change['operation'] == 'delete' else 'missing'
        files.append({'path': name, 'recorded_applied': any(
            item.get('event') == 'applied' and item.get('path') == name for item in events[1:]),
                      'current': observed})
    state = ('conflict' if any(item['current'] in ('conflict', 'missing') for item in files) else
             'complete' if current and not incomplete and events[-1].get('event') == 'complete' and
             all(item['current'] == 'matches_expected' for item in files) else 'interrupted')
    return {'task': task, 'worker': worker, 'state': state, 'files': files}


@dataclass(frozen=True)
class FileState:
    digest: str
    mode: int
    device: int
    inode: int
    uid: int
    gid: int


def _file(path: Path) -> FileState:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o7000:
            raise ValueError('Only regular files with one link and ordinary permissions can be applied')
        return FileState(hashlib.file_digest(stream, 'sha256').hexdigest(),
                         stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, info.st_uid, info.st_gid)


def _tree(root: Path) -> tuple[dict[str, FileState], dict[str, tuple[int, int]]]:
    files: dict[str, FileState] = {}
    directories: dict[str, tuple[int, int]] = {}
    for directory, children, names in os.walk(root, followlinks=False):
        path = Path(directory)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Directory aliases are unsupported')
        directories[path.relative_to(root).as_posix()] = (info.st_dev, info.st_ino)
        for child in children:
            if (path / child).is_symlink():
                raise ValueError('Directory aliases are unsupported')
        for name in names:
            relative = (path / name).relative_to(root).as_posix()
            files[relative] = _file(path / name)
    return files, directories


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class Change:
    path: str
    operation: str


class Changeset:
    """Trusted, in-memory baseline with a durable application progress record."""

    def __init__(self, supervisor: Supervisor, task: str, worker: str,
                 workspace: Path, journal: Path) -> None:
        self.supervisor = supervisor
        grant = supervisor.check_worker(task, worker, "shell")
        policy = supervisor.policy(task)
        self.revision = supervisor.status(task)["revision"]
        self.policy, self.grant = policy, grant
        self.workspace, self.journal = workspace.resolve(), journal.resolve()
        roots = (policy.root, self.workspace)
        if any(self.journal.is_relative_to(root) for root in roots):
            raise ValueError('Application journal must be outside both trees')
        if policy.root.is_relative_to(self.workspace) or self.workspace.is_relative_to(policy.root):
            raise ValueError('Workspace must be separate from original')
        if self.journal.exists():
            raise FileExistsError('Application journal already exists')
        self.baseline, self.directories = _tree(self.workspace)
        self.original: dict[str, FileState] = {}
        self.parents: dict[str, tuple[int, int]] = {}
        for name in self.directories:
            target = policy.root / name
            info = target.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError('Original directory changed')
            self.parents[name] = (info.st_dev, info.st_ino)
        for name, copied in self.baseline.items():
            source = _file(policy.root / name)
            if source.digest != copied.digest:
                raise ValueError('Original changed during preparation')
            self.original[name] = source
        self.used = False

    def _check_parents(self) -> None:
        for name, identity in self.parents.items():
            info = (self.policy.root / name).lstat()
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                raise ValueError('Original directory identity changed')

    def preview(self) -> tuple[Change, ...]:
        """Reject the entire changeset on any conflict or unsupported operation."""
        self._check_parents()
        files, directories = _tree(self.workspace)
        if directories != self.directories:
            raise ValueError('Directory changes require validated directory support')
        changes = []
        for name in sorted(self.baseline.keys() | files.keys()):
            before, after = self.baseline.get(name), files.get(name)
            if before is not None and after is not None:
                if before.mode != after.mode:
                    raise ValueError('File metadata changes are unsupported')
                if before.digest == after.digest:
                    continue
            decision = self.policy.decide(Action(self.grant.task_id, self.grant.worker_id,
                                                'write', name, 'shell'), self.grant)
            if not decision.allowed:
                raise PermissionError('Changeset contains an unauthorized write')
            target = self.policy.root / name
            if before is None:
                if os.path.lexists(target):
                    raise FileExistsError('New file collides with an original resource')
                if after is None or after.mode & ~0o777:
                    raise ValueError('Unsupported new file metadata')
            elif _file(target) != self.original[name]:
                raise ValueError('Original file changed since preparation')
            changes.append(Change(name, 'add' if before is None else
                                  'delete' if after is None else 'modify'))
        return tuple(changes)

    def _check_authority(self) -> None:
        grant = self.supervisor.check_worker(self.grant.task_id, self.grant.worker_id, "shell")
        if grant != self.grant or self.supervisor.status(grant.task_id)["revision"] != self.revision:
            raise PermissionError("Authority changed since preparation")

    def _authorize(self, changes: tuple[Change, ...]) -> None:
        actions = tuple(Action(self.grant.task_id, self.grant.worker_id, "write", item.path, "shell")
                        for item in changes)
        if actions:
            decisions = self.supervisor.authorize_many(actions)
            if any(not decision.allowed or decision.approval_id is not None for decision in decisions):
                raise PermissionError("Application requires ordinary current worker authority")

    def apply(self) -> tuple[Change, ...]:
        """Apply once after caller verifies worker cleanup and exclusive tree access.

        Each file replacement is atomic; the complete changeset is not. A journal
        with an unfinished operation requires manual reconciliation, never replay.
        """
        if sys.platform != 'linux':
            raise RuntimeError('File application metadata contract currently requires Linux')
        if self.used:
            raise ValueError('Application cannot be replayed')
        self._check_authority()
        changes = self.preview()
        self._authorize(changes)
        for change in changes:
            target = self.policy.root / change.path
            # Linux exposes POSIX access/default ACLs as extended attributes.
            # Refuse metadata we cannot faithfully retain instead of dropping it.
            if os.listxattr(target.parent) or (target.exists() and os.listxattr(target)):
                raise ValueError('Extended attributes and ACLs require explicit support')
            before = self.original.get(change.path)
            if before and (before.uid != os.geteuid() or before.gid != os.getegid()):
                raise ValueError('Non-default file ownership requires explicit support')
        if not self.journal.parent.is_dir():
            raise ValueError("Application journal requires an existing trusted directory")
        self.used = True
        with self.journal.open('x', encoding='utf-8') as log:
            def record(event: str, path: str | None = None) -> None:
                log.write(json.dumps({'event': event, 'path': path,
                                      'task': self.grant.task_id, 'worker': self.grant.worker_id}) + '\n')
                log.flush()
                os.fsync(log.fileno())
            log.write(json.dumps({'event': 'manifest', 'revision': self.revision,
                                  'policy': self.policy.as_dict(), 'grant': asdict(self.grant),
                                  'workspace': str(self.workspace),
                                  'baseline': {name: asdict(value) for name, value in self.original.items()},
                                  'expected': {name: {'digest': value.digest,
                                                     'mode': self.original[name].mode if name in self.original else 0o600}
                                               for name, value in _tree(self.workspace)[0].items()},
                                  'changes': [asdict(change) for change in changes]}) + '\n')
            record('started')
            _sync_directory(self.journal.parent)
            for change in changes:
                self._check_authority()
                self._authorize((change,))
                self._check_parents()
                target = self.policy.root / change.path
                if change.operation == 'add':
                    if os.path.lexists(target):
                        raise FileExistsError('Destination appeared during application')
                elif _file(target) != self.original[change.path]:
                    raise ValueError('Original changed during application')
                record('applying', change.path)
                if change.operation == 'delete':
                    target.unlink()
                else:
                    descriptor, temporary = tempfile.mkstemp(prefix='.planner-', dir=target.parent)
                    try:
                        with os.fdopen(descriptor, 'wb') as stream:
                            stream.write((self.workspace / change.path).read_bytes())
                            mode = self.original[change.path].mode if change.operation == 'modify' else 0o600
                            os.fchown(stream.fileno(), os.geteuid(), os.getegid())
                            os.fchmod(stream.fileno(), mode)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, target)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                _sync_directory(target.parent)
                record('applied', change.path)
            record('complete')
        return changes
