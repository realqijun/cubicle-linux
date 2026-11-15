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
        if current and change['operation'] in ('mkdir', 'rmdir'):
            target = policy.root / name
            present = target.is_dir() and not target.is_symlink()
            observed = ('matches_expected' if present == (change['operation'] == 'mkdir') else
                        'missing' if change['operation'] == 'mkdir' else 'conflict')
        elif current:
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
                 workspace: Path, journal: Path, *, attempt: str | None = None,
                 exceptions: dict[str, str] | None = None) -> None:
        """``exceptions`` maps a relative path to the write approval reserved for
        ``attempt``; only that exact staged edit may use it, once."""
        self.supervisor = supervisor
        self.attempt, self.exceptions = attempt, dict(exceptions or {})
        if self.exceptions and attempt is None:
            raise ValueError('Exception-bound edits require their attempt')
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

    def save(self, path: Path) -> None:
        """Keep the trusted baseline in operator storage for a later review-only apply."""
        data = {'revision': self.revision, 'grant': asdict(self.grant), 'workspace': str(self.workspace),
                'journal': str(self.journal),
                'baseline': {name: asdict(value) for name, value in self.baseline.items()},
                'original': {name: asdict(value) for name, value in self.original.items()},
                'directories': self.directories, 'parents': self.parents,
                'attempt': self.attempt, 'exceptions': self.exceptions}
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())

    @classmethod
    def restore(cls, supervisor: Supervisor, path: Path) -> 'Changeset':
        """Rebuild from a saved baseline; current authority must equal the recorded one."""
        data = json.loads(path.read_text(encoding='utf-8'))
        grant = data['grant']
        self = cls.__new__(cls)
        self.supervisor = supervisor
        self.grant = supervisor.check_worker(grant['task_id'], grant['worker_id'], 'shell')
        self.revision = data['revision']
        if (json.loads(json.dumps(asdict(self.grant))) != grant or
                supervisor.status(self.grant.task_id)['revision'] != self.revision):
            raise PermissionError('Authority changed since preparation')
        self.policy = supervisor.policy(self.grant.task_id)
        self.workspace, self.journal = Path(data['workspace']), Path(data['journal'])
        self.baseline = {name: FileState(**value) for name, value in data['baseline'].items()}
        self.original = {name: FileState(**value) for name, value in data['original'].items()}
        self.directories = {name: tuple(value) for name, value in data['directories'].items()}
        self.parents = {name: tuple(value) for name, value in data['parents'].items()}
        self.attempt, self.exceptions = data['attempt'], data['exceptions']
        self.used = self.journal.exists()
        return self

    def _check_parents(self) -> None:
        for name, identity in self.parents.items():
            info = (self.policy.root / name).lstat()
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != identity:
                raise ValueError('Original directory identity changed')

    def preview(self) -> tuple[Change, ...]:
        """Reject the entire changeset on any conflict or unsupported operation."""
        self._check_parents()
        files, directories = _tree(self.workspace)
        created = sorted(directories.keys() - self.directories.keys(), key=lambda n: (n.count('/'), n))
        removed = sorted(self.directories.keys() - directories.keys(), key=lambda n: (-n.count('/'), n))
        changes = []
        for name in created:
            self._permitted(name)
            if os.path.lexists(self.policy.root / name):
                raise FileExistsError('New directory collides with an original resource')
            changes.append(Change(name, 'mkdir'))
        for name in sorted(self.baseline.keys() | files.keys()):
            before, after = self.baseline.get(name), files.get(name)
            if before is not None and after is not None:
                if before.mode != after.mode:
                    raise ValueError('File metadata changes are unsupported')
                if before.digest == after.digest:
                    continue
            if name in self.exceptions:
                self._exception(name, consume=False)
            elif not self.policy.decide(Action(self.grant.task_id, self.grant.worker_id,
                                               'write', name, 'shell'), self.grant).allowed:
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
        for name in removed:
            self._permitted(name)
            # Copies omit denied files; removing their directory would delete
            # content the worker never saw, so require exactly the exposed entries.
            target = self.policy.root / name
            exposed = {item.rsplit('/', 1)[-1] for item in (*self.baseline, *self.directories)
                       if item != name and item.rsplit('/', 1)[0] == name}
            if {child.name for child in target.iterdir()} - exposed:
                raise PermissionError('Directory removal would delete unexposed content')
            changes.append(Change(name, 'rmdir'))
        return tuple(changes)

    def _permitted(self, name: str) -> None:
        if not self.policy.decide(Action(self.grant.task_id, self.grant.worker_id, 'write', name, 'shell'),
                                  self.grant).allowed:
            raise PermissionError('Changeset contains an unauthorized directory change')

    def _check_authority(self) -> None:
        grant = self.supervisor.check_worker(self.grant.task_id, self.grant.worker_id, "shell")
        if grant != self.grant or self.supervisor.status(grant.task_id)["revision"] != self.revision:
            raise PermissionError("Authority changed since preparation")

    def _exception(self, name: str, *, consume: bool) -> None:
        assert self.attempt is not None
        self.supervisor.check_application_exception(self.exceptions[name], self.attempt,
                                                    self.policy.root / name, consume=consume)

    def _authorize(self, changes: tuple[Change, ...], *, consume: bool = False) -> None:
        for item in changes:
            if item.path in self.exceptions:
                self._exception(item.path, consume=consume)
        actions = tuple(Action(self.grant.task_id, self.grant.worker_id, "write", item.path, "shell")
                        for item in changes if item.path not in self.exceptions)
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
            parent = target.parent
            while not parent.exists():  # Created by an earlier mkdir in this changeset.
                parent = parent.parent
            # Linux exposes POSIX access/default ACLs as extended attributes.
            # Refuse metadata we cannot faithfully retain instead of dropping it.
            if os.listxattr(parent) or (target.exists() and os.listxattr(target)):
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
                self._check_parents()
                target = self.policy.root / change.path
                if change.operation in ('add', 'mkdir'):
                    if os.path.lexists(target):
                        raise FileExistsError('Destination appeared during application')
                elif change.operation == 'rmdir':
                    pass  # Identity checked above; rmdir itself refuses a non-empty directory.
                elif _file(target) != self.original[change.path]:
                    raise ValueError('Original changed during application')
                # Consumes a once-only exception before the write; an interruption
                # after this point is reconciled, never replayed with that approval.
                self._authorize((change,), consume=True)
                record('applying', change.path)
                if change.operation == 'mkdir':
                    target.mkdir(mode=0o700)
                    os.chmod(target, 0o700)  # Not widened by umask; documented new-directory rule.
                    info = target.lstat()
                    self.parents[change.path] = (info.st_dev, info.st_ino)
                elif change.operation == 'rmdir':
                    target.rmdir()
                    del self.parents[change.path]
                elif change.operation == 'delete':
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
