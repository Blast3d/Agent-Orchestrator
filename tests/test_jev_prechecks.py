import unittest

from app.jev_prechecks import citation_precheck, scan_sensitive, stale_facts


class ScanTests(unittest.TestCase):
    def test_prose_and_names(self):
        r = scan_sensitive("Set api_key later; password is a variable name.")
        self.assertFalse(r["blocked"])
        self.assertEqual(r["categories"], [])

    def test_secrets(self):
        blob = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "Authorization: Bearer abc.def\n"
            "sk-or-v1-abc sk-proj-xyz AKIAIOSFODNN7EXAMPLE\n"
            "api_key=\"secret1\" password='p' access_token=\"tok\""
        )
        r = scan_sensitive(blob)
        self.assertTrue(r["blocked"])
        self.assertIn("privatekeyheaders", r["categories"])
        self.assertIn("bearer", r["categories"])
        self.assertIn("provider_key", r["categories"])
        self.assertIn("aws_access_key", r["categories"])
        self.assertIn("credential_assignment", r["categories"])
        self.assertNotIn(blob, str(r))

    def test_json_and_oversize(self):
        inner = '{"k":"Bearer tok123"}'
        r = scan_sensitive(inner)
        self.assertTrue(r["blocked"])
        self.assertIn("bearer", r["categories"])
        bad = scan_sensitive(object())
        self.assertEqual(bad["categories"], ["input_unverifiable"])
        huge = scan_sensitive("x" * 70000)
        self.assertEqual(huge["categories"], ["input_unverifiable"])

    def test_structured_and_unquoted_credentials_block_without_echo(self):
        for value in ({'api_key': 'synthetic-value'}, 'password=synthetic-value',
                      {'access_token': 'synthetic-value'}, 'api_key="synthetic-value"'):
            result = scan_sensitive(value)
            self.assertTrue(result['blocked'])
            self.assertNotIn('synthetic-value', str(result))
        self.assertFalse(scan_sensitive('{ordinary evidence fragment')['blocked'])

    def test_additional_provider_tokens_and_citation_source_bounds(self):
        for value in ('sk-abcdefghijklmnopqrstuv', 'sk-ant-api03-synthetic-token',
                      {'refresh_token': 'synthetic'}, {'client_secret': 'synthetic'}):
            self.assertTrue(scan_sensitive(value)['blocked'])
        self.assertFalse(scan_sensitive('A variable called sk-short is not a key.')['blocked'])
        self.assertFalse(citation_precheck([{'memory_id': 'a', 'quote': 'x'}],
            [{'id': 'a', 'content': 'x' * 3001}])['valid'])

    def test_cycles_nonfinite_and_nonstring_keys_are_unverifiable(self):
        cyclic = []; cyclic.append(cyclic)
        for value in (cyclic, float('nan'), {1: 'text'}, [0] * 3000):
            self.assertEqual(scan_sensitive(value)['categories'], ['input_unverifiable'])

    def test_case_insensitive_bearer_and_nonstrings_in_credential_fields(self):
        for value in ({'Authorization': 'bearer SYNTHETIC_ONLY_TOKEN'},
                      {'Authorization': 'bEaReR SYNTHETIC_ONLY_TOKEN'},
                      {'password': 123456}, {'api_key': False}, {'access_token': ['value']}):
            self.assertTrue(scan_sensitive(value)['blocked'])
        self.assertFalse(scan_sensitive({'password': None, 'api_key': ''})['blocked'])


class CiteTests(unittest.TestCase):
    def test_exact_quote(self):
        mems = [{"id": "a", "content": "hello world"}]
        ok = citation_precheck([{"memory_id": "a", "quote": "hello"}], mems)
        self.assertTrue(ok["valid"])
        self.assertTrue(ok["checks"][0]["quote_exists"])
        miss = citation_precheck([{"memory_id": "a", "quote": "nope"}], mems)
        self.assertFalse(miss["valid"])
        unk = citation_precheck([{"memory_id": "z", "quote": "hello"}], mems)
        self.assertEqual(unk["checks"][0]["reason"], "unknown_id")
        dup = citation_precheck(
            [{"memory_id": "a", "quote": "h"}, {"memory_id": "a", "quote": "h"}], mems
        )
        self.assertFalse(dup["valid"])
        empty = citation_precheck([{"memory_id": "a", "quote": ""}], mems)
        self.assertFalse(empty["valid"])


class StaleTests(unittest.TestCase):
    def test_dates_versions(self):
        mems = [
            {
                "id": "m1",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "valid_to": "2026-06-01T00:00:00+00:00",
                "version": 1,
            },
            {
                "id": "m2",
                "valid_from": "not-a-date",
                "valid_to": "also-bad",
                "version": 2,
            },
        ]
        rows = stale_facts(mems, "2026-09-25T00:00:00Z", {"m1": 1})
        self.assertEqual(rows[0]["expired"], True)
        self.assertEqual(rows[0]["version_matches"], True)
        self.assertIsInstance(rows[0]["age_days"], int)
        self.assertIsNone(rows[1]["expired"])
        self.assertIsNone(rows[1]["age_days"])
        self.assertIsNone(rows[1]["version_matches"])


if __name__ == "__main__":
    unittest.main()
