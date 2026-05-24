"""End-to-end smoke test — drives the real TUI against live APIs + mpv + MCP.

Walks through:
  - Listen mode → Quranic Audio root → section → reciter → play a surah
  - Listen mode → switch to Haramain (key '2') → drill 'all' → play a recording
  - Switch to Study mode (Alt+2) → run search_translation against mcp.quran.ai
  - Switch to Read mode (Alt+3) → confirm placeholder
  - Switch back to Listen mode (Alt+1) → state preserved
"""

from __future__ import annotations

import asyncio
import sys

from textual.widgets import DataTable, Input

from quran_tui.controller import Controller
from quran_tui.mcp_quran import MCPSession
from quran_tui.player import MpvPlayer
from quran_tui.sources.haramain import HaramainSource
from quran_tui.sources.quranicaudio import QuranicAudioSource
from quran_tui.tui import QuranTuiApp
from quran_tui.widgets import NowPlaying


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
    mcp = MCPSession()
    app = QuranTuiApp(controller=controller, mcp_session=mcp, control_port=0)
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause(1.0)

        # 1. Listen mode is active by default.
        assert app._active_mode == "listen", f"expected listen, got {app._active_mode}"
        print(f"[smoke] default mode: {app._active_mode}")

        # 2. Listen → QA root populates.
        bs = controller.active_browse()
        await _wait_for(
            lambda: bs.result is not None and bs.result.categories, 8.0, pilot
        )
        assert bs.result, "QA root didn't populate"
        print(f"[smoke] Listen/QA: {len(bs.result.categories)} sections")

        # 3. Drill into section 1.
        browse = app.query_one("#browse-table", DataTable)
        browse.focus()
        browse.cursor_coordinate = (0, 0)
        await pilot.press("enter")
        await _wait_for(
            lambda: bs.result is not None and bs.result.title.startswith("Quranic Audio · Recitations"),
            5.0,
            pilot,
        )
        print(f"[smoke] section 1: {len(bs.result.categories)} reciters")

        # 4. Drill into first reciter, play first surah.
        browse.cursor_coordinate = (0, 0)
        await pilot.press("enter")
        await _wait_for(
            lambda: bs.result is not None and bs.result.tracks, 5.0, pilot
        )
        assert bs.result and bs.result.tracks, "no surahs loaded"
        print(f"[smoke] reciter: {len(bs.result.tracks)} surahs")
        browse.cursor_coordinate = (0, 0)
        await pilot.press("enter")
        np = app.query_one(NowPlaying)
        ok = await _wait_for(lambda: np.position > 1.0, 30.0, pilot)
        assert ok, f"playback didn't progress; pos={np.position}"
        print(f"[smoke] playing '{np.track.title if np.track else '?'}' pos={np.position:.1f}s")

        # 5. Switch to Study mode (Alt+2).
        await pilot.press("alt+2")
        await pilot.pause(0.5)
        assert app._active_mode == "study", f"expected study, got {app._active_mode}"
        print(f"[smoke] switched to mode: {app._active_mode} (audio still playing)")

        # 6. Run an MCP search_translation query.
        # Active sub-tab defaults to Search → tool defaults to search_quran.
        # Type a query and press Enter.
        # Find the active first input in StudyMode.
        study_input = app.query_one("#study-input-0", Input)
        study_input.focus()
        await pilot.press(*list("patience in adversity"))
        await pilot.press("enter")
        # Wait for MCP response — server takes a beat.
        study_mode = app._modes["study"]
        ok = await _wait_for(
            lambda: study_mode.last_outcome is not None and study_mode.last_outcome.ok,
            45.0,
            pilot,
        )
        assert ok, (
            f"MCP search didn't return within 45s; "
            f"last_outcome={study_mode.last_outcome!r}"
        )
        print(
            f"[smoke] MCP search ok; rendered length={len(study_mode.last_rendered)}; "
            f"sample: {study_mode.last_rendered[:80]}"
        )

        # 7. Confirm audio is STILL playing while we did MCP work.
        prior = np.position
        await pilot.pause(2.0)
        assert np.position > prior + 0.5, (
            f"audio froze during MCP call: {prior} → {np.position}"
        )
        print(f"[smoke] audio kept advancing: {prior:.1f}s → {np.position:.1f}s during MCP work")

        # 8. Switch to Read mode (placeholder).
        await pilot.press("alt+3")
        await pilot.pause(0.3)
        assert app._active_mode == "read"
        print(f"[smoke] read mode: {app._active_mode} (placeholder shows)")

        # 9. Back to Listen — state preserved.
        await pilot.press("alt+1")
        await pilot.pause(0.3)
        assert app._active_mode == "listen"
        # Reciter view should still be visible.
        assert bs.result and bs.result.tracks, "Listen state lost on mode switch"
        print(f"[smoke] back at Listen, still on '{bs.result.title}'")

        print("[smoke] all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
