"""Offline brief checks reject empty promises without judging model answers."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from brief_check import inspect_brief, inspect_file, main


COMPLETE = 'Goal: Explain the sample\nInputs: The supplied example\nOutput: One paragraph\nChecks: Address the example clearly'
FIELDS = {'objective': 'Explain the sample', 'inputs': ['The supplied example', 'Audience: beginners'],
          'output': 'One paragraph', 'acceptance': ['Address the example', 'Use plain language']}


class BriefCheckTests(unittest.TestCase):
    def test_markdown_aliases_and_multiline_inputs(self):
        text = ('# Review brief\n## PURPOSE:\nExplain this sample\n'
                '## Sources\n- supplied sample\n- audience notes\n'
                '### Expected Output ###\nOne paragraph\n'
                '## Definition of Done\n- Address the sample\n- Use plain language')
        report = inspect_brief(text)
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['format'], 'markdown')
        self.assertIn('audience notes', report['sections']['inputs'])
        self.assertEqual(report['character_count'], len(text))

    def test_colon_fields_and_case(self):
        report = inspect_brief(COMPLETE.replace('Goal:', 'pUrPoSe :').replace('Checks:', 'Success criteria:'))
        self.assertTrue(report['ok'], report)
        self.assertTrue(report['warnings'])

    def test_bold_colon_labels(self):
        report = inspect_brief('**Goal:** Explain it\n**Inputs**: Supplied sample\n'
                               '**Output:** A paragraph\n**Checks:** Use the sample')
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['sections']['objective'], 'Explain it')

    def test_json_lists_and_aliases(self):
        fields = dict(FIELDS)
        fields['goal'] = fields.pop('objective')
        report = inspect_brief(json.dumps(fields))
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['format'], 'json')
        self.assertIn('Audience: beginners', report['sections']['inputs'])

    def test_entire_json_fence_and_bom(self):
        for fence in ('```', '~~~~'):
            with self.subTest(fence=fence):
                report = inspect_brief('\ufeff\n' + fence + 'JSON\n' + json.dumps(FIELDS) + '\n' + fence)
                self.assertTrue(report['ok'], report)
                self.assertEqual(report['format'], 'json')

    def test_json_fence_must_be_complete_and_exclusive(self):
        for text in ('```json\n' + json.dumps(FIELDS),
                     '```json\n' + json.dumps(FIELDS) + '\n```\nPostscript'):
            with self.subTest(text=text[:20]):
                report = inspect_brief(text)
                self.assertFalse(report['ok'])
                self.assertTrue(report['errors'])

    def test_missing_and_placeholder_fields(self):
        for placeholder in ('', 'TODO', 'tbd.', '- [ ] TODO\n- TBD', '**TODO**'):
            with self.subTest(placeholder=placeholder):
                fields = dict(FIELDS, inputs=placeholder)
                report = inspect_brief(json.dumps(fields))
                self.assertFalse(report['ok'])
                self.assertIn('Inputs', report['missing'])
        report = inspect_brief('Goal: Explain this example\nInputs: TODO')
        self.assertEqual(report['missing'], ['Inputs', 'Required output', 'Acceptance checks'])

    def test_fenced_fake_sections_do_not_count(self):
        report = inspect_brief('An example only:\n```markdown\n' + COMPLETE + '\n```')
        self.assertFalse(report['ok'])
        self.assertEqual(report['format'], 'unstructured')
        self.assertEqual(len(report['missing']), 4)
        report = inspect_brief('Goal: Explain the sample\n~~~\nInputs: secret\nOutput: text\nChecks: pass\n~~~')
        self.assertFalse(report['ok'])
        self.assertEqual(report['missing'], ['Inputs', 'Required output', 'Acceptance checks'])

    def test_code_body_is_allowed_without_discovering_fake_headings(self):
        report = inspect_brief('Goal: Explain a snippet\nInputs:\n```python\nprint("hi")\n````\n'
                               'Output: A paragraph\nChecks: Explain the shown behavior')
        self.assertTrue(report['ok'], report)
        self.assertIn('print("hi")', report['sections']['inputs'])

    def test_quoted_prose_and_embedded_words_are_not_sections(self):
        for text in ('A Goal: explain it. Inputs: sample. Output: one paragraph. Checks: good.',
                     '> Goal: sample\n> Inputs: sample\n> Output: sample\n> Checks: sample',
                     '"Goal: sample"\n"Inputs: sample"\n"Output: sample"\n"Checks: sample"'):
            with self.subTest(text=text[:15]):
                report = inspect_brief(text)
                self.assertFalse(report['ok'])
                self.assertEqual(report['format'], 'unstructured')

    def test_unknown_heading_does_not_fill_empty_required_section(self):
        report = inspect_brief('## Goal\n## Notes\nThis is not the objective.\n'
                               'Inputs: sample\nOutput: paragraph\nChecks: explain sample')
        self.assertIn('Objective', report['missing'])

    def test_malformed_json_is_not_reinterpreted_as_markdown(self):
        for text in ('{ broken\n' + COMPLETE, '["Goal: text"]', '{"objective": NaN}'):
            with self.subTest(text=text[:20]):
                report = inspect_brief(text)
                self.assertFalse(report['ok'])
                self.assertEqual(report['format'], 'json')
                self.assertTrue(report['errors'])

    def test_wrong_json_field_types(self):
        for value in (123, True, None, {}, ['Some context', 3], ['Some context', '   ']):
            with self.subTest(value=value):
                report = inspect_brief(json.dumps(dict(FIELDS, inputs=value)))
                self.assertFalse(report['ok'])
                self.assertTrue(report['errors'])
                self.assertIn('Inputs', report['missing'])
        self.assertIn('Inputs', inspect_brief(json.dumps(dict(FIELDS, inputs=[])))['missing'])

    def test_duplicate_fields_and_aliases_do_not_silently_overwrite(self):
        duplicate = json.dumps(FIELDS)[:-1] + ', "objective": "Replacement"}'
        self.assertTrue(inspect_brief(duplicate)['errors'])
        alias = dict(FIELDS, goal='A conflicting goal')
        self.assertTrue(inspect_brief(json.dumps(alias))['errors'])

    def test_only_local_chat_has_a_length_limit(self):
        text = COMPLETE + '\n' + 'x' * 10100
        for worker in ('claude', 'grok', 'gemini'):
            with self.subTest(worker=worker):
                self.assertTrue(inspect_brief(text, worker)['ok'])
        local = inspect_brief(text, 'local-chat')
        self.assertFalse(local['ok'])
        self.assertTrue(any('10,000' in error for error in local['errors']))
        exact = COMPLETE + 'x' * (10000 - len(COMPLETE))
        self.assertTrue(inspect_brief(exact, 'local-chat')['ok'])
        self.assertFalse(inspect_brief(exact + 'x', 'local-chat')['ok'])

    def test_invalid_worker_or_nontext_input_has_feedback(self):
        self.assertFalse(inspect_brief(COMPLETE, 'invented')['ok'])
        self.assertFalse(inspect_brief(None)['ok'])

    def test_file_errors_and_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'brief.txt'
            missing = inspect_file(path)
            self.assertFalse(missing['ok'])
            self.assertTrue(missing['errors'])
            path.write_bytes(b'\xffPRIVATE_CONTENT\xfe')
            invalid = inspect_file(path)
            self.assertTrue(any('UTF-8' in error for error in invalid['errors']))
            self.assertNotIn('PRIVATE_CONTENT', json.dumps(invalid))
            path.write_bytes(b'')
            self.assertTrue(any('empty' in error for error in inspect_file(path)['errors']))
            path.write_text(COMPLETE, encoding='utf-8-sig')
            self.assertTrue(inspect_file(path)['ok'])

    def test_cli_writes_exclusive_report_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as temp:
            brief = Path(temp) / 'brief.txt'
            output = Path(temp) / 'report.json'
            brief.write_text(COMPLETE, encoding='utf-8')
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main([str(brief), '--json', '--output', str(output)]), 0)
            self.assertTrue(json.loads(stdout.getvalue())['ok'])
            saved = output.read_bytes()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main([str(brief), '--json', '--output', str(output)]), 2)
            self.assertEqual(output.read_bytes(), saved)
            self.assertIn('already exists', json.loads(stdout.getvalue())['errors'][-1])

    def test_cli_validation_failure_and_same_input_output(self):
        with tempfile.TemporaryDirectory() as temp:
            brief = Path(temp) / 'brief.txt'
            brief.write_text('Goal: TODO', encoding='utf-8')
            before = brief.read_bytes()
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main([str(brief), '--output', str(brief)]), 2)
            self.assertEqual(brief.read_bytes(), before)
            self.assertIn('Add useful content', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
