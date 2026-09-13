"""Operating context holds linked new work until startup matches current state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from init_run import create_run
from coordinator_handoff import Coordinator
from orchestration_context import load_operating_context, verify_run_startup, OperatingContextError


class OperatingContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run, _ = create_run(self.root, 'context-test', 'Verify startup scope', project_id='example')
        self.operating = load_operating_context()
        self.args = argparse.Namespace(output=self.run / 'drafts' / 'answer.json', project='example')
        state = Coordinator(self.run).read()
        self.receipt = {'schema_version': 1, 'run_id': self.run.name, 'project_id': 'example',
                        'operating_context': self.operating,
                        'coordinator': {key: state[key] for key in ('owner', 'session', 'generation')}}

    def save(self):
        (self.run / 'startup-context.json').write_text(json.dumps(self.receipt), encoding='utf-8')

    def test_linked_output_requires_startup_and_preserves_project_scope(self):
        with self.assertRaises(OperatingContextError):
            verify_run_startup(self.args, self.operating)
        self.save()
        self.assertEqual(verify_run_startup(self.args, self.operating)['run_id'], self.run.name)
        self.args.project = 'other-project'
        with self.assertRaises(OperatingContextError):
            verify_run_startup(self.args, self.operating)

    def test_checkpoint_change_invalidates_startup(self):
        self.save()
        coordinator = Coordinator(self.run)
        state = coordinator.read()
        coordinator.checkpoint(state['checkpoint'], state['owner'], state['session'], state['generation'])
        with self.assertRaises(OperatingContextError):
            verify_run_startup(self.args, self.operating)

    def test_corrupt_saved_context_is_not_a_valid_load_receipt(self):
        self.receipt['operating_context'] = dict(self.operating, context='different bytes')
        self.save()
        with self.assertRaises(OperatingContextError):
            verify_run_startup(self.args, self.operating)

    def test_missing_run_manifest_does_not_turn_linked_work_into_unlinked_work(self):
        (self.run / 'run.json').unlink()
        with self.assertRaises(OperatingContextError):
            verify_run_startup(self.args, self.operating)

    @unittest.skipUnless(os.name == 'nt', 'Windows path casing')
    def test_alternate_windows_casing_still_requires_startup(self):
        self.args.output = Path(str(self.args.output).replace('.orchestration', '.ORCHESTRATION'))
        with self.assertRaises(OperatingContextError):
            verify_run_startup(self.args, self.operating)

    def test_guide_accepts_windows_newlines_and_hashes_actual_bytes(self):
        path = self.root / 'guide.md'
        raw = self.operating['context'].replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8')
        path.write_bytes(raw)
        self.assertEqual(load_operating_context(path)['sha256'], hashlib.sha256(raw).hexdigest())

    def test_missing_empty_oversized_or_wrong_version_guide_fails(self):
        path = self.root / 'guide.md'
        for content in (None, b'', b'x' * (16384 + 1), b'# Orchestration Operating Guide (v2)\n' + b'x' * 200):
            with self.subTest(content_type=type(content).__name__):
                if content is not None:
                    path.write_bytes(content)
                with self.assertRaises(OperatingContextError):
                    load_operating_context(path)

    def test_grok_transport_file_preserves_the_recorded_request_bytes(self):
        from dispatch_worker import cloud_command, PREFIX
        prompt = self.operating['context'] + '\nTask with Unicode: café.\r\nKeep both newline styles.'
        cloud_command('grok', prompt, self.root)
        self.assertEqual((self.root / 'task.txt').read_bytes(), (PREFIX + prompt).encode('utf-8'))


if __name__ == '__main__':
    unittest.main()
