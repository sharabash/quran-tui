"""quranicaudio.com source — Quran recitations by 170+ reciters.

API endpoints (verified 2026-05):
  GET /api/sections   →  list of recitation styles ("Hafs", "Non-Hafs", …)
  GET /api/qaris      →  list of reciters with relative_path
  GET /api/surahs     →  list of 114 surahs with names + ayah counts

Stream URLs are deterministic: ``https://download.quranicaudio.com/quran/{relative_path}{NNN}.mp3``
where ``NNN`` is the zero-padded surah number.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from ..models import Category, Track
from .base import BrowseResult

_BASE = "https://quranicaudio.com/api"
_STREAM_BASE = "https://download.quranicaudio.com/quran/"
_USER_AGENT = "quran-tui/0.1 (+https://github.com/sharabash/quran-tui)"


def _build_track(reciter: dict[str, Any], surah: dict[str, Any]) -> Track:
    surah_id = int(surah["id"])
    relative = (reciter.get("relative_path") or "").strip("/")
    url = f"{_STREAM_BASE}{relative}/{surah_id:03d}.mp3"
    name = surah.get("name", {}) or {}
    english = name.get("english") or name.get("simple") or f"Surah {surah_id}"
    arabic = name.get("arabic", "")
    title = f"{surah_id:03d}. {name.get('simple') or english}"
    subtitle = reciter.get("name", "Unknown reciter")
    extra_bits = []
    if arabic:
        extra_bits.append(arabic)
    if english and english != name.get("simple"):
        extra_bits.append(english)
    revelation = surah.get("revelation_place")
    if revelation:
        extra_bits.append(str(revelation).capitalize())
    return Track(
        track_id=f"qa:{reciter['id']}:{surah_id}",
        title=title,
        subtitle=subtitle,
        extra=" · ".join(extra_bits),
        stream_url=url,
        source="quranicaudio",
        raw={"reciter_id": reciter["id"], "surah_id": surah_id},
    )


class QuranicAudioSource:
    name = "quranicaudio"
    label = "Quranic Audio"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._sections_cache: list[dict[str, Any]] | None = None
        self._reciters_cache: list[dict[str, Any]] | None = None
        self._surahs_cache: list[dict[str, Any]] | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_BASE,
                timeout=15.0,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _sections(self) -> list[dict[str, Any]]:
        if self._sections_cache is None:
            client = await self._get_client()
            r = await client.get("/sections")
            r.raise_for_status()
            data = r.json()
            self._sections_cache = data if isinstance(data, list) else []
        return self._sections_cache

    async def _reciters(self) -> list[dict[str, Any]]:
        if self._reciters_cache is None:
            client = await self._get_client()
            r = await client.get("/qaris")
            r.raise_for_status()
            data = r.json()
            self._reciters_cache = data if isinstance(data, list) else []
        return self._reciters_cache

    async def _surahs(self) -> list[dict[str, Any]]:
        if self._surahs_cache is None:
            client = await self._get_client()
            r = await client.get("/surahs")
            r.raise_for_status()
            data = r.json()
            self._surahs_cache = data if isinstance(data, list) else []
        return self._surahs_cache

    # --- Source protocol -------------------------------------------------

    async def browse(self, path: Sequence[str]) -> BrowseResult:
        if not path:
            sections = await self._sections()
            reciters = await self._reciters()
            counts: dict[int, int] = {}
            for r in reciters:
                sec = r.get("section_id")
                if sec is not None:
                    counts[int(sec)] = counts.get(int(sec), 0) + 1
            cats = [
                Category(
                    key=f"section:{sec['id']}",
                    title=sec.get("name", f"Section {sec['id']}"),
                    count=counts.get(int(sec["id"])),
                )
                for sec in sections
            ]
            return BrowseResult(title="Quranic Audio", categories=cats)

        head = path[0]
        if head.startswith("section:"):
            section_id = int(head.split(":", 1)[1])
            if len(path) == 1:
                reciters = await self._reciters()
                cats = [
                    Category(
                        key=f"reciter:{r['id']}",
                        title=r.get("name", "Unknown"),
                        subtitle=r.get("arabic_name", "") or "",
                    )
                    for r in reciters
                    if int(r.get("section_id") or 0) == section_id
                ]
                cats.sort(key=lambda c: c.title.lower())
                sections = await self._sections()
                section_name = next(
                    (s.get("name") for s in sections if int(s["id"]) == section_id),
                    f"Section {section_id}",
                )
                return BrowseResult(
                    title=f"Quranic Audio · {section_name}",
                    categories=cats,
                )
            # path[1] is reciter:<id>
            reciter_token = path[1]
            return await self._tracks_for_reciter(reciter_token)

        if head.startswith("reciter:"):
            return await self._tracks_for_reciter(head)

        return BrowseResult(title="Quranic Audio")

    async def _tracks_for_reciter(self, reciter_token: str) -> BrowseResult:
        rid = int(reciter_token.split(":", 1)[1])
        reciters = await self._reciters()
        reciter = next((r for r in reciters if int(r["id"]) == rid), None)
        if reciter is None:
            return BrowseResult(title="Quranic Audio · ?")
        surahs = await self._surahs()
        tracks = [_build_track(reciter, s) for s in surahs]
        return BrowseResult(
            title=f"Quranic Audio · {reciter.get('name', '?')}",
            tracks=tracks,
        )

    async def resolve_stream_url(self, track: Track) -> str:
        # Stream URLs are deterministic; no second call needed.
        return track.stream_url
