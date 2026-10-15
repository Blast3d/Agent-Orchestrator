"""Offline ranking/packing acceptance cases; vectors are synthetic, not model evidence."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def compare(app_dir):
    sys.path.insert(0, str(app_dir))
    from brain_store import BrainStore
    from brain_semantic import fingerprint

    cases = []
    for case in ('rank_agreement', 'exact_path', 'tight_context', 'keyword_fallback'):
        with tempfile.TemporaryDirectory(prefix='brain-hybrid-') as directory:
            brain = BrainStore(Path(directory))

            def add(title, content, **extra):
                payload = dict(project_id='synthetic', kind='fact', title=title, content=content,
                    source={'type': 'user', 'note': 'Synthetic offline ranking benchmark'})
                payload.update(extra)
                item = brain.propose(payload)
                return brain.approve(item['id'], 'Tester', 'Checked this synthetic benchmark fixture.')

            options = dict(strategy='semantic')
            pairs = []
            if case == 'tight_context':
                add('Retry', 'Retry details.', kind='episode', episode={
                    'problem': 'p' * 600, 'action': 'a' * 600, 'outcome': 'o' * 600})
                expected = add('Retry alternative', 'Check the saved receipt first.')
                query = 'retry'
                options.update(strategy='keyword', limit=1, max_chars=950)
            elif case == 'exact_path':
                expected = add('Production config', 'Use app/production/config.json.')
                other = add('Development', 'Use a different settings file.')
                query = 'app/production/config.json'
                pairs = [(other, .99), (expected, .66)]
            else:
                expected = add('Retry procedure', 'Retry with exponential backoff.')
                query = 'retry zebra'
                if case == 'rank_agreement':
                    other = add('Other label', 'No overlapping query terms.')
                    pairs = [(other, .99), (expected, .70)]
            with brain._connection() as con:
                candidates = [dict(id=item['id'], score=score, fingerprint=fingerprint(
                    con.execute('SELECT * FROM memories WHERE id=?', (item['id'],)).fetchone()))
                    for item, score in pairs]
            reply = dict(status='ok' if pairs else 'unavailable', candidates=candidates,
                         provider_calls=0, indexed_count=len(candidates), model='synthetic')
            with patch('brain_semantic.semantic_candidates', return_value=reply):
                result = brain.search(query, 'synthetic', jev=False, record_trace=False, **options)
            actual = result['results'][0]['id'] if result['results'] else None
            cases.append(dict(case=case, expected_first=actual == expected['id'],
                context_within_bound=result['context_chars'] <= options.get('max_chars', 8000),
                context_chars=result['context_chars'], elapsed_ms=result['elapsed_ms']))
    return dict(cases=cases, passed=sum(row['expected_first'] for row in cases), total=len(cases),
        provider_calls=0, limits=[
            'Hand-built regression cases with mocked vectors; not a representative retrieval-quality study.',
            'No live provider, embedding quality, cost saving or task speedup is measured.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'app')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.app_dir.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), passed=result['passed'], total=result['total'], provider_calls=0)))
