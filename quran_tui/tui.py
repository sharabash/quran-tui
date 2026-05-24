"""Top-level Textual app for quran-tui.

Owns the layout (sidebar · mode container · right tabs · footer) and
forwards mode-agnostic playback bindings to the controller. Per-mode
behaviour lives in :mod:`quran_tui.modes`.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)

from .control import ControlServer
from .controller import Controller
from .mcp_quran import MCPSession
from .models import Track
from .modes import ListenMode, Mode, ReadMode, StudyMode
from .widgets import NowPlaying, PrimaryNav, Trackbar

HELP_LINES = [
    ("Alt+1 / 2 / 3", "Switch primary mode (Listen · Study · Read)"),
    ("1 / 2", "Within Listen: switch source (Quranic Audio · Haramain)"),
    ("1 / 2 / 3 / 4", "Within Study: switch sub-tab (Search · Fetch · Word · Metadata)"),
    ("Enter", "Drill into category · play track · run MCP tool"),
    ("Backspace / u", "Go up one level (Listen browse tree)"),
    ("r", "Refresh active source (re-fetch upstream)"),
    ("a", "Enqueue selected track"),
    ("A", "Enqueue every visible track"),
    ("d / Delete", "Dequeue (focus the Queue pane first)"),
    ("f", "Filter the current Listen view"),
    ("Space", "Play / pause"),
    ("n / b", "Next / previous queued track"),
    ("[ / ]", "Seek −10s / +10s"),
    ("- / =", "Volume down / up"),
    ("s", "Shuffle the upcoming queue"),
    ("Click trackbar", "Seek to that point"),
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
        width: 70;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    #help-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .help-key { color: $accent; text-style: bold; width: 22; }
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
            yield Center(Static("[dim]press ? or Esc to close[/dim]", markup=True))


class QuranTuiApp(App[None]):
    TITLE = "quran-tui"
    SUB_TITLE = "Quran tools in your terminal"

    CSS = """
    Screen { layout: vertical; background: $background; }

    #shell {
        layout: horizontal;
        height: 1fr;
    }

    #mode-host {
        width: 3fr;
        height: 100%;
        padding: 0 1;
    }

    #right-pane {
        width: 1fr;
        min-width: 36;
        height: 100%;
        border: round $secondary;
        padding: 0 1;
    }
    #right-pane TabPane {
        padding: 0;
    }
    .queue-status {
        color: $text-muted;
        padding: 0 1;
        height: 1;
    }

    NowPlaying {
        height: 5;
        border: heavy $accent;
        padding: 0 1;
        margin: 0 1;
    }
    #np-title-row { height: 1; }
    #np-title { width: 1fr; }
    #np-time { width: auto; }
    #np-bar { width: 1fr; height: 1; }
    #np-meta { height: 1; }

    DataTable { height: 1fr; }
    DataTable > .datatable--cursor { background: $accent 30%; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", "Quit", priority=True, show=False),
        Binding("question_mark", "show_help", "Help"),
        # Several routes to the same mode-switch action so we don't depend on
        # any one keyboard layout / terminal: Alt+N, Ctrl+N, and F1..F3 all
        # work. Plain digits route through the sidebar via the ModeButton
        # focus + Enter path.
        Binding("alt+1", "switch_mode('listen')", "Listen", priority=True),
        Binding("alt+2", "switch_mode('study')", "Study", priority=True),
        Binding("alt+3", "switch_mode('read')", "Read", priority=True),
        Binding("ctrl+1", "switch_mode('listen')", show=False, priority=True),
        Binding("ctrl+2", "switch_mode('study')", show=False, priority=True),
        Binding("ctrl+3", "switch_mode('read')", show=False, priority=True),
        Binding("f1", "switch_mode('listen')", show=False, priority=True),
        Binding("f2", "switch_mode('study')", show=False, priority=True),
        Binding("f3", "switch_mode('read')", show=False, priority=True),
        Binding("tab", "focus_nav", show=False),
        Binding("space", "toggle_pause", "Play/Pause", priority=True),
        Binding("n", "next_track", "Next"),
        Binding("b", "prev_track", "Prev"),
        Binding("left_square_bracket", "seek_back", "-10s", key_display="[ "),
        Binding("right_square_bracket", "seek_fwd", "+10s", key_display="] "),
        Binding("minus", "vol_down", "Vol-", key_display="- "),
        Binding("equals_sign", "vol_up", "Vol+", key_display="= "),
        Binding("s", "shuffle_upcoming", "Shuffle", show=False),
        Binding("d", "dequeue_selected", "Dequeue", show=False),
        Binding("delete", "dequeue_selected", "Dequeue", show=False),
        Binding("ctrl+r", "refresh_source", "Refresh", show=False),
    ]

    def __init__(
        self,
        *,
        controller: Controller,
        mcp_session: MCPSession,
        control_port: int = 0,
    ) -> None:
        super().__init__()
        self.controller = controller
        self._mcp = mcp_session
        self._control_port = control_port
        self._control_server: ControlServer | None = None
        self._loading_track_id: str | None = None
        self._modes: dict[str, Mode] = {}
        self._active_mode: str = "listen"

    # --- compose ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="shell"):
            yield PrimaryNav(
                [
                    ("listen", "Listen"),
                    ("study", "Study"),
                    ("read", "Read"),
                ]
            )
            with Container(id="mode-host"):
                listen = ListenMode(controller=self.controller)
                study = StudyMode(mcp=self._mcp)
                read = ReadMode()
                self._modes = {"listen": listen, "study": study, "read": read}
                yield listen
                yield study
                yield read
            with Container(id="right-pane"):
                with TabbedContent(initial="tab-queue"):
                    with TabPane("Queue", id="tab-queue"):
                        yield Static("empty", id="queue-status", classes="queue-status")
                        yield DataTable(
                            id="queue-table",
                            cursor_type="row",
                            zebra_stripes=True,
                        )
        yield NowPlaying(id="now-playing")
        yield Footer()

    # --- lifecycle -------------------------------------------------------

    async def on_mount(self) -> None:
        queue = self.query_one("#queue-table", DataTable)
        queue.add_columns("#", "Title", "Source", "Time")

        # Show only the active mode initially.
        for name, mode in self._modes.items():
            mode.display = name == self._active_mode

        try:
            await self.controller.start()
        except Exception as exc:
            self.notify(f"Failed to start mpv: {exc}", severity="error", timeout=10)
            return

        add_listener = getattr(self.controller._player, "add_event_listener", None)
        if callable(add_listener):
            add_listener(self._on_mpv_event)

        if self._control_port:
            await self._start_control_server()

    async def on_unmount(self) -> None:
        if self._control_server is not None:
            await self._control_server.stop()
            self._control_server = None
        try:
            await self._mcp.close()
        except Exception:
            pass
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

    # --- mode switching --------------------------------------------------

    def action_switch_mode(self, name: str) -> None:
        if name not in self._modes:
            return
        if name == self._active_mode:
            return
        # If we're leaving a mode that toggled select-mode, restore mouse
        # capture so the rest of the app stays clickable.
        outgoing = self._modes.get(self._active_mode)
        exit_select = getattr(outgoing, "action_exit_select_mode", None)
        if callable(exit_select):
            try:
                exit_select()
            except Exception:
                pass
        self._active_mode = name
        for n, mode in self._modes.items():
            mode.display = n == name
        nav = self.query_one(PrimaryNav)
        nav.set_active(name)
        # Focus the new mode so its scoped bindings (1, 2, …) take effect.
        mode = self._modes[name]
        try:
            mode.focus()
        except Exception:
            pass

    def on_primary_nav_select(self, message: PrimaryNav.Select) -> None:
        self.action_switch_mode(message.name)

    def action_focus_nav(self) -> None:
        """Tab → cycle focus into the sidebar."""
        try:
            nav = self.query_one(PrimaryNav)
            nav.focus(scroll_visible=False)
            # Focus the active mode button so digit / Enter work immediately.
            for btn in nav.query("ModeButton"):
                if "active" in btn.classes:
                    btn.focus()
                    return
        except Exception:
            pass

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

    # --- Mouse-capture toggle (selection mode) ---------------------------
    #
    # Textual enables xterm mouse tracking globally so click-to-seek,
    # click-on-sidebar, and click-on-row work. Side effect: the terminal
    # can no longer do native click-drag-to-select, which is how every
    # other TUI (including Claude Code, which uses Ink and never enables
    # mouse tracking in the first place) gets free copy-on-drag.
    #
    # We expose a runtime toggle: when a mode flips into "selection mode",
    # we write the xterm sequences to disable mouse tracking, the terminal
    # takes over drag-selection + auto-copy, and we restore tracking when
    # the user exits the mode.
    #
    # Modes 1000 (basic click), 1002 (button-event), 1003 (any-event),
    # 1006 (SGR coords). We disable all four, then re-enable 1002+1006
    # which is the combination Textual normally sets.

    def set_terminal_mouse_capture(self, enabled: bool) -> None:
        if enabled:
            seq = "\x1b[?1002h\x1b[?1006h"
        else:
            seq = "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l"
        driver = getattr(self, "_driver", None)
        write = getattr(driver, "write", None)
        flush = getattr(driver, "flush", None)
        if callable(write):
            try:
                write(seq)
                if callable(flush):
                    flush()
                return
            except Exception:
                pass
        # Fallback: direct stdout. Less polite but reliable.
        import sys

        try:
            sys.__stdout__.write(seq)
            sys.__stdout__.flush()
        except Exception:
            pass

    # --- queue / playback API shared with modes --------------------------

    def enqueue(self, track: Track) -> None:
        self.controller.enqueue(track)
        self._refresh_queue()
        self.notify(f"queued: {track.title}", timeout=2)
        if self.controller.state.queue_index == -1:
            self.controller.set_queue_index(len(self.controller.state.queue) - 1)
            self._play_current_queue_track()

    def enqueue_many(self, tracks: list[Track]) -> None:
        n = self.controller.enqueue_many(tracks)
        self._refresh_queue()
        self.notify(f"queued: {n} tracks", timeout=3)
        if (
            self.controller.state.queue_index == -1
            and self.controller.state.queue
        ):
            self.controller.set_queue_index(0)
            self._play_current_queue_track()

    def enqueue_and_play(self, track: Track) -> None:
        self.controller.enqueue(track)
        self.controller.set_queue_index(len(self.controller.state.queue) - 1)
        self._refresh_queue()
        self._play_current_queue_track()

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
            status.update(
                f"[dim]{pos}/{len(state.queue)} · a / d to add / remove[/dim]"
            )
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

    # --- queue-pane events -----------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "queue-table":
            row = event.cursor_row
            if 0 <= row < len(self.controller.state.queue):
                self.controller.set_queue_index(row)
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

    # --- global playback bindings ---------------------------------------

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

    # --- click-to-seek + mpv property-change events ---------------------

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
        kind = event.get("event")
        if kind == "property-change":
            np = self.query_one(NowPlaying)
            name = event.get("name")
            data = event.get("data")
            if name == "time-pos":
                if data is not None:
                    np.position = float(data)
            elif name == "duration":
                if data is not None and data > 0:
                    np.duration = float(data)
            elif name == "pause":
                np.paused = bool(data)
            elif name == "volume":
                if data is not None:
                    np.volume = float(data)
            return
        if kind != "end-file":
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
