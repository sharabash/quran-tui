"""Console entry point for quran-tui."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quran-tui",
        description="A keyboard-driven terminal UI for Quran audio.",
    )
    parser.add_argument(
        "--version", action="version", version=f"quran-tui {__version__}"
    )
    parser.add_argument(
        "--mpv", default="mpv", help="Path to the mpv binary (default: mpv on PATH)."
    )
    parser.add_argument(
        "--ytdl",
        default="yt-dlp",
        help=(
            "yt-dlp binary that mpv's ytdl_hook should consult — only "
            "relevant for protected streams. Direct MP3 URLs from "
            "quranicaudio / haramain don't need it."
        ),
    )
    parser.add_argument(
        "--source",
        choices=("quranicaudio", "haramain"),
        default="quranicaudio",
        help="Source to focus on launch (default: quranicaudio).",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=int(os.environ.get("QURAN_CONTROL_PORT") or 13938),
        metavar="PORT",
        help=(
            "Bind a loopback-only HTTP control endpoint for media-key "
            "helpers. 0 disables. Default: 13938."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from .controller import Controller
    from .player import MpvPlayer
    from .sources.haramain import HaramainSource
    from .sources.quranicaudio import QuranicAudioSource
    from .tui import QuranTuiApp

    sources = [QuranicAudioSource(), HaramainSource()]
    name_to_index = {s.name: i for i, s in enumerate(sources)}
    initial = name_to_index.get(args.source, 0)

    player = MpvPlayer(mpv_binary=args.mpv, ytdl_binary=args.ytdl)
    controller = Controller(sources=sources, player=player)
    controller.set_active_source(initial)

    app = QuranTuiApp(controller=controller, control_port=args.control_port)
    try:
        app.run()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
