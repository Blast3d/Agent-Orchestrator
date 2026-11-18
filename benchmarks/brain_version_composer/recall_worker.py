"""Run frozen synthetic Brain recall in one isolated, network-blocked process."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import sysconfig
import time
from types import SimpleNamespace
import uuid


PROJECT = 'brain-version-composer-synthetic'
USER = 'synthetic'
SEED_TIME = '2026-01-01T00:00:00.000000+00:00'
QUERY = 'Tern aggregation allocation summary canonical measurements'


def block_network():
    attempts = []

    def audit(event, arguments):
        if event.startswith('socket.'):
            attempts.append(event)
            raise RuntimeError('Network access is disabled in the recall worker')
        if event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
            raise RuntimeError('Child processes are disabled in the recall worker')

    sys.addaudithook(audit)
    return attempts


def load_store(source, state):
    app = source / 'app'
    if not (app / 'brain_store.py').is_file():
        raise ValueError('--source must contain app/brain_store.py')
    # Drop cwd, PYTHONPATH and site-packages: a missing export dependency must
    # fail rather than silently import a production checkout or installed copy.
    standard = {Path(sysconfig.get_path(name)).resolve()
                for name in ('stdlib', 'platstdlib')}
    standard.update((Path(sys.base_prefix) / 'DLLs',
                     Path(sys.base_prefix) / ('python%d%d.zip' % sys.version_info[:2])))
    sys.path[:] = [str(app)] + [str(path) for path in sorted(standard)]
    sys.dont_write_bytecode = True
    paths = importlib.import_module('paths')
    if Path(paths.__file__).resolve() != app / 'paths.py':
        raise RuntimeError('paths was not imported from the requested export')
    paths.ROOT = state
    paths.APP = app
    paths.CONFIG = state / 'config'
    paths.STATE = state / 'runtime'
    paths.WORKSPACES = paths.STATE / 'workspaces'
    paths.TASKS = state / 'runs/tasks'
    paths.VENDOR = state / 'vendor/quota'
    brain = importlib.import_module('brain_store')
    if Path(brain.__file__).resolve() != app / 'brain_store.py':
        raise RuntimeError('BrainStore was not imported from the requested export')
    return brain, brain.BrainStore(root=state)


def seed_store(brain, store, rows):
    ids = {}
    original_now, original_uuid = brain.now, brain.uuid
    try:
        brain.now = lambda: SEED_TIME
        for row in rows:
            key = row['key']
            identifier = uuid.uuid5(uuid.NAMESPACE_URL,
                                    'brain-version-composer-synthetic/' + key)
            # Patch this module's UUID dependency only. Storage reservation IDs
            # remain real UUIDs and cannot consume the seed identity sequence.
            brain.uuid = SimpleNamespace(uuid4=lambda: identifier)
            payload = {name: row[name] for name in
                       ('kind', 'title', 'content', 'tags', 'importance', 'episode')
                       if name in row}
            payload.update(project_id=PROJECT, user_id=USER,
                           valid_from=SEED_TIME, valid_to=None,
                           source={'type': 'user',
                                   'note': 'Frozen synthetic benchmark seed: ' + key})
            proposed = store.propose(payload)
            if proposed.get('duplicate') or proposed['id'] != identifier.hex:
                raise ValueError('Frozen seed did not produce its expected unique ID: ' + key)
            store.approve(proposed['id'], reviewer='synthetic-controller',
                          note='Reviewed frozen synthetic benchmark material before recall.')
            ids[proposed['id']] = key
    finally:
        brain.now, brain.uuid = original_now, original_uuid
    return ids


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path,
                        help='Git-exported application source tree')
    parser.add_argument('--state', required=True, type=Path,
                        help='Absent or empty isolated Brain root; never reused')
    parser.add_argument('--seeds', required=True, type=Path,
                        help='Frozen memory-seeds.json object or memories list')
    parser.add_argument('--out', required=True, type=Path,
                        help='New JSON receipt path')
    args = parser.parse_args(argv)
    source, state = args.source.resolve(strict=True), args.state.resolve()
    out = args.out.resolve()
    if state.is_relative_to(source) or source.is_relative_to(state):
        raise ValueError('Source and state must be separate, non-nested directories')
    if out.is_relative_to(source):
        raise ValueError('Output must stay outside the source export')
    if out.exists():
        raise FileExistsError('Output already exists; preserve the earlier receipt')
    if state.exists() and (not state.is_dir() or any(state.iterdir())):
        raise FileExistsError('State must be absent or empty; an existing Brain cannot be reused')
    seed_bytes = args.seeds.read_bytes()
    template = json.loads(seed_bytes)
    rows = template.get('memories') if isinstance(template, dict) else template
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError('Seeds must be a nonempty memories list')
    keys = [row.get('key') for row in rows]
    if any(not isinstance(key, str) or not key.strip() or len(key) > 160 for key in keys):
        raise ValueError('Each seed requires a nonempty key of at most 160 characters')
    if len(set(keys)) != len(keys):
        raise ValueError('Seed keys must be unique')
    state.mkdir(parents=True, exist_ok=True)
    network_attempts = block_network()
    started = time.perf_counter()
    brain, store = load_store(source, state)
    ids = seed_store(brain, store, rows)
    searches = []
    for budget in (3000, 8000):
        search_started = time.perf_counter()
        found = store.search(QUERY, PROJECT, user_id=USER, strategy='keyword',
                             jev=False, record_trace=False, limit=6, max_chars=budget)
        wall_ms = (time.perf_counter() - search_started) * 1000
        if network_attempts:
            raise RuntimeError('A blocked network attempt invalidates this local-only receipt')
        if any(row['id'] not in ids for row in found['results']):
            raise RuntimeError('Recall returned an ID outside the frozen synthetic corpus')
        searches.append({**found, 'max_chars': budget, 'limit': 6,
                         'wall_elapsed_ms': round(wall_ms, 3),
                         'context_sha256': hashlib.sha256(found['context'].encode()).hexdigest(),
                         'returned_seed_keys': [ids[row['id']] for row in found['results']]})
    receipt = dict(schema_version=1, source=str(source), state=str(state),
                   brain_store_module=str(Path(brain.__file__).resolve()),
                   project_id=PROJECT, user_id=USER, seed_time=SEED_TIME,
                   seed_sha256=hashlib.sha256(seed_bytes).hexdigest(),
                   seed_ids=[{'key': key, 'id': identifier} for identifier, key in ids.items()],
                   query=QUERY, strategy='keyword', jev=False, record_trace=False,
                   network_blocked=True, network_attempts=network_attempts,
                   searches=searches,
                   elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as handle:
        json.dump(receipt, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write('\n')
    print(json.dumps({'status': 'complete', 'out': str(out),
                      'seed_count': len(ids), 'search_count': len(searches)}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'status': 'failed', 'error': type(error).__name__,
                          'message': str(error)}), file=sys.stderr)
        raise SystemExit(1)
