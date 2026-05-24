"""Pure-data tests for the two real sources — no network."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from quran_tui.sources.haramain import HaramainSource
from quran_tui.sources.quranicaudio import QuranicAudioSource


def _stub_client(handler: Callable[[httpx.Request], Awaitable[httpx.Response]]) -> httpx.AsyncClient:
    transport = httpx.MockTransport(lambda req: handler(req))  # type: ignore[arg-type]
    return httpx.AsyncClient(
        base_url="https://quranicaudio.com/api",
        transport=transport,
    )


def _make_quran_handler(
    sections: list[dict[str, Any]],
    qaris: list[dict[str, Any]],
    surahs: list[dict[str, Any]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/sections"):
            return httpx.Response(200, json=sections)
        if path.endswith("/qaris"):
            return httpx.Response(200, json=qaris)
        if path.endswith("/surahs"):
            return httpx.Response(200, json=surahs)
        return httpx.Response(404)
    return handle


@pytest.mark.asyncio
async def test_quranicaudio_browse_full_drill_down() -> None:
    sections = [
        {"id": 1, "name": "Recitations"},
        {"id": 2, "name": "Non-Hafs"},
    ]
    qaris = [
        {"id": 10, "name": "Mishary Alafasy", "arabic_name": "مشاري", "relative_path": "mishary/", "section_id": 1},
        {"id": 11, "name": "Saad Al-Ghamdi", "arabic_name": "سعد", "relative_path": "ghamdi/", "section_id": 1},
        {"id": 12, "name": "Other Style", "arabic_name": "", "relative_path": "other/", "section_id": 2},
    ]
    surahs = [
        {
            "id": 1,
            "ayah": 7,
            "revelation_place": "makkah",
            "name": {"simple": "Al-Fatihah", "english": "The Opener", "arabic": "الفاتحة"},
        },
        {
            "id": 36,
            "ayah": 83,
            "revelation_place": "makkah",
            "name": {"simple": "Ya-Sin", "english": "Ya Sin", "arabic": "يس"},
        },
    ]
    handler = _make_quran_handler(sections, qaris, surahs)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="https://quranicaudio.com/api",
        transport=transport,
    ) as client:
        src = QuranicAudioSource(client=client)
        root = await src.browse([])
        assert root.title == "Quranic Audio"
        assert [c.title for c in root.categories] == ["Recitations", "Non-Hafs"]
        assert root.categories[0].count == 2  # two reciters in section 1

        section = await src.browse(["section:1"])
        assert "Recitations" in section.title
        assert [c.title for c in section.categories] == ["Mishary Alafasy", "Saad Al-Ghamdi"]

        recital = await src.browse(["section:1", "reciter:10"])
        assert recital.tracks, "expected per-surah tracks"
        first = recital.tracks[0]
        assert first.title == "001. Al-Fatihah"
        assert first.subtitle == "Mishary Alafasy"
        assert first.stream_url == "https://download.quranicaudio.com/quran/mishary/001.mp3"

        second = recital.tracks[1]
        assert second.stream_url == "https://download.quranicaudio.com/quran/mishary/036.mp3"


@pytest.mark.asyncio
async def test_haramain_parses_feed_and_groups() -> None:
    sample_feed = """
    <rss>
      <channel>
        <item><enclosure url="https://mirrors.quranicaudio.com/haramain/2026/05/madinah/SheikhAhmed_Hudhaify_Fajr-2026-05-24.mp3"/></item>
        <item><enclosure url="https://mirrors.quranicaudio.com/haramain/2026/05/makkah/SheikhBadr_al_Turki_Fajr-2026-05-24.mp3"/></item>
        <item><enclosure url="https://mirrors.quranicaudio.com/haramain/2026/05/makkah/SheikhBadr_Al_Turki_Maghrib-2026-05-23.mp3"/></item>
        <item><enclosure url="https://mirrors.quranicaudio.com/haramain/2026/05/madinah/SheikhMuayqali_JumuaSalah-2026-05-22.mp3"/></item>
        <!-- duplicate -->
        <item><enclosure url="https://mirrors.quranicaudio.com/haramain/2026/05/madinah/SheikhAhmed_Hudhaify_Fajr-2026-05-24.mp3"/></item>
      </channel>
    </rss>
    """

    def handle(request: httpx.Request) -> httpx.Response:
        assert "haramain.info" in request.url.host
        return httpx.Response(200, text=sample_feed)

    transport = httpx.MockTransport(handle)
    async with httpx.AsyncClient(transport=transport) as client:
        src = HaramainSource(client=client)
        count = await src.refresh()
        assert count == 4  # 5 items, 1 duplicate dropped

        root = await src.browse([])
        cats = {c.key for c in root.categories}
        assert cats == {"all", "by-imam", "by-prayer", "by-location"}

        all_tracks = await src.browse(["all"])
        assert len(all_tracks.tracks) == 4
        # Newest first.
        assert all_tracks.tracks[0].raw["date"] == "2026-05-24"
        assert all_tracks.tracks[-1].raw["date"] == "2026-05-22"

        by_imam = await src.browse(["by-imam"])
        # SheikhBadr_al_Turki and SheikhBadr_Al_Turki collapse on imam_key.
        keys = {c.key for c in by_imam.categories}
        assert any(k.endswith(":sheikhbadr_al_turki") for k in keys)

        by_prayer = await src.browse(["by-prayer"])
        assert {"Fajr", "Maghrib", "JumuaSalah"} <= {c.title for c in by_prayer.categories}

        by_location = await src.browse(["by-location"])
        assert {c.title for c in by_location.categories} == {"Makkah", "Madinah"}

        # Drill into a leaf.
        makkah = await src.browse(["by-location", "by-location:makkah"])
        assert len(makkah.tracks) == 2
        for t in makkah.tracks:
            assert t.raw["location"] == "makkah"
