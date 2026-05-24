"""Textual front-end for quran-tui.

The UI sits on top of :class:`~quran_tui.controller.Controller` and
exposes one primary mode (``Audio``) with two switchable sub-sources
(``quranicaudio`` / ``haramain``). Each source is browseable as a tree —
press ``Enter`` to drill into a category, ``Backspace`` to go up. The
queue and now-playing footer are shared across sources.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from .control import ControlServer
from .controller import Controller
from .models import (
    Category,
    Track,
    category_matches_filter,
    format_duration,
    track_matches_filter,
)

# ---------------------------------------------------------------------------
# Trackbar (full-width, click-to-seek)
# ---------------------------------------------------------------------------


class Trackbar(Static):
    DEFAULT_CSS = """
    Trackbar {
        height: 1;
        background: transparent;
    }
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

    def watch_position(self, _old: float, _new: float) -> None:
        if self.is_mounted:
            self.refresh()

    def watch_duration(self, _old: float, _new: float) -> None:
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
            title_w.update("[dim italic]nothing playing — press 1 / 2 to browse[/dim italic]")
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
# Help overlay
# ---------------------------------------------------------------------------


HELP_LINES = [
    ("1 / 2", "Switch source (Quranic Audio / Haramain)"),
    ("Enter", "Drill into category · play track"),
    ("Backspace / u", "Go up one level in the browse tree"),
    ("r", "Refresh active source (re-fetch upstream)"),
    ("a", "Enqueue selected track"),
    ("A", "Enqueue every track in the current view"),
    ("d / Delete", "Dequeue (when focused on the Queue pane)"),
    ("f", "Filter the current view by title / subtitle"),
    ("Space", "Play / pause"),
    ("n / b", "Next / previous queued track"),
    ("[ / ]", "Seek −10s / +10s"),
    ("- / =", "Volume down / up"),
    ("s", "Shuffle the upcoming queue"),
    ("Click trackbar", "Seek to that point in the track"),
    ("Ctrl+P", "Command palette (themes, etc.)"),
    ("?", "Toggle this help"),
    ("q / Ctrl+C", "Quit"),
]


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-box {
        width: 64;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    #help-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .help-key { color: $accent; text-style: bold; width: 20; }
    .help-row { height: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("quran-tui — key bindings", id="help-title")
            with VerticalScroll():
                for key, desc in HELP_LINES:
                    with Horizontal(classes="help-row"):
                        yield Static(key, classes="help-key")
                        yield Static(desc)
            yield Center(
                Static("[dim]press ? or Esc to close[/dim]", markup=True)
            )


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------


class QuranTuiApp(App[None]):
    TITLE = "quran-tui"
    SUB_TITLE = "Quran audio in your terminal"

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }
    #source-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    .source-tab {
        padding: 0 2;
        margin: 0 1;
        color: $text-muted;
    }
    .source-tab.active {
        color: $accent;
        text-style: bold;
        background: $accent 15%;
    }
    #breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    #body {
        height: 1fr;
        margin: 0 1;
    }
    #browse-pane {
        width: 2fr;
        height: 100%;
        border: round $primary;
        padding: 0 1;
        margin-right: 1;
    }
    #queue-pane {
        width: 1fr;
        height: 100%;
        border: round $secondary;
        padding: 0 1;
    }
    .pane-title {
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    .status-line {
        color: $text-muted;
        padding: 0 1;
        height: 1;
    }
    NowPlaying {
        height: 5;
        border: heavy $accent;
        padding: 0 1;
        margin: 0 1 0 1;
    }
    #np-title-row { height: 1; }
    #np-title { width: 1fr; }
    #np-time { width: auto; }
    #np-bar { width: 1fr; height: 1; }
    #np-meta { height: 1; }
    DataTable { height: 1fr; }
    DataTable > .datatable--cursor { background: $accent 30%; }
    #filter-input.hidden { display: none; }
    #filter-input {
        height: 3;
        border: round $accent;
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("question_mark", "show_help", "Help"),
        Binding("space", "toggle_pause", "Play/Pause", priority=True),
        Binding("1", "switch_source(0)", "Quranic Audio"),
        Binding("2", "switch_source(1)", "Haramain"),
        Binding("backspace", "browse_up", "Up a level"),
        Binding("u", "browse_up", "Up", show=False),
        Binding("r", "refresh_source", "Refresh"),
        Binding("n", "next_track", "Next"),
        Binding("b", "prev_track", "Prev"),
        Binding("left_square_bracket", "seek_back", "-10s"),
        Binding("right_square_bracket", "seek_fwd", "+10s"),
        Binding("minus", "vol_down", "Vol-"),
        Binding("equals_sign", "vol_up", "Vol+"),
        Binding("a", "enqueue_selected", "Enqueue"),
        Binding("A", "enqueue_all", "Enqueue all", show=False),
        Binding("d", "dequeue_selected", "Dequeue"),
        Binding("delete", "dequeue_selected", "Dequeue", show=False),
        Binding("s", "shuffle_upcoming", "Shuffle"),
        Binding("f", "focus_filter", "Filter"),
    ]

    def __init__(
        self,
        *,
        controller: Controller,
        control_port: int = 0,
    ) -> None:
        super().__init__()
        self.controller = controller
        self._control_port = control_port
        self._control_server: ControlServer | None = None
        self._progress_task: asyncio.Task[None] | None = None
        self._loading_track_id: str | None = None
        self._filter_query: str = ""

    # --- compose ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="source-bar"):
            for i, src in enumerate(self.controller.state.sources):
                cls = "source-tab"
                if i == self.controller.state.active_source_index:
                    cls += " active"
                yield Static(
                    f" {i + 1}  {src.label} ",
                    id=f"source-tab-{i}",
                    classes=cls,
                )
        yield Static("", id="breadcrumb")
        with Horizontal(id="body"):
            with Vertical(id="browse-pane"):
                yield Static("Browse", id="browse-title", classes="pane-title")
                yield Static("", id="browse-status", classes="status-line")
                yield Input(
                    placeholder="filter…  (Esc to clear)",
                    id="filter-input",
                    classes="hidden",
                )
                yield DataTable(
                    id="browse-table", cursor_type="row", zebra_stripes=True
                )
            with Vertical(id="queue-pane"):
                yield Static("Queue", id="queue-title", classes="pane-title")
                yield Static("empty", id="queue-status", classes="status-line")
                yield DataTable(
                    id="queue-table", cursor_type="row", zebra_stripes=True
                )
        yield NowPlaying(id="now-playing")
        yield Footer()

    # --- lifecycle -------------------------------------------------------

    async def on_mount(self) -> None:
        browse = self.query_one("#browse-table", DataTable)
        browse.add_columns("Title", "Subtitle", "Extra")
        queue = self.query_one("#queue-table", DataTable)
        queue.add_columns("#", "Title", "Source", "Time")

        try:
            await self.controller.start()
        except Exception as exc:
            self.notify(f"Failed to start mpv: {exc}", severity="error", timeout=10)
            return

        add_listener = getattr(
            self.controller._player, "add_event_listener", None
        )
        if callable(add_listener):
            add_listener(self._on_mpv_event)

        self._progress_task = asyncio.create_task(self._poll_progress())

        if self._control_port:
            await self._start_control_server()

        # Initial root browse for source 0.
        await self._reload_browse()
        browse.focus()

    async def on_unmount(self) -> None:
        if self._progress_task is not None:
            self._progress_task.cancel()
            try:
                await self._progress_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._control_server is not None:
            await self._control_server.stop()
            self._control_server = None
        await self.controller.quit()

    async def _start_control_server(self) -> None:
        server = ControlServer(port=self._control_port)
        server.register("play-pause", lambda: self.run_action("toggle_pause"))
        server.register("next", lambda: self.run_action("next_track"))
        server.register("prev", lambda: self.run_action("prev_track"))
        server.register("previous", lambda: self.run_action("prev_track"))
        server.register("volume-up", lambda: self.run_action("vol_up"))
        server.register("volume-down", lambda: self.run_action("vol_down"))
        server.register("seek-forward", lambda: self.run_action("seek_fwd"))
        server.register("seek-backward", lambda: self.run_action("seek_back"))
        try:
            await server.start()
        except OSError as exc:
            self.notify(
                f"control port {self._control_port} unavailable: {exc}",
                severity="warning",
                timeout=6,
            )
            return
        self._control_server = server

    # --- browsing --------------------------------------------------------

    def _current_view_items(
        self,
    ) -> tuple[list[Category], list[Track]]:
        bs = self.controller.active_browse()
        result = bs.result
        if result is None:
            return [], []
        if not self._filter_query:
            return list(result.categories), list(result.tracks)
        categories = [
            c for c in result.categories if category_matches_filter(c, self._filter_query)
        ]
        tracks = [
            t for t in result.tracks if track_matches_filter(t, self._filter_query)
        ]
        return categories, tracks

    async def _reload_browse(self) -> None:
        try:
            await self.controller.refresh()
        except Exception as exc:
            self._set_browse_status(f"[red]load failed: {exc}[/red]")
            self.notify(f"Load failed: {exc}", severity="error", timeout=6)
            return
        self._render_browse()

    def _set_browse_status(self, markup: str) -> None:
        self.query_one("#browse-status", Static).update(markup)

    def _render_breadcrumb(self) -> None:
        source = self.controller.state.active_source
        if source is None:
            self.query_one("#breadcrumb", Static).update("")
            return
        bs = self.controller.active_browse()
        title = bs.result.title if bs.result else source.label
        self.query_one("#breadcrumb", Static).update(f"[dim]›[/dim]  {title}")

    def _render_source_tabs(self) -> None:
        for i in range(len(self.controller.state.sources)):
            tab = self.query_one(f"#source-tab-{i}", Static)
            if i == self.controller.state.active_source_index:
                tab.add_class("active")
            else:
                tab.remove_class("active")

    def _render_browse(self) -> None:
        self._render_breadcrumb()
        self._render_source_tabs()
        categories, tracks = self._current_view_items()
        table = self.query_one("#browse-table", DataTable)
        table.clear()
        for c in categories:
            count = "" if c.count is None else f"{c.count}"
            table.add_row(f"📁 {c.title}", c.subtitle or "", count)
        for t in tracks:
            table.add_row(
                t.title,
                t.subtitle,
                t.extra,
            )
        title = self.query_one("#browse-title", Static)
        bs = self.controller.active_browse()
        base = bs.result.title if bs.result else "Browse"
        if self._filter_query:
            title.update(f"{base}  [dim]· filter: {self._filter_query!r}[/dim]")
        else:
            title.update(base)
        bs_result = bs.result
        total_cats = len(bs_result.categories) if bs_result else 0
        total_tracks = len(bs_result.tracks) if bs_result else 0
        showing = len(categories) + len(tracks)
        total = total_cats + total_tracks
        status = (
            f"[dim]{showing}/{total}[/dim]" if self._filter_query else f"[dim]{total} items[/dim]"
        )
        self._set_browse_status(status)
        # Try to keep focus where it was.
        if (categories or tracks) and not self.query_one("#filter-input", Input).has_focus:
            table.focus()

    # --- actions ---------------------------------------------------------

    async def action_switch_source(self, index: int) -> None:
        self.controller.set_active_source(index)
        self._clear_filter()
        await self._reload_browse()

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_browse_up(self) -> None:
        bs = self.controller.active_browse()
        if not bs.path:
            return
        try:
            await self.controller.browse_up()
        except Exception as exc:
            self.notify(f"Up failed: {exc}", severity="error", timeout=6)
            return
        self._clear_filter()
        self._render_browse()

    async def action_refresh_source(self) -> None:
        source = self.controller.state.active_source
        if source is None:
            return
        self.notify(f"refreshing {source.label}…", timeout=2)
        try:
            _result, loaded = await self.controller.force_refresh()
        except Exception as exc:
            self.notify(f"Refresh failed: {exc}", severity="error", timeout=6)
            return
        self._clear_filter()
        self._render_browse()
        if loaded >= 0:
            self.notify(f"{source.label}: {loaded} items", timeout=3)
        else:
            self.notify(f"{source.label} re-loaded", timeout=2)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "browse-table":
            self.run_worker(self._browse_select(event.cursor_row))
        elif event.data_table.id == "queue-table":
            row = event.cursor_row
            queue = self.controller.state.queue
            if 0 <= row < len(queue):
                self.controller.set_queue_index(row)
                self._play_current_queue_track()

    async def _browse_select(self, row: int) -> None:
        categories, tracks = self._current_view_items()
        if row < len(categories):
            cat = categories[row]
            try:
                await self.controller.browse_into(cat.key)
            except Exception as exc:
                self.notify(f"Failed: {exc}", severity="error", timeout=6)
                return
            self._clear_filter()
            self._render_browse()
            return
        idx = row - len(categories)
        if 0 <= idx < len(tracks):
            track = tracks[idx]
            self.controller.enqueue(track)
            self.controller.set_queue_index(len(self.controller.state.queue) - 1)
            self._refresh_queue()
            self._play_current_queue_track()

    def action_enqueue_selected(self) -> None:
        focused = self.focused
        if not isinstance(focused, DataTable):
            return
        row = focused.cursor_row
        if focused.id == "browse-table":
            categories, tracks = self._current_view_items()
            if row < len(categories) or row >= len(categories) + len(tracks):
                return
            track = tracks[row - len(categories)]
            self.controller.enqueue(track)
            self._refresh_queue()
            self.notify(f"queued: {track.title}", timeout=2)
            if self.controller.state.queue_index == -1:
                self.controller.set_queue_index(len(self.controller.state.queue) - 1)
                self._play_current_queue_track()

    def action_enqueue_all(self) -> None:
        _, tracks = self._current_view_items()
        if not tracks:
            return
        n = self.controller.enqueue_many(tracks)
        self._refresh_queue()
        self.notify(f"queued: {n} tracks", timeout=3)
        if self.controller.state.queue_index == -1 and self.controller.state.queue:
            self.controller.set_queue_index(0)
            self._play_current_queue_track()

    def action_dequeue_selected(self) -> None:
        focused = self.focused
        if not isinstance(focused, DataTable) or focused.id != "queue-table":
            self.notify(
                "Focus the Queue pane (Tab) first to dequeue.",
                severity="information",
                timeout=3,
            )
            return
        row = focused.cursor_row
        removed, was_current = self.controller.dequeue(row)
        if removed is None:
            return
        self._refresh_queue()
        self.notify(f"removed: {removed.title}", timeout=2)
        if was_current:
            state = self.controller.state
            if 0 <= state.queue_index < len(state.queue):
                self._play_current_queue_track()
            else:
                self.run_worker(self._stop_playback())

    async def _stop_playback(self) -> None:
        stop = getattr(self.controller._player, "stop", None)
        if callable(stop):
            try:
                await stop()
            except Exception:
                pass
        np = self.query_one(NowPlaying)
        np.track = None
        np.paused = True
        np.position = 0.0
        np.duration = 0.0
        np.queue_position = ""

    def action_shuffle_upcoming(self) -> None:
        count = self.controller.shuffle_upcoming()
        if count > 0:
            self._refresh_queue()
            self.notify(f"Shuffled {count} upcoming tracks", timeout=2)
        else:
            self.notify(
                "Nothing to shuffle.", severity="information", timeout=3
            )

    async def action_toggle_pause(self) -> None:
        await self.controller.toggle_pause()
        self.query_one(NowPlaying).paused = self.controller.state.paused

    async def action_next_track(self) -> None:
        state = self.controller.state
        if state.queue_index + 1 < len(state.queue):
            self.controller.set_queue_index(state.queue_index + 1)
            self._refresh_queue()
            self._play_current_queue_track()

    async def action_prev_track(self) -> None:
        state = self.controller.state
        if state.queue_index > 0:
            self.controller.set_queue_index(state.queue_index - 1)
            self._refresh_queue()
            self._play_current_queue_track()

    async def action_seek_back(self) -> None:
        seek = getattr(self.controller._player, "seek", None)
        if callable(seek):
            try:
                await seek(-10, relative=True)
            except Exception:
                pass

    async def action_seek_fwd(self) -> None:
        seek = getattr(self.controller._player, "seek", None)
        if callable(seek):
            try:
                await seek(10, relative=True)
            except Exception:
                pass

    async def action_vol_down(self) -> None:
        np = self.query_one(NowPlaying)
        await self.controller.set_volume(np.volume - 5)
        np.volume = self.controller.state.volume

    async def action_vol_up(self) -> None:
        np = self.query_one(NowPlaying)
        await self.controller.set_volume(np.volume + 5)
        np.volume = self.controller.state.volume

    def action_focus_filter(self) -> None:
        f = self.query_one("#filter-input", Input)
        f.remove_class("hidden")
        f.focus()

    def _clear_filter(self) -> None:
        self._filter_query = ""
        try:
            f = self.query_one("#filter-input", Input)
            f.value = ""
            f.add_class("hidden")
        except Exception:
            pass

    async def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            f = self.query_one("#filter-input", Input)
            if f.has_focus:
                event.stop()
                event.prevent_default()
                self._clear_filter()
                self._render_browse()
                self.query_one("#browse-table", DataTable).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "filter-input":
            return
        event.input.add_class("hidden")
        try:
            self.query_one("#browse-table", DataTable).focus()
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter-input":
            return
        self._filter_query = event.value
        self._render_browse()

    # --- queue rendering & playback --------------------------------------

    def _refresh_queue(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        state = self.controller.state
        for i, t in enumerate(state.queue):
            marker = "[#9ece6a]▶[/#9ece6a]" if i == state.queue_index else str(i + 1)
            table.add_row(marker, t.title, t.source, t.duration_text)
        status = self.query_one("#queue-status", Static)
        if state.queue:
            pos = state.queue_index + 1 if state.queue_index >= 0 else 0
            status.update(f"[dim]{pos}/{len(state.queue)} · a / d to add / remove[/dim]")
        else:
            status.update("[dim]empty[/dim]")
        self._update_np_queue_position()

    def _update_np_queue_position(self) -> None:
        state = self.controller.state
        np = self.query_one(NowPlaying)
        if not state.queue or state.queue_index < 0:
            np.queue_position = ""
        else:
            np.queue_position = f"{state.queue_index + 1}/{len(state.queue)}"

    def _play_current_queue_track(self) -> None:
        track = self.controller.current_queue_track()
        if track is None:
            return
        self._loading_track_id = track.track_id
        np = self.query_one(NowPlaying)
        np.track = track
        np.position = 0.0
        np.duration = float(track.duration_seconds or 0)
        np.paused = False
        np.loading = True
        self._update_np_queue_position()
        self.run_worker(self._play_track_worker(track))

    async def _play_track_worker(self, track: Track) -> None:
        await self.controller.play_track(track)
        if self._loading_track_id != track.track_id:
            return
        np = self.query_one(NowPlaying)
        np.loading = False
        np.paused = self.controller.state.paused

    # --- click-to-seek + mpv events --------------------------------------

    async def on_trackbar_seek(self, message: Trackbar.Seek) -> None:
        np = self.query_one(NowPlaying)
        if np.duration <= 0:
            return
        target = max(0.0, min(np.duration, message.fraction * np.duration))
        seek = getattr(self.controller._player, "seek", None)
        if callable(seek):
            try:
                await seek(target, relative=False)
                np.position = target
            except Exception:
                pass

    async def _on_mpv_event(self, event: dict[str, Any]) -> None:
        if event.get("event") != "end-file":
            return
        if event.get("reason") not in ("eof", "error"):
            return
        state = self.controller.state
        if state.queue_index + 1 < len(state.queue):
            self.controller.set_queue_index(state.queue_index + 1)
            self._refresh_queue()
            self._play_current_queue_track()
        else:
            self.query_one(NowPlaying).paused = True

    async def _poll_progress(self) -> None:
        np = self.query_one(NowPlaying)
        player = self.controller._player
        getter_pos = getattr(player, "get_position", None)
        getter_dur = getattr(player, "get_duration", None)
        getter_pause = getattr(player, "is_paused", None)
        if not (
            callable(getter_pos)
            and callable(getter_dur)
            and callable(getter_pause)
        ):
            return
        while True:
            try:
                pos = await getter_pos()
                dur = await getter_dur()
                paused = await getter_pause()
            except Exception:
                await asyncio.sleep(0.5)
                continue
            if pos is not None:
                np.position = float(pos)
            if dur is not None and dur > 0:
                np.duration = float(dur)
            np.paused = bool(paused)
            await asyncio.sleep(0.5)
