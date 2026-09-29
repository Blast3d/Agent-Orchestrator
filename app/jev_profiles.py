"""Task-role preferences for bounded evidence ranking, independent of worker identity."""

PROFILE_NEEDS = {
    'general': 'Current decisions, constraints, and evidence directly useful for this query.',
    'implementation': 'Interfaces, implementation constraints, and known fixes.',
    'review': 'Prior failures, relevant tests, and rejected approaches.',
    'research': 'Supporting evidence, uncertainty, and alternatives.',
    'handoff': 'Decisions, completed work, remaining work, and current constraints.',
}
PROFILES = tuple(PROFILE_NEEDS)
CATEGORY_PROFILES = {
    'coding': 'implementation', 'implementation': 'implementation', 'debug': 'implementation',
    'review': 'review', 'audit': 'review', 'research': 'research', 'handoff': 'handoff',
}


def validate_profile(profile):
    if not isinstance(profile, str) or profile not in PROFILE_NEEDS:
        raise ValueError('Memory profile must be general, implementation, review, research, or handoff.')
    return profile


def select_profile(category=None, explicit=None):
    if explicit is not None:
        return validate_profile(explicit)
    category = category.strip().casefold() if isinstance(category, str) else ''
    return CATEGORY_PROFILES.get(category, 'general')
