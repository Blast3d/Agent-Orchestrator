"""Exercise saved-session recovery and the loopback UI using synthetic data only."""
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from coordinator_handoff import Coordinator
from init_run import create_run
from coordinator_viewer import ViewerServer, ViewerStore, history_page


ACTUAL_SESSION = '1234abcd-1234-4567-8910-123456789abc'
LOGICAL_SESSION = '9999abcd-1234-4567-8910-123456789abc'
SHORT_ID = ACTUAL_SESSION[:8]
HANDOFF_ID = '0123456789abcdef0123456789abcdef'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def append_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def claude_message(text, role='assistant', identifier='message-1', **extra):
    return {'type': role, 'uuid': identifier, 'timestamp': '2026-09-09T01:00:00Z',
            'message': {'role': role, 'content': [{'type': 'text', 'text': text}]}, **extra}


def directory_link(target, link):
    """Use a real Windows junction where ordinary symlinks need privileges."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if sys.platform != 'win32':
            raise
        import _winapi
        _winapi.CreateJunction(str(target.resolve()), str(link.absolute()))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'session.jsonl'

    def test_claude_history_omits_private_thinking_and_tool_payloads(self):
        append_rows(self.path, [
            claude_message('Visible user question', 'user', 'question'),
            {'type': 'assistant', 'uuid': 'answer', 'message': {'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': 'PRIVATE_CHAIN_MARKER'},
                {'type': 'text', 'text': 'Visible final answer'},
                {'type': 'tool_use', 'id': 'tool-1', 'name': 'Read',
                 'input': {'file_path': 'PRIVATE_TOOL_INPUT_MARKER'}}]}},
            {'type': 'user', 'uuid': 'result', 'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'tool-1',
                 'content': 'PRIVATE_TOOL_RESULT_MARKER'}]}},
        ])
        page = history_page(self.path, 'claude')
        encoded = json.dumps(page)
        self.assertIn('Visible user question', encoded)
        self.assertIn('Visible final answer', encoded)
        self.assertNotIn('PRIVATE_CHAIN_MARKER', encoded)
        self.assertNotIn('PRIVATE_TOOL_INPUT_MARKER', encoded)
        self.assertNotIn('PRIVATE_TOOL_RESULT_MARKER', encoded)

    def test_codex_history_keeps_visible_messages_and_omits_analysis(self):
        append_rows(self.path, [
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': 'Visible Codex question'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'channel': 'analysis', 'content': [{'type': 'output_text', 'text': 'PRIVATE_CODEX_ANALYSIS'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                'channel': 'final', 'content': [{'type': 'output_text', 'text': 'Visible Codex answer'}]}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output',
                'output': 'PRIVATE_CODEX_TOOL_OUTPUT'}},
        ])
        encoded = json.dumps(history_page(self.path, 'codex'))
        self.assertIn('Visible Codex question', encoded)
        self.assertIn('Visible Codex answer', encoded)
        self.assertNotIn('PRIVATE_CODEX_ANALYSIS', encoded)
        self.assertNotIn('PRIVATE_CODEX_TOOL_OUTPUT', encoded)

    def test_older_pages_recover_every_visible_record_without_duplication(self):
        texts = ['Visible message %03d café 漢字' % i for i in range(75)]
        append_rows(self.path, [claude_message(text, identifier='id-' + str(i))
                               for i, text in enumerate(texts)])
        pages = []
        cursor = None
        for _ in range(30):
            page = history_page(self.path, 'claude', before=cursor, limit=7)
            pages.append(page['messages'])
            if not page['has_more']:
                break
            self.assertIsNotNone(page['before'])
            self.assertNotEqual(page['before'], cursor)
            cursor = page['before']
        else:
            self.fail('History pagination did not finish.')
        encoded = json.dumps(pages, ensure_ascii=False)
        for text in texts:
            self.assertEqual(encoded.count(text), 1, text)

    def test_history_cursor_rejects_arbitrary_or_replaced_file_cursor(self):
        append_rows(self.path, [claude_message('Original %d' % i, identifier=str(i)) for i in range(5)])
        page = history_page(self.path, 'claude', limit=1)
        self.assertTrue(page['has_more'])
        with self.assertRaises(ValueError):
            history_page(self.path, 'claude', before='../../unrelated-file')
        self.path.write_text('', encoding='utf-8')
        append_rows(self.path, [claude_message('Replacement %d' % i, identifier=str(i)) for i in range(5)])
        with self.assertRaises(ValueError):
            history_page(self.path, 'claude', before=page['before'])

    def test_saved_cursor_keeps_its_older_page_when_new_messages_are_appended(self):
        append_rows(self.path, [claude_message('Before append %d' % i, identifier=str(i)) for i in range(6)])
        latest = history_page(self.path, 'claude', limit=2)
        expected = history_page(self.path, 'claude', before=latest['before'], limit=2)
        append_rows(self.path, [claude_message('New message after cursor', identifier='new')])
        actual = history_page(self.path, 'claude', before=latest['before'], limit=2)
        self.assertEqual(actual['messages'], expected['messages'])
        self.assertNotIn('New message after cursor', json.dumps(actual['messages']))
        self.assertIn('New message after cursor', json.dumps(history_page(self.path, 'claude')))

    def test_incomplete_last_record_is_read_after_writer_finishes_it(self):
        append_rows(self.path, [claude_message('Completed record')])
        payload = json.dumps(claude_message('Eventually completed record', identifier='later'))
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(payload[:len(payload) // 2])
        first = history_page(self.path, 'claude')
        self.assertIn('Completed record', json.dumps(first))
        self.assertNotIn('Eventually completed record', json.dumps(first))
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(payload[len(payload) // 2:] + '\n')
        second = history_page(self.path, 'claude')
        self.assertIn('Eventually completed record', json.dumps(second))

    def test_oversized_record_cannot_trap_older_history_pagination(self):
        append_rows(self.path, [claude_message('Oldest visible record', identifier='oldest'),
                               claude_message('X' * 4000, identifier='large'),
                               claude_message('Newest visible record', identifier='newest')])
        pages = []
        cursor = None
        seen = set()
        with patch('coordinator_viewer.MAX_HISTORY_BYTES', 1024):
            for _ in range(20):
                page = history_page(self.path, 'claude', before=cursor)
                pages.append(page)
                if not page['has_more']:
                    break
                cursor = page['before']
                self.assertNotIn(cursor, seen, 'An oversized record trapped pagination at the same cursor.')
                seen.add(cursor)
            else:
                self.fail('Bounded history pages did not reach older records.')
        self.assertIn('Oldest visible record', json.dumps(pages))
        self.assertIn('Newest visible record', json.dumps(pages))
        self.assertTrue(any(page['notice'] for page in pages), 'Skipped oversized content must be explained.')


class StoreTests(unittest.TestCase):
    def setUp(self):
        policy = patch('coordinator_viewer.require_model_allowed', side_effect=lambda model: model)
        self.model_policy = policy.start()
        self.addCleanup(policy.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'workspace'
        self.root.mkdir()
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.run, _ = create_run(self.root, 'viewer-test', 'Keep the exact coordinator visible')
        self.coordinator = Coordinator(self.run)
        state = self.coordinator.read()
        state.update(owner='fable', session=LOGICAL_SESSION,
                     handoff={'id': HANDOFF_ID, 'to': 'fable', 'from': 'astra',
                              'launch': {'session': LOGICAL_SESSION, 'background_id': SHORT_ID,
                                         'status': 'submitted'}})
        self.coordinator.save(state)
        self.listing = [{'kind': 'background', 'id': SHORT_ID,
                         'name': 'Fable coordinator ' + HANDOFF_ID[:8],
                         'cwd': str(self.root), 'sessionId': ACTUAL_SESSION,
                         'status': 'running', 'startedAt': 123}]
        write_json(self.home / '.claude' / 'jobs' / SHORT_ID / 'state.json',
                   {'cwd': str(self.root), 'sessionId': ACTUAL_SESSION, 'daemonShort': SHORT_ID})
        project = re.sub('[^a-zA-Z0-9-]', '-', str(self.root))
        self.transcript = self.home / '.claude' / 'projects' / project / (ACTUAL_SESSION + '.jsonl')
        append_rows(self.transcript, [claude_message('Exact run saved conversation')])
        self.opened = []
        self.store = ViewerStore(self.root, home=self.home,
                                 async_listing=False,
                                 collector=lambda: self.listing,
                                 opener=lambda *args: self.opened.append(args) or 'opened')

    def attach_payload(self):
        state = self.coordinator.read()
        return {'run': self.run.name, 'session': state['session'],
                'generation': state['generation'], 'background_id': SHORT_ID}

    def test_saved_history_uses_actual_daemon_session_not_logical_handoff_uuid(self):
        page = self.store.history(self.run.name)
        self.assertTrue(page['available'])
        self.assertIn('Exact run saved conversation', json.dumps(page))
        self.assertEqual(self.opened, [])

    def test_ended_session_retains_saved_history_without_launching_anything(self):
        self.listing[0]['status'] = 'done'
        page = self.store.history(self.run.name)
        self.assertTrue(page['available'])
        self.assertIn('Exact run saved conversation', json.dumps(page))
        self.assertEqual(self.opened, [])

    def test_saved_opus_launch_name_resolves_only_that_handoff_and_labels_it(self):
        state = self.coordinator.read()
        launch = state['handoff']['launch']
        launch.pop('background_id')
        launch.update(model='opus', name='Claude Opus coordinator ' + HANDOFF_ID[:8])
        self.coordinator.save(state)
        self.listing[0]['name'] = launch['name']
        self.listing.append(dict(self.listing[0], id='bbbb2222',
                                 name='Claude Opus coordinator unrelated', startedAt=999))
        provider = self.store.provider(state)
        self.assertEqual(provider['background_id'], SHORT_ID)
        self.assertEqual(provider['model'], 'opus')
        self.assertEqual(provider['name'], 'Claude / Opus')
        self.assertEqual(provider['interaction']['label'], 'Open Opus console')
        self.assertTrue(provider['can_attach'])
        self.model_policy.assert_called_with('opus')

    def test_missing_saved_opus_name_does_not_select_another_run_or_legacy_name(self):
        state = self.coordinator.read()
        state['handoff']['launch'].pop('background_id')
        state['handoff']['launch'].update(model='opus', name='Claude Opus coordinator ' + HANDOFF_ID[:8])
        self.listing.append(dict(self.listing[0], id='bbbb2222',
                                 name='Claude Opus coordinator unrelated', startedAt=999))
        provider = self.store.provider(state)
        self.assertIsNone(provider['background_id'])
        self.assertFalse(provider['can_attach'])

    def test_manual_takeover_uses_saved_receiving_model_without_launch_model(self):
        state = self.coordinator.read()
        state['handoff']['receiving_model'] = 'opus'
        provider = self.store.provider(state)
        self.assertEqual(provider['name'], 'Claude / Opus')
        self.assertEqual(provider['model'], 'opus')
        self.model_policy.assert_called_with('opus')

    def test_paused_legacy_fable_preserves_history_but_cannot_attach_or_interact(self):
        self.model_policy.side_effect = ValueError('Claude Fable is paused by the user until further notice')
        state = self.coordinator.read()
        provider = self.store.provider(state)
        self.assertEqual(provider['name'], 'Claude / Fable')
        self.assertTrue(provider['history_available'])
        self.assertFalse(provider['can_attach'])
        self.assertIn('paused by the user', provider['interaction']['reason'])
        self.assertIn('Exact run saved conversation', json.dumps(self.store.history(self.run.name)))
        self.model_policy.assert_called_with('claude-fable-5')
        with self.assertRaises(ValueError):
            self.store.attach(self.attach_payload())
        request = {key: provider['interaction'][key] for key in ('kind', 'session_id', 'background_id')}
        request.update(run=self.run.name, session=state['session'], generation=state['generation'])
        with self.assertRaisesRegex(ValueError, 'paused by the user'):
            self.store.interact(request)
        self.assertEqual(self.opened, [])

    def test_model_pause_is_rechecked_before_opening_a_previously_available_console(self):
        state = self.coordinator.read()
        state['handoff']['launch']['model'] = 'opus'
        self.coordinator.save(state)
        action = self.store.provider(state)['interaction']
        self.assertTrue(action['available'])
        request = {key: action[key] for key in ('kind', 'session_id', 'background_id')}
        request.update(run=self.run.name, session=state['session'], generation=state['generation'])
        self.model_policy.side_effect = ValueError('Claude Opus is paused by the user until further notice')
        with self.assertRaisesRegex(ValueError, 'paused by the user'):
            self.store.interact(request)
        self.assertEqual(self.opened, [])

    def test_saved_opus_receipt_cannot_attach_a_session_reporting_paused_fable(self):
        def allow_opus(model):
            if 'fable' in model:
                raise ValueError('Claude Fable is paused by the user until further notice')
            return model
        self.model_policy.side_effect = allow_opus
        state = self.coordinator.read()
        state['handoff']['launch']['model'] = 'opus'
        self.listing[0]['model'] = 'claude-fable-5'
        provider = self.store.provider(state)
        self.assertEqual(provider['name'], 'Claude / Opus')
        self.assertFalse(provider['can_attach'])
        self.assertTrue(provider['history_available'])
        self.assertIn('Fable is paused', provider['interaction']['reason'])

    def test_ended_session_has_explanation_instead_of_a_broken_console_button(self):
        self.listing[0]['status'] = 'done'
        action = self.store.provider(self.coordinator.read())['interaction']
        self.assertFalse(action['available'])
        self.assertIn('has ended', action['reason'])
        with self.assertRaises(ValueError):
            self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])

    def test_unknown_live_state_is_explained_and_not_treated_as_attachable(self):
        self.listing[0]['status'] = None
        self.listing[0]['state'] = 'future-unknown-state'
        provider = self.store.provider(self.coordinator.read())
        self.assertFalse(provider['interaction']['available'])
        self.assertIn('unrecognized session state', provider['interaction']['reason'])
        self.assertEqual(provider['status'], 'future-unknown-state')
        with self.assertRaises(ValueError):
            self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])

    def test_provider_aware_fable_action_still_opens_the_exact_console(self):
        state = self.coordinator.read()
        action = self.store.provider(state)['interaction']
        self.assertTrue(action['available'])
        request = {key: action[key] for key in ('kind', 'session_id', 'background_id')}
        request.update(run=self.run.name, session=state['session'], generation=state['generation'])
        self.assertEqual(self.store.interact(request)['model_calls'], 0)
        self.assertEqual(self.opened[0][1], SHORT_ID)

    def test_provider_aware_fable_action_rejects_changed_actual_session(self):
        state = self.coordinator.read()
        action = self.store.provider(state)['interaction']
        request = {key: action[key] for key in ('kind', 'session_id', 'background_id')}
        request.update(run=self.run.name, session=state['session'], generation=state['generation'])
        self.listing[0]['sessionId'] = LOGICAL_SESSION
        write_json(self.home / '.claude/jobs' / SHORT_ID / 'state.json',
                   {'cwd': str(self.root), 'sessionId': LOGICAL_SESSION, 'daemonShort': SHORT_ID})
        with self.assertRaises(ValueError):
            self.store.interact(request)
        self.assertEqual(self.opened, [])

    def test_explicit_run_never_attaches_another_newer_fable(self):
        self.listing[:] = [{'kind': 'background', 'id': 'bbbb2222',
                           'name': 'Fable coordinator unrelated', 'cwd': str(self.root),
                           'status': 'running', 'startedAt': 999}]
        with self.assertRaises(ValueError):
            self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])

    def test_stale_lead_generation_cannot_open_a_session(self):
        payload = self.attach_payload()
        state = self.coordinator.read()
        state['generation'] += 1
        self.coordinator.save(state)
        with self.assertRaises(ValueError):
            self.store.attach(payload)
        self.assertEqual(self.opened, [])

    def test_exact_current_session_opens_once_and_read_only_views_do_not_attach(self):
        self.store.runs()
        self.store.detail(self.run.name)
        self.store.history(self.run.name)
        self.assertEqual(self.opened, [])
        response = self.store.attach(self.attach_payload())
        self.assertEqual(response['status'], 'opened')
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.opened[0][1], SHORT_ID)
        self.assertEqual(self.opened[0][2], self.root)
        self.store.attach(self.attach_payload())
        self.assertEqual(len(self.opened), 1, 'A double click must not steal the same attachment twice.')

    def test_conflicting_daemon_session_metadata_disables_history_and_attach(self):
        write_json(self.home / '.claude' / 'jobs' / SHORT_ID / 'state.json',
                   {'cwd': str(self.root), 'sessionId': LOGICAL_SESSION, 'daemonShort': SHORT_ID})
        detail = self.store.detail(self.run.name)
        self.assertFalse(detail['provider']['history_available'])
        self.assertFalse(detail['provider']['can_attach'])
        with self.assertRaises(ValueError):
            self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])

    def test_foreign_project_listing_cannot_be_attached_as_this_run(self):
        self.listing[0]['cwd'] = str(self.home)
        detail = self.store.detail(self.run.name)
        self.assertFalse(detail['provider']['can_attach'])
        with self.assertRaises(ValueError):
            self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])

    def test_conflicting_job_project_does_not_silently_trust_listing(self):
        write_json(self.home / '.claude' / 'jobs' / SHORT_ID / 'state.json',
                   {'cwd': str(self.home), 'sessionId': ACTUAL_SESSION, 'daemonShort': SHORT_ID})
        detail = self.store.detail(self.run.name)
        self.assertFalse(detail['provider']['can_attach'])
        self.assertFalse(detail['provider']['history_available'])
        with self.assertRaises(ValueError):
            self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])

    def test_symlinked_run_cannot_escape_workspace(self):
        foreign, _ = create_run(self.home, 'foreign-run', 'Unrelated workspace')
        link = self.root / '.orchestration' / 'linked-run'
        try:
            directory_link(foreign, link)
        except OSError:
            self.skipTest('The host does not permit creation of directory links.')
        with self.assertRaises(ValueError):
            self.store.detail(link.name)

    def test_symlinked_history_cannot_read_outside_provider_transcripts(self):
        outside = self.home / 'outside-project' / self.transcript.name
        append_rows(outside, [claude_message('UNRELATED_PRIVATE_SESSION')])
        self.transcript.unlink()
        self.transcript.parent.rmdir()
        try:
            directory_link(outside.parent, self.transcript.parent)
        except OSError:
            self.skipTest('The host does not permit creation of directory links.')
        detail = self.store.detail(self.run.name)
        self.assertFalse(detail['provider']['history_available'])
        try:
            result = self.store.history(self.run.name)
        except ValueError:
            return
        self.assertNotIn('UNRELATED_PRIVATE_SESSION', json.dumps(result))

    def test_codex_binding_is_exact_and_expires_when_logical_coordinator_changes(self):
        state = self.coordinator.read()
        state.update(owner='astra', session='logical-astra-session')
        self.coordinator.save(state)
        transcript = self.home / '.codex/sessions/2026/09/09' / ('rollout-2026-09-09-' + ACTUAL_SESSION + '.jsonl')
        append_rows(transcript, [
            {'type': 'session_meta', 'payload': {'id': ACTUAL_SESSION, 'cwd': str(self.root)}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'channel': 'final',
                'content': [{'type': 'output_text', 'text': 'Bound exact Codex conversation'}]}},
        ])
        self.store.bind(self.run.name, 'codex', ACTUAL_SESSION)
        self.assertIn('Bound exact Codex conversation', json.dumps(self.store.history(self.run.name)))
        state['session'] = 'new-logical-astra-session'
        state['generation'] += 1
        self.coordinator.save(state)
        self.assertFalse(self.store.history(self.run.name)['available'])
        self.assertEqual(self.opened, [])

    def test_run_identifiers_cannot_read_other_directories(self):
        for identifier in ('../outside', str(self.run), self.run.name + '/../outside', '.', ''):
            with self.subTest(identifier=identifier):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.store.detail(identifier)

    def test_cli_failure_is_reported_without_opening_another_session(self):
        def failed_collector():
            raise RuntimeError('Synthetic collector failure')
        self.store = ViewerStore(self.root, home=self.home,
                                 collector=failed_collector,
                                 async_listing=False,
                                 opener=lambda *args: self.opened.append(args) or 'opened')
        detail = self.store.detail(self.run.name)
        self.assertIsInstance(detail, dict)
        with self.assertRaises((RuntimeError, ValueError)):
            self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])

    def test_real_cli_collector_timeout_preserves_history_and_never_starts_inference(self):
        self.store = ViewerStore(self.root, home=self.home,
                                 async_listing=False,
                                 opener=lambda *args: self.opened.append(args) or 'opened')
        with patch('coordinator_viewer.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('claude agents', 15)) as command:
            detail = self.store.detail(self.run.name)
            self.assertTrue(detail['provider']['error'])
            self.assertFalse(detail['provider']['can_attach'])
            self.assertIn('Exact run saved conversation', json.dumps(self.store.history(self.run.name)))
            with self.assertRaises(ValueError):
                self.store.attach(self.attach_payload())
        self.assertEqual(self.opened, [])
        self.assertGreaterEqual(command.call_count, 1)
        for call in command.call_args_list:
            self.assertEqual(call.args[0][1:5], ['agents', '--json', '--all', '--cwd'])
            self.assertNotIn('--bg', call.args[0])
            self.assertNotIn('--resume', call.args[0])
            self.assertNotIn('--print', call.args[0])
            self.assertGreater(call.kwargs['timeout'], 0)

    def test_slow_provider_status_does_not_delay_saved_history_or_checkpoint(self):
        entered, release, finished = Event(), Event(), Event()
        def slow_collector():
            entered.set()
            release.wait(3)
            finished.set()
            return self.listing
        self.store = ViewerStore(self.root, home=self.home, collector=slow_collector,
                                 opener=lambda *args: self.opened.append(args) or 'opened')
        try:
            started = time.monotonic()
            detail = self.store.detail(self.run.name)
            history = self.store.history(self.run.name)
            self.assertLess(time.monotonic() - started, .75)
            self.assertTrue(entered.wait(.5))
            self.assertTrue(detail['provider']['refreshing'])
            self.assertEqual(detail['provider']['status'], 'checking')
            self.assertFalse(detail['provider']['can_attach'])
            self.assertIn('Exact run saved conversation', json.dumps(history))
            self.assertEqual(self.opened, [])
        finally:
            release.set()
            self.assertTrue(finished.wait(2))

    def test_detail_exposes_verified_scope_and_sanitized_activity(self):
        manifest = json.loads((self.run / 'run.json').read_text(encoding='utf-8'))
        manifest['memory_project_id'] = 'shared-library'
        write_json(self.run / 'run.json', manifest)
        expected = {'tasks': [], 'checked_at': '2026-09-10T01:00:00Z', 'task_count': 0}
        with patch('task_activity.snapshot', return_value=expected) as activity:
            detail = self.store.detail(self.run.name)
        self.assertEqual(detail['memory_projects'], ['shared-library'])
        self.assertEqual(detail['activity'], expected)
        activity.assert_called_once_with(self.root.resolve(), self.run.name, ['shared-library'], home=self.home.resolve())


class FakeHTTPStore:
    def __init__(self):
        self.opened = []

    def runs(self):
        return {'runs': [], 'errors': [], 'model_calls': 0}

    def detail(self, identifier):
        return {'id': identifier, 'provider': {'can_attach': False}}

    def history(self, identifier, before=None):
        return {'available': True, 'messages': [], 'before': None,
                'has_more': False, 'notice': '', 'revision': 'fixture'}

    def attach(self, payload):
        self.opened.append(payload)
        return {'ok': True, 'status': 'opened'}

    def interact(self, payload):
        self.opened.append(payload)
        return {'ok': True, 'status': 'opened'}


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FakeHTTPStore()
        self.server = ViewerServer(Path(self.temp.name), store=self.store)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method='GET', path='/', payload=None, authorized=True, headers=None):
        final_headers = {}
        if authorized:
            final_headers['X-Viewer-Token'] = self.server.viewer_token
        if method == 'POST':
            final_headers['Origin'] = self.server.origin
        body = None
        if payload is not None:
            final_headers['Content-Type'] = 'application/json'
            body = json.dumps(payload).encode('utf-8')
        final_headers.update(headers or {})
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, body=body, headers=final_headers)
            response = connection.getresponse()
            content = response.read().decode('utf-8')
            return response.status, dict(response.getheaders()), content
        finally:
            connection.close()

    def test_read_only_api_cannot_open_an_interactive_session(self):
        for endpoint in ('/api/runs', '/api/run?run=fixture', '/api/history?run=fixture'):
            with self.subTest(endpoint=endpoint):
                status, _, _ = self.request(path=endpoint)
                self.assertEqual(status, 200)
        self.assertEqual(self.store.opened, [])

    def test_api_requires_token_and_rejects_cross_origin_or_foreign_host(self):
        for headers, authorized in (({}, False), ({'Origin': 'https://foreign.example'}, True),
                                    ({'Host': 'foreign.example'}, True),
                                    ({'Sec-Fetch-Site': 'cross-site'}, True)):
            with self.subTest(headers=headers, authorized=authorized):
                status, _, _ = self.request(path='/api/runs', headers=headers, authorized=authorized)
                self.assertEqual(status, 403)
        self.assertEqual(self.store.opened, [])

    def test_memory_port_can_navigate_to_exact_viewer_run_without_api_access(self):
        navigation = {'Sec-Fetch-Site': 'same-site', 'Sec-Fetch-Mode': 'navigate',
                      'Sec-Fetch-Dest': 'document'}
        status, headers, body = self.request(path='/?run=fixture&project=alpha',
                                             headers=navigation, authorized=False)
        self.assertEqual(status, 200)
        self.assertIn('Your session, in view.', body)
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(self.request(path='/api/runs', headers=navigation)[0], 403)
        self.assertEqual(self.request('POST', '/api/attach', payload={'run': 'fixture'}, headers=navigation)[0], 403)
        for invalid in ({'Sec-Fetch-Mode': 'cors'}, {'Sec-Fetch-Dest': 'iframe'},
                        {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Dest': 'iframe'}, {'Host': 'foreign.example'},
                        {'Origin': 'http://127.0.0.1:5151'}):
            self.assertEqual(self.request(headers={**navigation, **invalid}, authorized=False)[0], 403, invalid)
        self.assertEqual(self.store.opened, [])

    def test_attach_requires_same_origin_token_and_a_post(self):
        payload = {'run': 'fixture', 'session': 'session', 'generation': 1, 'background_id': SHORT_ID}
        for headers, authorized in (({'Origin': ''}, True), ({'Origin': 'https://foreign.example'}, True),
                                    ({}, False), ({'X-Viewer-Token': 'wrong'}, True)):
            with self.subTest(headers=headers, authorized=authorized):
                status, _, _ = self.request('POST', '/api/attach', payload=payload,
                                            headers=headers, authorized=authorized)
                self.assertEqual(status, 403)
        status, _, _ = self.request(path='/api/attach')
        self.assertEqual(status, 404)
        self.assertEqual(self.store.opened, [])
        status, _, _ = self.request('POST', '/api/attach', payload=payload)
        self.assertEqual(status, 200)
        self.assertEqual(self.store.opened, [payload])

    def test_interact_requires_same_origin_token_and_post_before_opening_any_provider(self):
        payload = {'run': 'fixture', 'session': 'session', 'generation': 1,
                   'kind': 'codex_conversation', 'session_id': ACTUAL_SESSION, 'background_id': None}
        for headers, authorized in (({'Origin': ''}, True), ({'Origin': 'https://foreign.example'}, True),
                                    ({}, False), ({'X-Viewer-Token': 'wrong'}, True),
                                    ({'Sec-Fetch-Site': 'cross-site'}, True)):
            with self.subTest(headers=headers, authorized=authorized):
                self.assertEqual(self.request('POST', '/api/interact', payload=payload,
                                              headers=headers, authorized=authorized)[0], 403)
        self.assertEqual(self.request(path='/api/interact')[0], 404)
        self.assertEqual(self.store.opened, [])
        self.assertEqual(self.request('POST', '/api/interact', payload=payload)[0], 200)
        self.assertEqual(self.store.opened, [payload])

    def test_duplicate_or_unknown_query_fields_do_not_select_a_run(self):
        for endpoint in ('/api/run?run=first&run=second', '/api/history?run=fixture&path=secret',
                         '/api/runs?run=fixture'):
            with self.subTest(endpoint=endpoint):
                status, _, _ = self.request(path=endpoint)
                self.assertEqual(status, 400)

    def test_oversized_mutation_body_is_rejected_before_opening_a_console(self):
        status, _, _ = self.request('POST', '/api/attach', payload={'padding': 'X' * 70000})
        self.assertEqual(status, 413)
        self.assertEqual(self.store.opened, [])

    def test_duplicate_api_token_header_is_rejected(self):
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.putrequest('GET', '/api/runs')
            connection.putheader('X-Viewer-Token', self.server.viewer_token)
            connection.putheader('X-Viewer-Token', self.server.viewer_token)
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

    def test_loopback_page_has_nonce_and_does_not_allow_embedding_or_caching(self):
        status, headers, body = self.request(authorized=False)
        self.assertEqual(status, 200)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(headers['Cross-Origin-Resource-Policy'], 'same-origin')
        self.assertIn("script-src 'nonce-" + self.server.nonce, headers['Content-Security-Policy'])
        self.assertIn(self.server.viewer_token, body)
        self.assertNotIn('__TOKEN__', body)
        self.assertNotIn('__NONCE__', body)
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_services_and_open_are_gated_and_never_touch_sessions(self):
        status, _, body = self.request(path='/api/services')
        self.assertEqual(status, 200, body)
        body = json.loads(body)
        self.assertEqual(body['current'], 'viewer')
        self.assertEqual([(r['id'], r['current'], r['origin']) for r in body['services']],
                         [('brain', False, None), ('viewer', True, None)])
        self.assertEqual(self.request(path='/api/services?run=fixture')[0], 400)
        self.assertEqual(self.request(path='/api/services', authorized=False)[0], 403)
        self.assertEqual(self.request(path='/api/open')[0], 404)
        self.assertEqual(self.request('POST', '/api/open', payload={'target': 'brain'}, headers={'Origin': 'http://evil.example'})[0], 403)
        opened = {'origin': 'http://127.0.0.1:6161', 'reused': False, 'pid': 7}
        with patch('start_brain_dashboard.open_dashboard', return_value=opened) as launcher:
            status, _, body = self.request('POST', '/api/open', payload={'target': 'brain'})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['origin'], 'http://127.0.0.1:6161')
        self.assertEqual(launcher.call_args.kwargs['script'], 'brain_dashboard.py')
        self.assertEqual(self.request('POST', '/api/open', payload={'target': 'viewer-2'})[0], 400)
        self.assertEqual(self.store.opened, [])
        page = self.request(authorized=False)[2]
        self.assertIn('id="crumb-brain"', page)
        self.assertIn("api('/api/open', {target: 'brain'})", page)
        self.assertIn("scope.set('project', memoryProject)", page)
        self.assertIn("scope.set('run', selected)", page)


if __name__ == '__main__':
    unittest.main()
