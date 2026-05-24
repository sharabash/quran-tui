"""Playback / browse coordinator.

Owns mutable session state — active source, browse path, current
:class:`~quran_tui.sources.base.BrowseResult`, queue, currently-playing
track — and delegates I/O to a :class:`Source` and an mpv player.
The controller is fully decoupled from Textual so it can be unit-tested
with fakes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Protocol

from .models import Track
from .player import clamp_volume
from .sources.base import BrowseResult, Source


class _Player(Protocol):
    async def start(self) -> None: ...
    async def quit(self) -> None: ...
    async def load(self, url: str) -> None: ...
    async def play(self) -> None: ...
    async def pause(self) -> None: ...
    async def toggle_pause(self) -> None: ...
    async def stop(self) -> None: ...
    async def set_volume(self, volume: float) -> None: ...


@dataclass
class BrowseState:
    """The currently-displayed view for one source."""

    path: list[str] = field(default_factory=list)
    result: BrowseResult | None = None


@dataclass
class AppState:
    sources: list[Source] = field(default_factory=list)
    active_source_index: int = 0
    browse: dict[str, BrowseState] = field(default_factory=dict)
    queue: list[Track] = field(default_factory=list)
    queue_index: int = -1
    current: Track | None = None
    paused: bool = True
    volume: float = 100.0
    status: str = ""

    @property
    def active_source(self) -> Source | None:
        if not self.sources:
            return None
        if 0 <= self.active_source_index < len(self.sources):
            return self.sources[self.active_source_index]
        return None


class Controller:
    """Coordinates a list of :class:`Source` backends and one mpv player."""

    def __init__(self, *, sources: list[Source], player: _Player) -> None:
        if not sources:
            raise ValueError("controller needs at least one source")
        self._player = player
        self._started = False
        self.state = AppState(sources=list(sources))
        for s in sources:
            self.state.browse[s.name] = BrowseState()

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        await self._player.start()
        self._started = True

    async def quit(self) -> None:
        if self._started:
            try:
                await self._player.quit()
            finally:
                self._started = False
        for source in self.state.sources:
            close = getattr(source, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass

    # --- source switching -------------------------------------------------

    def set_active_source(self, index: int) -> None:
        if 0 <= index < len(self.state.sources):
            self.state.active_source_index = index

    def active_browse(self) -> BrowseState:
        source = self.state.active_source
        if source is None:
            return BrowseState()
        return self.state.browse[source.name]

    # --- browse navigation ------------------------------------------------

    async def browse_root(self) -> BrowseResult:
        return await self._browse([])

    async def browse_into(self, key: str) -> BrowseResult:
        bs = self.active_browse()
        new_path = [*bs.path, key]
        return await self._browse(new_path)

    async def browse_up(self) -> BrowseResult:
        bs = self.active_browse()
        if not bs.path:
            return bs.result or BrowseResult(title="")
        return await self._browse(bs.path[:-1])

    async def refresh(self) -> BrowseResult:
        """Re-render the current path from the source (uses any cache)."""
        return await self._browse(self.active_browse().path)

    async def force_refresh(self) -> tuple[BrowseResult, int]:
        """Force the active source to re-fetch upstream content, then re-browse.

        Returns ``(result, items_loaded)``. ``items_loaded`` is ``-1`` for
        sources that don't expose a refresh method.
        """
        source = self.state.active_source
        loaded = -1
        if source is not None:
            refresh = getattr(source, "refresh", None)
            if callable(refresh):
                try:
                    loaded = int(await refresh())
                except Exception as exc:
                    self.state.status = f"Refresh failed: {exc}"
                    return await self._browse(self.active_browse().path), -1
        result = await self._browse(self.active_browse().path)
        return result, loaded

    async def _browse(self, path: list[str]) -> BrowseResult:
        source = self.state.active_source
        if source is None:
            return BrowseResult(title="")
        result = await source.browse(path)
        bs = self.state.browse[source.name]
        bs.path = list(path)
        bs.result = result
        self.state.status = result.title
        return result

    # --- playback ---------------------------------------------------------

    async def play_track(self, track: Track) -> None:
        source = self.state.active_source
        if source is None:
            return
        try:
            url = track.stream_url or await source.resolve_stream_url(track)
            await self._player.load(url)
            await self._player.play()
        except Exception as exc:
            self.state.status = f"Playback error: {exc}"
            return
        self.state.current = track
        self.state.paused = False
        self.state.status = f"Playing: {track.title} — {track.display_subtitle}"

    async def toggle_pause(self) -> None:
        try:
            await self._player.toggle_pause()
        except Exception as exc:
            self.state.status = f"Playback error: {exc}"
            return
        self.state.paused = not self.state.paused

    async def set_volume(self, volume: float) -> None:
        clamped = clamp_volume(volume)
        try:
            await self._player.set_volume(clamped)
        except Exception:
            pass
        self.state.volume = clamped

    # --- queue management -------------------------------------------------

    def enqueue(self, track: Track) -> None:
        self.state.queue.append(track)

    def enqueue_many(self, tracks: list[Track]) -> int:
        self.state.queue.extend(tracks)
        return len(tracks)

    def dequeue(self, index: int) -> tuple[Track | None, bool]:
        if not (0 <= index < len(self.state.queue)):
            return None, False
        removed = self.state.queue.pop(index)
        was_current = index == self.state.queue_index
        if not self.state.queue:
            self.state.queue_index = -1
        elif index < self.state.queue_index:
            self.state.queue_index -= 1
        return removed, was_current

    def set_queue_index(self, index: int) -> None:
        if 0 <= index < len(self.state.queue):
            self.state.queue_index = index

    def current_queue_track(self) -> Track | None:
        idx = self.state.queue_index
        if 0 <= idx < len(self.state.queue):
            return self.state.queue[idx]
        return None

    def shuffle_upcoming(self) -> int:
        """Shuffle the still-to-play portion of the queue."""
        if self.state.queue_index < 0:
            if len(self.state.queue) < 2:
                return 0
            random.shuffle(self.state.queue)
            self.state.status = f"Shuffled {len(self.state.queue)} tracks"
            return len(self.state.queue)
        cut = self.state.queue_index + 1
        upcoming = self.state.queue[cut:]
        if len(upcoming) < 2:
            return 0
        random.shuffle(upcoming)
        self.state.queue[cut:] = upcoming
        self.state.status = f"Shuffled {len(upcoming)} upcoming tracks"
        return len(upcoming)
