"""Terminal capability sniffing — specifically: does this terminal apply
the Unicode bidi algorithm and Arabic cursive joining itself?

There is no in-band protocol for the app to ask. We sniff well-known
environment variables for the small handful of terminals known to do
bidi, and default to "no" (which is the correct conservative call for
the long tail: Windows Terminal pre-1.20, xterm, Alacritty, Kitty,
GNOME Terminal pre-3.40, etc.).

The user can override either way with ``QURAN_TUI_RTL=on|off|auto``.
"""

from __future__ import annotations

import os
from typing import Literal

RtlMode = Literal["auto", "on", "off"]


# Terminals that ship a working bidi algorithm by default.
_KNOWN_BIDI_TERMINALS = {
    "konsole",       # KDE Konsole
    "wezterm",       # WezFurlong's WezTerm
    "foot",          # foot (Wayland)
    "gnome-terminal",  # GNOME Terminal (newer)
}


def _env_match(value: str, needles: set[str]) -> bool:
    if not value:
        return False
    v = value.lower()
    return any(n in v for n in needles)


def detect_bidi_support() -> bool:
    """Best-effort: does the current terminal apply bidi itself?

    Cleared overrides aside, returns True only for terminals we can
    positively identify as bidi-capable. Everything else assumed False.
    """
    if os.environ.get("KONSOLE_VERSION"):
        return True
    if os.environ.get("WEZTERM_EXECUTABLE") or os.environ.get("WEZTERM_PANE"):
        return True
    term_program = os.environ.get("TERM_PROGRAM", "")
    if _env_match(term_program, _KNOWN_BIDI_TERMINALS):
        return True
    return False


def resolve_rtl_mode(env_value: str | None = None) -> RtlMode:
    """Read ``QURAN_TUI_RTL`` (or the passed-in value) into a normalised mode.

    - ``"on" | "native" | "yes" | "1"``  → ``"on"``  (terminal handles bidi)
    - ``"off" | "reshape" | "no" | "0"`` → ``"off"`` (we always reshape)
    - anything else (or absent)         → ``"auto"``
    """
    raw = (env_value if env_value is not None else os.environ.get("QURAN_TUI_RTL", "")).strip().lower()
    if raw in {"on", "native", "yes", "1", "true"}:
        return "on"
    if raw in {"off", "reshape", "no", "0", "false"}:
        return "off"
    return "auto"


def should_reshape_arabic(mode: RtlMode | None = None) -> bool:
    """Decide whether the app should reshape + bidi-reorder Arabic strings
    before display."""
    resolved = mode if mode is not None else resolve_rtl_mode()
    if resolved == "on":
        return False  # terminal does it natively — don't double-reverse
    if resolved == "off":
        return True   # user forced reshaping
    # auto
    return not detect_bidi_support()
