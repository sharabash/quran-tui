"""Domain types shared across the quran-tui app."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Track — the unit of playback. Origins vary (a surah recorded by a reciter,
# a salah recording from a haram, an imam's Tarawih, etc.) so the dataclass
# keeps a free-form ``kind`` and a couple of source-specific metadata fields.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Track:
    """A playable audio entry — surah, salah, taraweeh, etc."""

    track_id: str               # globally-unique handle inside the source
    title: str                  # e.g. "Al-Fatihah" or "Fajr — 2026-05-24"
    subtitle: str = ""          # reciter name / imam name / date
    extra: str = ""             # location, prayer, format … displayed in dim
    stream_url: str = ""        # direct mp3 URL; empty until resolved
    duration_seconds: int | None = None
    source: str = ""            # "quranicaudio" / "haramain"
    raw: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def display_subtitle(self) -> str:
        bits = [self.subtitle]
        if self.extra:
            bits.append(self.extra)
        return " · ".join(b for b in bits if b)

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_seconds)


# ---------------------------------------------------------------------------
# Browse-tree nodes. Each source returns a flat list of nodes from its
# `browse(path)` method; selecting one either drills into another node (a
# `Category`) or yields a Track (a `Leaf`).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Category:
    """A clickable node that leads to another listing."""

    key: str                    # opaque id consumed by the source
    title: str
    subtitle: str = ""
    count: int | None = None


# Helpers ---------------------------------------------------------------------


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "--:--"
    s = int(seconds)
    if s < 0:
        return "--:--"
    m, s = divmod(s, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def track_matches_filter(track: Track, needle: str) -> bool:
    """Case-insensitive substring match on title, subtitle, and extra."""
    if not needle:
        return True
    n = needle.strip().lower()
    if not n:
        return True
    return (
        n in track.title.lower()
        or n in track.subtitle.lower()
        or n in track.extra.lower()
    )


def category_matches_filter(category: Category, needle: str) -> bool:
    if not needle:
        return True
    n = needle.strip().lower()
    if not n:
        return True
    return n in category.title.lower() or n in category.subtitle.lower()
