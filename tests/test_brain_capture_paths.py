"""Reviewed capture selects an exact run without relocating managed storage."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import brain_cli
from brain_store import BrainStore
from coordinator_handoff import Coordinator
from init_run import create_run
from memory_bundle import capture_run
from task_store import write_json


class BrainCapturePathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'application'
        self.workspace = self.base / 'external-workspace'
        self.root.mkdir()
        self.workspace.mkdir()
        self.run, _ = create_run(self.workspace, 'external-proof',
                                 'Review only synthetic external evidence',
                                 project_id='capture-path-fixture', lead='sol')
        self.state = Coordinator(self.run).read()
        self.proof = self.run / 'review/evidence.md'
        self.proof.write_text('Synthetic external evidence is frozen before capture.\n', encoding='utf-8')
        self.bundle = {
            'project_id': 'capture-path-fixture',
            'capture_id': 'external-path-proof',
            'evidence': ['review/evidence.md'],
            'memories': [
                {'key': 'boundary', 'kind': 'fact', 'title': 'Synthetic storage boundary',
                 'content': 'Reviewed evidence belongs to the exact external run.'},
                {'key': 'replay', 'kind': 'procedure', 'title': 'Synthetic replay procedure',
                 'content': 'Reuse the canonical receipt instead of importing another artifact.'},
            ],
            'relations': [{'from': 'replay', 'to': 'boundary', 'relation': 'supports',
                           'reason': 'Canonical replay retains the exact reviewed source boundary.'}],
        }
        self.bundle_path = self.run / 'review/bundle.json'
        write_json(self.bundle_path, self.bundle)
        self.identity = dict(owner=self.state['owner'], session=self.state['session'],
                             generation=self.state['generation'], reviewer='Synthetic path reviewer',
                             note='Verified both synthetic claims against the immutable local evidence.')

    def cli(self, *, run=None, file=None, **overrides):
        identity = dict(self.identity, **overrides)
        args = ['--root', str(self.root), 'capture', '--run', str(self.run if run is None else run),
                '--file', str(self.bundle_path if file is None else file)]
        for key, value in identity.items():
            args.extend(['--' + key, str(value)])
        output = io.StringIO()
        with redirect_stdout(output), \
                patch('subprocess.run', side_effect=AssertionError('Capture must not invoke a worker')), \
                patch('subprocess.Popen', side_effect=AssertionError('Capture must not launch a worker')):
            code = brain_cli.main(args)
        return code, json.loads(output.getvalue())

    def direct(self, run=None):
        with patch('subprocess.run', side_effect=AssertionError('Capture must not invoke a worker')), \
                patch('subprocess.Popen', side_effect=AssertionError('Capture must not launch a worker')):
            return capture_run(self.root, self.run if run is None else run, self.bundle, **self.identity)

    def assert_no_managed_writes(self):
        self.assertFalse((self.root / 'runtime').exists(), 'Preflight must not initialize the Brain')
        tasks = self.root / 'runs/tasks'
        self.assertFalse(tasks.exists() and any(tasks.iterdir()), 'Rejected capture must not import a task')
        self.assertFalse((self.workspace / 'runtime').exists())
        self.assertFalse((self.workspace / 'runs').exists())
        self.assertEqual(json.loads((self.run / 'run.json').read_text(encoding='utf-8'))['tasks'], [])
        self.assertFalse((self.run / 'review/memory-capture-external-path-proof.json').exists())

    def assert_cli_held(self, *, error=None, **args):
        code, result = self.cli(**args)
        self.assertEqual(code, 2, result)
        self.assertEqual(result['status'], 'error', result)
        if error:
            self.assertRegex(result['error'], error)
        self.assert_no_managed_writes()

    def test_cli_external_exact_run_keeps_brain_and_tasks_in_application(self):
        before = {str(p.relative_to(self.run)): p.read_bytes()
                  for p in self.run.rglob('*') if p.is_file()}
        code, result = self.cli()
        self.assertEqual(code, 0, result)
        self.assertEqual(result['memory_outcome']['status'], 'remembered')
        self.assertEqual(result['provider_calls'], 0)
        self.assertTrue(result['imported_artifact'])
        job = self.root / 'runs/tasks' / result['job_id']
        canonical = json.loads((job / 'result.json').read_text(encoding='utf-8'))
        self.assertEqual(canonical['run_id'], self.run.name)
        self.assertEqual(canonical['run_directory'], str(self.run.resolve()))
        self.assertEqual(canonical['assignment_project_id'], self.bundle['project_id'])
        self.assertEqual(canonical['worker'], 'native-review')
        self.assertEqual(canonical['review_status'], 'accepted')
        self.assertEqual(canonical['provider_calls'], 0)
        self.assertTrue(canonical['imported_completed_artifact'])
        evidence = json.loads(canonical['response'])
        self.assertEqual(evidence['knowledge'], self.bundle)
        self.assertEqual(evidence['evidence'], [{'path': 'review/evidence.md',
                                               'sha256': hashlib.sha256(self.proof.read_bytes()).hexdigest()}])
        self.assertTrue((self.root / 'runtime/brain/memory.sqlite').is_file())
        self.assertTrue((job / 'memory-outcome.json').is_file())
        self.assertFalse((self.root / '.orchestration').exists())
        self.assertFalse((self.workspace / 'runtime').exists())
        self.assertFalse((self.workspace / 'runs').exists())
        after = {str(p.relative_to(self.run)): p.read_bytes()
                 for p in self.run.rglob('*') if p.is_file()}
        journal = str(Path('review/memory-capture-external-path-proof.json'))
        self.assertEqual(set(after) - set(before), {journal})
        self.assertEqual({name for name in before if before[name] != after[name]}, {'run.json'})
        manifest = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['tasks'], [{'job_id': result['job_id'], 'status': 'accepted',
                                             'kind': 'reviewed-closeout'}])

    def test_direct_capture_accepts_absolute_string_and_path_and_reuses_job(self):
        first = self.direct(str(self.run))
        second = self.direct(self.run)
        self.assertEqual(first['memory_outcome']['status'], 'remembered')
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(first['memory_outcome']['memory_ids'], second['memory_outcome']['memory_ids'])
        self.assertEqual(len(list((self.root / 'runs/tasks').iterdir())), 1)

    def test_cli_external_replay_preserves_canonical_memories_and_source_hashes(self):
        code, first = self.cli()
        self.assertEqual(code, 0, first)
        receipt_path = self.root / 'runs/tasks' / first['job_id'] / 'memory-outcome.json'
        original_receipt = receipt_path.read_bytes()
        code, replay = self.cli()
        self.assertEqual(code, 0, replay)
        self.assertEqual(first['job_id'], replay['job_id'])
        self.assertEqual(first['memory_outcome']['memory_ids'], replay['memory_outcome']['memory_ids'])
        self.assertTrue(replay['memory_outcome']['reused'])
        self.assertEqual(receipt_path.read_bytes(), original_receipt)
        memories = BrainStore(self.root).list_memories(self.bundle['project_id'])
        self.assertEqual({row['id'] for row in memories}, set(first['memory_outcome']['memory_ids']))
        self.assertEqual(len(list((self.root / 'runs/tasks').iterdir())), 1)
        self.assertEqual(len(json.loads((self.run / 'run.json').read_text(encoding='utf-8'))['tasks']), 1)

    def test_cli_local_exact_run_id_remains_compatible(self):
        local, _ = create_run(self.root, 'local-proof', 'Review synthetic local evidence',
                              project_id=self.bundle['project_id'], lead='sol')
        state = Coordinator(local).read()
        (local / 'review/evidence.md').write_bytes(self.proof.read_bytes())
        bundle_path = local / 'review/bundle.json'
        write_json(bundle_path, self.bundle)
        code, result = self.cli(run=local.name, file=bundle_path, owner=state['owner'],
                                session=state['session'], generation=state['generation'])
        self.assertEqual(code, 0, result)
        self.assertEqual(result['memory_outcome']['status'], 'remembered')
        task = self.root / 'runs/tasks' / result['job_id'] / 'result.json'
        self.assertEqual(json.loads(task.read_text(encoding='utf-8'))['run_id'], local.name)
        self.assertEqual(json.loads((self.run / 'run.json').read_text(encoding='utf-8'))['tasks'], [])

    def test_cli_does_not_discover_external_workspace_from_run_basename(self):
        self.assert_cli_held(run=self.run.name)

    def test_cli_bundle_must_belong_to_selected_run_not_sibling_workspace_or_app(self):
        sibling, _ = create_run(self.workspace, 'sibling-proof', 'Unselected synthetic run',
                                project_id=self.bundle['project_id'], lead='sol')
        for outside in (sibling / 'review/bundle.json', self.workspace / 'bundle.json',
                        self.root / 'bundle.json'):
            with self.subTest(path=outside):
                write_json(outside, self.bundle)
                self.assert_cli_held(file=outside, error='outside')

    def test_cli_rejects_relative_or_traversing_external_run_selection(self):
        for selection in (self.run.relative_to(self.base), self.run / '..' / self.run.name):
            with self.subTest(selection=selection):
                self.assert_cli_held(run=selection, error='absolute|exact')

    def test_cli_rejects_bundle_over_96_kib_before_initializing_brain(self):
        write_json(self.bundle_path, dict(self.bundle, padding='x' * (96 * 1024)))
        self.assertGreater(self.bundle_path.stat().st_size, 96 * 1024)
        self.assert_cli_held(error='size limit|bounded')

    def test_cli_external_capture_rejects_stale_owner_session_or_generation(self):
        for update in ({'owner': 'astra'}, {'session': 'stale-synthetic-session'},
                       {'generation': self.state['generation'] + 1}):
            with self.subTest(update=update):
                self.assert_cli_held(error='Stale', **update)

    def test_cli_external_capture_requires_matching_well_formed_manifest(self):
        manifest_path = self.run / 'run.json'
        original = json.loads(manifest_path.read_text(encoding='utf-8'))
        for update in ({'run_id': 'another-exact-run'}, {'schema_version': 2}, {'tasks': {}}):
            with self.subTest(update=update):
                write_json(manifest_path, dict(original, **update))
                before = manifest_path.read_bytes()
                code, result = self.cli()
                self.assertEqual(code, 2, result)
                self.assertRegex(result['error'], 'Manifest|exact')
                self.assertFalse((self.root / 'runtime').exists())
                self.assertFalse((self.root / 'runs/tasks').exists())
                self.assertEqual(manifest_path.read_bytes(), before)
                self.assertFalse((self.run / 'review/memory-capture-external-path-proof.json').exists())
        write_json(manifest_path, original)

    def test_cli_external_capture_rejects_wrong_project_bundle(self):
        write_json(self.bundle_path, dict(self.bundle, project_id='other-project'))
        self.assert_cli_held(error='exact source project')

    def copy_run(self):
        workspace = self.base / 'other-workspace'
        run = workspace / '.orchestration' / self.run.name
        run.parent.mkdir(parents=True)
        shutil.copytree(self.run, run)
        state = json.loads((run / 'coordinator.json').read_text(encoding='utf-8'))
        state['workspace'] = str(workspace.resolve())
        write_json(run / 'coordinator.json', state)
        return run

    def test_equal_basename_external_runs_keep_independent_jobs_and_replay_bindings(self):
        other = self.copy_run()
        code, first = self.cli()
        self.assertEqual(code, 0, first)
        code, second = self.cli(run=other, file=other / 'review/bundle.json')
        self.assertEqual(code, 0, second)
        self.assertNotEqual(first['job_id'], second['job_id'])
        self.assertTrue(set(first['memory_outcome']['memory_ids']).isdisjoint(second['memory_outcome']['memory_ids']))
        for run, result in ((self.run, first), (other, second)):
            with self.subTest(run=run):
                canonical = self.root / 'runs/tasks' / result['job_id'] / 'result.json'
                task = json.loads(canonical.read_text(encoding='utf-8'))
                self.assertEqual(task['run_id'], self.run.name)
                self.assertEqual(task['run_directory'], str(run.resolve()))
                code, replay = self.cli(run=run, file=run / 'review/bundle.json')
                self.assertEqual(code, 0, replay)
                self.assertEqual(replay['job_id'], result['job_id'])
                self.assertEqual(replay['memory_outcome']['memory_ids'], result['memory_outcome']['memory_ids'])
        self.assertEqual(len(list((self.root / 'runs/tasks').iterdir())), 2)
        self.assertEqual(len(BrainStore(self.root).list_memories(self.bundle['project_id'])), 4)

    def test_copied_capture_journal_cannot_adopt_equal_basename_other_workspace(self):
        code, first = self.cli()
        self.assertEqual(code, 0, first)
        other = self.copy_run()
        journal = other / 'review/memory-capture-external-path-proof.json'
        original_journal = journal.read_bytes()
        original_manifest = (other / 'run.json').read_bytes()
        code, rejected = self.cli(run=other, file=other / 'review/bundle.json')
        self.assertEqual(code, 2, rejected)
        self.assertRegex(rejected['error'], 'journal|canonical')
        self.assertEqual(journal.read_bytes(), original_journal)
        self.assertEqual((other / 'run.json').read_bytes(), original_manifest)
        self.assertEqual(len(list((self.root / 'runs/tasks').iterdir())), 1)
        self.assertEqual({row['id'] for row in BrainStore(self.root).list_memories(self.bundle['project_id'])},
                         set(first['memory_outcome']['memory_ids']))

    @unittest.skipUnless(os.name == 'nt', 'Windows path capitalization aliases')
    def test_windows_case_variant_path_captures_with_canonical_directory_and_replays(self):
        alternate = str(self.run).lower()
        self.assertNotEqual(alternate, str(self.run))
        code, first = self.cli(run=alternate, file=str(self.bundle_path).lower())
        self.assertEqual(code, 0, first)
        canonical = self.root / 'runs/tasks' / first['job_id'] / 'result.json'
        self.assertEqual(json.loads(canonical.read_text(encoding='utf-8'))['run_directory'], str(self.run.resolve()))
        journal = self.run / 'review/memory-capture-external-path-proof.json'
        self.assertEqual(json.loads(journal.read_text(encoding='utf-8'))['run_directory'], str(self.run.resolve()))
        code, replay = self.cli()
        self.assertEqual(code, 0, replay)
        self.assertEqual(replay['job_id'], first['job_id'])
        self.assertEqual(replay['memory_outcome']['memory_ids'], first['memory_outcome']['memory_ids'])

    def junction(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        else:
            link.symlink_to(target, target_is_directory=True)
        # Remove only the directory link, before TemporaryDirectory cleans its tree.
        self.addCleanup(os.rmdir if os.name == 'nt' else Path.unlink, link)
        return link

    def test_cli_rejects_junction_run_before_coordinator_resolves_alias(self):
        alias = self.junction(self.base / 'aliased-workspace/.orchestration' / self.run.name, self.run)
        self.assert_cli_held(run=alias, error='Linked')

    def test_cli_rejects_linked_bundle_parent_even_when_target_is_inside_selected_run(self):
        alias = self.junction(self.run / 'linked-review', self.run / 'review')
        self.assert_cli_held(file=alias / 'bundle.json', error='Linked')

    def test_direct_capture_rejects_linked_evidence_inside_selected_run(self):
        self.junction(self.run / 'linked-review', self.run / 'review')
        self.bundle['evidence'] = ['linked-review/evidence.md']
        with self.assertRaisesRegex(ValueError, 'Linked'):
            self.direct()
        self.assert_no_managed_writes()


if __name__ == '__main__':
    unittest.main()
