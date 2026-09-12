"""Execution-deadline policy helper for the local orchestration app.

Provides timeout_for_task(size, override=None) -> int, returning a hard
deadline in seconds. Replaces the old fixed 180s limit that prematurely
killed long coding replies.
"""

_DEFAULTS = {
    "tiny": 120,
    "small": 450,
    "medium": 900,
    "large": 1350,
}

_MIN_OVERRIDE = 30
_MAX_OVERRIDE = 1800


def _strict_int(value):
    """Return value as int, rejecting bool, float, str and nonfinite input."""
    if isinstance(value, bool):
        raise ValueError("override must not be a boolean")
    if not isinstance(value, int):
        raise ValueError("override must be an integer")
    return value


def timeout_for_task(size, override=None):
    """Compute the hard execution deadline in seconds.

    size must be one of: tiny, small, medium, large. override, when given,
    must be an integer from 30 through 1800 seconds inclusive.
    """
    if not isinstance(size, str) or size not in _DEFAULTS:
        raise ValueError("unknown task size: %r" % (size,))
    if override is None:
        return _DEFAULTS[size]
    override = _strict_int(override)
    if override < _MIN_OVERRIDE or override > _MAX_OVERRIDE:
        raise ValueError("override out of range: %d" % override)
    return override
