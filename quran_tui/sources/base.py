"""Source protocol shared by every audio backend (quranicaudio / haramain / …).

A source is essentially a directory tree. The TUI walks it by calling
``browse(path)`` with a list of path segments understood by that source.
The returned :class:`BrowseResult` either contains sub-categories (drill
further) or :class:`Track` leaves (play them).

Stream URLs are resolved lazily because some endpoints require a second API
call to get the direct MP3.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from ..models import Category, Track


@dataclass(frozen=True)
class BrowseResult:
    """Outcome of one navigation step."""

    title: str                              # breadcrumb-style label
    categories: list[Category] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return bool(self.tracks) and not self.categories


class Source(Protocol):
    """One audio backend. Stateless aside from cheap caches."""

    name: str
    label: str

    async def browse(self, path: Sequence[str]) -> BrowseResult:
        """Return categories / tracks at ``path``.

        ``path == []`` is the source's root. Each entry in ``path`` is a
        ``Category.key`` returned by an earlier ``browse`` call.
        """
        ...

    async def resolve_stream_url(self, track: Track) -> str:
        """Return a direct, playable URL for ``track``.

        Defaults to ``track.stream_url`` for sources that put the URL inline.
        """
        ...


class RefreshableSource(Source, Protocol):
    """Optional protocol for sources whose backend changes over time."""

    async def refresh(self) -> int:
        """Re-fetch upstream content. Returns the number of items loaded."""
        ...
