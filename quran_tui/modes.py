"""Primary-mode containers.

A *mode* is the top-level activity in the app — Listen (audio playback),
Study (MCP-driven research), Read (deferred placeholder). Each mode owns
the middle pane and a small set of mode-scoped key bindings. Modes share
the global now-playing footer + the right-side queue.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import DataTable, Input, Static

from .arabic import render_for_terminal
from .mcp_quran import MCPSession, ToolOutcome
from .models import (
    category_matches_filter,
    track_matches_filter,
)

if TYPE_CHECKING:
    from .controller import Controller


# ---------------------------------------------------------------------------
# Mode base
# ---------------------------------------------------------------------------


class Mode(Container):
    """Base class for primary modes.

    Each subclass sets ``MODE_NAME`` / ``MODE_LABEL`` / ``MODE_ICON``, owns
    its own compose tree, and declares mode-scoped BINDINGS that are merged
    onto the App while this mode is visible.
    """

    MODE_NAME: str = "mode"
    MODE_LABEL: str = "Mode"
    MODE_ICON: str = "•"

    BINDINGS: list = []


# ---------------------------------------------------------------------------
# Listen mode — wraps the existing audio source switcher
# ---------------------------------------------------------------------------


class ListenMode(Mode):
    """Audio playback over quranicaudio / haramain sources."""

    MODE_NAME = "listen"
    MODE_LABEL = "Listen"
    MODE_ICON = "♫"

    DEFAULT_CSS = """
    ListenMode { layout: vertical; }
    #source-bar {
        height: 1;
        background: $surface;
        padding: 0 1;
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
    #listen-breadcrumb {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    #browse-pane {
        border: round $primary;
        padding: 0 1;
        height: 1fr;
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
    #filter-input.hidden { display: none; }
    #filter-input {
        height: 3;
        border: round $accent;
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("1", "switch_source(0)", "Quranic Audio"),
        Binding("2", "switch_source(1)", "Haramain"),
        Binding("enter", "select", "Enter", show=False),
        # "Back" is the user-facing name; ⌫ in the footer makes the key
        # visually obvious without spelling out "Backspace".
        Binding("backspace", "browse_up", "Back", key_display="⌫ "),
        Binding("u", "browse_up", "Back", show=False),
        Binding("r", "refresh_source", "Refresh"),
        Binding("a", "enqueue_selected", "Enqueue"),
        Binding("A", "enqueue_all", "Enqueue all", show=False),
        Binding("f", "focus_filter", "Filter"),
        # Shift+arrow extends a range selection. 'a' enqueues whatever's
        # currently selected (single row OR multi-row range).
        Binding("shift+down", "extend_select(1)", "Extend ↓", show=False),
        Binding("shift+up", "extend_select(-1)", "Extend ↑", show=False),
        Binding("escape", "clear_selection", "Clear selection", show=False),
    ]

    def __init__(self, *, controller: Controller) -> None:
        super().__init__()
        self.controller = controller
        self._filter_query: str = ""
        # Range selection in the browse table. Anchor is the row where the
        # selection started; selected_range expands as the user shift-arrows
        # or shift-clicks.
        self._selection_anchor: int | None = None
        self._selection: set[int] = set()

    def compose(self) -> ComposeResult:
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
        yield Static("", id="listen-breadcrumb")
        with Vertical(id="browse-pane"):
            yield Static("Browse", id="browse-title", classes="pane-title")
            yield Static("", id="browse-status", classes="status-line")
            yield Input(
                placeholder="filter…  (Esc to clear)",
                id="filter-input",
                classes="hidden",
            )
            yield DataTable(id="browse-table", cursor_type="row", zebra_stripes=True)

    async def on_mount(self) -> None:
        table = self.query_one("#browse-table", DataTable)
        table.add_columns("Title", "Subtitle", "Extra")
        await self._reload_browse()

    # --- helpers ---------------------------------------------------------

    def _current_view_items(self):
        bs = self.controller.active_browse()
        result = bs.result
        if result is None:
            return [], []
        if not self._filter_query:
            return list(result.categories), list(result.tracks)
        cats = [
            c for c in result.categories
            if category_matches_filter(c, self._filter_query)
        ]
        tracks = [
            t for t in result.tracks
            if track_matches_filter(t, self._filter_query)
        ]
        return cats, tracks

    async def _reload_browse(self) -> None:
        try:
            await self.controller.refresh()
        except Exception as exc:
            self.query_one("#browse-status", Static).update(
                f"[red]load failed: {exc}[/red]"
            )
            return
        self._render_browse()

    def _render_breadcrumb(self) -> None:
        source = self.controller.state.active_source
        bs = self.controller.active_browse()
        title = bs.result.title if bs.result else (source.label if source else "")
        self.query_one("#listen-breadcrumb", Static).update(f"[dim]›[/dim]  {title}")

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
        # Selection range markers go in column 0.
        for i, c in enumerate(categories):
            count = "" if c.count is None else str(c.count)
            marker = "★ " if i in self._selection else ""
            table.add_row(f"{marker}{c.title}", c.subtitle or "", count)
        offset = len(categories)
        for i, t in enumerate(tracks):
            marker = "★ " if (i + offset) in self._selection else ""
            table.add_row(f"{marker}{t.title}", t.subtitle, t.extra)
        title = self.query_one("#browse-title", Static)
        bs = self.controller.active_browse()
        base = bs.result.title if bs.result else "Browse"
        if self._filter_query:
            title.update(f"{base}  [dim]· filter: {self._filter_query!r}[/dim]")
        else:
            title.update(base)
        bs_result = bs.result
        total = (len(bs_result.categories) + len(bs_result.tracks)) if bs_result else 0
        showing = len(categories) + len(tracks)
        status_line = (
            f"[dim]{showing}/{total}[/dim]"
            if self._filter_query
            else f"[dim]{total} items[/dim]"
        )
        self.query_one("#browse-status", Static).update(status_line)
        if (categories or tracks) and not self.query_one("#filter-input", Input).has_focus:
            table.focus()

    # --- actions ---------------------------------------------------------

    async def action_switch_source(self, index: int) -> None:
        self.controller.set_active_source(index)
        self._clear_filter()
        await self._reload_browse()

    async def action_browse_up(self) -> None:
        bs = self.controller.active_browse()
        if not bs.path:
            return
        try:
            await self.controller.browse_up()
        except Exception as exc:
            self.app.notify(f"Up failed: {exc}", severity="error", timeout=6)
            return
        self._clear_filter()
        self._render_browse()

    async def action_refresh_source(self) -> None:
        source = self.controller.state.active_source
        if source is None:
            return
        self.app.notify(f"refreshing {source.label}…", timeout=2)
        try:
            _result, loaded = await self.controller.force_refresh()
        except Exception as exc:
            self.app.notify(f"Refresh failed: {exc}", severity="error", timeout=6)
            return
        self._clear_filter()
        self._render_browse()
        if loaded >= 0:
            self.app.notify(f"{source.label}: {loaded} items", timeout=3)

    def action_select(self) -> None:
        table = self.query_one("#browse-table", DataTable)
        if not table.has_focus:
            return
        self.run_worker(self._browse_select(table.cursor_row))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "browse-table":
            self.run_worker(self._browse_select(event.cursor_row))

    async def _browse_select(self, row: int) -> None:
        categories, tracks = self._current_view_items()
        if row < len(categories):
            cat = categories[row]
            try:
                await self.controller.browse_into(cat.key)
            except Exception as exc:
                self.app.notify(f"Failed: {exc}", severity="error", timeout=6)
                return
            self._clear_filter()
            self._render_browse()
            return
        idx = row - len(categories)
        if 0 <= idx < len(tracks):
            track = tracks[idx]
            self.app.enqueue_and_play(track)

    def action_enqueue_selected(self) -> None:
        table = self.query_one("#browse-table", DataTable)
        if not table.has_focus:
            return
        categories, tracks = self._current_view_items()
        cat_count = len(categories)

        # Multi-row enqueue if there's an active selection range.
        track_rows = sorted(i for i in self._selection if i >= cat_count)
        if track_rows:
            to_enqueue = [tracks[i - cat_count] for i in track_rows if i - cat_count < len(tracks)]
            if to_enqueue:
                self.app.enqueue_many(to_enqueue)
                self._clear_selection_state()
                self._render_browse()
                return

        # Single-row enqueue (cursor position).
        row = table.cursor_row
        if row < cat_count or row >= cat_count + len(tracks):
            return
        self.app.enqueue(tracks[row - cat_count])

    def action_extend_select(self, direction: int) -> None:
        """Shift+Up / Shift+Down: grow a contiguous selection range."""
        table = self.query_one("#browse-table", DataTable)
        if not table.has_focus:
            return
        categories, tracks = self._current_view_items()
        total = len(categories) + len(tracks)
        if total == 0:
            return
        cur = table.cursor_row
        if self._selection_anchor is None:
            self._selection_anchor = cur
            self._selection = {cur}
        target = max(0, min(total - 1, cur + direction))
        table.cursor_coordinate = (target, 0)
        lo, hi = min(self._selection_anchor, target), max(self._selection_anchor, target)
        self._selection = set(range(lo, hi + 1))
        self._render_browse()
        try:
            table.cursor_coordinate = (target, 0)
        except Exception:
            pass

    def action_clear_selection(self) -> None:
        self._clear_selection_state()
        self._render_browse()

    def _clear_selection_state(self) -> None:
        self._selection_anchor = None
        self._selection = set()

    def action_enqueue_all(self) -> None:
        _, tracks = self._current_view_items()
        if tracks:
            self.app.enqueue_many(tracks)

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
        self.query_one("#browse-table", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter-input":
            return
        self._filter_query = event.value
        self._render_browse()


# ---------------------------------------------------------------------------
# Study mode — MCP browser
# ---------------------------------------------------------------------------


_MCP_SUBTABS = (
    ("search", "Search", ("search_quran", "search_translation", "search_tafsir")),
    ("fetch", "Fetch", ("fetch_quran", "fetch_translation", "fetch_tafsir")),
    ("word", "Word", ("fetch_word_morphology", "fetch_word_paradigm", "fetch_word_concordance")),
    ("meta", "Metadata", ("fetch_quran_metadata", "list_editions")),
)


# Per-tool form schema for v1. Each entry: (display label, kwarg name,
# placeholder). Keep it intentionally tiny — only the fields needed for a
# useful first call. Power users get the full schema via raw MCP.
_TOOL_FORM: dict[str, list[tuple[str, str, str]]] = {
    "search_quran": [("query", "query", "e.g. 'patience in adversity'")],
    "search_translation": [
        ("query", "query", "e.g. 'mercy'"),
        ("editions (optional, comma-sep)", "editions", "e.g. en-asad, en-pickthall"),
    ],
    "search_tafsir": [
        ("query", "query", "e.g. 'throne verse explanation'"),
        ("editions (optional, comma-sep)", "editions", "e.g. ar-jalalayn"),
    ],
    "fetch_quran_metadata": [("surah (1-114)", "surah", "e.g. 36")],
    "fetch_quran": [
        ("surah (1-114)", "surah", "e.g. 1"),
        ("ayah", "ayah", "e.g. 7"),
    ],
    "fetch_translation": [
        ("surah", "surah", "e.g. 1"),
        ("ayah", "ayah", "e.g. 7"),
        ("edition_id", "edition_id", "e.g. en-asad"),
    ],
    "fetch_tafsir": [
        ("surah", "surah", "e.g. 1"),
        ("ayah", "ayah", "e.g. 7"),
        ("edition_id", "edition_id", "e.g. ar-jalalayn"),
    ],
    "fetch_word_morphology": [("word", "word", "Arabic word or transliteration")],
    "fetch_word_paradigm": [("word", "word", "Arabic word or transliteration")],
    "fetch_word_concordance": [("word", "word", "Arabic word, root, or lemma")],
    "list_editions": [("type (quran/tafsir/translation)", "edition_type", "translation")],
}


def _coerce(value: str, name: str) -> Any:
    """Light-touch coercion for form values headed to MCP tools."""
    s = value.strip()
    if not s:
        return None
    if name in {"surah", "ayah"}:
        try:
            return int(s)
        except ValueError:
            return s
    if name == "editions":
        return [chunk.strip() for chunk in s.split(",") if chunk.strip()]
    return s


class StudyMode(Mode):
    """MCP-driven Quran research mode.

    v1 surfaces the quran.ai server as one *class* of tooling, organised
    into four sub-tabs (Search / Fetch / Word / Metadata). Each sub-tab
    offers a small set of canonical tools with hand-curated forms; full
    schema flexibility is intentionally deferred.
    """

    MODE_NAME = "study"
    MODE_LABEL = "Study"
    MODE_ICON = "✦"

    DEFAULT_CSS = """
    StudyMode { layout: vertical; }
    #study-header {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }
    #study-class-row {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
    }
    #study-subtabs {
        height: 1;
        padding: 0 1;
    }
    .study-subtab {
        padding: 0 2;
        margin: 0 1;
        color: $text-muted;
    }
    .study-subtab.active {
        color: $accent;
        text-style: bold;
        background: $accent 15%;
    }
    #study-toolbar {
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    .study-tool {
        padding: 0 2;
        color: $text-muted;
    }
    .study-tool.active {
        color: $accent;
        text-style: bold;
        background: $accent 20%;
    }
    .study-input {
        height: 3;
        margin: 0 1 0 0;
    }
    .study-input-label {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #study-results {
        border: round $primary;
        padding: 0 1;
        height: 1fr;
    }
    #study-status {
        color: $text-muted;
        padding: 0 1;
        height: 1;
    }
    .result-row {
        height: auto;
        min-height: 1;
        padding: 0 1;
    }
    .result-row.odd {
        background: $boost;
    }
    .result-row > .result-key {
        width: auto;
        min-width: 8;
        color: #7aa2f7;
        text-style: bold;
        padding: 0 1 0 0;
    }
    .result-row > .result-text {
        width: 1fr;
        text-align: right;
    }
    .result-row.selected {
        background: $accent 25%;
    }
    .result-row.selected.odd {
        background: $accent 30%;
    }
    """

    BINDINGS = [
        Binding("1", "set_subtab(0)", "Search"),
        Binding("2", "set_subtab(1)", "Fetch"),
        Binding("3", "set_subtab(2)", "Word"),
        Binding("4", "set_subtab(3)", "Metadata"),
        Binding("ctrl+enter", "run_tool", "Run", show=False),
        Binding("j", "select_next", "Next result", show=False),
        Binding("k", "select_prev", "Prev result", show=False),
        Binding("down", "select_next", "Next result", show=False),
        Binding("up", "select_prev", "Prev result", show=False),
        Binding("c", "copy_selected", "Copy ayah"),
        Binding("y", "copy_selected", "Copy", show=False),
    ]

    active_subtab: reactive[int] = reactive(0)

    def __init__(self, *, mcp: MCPSession) -> None:
        super().__init__()
        self._mcp = mcp
        self._active_tool: str = _MCP_SUBTABS[0][2][0]
        self.last_outcome: ToolOutcome | None = None
        self.last_rendered: str = ""
        # Per-result records {ref, raw_text, edition_label} keyed by index.
        self._result_rows: list[dict[str, str]] = []
        self._selected_row: int = -1

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]Quran MCP Tools[/bold]  [dim](mcp.quran.ai · search_tafsir, "
            "search_translation, fetch_quran_metadata …)[/dim]",
            id="study-header",
        )
        yield Static("[dim]class:[/dim]  [bold]quran-mcp[/bold]", id="study-class-row")
        with Horizontal(id="study-subtabs"):
            for i, (_k, label, _tools) in enumerate(_MCP_SUBTABS):
                cls = "study-subtab"
                if i == 0:
                    cls += " active"
                yield Static(f"[dim]{i + 1}[/dim]  {label}", id=f"sub-{i}", classes=cls)
        with Horizontal(id="study-toolbar"):
            for tool in _MCP_SUBTABS[0][2]:
                cls = "study-tool"
                if tool == self._active_tool:
                    cls += " active"
                yield Static(f" {tool} ", id=f"tool-{tool}", classes=cls)
        yield Static("", id="study-form-label", classes="study-input-label")
        yield Input(placeholder="", id="study-input-0", classes="study-input")
        yield Input(placeholder="", id="study-input-1", classes="study-input")
        yield Static("", id="study-status")
        yield VerticalScroll(id="study-results")

    async def on_mount(self) -> None:
        # Initial form for the default tool.
        self._render_subtab()
        self._render_form_for(self._active_tool)
        # Greeting message
        await self._mount_results_body(
            Static(
                "[dim]Pick a tool, type a query, then press [b]Enter[/b] to run.[/dim]"
                "\n[dim]On Arabic results: [b]c[/b] to copy the original ayah · "
                "[b]↑/↓[/b] to navigate.[/dim]",
                markup=True,
            )
        )

    async def _mount_results_body(self, *widgets) -> None:
        scroll = self.query_one("#study-results", VerticalScroll)
        await scroll.remove_children()
        scroll.mount(*widgets)

    # --- rendering helpers ----------------------------------------------

    def _render_subtab(self) -> None:
        for i in range(len(_MCP_SUBTABS)):
            tab = self.query_one(f"#sub-{i}", Static)
            if i == self.active_subtab:
                tab.add_class("active")
            else:
                tab.remove_class("active")
        self.run_worker(self._repaint_toolbar(), exclusive=True)

    async def _repaint_toolbar(self) -> None:
        toolbar = self.query_one("#study-toolbar", Horizontal)
        # remove_children is async — wait for it to finish before mounting.
        await toolbar.remove_children()
        tools = _MCP_SUBTABS[self.active_subtab][2]
        if self._active_tool not in tools:
            self._active_tool = tools[0]
        for tool in tools:
            cls = "study-tool"
            if tool == self._active_tool:
                cls += " active"
            toolbar.mount(Static(f" {tool} ", id=f"tool-{tool}", classes=cls))
        self._render_form_for(self._active_tool)

    def _render_form_for(self, tool: str) -> None:
        spec = _TOOL_FORM.get(tool, [])
        label_w = self.query_one("#study-form-label", Static)
        label_w.update(
            f"[dim]inputs for [b]{tool}[/b]:[/dim]"
            + ("  [dim italic](no inputs — press Enter to run)[/dim italic]" if not spec else "")
        )
        for slot in range(2):
            input_w = self.query_one(f"#study-input-{slot}", Input)
            if slot < len(spec):
                label, _name, placeholder = spec[slot]
                input_w.placeholder = f"{label} — {placeholder}"
                input_w.styles.display = "block"
                input_w.value = ""
            else:
                input_w.styles.display = "none"
                input_w.value = ""

    def _set_active_tool(self, tool: str) -> None:
        self._active_tool = tool
        for cls_tool in _MCP_SUBTABS[self.active_subtab][2]:
            try:
                w = self.query_one(f"#tool-{cls_tool}", Static)
            except Exception:
                continue
            if cls_tool == tool:
                w.add_class("active")
            else:
                w.remove_class("active")
        self._render_form_for(tool)

    # --- actions ---------------------------------------------------------

    def action_set_subtab(self, index: int) -> None:
        if 0 <= index < len(_MCP_SUBTABS):
            self.active_subtab = index
            self._render_subtab()

    def watch_active_subtab(self, _old: int, _new: int) -> None:
        if self.is_mounted:
            self._render_subtab()

    async def action_run_tool(self) -> None:
        await self._run_active_tool()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id and event.input.id.startswith("study-input-"):
            self.run_worker(self._run_active_tool())

    async def _run_active_tool(self) -> None:
        if not self._mcp.is_open:
            self.query_one("#study-status", Static).update(
                "[yellow]opening MCP session…[/yellow]"
            )
            try:
                await self._mcp.open()
            except Exception as exc:
                self.query_one("#study-status", Static).update(
                    f"[red]MCP open failed: {exc}[/red]"
                )
                return
        spec = _TOOL_FORM.get(self._active_tool, [])
        args: dict[str, Any] = {}
        for slot, (_label, name, _ph) in enumerate(spec):
            input_w = self.query_one(f"#study-input-{slot}", Input)
            coerced = _coerce(input_w.value, name)
            if coerced is None:
                continue
            args[name] = coerced

        self.query_one("#study-status", Static).update(
            f"[dim]calling [b]{self._active_tool}[/b]…[/dim]"
        )
        await self._mount_results_body(Static("[dim]…[/dim]", markup=True))

        outcome = await self._mcp.call(self._active_tool, args)
        self._render_outcome(outcome)

    def _render_outcome(self, outcome: ToolOutcome) -> None:
        self.last_outcome = outcome
        status = self.query_one("#study-status", Static)
        if not outcome.ok:
            self.last_rendered = outcome.error or "unknown error"
            status.update("[red]error[/red]")
            self.run_worker(
                self._mount_results_body(
                    Static(f"[red]{self.last_rendered}[/red]", markup=True)
                ),
                exclusive=True,
            )
            return
        data = outcome.data

        results: list[dict] = []
        if isinstance(data, dict):
            for key in ("results", "matches", "hits"):
                lst = data.get(key)
                if isinstance(lst, list) and lst:
                    results = lst
                    break

        if results:
            self.run_worker(self._mount_result_rows(results), exclusive=True)
            self.last_rendered = f"{len(results)} results"
            status.update(f"[dim]ok · {len(results)} results · c to copy[/dim]")
        else:
            rendered = _render_mcp_data(data, fallback_text=outcome.text)
            self.last_rendered = rendered
            self.run_worker(
                self._mount_results_body(Static(rendered, markup=True)),
                exclusive=True,
            )
            status.update("[dim]ok[/dim]")

    async def _mount_result_rows(self, results: list[dict]) -> None:
        """Render search results as horizontal flex rows: ayah-key left,
        ayah text right-aligned. Stores the original (un-reshaped) text on
        each row so 'c' can copy the raw codepoints."""
        self._result_rows = []
        self._selected_row = -1
        rows: list[Horizontal] = []
        for i, r in enumerate(results[:50]):
            ref = r.get("ayah_key") or f"{r.get('surah', '?')}:{r.get('ayah', '?')}"
            raw_text = r.get("text") or r.get("snippet") or ""
            display_text = render_for_terminal(raw_text)
            edition = r.get("edition") or {}
            edition_label = ""
            if isinstance(edition, dict):
                ed_id = edition.get("edition_id") or ""
                ed_author = edition.get("author") or ""
                if ed_id or ed_author:
                    parts = [ed_id]
                    if ed_author:
                        parts.append(ed_author)
                    edition_label = " · ".join(p for p in parts if p)

            self._result_rows.append(
                {
                    "ref": ref,
                    "raw_text": raw_text,
                    "display_text": display_text,
                    "edition_label": edition_label,
                }
            )
            parity = "even" if i % 2 == 0 else "odd"
            row = Horizontal(
                Static(ref, classes="result-key", markup=True),
                Static(display_text, classes="result-text", markup=True),
                id=f"result-row-{i}",
                classes=f"result-row {parity}",
            )
            rows.append(row)
        scroll = self.query_one("#study-results", VerticalScroll)
        await scroll.remove_children()
        if rows:
            scroll.mount(*rows)
            self._select_row(0)

    def _select_row(self, idx: int) -> None:
        if not self._result_rows:
            self._selected_row = -1
            return
        idx = max(0, min(idx, len(self._result_rows) - 1))
        prev = self._selected_row
        self._selected_row = idx
        if prev >= 0:
            try:
                self.query_one(f"#result-row-{prev}", Horizontal).remove_class("selected")
            except Exception:
                pass
        try:
            row = self.query_one(f"#result-row-{idx}", Horizontal)
            row.add_class("selected")
            row.scroll_visible(animate=False)
        except Exception:
            pass
        # Echo edition info in the status so the user knows what they're about to copy.
        rec = self._result_rows[idx]
        ed = f"  ·  {rec['edition_label']}" if rec["edition_label"] else ""
        self.query_one("#study-status", Static).update(
            f"[dim]row {idx + 1}/{len(self._result_rows)}  ·  {rec['ref']}{ed}  ·  "
            "press [b]c[/b] to copy[/dim]"
        )

    def action_select_next(self) -> None:
        if self._result_rows:
            self._select_row(self._selected_row + 1)

    def action_select_prev(self) -> None:
        if self._result_rows:
            self._select_row(self._selected_row - 1)

    def action_copy_selected(self) -> None:
        if not self._result_rows or self._selected_row < 0:
            self.app.notify(
                "no result selected — type a query and run a tool first",
                severity="information",
                timeout=3,
            )
            return
        rec = self._result_rows[self._selected_row]
        payload = rec["raw_text"]
        if not payload:
            self.app.notify("nothing to copy on this row", severity="warning", timeout=3)
            return
        # Textual's copy_to_clipboard emits OSC-52, which Windows Terminal +
        # most modern terminals honour. Falls back to a notification if the
        # terminal doesn't grant clipboard access.
        try:
            self.app.copy_to_clipboard(payload)
            self.app.notify(
                f"copied original Arabic for {rec['ref']} ({len(payload)} chars)",
                timeout=3,
            )
        except Exception as exc:
            self.app.notify(
                f"copy failed ({exc}); your terminal may not support OSC-52",
                severity="warning",
                timeout=5,
            )

    def on_click(self, event) -> None:
        # Tap on a sub-tab or tool name.
        widget = getattr(event, "widget", None)
        if widget is None:
            return
        wid = widget.id or ""
        if wid.startswith("sub-"):
            try:
                idx = int(wid.split("-", 1)[1])
            except ValueError:
                return
            self.action_set_subtab(idx)
            return
        if wid.startswith("tool-"):
            self._set_active_tool(wid.split("-", 1)[1])
            return
        # Clicks anywhere on a result row select it.
        node = widget
        while node is not None:
            nid = getattr(node, "id", "") or ""
            if nid.startswith("result-row-"):
                try:
                    self._select_row(int(nid.split("-", 2)[2]))
                except ValueError:
                    pass
                return
            node = getattr(node, "parent", None)


def _render_mcp_data(data: Any, *, fallback_text: str) -> str:
    """Best-effort markup rendering of a tool's structured response.

    Tries common shapes (``results`` / ``matches`` / ``editions``) first and
    falls back to a json dump.
    """
    if isinstance(data, dict):
        # Search-like responses
        for key in ("results", "matches", "hits"):
            results = data.get(key)
            if isinstance(results, list):
                if not results:
                    return f"[dim]no {key}[/dim]"
                lines = []
                for r in results[:30]:
                    lines.append(_format_result_row(r))
                if len(results) > 30:
                    lines.append(
                        f"[dim italic]… +{len(results) - 30} more (refine query to narrow)[/dim italic]"
                    )
                return "\n".join(lines)
        # Editions
        if isinstance(data.get("editions"), list):
            eds = data["editions"]
            if not eds:
                return "[dim]no editions[/dim]"
            return "\n".join(
                f"[bold]{e.get('edition_id', '?')}[/bold]  {e.get('name', '?')}  "
                f"[dim]· {e.get('lang', '?')} · {(e.get('author') or '')}[/dim]"
                for e in eds[:80]
            )
        # Metadata
        if "surah" in data or "ayah" in data:
            return _format_metadata(data)
    # Fallback: pretty json (truncated)
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False) if data is not None else fallback_text
    except Exception:
        text = fallback_text
    if len(text) > 10_000:
        text = text[:10_000] + "\n[dim]… (truncated)[/dim]"
    return text or "[dim]empty response[/dim]"


def _format_result_row(r: dict) -> str:
    ref = r.get("ayah_key") or f"{r.get('surah', '?')}:{r.get('ayah', '?')}"
    text = r.get("text") or r.get("snippet") or ""
    text = (text[:240] + "…") if len(text) > 240 else text
    # Reshape + bidi if Arabic — terminals can't apply the bidi algorithm
    # themselves, so we hand them a presentation-form, LTR-laid-out string.
    text = render_for_terminal(text)
    edition = (r.get("edition") or {})
    edition_label = ""
    if isinstance(edition, dict):
        ed_id = edition.get("edition_id") or ""
        ed_author = edition.get("author") or ""
        if ed_id or ed_author:
            edition_label = (
                f"  [dim]{ed_id}{' · ' + ed_author if ed_author else ''}[/dim]"
            )
    return f"[bold #7aa2f7]{ref}[/]  {text}{edition_label}"


def _format_metadata(data: dict) -> str:
    bits = []
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v_text = json.dumps(v, ensure_ascii=False)
            if len(v_text) > 200:
                v_text = v_text[:200] + "…"
        else:
            v_text = str(v)
        bits.append(f"[bold]{k}[/]  {render_for_terminal(v_text)}")
    return "\n".join(bits)


# ---------------------------------------------------------------------------
# Read mode — placeholder
# ---------------------------------------------------------------------------


class ReadMode(Mode):
    MODE_NAME = "read"
    MODE_LABEL = "Read"
    MODE_ICON = "📖"

    DEFAULT_CSS = """
    ReadMode {
        layout: vertical;
        align: center middle;
    }
    #read-placeholder {
        padding: 2 4;
        border: round $accent;
        background: $surface;
    }
    """

    BINDINGS: list = []

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]Read[/]\n\n"
            "[dim]Verse-by-verse reading view with side-by-side translation\n"
            "and tafsir — coming in a future release.[/dim]\n\n"
            "[dim italic]This pane is intentionally empty in v0.2.[/dim italic]",
            id="read-placeholder",
            markup=True,
        )
