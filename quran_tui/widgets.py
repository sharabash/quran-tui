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
                "[dim italic]nothing playing[/dim italic]"
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


class ModeButton(Static):
    """One row in the left nav. Focusable via Tab, clickable, Enter activates."""

    DEFAULT_CSS = """
    ModeButton {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        background: $background;
    }
    ModeButton:focus {
        color: $accent;
        background: $accent 10%;
        text-style: bold;
    }
    ModeButton.active {
        color: $accent;
        text-style: bold;
    }
    ModeButton.active:focus {
        background: $accent 20%;
    }
    """

    can_focus = True

    class Activate(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, name: str, label: str) -> None:
        super().__init__()
        self._name = name
        self._label = label
        self._is_active = False

    def render(self) -> str:
        marker = "▸" if self._is_active else " "
        return f"{marker} {self._label}"

    def set_active(self, active: bool) -> None:
        self._is_active = active
        if self.is_mounted:
            if active:
                self.add_class("active")
            else:
                self.remove_class("active")
            self.refresh()

    def on_click(self, event: events.Click) -> None:
        self.post_message(self.Activate(self._name))
        self.focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            self.post_message(self.Activate(self._name))


class PrimaryNav(Vertical):
    """Left-side rail. Owns no state itself — host App passes ``(name, label)``
    tuples and reactively re-renders when the active mode changes."""

    DEFAULT_CSS = """
    PrimaryNav {
        width: 18;
        background: $background;
        padding: 1 0 0 0;
        border-right: vkey $surface;
    }
    #nav-title {
        height: 2;
        padding: 0 2 1 2;
        color: $accent;
        text-style: bold;
    }
    """

    class Select(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def __init__(self, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self._items = items
        self._active = items[0][0] if items else ""

    def compose(self) -> ComposeResult:
        yield Static("quran-tui", id="nav-title")
        for name, label in self._items:
            button = ModeButton(name, label)
            yield button

    def on_mount(self) -> None:
        # Set initial active state on the buttons.
        self.set_active(self._active)

    def set_active(self, name: str) -> None:
        self._active = name
        if not self.is_mounted:
            return
        for btn in self.query(ModeButton):
            btn.set_active(btn._name == name)

    def on_mode_button_activate(self, message: ModeButton.Activate) -> None:
        self.post_message(self.Select(message.name))
