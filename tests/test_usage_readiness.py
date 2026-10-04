"""Plain per-bot readiness: held reasons, reading age and technical details (no providers)."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from usage_report import age_class, age_words, bot_summary, readiness, render_usage

NOW = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


def window(identifier, hours_old, remaining, pending=0.0, unsettled=0.0, settled=0.0, **extra):
    reserved = pending + settled
    return dict({'id': identifier, 'remaining_pct': remaining, 'reserved_pct': reserved, 'pending_pct': pending,
                 'unsettled_finished_pct': unsettled, 'available_pct': max(0, remaining - reserved),
                 'observed_at': (NOW - timedelta(hours=hours_old)).isoformat(), 'max_age_seconds': 600,
                 'source': 'official test reading', 'reset_at': None, 'reset_display': None,
                 'freshness': 'cached', 'reset_passed': False}, **extra)


HELD_CODEX = {'worker': 'codex', 'status': 'held', 'allowed': False, 'reading_status': 'unknown',
              'windows': [window('codex-provider-block', 32, 100, pending=81),
                          window('codex-weekly', 32, 99, pending=81)],
              'reasons': ['codex-provider-block: available allowance is at or below the worker start threshold',
                          'codex-weekly: available allowance is at or below the worker start threshold'],
              'warnings': ['codex-bucket-d62616d234d8-five-hour: usage unknown; refresh or supply a current account reading',
                           'codex-weekly: reading is stale']}


class TechnicalText(HTMLParser):
    """Collect page text outside and inside Technical details disclosures separately."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.outside, self.inside = [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'details' and ('class', 'technical') in attrs:
            self.depth = 1
        elif tag == 'details' and self.depth:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == 'details' and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        (self.inside if self.depth else self.outside).append(data)


class ReadinessTests(unittest.TestCase):
    def test_age_classes_and_words_follow_the_ten_minute_and_one_day_boundaries(self):
        self.assertEqual([age_class(s) for s in (None, 0, 599, 600, 86399, 86400)],
                         ['unknown', 'fresh', 'fresh', 'aging', 'aging', 'stale'])
        self.assertEqual(age_words(45), 'reading under a minute old')
        self.assertEqual(age_words(19 * 3600), 'reading 19 h old')
        self.assertEqual(age_words(53 * 3600), 'reading 2 days old')

    def test_held_bot_gets_one_plain_sentence_with_reservations_age_and_next_step(self):
        summary = bot_summary(HELD_CODEX, 20, 'advisory', NOW)
        self.assertEqual(summary['state'], 'held')
        self.assertEqual(summary['blocking_text'], 'Blocking new work')
        self.assertEqual(summary['sentence'], 'Held: 18% is free; this assignment prefers an authorized alternate. '
                                              'Unfinished tasks reserve 81% of the 99% left.')
        self.assertEqual((summary['age_text'], summary['age_class']), ('reading 32 h old', 'stale'))
        self.assertIn('close tasks that have finished', summary['next_step'])
        self.assertIn('python orchestrator.py refresh --provider codex', summary['next_step'])
        for text in (summary['sentence'], summary['next_step'], summary['status_text']):
            self.assertNotIn('codex-', text)
        self.assertTrue(any('codex-bucket-d62616d234d8' in item for item in summary['technical']))

    def test_stale_cached_ready_bot_says_it_is_not_blocking_in_advisory_mode(self):
        claude = {'worker': 'claude', 'status': 'ready', 'allowed': True, 'reading_status': 'cached',
                  'windows': [window('claude-five-hour', 53, 99)], 'reasons': [],
                  'warnings': ['claude-five-hour: reading is stale']}
        summary = bot_summary(claude, 20, 'advisory', NOW)
        self.assertEqual((summary['status_text'], summary['age_text'], summary['age_class']),
                         ('Ready', 'reading 2 days old', 'stale'))
        self.assertEqual(summary['blocking_text'], 'Not blocking new work (advisory mode)')
        fresh = dict(claude, reading_status='fresh', windows=[window('claude-five-hour', 0.05, 99)])
        self.assertEqual(bot_summary(fresh, 20, 'advisory', NOW)['blocking_text'], 'Not blocking new work')
        self.assertEqual(bot_summary(fresh, 20, 'advisory', NOW)['age_class'], 'fresh')

    def test_held_summary_names_the_bot_reservation_count_when_known(self):
        held = dict(HELD_CODEX, active_reservation_count=4)
        self.assertIn('4 unfinished tasks reserve 81%', bot_summary(held, 20, 'advisory', NOW)['sentence'])
        held['active_reservation_count'] = 1
        self.assertIn('1 unfinished task reserves 81%', bot_summary(held, 20, 'advisory', NOW)['sentence'])

    def test_cooldown_and_strict_mode_holds_are_explained_without_identifiers(self):
        until = (NOW + timedelta(hours=1)).isoformat()
        cooling = {'worker': 'grok', 'status': 'held', 'allowed': False, 'reading_status': 'fresh',
                   'windows': [window('grok-weekly', .01, 90)], 'cooldown_active_pools': {'grok-weekly': until},
                   'reasons': ['grok-weekly: provider rejected work; cooldown active'], 'warnings': []}
        summary = bot_summary(cooling, 20, 'advisory', NOW)
        self.assertTrue(summary['sentence'].startswith('Held: the provider rejected recent work, so new work waits until '))
        self.assertEqual(summary['next_step'], 'Wait for the cooldown to end, or give the work to another bot.')
        strict = {'worker': 'claude', 'status': 'held', 'allowed': False, 'windows': [window('claude-five-hour', 5, 70)],
                  'reasons': ['claude-five-hour: reading is stale', 'claude-five-hour: latest quota refresh failed']}
        summary = bot_summary(strict, 20, 'strict', NOW)
        self.assertEqual(summary['sentence'], 'Held: the last reading is too old to trust; the latest usage check failed.')
        self.assertIn('refresh --provider claude', summary['next_step'])

    def test_readiness_orders_held_first_and_counts_every_state(self):
        report = {'updated_at': NOW.isoformat(), 'admission_mode': 'advisory', 'worker_start_threshold_pct': 20,
                  'active_reservations': 4,
                  'workers': [{'worker': 'local-chat', 'status': 'local', 'allowed': True, 'windows': []},
                              {'worker': 'grok-bot', 'status': 'ready', 'allowed': True, 'windows': [],
                               'reading_status': 'unknown'},
                              HELD_CODEX]}
        data = readiness(report, NOW)
        self.assertEqual([bot['id'] for bot in data['bots']], ['codex', 'grok-bot', 'local-chat'])
        self.assertEqual(data['counts'], {'held': 1, 'ready': 0, 'unknown': 1, 'local': 1})
        self.assertEqual(data['headline'], 'Bots: 0 ready · 1 held · 1 without a reading · 1 local · oldest reading 32 h')

    def test_rendered_page_keeps_raw_window_ids_inside_technical_details(self):
        report = {'updated_at': NOW.isoformat(), 'admission_mode': 'advisory', 'worker_start_threshold_pct': 20,
                  'active_reservations': 4, 'workers': [HELD_CODEX]}
        page = render_usage(report)
        parsed = TechnicalText()
        parsed.feed(page)
        outside, inside = ' '.join(parsed.outside), ' '.join(parsed.inside)
        self.assertIn('Held: 18% is free; this assignment prefers an authorized alternate', outside)
        self.assertIn('Blocking new work', outside)
        self.assertIn('81% reserved', outside)
        self.assertNotIn('codex-bucket', outside)
        self.assertNotIn('codex-weekly', outside)
        self.assertIn('codex-bucket-d62616d234d8-five-hour', inside)
        self.assertIn('codex-weekly', inside)
        self.assertIn('data-reading-at="', page)
        self.assertNotIn('file:', page)


if __name__ == '__main__':
    unittest.main()
