import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.skill_catalog import catalog, load_selected


class TestSkillCatalog(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.addCleanup(patch.stopall)
        patch('app.skill_catalog.Path.home', return_value=self.home).start()
        self.skill('core/alpha', 'alpha', 'Retrieve the relevant project lessons.')
        self.skill('project/beta', 'beta', 'Manage voice requests.')
        self.skill('dormant/extra', 'extra', 'Build a scoped research brief.')
        self.manifest = {
            'schema_version': 1,
            'roots': {
                'core': '${orchestrator}/fixtures/core',
                'project': '${orchestrator}/fixtures/project',
                'dormant': '${orchestrator}/fixtures/dormant',
            },
            'packs': [
                self.pack('core', 'core', 'alpha'),
                self.pack('openwhispr', 'project', 'beta'),
                self.pack('research', 'dormant', 'extra', optional=True),
            ],
            'projects': {
                'agent-orchestrator': ['core', 'research'],
                'openwhispr': ['core', 'openwhispr'],
            },
        }
        self.save()

    def skill(self, relative, name, description, body='Follow the current task scope.'):
        path = self.root / 'fixtures' / relative / 'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n', encoding='utf-8')
        return path

    def pack(self, identifier, root, skill, optional=False):
        return {
            'id': identifier, 'label': identifier.title(),
            'description': 'Reviewed skills for ' + identifier,
            'optional': optional,
            'skills': [{'id': skill, 'root': root, 'path': skill + '/SKILL.md',
                        'availability': 'dormant' if optional else 'installed'}],
        }

    def save(self):
        config = self.root / 'config'
        config.mkdir(exist_ok=True)
        (config / 'skill-packs.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def selected(self, ids=('alpha',)):
        result = catalog(self.root, 'agent-orchestrator')
        hashes = {skill['id']: skill['sha256'] for pack in result['packs'] for skill in pack['skills'] if skill['id'] in ids}
        return result, hashes

    def test_catalog_scopes_exact_packs_and_reads_actual_metadata(self):
        result = catalog(self.root, 'openwhispr')
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual([pack['id'] for pack in result['packs']], ['core', 'openwhispr'])
        alpha = result['packs'][0]['skills'][0]
        self.assertEqual(alpha['description'], 'Retrieve the relevant project lessons.')
        self.assertEqual(alpha['sha256'], hashlib.sha256(Path(alpha['path']).read_bytes()).hexdigest())
        self.assertEqual(alpha['availability'], 'installed')
        self.assertEqual(result['warnings'], [])

    def test_optional_pack_is_available_only_for_explicit_project_allowlist(self):
        result = catalog(self.root, 'agent-orchestrator')
        self.assertTrue(result['packs'][1]['optional'])
        self.assertEqual(result['packs'][1]['skills'][0]['availability'], 'dormant')
        with self.assertRaises(ValueError):
            catalog(self.root, 'unknown')

    def test_load_full_instructions_binds_content_and_descriptors(self):
        result, hashes = self.selected(('alpha', 'extra'))
        loaded = load_selected(self.root, 'agent-orchestrator', ['alpha', 'extra'], result['manifest_sha256'], hashes)
        self.assertIn('Follow the current task scope.', loaded['text'])
        self.assertIn('# alpha', loaded['text'])
        self.assertEqual([skill['id'] for skill in loaded['skills']], ['alpha', 'extra'])
        self.assertEqual(loaded['bytes'], len(loaded['text'].encode('utf-8')))
        self.assertEqual(loaded['sha256'], hashlib.sha256(loaded['text'].encode('utf-8')).hexdigest())
        self.assertIn('supplied-text workers have no tools', loaded['text'])
        self.assertEqual(loaded['manifest_sha256'], result['manifest_sha256'])

    def test_skill_edit_invalidates_catalog_and_selection(self):
        result, hashes = self.selected()
        self.skill('core/alpha', 'alpha', 'Changed instructions.')
        self.assertNotEqual(catalog(self.root, 'agent-orchestrator')['manifest_sha256'], result['manifest_sha256'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            load_selected(self.root, 'agent-orchestrator', ['alpha'], result['manifest_sha256'], hashes)

    def test_manifest_edit_and_expected_skill_hash_are_checked(self):
        result, hashes = self.selected()
        with self.assertRaisesRegex(ValueError, 'hash'):
            load_selected(self.root, 'agent-orchestrator', ['alpha'], result['manifest_sha256'], {'alpha': '0' * 64})
        self.manifest['packs'][0]['description'] += ' changed'
        self.save()
        with self.assertRaisesRegex(ValueError, 'stale'):
            load_selected(self.root, 'agent-orchestrator', ['alpha'], result['manifest_sha256'], hashes)

    def test_missing_root_and_file_are_warnings_and_affect_fingerprint(self):
        before = catalog(self.root, 'agent-orchestrator')
        shutil.rmtree(self.root / 'fixtures' / 'dormant')
        missing = catalog(self.root, 'agent-orchestrator')
        self.assertEqual(missing['packs'][1]['skills'], [])
        self.assertTrue(any('missing root' in warning.lower() for warning in missing['warnings']))
        self.assertNotEqual(before['manifest_sha256'], missing['manifest_sha256'])
        (self.root / 'fixtures' / 'dormant').mkdir()
        missing_file = catalog(self.root, 'agent-orchestrator')
        self.assertTrue(any('missing skill' in warning.lower() for warning in missing_file['warnings']))

    def test_out_of_scope_selection_duplicates_and_limits_fail(self):
        result, hashes = self.selected()
        for ids in ([], ['alpha', 'alpha'], ['beta'], ['alpha'] * 4):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                load_selected(self.root, 'agent-orchestrator', ids, result['manifest_sha256'], hashes)
        with self.assertRaises(ValueError):
            load_selected(self.root, 'agent-orchestrator', ['alpha'], result['manifest_sha256'], {})

    def test_oversized_context_rejected_without_truncation(self):
        self.skill('core/alpha', 'alpha', 'Big instructions.', body='x' * (24 * 1024))
        result, hashes = self.selected()
        with self.assertRaisesRegex(ValueError, '24 KiB'):
            load_selected(self.root, 'agent-orchestrator', ['alpha'], result['manifest_sha256'], hashes)

    def test_oversized_skill_and_manifest_are_rejected(self):
        self.skill('core/alpha', 'alpha', 'Big instructions.', body='x' * (64 * 1024))
        with self.assertRaisesRegex(ValueError, 'size'):
            catalog(self.root, 'agent-orchestrator')
        (self.root / 'config' / 'skill-packs.json').write_text(' ' * (64 * 1024 + 1), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'size'):
            catalog(self.root, 'agent-orchestrator')

    def test_duplicate_pack_and_skill_ids_reserved_ids_rejected(self):
        mutations = [
            lambda: self.manifest['packs'].append(self.pack('core', 'project', 'beta')),
            lambda: self.manifest['packs'][1]['skills'][0].update(id='alpha'),
            lambda: self.manifest['packs'][0]['skills'][0].update(id='none'),
            lambda: self.manifest['packs'][0]['skills'][0].update(id='unsafe id'),
        ]
        for mutate in mutations:
            original = json.loads(json.dumps(self.manifest))
            mutate()
            self.save()
            with self.assertRaises(ValueError):
                catalog(self.root, 'agent-orchestrator')
            self.manifest = original

    def test_traversal_absolute_urls_and_credential_files_rejected(self):
        for path in ('../alpha/SKILL.md', '/tmp/SKILL.md', 'C:/other/SKILL.md',
                     '//host/share/SKILL.md', 'https://example.test/SKILL.md',
                     'alpha/../../secret/SKILL.md', 'alpha/.env', 'alpha/SKILL.md:stream'):
            with self.subTest(path=path):
                self.manifest['packs'][0]['skills'][0]['path'] = path
                self.save()
                with self.assertRaises(ValueError):
                    catalog(self.root, 'agent-orchestrator')

    def test_root_traversal_unknown_alias_and_absolute_roots_rejected(self):
        for value in ('${orchestrator}/../outside', '${unknown}/skills', 'https://example.test', '/tmp', '~/../outside'):
            with self.subTest(root=value):
                self.manifest['roots']['core'] = value
                self.save()
                with self.assertRaises(ValueError):
                    catalog(self.root, 'agent-orchestrator')

    def test_symlink_escape_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        target = outside / 'SKILL.md'
        target.write_text('Secret outside root', encoding='utf-8')
        link = self.root / 'fixtures' / 'core' / 'escape'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if not hasattr(subprocess, 'CREATE_NO_WINDOW'):
                self.skipTest('Symlinks unavailable: ' + str(exc))
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                 'New-Item -ItemType Junction -Path ' + quote(link) + ' -Value ' + quote(outside) + ' -ErrorAction Stop | Out-Null'],
                capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.manifest['packs'][0]['skills'][0]['path'] = 'escape/SKILL.md'
        self.save()
        with self.assertRaisesRegex(ValueError, 'escape'):
            catalog(self.root, 'agent-orchestrator')

    def test_disabled_installed_entry_is_hidden_but_dormant_copy_is_available(self):
        settings = self.home / '.codex' / 'config.toml'
        settings.parent.mkdir()
        alpha_path = (self.root / 'fixtures' / 'core' / 'alpha' / 'SKILL.md').as_posix()
        settings.write_text('private_token = "DO_NOT_INCLUDE"\n[[skills.config]]\npath = ' + json.dumps(alpha_path) + '\nenabled = false\n', encoding='utf-8')
        result = catalog(self.root, 'agent-orchestrator')
        self.assertEqual(result['packs'][0]['skills'], [])
        self.assertEqual(result['packs'][1]['skills'][0]['id'], 'extra')
        self.assertNotIn('DO_NOT_INCLUDE', json.dumps(result))

    def test_scoped_hash_tracks_dependency_identity_not_unrelated_file_contents(self):
        before = catalog(self.root, 'agent-orchestrator')['manifest_sha256']
        self.skill('project/beta', 'beta', 'Unrelated project changed.')
        self.assertEqual(catalog(self.root, 'agent-orchestrator')['manifest_sha256'], before)
        self.skill('dormant/extra', 'extra', 'Allowed optional instructions changed.')
        self.assertNotEqual(catalog(self.root, 'agent-orchestrator')['manifest_sha256'], before)

    def test_multiline_metadata_and_non_text_input_validation(self):
        path = self.root / 'fixtures' / 'core' / 'alpha' / 'SKILL.md'
        path.write_text('---\nname: "alpha"\ndescription: >\n  First paragraph\n  second line.\n---\n# Full instructions\n', encoding='utf-8')
        self.assertEqual(catalog(self.root, 'agent-orchestrator')['packs'][0]['skills'][0]['description'], 'First paragraph second line.')
        for value in (None, 7, '', 'project id'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                catalog(self.root, value)

    def test_malformed_manifest_types_fail_as_validation_errors(self):
        original = json.loads(json.dumps(self.manifest))
        for field, value in (('schema_version', True), ('projects', {'openwhispr': [{}]}),
                             ('roots', []), ('packs', None)):
            self.manifest = dict(original, **{field: value})
            self.save()
            with self.subTest(field=field), self.assertRaises(ValueError):
                catalog(self.root, 'agent-orchestrator')

    def test_empty_pack_explicit_root_reports_missing_location(self):
        self.manifest['roots']['civil3d'] = '${civil3d}/.codex/skills'
        self.manifest['packs'].append({'id': 'civil3d', 'label': 'Civil3D',
                                      'description': 'Maintained project skills.',
                                      'roots': ['civil3d'], 'skills': []})
        self.manifest['projects']['civil3d'] = ['core', 'civil3d']
        self.save()
        result = catalog(self.root, 'civil3d')
        self.assertEqual(result['packs'][1]['skills'], [])
        self.assertTrue(any('Missing root' in value for value in result['warnings']))


if __name__ == '__main__':
    unittest.main()
