"""Render reconciled usage and allocation as TSV."""


def render_report(usage, plan):
    slots = {row['id']: row['slots'] for row in plan['allocations']}
    lines = ['provider\tattempts\tfailures\ttokens\tslots']
    attempts = failures = tokens = assigned = 0
    for row in usage:
        count = slots.get(row['provider'], 0)
        shown = str(row['tokens']) if row['tokens'] else '?'
        lines.append('\t'.join([row['provider'], str(row['attempts']),
            str(row['failures']), shown, str(count)]))
        attempts += row['attempts']
        failures += row['failures']
        tokens += row['tokens'] or 0
        assigned += count
    lines.append('TOTAL\t' + '\t'.join(map(str, [attempts, failures, tokens, assigned])))
    lines.append('UNASSIGNED\t' + str(plan['remaining']))
    return '\n'.join(lines)
