"""Load shared startup context and verify evidence before completing a run."""
import argparse
from contextlib import ExitStack
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

from automatic_memory import MAX_CANONICAL_BYTES, _payload
from brain_store import BrainStore, digest, safe_path, scope
from contribution_tasks import task_ledger
from contributions import _atomic_text, build_report
from coordinator_handoff import Coordinator
from init_run import create_run
from memory_bundle import read_json, validate_bundle
from paths import ROOT
from project_visuals import ProjectLibrary, project_from_report
from task_store import timestamp, write_json
from usage_guard import file_lock

JOB = re.compile(r'[a-f0-9]{32}')
MAX_SCAN = 2000
MAX_SCAN_BYTES = 16 * 1024**2


def load_operating_context():
    # Lazy import keeps lifecycle tests and recovery inspection independent of
    # guidance availability. A real startup must successfully load the file.
    from orchestration_context import load_operating_context as load
    return load()


def _object(path, root, maximum=512 * 1024):
    value = read_json(path, root, maximum)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object: ' + path.name)
    return value


def _manifest(coordinator):
    value = _object(coordinator.run / 'run.json', coordinator.run)
    if value.get('schema_version') != 1 or value.get('run_id') != coordinator.run.name:
        raise ValueError('Manifest does not identify the exact selected run')
    scope(value.get('project_id') or value['run_id'])
    if not isinstance(value.get('tasks'), list):
        raise ValueError('Manifest tasks must be a list')
    return value


def _identity(state):
    return {key: state[key] for key in ('owner', 'session', 'generation')}


def start_run(*, run=None, workspace=None, name=None, objective=None, project=None,
              owner=None, session=None, generation=None, query=None, no_memory=False,
              root=ROOT, brain_factory=BrainStore, context_loader=None):
    """Prepare a bounded packet; existing runs require explicit lead authority."""
    if run is not None and any(value is not None for value in (workspace, name, objective, project)):
        raise ValueError('Choose an existing --run or new-run creation options')
    if run is not None and any(value is None for value in (owner, session, generation)):
        raise ValueError('Resuming needs explicit --owner, --session and --generation')
    if run is None and (workspace is None or name is None or not objective):
        raise ValueError('Creating a run needs --workspace, --name and --objective')
    if run is None and any(value is not None for value in (owner, session, generation)):
        raise ValueError('New run identity is established by create_run')
    operating = (context_loader or load_operating_context)()
    created = run is None
    if created:
        run, _ = create_run(workspace, name, objective, project_id=project,
                            native_parent_session_id=os.environ.get('CODEX_THREAD_ID') or None)
    coordinator = Coordinator(run)
    with file_lock(coordinator.lock):
        state = coordinator.read()
        if created:
            owner, session, generation = (state[key] for key in ('owner', 'session', 'generation'))
        coordinator.require_owner(state, owner, session, generation)
        manifest = _manifest(coordinator)
        project_id = scope(manifest.get('project_id') or manifest['run_id'])
        search = query if query is not None else manifest.get('objective')
        if not isinstance(search, str) or not search.strip() or len(search) > 500:
            raise ValueError('Recall query must contain 1 to 500 characters; narrow --query for a long objective')
        if no_memory:
            recalled = {'query': search, 'project_id': project_id, 'user_id': 'local',
                        'results': [], 'context': '', 'context_chars': 0,
                        'status': 'not_requested', 'reason': 'explicit_no_memory'}
        else:
            recalled = brain_factory(Path(root)).search(search, project_id, limit=6, max_chars=8000)
        packet = {'schema_version': 1, 'status': 'prepared', 'created': created,
                  'prepared_at': timestamp(), 'run': str(coordinator.run),
                  'run_id': manifest['run_id'], 'project_id': project_id,
                  'coordinator': _identity(state), 'operating_context': operating,
                  'project_memory': recalled, 'provider_calls': 0,
                  'evidence_limit': 'Prepared for the lead; this is not proof of reading, obedience or native-worker delivery.'}
        rendered = ('# Orchestration startup packet\n\n' + packet['evidence_limit'] + '\n\n'
                    + operating['context'] + '\n\n## Project recall\n\n'
                    + (recalled['context'] or 'Project recall was explicitly omitted.') + '\n')
        packet['packet_sha256'] = hashlib.sha256(rendered.encode('utf-8')).hexdigest()
        # Match write_json's ASCII JSON and platform newlines, including Windows
        # CRLF, so the on-disk receipt stays within bounded metadata readers.
        serialized = (json.dumps(packet, indent=2) + '\n').replace('\n', os.linesep).encode('utf-8')
        if len(serialized) > 64 * 1024:
            raise ValueError('Startup packet exceeds its 64 KiB bound')
        coordinator.require_owner(coordinator.read(), owner, session, generation)
        # Each file is atomic. JSON is written last and binds the Markdown bytes.
        _atomic_text(safe_path(coordinator.run, coordinator.run / 'startup-context.md'), rendered)
        write_json(safe_path(coordinator.run, coordinator.run / 'startup-context.json'), packet)
        if 'native_work' not in manifest:
            manifest['native_work'] = True
            write_json(coordinator.run / 'run.json', manifest)
        return packet


def _linked(record, run, explicit):
    if record.get('job_id') in explicit or record.get('run_id') == run.name:
        return True
    output = record.get('requested_output')
    if not isinstance(output, str) or not output:
        return False
    path = Path(output)
    if not path.is_absolute():
        path = run.parent.parent / path
    return path.resolve().is_relative_to(run)


def _discover(root, run, manifest):
    explicit = set()
    for item in manifest['tasks']:
        identifier = item.get('job_id') if isinstance(item, dict) else item
        if identifier is not None:
            if not isinstance(identifier, str) or not JOB.fullmatch(identifier):
                raise ValueError('Manifest has an invalid task job ID')
            explicit.add(identifier)
    tasks = root / 'runs/tasks'
    candidates = list(itertools.islice(tasks.iterdir(), MAX_SCAN + 1)) if tasks.exists() else []
    if len(candidates) > MAX_SCAN:
        raise ValueError('Task discovery exceeded 2000 entries; scope cannot be fully verified')
    linked, budget = set(explicit), MAX_SCAN_BYTES
    for directory in candidates:
        if not JOB.fullmatch(directory.name):
            continue
        path = safe_path(root, directory / 'record.json')
        budget -= path.stat().st_size
        if budget < 0:
            raise ValueError('Task discovery exceeded its 16 MiB index budget')
        record = _object(path, root, 64 * 1024)
        if record.get('job_id') != directory.name:
            raise ValueError('Task index identity cannot be verified')
        if _linked(record, run, explicit):
            linked.add(directory.name)
    return sorted(linked)


def _receipt(root, result, project, brain):
    job = result['job_id']
    if result.get('assignment_project_id') != project:
        raise ValueError('Accepted task belongs to a different project')
    receipt = _object(root / 'runs/tasks' / job / 'memory-outcome.json', root, 96 * 1024)
    mode = receipt.get('mode')
    historical = receipt.get('status') == 'skipped' and receipt.get('reason') in (
        'forgotten', 'memory_removed', 'memory_inactive')
    if (receipt.get('schema_version') != 1 or receipt.get('job_id') != job
            or receipt.get('project_id') != project or mode not in ('automatic', 'curated', 'curated_bundle')
            or not (receipt.get('status') == 'remembered' or historical)):
        raise ValueError('Memory capture receipt has missing, foreign or unfinished identity/status')
    if not isinstance(receipt.get('request_sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', receipt['request_sha256']):
        raise ValueError('Memory capture request identity cannot be verified')
    if mode == 'automatic' and receipt['request_sha256'] != digest(_payload(result, None)):
        raise ValueError('Automatic memory capture request changed after its receipt')
    fields = ('job_id', 'assignment_project_id', 'response', 'finalized_at', 'review')
    if mode != 'curated_bundle':
        fields += ('task', 'category', 'assignment_id')
    if receipt.get('source_sha256') != digest({key: result.get(key) for key in fields}):
        raise ValueError('Memory capture source changed after its receipt')
    identifiers = receipt.get('memory_ids') if mode == 'curated_bundle' else [receipt.get('memory_id')]
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 24
            or any(not isinstance(value, str) or not JOB.fullmatch(value) for value in identifiers)
            or len(set(identifiers)) != len(identifiers) or receipt.get('memory_id') != identifiers[0]):
        raise ValueError('Memory capture identifiers cannot be verified')
    if mode == 'curated_bundle' and (not isinstance(receipt.get('memory_keys'), dict)
                                    or list(receipt['memory_keys'].values()) != identifiers):
        raise ValueError('Memory capture bundle identifiers cannot be verified')
    states = []
    # Use maintained Brain source proofs while retaining deleted/missing rows as
    # historical receipts, as record_accepted_bundle does. Never recapture here.
    with file_lock(brain.lock), brain._connection() as con:
        for identifier in identifiers:
            row = con.execute('SELECT * FROM memories WHERE id=?', (identifier,)).fetchone()
            if row is None:
                states.append('historical'); continue
            if row['project_id'] != project or row['user_id'] != 'local':
                raise ValueError('Memory receipt points to a different Brain project or user')
            if row['status'] == 'deleted':
                states.append('historical'); continue
            source = json.loads(row['source'])
            if (row['project_id'] != project or row['user_id'] != 'local'
                    or source.get('type') != 'task' or source.get('job_id') != job
                    or brain._source(source, project, approved=True)[1] != row['source_hash']):
                raise ValueError('Memory receipt points to unrelated or changed Brain evidence')
            if row['status'] == 'pending':
                raise ValueError('Memory receipt points to a memory still awaiting approval')
            states.append(row['status'])
    return {'mode': mode, 'memory_ids': identifiers, 'current_states': states,
            'historical_capture': True}


def _native_capture(root, run, result, receipt):
    if not (result.get('run_id') == run.name and result.get('imported_completed_artifact') is True
            and result.get('artifact_origin') == 'reviewed-run-closeout'
            and result.get('worker') == 'native-review' and result.get('category') == 'memory-curation'
            and type(result.get('provider_calls')) is int and result['provider_calls'] == 0
            and receipt['mode'] == 'curated_bundle'):
        return False
    payload = json.loads(result['response'])
    bundle, proof = payload['knowledge'], payload['evidence']
    validate_bundle(bundle, result['assignment_project_id'])
    capture_id = bundle.get('capture_id')
    if not isinstance(capture_id, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,59}', capture_id):
        raise ValueError('Native capture identifier cannot be verified')
    evidence = bundle.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12 or not isinstance(proof, list):
        raise ValueError('Native capture needs bounded evidence')
    actual = []
    for relative in evidence:
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError('Native capture evidence must stay within its run')
        path = safe_path(run, run / relative)
        with path.open('rb') as stream:
            raw = stream.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError('Native capture evidence exceeds 2 MiB')
        actual.append({'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()})
    if actual != proof:
        raise ValueError('Native capture evidence changed after review')
    journal = _object(run / 'review' / ('memory-capture-' + capture_id + '.json'), run, 96 * 1024)
    identity = digest({'bundle': bundle, 'proof': proof,
                       'reviewer': result['review']['reviewer'], 'note': result['review']['note']})
    saved = _object(root / 'runs/tasks' / result['job_id'] / 'memory-outcome.json', root, 96 * 1024)
    if (journal.get('job_id') != result['job_id'] or journal.get('status') != 'remembered'
            or journal.get('request_sha256') != identity or saved.get('request_sha256') != digest(bundle)):
        raise ValueError('Native capture journal does not match reviewed knowledge')
    return True


def closeout_run(run, *, owner, session, generation, root=ROOT,
                 brain_factory=BrainStore, library_factory=ProjectLibrary):
    """Verify existing reviews, receipts and map; never accept work or invent credit."""
    root = Path(root).resolve()
    coordinator = Coordinator(run)
    with file_lock(coordinator.lock), ExitStack() as task_locks:
        coordinator.require_owner(coordinator.read(), owner, session, generation)
        manifest = _manifest(coordinator)
        project = scope(manifest.get('project_id') or manifest['run_id'])
        result = {'schema_version': 1, 'run_id': coordinator.run.name, 'project_id': project,
                  'coordinator': {'owner': owner, 'session': session, 'generation': generation},
                  'checked_at': timestamp(), 'status': 'held', 'provider_calls': 0, 'checks': []}
        def check(name, action):
            try:
                value = action()
                result['checks'].append({'check': name, 'status': 'passed', 'detail': value})
                return value
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                result['checks'].append({'check': name, 'status': 'held', 'reason': reason})
                return None
        def native_status():
            for task in manifest['tasks']:
                if isinstance(task, dict) and task.get('agent') and not task.get('job_id'):
                    if task.get('status') not in ('completed', 'accepted', 'rejected', 'failed'):
                        raise ValueError('Native task remains pending or uncertain: ' + str(task['agent']))
            return 'No unresolved native task status'
        def startup():
            from orchestration_context import verify_run_startup
            operating = load_operating_context()
            binding = verify_run_startup(SimpleNamespace(run=coordinator.run,
                output=coordinator.run / 'closeout.json', project=project), operating)
            receipt = _object(coordinator.run / 'startup-context.json', coordinator.run, 64 * 1024)
            path = safe_path(coordinator.run, coordinator.run / 'startup-context.md')
            with path.open('rb') as stream:
                raw = stream.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024 or hashlib.sha256(raw).hexdigest() != receipt.get('packet_sha256'):
                raise ValueError('Startup packet bytes do not match their prepared receipt')
            return {'operating_sha256': operating['sha256'], 'packet_sha256': receipt['packet_sha256'],
                    'coordinator': binding['coordinator'], 'status': 'prepared_only'}
        startup_proof = check('startup_context', startup)
        check('native_task_status', native_status)
        jobs = check('task_discovery', lambda: _discover(root, coordinator.run, manifest))
        native = []
        hosted = {}
        source_hashes = {}
        for job in jobs or []:
            def validate_task(job=job):
                directory = safe_path(root, root / 'runs/tasks' / job)
                task_locks.enter_context(file_lock(safe_path(root, directory / 'review.lock')))
                task_locks.enter_context(file_lock(safe_path(root, directory / 'memory.lock')))
                canonical = _object(directory / 'result.json', root, MAX_CANONICAL_BYTES)
                if canonical.get('job_id') != job or not canonical.get('finalized_at'):
                    raise ValueError('Canonical task identity or finalization is missing')
                source_hashes[job] = digest(canonical)
                if canonical.get('status') == canonical.get('review_status') == 'rejected':
                    return {'decision': 'rejected', 'memory_required': False}
                if canonical.get('status') == canonical.get('execution_status') == 'failed':
                    return {'decision': 'failed', 'memory_required': False}
                if not (canonical.get('status') == canonical.get('review_status') == 'accepted'
                        and canonical.get('execution_status') == 'succeeded'):
                    raise ValueError('Task remains pending, unreviewed or uncertain')
                receipt = _receipt(root, canonical, project, brain_factory(root))
                if _native_capture(root, coordinator.run, canonical, receipt):
                    native.append(job)
                elif canonical.get('imported_completed_artifact') is True:
                    raise ValueError('Imported task is not a verified native capture')
                else:
                    observed = task_ledger(canonical)
                    worker = 'worker:' + canonical.get('worker', 'unknown')
                    actor = next(row for row in observed['contributors'] if row['id'] == worker)
                    events = [row for row in observed['activity'] if row['agent_id'] == worker and row['kind'] == 'delegation']
                    if len(events) != 1:
                        raise ValueError('Accepted hosted task lacks observed delegation evidence')
                    hosted[job] = {'actor': actor, 'event': events[0]}
                return dict(receipt, decision='accepted')
            check('task:' + job, validate_task)
        native_required = manifest.get('native_work') is True or any(
            isinstance(task, dict) and task.get('agent') and not task.get('job_id') for task in manifest['tasks'])
        audit_path = coordinator.run / 'contribution-audit.json'
        ledger_path = coordinator.run / 'contributions-ledger.json'
        def contribution():
            ledger = _object(ledger_path, coordinator.run, 2 * 1024**2)
            report = _object(audit_path, coordinator.run, 2 * 1024**2)
            expected = build_report(ledger)
            if expected['scope_id'] != coordinator.run.name or report != expected:
                raise ValueError('Canonical contribution audit does not match the current run ledger')
            if not expected['attribution_complete'] or not expected['accepted_weight']:
                raise ValueError('Contribution audit is empty or attribution is incomplete')
            if expected['work_status_counts']['pending']:
                raise ValueError('Contribution ledger still contains pending work')
            verified_jobs = set(hosted).union(native)
            discovered_jobs = set(jobs or [])
            for work in ledger['work_items']:
                if (work['status'] == 'accepted' and work['id'] in discovered_jobs
                        and work['id'] not in verified_jobs):
                    raise ValueError('Accepted ledger work lacks a verified accepted canonical task: ' + work['id'])
            actors = {row['id']: row for row in ledger['contributors']}
            hosted_actors = set()
            actor_models, actor_workers = {}, {}
            for job, observed in hosted.items():
                events = [row for row in ledger['activity'] if row['task_id'] == job and row['kind'] == 'delegation']
                if len(events) != 1:
                    raise ValueError('Accepted hosted task needs one exact ledger delegation: ' + job)
                event = events[0]
                actor = actors[event['agent_id']]
                if actor['provider'] != observed['actor']['provider']:
                    raise ValueError('Ledger worker identity differs from canonical execution: ' + job)
                actor_models.setdefault(actor['id'], set()).update(observed['event']['actual_models'])
                actor_workers.setdefault(actor['id'], set()).add(observed['actor']['id'])
                if any(event.get(key) != observed['event'].get(key) for key in (
                        'status', 'input_tokens', 'output_tokens', 'actual_models')):
                    raise ValueError('Ledger delegation differs from observed canonical activity: ' + job)
                work = [row for row in ledger['work_items'] if row['id'] == job and row['status'] == 'accepted']
                if len(work) != 1 or not any(a['agent_id'] == actor['id'] and a['percent'] > 0 for a in work[0]['allocations']):
                    raise ValueError('Accepted hosted task needs its job ID work item and worker allocation: ' + job)
                hosted_actors.add(actor['id'])
            for actor_id, models in actor_models.items():
                if len(actor_workers[actor_id]) != 1 or set(actors[actor_id]['model'].split(', ')) != (models or {'unknown'}):
                    raise ValueError('Ledger actor needs one worker identity and its observed model union: ' + actor_id)
            if any(row['kind'] == 'delegation' and row['task_id'] in native for row in ledger['activity']):
                raise ValueError('Imported native capture is not a worker delegation')
            retained = {a['agent_id'] for row in ledger['work_items'] if row['status'] == 'accepted'
                        for a in row['allocations'] if a['percent'] > 0}
            view = project_from_report(report, '', manifest.get('display_name'))
            if view['state'] != 'ready' or not view['people']:
                raise ValueError('Contribution map is not ready')
            return {'ledger_sha256': digest(ledger), 'report_sha256': digest(report), 'project': view,
                    'native_contributors': sorted(retained - hosted_actors)}
        audit = check('contribution_audit', contribution)
        def require_native():
            required = native_required or bool(audit and audit['native_contributors'])
            if required and not native:
                raise ValueError('Native work needs an explicit run-linked reviewed brain capture')
            return {'required': required, 'capture_jobs': native}
        check('native_memory_capture', require_native)
        def still_current():
            coordinator.require_owner(coordinator.read(), owner, session, generation)
            if startup() != startup_proof:
                raise ValueError('Startup packet changed during closeout')
            if _manifest(coordinator) != manifest or _discover(root, coordinator.run, manifest) != jobs:
                raise ValueError('Run or task membership changed during closeout; retry inspection')
            for job, expected in source_hashes.items():
                current = _object(root / 'runs/tasks' / job / 'result.json', root, MAX_CANONICAL_BYTES)
                if digest(current) != expected:
                    raise ValueError('Task changed during closeout: ' + job)
            if audit and (digest(_object(ledger_path, coordinator.run, 2 * 1024**2)) != audit['ledger_sha256']
                          or digest(_object(audit_path, coordinator.run, 2 * 1024**2)) != audit['report_sha256']):
                raise ValueError('Contribution evidence changed during closeout')
            return 'Ownership and inspected evidence still match'
        if all(row['status'] == 'passed' for row in result['checks']):
            check('current_evidence', still_current)
        if all(row['status'] == 'passed' for row in result['checks']):
            def render():
                library = library_factory(root=root, state=root / 'runtime')
                library.register(audit_path)
                rendered = library.render()
                path = safe_path(root, Path(rendered['output']))
                if path.stat().st_size > 16 * 1024**2:
                    raise ValueError('Generated contribution map exceeds 16 MiB')
                page = path.read_text(encoding='utf-8')
                match = re.search(r'<script id="project-map-data" type="application/json">(.*?)</script>', page, re.S)
                if not match:
                    raise ValueError('Generated map lacks its canonical project data')
                entries = [row for row in json.loads(match[1])['projects'] if row['id'] == coordinator.run.name]
                if len(entries) != 1:
                    raise ValueError('Generated map does not contain the exact run')
                actual = dict(entries[0], date='')
                if actual != audit['project']:
                    raise ValueError('Generated map does not match current contribution evidence')
                return rendered
            check('generated_map', render)
            check('final_evidence', still_current)
        coordinator.require_owner(coordinator.read(), owner, session, generation)
        if all(row['status'] == 'passed' for row in result['checks']):
            result['status'] = 'completed'
        write_json(safe_path(coordinator.run, coordinator.run / 'closeout.json'), result)
        if result['status'] == 'completed':
            manifest.update(status='completed', completed_utc=result['checked_at'])
            manifest['contribution_audit'] = dict(manifest.get('contribution_audit', {}), status='complete')
            write_json(coordinator.run / 'run.json', manifest)
        elif manifest.get('status') == 'completed':
            # Keep the prior timestamp as historical evidence, but withdraw the
            # current completion claim when a new verification no longer passes.
            manifest['last_completed_utc'] = manifest.pop('completed_utc', manifest.get('completed_at'))
            manifest.pop('completed_at', None)
            manifest.update(status='in_progress', closeout_status='held')
            write_json(coordinator.run / 'run.json', manifest)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('start')
    start.add_argument('--run', type=Path)
    start.add_argument('--workspace', type=Path)
    start.add_argument('--name')
    start.add_argument('--objective')
    start.add_argument('--project')
    start.add_argument('--query')
    start.add_argument('--no-memory', action='store_true')
    closeout = commands.add_parser('closeout')
    closeout.add_argument('--run', type=Path, required=True)
    for command in (start, closeout):
        command.add_argument('--owner', required=command is closeout)
        command.add_argument('--session', required=command is closeout)
        command.add_argument('--generation', type=int, required=command is closeout)
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        result = start_run(**args) if command == 'start' else closeout_run(**args)
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result['status'] in ('prepared', 'completed') else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'status': 'held', 'provider_calls': 0,
                          'reason': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
