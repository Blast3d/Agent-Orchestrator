"""Select the visible coordinator session from a saved listing; no CLI or model calls."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from watch_coordinator import CLAUDE, main, select_session

LISTING = [
    {'kind': 'interactive', 'name': 'openwhispr-58', 'sessionId': 'aaaa'},
    {'kind': 'background', 'id': 'aaaa1111', 'name': 'Fable coordinator 0ld0ld0l', 'startedAt': 1},
    {'kind': 'background', 'id': 'bbbb2222', 'name': 'Fable coordinator 1a02b462', 'startedAt': 2},
    {'kind': 'background', 'id': 'cccc3333', 'name': 'Unrelated worker', 'startedAt': 3},
    {'kind': 'interactive', 'name': 'Fable coordinator typed-by-hand', 'startedAt': 4},
]


class SelectSessionTests(unittest.TestCase):
    def setUp(self):
        policy = patch('watch_coordinator.require_model_allowed', side_effect=lambda model: model)
        self.model_policy = policy.start()
        self.addCleanup(policy.stop)

    def test_prefers_recorded_session_while_it_runs(self):
        self.assertEqual(select_session(LISTING, 'aaaa1111')['id'], 'aaaa1111')

    def test_falls_back_to_newest_fable_coordinator(self):
        self.assertEqual(select_session(LISTING)['id'], 'bbbb2222')
        self.assertIsNone(select_session(LISTING, 'gone0000'))

    def test_ignores_interactive_and_foreign_background_sessions(self):
        self.assertIsNone(select_session([LISTING[0], LISTING[3], LISTING[4]]))

    def test_nothing_running_returns_none(self):
        self.assertIsNone(select_session([]))
        self.assertIsNone(select_session(['not a row', None]))

    def test_selects_newest_opus_coordinator_and_preserves_explicit_run_choice(self):
        opus = {'kind': 'background', 'id': 'dddd4444',
                'name': 'Claude Opus coordinator 3a02b462', 'startedAt': 5}
        self.assertEqual(select_session(LISTING + [opus])['id'], 'dddd4444')
        self.assertEqual(select_session(LISTING + [opus], 'aaaa1111')['id'], 'aaaa1111')
        self.assertIsNone(select_session(LISTING + [opus], 'gone0000'))

    def test_paused_fable_is_skipped_and_exact_choice_never_falls_through(self):
        def allow_opus(model):
            if 'fable' in model:
                raise ValueError('Claude Fable is paused by the user until further notice')
            return model
        self.model_policy.side_effect = allow_opus
        opus = {'kind': 'background', 'id': 'dddd4444',
                'name': 'Claude Opus coordinator 3a02b462', 'startedAt': 1}
        self.assertEqual(select_session(LISTING + [opus])['id'], 'dddd4444')
        self.assertIsNone(select_session(LISTING + [opus], 'aaaa1111'))
        self.assertIsNone(select_session(LISTING))

    def test_reported_paused_model_cannot_hide_behind_an_opus_name(self):
        self.model_policy.side_effect = ValueError('Claude Fable is paused by the user until further notice')
        opus_name = {'kind': 'background', 'id': 'dddd4444', 'model': 'claude-fable-5',
                     'name': 'Claude Opus coordinator 3a02b462', 'startedAt': 5}
        self.assertIsNone(select_session([opus_name]))
        self.model_policy.assert_called_with('claude-fable-5')

    def test_exact_run_in_external_project_uses_that_workspace_for_viewer_and_attach(self):
        from coordinator_handoff import Coordinator
        from coordinator_viewer import ViewerStore
        from init_run import create_run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'external project'
            workspace.mkdir()
            run, _ = create_run(workspace, 'external-watch', 'Watch this exact external project run')
            coordinator = Coordinator(run)
            state = coordinator.read()
            session = '1234abcd-1234-4567-8910-123456789abc'
            state.update(owner='fable', session=session,
                         handoff={'id': '0123456789abcdef', 'to': 'fable', 'from': 'astra',
                                  'launch': {'model': 'opus', 'session': session,
                                             'background_id': 'dddd4444', 'status': 'submitted'}})
            coordinator.save(state)
            listing = [{'kind': 'background', 'id': 'dddd4444', 'model': 'opus',
                        'name': 'Claude Opus coordinator 01234567', 'status': 'running',
                        'cwd': str(workspace.resolve()), 'sessionId': session}]
            def viewer(selected_workspace):
                return ViewerStore(selected_workspace, home=root / 'home', async_listing=False,
                                   collector=lambda: listing)
            with (patch('watch_coordinator.list_agents', return_value=listing) as collect,
                  patch('dispatch_worker.worker_environment', return_value={}),
                  patch('coordinator_viewer.ViewerStore', side_effect=viewer) as create_viewer,
                  patch('coordinator_viewer.require_model_allowed', side_effect=lambda model: model),
                  patch('watch_coordinator.subprocess.call', return_value=0) as attach):
                self.assertEqual(main(['--run', str(run)]), 0)
            create_viewer.assert_called_once_with(workspace.resolve())
            collect.assert_called_once_with(CLAUDE, workspace.resolve(), {})
            attach.assert_called_once_with([str(CLAUDE), 'attach', 'dddd4444'],
                                           cwd=workspace.resolve(), env={})


if __name__ == '__main__':
    unittest.main()
