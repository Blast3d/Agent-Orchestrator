"""Count contributing model families from reviewed, retained execution evidence."""
import math

MINIMUM_FAMILIES = 2
PREFERRED_FAMILIES = 3
SCHEMA_VERSION = 1

_OFFICIAL = {
    "codex": "OpenAI",
    "claude": "Anthropic",
    "grok": "xAI",
    "gemini": "Google",
}
_LEAD_OWNERS = {"astra": "OpenAI", "sol": "OpenAI", "fable": "Anthropic"}
_CROSS = {
    "OpenAI": ("claude", "grok", "gemini"),
    "Anthropic": ("codex", "grok", "gemini"),
}


def _norm(value):
    return str(value or "").strip().lower()


def _model_family(model_id):
    text = _norm(model_id)
    if not text:
        return None
    hits = set()
    if "claude" in text or text in {"opus", "sonnet", "haiku"}:
        hits.add("Anthropic")
    if "gemini" in text:
        hits.add("Google")
    if "grok" in text:
        hits.add("xAI")
    if text.startswith("gpt-") or text.startswith(("o1", "o3", "o4")):
        hits.add("OpenAI")
    return next(iter(hits)) if len(hits) == 1 else None


def family_for_worker(worker, models=()):
    name = _norm(worker)
    if name in {"jev", "local-chat"} or not name:
        return None
    observed = [_model_family(item) for item in (models or ())]
    if name == "vscode-copilot":
        if not observed or any(item is None for item in observed):
            return None
        unique = set(observed)
        return next(iter(unique)) if len(unique) == 1 else None
    official = _OFFICIAL.get(name)
    if official is None:
        return None
    if not observed:
        return official
    if any(item is None or item != official for item in observed):
        return None
    return official


def plan(owner):
    lead = _LEAD_OWNERS.get(_norm(owner))
    if lead is None:
        raise ValueError("unknown lead owner: %r" % (owner,))
    return {
        "schema_version": SCHEMA_VERSION,
        "minimum_families": MINIMUM_FAMILIES,
        "preferred_families": PREFERRED_FAMILIES,
        "lead_family": lead,
        "cross_family_workers": list(_CROSS[lead]),
    }


def validate_plan(value, owner):
    expected = plan(owner)
    if not isinstance(value, dict):
        raise ValueError("plan must be a mapping")
    if set(value) != set(expected):
        raise ValueError("plan fields must match the required policy")
    for key in ("schema_version", "minimum_families", "preferred_families", "lead_family"):
        if type(value.get(key)) is not type(expected[key]) or value.get(key) != expected[key]:
            raise ValueError("immutable plan field mismatch: %s" % key)
    workers = value.get("cross_family_workers")
    if not isinstance(workers, list) or not workers:
        raise ValueError("cross_family_workers must be a unique nonempty list")
    names = [_norm(item) for item in workers]
    if len(names) != len(set(names)) or any(item not in _OFFICIAL for item in names):
        raise ValueError("unsupported or duplicate cross-family worker")
    families = {_OFFICIAL[item] for item in names}
    if expected["lead_family"] in families or not families:
        raise ValueError("cross_family_workers must cover at least one other family")
    normalized = dict(expected)
    normalized["cross_family_workers"] = names
    return normalized


def _worker_from_actor(actor):
    if not isinstance(actor, dict):
        return None
    ident = str(actor.get("id") or "")
    if ident.startswith("worker:"):
        return ident.split(":", 1)[1]
    return None


def _positive(value):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _delegation_map(ledger, hosted):
    mapped = {}
    for event in (ledger or {}).get("activity") or []:
        if not isinstance(event, dict):
            continue
        if event.get("kind") != "delegation":
            continue
        task = event.get("task_id")
        agent = event.get("agent_id")
        observed = hosted.get(task, {}).get('event', {})
        if (task and agent and observed.get('task_id') == task
                and observed.get('kind') == 'delegation'
                and all(event.get(key) == observed.get(key) for key in
                        ('status', 'actual_models', 'input_tokens', 'output_tokens'))):
            mapped.setdefault(task, set()).add(agent)
    return mapped


def _hosted_family(job_id, record, item, delegations):
    if not isinstance(record, dict) or record.get("synthetic") is True:
        return None
    actor = record.get("actor")
    event = record.get("event")
    if not isinstance(actor, dict) or not isinstance(event, dict):
        return None
    if event.get("status") != "succeeded":
        return None
    worker = _worker_from_actor(actor)
    if worker is None or _norm(worker) == "jev":
        return None
    models = event.get("actual_models") or ()
    family = family_for_worker(worker, models)
    if family is None:
        return None
    allocated = {
        alloc.get("agent_id")
        for alloc in item.get("allocations") or []
        if isinstance(alloc, dict) and _positive(alloc.get('percent'))
    }
    if allocated & delegations.get(job_id, set()):
        return family
    return None


def _native_lead(owner, ledger, hosted_actors):
    lead = plan(owner)["lead_family"]
    by_id = {
        row.get("id"): row
        for row in (ledger or {}).get("contributors") or []
        if isinstance(row, dict)
    }
    for item in (ledger or {}).get("work_items") or []:
        if not _retained(item):
            continue
        for alloc in item.get("allocations") or []:
            if not isinstance(alloc, dict) or not _positive(alloc.get('percent')):
                continue
            actor = by_id.get(alloc.get("agent_id"))
            if not isinstance(actor, dict):
                continue
            if actor.get('id') in hosted_actors or _worker_from_actor(actor) is not None:
                continue
            claimed = actor.get("provider") or family_for_worker(actor.get("name") or "")
            if claimed == lead:
                return lead
    return None


def _retained(item):
    return (isinstance(item, dict) and item.get('status') == 'accepted'
            and item.get('category') != 'memory-curation' and _positive(item.get('weight')))


def coverage(owner, ledger, hosted, *, native_capture=False):
    lead = plan(owner)["lead_family"]
    families = set()
    hosted = hosted if isinstance(hosted, dict) else {}
    delegations = _delegation_map(ledger, hosted)
    for item in (ledger or {}).get("work_items") or []:
        if not _retained(item):
            continue
        job_id = item.get("id")
        family = _hosted_family(job_id, hosted.get(job_id), item, delegations)
        if family:
            families.add(family)
    if native_capture is True:
        hosted_actors = {record['actor']['id'] for record in hosted.values()}
        hosted_actors.update(agent for aliases in delegations.values() for agent in aliases)
        native = _native_lead(owner, ledger, hosted_actors)
        if native:
            families.add(native)
    ordered = sorted(families)
    count = len(ordered)
    warnings = []
    if count < MINIMUM_FAMILIES:
        warnings.append(
            "minimum families unmet: observed %s, required %s" % (count, MINIMUM_FAMILIES)
        )
    if count < PREFERRED_FAMILIES:
        warnings.append("Prefer three contributing AI families; observed %s. The hard minimum remains two." % count)
    return {
        "schema_version": SCHEMA_VERSION,
        "minimum_families": MINIMUM_FAMILIES,
        "preferred_families": PREFERRED_FAMILIES,
        "lead_family": lead,
        "families": ordered,
        "family_count": count,
        "minimum_met": count >= MINIMUM_FAMILIES,
        "preferred_met": count >= PREFERRED_FAMILIES,
        "warnings": warnings,
    }


def require_coverage(owner, ledger, hosted, *, native_capture=False):
    result = coverage(owner, ledger, hosted, native_capture=native_capture)
    if not result["minimum_met"]:
        raise ValueError(
            "minimum families unmet: observed %s %s, required %s"
            % (result["family_count"], result["families"], MINIMUM_FAMILIES)
        )
    return result
