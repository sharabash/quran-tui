"""Reusable widgets shared across modes — the trackbar, the now-playing
footer, and the left-side primary-mode navigation rail."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from .models import Track, format_duration

# ---------------------------------------------------------------------------
# Click-to-seek trackbar
# ---------------------------------------------------------------------------


class Trackbar(Static):
    DEFAULT_CSS = """
    Trackbar { height: 1; background: transparent; }
    """

    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)

    class Seek(Message):
        def __init__(self, fraction: float) -> None:
            super().__init__()
            self.fraction = fraction

    def render(self) -> Text:
        width = max(self.size.width, 1)
        frac = (
            max(0.0, min(1.0, self.position / self.duration))
            if self.duration > 0
            else 0.0
        )
        filled = int(round(frac * width))
        result = Text()
        result.append("━" * filled, style="bold #7aa2f7")
        result.append("─" * max(0, width - filled), style="#3b4261")
        return result

    def on_click(self, event: events.Click) -> None:
        if self.size.width <= 0:
            return
        self.post_message(self.Seek(max(0.0, min(1.0, event.x / self.size.width))))

    def watch_position(self, *_: Any) -> None:
        if self.is_mounted:
            self.refresh()

    def watch_duration(self, *_: Any) -> None:
        if self.is_mounted:
            self.refresh()


# ---------------------------------------------------------------------------
# Now-playing footer
# ---------------------------------------------------------------------------


class NowPlaying(Static):
    track: reactive[Track | None] = reactive(None, layout=True)
    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)
    paused: reactive[bool] = reactive(True)
    volume: reactive[float] = reactive(100.0)
    queue_position: reactive[str] = reactive("")
    loading: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        with Horizontal(id="np-title-row"):
            yield Static("", id="np-title", markup=True)
            yield Static("", id="np-time", markup=True)
        yield Trackbar(id="np-bar")
        yield Static("", id="np-meta", markup=True)

    def _refresh_title(self) -> None:
        title_w = self.query_one("#np-title", Static)
        if self.loading and self.track is not None:
            title_w.update(
                f"[yellow]◌ loading…[/yellow]  [bold]{self.track.title}[/bold]"
                f"  [dim]·[/dim]  {self.track.display_subtitle}"
            )
            return
        if self.track is None:
            title_w.update(
                "[dim italic]nothing playing — press Alt+1 / Alt+2 to navigate[/dim italic]"
            )
            return
        marker = "[#f7768e]‖[/#f7768e]" if self.paused else "[#9ece6a]▶[/#9ece6a]"
        title_w.update(
            f"{marker}  [bold]{self.track.title}[/bold]"
            f"  [dim]·[/dim]  [#7aa2f7]{self.track.display_subtitle}[/#7aa2f7]"
        )

    def _refresh_progress(self) -> None:
        bar = self.query_one("#np-bar", Trackbar)
        time_w = self.query_one("#np-time", Static)
        bar.position = float(self.position)
        bar.duration = float(self.duration)
        if self.track is None:
            time_w.update("[dim]--:-- / --:--[/dim]")
            return
        pos_text = format_duration(self.position)
        dur_text = format_duration(self.duration if self.duration > 0 else None)
        time_w.update(f"[dim]{pos_text} / {dur_text}[/dim]")

    def _refresh_meta(self) -> None:
        meta = self.query_one("#np-meta", Static)
        parts = []
        if self.track is not None:
            state = "paused" if self.paused else "playing"
            colour = "#f7768e" if self.paused else "#9ece6a"
            parts.append(f"[{colour}]{state}[/{colour}]")
        parts.append(f"vol [bold]{int(self.volume)}[/bold]")
        if self.queue_position:
            parts.append(f"queue [bold]{self.queue_position}[/bold]")
        meta.update("  [dim]·[/dim]  ".join(parts))

    def on_mount(self) -> None:
        self._refresh_title()
        self._refresh_progress()
        self._refresh_meta()

    def watch_track(self, *_: Any) -> None:
        if self.is_mounted:
            self._refresh_title()
            self._refresh_progress()
            self._refresh_meta()

    def watch_paused(self, *_: Any) -> None:
        if self.is_mounted:
            self._refresh_title()
            self._refresh_meta()

    def watch_position(self, *_: Any) -> None:
        if self.is_mounted:
            self._refresh_progress()

    def watch_duration(self, *_: Any) -> None:
        if self.is_mounted:
            self._refresh_progress()

    def watch_volume(self, *_: Any) -> None:
        if self.is_mounted:
            self._refresh_meta()

    def watch_queue_position(self, *_: Any) -> None:
        if self.is_mounted:
            self._refresh_meta()

    def watch_loading(self, *_: Any) -> None:
        if self.is_mounted:
            self._refresh_title()


# ---------------------------------------------------------------------------
# Primary-mode navigation rail (left sidebar)
# ---------------------------------------------------------------------------


class PrimaryNav(Vertical):
    """Left-side rail with one Static-row per registered mode.

    Owns no state itself — the host App passes in a list of ``(key, label,
    icon)`` tuples and reactively re-renders when the active mode changes.
    Clicks emit :class:`PrimaryNav.Select`.
    """

    DEFAULT_CSS = """
    PrimaryNav {
        width: 20;
        background: $surface;
        padding: 1 0;
    }
    .nav-row {
        height: 2;
        padding: 0 1;
        color: $text-muted;
    }
    .nav-row.active {
        color: $accent;
        text-style: bold;
        background: $accent 15%;
    }
    .nav-spacer {
        height: 1;
    }
    .nav-footer {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        text-style: dim;
    }
    """

    class Select(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, items: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self._items = items
        self._active = items[0][0] if items else ""

    def compose(self) -> ComposeResult:
        yield Static(" [b]quran-tui[/b] ", classes="nav-row")
        yield Static("", classes="nav-spacer")
        for i, (name, label, icon) in enumerate(self._items, 1):
            cls = "nav-row active" if name == self._active else "nav-row"
            yield Static(
                f"  [dim]Alt+{i}[/dim]  {icon}  {label}",
                id=f"nav-{name}",
                classes=cls,
                markup=True,
            )
        yield Static("", classes="nav-spacer")
        yield Static("[dim]? help · q quit[/dim]", classes="nav-footer")

    def set_active(self, name: str) -> None:
        self._active = name
        if not self.is_mounted:
            return
        for n, _label, _icon in self._items:
            row = self.query_one(f"#nav-{n}", Static)
            if n == name:
                row.add_class("active")
            else:
                row.remove_class("active")
