"""Controller tests with fake sources + fake player (no network, no mpv)."""

from __future__ import annotations

import pytest

from quran_tui.controller import Controller
from quran_tui.models import Category, Track
from quran_tui.sources.base import BrowseResult


class FakeSource:
    def __init__(self, name: str = "fake", label: str = "Fake") -> None:
        self.name = name
        self.label = label
        self.calls: list[tuple[str, object]] = []

    async def browse(self, path):
        self.calls.append(("browse", tuple(path)))
        if not path:
            return BrowseResult(
                title="Fake root",
                categories=[Category(key="a", title="Category A", count=2)],
            )
        if path == ["a"]:
            return BrowseResult(
                title="Fake / Category A",
                tracks=[
                    Track(track_id="t1", title="Track 1", subtitle="Sub", source="fake"),
                    Track(track_id="t2", title="Track 2", subtitle="Sub", source="fake"),
                ],
            )
        return BrowseResult(title="Fake / ?")

    async def resolve_stream_url(self, track):
        self.calls.append(("resolve", track.track_id))
        return f"file:///fake/{track.track_id}.mp3"


class FakePlayer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def start(self) -> None:
        self.calls.append(("start", None))

    async def quit(self) -> None:
        self.calls.append(("quit", None))

    async def load(self, url: str) -> None:
        self.calls.append(("load", url))

    async def play(self) -> None:
        self.calls.append(("play", None))

    async def pause(self) -> None:
        self.calls.append(("pause", None))

    async def toggle_pause(self) -> None:
        self.calls.append(("toggle_pause", None))

    async def stop(self) -> None:
        self.calls.append(("stop", None))

    async def set_volume(self, volume: float) -> None:
        self.calls.append(("set_volume", volume))


def _track(tid: str = "t1") -> Track:
    return Track(track_id=tid, title=f"Track {tid}", source="fake")


@pytest.mark.asyncio
async def test_browse_root_then_drill_in() -> None:
    src = FakeSource()
    controller = Controller(sources=[src], player=FakePlayer())
    root = await controller.browse_root()
    assert root.title == "Fake root"
    assert controller.active_browse().path == []
    drilled = await controller.browse_into("a")
    assert drilled.title == "Fake / Category A"
    assert controller.active_browse().path == ["a"]
    up = await controller.browse_up()
    assert up.title == "Fake root"
    assert controller.active_browse().path == []


@pytest.mark.asyncio
async def test_play_track_resolves_url_and_drives_player() -> None:
    src = FakeSource()
    player = FakePlayer()
    controller = Controller(sources=[src], player=player)
    track = Track(track_id="t1", title="T1", source="fake")  # no inline url
    await controller.play_track(track)
    assert ("load", "file:///fake/t1.mp3") in player.calls
    assert ("play", None) in player.calls
    assert controller.state.current == track
    assert controller.state.paused is False


@pytest.mark.asyncio
async def test_play_track_uses_inline_url_when_present() -> None:
    src = FakeSource()
    player = FakePlayer()
    controller = Controller(sources=[src], player=player)
    track = Track(track_id="t2", title="T2", stream_url="file:///direct.mp3", source="fake")
    await controller.play_track(track)
    assert ("load", "file:///direct.mp3") in player.calls
    # We should NOT have asked the source to resolve.
    assert ("resolve", "t2") not in src.calls


def test_enqueue_and_dequeue_keep_indices_consistent() -> None:
    controller = Controller(sources=[FakeSource()], player=FakePlayer())
    a, b, c = _track("a"), _track("b"), _track("c")
    controller.enqueue(a)
    controller.enqueue(b)
    controller.enqueue(c)
    controller.set_queue_index(2)
    removed, was_current = controller.dequeue(0)
    assert removed == a and was_current is False
    assert controller.state.queue_index == 1
    removed, was_current = controller.dequeue(1)
    assert removed == c and was_current is True


@pytest.mark.asyncio
async def test_switching_source_keeps_per_source_browse_state() -> None:
    src1 = FakeSource(name="one", label="One")
    src2 = FakeSource(name="two", label="Two")
    controller = Controller(sources=[src1, src2], player=FakePlayer())
    await controller.browse_root()
    await controller.browse_into("a")  # path = ["a"] for src1
    controller.set_active_source(1)
    await controller.browse_root()
    assert controller.active_browse().path == []
    # Switch back — original path should persist.
    controller.set_active_source(0)
    assert controller.active_browse().path == ["a"]


def test_shuffle_upcoming_preserves_history() -> None:
    import random as _random

    _random.seed(0)
    controller = Controller(sources=[FakeSource()], player=FakePlayer())
    tracks = [_track(c) for c in "abcde"]
    for t in tracks:
        controller.enqueue(t)
    controller.set_queue_index(1)
    pre_history = controller.state.queue[:2]
    moved = controller.shuffle_upcoming()
    assert moved == 3
    assert controller.state.queue[:2] == pre_history
    assert sorted(t.track_id for t in controller.state.queue[2:]) == sorted("cde")


def test_controller_requires_at_least_one_source() -> None:
    with pytest.raises(ValueError):
        Controller(sources=[], player=FakePlayer())
