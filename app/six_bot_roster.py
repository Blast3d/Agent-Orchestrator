"""Fixed contestant identities for the audited medium memory benchmark."""
CONDITIONS = ('authoring', 'audit', 'team-cold', 'team-warm')
ROSTER = (
    ('openai_a', 'OpenAI', 'normalization'),
    ('openai_b', 'OpenAI', 'ledger'),
    ('claude_a', 'Anthropic', 'dependencies'),
    ('claude_b', 'Anthropic', 'allocation'),
    ('grok_a', 'xAI', 'intervals'),
    ('grok_b', 'xAI', 'reporting'),
)


def declaration():
    return [dict(id=actor, provider=provider, role=role,
                 model='gpt-6-astra' if provider=='OpenAI' else 'opus' if provider=='Anthropic' else 'configured grok-4.6',
                 effort='low' if provider=='xAI' else 'medium') for actor,provider,role in ROSTER]
