"""Run identity stays unique while reviewed memory uses an explicit enduring scope."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from init_run import create_run, main
from local_services import memory_projects


class ProjectIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.parent = self.root / '.orchestration'

    def config(self, value):
        self.parent.mkdir(exist_ok=True)
        path = self.parent / 'project.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def create(self, **kwargs):
        return create_run(self.root, 'continuity-test', 'Verify project continuity', **kwargs)

    def test_workspace_default_shared_by_distinct_runs_preserves_history(self):
        config = self.config({'schema_version': 1, 'project_id': 'agent-orchestrator'})
        config_before = config.read_bytes()
        first, record = self.create()
        before = {p.relative_to(first): p.read_bytes() for p in first.rglob('*') if p.is_file()}
        second, current = self.create()
        self.assertNotEqual(first, second)
        self.assertEqual(record['project_id'], current['project_id'])
        self.assertEqual(memory_projects(self.root, current['run_id'], current), ['agent-orchestrator'])
        self.assertEqual(config.read_bytes(), config_before)
        self.assertEqual({p.relative_to(first): p.read_bytes() for p in first.rglob('*') if p.is_file()}, before)
        self.assertFalse((self.root / 'runtime/brain').exists())

    def test_explicit_override_does_not_change_default_or_prior_run(self):
        config = self.config({'schema_version': 1, 'project_id': 'workspace-project'})
        first, _ = self.create()
        before = (first / 'run.json').read_bytes()
        _, record = self.create(project_id='explicit-project')
        self.assertEqual(record['project_id'], 'explicit-project')
        self.assertEqual(json.loads(config.read_text())['project_id'], 'workspace-project')
        self.assertEqual((first / 'run.json').read_bytes(), before)

    def test_explicit_override_does_not_consult_malformed_default(self):
        self.config({'schema_version': 99, 'project_id': 'unrelated-project'})
        _, record = self.create(project_id='chosen-project')
        self.assertEqual(record['project_id'], 'chosen-project')

    def test_explicit_scope_without_config_does_not_create_default(self):
        _, record = self.create(project_id='chosen-project')
        self.assertEqual(record['project_id'], 'chosen-project')
        self.assertFalse((self.parent / 'project.json').exists())

    def test_unconfigured_workspace_keeps_legacy_run_scope(self):
        _, record = self.create()
        self.assertNotIn('project_id', record)
        self.assertEqual(memory_projects(self.root, record['run_id'], record), [record['run_id']])

    def test_uses_brain_scope_rules_and_normalization(self):
        _, record = self.create(project_id='  Client:2026/Project A  ')
        self.assertEqual(record['project_id'], 'Client:2026/Project A')
        _, record = self.create(project_id='p' * 100)
        self.assertEqual(len(record['project_id']), 100)

    def test_invalid_explicit_scopes_fail_before_any_run_writes(self):
        for value in ('', '   ', 'p' * 101, 'bad?scope', '../not-project', 12, ['project']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.create(project_id=value)
            self.assertFalse(self.parent.exists())

    def test_invalid_config_schema_or_project_preserves_only_config(self):
        for value in ({}, [], None, {'schema_version': True, 'project_id': 'x'},
                      {'schema_version': 2, 'project_id': 'x'}, {'schema_version': 1},
                      {'schema_version': 1, 'project_id': None},
                      {'schema_version': 1, 'project_id': 'p' * 101}):
            with self.subTest(value=value):
                config = self.config(value)
                before = config.read_bytes()
                with self.assertRaises(ValueError):
                    self.create()
                self.assertEqual(list(self.parent.iterdir()), [config])
                self.assertEqual(config.read_bytes(), before)

    def test_unreadable_json_and_oversized_config_create_no_run(self):
        config = self.config({'schema_version': 1, 'project_id': 'valid'})
        for raw in (b'{not json', b'\xff\xfe', b' ' * 8193):
            with self.subTest(length=len(raw)):
                config.write_bytes(raw)
                with self.assertRaises(ValueError):
                    self.create()
                self.assertEqual(list(self.parent.iterdir()), [config])

    def test_config_directory_is_rejected(self):
        (self.parent / 'project.json').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.create()
        self.assertEqual(len(list(self.parent.iterdir())), 1)

    def test_reparse_config_is_rejected_before_open(self):
        config = self.config({'schema_version': 1, 'project_id': 'outside-project'})
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            if path == config:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', lstat), self.assertRaisesRegex(ValueError, 'reparse'):
            self.create()
        self.assertEqual(list(self.parent.iterdir()), [config])

    def test_reparse_parent_is_rejected_even_with_explicit_project(self):
        self.parent.mkdir()
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            if path == self.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', lstat), self.assertRaisesRegex(ValueError, 'reparse'):
            self.create(project_id='explicit-project')
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_config_identity_change_is_rejected_before_run_writes(self):
        config = self.config({'schema_version': 1, 'project_id': 'original-project'})
        with patch('init_run.os.path.samestat', return_value=False), self.assertRaisesRegex(ValueError, 'changed'):
            self.create()
        self.assertEqual(list(self.parent.iterdir()), [config])

    def test_cli_override_and_native_lineage_coexist(self):
        self.config({'schema_version': 1, 'project_id': 'workspace-project'})
        session = '1234abcd-1234-4567-8910-123456789abc'
        with patch.dict(os.environ, {'CODEX_THREAD_ID': session}), patch.object(sys, 'argv',
                ['init_run.py', '--workspace', str(self.root), '--name', 'cli-project', '--objective',
                 'Verify CLI identity', '--project', 'explicit-project']), patch('builtins.print'):
            main()
        manifest = next(self.parent.glob('*/run.json'))
        record = json.loads(manifest.read_text())
        self.assertEqual(record['project_id'], 'explicit-project')
        self.assertEqual(record['native_parent_session_id'], session)
        self.assertFalse((manifest.parent / 'viewer-session.json').exists())


if __name__ == '__main__':
    unittest.main()
