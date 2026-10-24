"""User text sanitizer (Req 28.5): strips all HTML/markup, idempotent."""
from __future__ import annotations

import nh3

_MAX_PASSES = 8


def sanitize_text(text: str) -> str:
    """Remove every HTML tag/attribute (and script/style content) from ``text``.

    nh3 is run to a fixed point so the result is idempotent: ``f(f(x)) == f(x)``.
    Remaining ``&``, ``<`` and ``>`` characters come back entity-escaped by nh3,
    which is safe for any HTML context. If no fixed point is reached, angle
    brackets are dropped as a last resort.
    """
    current = text
    for _ in range(_MAX_PASSES):
        cleaned = nh3.clean(current, tags=set(), attributes={}, strip_comments=True)
        if cleaned == current:
            return cleaned
        current = cleaned
    return nh3.clean(current.replace("<", "").replace(">", ""), tags=set(), attributes={})
