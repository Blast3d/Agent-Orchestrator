"""Count contributing model families from reviewed, retained execution evidence."""
import math
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

MINIMUM_FAMILIES = 3
PREFERRED_FAMILIES = 4
LEGACY_MINIMUM_FAMILIES = 2
LEGACY_PREFERRED_FAMILIES = 3
SCHEMA_VERSION = 3
SCHEMA_V2 = 2

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
_ROTATION = ("gemini", "grok", "codex", "claude")
_ALL_FAMILIES = ("OpenAI", "Anthropic", "xAI", "Google")
_PARTICIPATION = {
    "measure": "accepted_contribution_share",
    "mix_file": "team-mix.json",
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


def legacy_plan(owner):
    lead = _LEAD_OWNERS.get(_norm(owner))
    if lead is None:
        raise ValueError("unknown lead owner: %r" % (owner,))
    return {
        "schema_version": 1,
        "minimum_families": LEGACY_MINIMUM_FAMILIES,
        "preferred_families": LEGACY_PREFERRED_FAMILIES,
        "lead_family": lead,
        "cross_family_workers": list(_CROSS[lead]),
    }


def plan_v2(owner):
    result = legacy_plan(owner)
    result.update({
        "schema_version": SCHEMA_V2,
        "minimum_families": MINIMUM_FAMILIES,
        "preferred_families": PREFERRED_FAMILIES,
        "required_worker_families": [
            _OFFICIAL[worker] for worker in result["cross_family_workers"]
        ],
        "balance": {
            "measure": "accepted_hosted_tasks",
            "max_spread_ratio": 0.15,
            "min_spread": 1,
        },
    })
    return result


def plan(owner):
    result = plan_v2(owner)
    result["schema_version"] = SCHEMA_VERSION
    result.pop("balance", None)
    result["participation"] = dict(_PARTICIPATION)
    return result


def policy_for(owner, recorded=None):
    """The policy a run's startup records: a legacy receipt stays on its schema."""
    if isinstance(recorded, dict):
        version = recorded.get("schema_version")
        if version == 1:
            return legacy_plan(owner)
        if version == SCHEMA_V2:
            return plan_v2(owner)
    return plan(owner)


def validate_plan(value, owner):
    if not isinstance(value, dict):
        raise ValueError("plan must be a mapping")
    version = value.get("schema_version")
    if type(version) is not int or version not in (1, SCHEMA_V2, SCHEMA_VERSION):
        raise ValueError("immutable plan field mismatch: schema_version")
    if version == 1:
        expected = legacy_plan(owner)
    elif version == SCHEMA_V2:
        expected = plan_v2(owner)
    else:
        expected = plan(owner)
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
    if version >= SCHEMA_V2:
        if set(names) != set(expected["cross_family_workers"]):
            raise ValueError("cross_family_workers must cover every required worker family")
        required = value.get("required_worker_families")
        if not isinstance(required, list) or required != [_OFFICIAL[name] for name in names]:
            raise ValueError("required_worker_families must match cross_family_workers in order")
    if version == SCHEMA_V2:
        balance = value.get("balance")
        if not isinstance(balance, dict) or set(balance) != set(expected["balance"]):
            raise ValueError("balance fields must match the required policy")
        for key, target in expected["balance"].items():
            if type(balance[key]) is not type(target) or balance[key] != target:
                raise ValueError("immutable balance field mismatch: %s" % key)
    if version == SCHEMA_VERSION:
        participation = value.get("participation")
        if not isinstance(participation, dict) or set(participation) != set(expected["participation"]):
            raise ValueError("participation fields must match the required policy")
        for key, target in expected["participation"].items():
            if type(participation[key]) is not type(target) or participation[key] != target:
                raise ValueError("immutable participation field mismatch: %s" % key)
    result = dict(value)
    result["cross_family_workers"] = list(workers)
    if version >= SCHEMA_V2:
        result["required_worker_families"] = list(value["required_worker_families"])
    if version == SCHEMA_V2:
        result["balance"] = dict(value["balance"])
    if version == SCHEMA_VERSION:
        result["participation"] = dict(value["participation"])
    return result


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


def _dec(value):
    return Decimal(str(value))


def _pct(part, total):
    if total == 0:
        return 0.0
    quantized = (Decimal(100) * part / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return float(quantized)


def _mix_fields(mix):
    if not isinstance(mix, dict):
        raise ValueError("v3 coverage requires a validated mix")
    shares = mix.get("shares")
    tolerance = mix.get("tolerance")
    if not isinstance(shares, dict):
        raise ValueError("v3 coverage requires a validated mix")
    if not _positive(tolerance) and tolerance != 0:
        raise ValueError("v3 coverage requires a validated mix")
    return shares, tolerance


def _delegation_map(ledger, hosted):
    mapped = {}
    for event in (ledger or {}).get("activity") or []:
        if not isinstance(event, dict):
            continue
        if event.get("kind") != "delegation":
            continue
        task = event.get("task_id")
        agent = event.get("agent_id")
        record = hosted.get(task)
        observed = record.get("event") if isinstance(record, dict) else None
        if not isinstance(observed, dict):
            continue
        if (task and agent and observed.get("task_id") == task
                and observed.get("kind") == "delegation"
                and all(event.get(key) == observed.get(key) for key in
                        ("status", "actual_models", "input_tokens", "output_tokens"))):
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
        if isinstance(alloc, dict) and _positive(alloc.get("percent"))
    }
    if allocated & delegations.get(job_id, set()):
        return family
    return None


def _native_lead_actor(actor, lead, hosted_actors):
    if not isinstance(actor, dict):
        return False
    if actor.get("id") in hosted_actors or _worker_from_actor(actor) is not None:
        return False
    claimed = actor.get("provider") or family_for_worker(actor.get("name") or "")
    return claimed == lead


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
            if not isinstance(alloc, dict) or not _positive(alloc.get("percent")):
                continue
            actor = by_id.get(alloc.get("agent_id"))
            if _native_lead_actor(actor, lead, hosted_actors):
                return lead
    return None


def _retained(item):
    return (isinstance(item, dict) and item.get("status") == "accepted"
            and item.get("category") != "memory-curation" and _positive(item.get("weight")))


def _percent_sum(item):
    total = Decimal(0)
    for alloc in item.get("allocations") or []:
        if isinstance(alloc, dict) and _positive(alloc.get("percent")):
            total += _dec(alloc.get("percent"))
    return total


def _alloc_family(
        job_id, record, alloc, delegations, by_id, hosted_actors, native_capture, lead
):
    agent = alloc.get("agent_id")
    hosted_family = None
    if isinstance(record, dict) and record.get("synthetic") is not True:
        actor = record.get("actor")
        event = record.get("event")
        if isinstance(actor, dict) and isinstance(event, dict) and event.get("status") == "succeeded":
            worker = _worker_from_actor(actor)
            if worker is not None and _norm(worker) != "jev":
                hosted_family = family_for_worker(worker, event.get("actual_models") or ())
    if agent in delegations.get(job_id, set()) and hosted_family:
        return hosted_family
    if native_capture is True:
        actor = by_id.get(agent)
        if _native_lead_actor(actor, lead, hosted_actors):
            return lead
    return "unverified"


def coverage(owner, ledger, hosted, *, native_capture=False, policy=None, mix=None):
    policy = validate_plan(plan(owner) if policy is None else policy, owner)
    lead = policy["lead_family"]
    version = policy["schema_version"]
    legacy = version == 1
    required = [] if legacy else policy["required_worker_families"]
    counts = {family: 0 for family in required}
    families = set()
    counted_jobs = set()
    hosted = hosted if isinstance(hosted, dict) else {}
    delegations = _delegation_map(ledger, hosted)
    for item in (ledger or {}).get("work_items") or []:
        if not _retained(item):
            continue
        job_id = item.get("id")
        family = _hosted_family(job_id, hosted.get(job_id), item, delegations)
        if family:
            families.add(family)
            if family in counts and job_id not in counted_jobs:
                counts[family] += 1
                counted_jobs.add(job_id)
    if native_capture is True:
        hosted_actors = {
            record["actor"].get("id")
            for record in hosted.values()
            if isinstance(record, dict) and isinstance(record.get("actor"), dict)
        }
        hosted_actors.update(agent for aliases in delegations.values() for agent in aliases)
        native = _native_lead(owner, ledger, hosted_actors)
        if native:
            families.add(native)
    else:
        hosted_actors = set()
    ordered = sorted(families)
    count = len(ordered)
    warnings = []
    participation = None
    if legacy:
        minimum_met = count >= LEGACY_MINIMUM_FAMILIES
        preferred_met = count >= LEGACY_PREFERRED_FAMILIES
        if not minimum_met:
            warnings.append(
                "minimum families unmet: observed %s, required %s"
                % (count, LEGACY_MINIMUM_FAMILIES)
            )
        if not preferred_met:
            warnings.append(
                "Prefer three contributing AI families; observed %s. "
                "The hard minimum remains two." % count
            )
    elif version == SCHEMA_V2:
        missing = [family for family in required if counts[family] == 0]
        highest = max(counts.values())
        lowest = min(counts.values())
        spread = highest - lowest
        allowed = max(
            policy["balance"]["min_spread"],
            math.ceil(policy["balance"]["max_spread_ratio"] * highest),
        )
        balanced = spread <= allowed
        minimum_met = not missing
        preferred_met = minimum_met and lead in families
        workers = {
            _OFFICIAL[_norm(worker)]: _norm(worker)
            for worker in policy["cross_family_workers"]
        }
        for family in missing:
            warnings.append("assign accepted work to %s (%s)" % (workers[family], family))
        if not balanced:
            needs = [
                "%s (%s)" % (workers[family], family)
                for family in required if counts[family] == lowest
            ]
            warnings.append(
                "worker task balance unmet: counts %s; spread %s exceeds allowed %s; "
                "assign more accepted work to %s"
                % (counts, spread, allowed, ", ".join(needs))
            )
        if minimum_met and not preferred_met:
            warnings.append("Prefer retained lead-family work from %s." % lead)
    else:
        shares, tolerance = _mix_fields(mix)
        missing = [family for family in required if counts[family] == 0]
        minimum_met = not missing
        workers = {
            _OFFICIAL[_norm(worker)]: _norm(worker)
            for worker in policy["cross_family_workers"]
        }
        by_id = {
            row.get("id"): row
            for row in (ledger or {}).get("contributors") or []
            if isinstance(row, dict)
        }
        if native_capture is True and not hosted_actors:
            hosted_actors = {
                record["actor"].get("id")
                for record in hosted.values()
                if isinstance(record, dict) and isinstance(record.get("actor"), dict)
            }
            hosted_actors.update(agent for aliases in delegations.values() for agent in aliases)
        weights = {family: Decimal(0) for family in _ALL_FAMILIES}
        unverified = Decimal(0)
        total_weight = Decimal(0)
        for item in (ledger or {}).get("work_items") or []:
            if not _retained(item):
                continue
            item_weight = _dec(item.get("weight"))
            total_weight += item_weight
            percent_sum = _percent_sum(item)
            job_id = item.get("id")
            record = hosted.get(job_id)
            if percent_sum == 0:
                unverified += item_weight
                continue
            for alloc in item.get("allocations") or []:
                if not isinstance(alloc, dict) or not _positive(alloc.get("percent")):
                    continue
                share = item_weight * _dec(alloc.get("percent")) / percent_sum
                family = _alloc_family(
                    job_id, record, alloc, delegations, by_id,
                    hosted_actors, native_capture, lead,
                )
                if family in weights:
                    weights[family] += share
                else:
                    unverified += share
        targets = {family: float(shares.get(family, 0)) for family in _ALL_FAMILIES}
        actual = {family: _pct(weights[family], total_weight) for family in _ALL_FAMILIES}
        unverified_pct = _pct(unverified, total_weight)
        deltas = {family: round(actual[family] - targets[family], 1) for family in _ALL_FAMILIES}
        within = {
            family: abs(actual[family] - targets[family]) <= float(tolerance)
            for family in _ALL_FAMILIES
        }
        if total_weight == 0:
            met = False
            warnings.append(
                "no accepted contribution weight; cannot meet participation targets"
            )
        else:
            met = minimum_met and all(within.values())
        for family in missing:
            warnings.append("assign accepted work to %s (%s)" % (workers[family], family))
        if total_weight > 0:
            for family in _ALL_FAMILIES:
                if within[family]:
                    continue
                if actual[family] > targets[family]:
                    if family == lead:
                        warnings.append(
                            "%s actual share %s exceeds target %s by more than tolerance %s; "
                            "delegate more"
                            % (family, actual[family], targets[family], tolerance)
                        )
                    else:
                        warnings.append(
                            "%s actual share %s exceeds target %s by more than tolerance %s"
                            % (family, actual[family], targets[family], tolerance)
                        )
                else:
                    warnings.append(
                        "%s actual share %s is under target %s by more than tolerance %s; "
                        "assign more accepted work"
                        % (family, actual[family], targets[family], tolerance)
                    )
        preferred_met = minimum_met and lead in families
        participation = {
            "targets": targets,
            "actual": actual,
            "unverified_pct": unverified_pct,
            "tolerance": float(tolerance) if not isinstance(tolerance, float) else tolerance,
            "within": within,
            "deltas": deltas,
            "met": met,
            "total_weight": float(total_weight),
        }
        try:
            participation["tolerance"] = int(tolerance) if int(tolerance) == float(tolerance) else float(tolerance)
        except (TypeError, ValueError, OverflowError):
            participation["tolerance"] = float(tolerance)
    result = {
        "schema_version": policy["schema_version"],
        "minimum_families": policy["minimum_families"],
        "preferred_families": policy["preferred_families"],
        "lead_family": lead,
        "families": ordered,
        "family_count": count,
        "minimum_met": minimum_met,
        "preferred_met": preferred_met,
        "warnings": warnings,
    }
    if not legacy:
        result.update({
            "required_worker_families": list(required),
            "worker_task_counts": counts,
            "missing_worker_families": missing,
        })
        if version == SCHEMA_V2:
            result["balance"] = {
                "counts": dict(counts),
                "spread": spread,
                "allowed_spread": allowed,
                "balanced": balanced,
            }
        if version == SCHEMA_VERSION:
            result["participation"] = participation
    return result


def require_coverage(owner, ledger, hosted, *, native_capture=False, policy=None, mix=None):
    result = coverage(
        owner, ledger, hosted, native_capture=native_capture, policy=policy, mix=mix
    )
    if result["schema_version"] == 1:
        if not result["minimum_met"]:
            raise ValueError(
                "minimum families unmet: observed %s %s, required %s"
                % (result["family_count"], result["families"], LEGACY_MINIMUM_FAMILIES)
            )
    elif result["schema_version"] == SCHEMA_V2:
        if not result["minimum_met"] or not result["balance"]["balanced"]:
            raise ValueError("; ".join(result["warnings"]))
    elif not result["minimum_met"] or not result["participation"]["met"]:
        raise ValueError("; ".join(result["warnings"]))
    return result


def next_worker(owner, counts, *, eligible=None, policy=None, mix=None):
    policy = validate_plan(plan(owner) if policy is None else policy, owner)
    candidates = {_norm(worker) for worker in policy["cross_family_workers"]}
    if eligible is not None:
        candidates.intersection_update(_norm(worker) for worker in eligible)
    candidates = [
        worker for worker in _ROTATION
        if worker in candidates and _OFFICIAL[worker] != policy["lead_family"]
    ]
    if not candidates:
        return None
    if policy["schema_version"] != SCHEMA_VERSION or mix is None:
        return min(candidates, key=lambda worker: counts.get(worker, 0))
    shares = mix.get("shares") if isinstance(mix, dict) else None
    if not isinstance(shares, dict):
        return min(candidates, key=lambda worker: counts.get(worker, 0))
    required = [
        worker for worker in _ROTATION
        if worker in {_norm(item) for item in policy["cross_family_workers"]}
        and _OFFICIAL[worker] != policy["lead_family"]
    ]
    target_sum = sum(Fraction(str(shares.get(_OFFICIAL[worker], 0))) for worker in required)
    count_sum = max(1, sum(int(counts.get(worker, 0) or 0) for worker in required))

    def deficit(worker):
        if target_sum == 0:
            return -Fraction(int(counts.get(worker, 0) or 0), count_sum)
        target = Fraction(str(shares.get(_OFFICIAL[worker], 0)))
        return target / target_sum - Fraction(int(counts.get(worker, 0) or 0), count_sum)

    return min(candidates, key=lambda worker: (-deficit(worker), _ROTATION.index(worker)))
