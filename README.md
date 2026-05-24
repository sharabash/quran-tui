# quran-tui

> A keyboard-driven terminal UI for Quran audio. Browse and play recitations from quranicaudio.com and daily Haramain salah recordings from haramain.info — without leaving the shell.

(Hero screenshot + demo GIF land here once the v0.1 release goes out.)

## What it does

- **Two sources, one UI**
  - **Quranic Audio** — 170+ reciters × 114 surahs each, organised by recitation style (Hafs / Non-Hafs / Translations / Haramain Tarawih).
  - **Haramain** — daily salah (Fajr, Maghrib, Isha), Jumua Khutbah + Salah, and Tarawih recordings from Makkah and Madinah.
- **Browse tree** — drill in / out with Enter and Backspace.
- **Queue management** — enqueue (`a`), enqueue everything visible (`A`), dequeue (`d`/Delete), shuffle (`s`), skip (`n` / `b`).
- **Filter** (`f`) — live-narrow whatever's loaded by title, subtitle, or extra (reciter / imam / location).
- **Click-to-seek trackbar** at the full width of the now-playing row.
- **Themes** via Textual's command palette (`Ctrl+P`).
- **Optional media-key bridge** on `127.0.0.1:13938` for AutoHotkey / xbindkeys / etc.

## Install

Requires Python 3.11+ and [`mpv`](https://mpv.io/) on `PATH`.

```sh
git clone https://github.com/sharabash/quran-tui
cd quran-tui
uv sync
uv run quran-tui
```

## Key bindings

| Key             | Action                                                       |
| --------------- | ------------------------------------------------------------ |
| `1` / `2`       | Switch source (Quranic Audio / Haramain)                     |
| `Enter`         | Drill into category · play track                             |
| `Backspace`     | Go up one level in the browse tree                           |
| `a`             | Enqueue selected track                                       |
| `A`             | Enqueue every visible track                                  |
| `d` / `Delete`  | Dequeue (focus the Queue pane first)                         |
| `f`             | Filter the current view by title / subtitle / extra          |
| `Space`         | Play / pause                                                 |
| `n` / `b`       | Next / previous queued track                                 |
| `[` / `]`       | Seek −10s / +10s                                             |
| `-` / `=`       | Volume down / up                                             |
| `s`             | Shuffle the upcoming queue                                   |
| Click trackbar  | Seek to that point                                           |
| `Ctrl+P`        | Command palette (themes, etc.)                               |
| `?`             | Help overlay                                                 |
| `q` / `Ctrl+C`  | Quit                                                         |

## How it works

- **Quranic Audio** — three JSON endpoints (`/api/sections`, `/api/qaris`, `/api/surahs`), and a deterministic stream URL: `https://download.quranicaudio.com/quran/{relative_path}{NNN}.mp3`.
- **Haramain** — the haramain.info blog is fetched once per session (`http://www.haramain.info/feeds/posts/default?alt=rss&max-results=500`) and every MP3 URL is regex-extracted. Date / location / imam / prayer are parsed straight out of the URL path; no HTML scraping.
- **Playback** — direct MP3 → `mpv` over its JSON IPC socket. No yt-dlp or special handling required.
- A single `Controller` owns mutable session state (active source, browse path, queue, current track) and is fully decoupled from Textual — sources / player are unit-tested with fakes.

## Architecture

```
quran_tui/
├── models.py            Track, Category, duration helpers
├── sources/
│   ├── base.py          Source protocol + BrowseResult
│   ├── quranicaudio.py  api/sections + api/qaris + api/surahs
│   └── haramain.py      Blogger RSS feed → MP3 metadata
├── player.py            Async mpv IPC wrapper (shared with ytm-tui)
├── controller.py        Orchestrates source(s) + player + state
├── tui.py               Textual UI with source switcher + browse tree
├── control.py           Loopback HTTP control plane for media keys
└── cli.py               argparse entry point
```

## License

MIT.
