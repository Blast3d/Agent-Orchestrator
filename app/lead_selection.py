"""The user's lead-orchestrator switch: Claude, ASTRA or Sol.

The switch chooses who leads new runs and who is the runner-up at the
5-percent handoff. It never moves an existing run: live leadership changes only
through the ownership-checked `lead prepare`/`lead claim` commands.
"""
import json
import re

from paths import ROOT
from task_store import timestamp, write_json
from usage_guard import file_lock

# The Claude slot keeps its legacy `fable` owner key; the model comes from the
# coordinator policy (currently Opus 5.5) and honors paused model families.
LEADS = {
    'astra': {'owner': 'astra', 'label': 'ASTRA', 'app': 'Codex', 'quota_worker': 'codex',
              'model': 'gpt-6-astra', 'model_label': 'GPT-6-Astra'},
    'sol': {'owner': 'sol', 'label': 'Sol', 'app': 'Codex', 'quota_worker': 'codex',
            'model': 'gpt-6-sol', 'model_label': 'GPT-6-Sol'},
    'claude': {'owner': 'fable', 'label': 'Claude', 'app': 'Claude Code', 'quota_worker': 'claude',
               'model': None, 'model_label': None},
}
DEFAULT_LEAD = 'astra'
CODEX_LEADS = ('astra', 'sol')


def lead_for_owner(owner):
    for lead, option in LEADS.items():
        if option['owner'] == owner:
            return lead
    raise ValueError('Unknown coordinator owner')


def _config_path():
    return ROOT / 'config/workers.json'


def _read():
    path = _config_path()
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Lead selection settings could not be read') from exc
    if not isinstance(config, dict):
        raise ValueError('Lead selection settings must contain an object')
    handoff = config.get('coordinator_handoff', {})
    if not isinstance(handoff, dict):
        raise ValueError('Coordinator handoff settings must be an object')
    return config, handoff


def _claude_model_label(model):
    """claude-opus-5-5 -> Opus 5.5; aliases such as opus -> Opus."""
    base = model.lower().split('[', 1)[0].removeprefix('claude-')
    match = re.fullmatch(r'([a-z]+)((?:-\d+)*)(?:-\d{8})?', base)
    if not match:
        return model
    version = '.'.join(match[2].strip('-').split('-')) if match[2] else ''
    return (match[1].title() + ' ' + version).strip()


def _options():
    from claude_models import select_model
    options = []
    for lead, option in LEADS.items():
        option = dict(option, id=lead)
        if lead == 'claude':
            try:
                option['model'] = select_model('coordinator')
                option['model_label'] = _claude_model_label(option['model'])
                option['available'] = True
            except ValueError as exc:
                option.update(model=None, model_label='unavailable', available=False, reason=str(exc))
        else:
            option['available'] = True
        option['display'] = option['label'] + ' (' + option['app'] + ' · ' + str(option['model_label']) + ')'
        options.append(option)
    return options


def _state(handoff):
    lead = handoff.get('lead')
    lead = lead if lead in LEADS else DEFAULT_LEAD
    codex = handoff.get('codex_lead')
    codex = codex if codex in CODEX_LEADS else (lead if lead in CODEX_LEADS else DEFAULT_LEAD)
    # ASTRA and Sol share the Codex allowance, so a Codex lead hands off to Claude;
    # Claude hands back to the Codex lead the user chose last.
    runner_up = codex if lead == 'claude' else 'claude'
    return lead, codex, runner_up


def describe():
    """Read-only summary for the CLI, viewer and startup packets."""
    _, handoff = _read()
    lead, codex, runner_up = _state(handoff)
    return {'lead': lead, 'owner': LEADS[lead]['owner'], 'runner_up': runner_up,
            'runner_up_owner': LEADS[runner_up]['owner'], 'codex_lead': codex,
            'selected_at': handoff.get('lead_selected_at'), 'options': _options(),
            'applies_to': 'new runs and the runner-up handoff target; existing runs change only through lead prepare/claim',
            'model_calls': 0}


def select_lead(choice):
    """Persist the switch atomically; other configuration fields are preserved."""
    if not isinstance(choice, str) or choice not in LEADS:
        raise ValueError('Choose claude, astra or sol')
    if choice == 'claude':
        from claude_models import select_model
        select_model('coordinator')  # Refuse a paused or invalid Claude coordinator model.
    path = _config_path()
    with file_lock(path.with_name(path.name + '.lock')):
        config, handoff = _read()
        handoff = dict(handoff, lead=choice, lead_selected_at=timestamp())
        if choice in CODEX_LEADS:
            handoff['codex_lead'] = choice
        config['coordinator_handoff'] = handoff
        write_json(path, config)
    return describe()


def run_owner(lead=None):
    """Owner key for a new run: an explicit lead, else the switch."""
    if lead is None:
        lead = describe()['lead']
    if lead not in LEADS:
        raise ValueError('Choose claude, astra or sol as the run lead')
    return LEADS[lead]['owner']
