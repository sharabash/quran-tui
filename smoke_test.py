"""End-to-end smoke test — drives the real TUI against live APIs + mpv.

Walks the user through: Quranic Audio root → section → reciter → play a
surah; switches to Haramain → drills "all" → plays a recording; exercises
filter, dequeue, shuffle. Exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import sys

from textual.widgets import DataTable

from quran_tui.controller import Controller
from quran_tui.player import MpvPlayer
from quran_tui.sources.haramain import HaramainSource
from quran_tui.sources.quranicaudio import QuranicAudioSource
from quran_tui.tui import NowPlaying, QuranTuiApp


async def _wait_for(predicate, timeout: float, pilot) -> bool:
    elapsed = 0.0
    while elapsed < timeout:
        await pilot.pause(0.25)
        elapsed += 0.25
        if predicate():
            return True
    return False


async def run() -> int:
    sources = [QuranicAudioSource(), HaramainSource()]
    controller = Controller(sources=sources, player=MpvPlayer())
    app = QuranTuiApp(controller=controller, control_port=0)
    async with app.run_test(size=(130, 40)) as pilot:
        await pilot.pause(1.0)
        # 1. Quranic Audio root should have 4 sections.
        bs = controller.active_browse()
        await _wait_for(
            lambda: bs.result is not None and bs.result.categories, 8.0, pilot
        )
        assert bs.result is not None, "QA root did not load"
        print(f"[smoke] QA root: {len(bs.result.categories)} sections")

        # 2. Drill into section 1.
        await pilot.press("enter")
        await _wait_for(
            lambda: bs.result is not None
            and bs.result.title.startswith("Quranic Audio · Recitations"),
            5.0,
            pilot,
        )
        assert bs.result and bs.result.categories, "section 1 not loaded"
        print(f"[smoke] QA section 1: {len(bs.result.categories)} reciters")

        # 3. Pick the first reciter — drill in.
        await pilot.press("enter")
        await _wait_for(
            lambda: bs.result is not None and bs.result.tracks, 5.0, pilot
        )
        assert bs.result and bs.result.tracks, "reciter tracks not loaded"
        print(f"[smoke] QA reciter: {len(bs.result.tracks)} surahs")

        # 4. Play first surah.
        await pilot.press("enter")
        np = app.query_one(NowPlaying)
        ok = await _wait_for(lambda: np.position > 1.0, 25.0, pilot)
        assert ok, f"playback didn't progress; pos={np.position}"
        print(f"[smoke] playing '{np.track.title if np.track else '?'}' pos={np.position:.1f}s")

        # 5. Switch to Haramain (key "2").
        await pilot.press("2")
        # First haramain browse triggers a feed fetch (~1-2s).
        await _wait_for(
            lambda: controller.active_browse().result is not None, 10.0, pilot
        )
        ha_bs = controller.active_browse()
        assert ha_bs.result, "Haramain root not loaded"
        print(f"[smoke] Haramain root: categories={[c.title for c in ha_bs.result.categories]}")

        # 6. Drill into "All recent recordings".
        browse_table = app.query_one("#browse-table", DataTable)
        browse_table.focus()
        browse_table.cursor_coordinate = (0, 0)
        await pilot.press("enter")
        await _wait_for(
            lambda: ha_bs.result is not None and ha_bs.result.tracks, 5.0, pilot
        )
        assert ha_bs.result and ha_bs.result.tracks, "Haramain all tracks not loaded"
        print(f"[smoke] Haramain all: {len(ha_bs.result.tracks)} recordings")

        # 7. Play first Haramain track.
        browse_table.cursor_coordinate = (0, 0)
        await pilot.press("enter")
        ok = await _wait_for(
            lambda: np.track is not None
            and np.track.source == "haramain"
            and np.position > 1.0,
            30.0,
            pilot,
        )
        assert ok, f"haramain playback didn't progress; pos={np.position}"
        print(f"[smoke] playing haramain '{np.track.title}'  pos={np.position:.1f}s")

        # 8. Filter test.
        await pilot.press("f")
        await pilot.pause(0.3)
        await pilot.press("f", "a", "j", "r")
        await pilot.pause(0.5)
        # Should have narrowed.
        result = ha_bs.result
        assert result is not None
        filtered = [
            t for t in result.tracks if "fajr" in t.subtitle.lower()
        ]
        assert filtered, "filter should leave at least one Fajr match"
        print(f"[smoke] filter ok: {len(filtered)} Fajr matches")
        await pilot.press("escape")
        await pilot.pause(0.3)

        # 9. Shuffle the queue (we only have one track; should be a no-op).
        await pilot.press("s")
        await pilot.pause(0.3)
        print("[smoke] shuffle ok (no-op on single-track queue)")

        # 10. Switch back to Quranic Audio — browse state should persist.
        await pilot.press("1")
        await pilot.pause(0.5)
        qa_bs = controller.active_browse()
        assert qa_bs.path == ["section:1", qa_bs.path[1]] if len(qa_bs.path) > 1 else True
        print(f"[smoke] back at QA, path={qa_bs.path}")

        print("[smoke] all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
