"""App-wide Jev in-flight slots and the new configuration keys."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import jev_openrouter


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'runtime').mkdir()
        (self.root / '.env').write_text('OPENROUTER_API_KEY=test-key-value\n', encoding='utf-8')

    def write(self, **values):
        settings = dict({'schema_version': 1, 'enabled': True, 'authorized_projects': ['alpha'],
                         'purposes': ['memory_rank']}, **values)
        (self.root / 'runtime' / 'jev-config.json').write_text(json.dumps(settings), encoding='utf-8')
        return jev_openrouter.load_config(self.root)

    def test_defaults_keep_rubric_and_four_slots(self):
        config = self.write()
        self.assertEqual((config['status'], config['memory_rank_shape'], config['max_in_flight']),
                         ('ready', 'rubric', 4))

    def test_choice_and_slot_count_are_accepted(self):
        config = self.write(memory_rank_shape='choice', max_in_flight=2)
        self.assertEqual((config['memory_rank_shape'], config['max_in_flight']), ('choice', 2))

    def test_invalid_values_make_the_config_unavailable(self):
        for values in ({'memory_rank_shape': 'tournament'}, {'max_in_flight': 0},
                       {'max_in_flight': 17}, {'max_in_flight': True}):
            with self.subTest(values=values):
                self.assertEqual(self.write(**values)['status'], 'unavailable')

    def test_configure_command_writes_both_settings(self):
        import jev_cli
        with patch('builtins.print'):
            code = jev_cli.main(['--root', str(self.root), 'configure', '--project', 'alpha',
                                 '--purpose', 'memory_rank', '--enable', '--rank-shape', 'choice',
                                 '--max-in-flight', '3'])
        self.assertEqual(code, 0)
        saved = json.loads((self.root / 'runtime' / 'jev-config.json').read_text(encoding='utf-8'))
        self.assertEqual((saved['memory_rank_shape'], saved['max_in_flight']), ('choice', 3))


class FlightSlotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'runtime').mkdir()

    def test_slots_are_exclusive_and_saturation_is_reported(self):
        with jev_openrouter._flight_slot(self.root, 2, 0) as first:
            with jev_openrouter._flight_slot(self.root, 2, 0) as second:
                self.assertEqual({first, second}, {0, 1})
                with self.assertRaises(jev_openrouter._Unavailable) as raised:
                    with jev_openrouter._flight_slot(self.root, 2, .1):
                        pass
                self.assertEqual(raised.exception.status, 'saturated')
            with jev_openrouter._flight_slot(self.root, 2, 0) as reused:
                self.assertEqual(reused, 1)

    def test_a_released_slot_admits_a_waiting_request(self):
        acquired = threading.Event()
        release = threading.Event()

        def holder():
            with jev_openrouter._flight_slot(self.root, 1, 0):
                acquired.set()
                release.wait(2)

        thread = threading.Thread(target=holder)
        thread.start()
        acquired.wait(2)
        threading.Timer(.2, release.set).start()
        started = time.monotonic()
        with jev_openrouter._flight_slot(self.root, 1, 2) as slot:
            self.assertEqual(slot, 0)
        self.assertGreaterEqual(time.monotonic() - started, .15)
        thread.join(2)

    def test_evaluate_caps_concurrent_requests_and_saturation_costs_no_daily_slot(self):
        config = {'status': 'ready', 'reason': 'ok', 'key_present': True, 'authorized_projects': ['alpha'],
                  'purposes': ['route'], 'timeout_seconds': .1, 'max_requests_per_day': 100,
                  'min_confidence': .8, 'cache_enabled': False, 'cache_ttl_seconds': 3600,
                  'max_in_flight': 2, 'api_key_env': 'OPENROUTER_API_KEY', 'env_file': '.env'}
        lock, active, peak = threading.Lock(), [0], [0]
        questions = {'pick': {'type': 'choice', 'instructions': 'Which option fits?',
                              'criteria': {'a': 'Option a', 'b': 'Option b'}}}
        body = json.dumps({'model': jev_openrouter.MODEL, 'provider': 'TypeSafe', 'id': 'req-1', 'answers': {
            'pick': {'type': 'choice', 'confidence': .9, 'choice': 'a', 'probabilities': {'a': .9, 'b': .1}}}}).encode()

        release = threading.Event()

        def slow_post(raw, key, timeout):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            release.wait(10)  # hold both slots until every other request has given up
            with lock:
                active[0] -= 1
            return body

        results = []
        with patch('jev_openrouter.load_config', return_value=config), \
                patch('jev_openrouter._key', return_value='key'), \
                patch('jev_openrouter._post', side_effect=slow_post):
            threads = [threading.Thread(target=lambda: results.append(jev_openrouter.evaluate(
                self.root, 'alpha', 'route', {'task': 'demo'}, questions))) for _ in range(5)]
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + 10
            while (sum(1 for result in list(results) if result['status'] == 'saturated') < 3
                   and time.monotonic() < deadline):
                time.sleep(.02)
            release.set()
            for thread in threads:
                thread.join(10)
        statuses = sorted(result['status'] for result in results)
        self.assertLessEqual(peak[0], 2)
        self.assertEqual(statuses.count('ok'), 2)
        self.assertEqual(statuses.count('saturated'), 3)
        self.assertTrue(all(r['provider_calls'] == 0 for r in results if r['status'] == 'saturated'))
        usage = json.loads((self.root / 'runtime' / 'jev-usage.json').read_text(encoding='utf-8'))
        self.assertEqual(usage['attempts'], 2)


if __name__ == '__main__':
    unittest.main()
