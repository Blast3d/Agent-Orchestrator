"""Prepare and record native-composer trials; this command never launches a model."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MODELS = {'astra': 'gpt-6-astra', 'sol': 'gpt-6.1-sol', 'terra': 'gpt-6.1-terra'}
WORKERS = {'aggregation': 'gpt-6.1-sol', 'summary': 'gpt-6.1-terra'}
OWNED = {'aggregation': 'tern/usage/ledger.py', 'allocation': 'tern/dispatch/allocator.py',
         'summary': 'tern/reporting/summary.py'}
ORDER = ('astra-old', 'astra-new', 'sol-new', 'sol-old', 'terra-old', 'terra-new')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write('\n')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def run(command, cwd=None, accepted=(0,)):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            encoding='utf-8', timeout=120)
    if result.returncode not in accepted:
        raise ValueError(f'Command failed ({result.returncode}): {result.stderr[-3000:]} {result.stdout[-3000:]}')
    return result.stdout


def export(commit, destination):
    archive = subprocess.run(['git', 'archive', '--format=zip', commit, 'app'], cwd=ROOT,
                             check=True, capture_output=True, timeout=60).stdout
    destination.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        for item in source.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('Export path escapes destination')
        source.extractall(destination)


def tree_hashes(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def prepare(out, parent):
    out = out.resolve()
    if out.exists():
        raise ValueError('Use a fresh output directory; existing experiments are immutable')
    if not Path(parent, 'startup-context.json').is_file():
        raise ValueError('Provide the exact preparation run with a startup receipt')
    commits = {'old': run(['git', 'rev-parse', '0261045^'], ROOT).strip(),
               'new': run(['git', 'rev-parse', 'HEAD'], ROOT).strip()}
    if run(['git', 'status', '--porcelain', '--', 'app'], ROOT).strip():
        raise ValueError('Application source is dirty; freeze an explicit committed candidate first')
    out.mkdir(parents=True)
    frozen = out / 'frozen'
    frozen.mkdir()
    for mode, commit in commits.items():
        export(commit, frozen / mode)
    shutil.copytree(ROOT / 'benchmarks/memory_control/fixture', frozen / 'fixture',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    common = frozen / 'grader'
    common.mkdir()
    shutil.copy(ROOT / 'benchmarks/memory_control/grader.py', common / 'grader.py')
    shutil.copytree(frozen / 'fixture', common / 'fixture')
    shutil.copy(HERE / 'recall_worker.py', frozen / 'recall_worker.py')
    shutil.copy(ROOT / 'app/assets/worker-guide.md', frozen / 'worker-guide.md')
    seeds = read(ROOT / 'benchmarks/memory_control/memory-seeds.json')
    seeds['memories'].insert(0, {
        'key': 'tern-long-history', 'kind': 'fact', 'title': 'Tern aggregation allocation summary history',
        'content': ('Tern aggregation allocation summary maintenance history. This is background, not a solution. '
                    'Current public contracts are authoritative. ' * 17)[:2800],
        'tags': ['tern', 'aggregation', 'allocation', 'summary', 'canonical', 'measurements'],
        'importance': 1.0})
    save(frozen / 'seeds.json', seeds)
    checks = json.loads(run([sys.executable, '-I', '-B', str(common / 'grader.py'), '--self-test']))
    save(out / 'grader-self-test.json', checks)
    for mode in commits:
        run([sys.executable, '-I', '-B', str(frozen / 'recall_worker.py'),
             '--source', str(frozen / mode), '--state', str(out / 'retrieval-state' / mode),
             '--seeds', str(frozen / 'seeds.json'), '--out', str(frozen / (mode + '-recall.json'))])
    old, new = (read(frozen / (mode + '-recall.json')) for mode in ('old', 'new'))
    if old['seed_ids'] != new['seed_ids'] or old['seed_sha256'] != new['seed_sha256']:
        raise ValueError('Corpus identity differs between Brain versions')
    manifest = {
        'schema_version': 1, 'created_at': now(), 'preparation_run': str(Path(parent).resolve()),
        'preparation_session': os.environ.get('CODEX_THREAD_ID'), 'commits': commits,
        'models': MODELS, 'workers': WORKERS, 'reasoning_effort': 'high', 'order': list(ORDER),
        'repetitions': 1, 'public_feedback_limit': 2, 'context_chars_limit': 3000,
        'model_exception': 'User confirmed Astra means gpt-6-astra. Sol and Terra must be 6.1.',
        'scope': 'Synthetic startup-recall and native lead coordination diagnostic; not a full Brain workflow evaluation.',
        'excluded': ['JEV', 'semantic API retrieval', 'production memory', 'inbox capture', 'MCP workflow benefit'],
        'limitations': ['One paired trial per lead; no statistical model ranking.',
                       'Fresh session identity reduces contamination; tool access is governed by instructions, not an OS sandbox.',
                       'Precomputed startup recall is supplied equally to lead and workers; receipt does not prove use.',
                       'Elapsed time includes human and tool waits; reported token windows may omit trailing usage.'],
        'frozen_hashes': tree_hashes(frozen), 'harness_sha256': sha(__file__),
        'worker_script_sha256': sha(HERE / 'recall_worker.py'),
    }
    save(out / 'manifest.json', manifest)
    (out / 'manifest.sha256').write_text(sha(out / 'manifest.json') + '\n', encoding='utf-8')
    for condition in ORDER:
        directory = out / 'trials' / condition
        directory.mkdir(parents=True)
        (directory / 'START.txt').write_text(start_prompt(out, condition), encoding='utf-8')
    return status(out)


def verified(root):
    root = Path(root).resolve()
    if sha(root / 'manifest.json') != (root / 'manifest.sha256').read_text().strip():
        raise ValueError('Manifest changed after preparation')
    manifest = read(root / 'manifest.json')
    if sha(__file__) != manifest['harness_sha256']:
        raise ValueError('Harness changed: prepare a new suite')
    if tree_hashes(root / 'frozen') != manifest['frozen_hashes']:
        raise ValueError('Frozen fixture, grader, source or recall changed')
    return manifest


def session_log(session_id):
    if not session_id or any(c not in '0123456789abcdef-' for c in session_id):
        raise ValueError('A valid native composer session ID is required')
    base = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions'
    matches = list(base.glob(f'*/*/*/rollout-*{session_id}.jsonl'))
    if len(matches) != 1:
        raise ValueError('Cannot locate exactly one native session receipt')
    return matches[0]


def session_evidence(path):
    meta, turns, usage = None, [], None
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # active log can end with an incomplete last line
            payload = row.get('payload', {})
            if row.get('type') == 'session_meta':
                meta = payload
            elif row.get('type') == 'turn_context':
                turns.append({'turn_id': payload.get('turn_id'), 'model': payload.get('model'),
                              'effort': payload.get('effort'), 'timestamp': row.get('timestamp')})
            elif row.get('type') == 'event_msg' and payload.get('type') == 'token_count':
                usage = (payload.get('info') or {}).get('total_token_usage')
    if not meta or not turns:
        raise ValueError('Receipt lacks native session and turn/model evidence')
    return {'session_id': meta['id'], 'created_at': meta.get('timestamp'),
            'source': meta.get('source'),
            'forked_from_id': meta.get('forked_from_id'), 'turns': turns, 'usage': usage,
            'receipt_path': str(Path(path).resolve()), 'receipt_sha256_at_read': sha(path)}


def validate_identity(evidence, model, effort='high', start_turn=None):
    turns = evidence['turns']
    if start_turn is not None:
        indexes = [i for i, turn in enumerate(turns) if turn['turn_id'] == start_turn]
        if not indexes:
            raise ValueError('Starting turn is missing from receipt')
        turns = turns[indexes[0]:]
    else:
        turns = turns[-1:]
    if any(t['model'] != model or t['effort'] != effort for t in turns):
        raise ValueError(f'Required model/effort: {model}/{effort}; observed {turns[-1]}')


def current_evidence():
    evidence = session_evidence(session_log(os.environ.get('CODEX_THREAD_ID')))
    if evidence['session_id'] != os.environ.get('CODEX_THREAD_ID'):
        raise ValueError('Receipt belongs to a different composer')
    return evidence


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing receipt timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Receipt timestamp must include timezone')
    return parsed


def worker_parent(evidence):
    source = evidence.get('source')
    if not isinstance(source, dict):
        return None
    return ((source.get('subagent') or {}).get('thread_spawn') or {}).get('parent_thread_id')


def start_prompt(root, condition):
    lead, mode = condition.split('-')
    return f'''Run only {condition} of the Brain comparison at {root}.
Required composer model: {MODELS[lead]}, reasoning High. This must be a fresh conversation, not a fork.
Do not inspect preparation history, other trial folders, frozen source, grader or answers.
First run:
python "{HERE / 'suite.py'}" begin --suite "{root}" --condition {condition}
If it refuses, stop and report the reason. Otherwise read only the returned trial's BRIEF.md and follow it.
This user-authorized experimental protocol uses only the declared lead and Sol6.1/Terra6.1 workers.
Do not dispatch other model families, change global orchestration policy, or substitute an older Sol/Terra.
At completion stop; tell Jacob to open the next fresh composer with the next requested model.
'''


def brief(root, condition, manifest):
    directory = root / 'trials' / condition
    return f'''# One native lead trial: {condition}

Repair the synthetic Tern application in `{directory / 'workspace'}` according to its public files.
The frozen startup Brain result is `memory-context.md`. Read it before planning. Validate it against public sources.
Only this trial folder and the commands below are authorized test inputs. Do not read controller/frozen files,
other conditions, global memory, production repos, historical conversations, reference solutions or hidden cases.
This is a cooperative scope boundary, not a filesystem sandbox.

## Fixed assignment

Use exactly two native workers, fork_turns="none", reasoning_effort="high":
- gpt-6.1-sol owns aggregation: `{OWNED['aggregation']}`.
- gpt-6.1-terra owns summary: `{OWNED['summary']}`.
You own allocation `{OWNED['allocation']}`, task decomposition, review and integration.
No substitutions or additional helpers. Give each worker the full worker guide below, the same memory-context.md
content, its exact owned path, public fixture scope and acceptance criteria. Record the actual prompt in this trial.
Each worker must return its CODEX_THREAD_ID, actual receipt path, changed file, verification and limitations.
Keep workers' access inside this trial. Ask them to avoid more workers and global Brain/Codex memory.
Do not call the old memory_control runner: its models differ from this protocol.

## Evidence and feedback

Before work, record your plan and memory IDs you expect to verify in plan.md. Preserve worker outputs and reviews.
Use only this standardized public feedback command, at most {manifest['public_feedback_limit']} times total:
python "{HERE / 'suite.py'}" public --suite "{root}" --condition {condition}
Workers return code and reasoning without running extra tests. Review and integrate within two feedback rounds.
No hidden feedback is returned until finish; finish is irreversible for this condition.
Only the three owned implementation modules may change. Public docs/tests and package files stay frozen.
Write review.json with both worker session IDs, accepted/rejected findings, memory IDs actually checked/useful,
limitations, and any unauthorized input/tool access. Self-reported usefulness is not causal evidence.

Finish with the actual worker IDs:
python "{HERE / 'suite.py'}" finish --suite "{root}" --condition {condition} --sol-session ID --terra-session ID
Do not repair after finish or read another condition. Report quality, elapsed time, recorded usage and next composer.
The first pair is a diagnostic; do not claim a model ranking or overall new-Brain superiority.

## Worker guide (include verbatim in each assignment)

{(root / 'frozen/worker-guide.md').read_text(encoding='utf-8')}
'''


def status(root):
    root = Path(root).resolve()
    manifest = verified(root)
    rows = []
    for name in manifest['order']:
        directory = root / 'trials' / name
        state = 'finished' if (directory / 'result.json').exists() else 'active' if (directory / 'begin.json').exists() else 'planned'
        rows.append({'condition': name, 'status': state, 'model': manifest['models'][name.split('-')[0]]})
    next_row = next((row for row in rows if row['status'] != 'finished'), None)
    return {'suite': str(root), 'conditions': rows, 'next': next_row,
            'next_prompt': str(root / 'trials' / next_row['condition'] / 'START.txt') if next_row else None,
            'scored_trials': sum(row['status'] == 'finished' for row in rows)}


def begin(root, condition):
    root = root.resolve()
    manifest = verified(root)
    info = status(root)
    if not info['next'] or info['next']['condition'] != condition or info['next']['status'] != 'planned':
        raise ValueError('Only the next unstarted condition may begin; active/uncertain work cannot be retried')
    evidence = current_evidence()
    validate_identity(evidence, manifest['models'][condition.split('-')[0]])
    if len({turn['turn_id'] for turn in evidence['turns']}) != 1:
        raise ValueError('Use a fresh composer with this trial as its first request')
    if (evidence['session_id'] == manifest['preparation_session'] or evidence['forked_from_id']
            or evidence.get('source') not in ('vscode', 'cli', 'app')):
        raise ValueError('Use a fresh composer; preparation and forked contexts cannot be contestants')
    if timestamp(evidence['created_at']) < timestamp(manifest['created_at']):
        raise ValueError('Use a composer created after this suite was frozen')
    for previous in (root / 'trials').glob('*/begin.json'):
        if read(previous)['session']['session_id'] == evidence['session_id']:
            raise ValueError('One fresh composer per condition; session reuse contaminates the comparison')
    directory = root / 'trials' / condition
    record = {'started_at': now(), 'start_time_ns': time.time_ns(), 'session': evidence,
              'start_turn_id': evidence['turns'][-1]['turn_id'], 'condition': condition}
    save(directory / 'begin.json', record)  # claim before materialization; interrupted setup remains held
    shutil.copytree(root / 'frozen/fixture', directory / 'workspace')
    recall = read(root / 'frozen' / (condition.split('-')[1] + '-recall.json'))
    selected = next(row for row in recall['searches'] if row['max_chars'] == manifest['context_chars_limit'])
    (directory / 'memory-context.md').write_text(selected['context'], encoding='utf-8')
    save(directory / 'memory-delivery.json', selected)
    (directory / 'BRIEF.md').write_text(brief(root, condition, manifest), encoding='utf-8')
    return {'condition': condition, 'brief': str(directory / 'BRIEF.md'), 'session_model': evidence['turns'][-1]['model']}


def active(root, condition):
    root = root.resolve()
    manifest = verified(root)
    if condition not in manifest['order']:
        raise ValueError('Unknown condition')
    directory = root / 'trials' / condition
    if (directory / 'result.json').exists():
        raise ValueError('Condition is finished and immutable')
    start = read(directory / 'begin.json')
    evidence = current_evidence()
    if evidence['session_id'] != start['session']['session_id']:
        raise ValueError('Only the originating composer may work on this trial')
    validate_identity(evidence, MODELS[condition.split('-')[0]], start_turn=start['start_turn_id'])
    recall = read(root / 'frozen' / (condition.split('-')[1] + '-recall.json'))
    selected = next(row for row in recall['searches'] if row['max_chars'] == manifest['context_chars_limit'])
    if ((directory / 'memory-context.md').read_text(encoding='utf-8') != selected['context']
            or read(directory / 'memory-delivery.json') != selected
            or (directory / 'BRIEF.md').read_text(encoding='utf-8') != brief(root, condition, manifest)):
        raise ValueError('Trial brief or memory input changed after begin')
    return manifest, directory, start, evidence


def grade(root, directory, public=False):
    command = [sys.executable, '-I', '-B', str(root / 'frozen/grader/grader.py'),
               '--workspace', str(directory / 'workspace')]
    if public:
        command.append('--public-only')
    return json.loads(run(command, accepted=(0, 1)))


def public(root, condition):
    manifest, directory, _, _ = active(root, condition)
    count = len(list(directory.glob('public-*.json')))
    if count >= manifest['public_feedback_limit']:
        raise ValueError('Public feedback allowance exhausted')
    result = grade(root, directory, public=True)
    save(directory / f'public-{count + 1}.json', result)
    return result


def usage_delta(before, after):
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    result = {key: after[key] - value for key, value in before.items()
              if type(value) is int and type(after.get(key)) is int}
    return result if result and all(value >= 0 for value in result.values()) else None


def finish(root, condition, sol_session, terra_session):
    manifest, directory, start, evidence = active(root, condition)
    if not (directory / 'plan.md').is_file() or not (directory / 'review.json').is_file():
        raise ValueError('Record plan.md and reviewed worker evidence in review.json before finishing')
    review = read(directory / 'review.json')
    workers = []
    for worker_id, model in ((sol_session, MODELS['sol']), (terra_session, MODELS['terra'])):
        worker = session_evidence(session_log(worker_id))
        validate_identity(worker, model, start_turn=worker['turns'][0]['turn_id'])
        if (worker['session_id'] != worker_id or worker['forked_from_id']
                or timestamp(worker['created_at']) < timestamp(start['started_at'])
                or worker_parent(worker) != evidence['session_id']):
            raise ValueError('Worker must have an independent fresh session within this trial')
        for prior in (root / 'trials').glob('*/result.json'):
            if any(row['session_id'] == worker_id for row in read(prior)['workers']):
                raise ValueError('A worker session cannot be reused across conditions')
        workers.append(worker)
    if workers[0]['session_id'] == workers[1]['session_id']:
        raise ValueError('Worker sessions must be distinct')
    baseline = tree_hashes(root / 'frozen/fixture')
    actual = tree_hashes(directory / 'workspace')
    if set(actual) != set(baseline) or any(actual[p] != baseline[p] for p in baseline if p not in OWNED.values()):
        raise ValueError('Only the three owned implementation modules may change')
    result = {'condition': condition, 'finished_at': now(),
              'elapsed_seconds_including_waits': (time.time_ns() - start['start_time_ns']) / 1e9,
              'lead': evidence, 'workers': workers, 'review': review,
              'lead_reported_usage_delta': usage_delta(start['session']['usage'], evidence['usage']),
              'usage_limit': 'Last reported native counters; trailing tokens may be unreported. Charges unknown.',
              'grade': grade(root, directory), 'output_hashes': actual,
              'memory_delivery_sha256': sha(directory / 'memory-context.md'),
              'public_feedback_count': len(list(directory.glob('public-*.json'))),
              'protocol_compliance': 'Requires transcript review; model receipts alone do not prove scope compliance.'}
    save(directory / 'result.json', result)
    return {'condition': condition, 'passed': result['grade']['passed'], 'total': result['grade']['total'],
            'result': str(directory / 'result.json'), 'next': status(root)['next']}


def report(root):
    root = root.resolve()
    manifest = verified(root)
    comparisons = []
    for lead in MODELS:
        arms = {}
        for mode in ('old', 'new'):
            path = root / 'trials' / f'{lead}-{mode}' / 'result.json'
            if not path.exists():
                arms[mode] = None
                continue
            result = read(path)
            arms[mode] = {key: result[key] for key in
                          ('elapsed_seconds_including_waits', 'lead_reported_usage_delta', 'public_feedback_count')}
            arms[mode]['passed'] = result['grade']['passed']
            arms[mode]['total'] = result['grade']['total']
            arms[mode]['worker_reported_usage'] = [
                {'session_id': row['session_id'], 'model': row['turns'][-1]['model'],
                 'last_reported_counters': row['usage']} for row in result['workers']]
        comparisons.append({'lead': lead, 'model': MODELS[lead], 'arms': arms,
                            'paired_quality_delta': arms['new']['passed'] - arms['old']['passed']
                            if all(arms.values()) else None})
    retrieval = {}
    for mode in ('old', 'new'):
        receipt = read(root / 'frozen' / (mode + '-recall.json'))
        retrieval[mode] = [{'max_chars': row['max_chars'], 'returned_seed_keys': row['returned_seed_keys'],
                            'context_chars': len(row['context']), 'lookup_ms': row.get('elapsed_ms'),
                            'wall_elapsed_ms': row['wall_elapsed_ms']} for row in receipt['searches']]
    return {'status': status(root), 'commits': manifest['commits'], 'retrieval': retrieval,
            'comparisons': comparisons, 'limitations': manifest['limitations'], 'charges': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--out', type=Path, required=True)
    prep.add_argument('--parent', type=Path, required=True)
    for name in ('status', 'report', 'begin', 'public', 'finish'):
        command = sub.add_parser(name)
        command.add_argument('--suite', type=Path, required=True)
        if name not in ('status', 'report'):
            command.add_argument('--condition', choices=ORDER, required=True)
        if name == 'finish':
            command.add_argument('--sol-session', required=True)
            command.add_argument('--terra-session', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.out, args.parent)
        elif args.command == 'status':
            result = status(args.suite)
        elif args.command == 'report':
            result = report(args.suite)
        elif args.command == 'begin':
            result = begin(args.suite, args.condition)
        elif args.command == 'public':
            result = public(args.suite, args.condition)
        else:
            result = finish(args.suite, args.condition, args.sol_session, args.terra_session)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'held', 'reason': str(error)}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
