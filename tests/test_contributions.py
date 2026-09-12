"""Contribution accounting invariants, with no provider calls or token estimates."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from contributions import build_report, write_report


def contributor(identifier, provider='OpenAI', model='Codex'):
    return {'id': identifier, 'name': identifier.title(), 'provider': provider, 'model': model}


def allocation(agent, percent):
    return {'agent_id': agent, 'percent': percent, 'evidence': f'Reviewed accepted artifact by {agent}.'}


def item(identifier, weight=1, shares=None, category='coding', status='accepted'):
    return {'id': identifier, 'label': identifier, 'category': category, 'status': status,
            'weight': weight, 'allocations': shares if shares is not None else [allocation('lead', 100)]}


def activity(identifier, agent='lead', inputs=None, outputs=None, kind='delegation', status='succeeded'):
    return {'id': identifier, 'agent_id': agent, 'kind': kind, 'status': status,
            'task_id': 'task-1', 'input_tokens': inputs, 'output_tokens': outputs, 'actual_models': []}


def ledger(items=None, events=None):
    return {'schema_version': 1, 'scope_id': 'scope-test', 'title': 'Build a presentation',
            'basis': 'Review estimates weighted by accepted deliverable scope.',
            'contributors': [contributor('lead'), contributor('writer', 'Anthropic', 'Claude')],
            'work_items': items if items is not None else [item('implementation')],
            'activity': events if events is not None else []}


class ContributionTests(unittest.TestCase):
    def test_explicit_ninety_ten_is_distinct_from_delegation_share(self):
        result = build_report(ledger([item('code', shares=[allocation('lead', 90), allocation('writer', 10)])],
                                    [activity('one', 'writer'), activity('two', 'writer'), activity('three', 'lead')]))
        self.assertEqual([row['accepted_work_pct'] for row in result['by_agent']], [90, 10])
        self.assertEqual([row['delegation_pct'] for row in result['by_agent']], [33.3, 66.7])
        self.assertEqual(result['usage']['delegations'], 3)
        self.assertTrue(result['attribution_complete'])
        self.assertEqual(result['status'], 'complete')

    def test_categories_are_weighted_by_deliverable_scope_not_equalized(self):
        result = build_report(ledger([item('code', 9), item('slides', 1,
            shares=[allocation('writer', 100)], category='creative')]))
        self.assertEqual([row['accepted_work_pct'] for row in result['by_agent']], [90, 10])
        categories = {row['category']: row for row in result['by_category']}
        self.assertEqual(categories['coding']['by_agent'][0]['accepted_work_pct'], 100)
        self.assertEqual(categories['creative']['by_agent'][1]['accepted_work_pct'], 100)

    def test_rejected_failed_pending_work_never_receives_accepted_credit(self):
        items = [item('accepted')]
        items += [item(status, 500, [allocation('writer', 100)], status=status)
                  for status in ('rejected', 'failed', 'pending')]
        events = [activity('rejected-call', 'writer', 10, 5, status='rejected'),
                  activity('failed-call', 'writer', 10, None, status='failed')]
        result = build_report(ledger(items, events))
        self.assertEqual([row['accepted_work_pct'] for row in result['by_agent']], [100, 0])
        self.assertEqual(result['accepted_weight'], 1)
        self.assertEqual(result['usage']['delegations'], 2)
        self.assertEqual(result['by_agent'][1]['input_tokens'], 20)
        self.assertEqual(result['by_agent'][1]['token_coverage']['status'], 'partial')

    def test_unattributed_accepted_work_remains_in_denominator(self):
        result = build_report(ledger([item('known', 3), item('unassigned', 1, shares=[])]))
        self.assertEqual([row['accepted_work_pct'] for row in result['by_agent']], [75, 0])
        self.assertEqual(result['unattributed_pct'], 25)
        self.assertFalse(result['attribution_complete'])
        self.assertEqual(result['status'], 'needs_attribution')
        self.assertEqual(result['by_category'][0]['unattributed_pct'], 25)

    def test_no_accepted_work_is_pending_not_zero(self):
        result = build_report(ledger([item('draft', status='pending')]))
        self.assertEqual(result['status'], 'pending')
        self.assertFalse(result['attribution_complete'])
        self.assertIsNone(result['unattributed_pct'])
        self.assertTrue(all(row['accepted_work_pct'] is None for row in result['by_agent']))
        self.assertIsNone(result['by_category'][0]['by_agent'][0]['accepted_work_pct'])

    def test_unknown_tokens_are_not_zero_and_partial_coverage_is_explicit(self):
        result = build_report(ledger(events=[activity('one', 'lead', 30, None),
            activity('two', 'lead', None, None), activity('three', 'writer', None, None)]))
        lead, writer = result['by_agent']
        self.assertEqual(lead['input_tokens'], 30)
        self.assertIsNone(lead['output_tokens'])
        self.assertEqual(lead['known_token_share_pct'], 100)
        self.assertEqual(lead['token_coverage']['status'], 'partial')
        self.assertEqual(lead['token_coverage']['events'], 2)
        self.assertIsNone(writer['input_tokens'])
        self.assertIsNone(writer['known_token_share_pct'])
        self.assertEqual(writer['token_coverage']['status'], 'unavailable')
        self.assertEqual(result['usage']['known_tokens'], 30)
        self.assertEqual(result['usage']['token_coverage']['events_with_complete_tokens'], 0)

    def test_explicit_zero_tokens_are_observations_but_zero_denominator_has_no_share(self):
        result = build_report(ledger(events=[activity('one', inputs=0, outputs=0)]))
        self.assertEqual(result['by_agent'][0]['known_tokens'], 0)
        self.assertIsNone(result['by_agent'][0]['known_token_share_pct'])
        self.assertEqual(result['usage']['token_coverage']['status'], 'complete')

    def test_auxiliary_model_names_are_metadata_and_not_extra_usage(self):
        event = activity('one', 'writer', 100, 25)
        event['actual_models'] = ['claude-opus', 'claude-haiku', 'claude-opus']
        event['modelUsage'] = {'claude-opus': {'inputTokens': 1000, 'outputTokens': 1000}}
        result = build_report(ledger(events=[event]))
        self.assertEqual(result['usage']['known_tokens'], 125)
        self.assertEqual(result['by_agent'][1]['actual_models'], ['claude-haiku', 'claude-opus'])
        self.assertEqual(result['by_agent'][1]['model'], 'Claude')

    def test_orchestration_activity_is_not_a_delegation(self):
        result = build_report(ledger(events=[activity('one', kind='review'),
                                            activity('two', kind='orchestration')]))
        self.assertEqual(result['usage']['delegations'], 0)
        self.assertIsNone(result['by_agent'][0]['delegation_pct'])

    def test_same_model_agents_remain_distinct_identities(self):
        data = ledger([item('one'), item('two', shares=[allocation('writer', 100)])])
        data['contributors'][1] = contributor('writer')
        result = build_report(data)
        self.assertEqual(len(result['by_agent']), 2)
        self.assertEqual([row['accepted_work_pct'] for row in result['by_agent']], [50, 50])
        self.assertEqual({row['model'] for row in result['by_agent']}, {'Codex'})

    def test_rounding_includes_unattributed_and_totals_one_hundred(self):
        result = build_report(ledger([item('a'), item('b', shares=[allocation('writer', 100)]),
                                      item('c', shares=[])]))
        values = [row['accepted_work_pct'] for row in result['by_agent']] + [result['unattributed_pct']]
        self.assertEqual(values, [33.4, 33.3, 33.3])
        self.assertAlmostEqual(sum(values), 100)

    def test_invalid_ledgers_fail_closed(self):
        cases = []
        for weight in (0, -1, float('nan'), float('inf'), True, '3'):
            data = ledger()
            data['work_items'][0]['weight'] = weight
            cases.append(data)
        for percent in (-1, 101, float('nan'), True, 99):
            data = ledger()
            data['work_items'][0]['allocations'][0]['percent'] = percent
            cases.append(data)
        for collection in ('contributors', 'work_items'):
            data = ledger()
            data[collection].append(deepcopy(data[collection][0]))
            cases.append(data)
        data = ledger(events=[activity('duplicate'), activity('duplicate')])
        cases.append(data)
        for key, value in [('agent_id', 'unknown'), ('evidence', '')]:
            data = ledger()
            data['work_items'][0]['allocations'][0][key] = value
            cases.append(data)
        data = ledger()
        data['work_items'][0]['allocations'] = [allocation('lead', 50), allocation('lead', 50)]
        cases.append(data)
        cases.append(ledger(events=[activity('one', 'unknown')]))
        for count in (-1, 1.5, True, float('nan')):
            cases.append(ledger(events=[activity('one', inputs=count)]))
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    build_report(data)

    def test_report_is_deterministic_and_does_not_mutate_ledger(self):
        data = ledger(events=[activity('one')])
        original = deepcopy(data)
        report = build_report(data)
        self.assertEqual(report, build_report(data))
        self.assertEqual(data, original)
        report['work_items'][0]['label'] = 'mutated copy'
        self.assertEqual(data, original)

    def test_json_and_readable_markdown_include_evidence_and_coverage(self):
        data = ledger([item('implementation', shares=[allocation('lead', 90), allocation('writer', 10)])])
        with tempfile.TemporaryDirectory() as directory:
            result = write_report(Path(directory), data)
            saved = json.loads((Path(directory) / 'contribution-audit.json').read_text(encoding='utf-8'))
            markdown = (Path(directory) / 'contribution-audit.md').read_text(encoding='utf-8')
            self.assertEqual(result, saved)
            self.assertIn('90.0%', markdown)
            self.assertIn('10.0%', markdown)
            self.assertIn('Reviewed accepted artifact', markdown)
            self.assertIn('Known-token share', markdown)
            self.assertIn('Unknown', markdown)

    def test_failed_replace_preserves_prior_json_and_removes_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            existing = path / 'contribution-audit.json'
            existing.write_text('prior report', encoding='utf-8')
            with patch('contributions.os.replace', side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):
                    write_report(path, ledger())
            self.assertEqual(existing.read_text(), 'prior report')
            self.assertEqual(list(path.glob('*.tmp')), [])

    def test_invalid_report_stems_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            for stem in ('../escape', 'C:\\escape', '.', '', 'report.json', 'a/b'):
                with self.subTest(stem=stem), self.assertRaises(ValueError):
                    write_report(Path(directory), ledger(), stem=stem)


if __name__ == '__main__':
    unittest.main()
