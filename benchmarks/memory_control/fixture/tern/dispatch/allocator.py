"""Assign finite provider capacity."""


def allocate_slots(requests, slots):
    remaining = slots
    rows = []
    total_weight = sum(request['weight'] for request in requests)
    for request in requests:
        count = min(request['limit'], int(slots * request['weight'] / total_weight))
        rows.append({'id': request['id'], 'slots': count})
        remaining -= count
    return {'allocations': rows, 'remaining': remaining}
