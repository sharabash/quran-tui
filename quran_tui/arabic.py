"""Arabic text shaping + bidi reordering for LTR-only terminals.

Most terminals — including the WSL-side Windows Terminal — don't apply the
Unicode bidi algorithm. They lay out codepoints strictly left-to-right and
don't perform Arabic cursive joining (initial/medial/final/isolated glyph
selection). The result: Arabic strings appear visually reversed AND with
disconnected letterforms.

The fix is the standard two-step pipeline used by every "Arabic in a
text-mode app" library:

1. :mod:`arabic_reshaper` substitutes each character with its joining
   variant ("ك" → "ﻛ" in medial position, etc.).
2. :mod:`bidi.algorithm.get_display` runs the Unicode bidi algorithm on
   the reshaped string, producing a left-to-right sequence whose visual
   order matches what a bidi-aware renderer would draw.

We auto-detect Arabic by scanning for codepoints in the Arabic Unicode
block; non-Arabic strings (and strings with no Arabic at all) pass
through untouched so we don't perturb English / Latin / digits.
"""

from __future__ import annotations

import re

import arabic_reshaper
from bidi.algorithm import get_display

# Arabic + Arabic Supplement + Arabic Extended-A/B + Arabic Presentation
# Forms. Quran-com data typically lives in the basic block + presentation
# forms; we err on the side of "if any of these appear, reshape".
_ARABIC_RANGES = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)

_ARABIC_RE = re.compile(
    "|".join(f"[\\u{lo:04x}-\\u{hi:04x}]" for lo, hi in _ARABIC_RANGES)
)


def contains_arabic(text: str) -> bool:
    """True if ``text`` has at least one character in the Arabic Unicode block."""
    if not text:
        return False
    return _ARABIC_RE.search(text) is not None


def render_for_terminal(text: str, *, force: bool | None = None) -> str:
    """Convert raw Arabic (or mixed) text into a form that looks correct in a
    plain LTR terminal.

    - Pure ASCII / non-Arabic input is returned unchanged (fast path).
    - Strings containing any Arabic codepoint get reshaped + bidi'd, UNLESS
      :func:`quran_tui.terminal_caps.should_reshape_arabic` indicates the
      terminal handles bidi itself — in which case we pass the raw text
      through and let the terminal's renderer do its job.
    - ``force=True`` always reshapes (useful for unit tests and screenshot
      generation regardless of the runtime terminal).
    - ``force=False`` always skips reshape.
    """
    if not _should_reshape(text, force):
        return text
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def render_for_terminal_wrapped(
    text: str, width: int, *, force: bool | None = None
) -> str:
    """Reshape Arabic for an LTR terminal but preserve logical reading order
    across wrapped lines.

    Why this exists: when you reshape + bidi-reverse a long Arabic sentence
    in one go you get a single LTR-visual string whose *first* characters
    are the *last* words of the original. If Textual then wraps that string
    at a column boundary, the top line ends up containing the END of the
    sentence and the bottom line contains the BEGINNING — i.e. the sentence
    reads bottom-up.

    The fix is to wrap the *logical* string at word boundaries into chunks
    that each fit ``width`` columns, then reshape each chunk independently.
    The lines are joined top-to-bottom in logical order, so reading top-down
    matches reading the original right-to-left.

    ``width`` is in terminal columns. We try to keep each reshaped line
    under that, but a single word longer than ``width`` is emitted as-is
    rather than character-broken (we'd rather overflow than corrupt the
    glyph shaping).
    """
    if not _should_reshape(text, force) or width <= 0:
        return text
    # Whitespace-separated tokens preserve word boundaries and digit groups.
    # The Arabic codepoint U+0020 is a regular space; quran.com text uses it
    # plus the occasional NBSP — both fall out of split() naturally.
    words = text.split()
    if not words:
        return text
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        candidate_len = len(word) if not current else current_len + 1 + len(word)
        if current and candidate_len > width:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len = candidate_len
    if current:
        lines.append(" ".join(current))
    return "\n".join(
        get_display(arabic_reshaper.reshape(line)) for line in lines
    )


def _should_reshape(text: str, force: bool | None) -> bool:
    if not text or not contains_arabic(text):
        return False
    if force is True:
        return True
    if force is False:
        return False
    from .terminal_caps import should_reshape_arabic

    return should_reshape_arabic()
