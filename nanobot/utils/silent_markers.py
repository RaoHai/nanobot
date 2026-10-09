"""[LOCAL] Silent-marker convention shared by the agent loop, turn delivery,
and the channel manager.

Models signal "say nothing" by emitting a marker such as ``[SILENT]`` as the
entire message. Upstream has no such handling — keep this module when merging
upstream changes, and keep it in sync with ``.agent/gotchas.md``.
"""

from __future__ import annotations

SILENT_MARKERS = frozenset({"silent", "no_response", "no_reply", "skip"})

_EMPHASIS_WRAPPERS = ("**", "__", "``", "~~")

# Every full textual form a marker can take, used for stream-prefix holdback:
# a delta stream is held back while its accumulated text is still a prefix of
# any of these forms, so a marker never reaches the channel mid-stream.
_MARKER_FORMS: tuple[str, ...] = tuple(
    form
    for marker in SILENT_MARKERS
    for core in (marker, f"[{marker}]")
    for form in (core, *(f"{w}{core}{w}" for w in _EMPHASIS_WRAPPERS))
)


def _unwrap(content: str) -> str:
    text = content.strip()
    while len(text) >= 2 and text[0] in "*_`~" and text[-1] == text[0]:
        text = text[1:-1].strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text


def is_silent_marker(content: str) -> bool:
    """Return True if *content* consists solely of a stay-silent marker.

    Tolerant matcher: trims whitespace, strips markdown emphasis wrappers and
    one pair of square brackets, and ignores case — so ``SILENT``,
    ``[Silent]``, ``**[SILENT]**`` are all caught. Text that merely contains
    a marker among other content is NOT suppressed.
    """
    return _unwrap(content).casefold() in SILENT_MARKERS


def is_silent_marker_prefix(text: str) -> bool:
    """Return True while *text* could still grow into a complete marker.

    Used by turn delivery to hold back stream deltas: as long as the
    accumulated stream text is a prefix of some marker form, publishing it
    might leak a marker mid-stream, so callers should keep buffering. Once
    the text diverges from every form, it can never become a marker.
    """
    stripped = text.strip().casefold()
    if not stripped:
        return True
    if len(stripped) > max(len(form) for form in _MARKER_FORMS):
        return False
    return any(form.startswith(stripped) for form in _MARKER_FORMS)
