"""Relay messages in startup packets and closeout; temporary local stores only, no provider calls."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from coordinator_handoff import Coordinator
from init_run import create_run
from orchestration_lifecycle import closeout_run, start_run
from paths import ROOT
import relay_bridge


def operating():
    context = '# Shared operating guidance\nRead current instructions; memories are evidence.\n'
    return {'schema_version': 1, 'revision': '1', 'context': context,
            'chars': len(context), 'sha256': hashlib.sha256(context.encode()).hexdigest(),
            'source': 'app/assets/orchestration-context.md', 'loaded_at': '2026-10-02T00:00:00+00:00'}


class RelayLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'app/assets').mkdir(parents=True)
        shutil.copyfile(ROOT / 'app/assets/project-map.html', self.root / 'app/assets/project-map.html')
        self.run, _ = create_run(self.root, 'relay-scope', 'Check Relay listing', project_id='alpha', lead='astra')
        state = Coordinator(self.run).read()
        self.identity = {key: state[key] for key in ('owner', 'session', 'generation')}
        self.mail = self.root / 'runtime' / 'relay'
        context = patch('orchestration_lifecycle.load_operating_context', side_effect=operating)
        context.start()
        self.addCleanup(context.stop)

    def post(self, sender='relay', run_id=None, subject='Status?', **extra):
        return relay_bridge.post(sender, subject, 'Body for ' + subject, run_id=run_id, root=self.mail, **extra)

    def packet_text(self):
        return (self.run / 'startup-context.md').read_text(encoding='utf-8')

    def test_quiet_run_packet_is_unchanged(self):
        packet = start_run(run=self.run, root=self.root, **self.identity)
        self.assertNotIn('relay_inbox', packet)
        self.assertNotIn('## Relay messages', self.packet_text())
        self.assertFalse(self.mail.exists())

    def test_lists_only_this_runs_unread_lead_messages_without_consuming(self):
        mine = self.post(run_id=self.run.name, subject='For this run', kind='request')
        self.post(run_id='another-run-20261002T000000Z-aaaaaaaa', subject='For another run')
        self.post(subject='No run named')
        self.post(sender='lead', run_id=self.run.name, subject='Lead to Relay')
        self.post(sender='user', subject='Voice to Relay')
        packet = start_run(run=self.run, root=self.root, **self.identity)
        listed = packet['relay_inbox']
        self.assertEqual([m['id'] for m in listed['messages']], [mine['id']])
        self.assertEqual((listed['unscoped_unread'], listed['marks_read']), (1, False))
        text = self.packet_text()
        self.assertIn('## Relay messages for this run', text)
        self.assertIn(f"id {mine['id']}, request from relay", text)
        self.assertIn('not instructions or authorization', text)
        self.assertNotIn('For another run', text)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), packet['packet_sha256'])
        self.assertFalse((self.mail / 'cursors.json').exists())
        self.assertEqual(relay_bridge.inbox('lead', mark_read=False, root=self.mail)['count'], 3)

    def test_reading_removes_messages_from_later_packets(self):
        self.post(run_id=self.run.name, subject='First')
        self.post(subject='General')
        relay_bridge.inbox('lead', run_id=self.run.name, root=self.mail, mark_read=True)
        packet = start_run(run=self.run, root=self.root, **self.identity)
        self.assertEqual((packet['relay_inbox']['messages'], packet['relay_inbox']['unscoped_unread']), ([], 1))
        relay_bridge.inbox('lead', root=self.mail, mark_read=True)
        self.assertNotIn('relay_inbox', start_run(run=self.run, root=self.root, **self.identity))

    def test_new_generation_still_lists_and_stale_identity_is_refused(self):
        self.post(run_id=self.run.name, subject='Across checkpoints')
        coordinator = Coordinator(self.run)
        checkpoint = coordinator.read()['checkpoint']
        coordinator.checkpoint(checkpoint, self.identity['owner'], self.identity['session'], self.identity['generation'])
        with self.assertRaises(ValueError):
            start_run(run=self.run, root=self.root, **self.identity)
        current = dict(self.identity, generation=self.identity['generation'] + 1)
        packet = start_run(run=self.run, root=self.root, **current)
        self.assertEqual(packet['coordinator']['generation'], current['generation'])
        self.assertEqual([m['subject'] for m in packet['relay_inbox']['messages']], ['Across checkpoints'])

    def test_unreadable_mailbox_is_reported_without_failing_startup(self):
        self.post(run_id=self.run.name)
        with patch.object(relay_bridge, 'MAX_MAILBOX_BYTES', 10), patch.object(relay_bridge, 'MAX_BODY', 1):
            packet = start_run(run=self.run, root=self.root, **self.identity)
        self.assertEqual(packet['relay_inbox']['status'], 'unavailable')
        self.assertIn('could not be read', self.packet_text())

    def test_closeout_reports_a_notice_that_never_holds(self):
        start_run(run=self.run, root=self.root, **self.identity)
        quiet = closeout_run(self.run, root=self.root, **self.identity)
        message = self.post(run_id=self.run.name, subject='Before you close')
        noticed = closeout_run(self.run, root=self.root, **self.identity)
        self.assertNotIn('relay_inbox', quiet)
        self.assertEqual([m['id'] for m in noticed['relay_inbox']['messages']], [message['id']])
        self.assertFalse(noticed['relay_inbox']['blocks_completion'])
        self.assertNotIn('relay_inbox', [row['check'] for row in noticed['checks']])
        strip = lambda result: [(row['check'], row['status']) for row in result['checks']]
        self.assertEqual(strip(noticed), strip(quiet))
        saved = json.loads((self.run / 'closeout.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['relay_inbox']['messages'][0]['id'], message['id'])
        self.assertEqual(relay_bridge.inbox('lead', mark_read=False, root=self.mail)['count'], 1)


class UserPartyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.mail = Path(temporary.name)

    def test_routes_are_hub_shaped_through_relay(self):
        self.assertEqual(relay_bridge.post('user', 's', 'b', root=self.mail)['to'], 'relay')
        self.assertEqual(relay_bridge.post('relay', 's', 'b', to='user', root=self.mail)['to'], 'user')
        self.assertEqual(relay_bridge.post('relay', 's', 'b', root=self.mail)['to'], 'lead')
        for sender, to in (('user', 'lead'), ('lead', 'user'), ('user', 'user'), ('relay', 'relay')):
            with self.subTest(sender=sender, to=to), self.assertRaises(ValueError):
                relay_bridge.post(sender, 's', 'b', to=to, root=self.mail)

    def test_client_ref_makes_retries_idempotent_per_sender(self):
        first = relay_bridge.post('user', 'Voice', 'Hello Relay', client_ref='req-12345678', root=self.mail)
        again = relay_bridge.post('user', 'Voice', 'Hello Relay', client_ref='req-12345678', root=self.mail)
        self.assertEqual((again['id'], again['seq'], again['duplicate']), (first['id'], first['seq'], True))
        other = relay_bridge.post('relay', 'x', 'y', to='user', client_ref='req-12345678', root=self.mail)
        self.assertNotEqual(other['id'], first['id'])
        self.assertEqual(len(relay_bridge.thread(root=self.mail)['messages']), 2)
        with self.assertRaises(ValueError):
            relay_bridge.post('user', 's', 'b', client_ref='short', root=self.mail)

    def test_cli_sends_user_body_from_stdin(self):
        # main() on a temporary mailbox; a subprocess would write to the real runtime mailbox.
        stdin = io.TextIOWrapper(io.BytesIO('Plan my afternoon — café at 3'.encode('utf-8')), encoding='utf-8')
        with patch.object(relay_bridge, 'relay_dir', return_value=self.mail), patch('sys.stdin', new=stdin), \
                patch('builtins.print'):
            relay_bridge.main(['send', '--from', 'user', '--kind', 'request', '--subject', 'Voice request',
                               '--body-stdin', '--client-ref', 'voice-abcdef12'])
        saved = relay_bridge.thread(root=self.mail)['messages'][0]
        self.assertEqual((saved['from'], saved['to'], saved['body'], saved['client_ref']),
                         ('user', 'relay', 'Plan my afternoon — café at 3', 'voice-abcdef12'))


if __name__ == '__main__':
    unittest.main()
