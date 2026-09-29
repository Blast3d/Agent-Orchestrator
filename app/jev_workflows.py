"""Source-bound Jev advisories. This module never mutates memory or dispatches work.

Explicit IDs select a small set of reviewed records. Their source proofs and
fingerprints are checked both before and after a decision, including cache hits.
Model decisions are proposals for the lead, never authority to perform actions.
"""
from datetime import datetime
import importlib
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from brain_store import BrainStore, RELATIONS, date, digest, now, safe_path, scope, text
from brain_semantic import fingerprint
from usage_guard import file_lock
import jev_openrouter

MAX_PAYLOAD_BYTES = 24 * 1024
MAX_MEMORIES = 12
MAX_PAIRS = 6
_ID = re.compile(r'[a-f0-9]{32}')
_RESERVED_SKILLS = {'none', 'defer', 'insufficient', 'ask_lead'}
_RESERVED_EVENTS = _RESERVED_SKILLS | {'queue'}
_CATALOGUE = {
    'evidence_review': ('evidence', 'Separate relevance, evidence quality and conflict.', ('query',)),
    'instruction_scan': ('evidence', 'Flag instruction-like evidence for human review.', ()),
    'memory_support': ('evidence', 'Check a proposed claim against reviewed evidence.', ('claim',)),
    'duplicates': ('curation', 'Suggest duplicate review for locally selected pairs.', ()),
    'relations': ('curation', 'Suggest an approved relationship type for review.', ()),
    'sufficiency': ('evidence', 'Assess answer support and whether more evidence is needed.', ('question', 'answer')),
    'recover': ('orchestration', 'Rank reviewed failure episodes and check applicability.', ('error', 'task')),
    'stale': ('curation', 'Prioritize records for rechecking without deleting them.', ()),
    'skills': ('orchestration', 'Recommend from an explicitly permitted skill catalogue.', ('task',)),
    'handoff': ('orchestration', 'Rank handoff evidence while preserving canonical ownership.', ('run_id',)),
    'durability': ('curation', 'Suggest durable, novel memories from a reviewed outcome.', ('claim',)),
    'sensitivity': ('evidence', 'Flag content for review before any broader reuse.', ('intended_use',)),
    'citations': ('evidence', 'Verify exact quotes locally, then assess source support.', ('answer',)),
    'event': ('orchestration', 'Recommend queue, an allowed handler, or lead review.', ('event',)),
}
_FIELDS = {
    'evidence_review': {'query', 'answer', 'claim'}, 'instruction_scan': set(), 'memory_support': {'claim'},
    'duplicates': {'pairs'}, 'relations': {'pairs'}, 'sufficiency': {'question', 'answer'},
    'recover': {'error', 'task', 'current_versions', 'memory_versions'},
    'stale': {'as_of', 'current_versions', 'memory_versions'},
    'skills': {'task', 'catalogue'}, 'handoff': {'run_id', 'task'},
    'durability': {'claim'}, 'sensitivity': {'intended_use'},
    'citations': {'answer', 'citations'}, 'event': {'event', 'handlers'},
}
_TELEMETRY = ('status', 'reason', 'model', 'provider', 'request_id', 'provider_calls',
              'input_tokens', 'output_tokens', 'cost_usd', 'elapsed_ms', 'http_status',
              'cache_hit', 'cache_status', 'cache_key', 'cache', 'origin_usage')


def workflow_catalog():
    """Return fresh JSON-ready catalogue entries in implementation priority order."""
    required_extra = {'skills': ['catalogue'], 'event': ['handlers'], 'citations': ['citations']}
    return [{'workflow': name, 'description': entry[1],
             'required_fields': list(entry[2]) + required_extra.get(name, []),
             'one_of_required': [],
             'accepted_fields': sorted(_FIELDS[name] | {'memory_ids'}),
             'memory_required': name not in ('skills', 'event'),
             'max_memories': 6 if name == 'evidence_review' else MAX_MEMORIES,
             'advisory_only': True, 'requires_review': True}
            for name, entry in _CATALOGUE.items()]


def _encoded(value):
    jev_openrouter._json_value(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')


def _validated_payload(workflow, payload):
    if not isinstance(payload, dict) or len(_encoded(payload)) > MAX_PAYLOAD_BYTES:
        raise ValueError('Workflow payload must be a bounded JSON object.')
    if payload.keys() - _FIELDS[workflow] - {'memory_ids'}:
        raise ValueError('Workflow payload contains unsupported fields.')
    value = json.loads(_encoded(payload))  # Detach caller-owned mutable objects.
    ids = value.setdefault('memory_ids', [])
    if (not isinstance(ids, list) or len(ids) > MAX_MEMORIES
            or any(not isinstance(i, str) or not _ID.fullmatch(i) for i in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Supply at most twelve unique memory identifiers.')
    for field in _CATALOGUE[workflow][2]:
        value[field] = text(value.get(field), field, 4000)
    for field in ('task', 'as_of'):
        if field in value:
            value[field] = text(value[field], field, 4000 if field == 'task' else 40)
    if workflow == 'evidence_review':
        if len(ids) > 6:
            raise ValueError('Evidence review accepts up to six memories.')
        for field in ('claim', 'answer'):
            if field in value:
                value[field] = text(value[field], field, 4000)
    return value


def _permitted(config, project, workflow):
    return (config.get('status') == 'ready' and config.get('key_present') is True
            and project in config.get('authorized_projects', [])
            and workflow in config.get('purposes', []))


def _snapshot(store, project, ids):
    records, proofs = [], {}
    with file_lock(store.lock), store._connection() as con:
        source_cache = {}
        for identifier in ids:
            row = con.execute('SELECT * FROM memories WHERE id=?', (identifier,)).fetchone()
            if (row is None or row['project_id'] != project or row['user_id'] != 'local'
                    or not row['reviewed_at'] or not row['reviewer']
                    or not store._current(row, now(), source_cache)):
                raise ValueError('One or more selected memories are not current reviewed sources in this scope.')
            # _current verifies task proofs. Check user-note proofs as well.
            source = json.loads(row['source'])
            if source.get('type') == 'user':
                _, proof_hash = store._source(source, project, approved=True)
                if proof_hash != row['source_hash']:
                    raise ValueError('A selected source proof changed.')
            elif source.get('type') not in ('task', 'memory_reference'):
                raise ValueError('Unknown selected source type.')
            proof = {'id': identifier, 'fingerprint': fingerprint(row),
                     'source_sha256': row['source_hash'],
                     'review_sha256': digest([row['reviewer'], row['reviewed_at'], row['review_note']]),
                     'valid_from': row['valid_from'], 'valid_to': row['valid_to']}
            proofs[identifier] = proof
            public = store._public(row)
            episode = public['episode']
            if (not isinstance(episode, dict)
                    or episode.keys() - {'problem', 'action', 'outcome'}
                    or any(not isinstance(value, str) for value in episode.values())):
                raise ValueError('Stored reviewed episode shape is invalid.')
            records.append({key: public[key] for key in
                            ('id', 'kind', 'title', 'content', 'episode', 'source', 'tags',
                             'created_at', 'valid_from', 'valid_to')})
            records[-1]['source_sha256'] = row['source_hash']
    if len(_encoded(records)) > 12 * 1024:
        raise ValueError('Selected full memory summaries exceed 12 KiB; select fewer records.')
    return records, proofs


def _pairs(payload, records, *, directed=False):
    ids = [r['id'] for r in records]
    requested = payload.get('pairs')
    if requested is not None:
        if not isinstance(requested, list) or not 1 <= len(requested) <= MAX_PAIRS:
            raise ValueError('Supply one to six memory pairs.')
        pairs = []
        seen = set()
        for pair in requested:
            if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                    or any(not isinstance(i, str) or i not in ids for i in pair)):
                raise ValueError('Each pair must identify two different selected memories.')
            identity = tuple(pair) if directed else tuple(sorted(pair))
            if identity in seen:
                raise ValueError('Duplicate pair.')
            seen.add(identity)
            pairs.append(pair)
        return pairs
    if len(records) < 2:
        return []
    # Compare only an explicit first anchor against the bounded shortlist, not
    # all combinations. The model sees at most six locally preselected pairs.
    tokens = lambda r: set(re.findall(r'\w+', (r['title'] + ' ' + r['content']).casefold()))
    anchor = tokens(records[0])
    scores = [(len(anchor & tokens(r)) / max(1, len(anchor | tokens(r))), i, r['id'])
              for i, r in enumerate(records[1:])]
    return [[ids[0], identifier] for _, _, identifier in sorted(scores, key=lambda s: (-s[0], s[1]))[:MAX_PAIRS]]


def _versions(value, label):
    if (not isinstance(value, dict) or len(value) > 12
            or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', k)
                   or not isinstance(v, str) or not 1 <= len(v) <= 100 for k, v in value.items())):
        raise ValueError(label + ' must be a bounded component/version map.')
    return value


def _catalogue(value, label, reserved, minimum=1):
    if (not isinstance(value, dict) or not minimum <= len(value) <= 12
            or any(not isinstance(k, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,49}', k)
                   or k in reserved or not isinstance(v, str) or not v.strip() or len(v) > 600
                   for k, v in value.items())):
        raise ValueError(label + ' requires one to twelve allowed IDs with short descriptions.')
    return value


def _run_context(root, run_id, project):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', run_id):
        raise ValueError('Invalid run identifier.')
    home = root / '.orchestration' / run_id
    manifest = jev_openrouter._read(safe_path(root, home / 'run.json'), 128 * 1024)
    state = jev_openrouter._read(safe_path(root, home / 'coordinator.json'), 128 * 1024)
    if (not isinstance(manifest, dict) or not isinstance(state, dict)
            or manifest.get('run_id') != run_id or state.get('run_id') != run_id):
        raise ValueError('Canonical run context could not be verified.')
    # Match the maintained startup scope; never infer scope from a display name.
    run_project = manifest.get('memory_project_id') or manifest.get('project_id') or run_id
    if run_project != project:
        raise ValueError('Handoff run belongs to another project.')
    jobs = []
    tasks = manifest.get('tasks', [])
    if not isinstance(tasks, list) or len(tasks) > 200:
        raise ValueError('Run task list is invalid or oversized.')
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError('Invalid run task.')
        job_id = task.get('job_id')
        if job_id is not None:
            if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
                raise ValueError('Invalid canonical job identifier.')
            jobs.append(job_id)
    context = {k: state.get(k) for k in ('run_id', 'owner', 'session', 'generation', 'status')}
    context['job_ids'] = list(dict.fromkeys(jobs))
    if (not isinstance(context['owner'], str) or not isinstance(context['session'], str)
            or type(context['generation']) is not int or context['generation'] < 1):
        raise ValueError('Canonical ownership is incomplete.')
    checkpoint = state.get('checkpoint', {})
    if not isinstance(checkpoint, dict):
        raise ValueError('Invalid canonical checkpoint.')
    for key in ('open_jobs', 'authorization', 'completed', 'next_steps', 'decisions'):
        entries = checkpoint.get(key, [])
        if not isinstance(entries, list) or len(entries) > 24:
            raise ValueError('Handoff checkpoint exceeds bounded context.')
        context[key] = [text(entry, 'Handoff context', 1000) for entry in entries]
    if len(_encoded(context)) > 8000:
        raise ValueError('Handoff context exceeds 8000 bytes.')
    return context


def _prepare(root, project, workflow, payload, records):
    value = dict(payload, memories=records)
    checks = {}
    if workflow in ('duplicates', 'relations'):
        value['pairs'] = _pairs(payload, records, directed=workflow == 'relations')
        value['allowed_relations'] = list(RELATIONS)
        checks['pairs_preselected_locally'] = True
        checks['pairs'] = [list(pair) for pair in value['pairs']]
        checks['pairs_source'] = 'caller' if payload.get('pairs') is not None else 'local_anchor_shortlist'
    if workflow in ('recover', 'stale'):
        current = _versions(payload.get('current_versions', {}), 'Current versions')
        versions = payload.get('memory_versions', {})
        if not isinstance(versions, dict) or versions.keys() - set(payload['memory_ids']):
            raise ValueError('Memory versions must refer only to selected records.')
        applicability = {}
        for record in records:
            recorded = _versions(versions.get(record['id'], {}), 'Recorded versions')
            applicability[record['id']] = ('unknown' if not recorded or not current
                or recorded.keys() - current.keys() else
                'version_match' if all(current[k] == v for k, v in recorded.items()) else 'version_mismatch')
        checks['version_applicability'] = applicability
        checks['version_evidence'] = 'caller_supplied_exact_comparison; verify installed versions before reuse'
        if workflow == 'recover':
            value['memories'] = [r for r in records if r['kind'] == 'episode']
            checks['excluded_non_episode_ids'] = [r['id'] for r in records if r['kind'] != 'episode']
        else:
            as_of = date(payload.get('as_of'), now())
            instant = datetime.fromisoformat(as_of)
            checks['age_days'] = {r['id']: (instant - datetime.fromisoformat(r['valid_from'])).total_seconds() / 86400
                                  for r in records}
            checks['as_of'] = as_of
            checks['action_limit'] = 'propose_refresh_only'
    if workflow == 'skills':
        catalogue = _catalogue(payload.get('catalogue'), 'Skill catalogue', _RESERVED_SKILLS)
        value['catalogue'] = [{'id': k, 'description': v} for k, v in catalogue.items()]
    if workflow == 'event':
        handlers = _catalogue(payload.get('handlers'), 'Handlers', _RESERVED_EVENTS, minimum=0)
        value['handlers'] = [{'id': k, 'description': v} for k, v in handlers.items()]
    if workflow == 'handoff':
        value['run_context'] = _run_context(root, payload['run_id'], project)
        checks['run_context'] = value['run_context']
    if workflow == 'citations':
        citations = payload.get('citations')
        if not isinstance(citations, list) or not 1 <= len(citations) <= 12:
            raise ValueError('Supply one to twelve exact citations.')
        by_id = {r['id']: r for r in records}
        verified = []
        for citation in citations:
            if (not isinstance(citation, dict) or citation.keys() - {'memory_id', 'quote', 'claim'}
                    or citation.get('memory_id') not in by_id):
                raise ValueError('Citation must refer to selected reviewed evidence.')
            text(citation.get('quote'), 'Exact quote', 2000)
            quote = citation['quote']  # Preserve whitespace for an exact check.
            claim = text(citation.get('claim', payload['answer']), 'Citation claim', 4000)
            record = by_id[citation['memory_id']]
            fields = {'title': record['title'], 'content': record['content'],
                      **{'episode.' + key: value for key, value in record['episode'].items()}}
            match = None
            for field, content in fields.items():
                start = content.find(quote)
                if start >= 0:
                    match = {'field': field, 'start': start, 'end': start + len(quote),
                             'offset_unit': 'unicode_codepoint'}
                    break
            verified.append({'memory_id': record['id'], 'quote': quote, 'claim': claim,
                             'quote_exists': match is not None, 'match': match,
                             'quote_source': 'reviewed_memory'})
        value['citations'] = verified
        checks['citations'] = [{'memory_id': c['memory_id'], 'quote_exists': c['quote_exists'],
                               'quote_source': c['quote_source'], 'match': c['match']} for c in verified]
    value['deterministic_checks'] = checks
    return value, checks


def _builder(workflow, payload):
    module = importlib.import_module('jev_' + _CATALOGUE[workflow][0] + '_decisions')
    if _CATALOGUE[workflow][0] == 'evidence':
        # Keep pure builders unaware of scope/cache/run mechanics.
        value = {k: v for k, v in payload.items()
                 if k in _FIELDS[workflow] | {'memories'} and k not in ('question',)}
        if workflow == 'sufficiency':
            value['query'] = payload['question']
        if workflow == 'citations':
            value.pop('answer', None)  # Claims are bound to individual citations.
        payload = value
    elif workflow == 'stale':
        checks = payload['deterministic_checks']
        payload = dict(payload, deterministic_checks={
            record['id']: {'age_days': checks['age_days'][record['id']],
                           'version_applicability': checks['version_applicability'][record['id']],
                           'version_evidence': 'caller supplied; installation not verified',
                           'as_of': checks['as_of']}
            for record in payload['memories']})
    return module.build(workflow, payload)


def _validate_choices(workflow, prepared, questions):
    permitted = {
        'evidence_review': {'supported', 'contradicted', 'insufficient', 'yes', 'no', 'conflict', 'consistent'},
        'memory_support': {'supported', 'contradicted', 'insufficient'},
        'citations': {'supported', 'contradicted', 'insufficient'},
        'sufficiency': {'yes', 'no', 'insufficient'},
        'recover': {'applicable', 'not_applicable', 'insufficient'},
        'duplicates': {'duplicate', 'related', 'distinct', 'insufficient'},
        'durability': {'novel', 'duplicate', 'insufficient'},
        'stale': {'applicable', 'superseded', 'insufficient', 'recheck', 'no_recheck'},
    }.get(workflow, set())
    if workflow == 'skills':
        permitted = {entry['id'] for entry in prepared['catalogue']} | {'none', 'defer'}
    elif workflow == 'event':
        permitted = {entry['id'] for entry in prepared['handlers']} | {'queue', 'ask_lead', 'none', 'defer'}
    elif workflow == 'relations':
        permitted = set(RELATIONS) | {'none', 'defer'}
    for question in questions.values():
        if question['type'] == 'choice' and set(question['criteria']) - permitted:
            raise ValueError('A builder expanded the permitted choices.')


def _judgments(questions, answers, threshold):
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError('Decision question binding changed.')
    judgments, suggestions = [], []
    for identifier, question in questions.items():
        answer, kind = answers[identifier], question['type']
        if not isinstance(answer, dict) or answer.get('type') != kind:
            raise ValueError('Invalid decision type.')
        judgment = {'question_id': identifier, 'type': kind, 'status': 'abstained'}
        if kind == 'noul':
            probability = answer.get('noul')
            if not jev_openrouter._number(probability, 0, 1):
                raise ValueError('Invalid probability.')
            judgment.update(probability=probability, status='signal')
            judgment['interpretation'] = 'review_signal' if probability >= threshold else 'no_positive_signal'
            if probability >= threshold:
                suggestions.append({'question_id': identifier, 'action': 'review_signal', 'probability': probability})
        else:
            confidence = answer.get('confidence')
            if not jev_openrouter._number(confidence, 0, 1):
                raise ValueError('Invalid confidence.')
            judgment['confidence'] = confidence
            value = answer.get(kind)
            if kind == 'choice' and value not in question['criteria']:
                raise ValueError('Unpermitted decision choice.')
            if kind == 'score' and not jev_openrouter._number(value, 0, len(question['criteria']) - 1):
                raise ValueError('Invalid decision score.')
            if kind == 'score':
                judgment.update(criteria=list(question['criteria']),
                                direction='higher_matches_later_criteria',
                                score_range={'minimum': 0, 'maximum': len(question['criteria']) - 1})
            if confidence >= threshold:
                judgment.update(status='review_required', **{kind: value})
                if kind == 'choice' and value in ('none', 'defer', 'insufficient', 'ask_lead'):
                    suggestions.append({'question_id': identifier, 'action': 'ask_lead', 'choice': value})
                else:
                    suggestion = {'question_id': identifier, 'action': 'review_' + kind, kind: value}
                    if kind == 'score':
                        suggestion.update(criteria=list(question['criteria']),
                                          direction='higher_matches_later_criteria',
                                          score_range=dict(judgment['score_range']), is_endorsement=False)
                    suggestions.append(suggestion)
        judgments.append(judgment)
    return judgments, suggestions


def run_workflow(root, project_id, workflow, payload, *, user_id='local'):
    """Return a bounded source-bound receipt; no proposal is automatically applied."""
    if workflow not in _CATALOGUE:
        raise ValueError('Unknown Jev workflow.')
    project = scope(project_id)
    if user_id != 'local':
        raise ValueError('Jev workflows currently support only the local user.')
    payload = _validated_payload(workflow, payload)
    receipt = {'schema_version': 1, 'workflow': workflow, 'project_id': project, 'user_id': 'local',
               'status': 'unavailable', 'advisory_only': True, 'requires_review': True,
               'created_at': now(), 'sources': [], 'source_ids': [], 'source_sha256': None,
               'input_provenance': {'memory_evidence': 'current_reviewed_sources',
                                    'supplied_fields': sorted(payload.keys() - {'memory_ids'}),
                                    'supplied_fields_reviewed': False},
               'decision': {'provider_calls': 0}, 'judgments': [], 'suggestions': [],
               'deterministic_checks': {}, 'reason': ''}
    config = jev_openrouter.load_config(root)
    if not _permitted(config, project, workflow):
        receipt['reason'] = 'This project and workflow require explicit Jev authorization and a ready connection.'
        return receipt
    if workflow not in ('skills', 'event') and not payload['memory_ids']:
        return dict(receipt, status='insufficient_evidence', reason='Select current reviewed memory evidence.')
    try:
        root = Path(root).resolve()
        store = BrainStore(root) if payload['memory_ids'] else None
        records, snapshots = _snapshot(store, project, payload['memory_ids']) if store else ([], {})
        prepared, checks = _prepare(root, project, workflow, payload, records)
        receipt['deterministic_checks'] = checks
        if workflow in ('duplicates', 'relations') and not prepared['pairs']:
            return dict(receipt, status='insufficient_evidence', reason='Select two reviewed records to compare.')
        if workflow == 'recover' and not prepared['memories']:
            return dict(receipt, status='insufficient_evidence', reason='Select reviewed problem/action/outcome episodes.')
        if workflow == 'citations' and not all(c['quote_exists'] for c in prepared['citations']):
            return dict(receipt, status='invalid_citation', reason='An exact quote is absent from its selected reviewed memory.')
        state, questions = _builder(workflow, prepared)
        request_bytes = jev_openrouter._request(state, questions)  # Bound before I/O, including mocked providers.
        _validate_choices(workflow, prepared, questions)
        # Explicit-ID workflows do not depend on unrelated project records.
        # Exact source proofs + request state bind every selected dependency;
        # source revalidation still runs before and after cache hits.
        decision = jev_openrouter.evaluate(root, project, workflow, state, questions,
            user_id='local', cache_context={'sources': snapshots})
        if not isinstance(decision, dict):
            raise ValueError('Invalid decision receipt.')
        receipt['decision'] = {key: decision[key] for key in _TELEMETRY if key in decision}
        # Source text, source IDs and every derived judgment are withheld when
        # one source changes: batch questions can depend on multiple memories.
        try:
            _, fresh = _snapshot(store, project, payload['memory_ids']) if store else ([], {})
            if fresh != snapshots:
                raise ValueError('Source fingerprint changed.')
            if workflow == 'handoff' and _run_context(root, payload['run_id'], project) != checks['run_context']:
                raise ValueError('Run ownership changed.')
        except (OSError, ValueError, sqlite3.Error):
            return dict(receipt, status='source_changed', reason='Evidence or run ownership changed during the decision.',
                        deterministic_checks={})
        fresh_config = jev_openrouter.load_config(root)
        if not _permitted(fresh_config, project, workflow):
            return dict(receipt, status='authorization_changed', reason='Workflow authorization changed during the decision.',
                        deterministic_checks={})
        receipt.update(sources=list(snapshots.values()), source_ids=list(snapshots), source_sha256=digest(snapshots))
        receipt['request_binding'] = {
            'sha256': hashlib.sha256(request_bytes).hexdigest(), 'bytes': len(request_bytes),
            'question_count': len(questions),
            'prepared_memory_ids': [m['id'] for m in state.get('memories', [])]
                if isinstance(state, dict) else [],
            'evidence_limit': 'Prepared request binding does not prove provider delivery or useful recall.'}
        if workflow == 'handoff':
            receipt.update(delivery_status='prepared_only', run_context_sha256=digest(checks['run_context']))
        if decision.get('status') != 'ok':
            return dict(receipt, status=decision.get('status', 'unavailable'), reason='No validated semantic proposal is available.')
        judgments, suggestions = _judgments(questions, decision.get('answers'), fresh_config.get('min_confidence', .8))
        targets = state.get('question_targets', {}) if isinstance(state, dict) else {}
        if isinstance(targets, dict):
            # Targets are local builder metadata, not model-written assertions.
            for judgment in judgments:
                target = targets.get(judgment['question_id'])
                if isinstance(target, dict):
                    judgment['target'] = target
                if workflow == 'recover' and judgment.get('choice') == 'applicable':
                    identifier = target.get('target_id', target.get('memory_id')) if isinstance(target, dict) else None
                    applicability = checks['version_applicability'].get(identifier, 'unknown')
                    if applicability != 'version_match':
                        judgment.update(status='blocked_by_version_check', deterministic_result=applicability)
                        suggestions = [s for s in suggestions if s['question_id'] != judgment['question_id']]
        for suggestion in suggestions:
            suggestion.update(workflow=workflow, source_ids=list(snapshots), requires_review=True)
            if workflow == 'recover':
                suggestion['version_applicability'] = dict(checks['version_applicability'])
                suggestion['applicability_verified'] = False
            elif workflow == 'stale':
                suggestion['action_limit'] = 'propose_refresh_only'
        uncertain = sum(j['status'] == 'abstained' for j in judgments)
        blocked = any(j['status'] == 'blocked_by_version_check' for j in judgments)
        receipt.update(judgments=judgments, suggestions=suggestions,
                       status='low_confidence' if uncertain == len(judgments) else 'partial' if uncertain or blocked else 'ok',
                       reason='Advisory judgments require lead review; no action has been performed.')
        return receipt
    except (OSError, ValueError, TypeError, ImportError, sqlite3.Error):
        # Never echo arbitrary provider/source text or secrets in an exception.
        return dict(receipt, status='invalid_input', reason='Workflow evidence or bounded input could not be validated.',
                    deterministic_checks={})
