"""Mandatory, bounded operating guidance for new worker requests."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from provider_policy import validate_plan

GUIDE = Path(__file__).with_name('assets') / 'orchestration-context.md'
MAX_GUIDE_BYTES = 16 * 1024


class OperatingContextError(ValueError):
    """Hold a new request until its operating context can be verified."""


def load_operating_context(path=None):
    path = Path(path) if path is not None else GUIDE
    try:
        if not path.is_file() or path.is_symlink() or path.resolve().parent != path.parent.resolve():
            raise ValueError('Guide must be a regular local file')
        with path.open('rb') as stream:
            raw = stream.read(MAX_GUIDE_BYTES + 1)
        if len(raw) > MAX_GUIDE_BYTES:
            raise ValueError('Guide exceeds 16 KiB')
        context = raw.decode('utf-8')
        if not context.splitlines() or context.splitlines()[0] != '# Orchestration Operating Guide (v1)' or len(context.strip()) < 100:
            raise ValueError('Guide version or content is invalid')
    except (OSError, UnicodeError, ValueError) as error:
        raise OperatingContextError('Operating guidance could not load; repair app/assets/orchestration-context.md before dispatch. ' + str(error)) from error
    return {'schema_version': 1, 'revision': '1', 'context': context,
            'sha256': hashlib.sha256(raw).hexdigest(), 'chars': len(context),
            'source': 'app/assets/orchestration-context.md',
            'loaded_at': datetime.now(timezone.utc).isoformat()}


def selected_run(args):
    explicit = getattr(args, 'run', None)
    if explicit:
        return Path(explicit).resolve()
    # An output inside a run is already an explicit assignment of that scope.
    for parent in Path(args.output).resolve().parents:
        if parent.parent.name == '.orchestration':
            return parent.resolve()
    return None


def verify_run_startup(args, operating):
    try:
        run = selected_run(args)
        if run is None:
            return None
        from coordinator_handoff import Coordinator, read_object
        coordinator = Coordinator(run)
        state = coordinator.read()
        manifest = read_object(run / 'run.json')
        receipt = read_object(run / 'startup-context.json')
        identity = receipt.get('coordinator', {})
        coordinator.require_owner(state, identity.get('owner'), identity.get('session'), identity.get('generation'))
        policy = validate_plan(receipt.get('provider_policy'), state['owner'])
        policy_text = json.dumps(policy, sort_keys=True)
        if hashlib.sha256(policy_text.encode('utf-8')).hexdigest() != receipt.get('provider_policy_sha256'):
            raise ValueError('Provider family policy no longer matches the prepared startup packet')
        packet_path = run / 'startup-context.md'
        if packet_path.is_symlink():
            raise ValueError('Startup packet must be a regular local file')
        with packet_path.open('rb') as stream:
            packet = stream.read(64 * 1024 + 1)
        if (len(packet) > 64 * 1024 or hashlib.sha256(packet).hexdigest() != receipt.get('packet_sha256')
                or ('```json\n' + policy_text + '\n```') not in packet.decode('utf-8')):
            raise ValueError('Provider family policy must match the prepared startup packet bytes')
        saved = receipt.get('operating_context', {})
        project = manifest.get('project_id') or run.name
        if (receipt.get('schema_version') != 1 or receipt.get('run_id') != run.name
                or receipt.get('project_id') != project or getattr(args, 'project', None) != project
                or saved.get('sha256') != operating['sha256']
                or hashlib.sha256(saved.get('context', '').encode('utf-8')).hexdigest() != operating['sha256']):
            raise ValueError('Startup context no longer matches this run, project, or guide')
        return {'run_id': run.name, 'run_path': str(run), 'coordinator': identity,
                'operating_sha256': operating['sha256'], 'provider_policy': policy}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise OperatingContextError('Run startup is missing or stale. Run orchestrator.py start --run with the current lead identity before dispatch. ' + str(error)) from error


def verify_request_context(args, operating, binding):
    """Recheck immediately before execution, after potentially slow quota work."""
    current = load_operating_context()
    if current['sha256'] != operating['sha256']:
        raise OperatingContextError('Operating guidance changed while preparing this request; start a new assignment after reloading it.')
    if verify_run_startup(args, current) != binding:
        raise OperatingContextError('Run startup changed while preparing this request; inspect the current lead.')
