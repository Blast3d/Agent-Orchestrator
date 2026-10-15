import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_handoff import Coordinator
from init_run import create_run
from orchestration_lifecycle import main
from orchestration_lifecycle import start_run


class ResumePreflightTests(unittest.TestCase):
    def test_valid_and_stale_preflight_leave_every_run_byte_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            run, _ = create_run(Path(folder), 'preflight', 'Read-only resume check', lead='astra')
            identity = Coordinator(run).read()
            def snapshot():
                return {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in run.rglob('*') if p.is_file()}
            before = snapshot()
            args = ['start', '--run', str(run), '--owner', identity['owner'],
                    '--session', identity['session'], '--generation', str(identity['generation']), '--dry-run']
            with patch('orchestration_lifecycle.load_operating_context', side_effect=AssertionError('must not load guide')), \
                    patch('brain_store.BrainStore.search', side_effect=AssertionError('must not recall')), \
                    patch('builtins.print') as output:
                self.assertEqual(main(args), 0)
                self.assertIn('"status": "ready"', output.call_args.args[0])
                self.assertIn('"provider_calls": 0', output.call_args.args[0])
                self.assertEqual(snapshot(), before)
                args[args.index('--generation') + 1] = str(identity['generation'] + 1)
                self.assertEqual(main(args), 2)
                self.assertIn('Stale coordinator', output.call_args.args[0])
                self.assertEqual(snapshot(), before)

    def test_direct_preflight_never_constructs_injected_services(self):
        with tempfile.TemporaryDirectory() as folder:
            run, _ = create_run(Path(folder), 'services', 'Read-only tuple', lead='astra')
            state = Coordinator(run).read()
            def forbidden(*args, **kwargs):
                raise AssertionError('dry-run must not request services')
            result = start_run(run=run, owner=state['owner'], session=state['session'],
                               generation=state['generation'], dry_run=True,
                               brain_factory=forbidden, context_loader=forbidden)
            self.assertEqual(result['status'], 'ready')
            self.assertIn('not checked', result['evidence_limit'])

    def test_dry_run_requires_existing_exact_run_and_complete_tuple(self):
        with patch('builtins.print') as output:
            self.assertEqual(main(['start', '--workspace', '.', '--name', 'x', '--objective', 'x', '--dry-run']), 2)
            self.assertIn('existing --run', output.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
