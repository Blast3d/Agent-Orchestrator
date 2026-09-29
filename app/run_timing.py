"""Content-free timing rollup; parallel worker time is never run wall time."""
from datetime import datetime
from task_performance import number


def _instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.timestamp() if result.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return None


def summarize(records):
    execution, recall, intervals = [], [], []
    considered = 0
    for row in records:
        if row.get('imported_completed_artifact') is True:
            continue
        considered += 1
        phases = row.get('phase_durations_ms')
        phases = phases if isinstance(phases, dict) else {}
        duration = number(phases.get('provider_execution'))
        if duration is not None:
            execution.append(duration / 1000)
        lookup = number(phases.get('memory_lookup'))
        if lookup is not None:
            recall.append(lookup / 1000)
        a, b = _instant(row.get('started_at')), _instant(row.get('ended_at'))
        if a is None or b is None or b <= a:
            continue
        # Wall-clock intervals locate overlap only when they agree with measured
        # elapsed time. An adjusted system clock must not invent concurrency.
        if duration is not None and abs((b - a) - duration / 1000) > max(5, duration / 1000 * .1):
            continue
        intervals.append((a, b))
    events = sorted((moment, change) for a, b in intervals for moment, change in ((a, 1), (b, -1)))
    active = peak = 0
    for _, change in events:
        active += change
        peak = max(peak, active)
    execution_sum = number(sum(execution)) if execution else None
    recall_sum = number(sum(recall)) if recall else None
    return {'schema_version': 1, 'hosted_tasks': considered,
            'execution_samples': len(execution),
            'execution_sum_seconds': round(execution_sum, 3) if execution_sum is not None else None,
            'recall_samples': len(recall),
            'recall_sum_seconds': round(recall_sum, 3) if recall_sum is not None else None,
            'interval_samples': len(intervals),
            'observed_peak_workers': peak if intervals else None,
            'observed_span_seconds': round(max(b for _, b in intervals) - min(a for a, _ in intervals), 3) if intervals else None,
            'interpretation': 'Completed recorded intervals only; native sessions and unknown durations are excluded. Summed worker and recall time can overlap and are not run wall time. Overlap is observational, not a speedup measurement.'}
