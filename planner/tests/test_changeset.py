"""Actual disposable file effects; no model or OS-confinement claims."""

from pathlib import Path
import json
import os
import stat
import tempfile
import sys
import subprocess
import unittest
from unittest.mock import patch

from planner.changeset import ApplicationConflict, Changeset, application_status
from planner.policy import Policy
from planner.records import Grant
from planner.supervisor import Supervisor


@unittest.skipUnless(sys.platform == "linux", "Application metadata contract requires Linux")
class ChangesetTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project, self.work = self.base / 'project', self.base / 'work'
        for root in (self.project, self.work):
            (root / 'src').mkdir(parents=True)
            (root / 'src/a').write_text('old')
            (root / 'src/b').write_text('keep')
            (root / 'README').write_text('public')
        (self.project / '.env').write_text('DUMMY')
        self.policy = Policy(self.project, read=('**',), write=('src/**',),
                             deny_read=('**/.env*',), tools=('shell',))
        self.supervisor = Supervisor(self.base / 'state.sqlite')
        self.addCleanup(self.supervisor.close)
        self.task = self.supervisor.propose(self.policy)
        self.supervisor.approve_task(self.task)
        self.grant = Grant(self.task, 'worker', self.policy.read, self.policy.write, ('shell',))
        self.supervisor.approve_worker(self.grant)
        self.journal = self.base / 'journal.jsonl'
        self.changes = Changeset(self.supervisor, self.task, "worker", self.work, self.journal)

    def apply(self) -> None:
        self.changes.apply()

    def test_modify_add_delete_and_preserve_excluded_original(self) -> None:
        (self.work / 'src/a').write_text('new')
        (self.work / 'src/b').unlink()
        (self.work / 'src/c').write_text('added')
        self.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'new')
        self.assertEqual((self.project / 'src/c').read_text(), 'added')
        self.assertFalse((self.project / 'src/b').exists())
        self.assertEqual((self.project / '.env').read_text(), 'DUMMY')
        events = [json.loads(line) for line in self.journal.read_text().splitlines()]
        self.assertEqual(events[-1]['event'], 'complete')
        self.assertNotIn('DUMMY', self.journal.read_text())
        with self.assertRaises(ValueError):
            self.apply()

    def test_restart_inspection_distinguishes_applied_files_from_current_conflicts(self) -> None:
        (self.work / 'src/a').write_text('new')
        self.apply()
        state = application_status(self.supervisor, self.journal)
        self.assertEqual(state['state'], 'complete')
        self.assertEqual(state['files'], [{'path': 'src/a', 'recorded_applied': True,
                                           'current': 'matches_expected'}])
        command = subprocess.run([sys.executable, '-m', 'planner', '--state',
                                  str(self.supervisor.path), 'application-status', str(self.journal)],
                                 cwd=Path(__file__).parents[2], capture_output=True, text=True, timeout=10)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertEqual(json.loads(command.stdout)['state'], 'complete')
        (self.project / 'src/a').write_text('later user edit')
        state = application_status(self.supervisor, self.journal)
        self.assertEqual(state['state'], 'conflict')
        self.assertEqual((self.project / 'src/a').read_text(), 'later user edit')

    def test_steps_never_reached_are_interrupted_not_conflicts(self) -> None:
        (self.work / 'src/new').mkdir()
        (self.work / 'src/new/x').write_text('created')
        self.apply()
        events = self.journal.read_text().splitlines()
        cut = next(i for i, line in enumerate(events)
                   if (json.loads(line)['event'], json.loads(line).get('path')) == ('applied', 'src/new'))
        self.journal.write_text('\n'.join(events[:cut + 1]) + '\n')
        (self.project / 'src/new/x').unlink()  # The owner died before this step.
        state = application_status(self.supervisor, self.journal)
        self.assertEqual(state['state'], 'interrupted')
        self.assertEqual({item['path']: item['current'] for item in state['files']},
                         {'src/new': 'matches_expected', 'src/new/x': 'matches_baseline'})

    def test_content_changed_after_review_is_refused(self) -> None:
        (self.work / 'src/a').write_text('reviewed')
        reviewed = self.changes.preview()
        (self.work / 'src/a').write_text('swapped')
        with self.assertRaises(ApplicationConflict):
            self.changes.apply(reviewed)
        self.assertNotIn((self.project / 'src/a').read_text(), ('reviewed', 'swapped'))

    def test_truncated_or_unfinished_journal_never_claims_completion(self) -> None:
        (self.work / 'src/a').write_text('new')
        self.apply()
        lines = self.journal.read_text().splitlines()
        self.journal.write_text('\n'.join(lines[:-1]) + '\n{"event":')
        state = application_status(self.supervisor, self.journal)
        self.assertEqual(state['state'], 'interrupted')
        self.assertEqual((self.project / 'src/a').read_text(), 'new')

    def test_inspection_after_revocation_does_not_restore_authority(self) -> None:
        (self.work / 'src/a').write_text('new')
        self.apply()
        self.supervisor.revoke_worker(self.task, 'worker')
        state = application_status(self.supervisor, self.journal)
        self.assertEqual(state['state'], 'interrupted')
        self.assertEqual(state['files'][0]['current'], 'unverified')
        self.assertEqual(self.supervisor.worker_status(self.task, 'worker')['state'], 'revoked')

    def test_conflict_rejects_whole_changeset(self) -> None:
        (self.work / 'src/a').write_text('new')
        (self.work / 'src/b').write_text('new')
        (self.project / 'src/b').write_text('user edit')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'old')
        self.assertEqual((self.project / 'src/b').read_text(), 'user edit')
        self.assertFalse(self.journal.exists())

    def test_unauthorized_change_blocks_other_edits(self) -> None:
        (self.work / 'src/a').write_text('new')
        (self.work / 'README').write_text('bad')
        with self.assertRaises(PermissionError):
            self.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'old')

    def test_unexposed_existing_file_is_not_an_addition(self) -> None:
        (self.project / 'src/hidden').write_text('private')
        (self.work / 'src/hidden').write_text('replacement')
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual((self.project / 'src/hidden').read_text(), 'private')

    def test_link_substitution_is_refused(self) -> None:
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                path = self.work / 'src/a'
                path.unlink()
                if kind == 'symlink':
                    path.symlink_to(self.project / '.env')
                else:
                    os.link(self.project / '.env', path)
                with self.assertRaises((ValueError, OSError)):
                    self.changes.preview()
                path.unlink()
                path.write_text('old')

    def test_replaced_parent_is_refused(self) -> None:
        (self.work / 'src/a').write_text('new')
        (self.project / 'src').rename(self.project / 'old-src')
        (self.project / 'src').symlink_to(self.project / 'old-src')
        with self.assertRaises(ValueError):
            self.apply()

    def fresh(self) -> None:
        """Recapture after changing both trees identically, as preparation would."""
        self.changes = Changeset(self.supervisor, self.task, "worker", self.work, self.base / 'journal2.jsonl')
        self.journal = self.base / 'journal2.jsonl'

    def placeholder(self, relative: str, original: str) -> None:
        (self.project / relative).write_text(original)
        (self.work / relative).write_text('')
        os.chmod(self.work / relative, stat.S_IMODE((self.project / relative).stat().st_mode))
        self.changes = Changeset(self.supervisor, self.task, "worker", self.work, self.base / 'journal2.jsonl',
                                 placeholders={relative})

    def test_unchanged_placeholder_is_ignored_and_original_kept(self) -> None:
        self.placeholder('src/.env.local', 'NEVER_COPIED')
        (self.work / 'src/a').write_text('new')
        self.assertEqual({(c.path, c.operation) for c in self.changes.apply()}, {('src/a', 'modify')})
        self.assertEqual((self.project / 'src/.env.local').read_text(), 'NEVER_COPIED')

    def test_blind_replacement_of_withheld_file_applies_under_write_authority(self) -> None:
        self.placeholder('src/.env.local', 'NEVER_COPIED')
        (self.work / 'src/.env.local').write_text('REPLACED')
        self.assertEqual({(c.path, c.operation) for c in self.changes.apply()}, {('src/.env.local', 'modify')})
        self.assertEqual((self.project / 'src/.env.local').read_text(), 'REPLACED')

    def test_deleting_a_withheld_stand_in_never_deletes_the_original(self) -> None:
        self.placeholder('src/.env.local', 'NEVER_COPIED')
        (self.work / 'src/.env.local').unlink()
        with self.assertRaises(PermissionError):
            self.changes.apply()
        self.assertEqual((self.project / 'src/.env.local').read_text(), 'NEVER_COPIED')

    def test_blind_replacement_conflicts_with_a_changed_original(self) -> None:
        self.placeholder('src/.env.local', 'NEVER_COPIED')
        (self.work / 'src/.env.local').write_text('REPLACED')
        (self.project / 'src/.env.local').write_text('USER_EDIT')
        with self.assertRaises(ValueError):
            self.changes.apply()
        self.assertEqual((self.project / 'src/.env.local').read_text(), 'USER_EDIT')

    def test_directory_create_move_and_remove(self) -> None:
        for root in (self.project, self.work):
            (root / 'src/old').mkdir()
            (root / 'src/old/f').write_text('moved')
            (root / 'src/gone').mkdir()
            (root / 'src/gone/g').write_text('removed')
        self.fresh()
        (self.work / 'src/new/deep').mkdir(parents=True)
        (self.work / 'src/new/deep/x').write_text('created')
        os.rename(self.work / 'src/old', self.work / 'src/moved')
        (self.work / 'src/gone/g').unlink()
        (self.work / 'src/gone').rmdir()
        changes = {(change.path, change.operation) for change in self.changes.apply()}
        self.assertEqual(changes, {('src/new', 'mkdir'), ('src/new/deep', 'mkdir'), ('src/moved', 'mkdir'),
                                   ('src/new/deep/x', 'add'), ('src/moved/f', 'add'), ('src/old/f', 'delete'),
                                   ('src/gone/g', 'delete'), ('src/old', 'rmdir'), ('src/gone', 'rmdir')})
        self.assertEqual((self.project / 'src/new/deep/x').read_text(), 'created')
        self.assertEqual((self.project / 'src/moved/f').read_text(), 'moved')
        self.assertEqual(stat.S_IMODE((self.project / 'src/new').stat().st_mode), 0o700)
        for gone in ('src/old', 'src/gone'):
            self.assertFalse((self.project / gone).exists())
        status = application_status(self.supervisor, self.journal)
        self.assertEqual(status['state'], 'complete')

    def test_directory_removal_refuses_content_never_exposed(self) -> None:
        for root in (self.project, self.work):
            (root / 'src/gone').mkdir()
            (root / 'src/gone/g').write_text('exposed')
        self.fresh()
        (self.project / 'src/gone/.env.local').write_text('NEVER_COPIED')
        (self.work / 'src/gone/g').unlink()
        (self.work / 'src/gone').rmdir()
        with self.assertRaises(PermissionError):
            self.changes.apply()
        self.assertEqual((self.project / 'src/gone/g').read_text(), 'exposed')
        self.assertEqual((self.project / 'src/gone/.env.local').read_text(), 'NEVER_COPIED')

    def test_directory_outside_write_scope_is_refused(self) -> None:
        (self.work / 'docs').mkdir()
        with self.assertRaises(PermissionError):
            self.apply()
        self.assertFalse((self.project / 'docs').exists())

    def test_new_directory_destination_collision_is_refused(self) -> None:
        (self.work / 'src/new').mkdir()
        (self.work / 'src/new/x').write_text('worker')
        (self.project / 'src/new').mkdir()
        with self.assertRaises(FileExistsError):
            self.apply()
        self.assertEqual(list((self.project / 'src/new').iterdir()), [])

    def test_authority_failure_before_start_changes_nothing(self) -> None:
        (self.work / 'src/a').write_text('new')
        self.supervisor.revoke_worker(self.task, 'worker')
        with self.assertRaises(ValueError):
            self.changes.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'old')
        self.assertFalse(self.journal.exists())

    def test_interrupted_application_records_progress_and_cannot_replay(self) -> None:
        (self.work / 'src/a').write_text('new')
        (self.work / 'src/b').write_text('new')
        replace = os.replace
        def revoke_after_replace(source, target):
            replace(source, target)
            self.supervisor.revoke_worker(self.task, 'worker')
        with patch('planner.changeset.os.replace', side_effect=revoke_after_replace):
            with self.assertRaises(ValueError):
                self.changes.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'new')
        self.assertEqual((self.project / 'src/b').read_text(), 'keep')
        self.assertNotIn('complete', self.journal.read_text())
        with self.assertRaises(ValueError):
            self.apply()
        self.supervisor.revise(self.task, self.policy)
        self.supervisor.approve_task(self.task)
        other = Grant(self.task, "other", self.policy.read, self.policy.write, ("shell",))
        self.supervisor.approve_worker(other)
        with self.assertRaises(FileExistsError):
            Changeset(self.supervisor, self.task, "other", self.work, self.journal)

    def test_metadata_change_is_refused(self) -> None:
        (self.work / 'src/a').chmod(0o700)
        with self.assertRaises(ValueError):
            self.apply()

    def test_replaced_approved_root_is_rejected_at_capture(self) -> None:
        self.project.rename(self.base / 'old-project')
        self.project.mkdir()
        with self.assertRaises(ValueError):
            Changeset(self.supervisor, self.task, 'worker', self.work, self.journal)

    def test_extended_metadata_is_not_silently_dropped(self) -> None:
        try:
            os.setxattr(self.project / 'src/a', 'user.planner-test', b'metadata')
        except OSError:
            self.skipTest('Fixture filesystem lacks user extended attributes')
        (self.work / 'src/a').write_text('new')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(os.getxattr(self.project / 'src/a', 'user.planner-test'), b'metadata')
        self.assertEqual((self.project / 'src/a').read_text(), 'old')

    def test_revision_change_cannot_reuse_prepared_work(self) -> None:
        (self.work / 'src/a').write_text('new')
        self.supervisor.revise(self.task, self.policy)
        self.supervisor.approve_task(self.task)
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'old')

    def test_supervisor_code_remains_protected_under_a_broad_grant(self) -> None:
        (self.work / 'src/a').write_text('new')
        with patch('planner.supervisor.__file__', str(self.project / 'src/supervisor.py')):
            with self.assertRaises(PermissionError):
                self.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'old')

    def test_disk_manifest_identifies_intended_result_without_worker_text(self) -> None:
        import hashlib
        (self.work / 'src/new').write_text('new content')
        self.apply()
        manifest = json.loads(self.journal.read_text().splitlines()[0])
        expected = manifest['expected']['src/new']
        actual = self.project / 'src/new'
        self.assertEqual(expected['digest'], hashlib.sha256(actual.read_bytes()).hexdigest())
        self.assertEqual(expected['mode'], actual.stat().st_mode & 0o777)
        self.assertEqual(manifest['revision'], 1)
        self.assertEqual(manifest['grant']['task_id'], self.task)
        self.assertNotIn('new content', self.journal.read_text())

    def test_missing_journal_directory_cannot_start_application(self) -> None:
        changes = Changeset(self.supervisor, self.task, 'worker', self.work,
                            self.base / 'missing' / 'journal.jsonl')
        (self.work / 'src/a').write_text('new')
        with self.assertRaises(ValueError):
            changes.apply()
        self.assertEqual((self.project / 'src/a').read_text(), 'old')
        self.assertFalse((self.base / 'missing').exists())
