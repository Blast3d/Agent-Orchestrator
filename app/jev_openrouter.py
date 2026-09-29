"""Optional Jev decisions through OpenRouter, with explicit project permission.

No configuration is created automatically and no request is retried. The daily
counter records attempted requests, including failures with uncertain billing.
Byte limits bound payload size; they are not token estimates or dollar caps.
"""
from datetime import date, datetime, timezone
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from storage_budget import StorageBudget, StorageLimitError
from usage_guard import file_lock, write_json

MODEL = 'typesafe/jev-1.13'
ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
MAX_CONFIG_BYTES = 16 * 1024
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_USAGE_BYTES = 1024
PURPOSES = ('memory_rank', 'memory_passage_review', 'route', 'evidence_review', 'instruction_scan',
            'memory_support', 'sufficiency', 'sensitivity', 'citations', 'duplicates',
            'relations', 'stale', 'durability', 'recover', 'skills', 'handoff', 'event')


class _Unavailable(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate key')
            result[key] = value
        return result

    def constant(_):
        raise ValueError('Nonfinite JSON')

    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def _number(value, lower, upper):
    return (type(value) in (int, float) and lower <= value <= upper
            and math.isfinite(value))


def _text(value, maximum=128):
    return (isinstance(value, str) and 1 <= len(value) <= maximum
            and value == value.strip() and all(ord(c) >= 32 and ord(c) != 127 for c in value))


def _path(root, name):
    root = Path(root).resolve()
    runtime = root / 'runtime'
    path = runtime / name
    # Reject junctions as well as symlinks, even if their target is inside root.
    for candidate in (runtime, path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or not (stat.S_ISDIR(info.st_mode) if candidate == runtime
                        else stat.S_ISREG(info.st_mode))):
            raise ValueError('Unsafe managed path')
    return path


def _read(path, limit):
    with path.open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Oversized JSON')
    return _json(raw)


def _env_path(root, env_file):
    if (not _text(env_file, 2048)
            or any(127 <= ord(char) < 160 for char in env_file)):
        raise ValueError('Invalid credential file path')
    root = Path(root).resolve()
    specified = Path(env_file)
    if '..' in specified.parts:
        raise ValueError('Parent traversal is not supported')
    if specified.is_absolute():
        path = specified
    else:
        # Drive-relative and root-relative Windows paths must not escape root.
        if specified.anchor:
            raise ValueError('Credential file requires a fully absolute or relative path')
        path = root / specified
        path.relative_to(root)
    for candidate in reversed((path, *path.parents)):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                or not (stat.S_ISREG(info.st_mode) if candidate == path
                        else stat.S_ISDIR(info.st_mode))):
            raise ValueError('Unsafe credential file path')
    return path


def _dotenv_key(root, name, env_file='.env'):
    """Read one literal credential; never execute or expand dotenv contents."""
    try:
        path = _env_path(root, env_file)
        with path.open('rb') as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            return None
        content = raw.decode('utf-8-sig')
        found = None
        seen = False
        for line in content.split('\n'):
            stripped = line.removesuffix('\r').strip(' \t')
            if not stripped or stripped.startswith('#'):
                continue
            match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)', stripped)
            if match is None:
                if re.match(re.escape(name) + r'(?:\s|=|$)', stripped):
                    return None
                continue
            if match[1] != name:
                continue
            if seen:
                return None
            seen = True
            literal = match[2].strip(' \t')
            if literal.startswith(('"', "'")):
                quote = literal[0]
                closing = literal.find(quote, 1)
                if closing < 0:
                    return None
                trailing = literal[closing + 1:].strip(' \t')
                if trailing and not trailing.startswith('#'):
                    return None
                found = literal[1:closing]
            else:
                found = re.split(r'[ \t]+#', literal, maxsplit=1)[0].strip(' \t')
                if found.endswith(('"', "'")):
                    return None
        return found
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def _key(config, root):
    key = os.environ.get(config['api_key_env'], '')
    if not key:
        key = _dotenv_key(root, config['api_key_env'], config.get('env_file', '.env'))
    if (not key or len(key) > 8192
            or any(ord(char) < 33 or ord(char) > 126 for char in key)):
        return None
    return key


def load_config(root):
    """Return safe configuration/status metadata; never return a credential."""
    try:
        path = _path(root, 'jev-config.json')
        if not path.exists():
            return {'status': 'disabled', 'reason': 'Jev has not been configured.',
                    'enabled': False, 'key_present': False}
        value = _read(path, MAX_CONFIG_BYTES)
        required = {'schema_version', 'enabled'}
        allowed = required | {'model', 'api_key_env', 'authorized_projects', 'purposes',
                              'timeout_seconds', 'max_requests_per_day', 'min_confidence', 'env_file',
                              'cache_enabled', 'cache_ttl_seconds'}
        if (not isinstance(value, dict) or not required <= value.keys()
                or value.keys() - allowed or type(value['schema_version']) is not int
                or value['schema_version'] != 1 or type(value['enabled']) is not bool):
            raise ValueError('Invalid configuration')
        if not value['enabled']:
            return {'status': 'disabled', 'reason': 'Jev is disabled.',
                    'enabled': False, 'key_present': False}
        config = {'schema_version': 1, 'enabled': True, 'model': MODEL,
                  'api_key_env': 'OPENROUTER_API_KEY', 'authorized_projects': [],
                  'purposes': [], 'timeout_seconds': 10, 'max_requests_per_day': 100,
                  'min_confidence': .8, 'env_file': '.env',
                  'cache_enabled': False, 'cache_ttl_seconds': 3600}
        config.update(value)
        if (config['model'] != MODEL or not isinstance(config['api_key_env'], str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', config['api_key_env'])):
            raise ValueError('Invalid model or environment name')
        _env_path(root, config['env_file'])
        for name in ('authorized_projects', 'purposes'):
            items = config[name]
            if (not isinstance(items, list) or len(items) > 100
                    or any(not _text(item) for item in items)
                    or len(items) != len(set(items))):
                raise ValueError('Invalid scope')
        if (any(item not in PURPOSES for item in config['purposes'])
                or not _number(config['timeout_seconds'], .1, 15)
                or type(config['max_requests_per_day']) is not int
                or not 1 <= config['max_requests_per_day'] <= 1000
                or not _number(config['min_confidence'], 0, 1)
                or type(config['cache_enabled']) is not bool
                or type(config['cache_ttl_seconds']) is not int
                or not 60 <= config['cache_ttl_seconds'] <= 86400):
            raise ValueError('Invalid limits')
        present = _key(config, root) is not None
        return dict(config, key_present=present,
                    status='ready' if present else 'unavailable',
                    reason='Jev is configured.' if present else
                    'The configured Jev credential is unavailable.')
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        return {'status': 'unavailable', 'reason': 'Jev configuration cannot be read safely.',
                'enabled': False, 'key_present': False}


def _json_value(value, depth=0):
    if depth > 32:
        raise ValueError('Excessive nesting')
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is dict and all(type(key) is str for key in value):
        children = value.values()
    elif type(value) is list:
        children = value
    else:
        raise ValueError('Invalid JSON value')
    for child in children:
        _json_value(child, depth + 1)


def _request(state, questions):
    if not isinstance(state, (str, dict, list)):
        raise ValueError('Invalid state')
    if (not isinstance(questions, dict) or not 1 <= len(questions) <= 64
            or any(not _text(key) for key in questions)):
        raise ValueError('Invalid questions')
    for question in questions.values():
        if not isinstance(question, dict) or question.get('type') not in ('choice', 'score', 'noul'):
            raise ValueError('Invalid question type')
        kind = question['type']
        expected = {'type', 'instructions'} | ({'criteria'} if kind != 'noul' else set())
        if set(question) != expected or not _text(question['instructions'], MAX_REQUEST_BYTES):
            raise ValueError('Invalid question fields')
        if kind == 'choice':
            criteria = question['criteria']
            if (not isinstance(criteria, dict) or not 2 <= len(criteria) <= 64
                    or any(not _text(key) or not _text(val, MAX_REQUEST_BYTES)
                           for key, val in criteria.items())):
                raise ValueError('Invalid choices')
        if kind == 'score':
            criteria = question['criteria']
            if (not isinstance(criteria, list) or not 2 <= len(criteria) <= 10
                    or any(not _text(item, MAX_REQUEST_BYTES) for item in criteria)
                    or len(criteria) != len(set(criteria))):
                raise ValueError('Invalid score levels')
    payload = {'model': MODEL, 'state': state, 'questions': questions}
    _json_value(payload)
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('Oversized request')
    return raw


def _reserve_request(root, maximum):
    """Durably consume one UTC-day slot before attempting any network request."""
    try:
        path = _path(root, 'jev-usage.json')
        lock = _path(root, 'jev-usage.lock')
        budget = StorageBudget(root)
        with budget.allocation(4 * MAX_USAGE_BYTES, kind='task'):
            with file_lock(lock):
                path = _path(root, 'jev-usage.json')
                today = datetime.now(timezone.utc).date()
                if path.exists():
                    usage = _read(path, MAX_USAGE_BYTES)
                    if (not isinstance(usage, dict)
                            or set(usage) != {'schema_version', 'day', 'attempts'}
                            or type(usage['schema_version']) is not int or usage['schema_version'] != 1
                            or not isinstance(usage['day'], str)
                            or type(usage['attempts']) is not int
                            or not 0 <= usage['attempts'] <= 1000):
                        raise ValueError('Invalid usage')
                    previous = date.fromisoformat(usage['day'])
                    if usage['day'] != previous.isoformat() or previous > today:
                        raise ValueError('Invalid counter date')
                    attempts = usage['attempts'] if previous == today else 0
                else:
                    attempts = 0
                if attempts >= maximum:
                    raise _Unavailable('rate_limited', 'The Jev daily request limit has been reached.')
                write_json(path, {'schema_version': 1, 'day': today.isoformat(), 'attempts': attempts + 1})
    except _Unavailable:
        raise
    except (OSError, TimeoutError, ValueError, TypeError, UnicodeError,
            OverflowError, RecursionError, StorageLimitError):
        raise _Unavailable('unavailable', 'Jev request accounting cannot safely admit a request.') from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Jev redirects are disabled.', headers, fp)


def _post(raw, api_key, timeout):
    request = Request(ENDPOINT, data=raw, method='POST',
                      headers={'Authorization': 'Bearer ' + api_key,
                               'Content-Type': 'application/json', 'Accept': 'application/json'})
    opener = build_opener(_NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError('Unexpected status')
        raw_response = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw_response) > MAX_RESPONSE_BYTES:
            raise ValueError('Oversized response')
        return raw_response


def _response(raw, questions):
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('Invalid response size')
    value = _json(raw)
    required = {'model', 'provider', 'id', 'answers'}
    if (not isinstance(value, dict) or not required <= value.keys()
            or value.keys() - required - {'usage'}
            or not isinstance(value['model'], str)
            or not re.fullmatch(re.escape(MODEL) + r'(?:-[0-9]{8})?', value['model'])
            or value['provider'] != 'TypeSafe' or not isinstance(value['id'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', value['id'])
            or not isinstance(value['answers'], dict)
            or set(value['answers']) != set(questions)):
        raise ValueError('Invalid response envelope')
    for key, question in questions.items():
        answer, kind = value['answers'][key], question['type']
        if not isinstance(answer, dict) or answer.get('type') != kind:
            raise ValueError('Invalid answer type')
        if kind == 'noul':
            if set(answer) != {'type', 'noul'} or not _number(answer['noul'], 0, 1):
                raise ValueError('Invalid probability')
            continue
        expected = {'type', 'confidence', 'probabilities', kind} | ({'legend'} if kind == 'score' else set())
        if set(answer) != expected or not _number(answer['confidence'], 0, 1):
            raise ValueError('Invalid answer fields')
        options = (set(question['criteria']) if kind == 'choice'
                   else {str(index) for index in range(len(question['criteria']))})
        probs = answer['probabilities']
        if (not isinstance(probs, dict) or set(probs) != options
                or any(not _number(prob, 0, 1) for prob in probs.values())
                or not math.isclose(sum(probs.values()), 1, rel_tol=0, abs_tol=.015)):
            raise ValueError('Invalid distribution')
        if kind == 'choice':
            if (not isinstance(answer['choice'], str) or answer['choice'] not in options
                    or probs[answer['choice']] < max(probs.values())):
                raise ValueError('Invalid choice')
        else:
            legend = {str(index): level for index, level in enumerate(question['criteria'])}
            if (answer['legend'] != legend or not _number(answer['score'], 0, len(options) - 1)
                    or not math.isclose(answer['score'], sum(int(i) * p for i, p in probs.items()),
                                        rel_tol=0, abs_tol=.05)):
                raise ValueError('Invalid weighted score or legend')
    usage = value.get('usage')
    if usage is None:
        usage = {}
    if not isinstance(usage, dict) or usage.keys() - {'input_tokens', 'output_tokens', 'cost'}:
        raise ValueError('Invalid usage metadata')
    for name in ('input_tokens', 'output_tokens'):
        number = usage.get(name)
        if number is not None and (type(number) is not int or not 0 <= number <= 1_000_000_000):
            raise ValueError('Invalid token count')
    if usage.get('cost') is not None and not _number(usage['cost'], 0, 1_000_000):
        raise ValueError('Invalid cost')
    return {'answers': value['answers'], 'model': value['model'], 'provider': value['provider'],
            'request_id': value['id'], 'input_tokens': usage.get('input_tokens'),
            'output_tokens': usage.get('output_tokens'), 'cost_usd': usage.get('cost')}


def evaluate(root, project_id, purpose, state, questions, *, user_id='local', cache_context=None):
    """Evaluate only explicitly permitted state; callers apply confidence policy.

    Callers retain responsibility for source validity and selecting authorized
    content. Lower-confidence valid answers remain available for local fallback.
    """
    start = time.monotonic()
    result = {'status': 'unavailable', 'reason': '', 'answers': {}, 'model': None,
              'provider': None, 'request_id': None, 'provider_calls': 0,
              'input_tokens': None, 'output_tokens': None, 'cost_usd': None, 'elapsed_ms': 0,
              'http_status': None, 'cache': {'status': 'disabled'}}
    try:
        config = load_config(root)
        if config['status'] != 'ready':
            raise _Unavailable(config['status'], config['reason'])
        if (not isinstance(project_id, str) or project_id not in config['authorized_projects']
                or not isinstance(purpose, str) or purpose not in config['purposes']):
            raise _Unavailable('unavailable', 'This project and purpose are not authorized for Jev.')
        if not _text(user_id, 128):
            raise _Unavailable('invalid_request', 'A bounded user scope is required.')
        try:
            raw = _request(state, questions)
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            raise _Unavailable('invalid_request', 'Jev requires bounded, valid decision input.') from None
        from jev_prechecks import scan_sensitive
        sensitive = scan_sensitive({'state': state, 'questions': questions})
        if sensitive['blocked']:
            result['sensitivity'] = sensitive
            raise _Unavailable('sensitive_input', 'Local content checks held this request for review; no content was sent.')
        api_key = _key(config, root)
        if api_key is None:
            raise _Unavailable('unavailable', 'The configured Jev credential is unavailable.')
        cache_key = None
        if config.get('cache_enabled'):
            try:
                import jev_cache
                cache_key = jev_cache.identity(Path(root), project_id, user_id, purpose,
                    raw, cache_context, {'min_confidence': config['min_confidence']})
                cached = jev_cache.get(root, cache_key, config['cache_ttl_seconds'], questions)
                result['cache'] = {'status': 'hit' if cached else 'miss'}
                if cached:
                    origin = {key: cached.get(key) for key in ('request_id', 'input_tokens', 'output_tokens', 'cost_usd')}
                    result.update(cached, status='ok', reason='Reused a validated decision; caller must revalidate source evidence.',
                                  request_id=None, input_tokens=0, output_tokens=0, cost_usd=0,
                                  cache={'status': 'hit', 'origin_usage': origin})
                    return result
            except (OSError, ValueError, TypeError, TimeoutError, OverflowError, RecursionError, StorageLimitError):
                result['cache'] = {'status': 'unavailable'}
                cache_key = None
        _reserve_request(root, config['max_requests_per_day'])
        result['provider_calls'] = 1
        try:
            response = _post(raw, api_key, config['timeout_seconds'])
        except HTTPError as exc:
            if type(exc.code) is int and 100 <= exc.code <= 599:
                result['http_status'] = exc.code
            exc.close()
            raise _Unavailable('error', 'The Jev endpoint rejected the request.') from None
        except (OSError, URLError, HTTPException, TimeoutError):
            raise _Unavailable('error', 'The Jev request failed; billing may be unknown.') from None
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            raise _Unavailable('invalid_response', 'The Jev response could not be validated.') from None
        try:
            result['http_status'] = 200
            validated = _response(response, questions)
        except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            raise _Unavailable('invalid_response', 'The Jev response could not be validated.') from None
        result.update(validated, status='ok', reason='Jev returned validated decisions.')
        if cache_key:
            try:
                jev_cache.put(root, cache_key, _json(response), config['cache_ttl_seconds'])
                result['cache']['stored'] = True
            except (OSError, ValueError, TypeError, TimeoutError, OverflowError, RecursionError, StorageLimitError):
                result['cache'].update(stored=False)
    except _Unavailable as exc:
        result.update(status=exc.status, reason=exc.reason)
    finally:
        result['elapsed_ms'] = round((time.monotonic() - start) * 1000, 3)
    return result
