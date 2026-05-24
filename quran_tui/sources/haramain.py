"""haramain.info source — daily salah, jumua, and taraweeh recordings from the
Haramain Sharifain (Makkah + Madinah).

The site is a Blogger blog that links to MP3s hosted on
``mirrors.quranicaudio.com/haramain/<YYYY>/<MM>/<location>/<imam>_<prayer>-<date>.mp3``.
We fetch the Blogger feed (``/feeds/posts/default?alt=rss&max-results=500``)
once per session, regex out every MP3 URL, and parse imam / prayer / date /
location out of the path itself — no HTML scraping needed.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any

import httpx

from ..models import Category, Track
from .base import BrowseResult


_FEED_URL = "http://www.haramain.info/feeds/posts/default?alt=rss&max-results=500"
_MP3_RE = re.compile(
    r"mirrors\.quranicaudio\.com/haramain/"
    r"(?P<year>\d{4})/(?P<month>\d{2})/"
    r"(?P<location>makkah|madinah)/"
    r"(?P<imam>Sheikh[A-Za-z_]+?)_"
    r"(?P<prayer>Fajr|Dhuhr|Asr|Maghrib|Isha|JumuaSalah|JumuaKhutbah|Taraweeh|Qiyam|Tahajjud|Witr)"
    r"-(?P<date>\d{4}-\d{2}-\d{2})\.mp3"
)
_USER_AGENT = "quran-tui/0.1 (+https://github.com/sharabash/quran-tui)"


def _humanise_imam(slug: str) -> str:
    # ``SheikhAhmed_Hudhaify`` → ``Sheikh Ahmed Hudhaify``
    cleaned = slug.replace("_", " ")
    # Add a space before each capital that follows a lower-case letter:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
    # Collapse repeated whitespace.
    return re.sub(r"\s+", " ", spaced).strip()


def _normalise_imam_key(slug: str) -> str:
    """Imam URLs sometimes mix case (SheikhBadr_Al_Turki vs SheikhBadr_al_Turki).
    Reduce to lowercase for grouping while keeping the prettiest display name."""
    return slug.lower()


def _build_track(match: re.Match[str]) -> Track:
    g = match.groupdict()
    imam_display = _humanise_imam(g["imam"])
    location = g["location"].capitalize()
    prayer = g["prayer"]
    date = g["date"]
    title = imam_display
    subtitle = f"{location} · {prayer}"
    extra = date
    url = f"https://{match.group(0)}"
    track_id = f"hi:{date}:{g['location']}:{g['imam']}:{prayer}"
    return Track(
        track_id=track_id,
        title=title,
        subtitle=subtitle,
        extra=extra,
        stream_url=url,
        source="haramain",
        raw={
            "imam_slug": g["imam"],
            "imam_key": _normalise_imam_key(g["imam"]),
            "location": g["location"],
            "prayer": prayer,
            "date": date,
        },
    )


class HaramainSource:
    name = "haramain"
    label = "Haramain"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None
        self._tracks: list[Track] = []
        self._loaded = False
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=20.0,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def refresh(self) -> int:
        """Re-fetch the feed and rebuild the in-memory track list."""
        async with self._lock:
            client = await self._get_client()
            response = await client.get(_FEED_URL)
            response.raise_for_status()
            text = response.text
            seen: set[str] = set()
            tracks: list[Track] = []
            for match in _MP3_RE.finditer(text):
                url = match.group(0)
                if url in seen:
                    continue
                seen.add(url)
                tracks.append(_build_track(match))
            # Most-recent first.
            tracks.sort(
                key=lambda t: (t.raw["date"], t.raw["location"], t.raw["prayer"]),
                reverse=True,
            )
            self._tracks = tracks
            self._loaded = True
            return len(tracks)

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self.refresh()

    # --- Source protocol -------------------------------------------------

    async def browse(self, path: Sequence[str]) -> BrowseResult:
        await self._ensure_loaded()
        if not path:
            categories = [
                Category(
                    key="all",
                    title="All recent recordings",
                    subtitle="Newest first",
                    count=len(self._tracks),
                ),
                Category(
                    key="by-imam",
                    title="By Imam",
                    count=len({t.raw["imam_key"] for t in self._tracks}),
                ),
                Category(
                    key="by-prayer",
                    title="By Prayer",
                    count=len({t.raw["prayer"] for t in self._tracks}),
                ),
                Category(
                    key="by-location",
                    title="By Location",
                    count=len({t.raw["location"] for t in self._tracks}),
                ),
            ]
            return BrowseResult(title="Haramain", categories=categories)

        head = path[0]

        if head == "all":
            return BrowseResult(
                title="Haramain · All recent",
                tracks=list(self._tracks),
            )

        if head == "by-imam":
            if len(path) == 1:
                seen: dict[str, tuple[str, int]] = {}
                for t in self._tracks:
                    k = t.raw["imam_key"]
                    if k not in seen:
                        seen[k] = (t.title, 0)
                    pretty, count = seen[k]
                    seen[k] = (pretty, count + 1)
                cats = [
                    Category(
                        key=f"by-imam:{k}",
                        title=pretty,
                        count=count,
                    )
                    for k, (pretty, count) in sorted(seen.items())
                ]
                return BrowseResult(
                    title="Haramain · By Imam", categories=cats
                )
            imam_key = path[1].split(":", 1)[1] if ":" in path[1] else path[1]
            tracks = [t for t in self._tracks if t.raw["imam_key"] == imam_key]
            pretty = tracks[0].title if tracks else imam_key
            return BrowseResult(
                title=f"Haramain · {pretty}",
                tracks=tracks,
            )

        if head == "by-prayer":
            if len(path) == 1:
                prayers = sorted({t.raw["prayer"] for t in self._tracks})
                cats = [
                    Category(
                        key=f"by-prayer:{p}",
                        title=p,
                        count=sum(1 for t in self._tracks if t.raw["prayer"] == p),
                    )
                    for p in prayers
                ]
                return BrowseResult(
                    title="Haramain · By Prayer", categories=cats
                )
            prayer = path[1].split(":", 1)[1]
            tracks = [t for t in self._tracks if t.raw["prayer"] == prayer]
            return BrowseResult(
                title=f"Haramain · {prayer}",
                tracks=tracks,
            )

        if head == "by-location":
            if len(path) == 1:
                cats = [
                    Category(
                        key=f"by-location:{loc}",
                        title=loc.capitalize(),
                        count=sum(1 for t in self._tracks if t.raw["location"] == loc),
                    )
                    for loc in ("makkah", "madinah")
                ]
                return BrowseResult(
                    title="Haramain · By Location", categories=cats
                )
            loc = path[1].split(":", 1)[1]
            tracks = [t for t in self._tracks if t.raw["location"] == loc]
            return BrowseResult(
                title=f"Haramain · {loc.capitalize()}",
                tracks=tracks,
            )

        return BrowseResult(title="Haramain")

    async def resolve_stream_url(self, track: Track) -> str:
        return track.stream_url
