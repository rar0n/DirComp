"""A zoomable / pannable / selectable timeline-style bar-chart widget.

Originally this only plotted files over time. It's now metric-agnostic: it
plots any numeric "metric" extracted from a list of records against a
`BinAxis` (see binning.py) that snaps bins to a fixed, human-meaningful
grid - calendar units for time, decimal orders of magnitude for size - so
panning the view never changes bin widths, only which bins are visible.
Arbitrary (non-"nice") bins are likewise anchored to a fixed absolute grid
rather than being re-divided from the current view on every pan, so their
edges - and anything tracking one, like a selection or hover highlight -
stay put in absolute terms and only zooming changes the grid.

Behaviour summary
------------------
- The full data range (`global_start` .. `global_end`) is the span covered by
  every loaded record. `reset_view()` sets the *visible* window to that full
  span - this is the "default zoom level" the view starts at. It's a
  view-only operation: it never touches the current selection.
- The visible window (`view_start` .. `view_end`) is divided into bins whose
  width is chosen automatically from `axis.choose_step()`, aiming for
  `bin_count` bins (a hint, not an exact count, since bins must land on nice
  boundaries).
- Mouse wheel zooms in/out, centred on the cursor.
- Plain left-drag pans the view.
- Shift+left-drag *or* right-drag selects a range (drawn as an overlay with
  start/end markers). The selection is stored as an absolute range, so it
  survives panning/zooming until explicitly changed.
- A plain right-click (no drag) zooms out, centred on the click.
- Ctrl+click a bar toggles that bin's files into/out of the current
  selection, independent of any drag-range/shift-range selection -
  builds up a discrete multi-bin selection one click at a time. See
  _toggle_bin_selection.
- A plain left-click (no drag):
    - if there's an active drag-selected range and the click lands inside
      it -> zoom to that range;
    - if there's an active drag-selected range and the click lands outside
      it -> clear the selection;
    - if Shift is held and a single bin was the last plain click -> select
      the inclusive range between that bin and this one (same result as
      dragging between them);
    - if the clicked bin is already fully selected -> zoom to it, same as
      clicking inside a drag-selected range;
    - otherwise, if the click lands on a bar -> show info (count) for the
      bin that was clicked, via the `binInfo` signal, and select its files;
    - otherwise (missed every bar - past the axis edges, or above a bar, in
      the empty space over it) -> clear the selection.
- "Zoom to selection" makes the selected range the new full view - bins
  recompute to a finer nice step automatically. With no active range
  (e.g. after a plain or ctrl+click bin selection), it falls back to the
  span covered by whatever bins are currently selected - see
  _selected_bins_bounds.

Selection is bidirectional and file-based, not bin-based
----------------------------------------------------------
`selected_paths` is the single source of truth for which files are
selected - clicking/dragging here sets it (and emits `selectionChanged` to
tell the host); `set_external_selection()` lets a host app (e.g. a file
list shown alongside this timeline) set it the other way, without
bouncing an echo back via `selectionChanged`. Either way, every bin is
recolored on every change: a bin with every one of its files selected
gets the normal selection color, a bin with only *some* selected gets a
blended "partial" color, and this is recomputed fresh after every pan,
zoom, or reset - so zooming out can merge several selected bins into one
partially-selected bin, and zooming in can split one bin's partial
selection back out across its children, all without touching
`selected_paths` itself.
"""
from __future__ import annotations

import bisect
import math
import os
from typing import Any, Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

import binning
from binning import BinAxis, SizeAxis, TimeAxis

MARGIN_LEFT = 55  # fallback width before there's any data to size against - see _y_axis_margin_left
#: Hard floor for the dynamic left margin (_y_axis_margin_left) - just enough
#: that a tiny max value (e.g. a single digit) still leaves breathing room
#: around its label, well below MARGIN_LEFT itself so genuinely small axes
#: actually get narrower rather than just never getting any wider.
MARGIN_LEFT_MIN = 24
MARGIN_RIGHT = 20
MARGIN_TOP = 24
# Tick labels are drawn at 45 degrees below their tick marks, which eats
# more vertical space than horizontal text did - leave room for that.
MARGIN_BOTTOM = 70
TICK_LABEL_ANGLE_DEG = 45
# Small gap kept between the widest y-axis tick label and the axis line
# itself (see _y_axis_margin_left/_draw_y_axis_labels) - the hover/
# selection/bin-size readout no longer needs its own nudge off the plot's
# left edge, now that it's flush with the axis labels' own inset instead
# (see _paint).
HOVER_READOUT_X_OFFSET = 4
# Mirror of the above for the selected-count readout, which now sits in
# its own right-aligned slot (see _paint) instead of sharing the hover
# text's left-aligned spot -- keeps it off the plot's right edge (and
# clear of MARGIN_RIGHT's tick labels) the same way the offset above
# keeps hover text off the left edge.
SELECTED_READOUT_X_MARGIN = 6

BAR_COLOR = QColor(70, 130, 220)
SELECTION_FILL = QColor(255, 200, 60, 90)
SELECTION_EDGE = QColor(230, 140, 20)
# a bin where only *some* of its files are in the external selection - a
# blend of the normal bar color and the full-selection color, so it reads
# as "partway there" at a glance without being confused with either.
# These five, plus GRID_COLOR/AXIS_TEXT_COLOR/FIXED_RANGE_TEXT_COLOR/
# BACKGROUND_COLOR below, are now only the *default* palette -- each has
# a same-named (or renamed, see set_palette) per-instance counterpart a
# host can override; see the colors block in TimelineWidget.__init__ and
# set_palette/_partial_selection_color/_selection_fill_color.
PARTIAL_SELECTION_COLOR = QColor(
    (BAR_COLOR.red() + SELECTION_EDGE.red()) // 2,
    (BAR_COLOR.green() + SELECTION_EDGE.green()) // 2,
    (BAR_COLOR.blue() + SELECTION_EDGE.blue()) // 2,
)
GRID_COLOR = QColor(60, 60, 60)
AXIS_TEXT_COLOR = QColor(210, 210, 210)
FIXED_RANGE_TEXT_COLOR = QColor(230, 140, 20)
BACKGROUND_COLOR = QColor(30, 30, 32)

# Target bin count. In "nice" mode this is a hint fed to axis.choose_step()
# (actual bins snap to calendar/decade boundaries, so the real count may
# differ slightly); in arbitrary mode it's exact.
DEFAULT_BIN_COUNT = 30
DRAG_THRESHOLD_PX = 2
#: cap on how many y-axis tick marks/gridlines are drawn at once, so a huge
#: count range doesn't turn into a wall of overlapping labels.
Y_AXIS_MAX_TICKS = 10
#: default point size for every piece of text this widget draws itself
#: (x-axis tick labels, y-axis tick labels, the upper-left hover/
#: selection/bin-size readout) -- see set_label_font_size.
DEFAULT_LABEL_FONT_PT = 8
#: x-axis tick label placement/collision (see _x_axis_label_layout):
#: minimum inset between a label's bounding box and the widget's own
#: left/right edge, so a centered label near either end is nudged inward
#: instead of being cut off by the widget boundary.
X_LABEL_EDGE_INSET = 2
#: two neighbouring labels only count as overlapping *across* their text
#: direction if closer than this fraction of the font's line height --
#: fm.height() includes leading/side room the glyphs never actually
#: fill, and a little slack keeps labels of slightly different widths
#: (whose centered origins then differ a little from the bin pitch) from
#: tipping over the edge of what _auto_tick_angle_deg just judged fine.
X_LABEL_CROSS_CLEARANCE = 0.85
#: minimum gap *along* the text direction between two labels, in
#: characters (of "0") -- half of what auto angle asks for before it
#: lets labels stay flat, for the same slack reason as above.
X_LABEL_ALONG_GAP_CHARS = 0.5
#: default hover tint (see TimelineWidget.hover_color/hover_opacity): a
#: hovered bin's bar and label are blended this far toward this color.
HOVER_TINT_COLOR = QColor(255, 255, 255)
HOVER_TINT_OPACITY = 35


def round_up_nice(n: float) -> int:
    """Round `n` up to a "nice" 1/2/5-sequence number (12 -> 20, 47 -> 50,
    130 -> 200, ...) so a frozen vertical-range ceiling looks intentional
    rather than an arbitrary bar height, and gives a little headroom."""
    n = max(n, 1)
    magnitude = 10 ** math.floor(math.log10(n))
    for mult in (1, 2, 5, 10):
        candidate = mult * magnitude
        if candidate >= n:
            return int(candidate)
    return int(n)


def _format_bytes(num: float) -> str:
    """Human-readable byte count (B/KB/MB/GB/TB, 1024-based) for the
    selected/total-size half of the upper-corner readout (see
    paintEvent) -- duplicated here, rather than imported from the host
    app's own equivalent (DirComp's DiffEngineMixin.format_size), so
    this widget stays a standalone, dependency-free component any host
    can drop in without needing that host's own formatting helper."""
    num = float(num)
    step = 1024.0 if BINARY_SIZES else 1000.0
    units = ("B", "KiB", "MiB", "GiB") if BINARY_SIZES else ("B", "kB", "MB", "GB")
    for unit in units:
        if num < step:
            return f"{num:.2f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= step
    return f"{num:.2f} {'TiB' if BINARY_SIZES else 'TB'}"


#: logarithmic sub-grid lines closer than this (in pixels) to a
#: neighbouring gridline are left out
SUBGRID_MIN_GAP_PX = 3.0

#: decimal (kB/MB, 1000) by default; the host flips this with set_binary_sizes
BINARY_SIZES = False


def set_binary_sizes(enabled: bool):
    """Binary (KiB/MiB, 1024) instead of decimal sizes in this module's
    text -- and in the size axis's labels/readouts (binning module)."""
    global BINARY_SIZES
    BINARY_SIZES = bool(enabled)
    binning.set_binary_sizes(enabled)

# Built-in time-based metrics (datetime attributes on FileRecord). "size" is
# handled separately (byte count -> SizeAxis).
TIME_METRICS = {"modified", "changed", "accessed"}


class TimelineWidget(QWidget):
    # emitted with the set of record identities (paths) that fall in the
    # current selection (empty set means "no selection")
    selectionChanged = Signal(set)
    # emitted whenever the visible window changes, so a scrollbar can sync
    viewRangeChanged = Signal(float, float)
    # emitted with a human-readable description whenever a bin is clicked
    binInfo = Signal(str)
    # emitted with a directory path when one's dropped onto this widget --
    # same "open this folder" contract as DirDropLineEdit/DirDropTreeWidget
    # elsewhere in the host app, just from here too.
    directoryDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Halved from an earlier 130px -- lets the File List/Statistics
        # splitter (see ui_builder._build_directory_widgets) squeeze
        # this view down about as far as the file list already could,
        # rather than the file list alone hitting a floor while this
        # one refuses to shrink past it.
        self.setMinimumHeight(65)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.OpenHandCursor)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.setAcceptDrops(True)
        self._drops_enabled = True

        #: When True, flips the hover/selection readout text (top-left
        #: <-> top-right) horizontally -- everything else (bars, axis,
        #: bin edges) stays exactly where it is. Set via set_mirrored,
        #: from the Settings tab's "Include Buttons in Symmetrical GUI"
        #: option (only meaningful for the right pane's own view, and
        #: only while the main Symmetrical GUI setting is also on -- see
        #: ui_builder._build_statistics_panel).
        self._mirrored: bool = False

        self.records: list = []
        #: False until set_records() is called for the first time, even
        #: if it's called with an empty list -- lets paintEvent tell "no
        #: folder loaded here yet at all" apart from "a folder's loaded,
        #: but nothing currently qualifies to be plotted" (e.g. File
        #: mode with Recursive Folder Selections off and every folder
        #: collapsed), which used to show the identical drag-prompt
        #: message either way since both left view_start as None. See
        #: _paint's empty-state branch.
        self._records_loaded: bool = False
        #: Whether the pane this widget belongs to actually has a folder
        #: open -- set by the host app via set_folder_open() (see
        #: listing_ops._refresh_pane_snapshot). None (the default) means
        #: the host never says, e.g. a different app reusing this widget
        #: on its own, in which case _paint falls back to
        #: _records_loaded, the original behavior. Once a host does set
        #: it, it's the only thing deciding the empty-state message:
        #: _records_loaded alone can't tell "no folder here" from "a
        #: folder's loaded, nothing to plot", because the host calls
        #: set_records([]) for a pane with no folder too -- which is
        #: what made a pane with nothing open say "No files in view"
        #: as soon as the *other* pane loaded something, and left that
        #: same message sitting there after "Clear view".
        self._folder_open: Optional[bool] = None
        self.metric_name: str = "modified"
        self.axis: BinAxis = TimeAxis()
        self._value_of: Callable[[Any], float] = lambda r: r.modified.timestamp()
        self._path_of: Callable[[Any], Optional[str]] = lambda r: getattr(r, "path", None)
        #: Byte size of a record, independent of whatever metric is
        #: currently plotted (unlike _value_of, which *is* the size only
        #: when metric == "size") -- feeds the selected/total-size half
        #: of the upper-corner "N files selected" readout (see
        #: paintEvent) and self._total_size/_selected_size below. Same
        #: FileRecord-attribute default set_records()'s other two
        #: defaults use, for a host that hands this widget real
        #: FileRecord objects instead of DirComp's own plain dicts.
        self._size_of: Callable[[Any], float] = lambda r: float(getattr(r, "size", 0) or 0)
        #: Running byte totals kept in step with self.records/
        #: selected_paths by set_records()/_recompute_selected_counts()
        #: respectively, rather than summed fresh on every paintEvent --
        #: see both methods.
        self._total_size: float = 0.0
        self._selected_size: float = 0.0
        #: Host-supplied overrides for the readout's selected/total size
        #: (see set_selected_total_size/set_total_size_override,
        #: paintEvent). self._total_size/_selected_size above only ever
        #: see whichever single type (files, or folders) this pane's
        #: Files/Folders toggle currently has plotted -- a combined
        #: selection spanning both types would silently under-report
        #: here. A host that tracks the real combined figure elsewhere
        #: (e.g. DirComp's own update_selection_count/update_type_counts,
        #: which already correctly avoid double-counting a selected
        #: folder that also has a selected ancestor) can push it in
        #: here instead of leaving the readout to this widget's own,
        #: narrower calculation. None means "no override, use the
        #: internal calculation" -- the default, for a host that never
        #: calls either setter.
        self._selected_size_override: Optional[float] = None
        self._any_selected: bool = False
        self._total_size_override: Optional[float] = None
        #: percent bin_color -> selection_color for partially selected bins
        self.partial_lerp: int = 50
        #: Singular noun for whatever a record actually represents, used in
        #: the hover/selection readouts below ("12 file(s)", "3 files
        #: selected") -- plural is just this + "s" (English-regular for
        #: every caller so far: file/files, folder/folders). Set via
        #: set_records(item_label=...); a caller that doesn't pass one
        #: keeps the original hardcoded "file" wording.
        self._item_label: str = "file"
        #: True = bins snap to a "nice" calendar/decade or magnitude grid.
        #: False = the old behaviour: view span / bin_count equal slices.
        self.nice_bins: bool = True
        self.bin_count: int = DEFAULT_BIN_COUNT
        #: True = flip which way the mouse wheel zooms (see wheelEvent) --
        #: independent of any scroll-direction setting elsewhere in the
        #: app (see set_invert_zoom_direction).
        self.invert_zoom_direction: bool = False

        self.log_scale: bool = False
        self.auto_vrange: bool = True
        #: max-count ceiling used when auto_vrange is off - "latest
        #: automatically determined value", rounded to a nice number.
        self.fixed_max_count: int = 1

        self.global_start: Optional[float] = None
        self.global_end: Optional[float] = None
        self.view_start: Optional[float] = None
        self.view_end: Optional[float] = None

        self._current_step: Any = None
        self.bin_edges: list[float] = []
        self.bin_counts: list[int] = []

        self.selection_range: Optional[tuple[float, float]] = None  # (v0, v1)
        #: Paths currently selected - the single source of truth for bin
        #: highlighting. Set internally by clicking/dragging in this
        #: widget (which also emits `selectionChanged` to tell the host),
        #: or externally via `set_external_selection()` when the host's
        #: own file list selection changes (which does *not* re-emit, to
        #: avoid bouncing the same selection back and forth). Either way,
        #: bin coloring is always derived fresh from this one set, so the
        #: highlight can never go stale relative to whichever side last
        #: changed it.
        self.selected_paths: set = set()
        #: bin_selected_counts[i] = how many of bin i's records are in
        #: selected_paths - kept in lockstep with bin_counts.
        self.bin_selected_counts: list[int] = []

        self._panning = False
        self._pan_last_x = 0.0
        self._selecting = False
        self._select_start_x: Optional[float] = None
        self._select_cur_x: Optional[float] = None
        self._hover_bin: Optional[int] = None
        #: absolute value (bin midpoint) of the hover bin - used to re-find
        #: the same bin after a recompute (pan, zoom, resize) instead of
        #: trusting the old index, which may now point at a different bin.
        self._hover_bin_value: Optional[float] = None
        #: which axis a wheel event would zoom right now, based purely on
        #: mouse x (see wheelEvent's own zone test) - "vertical" over the
        #: y-axis/its left margin, "horizontal" everywhere else in the
        #: widget, None while the mouse isn't over this widget at all.
        #: Drives the axis-highlight in _paint; kept independent of
        #: _hover_bin since a bar can be hovered (or not) regardless of
        #: which zoom zone the same x falls into.
        self._hover_zoom_zone: Optional[str] = None
        #: absolute value (bin midpoint) of the last plain (non-shift)
        #: single-bin click - only used to chain a shift+click into a
        #: range select; bin *highlighting* is driven entirely by
        #: selected_paths, not by this.
        self._last_click_anchor_value: Optional[float] = None

        self._press_x: Optional[float] = None
        self._press_moved = False
        self._press_button: Optional[Qt.MouseButton] = None

        #: point size for every piece of text this widget draws itself --
        #: see set_label_font_size.
        self.label_font_pt: int = DEFAULT_LABEL_FONT_PT

        #: Bottom margin actually used by _plot_rect -- recomputed from
        #: the current font size and however long the angled tick labels
        #: currently are (see _recompute_bottom_margin), instead of
        #: always reserving MARGIN_BOTTOM's worth of room. Starts at
        #: MARGIN_BOTTOM so early paints (before any bins exist) still
        #: look right.
        self._margin_bottom: float = MARGIN_BOTTOM

        #: Degrees the x-axis tick labels are rotated down-right from
        #: their tick mark (see _draw_axis_labels) -- overridable (e.g.
        #: DirComp's common Statistics-view setting) via
        #: set_tick_label_angle. Feeds _recompute_bottom_margin too, so
        #: a shallower angle (more horizontal, less vertical drop) also
        #: shrinks the reserved bottom margin, not just the paint angle.
        self.tick_label_angle_deg: float = TICK_LABEL_ANGLE_DEG

        #: Auto angle mode (see set_auto_tick_angle): when True, the
        #: angle actually used to paint/measure the x-axis tick labels
        #: is recomputed from the current label widths and how many
        #: pixels apart neighbouring ticks are, instead of being
        #: tick_label_angle_deg.
        self.auto_tick_angle: bool = False
        #: "Strip most significant calendar units from horizontal
        #: labels" (see set_strip_calendar_units): only takes effect
        #: while nice_bins is on and the axis has something to strip
        #: (TimeAxis) -- see _strip_active. The three below only matter
        #: while this is on (the Settings tab greys their checkboxes out
        #: otherwise) -- see TimeAxis.format_tick_stripped/strip_context
        #: for exactly what each one changes.
        self.strip_calendar_units: bool = False
        #: "Use calendar unit names instead of numbers" -- month/weekday
        #: names in place of month/weekday numbers, both in the x-axis
        #: labels and in the upper-left "Chart: ..." readout.
        self.strip_use_names: bool = False
        #: "Abbreviate names" -- "Jan"/"Mon" rather than "January"/
        #: "Monday", wherever strip_use_names is putting a name.
        self.strip_abbreviate_names: bool = False
        #: "Use week nr. for 1 week bins" -- e.g. "W12" instead of the
        #: bin's month-day, only for bins exactly 1 week wide.
        self.strip_use_week_number: bool = False
        #: The angle _draw_x_axis_labels/_axis_label_y_offset/
        #: _recompute_bottom_margin actually use -- either
        #: tick_label_angle_deg (manual) or whatever
        #: _auto_tick_angle_deg last worked out (auto). Refreshed by
        #: _recompute_bottom_margin.
        self._effective_tick_angle_deg: float = TICK_LABEL_ANGLE_DEG
        #: The inputs (_auto_angle_key_now) the auto angle was last
        #: computed against -- _paint compares against it to catch
        #: anything that changes label spacing without going through
        #: _recompute_bins (a widget resize, a y-axis label column
        #: widening), see _refresh_auto_angle_if_stale.
        self._auto_angle_key: Optional[tuple] = None

        #: SizeAxis's logarithmic bin-spacing mode (see SizeAxis.log_mode)
        #: -- a widget-level flag rather than living only on self.axis,
        #: since set_records() rebuilds a fresh SizeAxis every call and
        #: would otherwise silently reset it back to off. Only takes
        #: effect while metric_name == "size"; has no meaning for a
        #: TimeAxis and is simply held onto (unapplied) if the metric
        #: gets switched away from size and back.
        self.log_size_axis: bool = False
        #: logarithm base ("2", "e" or "10") of every logarithmic axis:
        #: the log size axis's bins and the log count axis's ticks
        self.log_base: str = binning.DEFAULT_LOG_BASE
        #: draw a log-paper sub-grid in logarithmic axes' grid cells
        #: (see set_subdivide_log_grid)
        self.subdivide_log_grid: bool = False

        # ---------------------------------------------------- colors
        # Per-instance, overridable via set_palette -- default to the
        # module-level constants above, so a widget nobody's customized
        # looks exactly as it always did. Kept as QColor instances
        # (rather than re-parsing a hex string on every paint) the same
        # way the module constants themselves are. This widget has no
        # notion of "modes" of its own -- a host that wants two
        # independent palettes swapped in and out (e.g. DirComp's
        # File/Folder toggle) just calls set_palette again with the
        # other palette's values whenever its own active mode changes.
        self.bin_color = QColor(BAR_COLOR)
        self.selection_color = QColor(SELECTION_EDGE)
        #: alpha percentage (0-100) used for the drag-range fill only
        #: (see _selection_fill_color) -- everywhere else a selection
        #: needs to read clearly (bar fill, range border, selected-count
        #: text) uses selection_color at full opacity.
        self.selection_opacity: int = round(SELECTION_FILL.alpha() / 255 * 100)
        self.grid_color = QColor(GRID_COLOR)
        self.axis_label_color = QColor(AXIS_TEXT_COLOR)
        #: upper-left readout, split into its three segments (see
        #: _paint's readout block/_draw_readout_segments): the always-
        #: shown "Bin: ..." text, the "Chart: ..." stripped-units
        #: context, and -- now the only thing hover_label_color still
        #: covers -- the hovered bin's own start/count text.
        self.bin_info_color = QColor(AXIS_TEXT_COLOR)
        self.stripped_info_color = QColor(AXIS_TEXT_COLOR)
        self.hover_label_color = QColor(AXIS_TEXT_COLOR)
        #: selected-count readout, the frozen-vertical-range ceiling
        #: label, and (implicitly, via _partial_selection_color) half of
        #: every partially-selected bin's blend.
        self.selection_info_color = QColor(SELECTION_EDGE)
        self.background_color = QColor(BACKGROUND_COLOR)
        #: the color a hovered bin's bar and x-axis label are tinted
        #: toward, and how far (0-100) -- see _mix/_x_label_color and
        #: the bar loop in _paint.
        self.hover_color = QColor(HOVER_TINT_COLOR)
        self.hover_opacity: int = HOVER_TINT_OPACITY

    # ------------------------------------------------------------------ data
    def set_drops_enabled(self, enabled: bool):
        """Accept dropped folders or not. Off (the right pane in Duplicate
        Finder mode, where there is nothing to open there) also drops the
        "Drag a folder ... to begin" hint from the empty state."""
        enabled = bool(enabled)
        if self._drops_enabled == enabled:
            return
        self._drops_enabled = enabled
        self.setAcceptDrops(enabled)
        self.update()

    def set_folder_open(self, is_open: bool):
        """Tell the widget whether its own pane has a folder open right
        now -- decides which empty-state message _paint shows when
        there's nothing to plot ("Drag a folder into either view to
        begin" while it doesn't, "No <items> in view" once it does).
        Per pane, independent of the other pane's state."""
        is_open = bool(is_open)
        if self._folder_open == is_open:
            return
        self._folder_open = is_open
        self.update()

    def set_records(
        self,
        records: list,
        metric: str = "modified",
        value_of: Optional[Callable[[Any], float]] = None,
        path_of: Optional[Callable[[Any], Optional[str]]] = None,
        size_of: Optional[Callable[[Any], float]] = None,
        axis: Optional[BinAxis] = None,
        item_label: str = "file",
    ):
        """Load records and pick the axis/value-extractor for `metric`.

        `metric` is one of "modified" / "changed" / "accessed" (a datetime
        attribute -> TimeAxis) or "size" (byte count -> SizeAxis). Callers
        with a different record type (e.g. a different file explorer reusing
        this widget) can bypass that mapping entirely by supplying `value_of`
        / `axis` / `path_of` directly.

        `size_of`, like `value_of`/`path_of`, lets a caller with a
        different record type override the default FileRecord.size
        attribute lookup. Unlike `value_of` this is independent of
        `metric` -- it's always the record's actual byte size, feeding
        the selected/total-size readout (see self._total_size/
        _selected_size, _recompute_selected_counts, paintEvent) even
        while a date metric is what's actually being plotted.

        `item_label` is the singular noun for what a record actually is --
        purely cosmetic, only feeding the "N file(s)"/"N files selected"
        readouts (see paintEvent/_draw_selection_status or wherever they're
        drawn) -- so a caller plotting something other than files (e.g. a
        folder-mode view plotting directories instead) can have those
        readouts say "12 folder(s)" instead of the default "file".
        """
        self.records = records
        self._records_loaded = True
        self.metric_name = metric
        self._item_label = item_label or "file"
        if axis is not None:
            self.axis = axis
        elif metric == "size":
            self.axis = SizeAxis()
            self.axis.set_log_base(self.log_base)
            self.axis.set_log_mode(self.log_size_axis)
        else:
            self.axis = TimeAxis()

        if value_of is not None:
            self._value_of = value_of
        elif metric == "size":
            self._value_of = lambda r: float(getattr(r, "size", 0) or 0)
        else:
            self._value_of = lambda r: getattr(r, metric).timestamp()

        self._path_of = path_of if path_of is not None else (lambda r: getattr(r, "path", None))
        self._size_of = size_of if size_of is not None else (lambda r: float(getattr(r, "size", 0) or 0))
        # Each file once, even if the host lists it more than once (the
        # File Filter types metric puts a name matching several entries in
        # each of their bins).
        self._total_size = self._unique_size_sum(records)

        if not records:
            self.global_start = self.global_end = None
            self.view_start = self.view_end = None
            self.bin_edges, self.bin_counts = [], []
            self.selection_range = None
            self._last_click_anchor_value = None
            self.selected_paths = set()
            self.bin_selected_counts = []
            self._selected_size = 0.0
            self.update()
            return
        values = [self._value_of(r) for r in records]
        self.global_start = min(values)
        self.global_end = max(values)
        if self.axis.fixed_range is not None:
            # A categorical axis always covers all of its categories, even
            # the ones no record falls in.
            self.global_start, self.global_end = self.axis.fixed_range
        if self.global_end - self.global_start < self.axis.min_span:
            # All values identical, or closer together than the axis can
            # even resolve (e.g. access times a few milliseconds apart):
            # treat as one point, centered.
            mid = (self.global_start + self.global_end) / 2
            pad = self.axis.equal_span_pad
            self.global_start = mid - pad
            self.global_end = mid + pad
        self.reset_view()

    def _unique_size_sum(self, records) -> float:
        """Combined size of `records`, counting every path only once."""
        seen = set()
        total = 0.0
        for rec in records:
            path = self._path_of(rec)
            if path is not None:
                if path in seen:
                    continue
                seen.add(path)
            total += self._size_of(rec)
        return total

    def set_bin_count(self, n: int):
        """Target number of bins. When `nice_bins` is on this is a hint fed
        to `axis.choose_step()` (bins still snap to nice boundaries, so the
        actual count may differ slightly); when off it's exact - the
        "arbitrary" bin behaviour: view span / bin_count."""
        self.bin_count = max(1, n)
        self._recompute_bins()

    def _nice(self) -> bool:
        """Whether bins follow the axis's step grid: the "Nice bins" setting,
        or always for an axis whose bins are fixed by nature (categories)."""
        return self.nice_bins or self.axis.always_nice

    def set_nice_bins(self, enabled: bool):
        """Toggle between "nice" calendar/magnitude-snapped bins and plain
        equal-width `view_span / bin_count` bins."""
        self.nice_bins = bool(enabled)
        self._recompute_bins()

    def set_invert_zoom_direction(self, enabled: bool):
        """Flip which way the mouse wheel zooms this view (see
        wheelEvent) -- scroll up zooms out and down zooms in, instead of
        the default. Purely a wheelEvent-side flip; doesn't touch
        anything else about zooming (pivot, vertical-zoom zone, ...)."""
        self.invert_zoom_direction = bool(enabled)

    def set_strip_calendar_units(self, enabled: bool):
        """Time axis + nice bins only: drop the parts of each x-axis
        label that are larger than the bin size (the year from month
        bins, year-month from day bins, the date from hour bins, ...)
        and show them once in the upper-left readout instead -- see
        _strip_active/_strip_readout_text/TimeAxis.format_tick_stripped.
        Filesize labels are never touched."""
        self.strip_calendar_units = bool(enabled)
        self._recompute_bottom_margin()  # label widths changed
        self.update()

    def set_strip_use_names(self, enabled: bool):
        self.strip_use_names = bool(enabled)
        self._recompute_bottom_margin()
        self.update()

    def set_strip_abbreviate_names(self, enabled: bool):
        self.strip_abbreviate_names = bool(enabled)
        self._recompute_bottom_margin()
        self.update()

    def set_strip_use_week_number(self, enabled: bool):
        self.strip_use_week_number = bool(enabled)
        self._recompute_bottom_margin()
        self.update()

    def _strip_active(self) -> bool:
        return self.strip_calendar_units and self._nice()

    def _format_x_tick(self, value: float, step) -> str:
        """The x-axis label text for a bin boundary -- stripped of its
        larger calendar units (and, per the three options above,
        renamed/abbreviated/week-numbered) while _strip_active, else the
        axis's own full label. Every place that measures or draws label
        text goes through this, so widths always match what's painted."""
        if self._strip_active():
            return self.axis.format_tick_stripped(
                value, step,
                use_names=self.strip_use_names,
                abbreviate=self.strip_abbreviate_names,
                use_week_number=self.strip_use_week_number,
            )
        return self.axis.format_tick(value, step)

    def set_log_base(self, base):
        """Settings tab "Log base" ("2", "e" or "10"): base of the log
        size axis's bins (back to the coarsest level, view kept) and of
        the log count axis's ticks."""
        base = binning.normalize_log_base(base)
        if base == self.log_base:
            return
        self.log_base = base
        if isinstance(self.axis, SizeAxis):
            self.axis.set_log_base(base)
            if self.view_start is not None:
                self.view_start, self.view_end = self.axis.relevel(self.view_start, self.view_end)
        if self.view_start is not None:
            self._recompute_bins()
        self.update()

    def set_subdivide_log_grid(self, enabled: bool):
        """Settings tab "Subdivide logarithmic axis grid": a log-paper
        sub-grid inside each cell of a logarithmic axis (log size x-axis,
        log count y-axis), one layer deep, in the grid color."""
        self.subdivide_log_grid = bool(enabled)
        self.update()

    def refresh_text(self):
        """Re-lay out the axis text after something that changes how it
        reads (e.g. binary/decimal size units): label widths feed the
        bottom margin and the auto label angle."""
        if self.view_start is not None:
            self._recompute_bins()
        self.update()

    def set_log_scale(self, enabled: bool):
        self.log_scale = bool(enabled)
        self.update()

    def set_mirrored(self, mirrored: bool):
        """Flip the hover/selection readout text horizontally -- bars,
        axis, and bin edges are unaffected. See the docstring on
        self._mirrored in __init__."""
        self._mirrored = bool(mirrored)
        self.update()

    def set_log_size_axis(self, enabled: bool):
        """Toggle SizeAxis's logarithmic bin spacing (see
        SizeAxis.log_mode) -- meaningless for a TimeAxis, so the host
        app is expected to only expose this control while metric ==
        "size" (see DirComp's tl_log_size_axis_cb). Stored on this
        widget (log_size_axis) rather than only on self.axis so it
        survives set_records() rebuilding a fresh SizeAxis."""
        self.log_size_axis = bool(enabled)
        if isinstance(self.axis, SizeAxis):
            self.axis.set_log_mode(self.log_size_axis)
            if self.view_start is not None:
                self.view_start, self.view_end = self.axis.relevel(self.view_start, self.view_end)
            self._recompute_bins()
            self.update()

    def set_label_font_size(self, pt: int):
        """Point size for every piece of text this widget draws itself:
        the angled x-axis tick labels, the y-axis tick labels, and the
        upper-left hover/selection/bin-size readout -- all three pick
        this up automatically (see _draw_x_axis_labels/_draw_y_axis_labels,
        and _paint's font handling around the upper-left readout) since
        they either read this directly or inherit whichever font the
        painter was last given, and nothing resets it in between.
        Exposed as a host-app control (e.g. DirComp's per-pane font-size
        spinbox) rather than staying a fixed default, since a bigger
        window or a high-DPI display can make DEFAULT_LABEL_FONT_PT
        uncomfortably small."""
        self.label_font_pt = max(1, int(pt))
        self._recompute_bottom_margin()
        self.update()

    def set_tick_label_angle(self, deg: float):
        """Degrees the x-axis tick labels are rotated down-right from
        their tick mark -- see tick_label_angle_deg/_draw_x_axis_labels.
        0-90: 0 reads fully horizontal (needs _axis_label_y_offset's
        extra clearance below the baseline, since a horizontal label is
        vertically centered on its rotation origin like every other
        angle is); 90 reads straight down, centered under its own tick
        (see _draw_x_axis_labels) rather than starting beside it."""
        self.tick_label_angle_deg = max(0.0, min(90.0, float(deg)))
        self._recompute_bottom_margin()
        self.update()

    def set_auto_tick_angle(self, enabled: bool):
        """Auto angle mode for the x-axis tick labels: while on, the
        angle is worked out from the labels' own text width/height and
        the current on-screen spacing between neighbouring ticks (see
        _auto_tick_angle_deg) -- as shallow as possible without one
        label running into the next -- and re-worked out whenever
        that spacing changes (zoom, resize, bin count, font size, ...),
        instead of using the fixed tick_label_angle_deg, which is kept
        untouched so switching auto off restores it."""
        self.auto_tick_angle = bool(enabled)
        self._recompute_bottom_margin()
        self.update()

    def set_palette(
        self, *, bin_color=None, selection_color=None, selection_opacity=None,
        grid_color=None, axis_label_color=None, hover_label_color=None,
        selection_info_color=None, background_color=None,
        hover_color=None, hover_opacity=None,
        bin_info_color=None, stripped_info_color=None, partial_lerp=None,
    ):
        """Override any subset of this widget's paint colors -- each
        *_color argument accepts anything QColor(...) does (a "#RRGGBB"
        string, another QColor, ...); selection_opacity and hover_opacity are 0-100.
        Arguments left as None keep whatever's already set (the module-
        level defaults above, until this is called). See the colors
        block in __init__ for what each one actually paints, and this
        widget's own module docstring note on set_palette not having
        any concept of "modes" -- that's entirely the host's business."""
        if bin_color is not None:
            self.bin_color = QColor(bin_color)
        if selection_color is not None:
            self.selection_color = QColor(selection_color)
        if selection_opacity is not None:
            self.selection_opacity = max(0, min(100, int(selection_opacity)))
        if grid_color is not None:
            self.grid_color = QColor(grid_color)
        if axis_label_color is not None:
            self.axis_label_color = QColor(axis_label_color)
        if hover_label_color is not None:
            self.hover_label_color = QColor(hover_label_color)
        if selection_info_color is not None:
            self.selection_info_color = QColor(selection_info_color)
        if background_color is not None:
            self.background_color = QColor(background_color)
        if hover_color is not None:
            self.hover_color = QColor(hover_color)
        if hover_opacity is not None:
            self.hover_opacity = max(0, min(100, int(hover_opacity)))
        if bin_info_color is not None:
            self.bin_info_color = QColor(bin_info_color)
        if stripped_info_color is not None:
            self.stripped_info_color = QColor(stripped_info_color)
        if partial_lerp is not None:
            self.partial_lerp = max(0, min(100, int(partial_lerp)))
        self.update()

    @staticmethod
    def _mix(base: QColor, toward: QColor, frac: float) -> QColor:
        """`base` blended `frac` (0-1) of the way toward `toward`."""
        frac = max(0.0, min(1.0, frac))
        return QColor(
            round(base.red() * (1 - frac) + toward.red() * frac),
            round(base.green() * (1 - frac) + toward.green() * frac),
            round(base.blue() * (1 - frac) + toward.blue() * frac),
        )

    def _hover_tint(self, color: QColor) -> QColor:
        """`color` tinted toward hover_color by hover_opacity -- what a
        hovered bin's bar and label become."""
        return self._mix(color, self.hover_color, self.hover_opacity / 100)

    def _selection_fill_color(self) -> QColor:
        """selection_color at selection_opacity -- the drag-range/
        selection-overlay fill (see _paint); the range's own border,
        the live drag box, and every other use of "the selection color"
        stay fully opaque."""
        c = QColor(self.selection_color)
        c.setAlpha(round(self.selection_opacity / 100 * 255))
        return c

    def _partial_selection_color(self) -> QColor:
        """A bin where only *some* of its files are selected - a blend
        of bin_color and selection_color, so it reads as "partway
        there" without being confused with either (see _paint)."""
        return self._mix(self.bin_color, self.selection_color, self.partial_lerp / 100)

    def _axis_highlight_color(self) -> QColor:
        """grid_color (an axis line's own normal color) blended toward
        selection_color, weighted by selection_opacity - the same
        highlight color/opacity the current mode's palette already uses
        everywhere else (_selection_fill_color/_partial_selection_
        color), just blended against an axis's own color here instead
        of a bin's or a plain alpha fill. Used in _paint to highlight
        whichever axis the wheel would currently zoom, while the mouse
        hovers that axis's own zone (see _hover_zoom_zone/wheelEvent) -
        a "this is what scrolling does right now" cue, without reusing
        selection_info_color's exact hue the way an earlier version of
        the max axis label did (see _draw_y_axis_labels's docstring),
        which read as too easy to confuse with the top-right "N
        selected" readout."""
        frac = self.selection_opacity / 100
        return QColor(
            round(self.grid_color.red() * (1 - frac) + self.selection_color.red() * frac),
            round(self.grid_color.green() * (1 - frac) + self.selection_color.green() * frac),
            round(self.grid_color.blue() * (1 - frac) + self.selection_color.blue() * frac),
        )

    def set_auto_vrange(self, enabled: bool):
        enabled = bool(enabled)
        if enabled == self.auto_vrange:
            return
        self.auto_vrange = enabled
        if not enabled:
            # freeze the ceiling at whatever auto mode was just showing.
            self.fixed_max_count = round_up_nice(max(self.bin_counts, default=1))
        self.update()

    def readjust_vrange(self):
        """Recompute the fixed max-count ceiling from what's currently on
        screen - same effect as flicking auto vertical range off and on."""
        self.fixed_max_count = round_up_nice(max(self.bin_counts, default=1))
        self.update()

    def current_bin_step_label(self) -> Optional[str]:
        """Human-readable current bin width, e.g. '1 month' or '10 MB'
        (nice bins), or an approximate duration/size (arbitrary bins)."""
        if self._nice():
            if self._current_step is None:
                return None
            return self.axis.format_step(self._current_step)
        if len(self.bin_edges) < 2:
            return None
        return self.axis.format_raw_width(self.bin_edges[1] - self.bin_edges[0])

    def reset_view(self):
        if self.global_start is None:
            return
        span = self.global_end - self.global_start
        pad = self.axis.view_padding(span)
        self.view_start = self.global_start - pad
        self.view_end = self.global_end + pad
        self._clamp_view_to_axis_min()
        self.axis.reset_level()
        self.view_start, self.view_end = self.axis.relevel(self.view_start, self.view_end)
        # Deliberately does NOT touch selection_range or selected_paths -
        # resetting the zoom is a view-only operation. Any existing range
        # overlay or file selection survives and is simply recolored
        # against whatever bins exist at the new (full) view.
        self._recompute_bins()
        # The padding above has no relationship to where nice-bin step
        # boundaries actually fall, so view_end usually lands mid-bin --
        # the rightmost bin then gets visibly cut off by the plot rect's
        # right edge (see _value_to_x, which maps view_end straight to
        # rect.right()). Snap it forward to the nearest real bin edge at
        # or beyond it so the last bin always renders whole. The left
        # edge is deliberately left as-is: bin_edges[0] is already
        # floor(view_start, step) (see _recompute_bins/axis.build_edges),
        # i.e. the nearest boundary AT OR BEFORE the padded view_start,
        # so view_start itself can still legitimately fall mid-bin --
        # that's the one allowed "leftover" partial bin, not a bug.
        # Arbitrary (non-nice) bins are anchored to a fixed global grid
        # rather than the view window (see _recompute_bins' else branch),
        # so they're never expected to tile the view edge-to-edge in the
        # first place -- nothing to snap there.
        if self._nice():
            for edge in self.bin_edges:
                if edge >= self.view_end:
                    self.view_end = edge
                    break
        self.viewRangeChanged.emit(self.view_start, self.view_end)

    def _clamp_view_to_axis_min(self):
        """Keep the view from panning/zooming below the axis's lower bound
        (e.g. file-size bins can't go below 0 bytes), preserving span."""
        if self.axis.min_value is None or self.view_start is None:
            return
        if self.view_start < self.axis.min_value:
            span = self.view_end - self.view_start
            self.view_start = self.axis.min_value
            self.view_end = self.view_start + span

    # ------------------------------------------------------------------ bins
    def _recompute_bins(self):
        if self.view_start is None:
            return
        if self._nice():
            target_bins = max(1, self.bin_count)
            step = self.axis.choose_step(self.view_start, self.view_end, target_bins)
            self._current_step = step
            # Request edges for a bit more than the visible span (one
            # bin's worth of padding on each side) so a bar that's about
            # to scroll past the edge is still generated and can slide
            # smoothly out of view (clipped in _paint) instead of just
            # vanishing the moment it's no longer strictly inside the
            # requested range.
            pad = (self.view_end - self.view_start) / target_bins
            self.bin_edges = self.axis.build_edges(self.view_start - pad, self.view_end + pad, step)
        else:
            # Anchor bins to a fixed absolute grid (multiples of `width`
            # from a fixed origin) instead of dividing the *current* view
            # into n equal slices. Dividing the live view meant every pan
            # produced brand-new boundaries relative to the continuously
            # moving view_start: the x-axis never visibly moved, but which
            # bin each file fell into kept changing underneath it.
            # Anchoring keeps bin edges - and anything tracking one, like a
            # selection - fixed in absolute terms, matching nice-bins.
            self._current_step = None
            n = max(1, self.bin_count)
            span = self.view_end - self.view_start
            width = span / n if span > 0 else self.axis.min_span
            origin = self.global_start if self.global_start is not None else 0.0
            # -1/+1: same one-bin padding as the nice-bins branch above,
            # so an edge bin can slide out rather than pop out of view.
            first_i = math.floor((self.view_start - origin) / width) - 1
            last_i = math.ceil((self.view_end - origin) / width) + 1
            self.bin_edges = [origin + i * width for i in range(first_i, last_i + 1)]

        n = max(0, len(self.bin_edges) - 1)
        self.bin_counts = [0] * n
        self.bin_selected_counts = [0] * n
        if n > 0:
            lo, hi = self.bin_edges[0], self.bin_edges[-1]
            for rec in self.records:
                v = self._value_of(rec)
                if v < lo or v > hi:
                    continue
                idx = bisect.bisect_right(self.bin_edges, v) - 1
                idx = max(0, min(n - 1, idx))
                self.bin_counts[idx] += 1
                if self.selected_paths and self._path_of(rec) in self.selected_paths:
                    self.bin_selected_counts[idx] += 1

        # Bins may have shifted, been rebuilt at a different count, or
        # simply scrolled - re-find the hover bin from the absolute value
        # it was anchored to, rather than trusting the old index, so the
        # highlight tracks the same underlying bin as the view pans or
        # zooms instead of staying at a fixed screen position. Selection
        # highlighting itself needs no such remapping - bin_selected_counts
        # was just rebuilt fresh above, straight from selected_paths.
        self._hover_bin = self._bin_index_for_value(self._hover_bin_value)

        self._recompute_bottom_margin()
        self.update()

    def _axis_label_y_offset(self, label_h: float) -> float:
        """How far below rect.bottom() the angled x-axis tick labels'
        own rotation origin sits (see _draw_x_axis_labels) -- a flat 4px
        gap, plus, the more horizontal tick_label_angle_deg is, extra
        clearance so a purely horizontal label (vertically centered on
        its own rotation origin, same as every angle -- see
        _draw_x_axis_labels' AlignVCenter) doesn't have its own top half
        overlapping the axis baseline right above it. A steeper angle
        needs progressively less of this: by the time labels read
        straight down (angle == 90) they clear the baseline on their
        own with no extra help, so this collapses back to the flat 4px.
        Shared with _recompute_bottom_margin so the margin reserved
        below the plot always matches where labels are actually drawn."""
        angle_t = max(0.0, min(1.0, self._effective_tick_angle_deg / 90.0))
        return 4 + (1 - angle_t) * (label_h * 0.5)

    def _recompute_bottom_margin(self):
        """How much space the angled x-axis tick labels need below the
        plot, given the current font size and however long those labels
        currently are -- replaces a fixed MARGIN_BOTTOM so a smaller
        font or shorter labels (a coarser bin step, e.g. "2024" instead
        of "Jan 15, 2024 10:30") let the axis -- and the bars above it --
        sit lower/taller instead of always reserving room for the
        longest label this widget could ever need. Called after every
        bin recompute (bin_edges/tick labels may have changed) and after
        set_label_font_size (label metrics changed even if the labels
        themselves didn't)."""
        # Settle which angle is really in effect first -- everything
        # below (and _axis_label_y_offset/_draw_x_axis_labels) reads
        # it from _effective_tick_angle_deg, never tick_label_angle_deg
        # directly, so auto mode only has to change it here.
        if self.auto_tick_angle:
            self._effective_tick_angle_deg = self._auto_tick_angle_deg()
        else:
            self._effective_tick_angle_deg = self.tick_label_angle_deg
        self._auto_angle_key = self._auto_angle_key_now()
        if len(self.bin_edges) < 2:
            self._margin_bottom = MARGIN_BOTTOM
            return
        font = QFont()
        font.setPointSize(self.label_font_pt)
        fm = QFontMetrics(font)
        tick_step = self._current_step if self._nice() else self.axis.choose_step(
            self.bin_edges[0], self.bin_edges[1], 1
        )
        max_label_w = max(
            (fm.horizontalAdvance(self._format_x_tick(t, tick_step)) for t in self.bin_edges),
            default=0,
        )
        # Vertical extent of a label rotated _effective_tick_angle_deg degrees
        # down-right from its tick (see _draw_x_axis_labels): the bounding
        # box of a rotated rectangle contributes cos(angle) of its own
        # height plus sin(angle) of its width.
        angle = math.radians(self._effective_tick_angle_deg)
        label_h = fm.height()
        rotated_extent = max_label_w * math.sin(angle) + label_h * math.cos(angle)
        # Reserve the same gap between the axis and where a label's
        # rotation origin actually sits that _draw_x_axis_labels itself
        # uses (_axis_label_y_offset), plus a little breathing room
        # below the label itself.
        self._margin_bottom = max(24, round(rotated_extent + self._axis_label_y_offset(label_h)) + 6)

    def _label_tick_step(self):
        """The step token the x-axis tick labels are formatted with
        (same choice _recompute_bottom_margin/_draw_x_axis_labels make
        inline): the real step in nice mode, or one derived purely for
        label granularity from the actual bin width otherwise."""
        if self._nice():
            return self._current_step
        return self.axis.choose_step(self.bin_edges[0], self.bin_edges[1], 1)

    def _auto_tick_angle_deg(self) -> float:
        """Auto angle mode: the shallowest angle (whole degrees, 0-90)
        at which no visible x-axis tick label runs into its right-hand
        neighbour.

        Every label is drawn with the same rotation, starting at its
        own tick, so any two neighbours are the same rectangle (text
        width w x text height h) offset by the pixel gap d between
        their ticks. Rotated by angle a, that offset splits into
        d*cos(a) along the text and d*sin(a) across it, and the two
        labels stay clear of each other if either
          - d*cos(a) >= w (+ 1 character of breathing room): only ever
            true at a == 0, and only when d >= w + 1 character, which
            is the "horizontal if it fits" rule; or
          - d*sin(a) >= h: a >= asin(h / d), i.e. the labels stack like
            lines of text once tilted far enough.
        Labels are centered under their bins (or start at them, for the
        size metric), so d is the pixel distance between neighbouring
        labels' anchors (see _x_label_bins).
        The angle for the whole axis is the steepest one any adjacent
        pair needs (in log mode the pixel gaps are uneven, so the
        tightest pair decides). If even 90 degrees can't fit a pair
        (gap <= text height) it maxes out at 90 -- the shallowest
        angle that still overlaps the least. Gaps are measured in
        pixels via the axis space, same as _value_to_x, so this
        follows zoom/log mode automatically."""
        if self.view_start is None or len(self.bin_edges) < 2:
            return 0.0
        # Labels sit centered under their bins (see _x_axis_label_layout),
        # so what separates two neighbouring labels is the pixel distance
        # between their bins' centers, not between their edge ticks.
        bins = self._x_label_bins()
        if len(bins) < 2:
            return 0.0

        font = QFont()
        font.setPointSize(self.label_font_pt)
        fm = QFontMetrics(font)
        text_h = fm.height()
        margin_w = fm.horizontalAdvance("0")  # ~1 character
        tick_step = self._label_tick_step()

        needed = 0.0
        for (a, cx_a, _, _), (b, cx_b, _, _) in zip(bins, bins[1:]):
            if b != a + 1:
                continue
            gap = abs(cx_b - cx_a)
            label_w = fm.horizontalAdvance(self._format_x_tick(self.bin_edges[a], tick_step))
            if gap >= label_w + margin_w:
                continue  # fits flat
            if gap <= text_h:
                return 90.0
            needed = max(needed, math.degrees(math.asin(text_h / gap)))
        return float(min(90, math.ceil(needed - 1e-9)))

    def _auto_angle_key_now(self) -> tuple:
        """Everything _auto_tick_angle_deg's answer depends on that
        _recompute_bins doesn't already cover (a resize, a y-axis label
        column changing width) -- see _refresh_auto_angle_if_stale."""
        return (
            self.width(), round(self._y_axis_margin_left(), 1), self.label_font_pt,
            self.view_start, self.view_end, len(self.bin_edges), self._strip_active(),
        )

    def _refresh_auto_angle_if_stale(self):
        """Called at the top of every paint while auto angle is on:
        redoes the angle (and the bottom margin it feeds) only if
        something it depends on actually changed since it was last
        worked out, so hover/selection repaints don't redo the math."""
        if self.auto_tick_angle and self._auto_angle_key != self._auto_angle_key_now():
            self._recompute_bottom_margin()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Bin edges no longer depend on pixel width, so a resize just needs
        # a redraw - Qt does that automatically, nothing else to do here.

    # ------------------------------------------------------------- geometry
    def _y_axis_label_left_pad(self) -> float:
        """~1 character's worth of left margin kept before a y-axis tick
        label's own text - and, to keep the two flush with each other
        (see _paint), before the upper-left hover/selection/bin-size
        readout too. Measured as a digit's width at the current axis
        font size rather than a fixed pixel value, which looked too
        tight at a large font size and wasted space at a small one."""
        font = QFont()
        font.setPointSize(self.label_font_pt)
        return QFontMetrics(font).horizontalAdvance("0")

    def _y_axis_margin_left(self) -> float:
        """Left margin width for the y-axis label column - sized to fit
        the current max axis value's own digit count (all other
        displayed y-tick labels are <= max_count, so its digit count is
        always the widest one), rather than a fixed width that either
        wastes space for small counts or clips a large one. Falls back
        to the fixed MARGIN_LEFT before there's any data to size
        against. Feeds _plot_rect() itself, so this is the single place
        controlling it -- everything else (bars, gridlines, hit-
        testing, wheelEvent's axis-zone check, ...) is built from that
        rect and picks up the change automatically."""
        if not self.bin_counts:
            return MARGIN_LEFT
        max_count = self._display_max_count()
        label = f"{int(round(max_count))}"
        font = QFont()
        font.setPointSize(self.label_font_pt)
        text_w = QFontMetrics(font).horizontalAdvance(label)
        # ~1 character's worth of leading inset (matches the label's own
        # drawText origin in _draw_y_axis_labels) plus HOVER_READOUT_X_
        # OFFSET as the trailing gap before the axis line - the same
        # small margin the hover/bin-size readout keeps off the plot's
        # own left edge, so the widest tick label and the axis line
        # never end up flush against each other.
        return max(MARGIN_LEFT_MIN, self._y_axis_label_left_pad() + text_w + HOVER_READOUT_X_OFFSET)

    def _plot_rect(self) -> QRectF:
        margin_left = self._y_axis_margin_left()
        return QRectF(
            margin_left,
            MARGIN_TOP,
            max(1.0, self.width() - margin_left - MARGIN_RIGHT),
            max(1.0, self.height() - MARGIN_TOP - self._margin_bottom),
        )

    def _value_to_x(self, v: float) -> float:
        rect = self._plot_rect()
        # Position in "axis space" rather than raw value - identity for
        # every axis/mode except SizeAxis's logarithmic one, where it's
        # log10(value), so decade bins end up equally spaced in pixels
        # instead of growing 10x wider each decade (see binning.py).
        # Measured as axis-space *distances* from view_start (see
        # BinAxis.axis_delta) rather than absolute positions, so deeply
        # nested log size bins keep full precision.
        delta = self.axis.axis_delta
        span = delta(self.view_end, self.view_start)
        if span == 0:
            return rect.left()
        frac = delta(v, self.view_start) / span
        return rect.left() + frac * rect.width()

    def _x_to_value(self, x: float) -> float:
        rect = self._plot_rect()
        frac = (x - rect.left()) / rect.width() if rect.width() else 0.0
        span = self.axis.axis_delta(self.view_end, self.view_start)
        return self.axis.from_axis_delta(self.view_start, frac * span)

    def _x_to_bin(self, x: float) -> Optional[int]:
        if not self.bin_edges or len(self.bin_edges) < 2:
            return None
        v = self._x_to_value(x)
        n = len(self.bin_edges) - 1
        idx = bisect.bisect_right(self.bin_edges, v) - 1
        if idx < 0 or idx >= n:
            return None
        return idx

    def _bin_index_for_value(self, v: Optional[float]) -> Optional[int]:
        """Index of the bin currently containing absolute value `v`, or
        None if `v` is unset or now falls outside the visible bins."""
        if v is None or not self.bin_edges or len(self.bin_edges) < 2:
            return None
        n = len(self.bin_edges) - 1
        idx = bisect.bisect_right(self.bin_edges, v) - 1
        if idx < 0 or idx >= n:
            return None
        return idx

    def _bin_midpoint(self, idx: Optional[int]) -> Optional[float]:
        """Absolute midpoint of bin `idx`, used as the anchor value to
        re-find that same bin after the next recompute."""
        if idx is None or idx + 1 >= len(self.bin_edges):
            return None
        return (self.bin_edges[idx] + self.bin_edges[idx + 1]) / 2

    def _bin_selection_state(self, idx: int) -> str:
        """"full" if every record in bin `idx` is in selected_paths,
        "partial" if some are, "none" otherwise (including empty bins)."""
        if idx >= len(self.bin_counts) or self.bin_counts[idx] == 0:
            return "none"
        selected = self.bin_selected_counts[idx] if idx < len(self.bin_selected_counts) else 0
        if selected <= 0:
            return "none"
        return "full" if selected >= self.bin_counts[idx] else "partial"

    def set_external_selection(self, paths):
        """Tell the widget which files are selected elsewhere (e.g. a file
        list the host app shows alongside this timeline), so bins light up
        to match: fully covered bins get the normal selection color,
        partially covered bins get a blended "partial" color. Does not
        emit `selectionChanged` - this is the *incoming* half of the
        bidirectional sync, so echoing it back would just bounce the same
        selection between widget and host."""
        self._set_selection(paths, emit=False)

    def set_external_selection_range(self, value_range: Optional[tuple]):
        """Directly set (or, with None, clear) the drag-range overlay,
        without touching selected_paths or emitting selectionChanged --
        the selection_range counterpart of set_external_selection above.
        Used to restore a previously-stashed range when a host swaps
        between independent selections it wants kept separate (e.g.
        DirComp's File/Folder mode toggle -- see
        TimelineOpsMixin._on_timeline_mode_changed)."""
        self.selection_range = tuple(value_range) if value_range else None
        self._last_click_anchor_value = None
        self.update()

    def _set_selection(self, paths, emit: bool):
        """Single entry point for changing selected_paths, whether the
        change originates here (a click/drag, which also notifies the
        host via `selectionChanged`) or from the host (via
        `set_external_selection`, which doesn't). Routing every change
        through here means bin highlighting can never go stale relative
        to whichever side changed it last."""
        self.selected_paths = {p for p in paths if p is not None}
        self._recompute_selected_counts()
        if emit:
            self.selectionChanged.emit(set(self.selected_paths))
        self.update()

    def set_selected_total_size(self, value: Optional[float], any_selected: bool = False):
        """Override for the readout's selected-size figure (see
        paintEvent) -- lets a host with a more complete notion of
        "selected size" than this widget can derive on its own take
        over that half of the readout. This widget's own
        self._selected_size (_recompute_selected_counts) only ever sums
        whichever single record type (files, or folders) this pane's
        Files/Folders toggle currently has loaded into self.records --
        a real selection spanning both files and folders at once would
        silently show only one type's share. Pass None to fall back to
        that internal, narrower calculation again."""
        if value == self._selected_size_override and any_selected == self._any_selected:
            return
        self._selected_size_override = value
        #: host says something is selected in the pane (any type), so the
        #: readout still shows e.g. "0 files, X total" for a folder-only selection
        self._any_selected = bool(any_selected)
        self.update()

    def set_total_size_override(self, value: Optional[float]):
        """Override for the readout's total-size figure (see
        paintEvent) -- same idea as set_selected_total_size, but for
        self._total_size (set_records()), which is likewise only ever
        this pane's current mode's own total. Kept as a genuinely
        separate override (not inferred from set_selected_total_size)
        since a host might want the two to disagree -- e.g. "selected"
        meaning the real combined figure while "total" stays scoped to
        what's actually being plotted."""
        if value == self._total_size_override:
            return
        self._total_size_override = value
        self.update()

    def _recompute_selected_counts(self):
        """Rebuild bin_selected_counts from the current bin_edges and
        selected_paths - called whenever either changes. Also keeps
        self._selected_size (see __init__/set_records) in step: the
        running byte total of every currently-selected record, computed
        over the *entire* selection regardless of the current bin range
        -- unlike bin_selected_counts, which only concerns itself with
        what's inside [lo, hi] since it's purely a per-bin display
        detail, the selected-size readout (paintEvent) is meant to
        answer "how much of what I've selected is that", which should
        stay accurate even for a selected item currently scrolled/zoomed
        out of view."""
        n = len(self.bin_counts)
        self.bin_selected_counts = [0] * n
        self._selected_size = 0.0
        if not self.selected_paths:
            return
        lo = self.bin_edges[0] if self.bin_edges else None
        hi = self.bin_edges[-1] if self.bin_edges else None
        counted = set()
        for rec in self.records:
            path = self._path_of(rec)
            if path is None or path not in self.selected_paths:
                continue
            if path not in counted:  # a record listed in several bins: size once
                counted.add(path)
                self._selected_size += self._size_of(rec)
            if n == 0 or lo is None:
                continue
            v = self._value_of(rec)
            if v < lo or v > hi:
                continue
            idx = bisect.bisect_right(self.bin_edges, v) - 1
            idx = max(0, min(n - 1, idx))
            self.bin_selected_counts[idx] += 1

    # --------------------------------------------------------------- paint
    def _display_max_count(self) -> int:
        """Max-count ceiling for the y-axis: live per-frame max when auto
        vertical range is on, otherwise the frozen fixed value."""
        if self.auto_vrange:
            return max(max(self.bin_counts, default=0), 1)
        return max(self.fixed_max_count, 1)

    def _count_to_frac(self, count: float) -> float:
        """Fraction of the plot height a given count maps to - shared by
        bar rendering and y-axis tick placement so gridlines always line
        up exactly with the bars, in both linear and log_scale mode."""
        max_count = self._display_max_count()
        if self.log_scale:
            log_denom = math.log1p(max_count)
            frac = (math.log1p(count) / log_denom) if log_denom > 0 else 0.0
        else:
            frac = count / max_count if max_count else 0.0
        return min(max(frac, 0.0), 1.0)

    def _y_tick_values(self, max_count: float) -> list[float]:
        """Tick values from 0 up to `max_count`, at most Y_AXIS_MAX_TICKS
        of them - evenly-spaced "nice" round numbers normally, or powers
        of ten when log_scale is on (matching the log1p bar mapping so
        the gridlines land where you'd expect relative to the bars)."""
        if max_count <= 0:
            return [0.0]
        if self.log_scale:
            # Powers of the Log base (whole numbers -- base e: 1, 3, 7,
            # 20, ... -- see binning.log_base_powers), thinned out evenly
            # when there are too many.
            powers = [p for p in binning.log_base_powers(self.log_base) if p <= max_count]
            if len(powers) > Y_AXIS_MAX_TICKS - 1:
                keep_every = max(1, math.ceil(len(powers) / (Y_AXIS_MAX_TICKS - 1)))
                kept = powers[::keep_every]
                if kept[-1] != powers[-1]:
                    kept.append(powers[-1])
                powers = kept
            ticks = {0.0, float(max_count)}
            ticks.update(powers)
            return sorted(ticks)
        raw_step = max_count / (Y_AXIS_MAX_TICKS - 1)
        magnitude = 10 ** math.floor(math.log10(raw_step))
        step = magnitude
        for mult in (1, 2, 5, 10):
            step = mult * magnitude
            if step >= raw_step:
                break
        ticks = []
        v = 0.0
        while v < max_count - 1e-9:
            ticks.append(v)
            v += step
        ticks.append(float(max_count))
        return ticks

    def _draw_y_axis_ticks(self, painter: QPainter, rect: QRectF, max_count: float):
        """Horizontal gridlines spanning the plot, from 0 to max_count -
        call inside the horizontal clip so they don't spill past the
        bars. Labels are drawn separately, in the left margin, via
        `_draw_y_axis_labels`."""
        painter.setPen(QPen(self.grid_color, 1, Qt.DotLine))
        major = self._y_tick_values(max_count)
        major_ys = []
        for v in major:
            y = rect.bottom() - self._count_to_frac(v) * (rect.height() - 4)
            major_ys.append(y)
            painter.drawLine(rect.left(), y, rect.right(), y)
        if self.subdivide_log_grid and self.log_scale and max_count > 1:
            # "Subdivide logarithmic axis grid": the Log base's sub-grid
            # multiples (binning.LOG_SUBGRID_MULTIPLIERS, e.g. 2..9 for
            # base 10) of each power, between the power gridlines -- and
            # the powers themselves the major ticks skipped when thinned
            # out. Same pen; a line too close to another one is left out.
            majors = set(major)
            mults = (1.0,) + binning.LOG_SUBGRID_MULTIPLIERS[self.log_base]
            last_y = None
            for p in binning.log_base_powers(self.log_base):
                if p >= max_count:
                    break
                for k in mults:
                    v = k * p
                    if v >= max_count:
                        break
                    if v in majors:
                        continue
                    y = rect.bottom() - self._count_to_frac(v) * (rect.height() - 4)
                    if any(abs(y - my) < SUBGRID_MIN_GAP_PX for my in major_ys):
                        continue
                    if last_y is not None and abs(last_y - y) < SUBGRID_MIN_GAP_PX:
                        continue
                    painter.drawLine(rect.left(), y, rect.right(), y)
                    last_y = y

    def _y_axis_label_positions(self, ticks: list, rect: QRectF, label_h: float) -> set:
        """Which of `ticks` (ascending) actually get a label drawn, so
        neighbouring labels never visually overlap as the plot gets
        shorter. The top (max) value always stays either way.

        Same greedy top-down walk regardless of scale: keep the highest
        tick, then walk down keeping each subsequent tick only if it
        doesn't collide with the last one actually kept (always the
        higher of the two -- so a higher value always wins over a lower
        one) -- repeat against whatever was kept most recently, not
        against every tick in between. Ticks are evenly spaced on a
        linear scale and decades (1/10/100/...) on a log one, but the
        walk itself doesn't care either way -- it only ever looks at
        actual pixel distance between consecutive *kept* labels."""
        if not ticks:
            return set()
        top_down = list(reversed(ticks))
        ys = [rect.bottom() - self._count_to_frac(v) * (rect.height() - 4) for v in top_down]

        kept_idx = [0]
        for i in range(1, len(top_down)):
            if abs(ys[i] - ys[kept_idx[-1]]) >= label_h * 0.75:
                kept_idx.append(i)
        return {top_down[i] for i in kept_idx}

    def _draw_y_axis_labels(self, painter: QPainter, rect: QRectF, max_count: float):
        """Value labels for the y-axis ticks, in the left margin (outside
        the plot area, like the old min/max labels) so bars scrolling
        underneath never make them hard to read.

        Labels that would collide are thinned via _y_axis_label_positions
        rather than drawn regardless -- the maximum's own position is the
        one tick whose spacing to its neighbours can't be predicted or
        avoided by tick selection (_y_tick_values) alone, since it moves
        around based on the data and vertical-range mode, but the same
        thinning now applies across the whole axis, not just its top
        pair, as the plot gets shorter (a smaller window, or a taller
        _margin_bottom eating into the same space).

        No separate "(log)" line on the max label anymore -- whether
        the axis is log or linear is already visible from the tick
        spacing/gridlines themselves (see _y_tick_values), so the extra
        line was redundant. Every label (including the max, frozen-
        ceiling or not) uses the plain axis_label_color now -- an
        earlier version colored the frozen-ceiling max label with
        selection_info_color to flag "this is a manually fixed value",
        but that's the exact color the upper-right "N selected" readout
        also uses (_paint), so the two were easy to visually confuse
        with each other. Not worth a second accent color just for this,
        given the wheel-zoom-when-Auto-Range-is-off interaction
        (wheelEvent) already makes a frozen ceiling obvious the moment
        you touch it.

        The 0 tick never gets a label here, on purpose -- it sits right
        where the y-axis crosses the x-axis, exactly the corner an
        x-axis tick label's own rotated text (_draw_x_axis_labels) starts
        from for the leftmost bin edge; a "0" drawn there just competes
        with (and can visually cut into) that x-axis label instead of
        leaving it the corner to itself. The 0 gridline itself (_draw_
        y_axis_ticks) is unaffected -- it lands exactly on the plot's
        own baseline anyway, so there's nothing distinct to remove
        there."""
        font = QFont()
        font.setPointSize(self.label_font_pt)
        painter.setFont(font)
        fm = QFontMetrics(font)
        label_h = fm.height()
        left_pad = self._y_axis_label_left_pad()
        ticks = self._y_tick_values(max_count)
        # 0 never gets a label (see below), so it's excluded before
        # thinning too -- otherwise it could still cost its neighbour a
        # slot (via _y_axis_label_positions' collision walk) for a label
        # that was never going to be drawn anyway.
        labeled_ticks = [v for v in ticks if v != 0.0]
        keep = self._y_axis_label_positions(labeled_ticks, rect, label_h)
        painter.setPen(self.axis_label_color)
        for v in labeled_ticks:
            if v not in keep:
                continue
            y = rect.bottom() - self._count_to_frac(v) * (rect.height() - 4)
            label = f"{int(round(v))}"
            painter.drawText(
                QRectF(left_pad, y - 8, rect.left() - left_pad - HOVER_READOUT_X_OFFSET, 16),
                Qt.AlignRight | Qt.AlignVCenter, label,
            )

    def _bar_rect(self, idx: int) -> QRectF:
        """The on-screen rectangle for bin `idx`'s bar, in plot coordinates -
        shared by painting and by click hit-testing."""
        rect = self._plot_rect()
        x0 = self._value_to_x(self.bin_edges[idx])
        x1 = self._value_to_x(self.bin_edges[idx + 1])
        bar_h = self._count_to_frac(self.bin_counts[idx]) * (rect.height() - 4)
        return QRectF(x0 + 1, rect.bottom() - bar_h, max(0.0, x1 - x0 - 2), bar_h)

    def _y_hits_bar(self, idx: int, y: float) -> bool:
        """Whether `y` (in widget coordinates) falls within bin `idx`'s
        rendered bar - i.e. not above its top, in the empty space between
        the bar and the top of the plot."""
        bar_rect = self._bar_rect(idx)
        if bar_rect.height() <= 0:
            return False
        return bar_rect.top() <= y <= self._plot_rect().bottom()

    def paintEvent(self, event):
        # Painting is wrapped in try/finally so painter.end() always runs,
        # even if something below raises (an unclosed QPainter on the next
        # repaint is what triggers Qt's "endPaint() called with active
        # painter" warning).
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter):
        painter.fillRect(self.rect(), self.background_color)
        if self.view_start is None or not self.bin_counts:
            painter.setPen(self.axis_label_color)
            font = QFont()
            font.setPointSize(self.label_font_pt)
            painter.setFont(font)
            # Two distinct empty states, previously indistinguishable --
            # set_records([]) (a real folder loaded, nothing currently
            # qualifies to be plotted) used to leave view_start None too,
            # same as never having loaded anything at all, so both paths
            # showed this same drag-prompt even once a folder genuinely
            # was open. _folder_open (fed by the host per pane, see
            # set_folder_open) tells them apart; _records_loaded (set the
            # first time set_records is ever called, empty or not -- see
            # __init__) is only the fallback for a host that never says.
            has_folder = self._records_loaded if self._folder_open is None else self._folder_open
            if has_folder:
                message = f"No {self._item_label}s in view"
            elif self._drops_enabled:
                message = "Drag a folder into either view to begin"
            else:
                return  # drops are off here (Duplicate Finder right pane): no hint
            painter.drawText(self.rect(), Qt.AlignCenter, message)
            return

        self._refresh_auto_angle_if_stale()
        rect = self._plot_rect()
        max_count = self._display_max_count()

        # Everything that can slide past the left/right edge of the plot
        # (bars, gridlines, selection overlays) is clipped horizontally to
        # the plot area - restricted to x only, not y, so it doesn't cut
        # into the tick-label margin below. Bin data is deliberately
        # generated slightly wider than the view (see _recompute_bins), so
        # without this clip those extra bars would spill into the axis
        # margins instead of sliding smoothly out of sight.
        painter.save()
        painter.setClipRect(QRectF(rect.left(), 0, rect.width(), self.height()))

        # axis gridlines (x ticks + y ticks) - drawn BEFORE the bars
        # below, so they run behind the bars rather than being scored on
        # top of them. Both axes' tick LABEL TEXT is a separate, later
        # pass (_draw_x_axis_labels/_draw_y_axis_labels, both after the
        # clip below is dropped) rather than drawn here alongside its
        # own gridlines - see _draw_x_axis_labels' docstring for why
        # that split matters for the x-axis specifically.
        self._draw_axis_labels(painter, rect)
        self._draw_y_axis_ticks(painter, rect, max_count)

        # bars - color reflects selection state first (full > partial >
        # plain), then hover just tints whatever that color already is
        # toward hover_color (by hover_opacity), rather than being its
        # own separate fixed color - so hovering a selected bar still
        # reads as "selected", just lit up.
        for i in range(len(self.bin_counts)):
            bar_rect = self._bar_rect(i)
            state = self._bin_selection_state(i)
            if state == "full":
                base_color = self.selection_color
            elif state == "partial":
                base_color = self._partial_selection_color()
            else:
                base_color = self.bin_color
            color = self._hover_tint(base_color) if i == self._hover_bin else base_color
            painter.fillRect(bar_rect, color)

        # selection overlay
        if self.selection_range:
            v0, v1 = self.selection_range
            x0, x1 = self._value_to_x(v0), self._value_to_x(v1)
            sel_rect = QRectF(min(x0, x1), rect.top(), abs(x1 - x0), rect.height())
            painter.fillRect(sel_rect, self._selection_fill_color())
            pen = QPen(self.selection_color, 2)
            painter.setPen(pen)
            painter.drawLine(sel_rect.left(), rect.top(), sel_rect.left(), rect.bottom())
            painter.drawLine(sel_rect.right(), rect.top(), sel_rect.right(), rect.bottom())

        # live drag-selection box (while shift/right-dragging, before release)
        if self._selecting and self._select_start_x is not None and self._select_cur_x is not None:
            x0, x1 = self._select_start_x, self._select_cur_x
            live_rect = QRectF(min(x0, x1), rect.top(), abs(x1 - x0), rect.height())
            painter.setPen(QPen(self.selection_color, 1, Qt.DashLine))
            painter.drawRect(live_rect)

        painter.restore()  # drop the horizontal clip

        # baseline + frame - whichever axis the wheel would currently
        # zoom (see wheelEvent's own zone test, mirrored here via
        # _hover_zoom_zone) picks up the highlight blend, as a "this is
        # what scrolling does right now" cue: the horizontal baseline
        # highlights on hover unconditionally (wheel always zooms it,
        # there's no "Auto Range" gate for the x-axis), while the
        # vertical axis only highlights on hover while Auto Range is
        # off too, matching wheelEvent's own guard (with it on, hovering
        # there wouldn't actually zoom anything).
        highlight = self._axis_highlight_color()
        h_pen = QPen(highlight if self._hover_zoom_zone == "horizontal" else self.grid_color, 1)
        v_pen = QPen(
            highlight if (self._hover_zoom_zone == "vertical" and not self.auto_vrange) else self.grid_color, 1
        )
        painter.setPen(h_pen)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setPen(v_pen)
        painter.drawLine(rect.topLeft(), rect.bottomLeft())

        # y-axis tick labels (drawn in the left margin, unclipped, so they
        # sit outside the plot area rather than over the bars)
        self._draw_y_axis_labels(painter, rect, max_count)

        # x-axis tick labels (see _draw_x_axis_labels for why these are
        # unclipped too, not just the y-axis ones)
        self._draw_x_axis_labels(painter, rect)

        # Upper-left readout (upper-right when self._mirrored): hover text
        # takes priority over the plain bin-size label, same as before --
        # but the selected-count readout (below) is no longer part of
        # this priority chain, so whichever of these two is showing here
        # doesn't hide it anymore.
        #
        # Flush with the same left inset the y-axis value labels
        # themselves use (_draw_y_axis_labels/_y_axis_label_left_pad) --
        # i.e. as far left as the axis's own top value, roughly where
        # the old "(Log)" line used to sit -- rather than just right of
        # the plot's left edge. Same rect either way; only the
        # alignment differs, mirroring which edge (of the whole widget,
        # not just the plot) it hugs.
        left_pad = self._y_axis_label_left_pad()
        readout_font = QFont()
        readout_font.setPointSize(self.label_font_pt)
        readout_align = Qt.AlignRight if self._mirrored else Qt.AlignLeft
        readout_rect = QRectF(left_pad, 2, self.width() - 2 * left_pad, 18)
        # Always leads with the bin size ("Bin: 1 month"); "Chart: ..."
        # (always -- the stripped units, or the visible range) and the hovered bin's own readout
        # (while hovering) are appended after it, each its own segment
        # -- so hovering a bin doesn't hide the Chart context, and
        # having the Chart context doesn't hide what's under the cursor.
        segments = []
        hover_segment = None
        bin_text = self._bin_size_readout_text()
        if bin_text:
            segments.append((bin_text, self.bin_info_color))
        shown = self._strip_readout_text()
        if shown:
            segments.append((shown, self.stripped_info_color))
        if self._hover_bin is not None and not self._panning and not self._selecting:
            i = self._hover_bin
            v0 = self.bin_edges[i]
            # The bin's start value (or, for the size axis, its whole
            # "start - end" range -- see format_hover_range), in the
            # axis's plain (never stripped/named -- see _format_x_tick)
            # tick format, plus how many records it holds -- format_bin's fuller
            # "start - end" range read as noise once the "Bin: ..."
            # segment above it already states the width. Same
            # nice-bins-or-not step lookup _recompute_bottom_margin
            # uses, so an arbitrary (non-nice) bin still gets a sensible
            # tick format instead of None.
            step = self._current_step if self._nice() else self.axis.choose_step(v0, self.bin_edges[i + 1], 1)
            hover_segment = (
                f"{self.axis.format_hover_range(v0, self.bin_edges[i + 1], step)} - {self.bin_counts[i]} {self._item_label}(s)",
                self.hover_label_color,
            )
        if hover_segment is not None:
            # The hover text follows "Bin: ... Chart: ..." on the same
            # line unless that would run into the selection readout at the
            # other end of the line (or off the widget, with nothing
            # selected) -- then it drops to its own line, below. Measured
            # with the font the readout is drawn in, and with the same gap
            # _draw_readout_segments leaves between segments.
            fm = QFontMetrics(readout_font)
            gap = fm.horizontalAdvance("   ")
            line_w = sum(fm.horizontalAdvance(t) for t, _ in segments + [hover_segment]) + gap * len(segments)
            selection_label = self._selection_readout_label()
            sel_w = fm.horizontalAdvance(selection_label) if selection_label else 0
            if self._mirrored:
                # Line is right-aligned to the widget's right edge; the
                # selection readout sits at the plot's left edge.
                line_start = self.width() - left_pad - line_w
                limit = (rect.left() + SELECTED_READOUT_X_MARGIN + sel_w + gap) if sel_w else left_pad
                overlaps = line_start < limit
            else:
                line_end = left_pad + line_w
                limit = (rect.right() - SELECTED_READOUT_X_MARGIN - sel_w - gap) if sel_w else (self.width() - left_pad)
                overlaps = line_end > limit
            if not overlaps:
                segments.append(hover_segment)
                hover_segment = None
        self._draw_readout_segments(painter, readout_rect, self._mirrored, segments)
        if hover_segment is not None:
            # It would have run into the selection readout (see the test
            # above), so it goes on its own line instead, just under the first one and after the vertical axis
            # -- always the top left of the chart itself: the plot never
            # mirrors (its axis stays on the left), even when the readout
            # line above it does. It's drawn over the plot area, so it can
            # sit on top of a tall bin.
            hover_rect = QRectF(
                rect.left() + HOVER_READOUT_X_OFFSET, rect.top() + 2,
                max(1.0, rect.width() - 2 * HOVER_READOUT_X_OFFSET), 18,
            )
            self._draw_readout_segments(painter, hover_rect, False, [hover_segment])

        # Upper-right readout (upper-left when self._mirrored): how many
        # files are currently selected (from a click/drag here, or from
        # the host app), in the same orange as a fully-selected bar --
        # drawn independently of the block above now (its own slot at
        # the same height) rather than sharing the hover-text spot and
        # only showing when nothing was being hovered, so it stays
        # visible at the same time as hover text instead of being
        # hidden by it.
        label = self._selection_readout_label()
        if label is not None:
            painter.setPen(self.selection_info_color)
            selected_align = Qt.AlignLeft if self._mirrored else Qt.AlignRight
            selected_rect = (
                QRectF(rect.left() + SELECTED_READOUT_X_MARGIN, 2, rect.width() - SELECTED_READOUT_X_MARGIN, 18) if self._mirrored
                else QRectF(rect.left(), 2, rect.width() - SELECTED_READOUT_X_MARGIN, 18)
            )
            painter.drawText(selected_rect, selected_align, label)

    def _selection_readout_label(self) -> Optional[str]:
        """"X files, Y / Z total" -- the text of the upper-right
        readout (upper-left when mirrored), or None while nothing's
        selected. Y is the selected subset's own combined size, Z is the
        total for whatever "total" means here. Both prefer a host-supplied
        override (see set_selected_total_size/set_total_size_override) over
        this widget's own self._selected_size/_total_size, which only ever
        cover whichever single record type is currently loaded into
        self.records. Its own method so _paint can measure it before
        deciding where the hover text goes."""
        if not self.selected_paths and not self._any_selected:
            return None
        n = len(self.selected_paths)
        selected_size = self._selected_size_override if self._selected_size_override is not None else self._selected_size
        return (
            f"{n} {self._item_label}{'s' if n != 1 else ''}, "
            f"{_format_bytes(selected_size)} total"
        )

    def _visible_tick_indices(self) -> set:
        """Which bin_edges indices border at least one bin that's at
        least partially visible in the current view - i.e. not a tick
        whose entire neighbourhood is padding (see _recompute_bins,
        which deliberately builds bin_edges a bit wider than the view).
        Used by _draw_x_axis_labels to skip labelling ticks for bins
        that are completely hidden, while still labelling one that's
        only partially cut off (see that method for why the partial
        one's label still needs full, unclipped room to draw in)."""
        n_edges = len(self.bin_edges)
        visible = set()
        for i in range(n_edges):
            lo_bin_visible = i > 0 and self.bin_edges[i] > self.view_start and self.bin_edges[i - 1] < self.view_end
            hi_bin_visible = i < n_edges - 1 and self.bin_edges[i + 1] > self.view_start and self.bin_edges[i] < self.view_end
            if lo_bin_visible or hi_bin_visible:
                visible.add(i)
        return visible

    def _draw_axis_labels(self, painter: QPainter, rect: QRectF):
        """Vertical dotted gridlines only, one per bin edge - drawn
        while still inside the horizontal clip (see _paint), which is
        exactly what keeps them from running past the plot's own left/
        right edges. The edges' own tick-label TEXT is a separate pass
        (_draw_x_axis_labels), deliberately drawn later, after that
        clip is dropped - see its docstring for why."""
        font = QFont()
        font.setPointSize(self.label_font_pt)
        painter.setFont(font)
        painter.setPen(QPen(self.grid_color, 1, Qt.DotLine))
        xs = [self._value_to_x(t) for t in self.bin_edges]
        for x in xs:
            painter.drawLine(x, rect.top(), x, rect.bottom())
        if self.subdivide_log_grid and self._nice() and self._current_step is not None:
            # "Subdivide logarithmic axis grid": a log-paper pattern inside
            # each bin of a logarithmic x-axis (see the axis's
            # log_subgrid_fractions), same pen; lines that would sit
            # closer than SUBGRID_MIN_GAP_PX to their neighbour or the
            # bin's own edges are left out, so narrow bins stay clean.
            for i in range(len(xs) - 1):
                x0, x1 = xs[i], xs[i + 1]
                if x1 < rect.left() or x0 > rect.right():
                    continue
                fracs = self.axis.log_subgrid_fractions(
                    self.bin_edges[i], self.bin_edges[i + 1], self._current_step
                )
                last = x0
                for f in fracs:
                    x = x0 + f * (x1 - x0)
                    if x - last < SUBGRID_MIN_GAP_PX or x1 - x < SUBGRID_MIN_GAP_PX:
                        continue
                    painter.drawLine(x, rect.top(), x, rect.bottom())
                    last = x

    def _strip_readout_text(self) -> Optional[str]:
        """The "Chart: ..." segment of the upper-left readout -- always
        shown once there's something plotted. While calendar units are
        being stripped from the x-axis labels it's what was stripped, for
        the first to the last labelled bin (see TimeAxis.strip_context),
        exactly as the old "Shown ..." segment was; otherwise (option
        off, size axis, year bins, ...) it's the chart's current visible
        range instead -- first to last labelled bin, in the axis's own
        plain tick format -- so it never simply disappears. None only
        when there's nothing to describe (no bins in view yet). The bin
        size itself is a separate, always-shown segment -- see
        _bin_size_readout_text."""
        bins = self._x_label_bins()
        if not bins:
            return None
        first, last = self.bin_edges[bins[0][0]], self.bin_edges[bins[-1][0]]
        if self._strip_active() and self._current_step is not None:
            context = self.axis.strip_context(
                first, last, self._current_step,
                use_names=self.strip_use_names, abbreviate=self.strip_abbreviate_names,
            )
            if context:
                return f"Chart: {context}"
        # Same step lookup the hover readout uses, so a non-nice (arbitrary)
        # bin width still gets a sensible tick format.
        step = self._current_step if self._nice() else self.axis.choose_step(first, last, 1)
        # format_hover_value: the full value (same as format_tick except
        # for nested log size bins, whose ticks are "+offset" labels)
        first_text = self.axis.format_hover_value(first, step)
        last_text = self.axis.format_hover_value(last, step)
        return f"Chart: {first_text}" if first_text == last_text else f"Chart: {first_text} - {last_text}"

    def _draw_readout_segments(self, painter: QPainter, rect: QRectF, align_right: bool, segments: list):
        """Draws `segments` (a list of (text, color) pairs, e.g. the
        upper-left readout's "Bin: ...", "Chart: ..." and hover-text
        parts -- see _paint) left-to-right, each in its own color, with
        a plain-space gap between them, vertically centered in `rect`.
        A single QPainter.drawText call can't mix colors within one
        string, so each segment gets its own call, advanced by its own
        measured width -- same reasoning as the x-axis labels' per-label
        drawText calls. `align_right` anchors the whole run to `rect`'s
        right edge instead of its left (the mirrored-widget case)."""
        if not segments:
            return
        font = QFont()
        font.setPointSize(self.label_font_pt)
        painter.setFont(font)
        fm = QFontMetrics(font)
        sep = "   "
        sep_w = fm.horizontalAdvance(sep)
        widths = [fm.horizontalAdvance(text) for text, _ in segments]
        total = sum(widths) + sep_w * (len(segments) - 1)
        x = rect.right() - total if align_right else rect.left()
        baseline = rect.top() + (rect.height() + fm.ascent() - fm.descent()) / 2
        for (text, color), w in zip(segments, widths):
            painter.setPen(color)
            painter.drawText(QPointF(x, baseline), text)
            x += w + sep_w

    def _bin_size_readout_text(self) -> Optional[str]:
        """"Bin: 1 month" -- the always-shown first segment of the
        upper-left readout (see _paint)."""
        label = self.current_bin_step_label()
        return f"Bin: {label}" if label else None

    def _x_label_bins(self) -> list:
        """The bins that get an x-axis label, as (index, center_x,
        left_x, right_x) tuples in pixels, ascending. A bin is labelled
        while its own center is inside the plot -- a bin that's mostly
        scrolled out of view (center past the plot's left/right edge)
        gets none, and neither does pure padding (see _recompute_bins,
        which pads bin_edges a bit past the view). Centers are taken in
        pixel space (via _value_to_x), so log-scale bins are centered as
        they look, not as their values average.

        For an axis with label_at_bin_start (the size metric) the label
        anchor is the bin's start instead of its center: center_x is
        then the start x, and a bin is labelled while its start is
        inside the plot (incl. the right edge, so the view's last
        boundary gets its value too)."""
        at_start = self.axis.label_at_bin_start
        n_edges = len(self.bin_edges)
        if self.view_start is None or n_edges < 2:
            return []
        rect = self._plot_rect()
        out = []
        for i in range(n_edges - 1):
            lo, hi = self.bin_edges[i], self.bin_edges[i + 1]
            if at_start:
                if lo < self.view_start or lo > self.view_end:
                    continue
            elif hi <= self.view_start or lo >= self.view_end:
                continue
            x0, x1 = self._value_to_x(lo), self._value_to_x(hi)
            cx = x0 if at_start else (x0 + x1) / 2
            tol = 0.5 if at_start else 0.0  # float slop at the plot's edges
            if cx < rect.left() - tol or cx > rect.right() + tol:
                continue
            out.append((i, cx, x0, x1))
        return out

    def _x_axis_label_layout(self, fm: QFontMetrics) -> list:
        """Which x-axis labels are drawn and where: a list of (text,
        origin_x, text_width, bin_index, bin_center_x) for the surviving
        bins, ascending.

        Each bin is labelled with its own start value. For an axis with
        label_at_bin_start (the size metric) the label's origin is
        simply the bin's start tick. Otherwise the label is
        centered under the bin -- centered as the *rotated* text
        actually looks: a label at angle a (down-right from its origin)
        of text width w and height h covers w*cos(a) + h*sin(a) pixels
        horizontally, from -(h/2)*sin(a) to w*cos(a) + (h/2)*sin(a)
        relative to its origin, so the origin goes at
        center - (w/2)*cos(a). If that horizontal extent is as wide as
        the bin or wider, centering would just push the label out over
        its neighbours, so it starts at the bin's own start instead
        (a small inset in, with the rotated box's left edge there).

        Then a left-to-right walk (least value first) keeps a label only
        if it doesn't overlap any label already kept -- skipped labels
        block nothing. All labels share one rotation, so any two are the
        same rectangle offset by the distance d between their origins;
        in the text's own frame that offset is (d*cos(a), d*sin(a)) and
        they overlap only if they overlap along the text *and* across it
        (the same geometry _auto_tick_angle_deg reasons with). That
        covers very long labels that span several bins, and very narrow
        bins where even a 90 degree label reaches into the next bin."""
        bins = self._x_label_bins()
        if not bins:
            return []
        tick_step = self._label_tick_step()
        angle = math.radians(self._effective_tick_angle_deg)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        h = fm.height()
        half_h_sin = (h / 2) * sin_a
        along_gap = fm.horizontalAdvance("0") * X_LABEL_ALONG_GAP_CHARS
        across_clear = h * X_LABEL_CROSS_CLEARANCE
        min_x = X_LABEL_EDGE_INSET
        max_x = self.width() - X_LABEL_EDGE_INSET

        at_start = self.axis.label_at_bin_start
        kept = []
        for i, cx, x0, x1 in bins:
            label = self._format_x_tick(self.bin_edges[i], tick_step)
            w = fm.horizontalAdvance(label)
            if at_start:
                # the text starts right at the bin's start tick
                ox = x0
            elif w * cos_a + h * sin_a < x1 - x0:
                ox = cx - (w / 2) * cos_a
            else:
                ox = x0 + X_LABEL_EDGE_INSET + half_h_sin
            # Keep the whole rotated box inside the widget (left edge
            # wins if it can't fit both) - a centered label near either
            # end would otherwise be cut off by the widget boundary.
            right = ox + w * cos_a + half_h_sin
            if right > max_x:
                ox -= right - max_x
            if ox - half_h_sin < min_x:
                ox = min_x + half_h_sin
            clash = False
            for _, k_ox, k_w, _, _ in kept:
                d = ox - k_ox
                along = d * cos_a
                if (abs(d * sin_a) < across_clear
                        and along < k_w + along_gap
                        and along + w + along_gap > 0):
                    clash = True
                    break
            if not clash:
                kept.append((label, ox, w, i, cx))
        return kept

    def _x_label_color(self, i: int, cx: float, range_px) -> QColor:
        """Text color of bin `i`'s x-axis label: axis_label_color, or
        selection_color for a fully selected bin (a blend halfway for a
        partly selected one), pulled toward selection_color by
        selection_opacity too if the label sits inside the drag-selected
        range (`range_px`, its pixel bounds or None) -- the same
        strength that range's fill has over the bars -- and finally
        tinted toward hover_color by hover_opacity for the hovered bin."""
        color = QColor(self.axis_label_color)
        state = self._bin_selection_state(i)
        if state == "full":
            color = QColor(self.selection_color)
        elif state == "partial":
            color = self._mix(color, self.selection_color, 0.5)
        if range_px is not None and range_px[0] <= cx <= range_px[1]:
            color = self._mix(color, self.selection_color, self.selection_opacity / 100)
        if i == self._hover_bin:
            color = self._hover_tint(color)
        return color

    def _draw_x_axis_labels(self, painter: QPainter, rect: QRectF):
        """The x-axis tick labels themselves (rotated down-right from
        where they're anchored, see _x_axis_label_layout for which bins
        get one and where each sits: centered under its bin, or starting
        at the bin's start if it's as long as the bin or longer, with
        labels that would overlap an earlier one skipped). See
        _draw_axis_labels for the gridlines they sit under. Drawn
        unclipped, after _paint has already dropped the horizontal clip
        used for bars/gridlines, so a label near either end of the plot
        prints in full instead of being chopped at the plot's edge (it
        may extend into the margins; _x_axis_label_layout keeps it
        inside the widget itself)."""
        font = QFont()
        font.setPointSize(self.label_font_pt)
        painter.setFont(font)
        fm = QFontMetrics(font)
        label_h = fm.height()
        y_offset = self._axis_label_y_offset(label_h)

        if len(self.bin_edges) < 2:
            return
        range_px = None
        if self.selection_range:
            xa, xb = (self._value_to_x(v) for v in self.selection_range)
            range_px = (min(xa, xb), max(xa, xb))
        for label, ox, label_w, i, cx in self._x_axis_label_layout(fm):
            painter.setPen(self._x_label_color(i, cx, range_px))
            # The label is rotated about (ox, baseline + y_offset).
            # AlignVCenter centers it on that origin along the axis
            # perpendicular to how it reads: at angle==0 that's straight
            # up/down (y_offset's extra clearance, _axis_label_y_offset,
            # keeps the top half off the axis baseline); at angle==90
            # it's left/right, which _x_axis_label_layout accounts for
            # when it picks ox. Sized to the measured text and drawn with
            # TextDontClip so drawText never chops a long label's tail.
            painter.save()
            painter.translate(ox, rect.bottom() + y_offset)
            painter.rotate(self._effective_tick_angle_deg)
            painter.drawText(
                QRectF(0, -label_h / 2, label_w + 4, label_h),
                Qt.AlignLeft | Qt.AlignVCenter | Qt.TextDontClip,
                label,
            )
            painter.restore()

    # ------------------------------------------------------------------ zoom
    def zoom(self, factor: float, pivot_v: Optional[float] = None):
        if self.view_start is None:
            return
        if self.axis.nonlinear:
            self._zoom_axis_space(factor, pivot_v)
            return
        span = self.view_end - self.view_start
        if pivot_v is None:
            pivot_v = self.view_start + span / 2
        new_span = span * factor
        max_span = (self.global_end - self.global_start) * 4 if self.global_end else span
        new_span = max(self.axis.min_span, min(new_span, max_span))
        frac = (pivot_v - self.view_start) / span if span else 0.5
        self.view_start = pivot_v - frac * new_span
        self.view_end = self.view_start + new_span
        self._clamp_view_to_axis_min()
        self._recompute_bins()
        self.viewRangeChanged.emit(self.view_start, self.view_end)

    def _zoom_axis_space(self, factor: float, pivot_v: Optional[float]):
        """zoom() for a nonlinear axis (SizeAxis's nested log mode):
        scales the AXIS-SPACE span around the pivot instead of the raw
        value span, so each wheel step zooms the same visual amount and
        the pivot stays under the cursor -- scaling raw bytes there
        would shift the pivot and zoom unevenly. Distances only (see
        _value_to_x), so it stays exact however deep the view is."""
        delta = self.axis.axis_delta
        from_delta = self.axis.from_axis_delta
        span = delta(self.view_end, self.view_start)
        if span <= 0:
            return
        if pivot_v is None:
            pivot_v = from_delta(self.view_start, span / 2)
        frac = delta(pivot_v, self.view_start) / span
        frac = min(1.0, max(0.0, frac))
        new_span = span * factor
        if self.global_end is not None:
            # zoom-out cap: the whole loaded range plus about one bin
            max_span = delta(max(self.global_end, 10.0), 0.0) + 1.0
            new_span = min(new_span, max_span)
        new_start = from_delta(pivot_v, -frac * new_span)
        new_end = from_delta(pivot_v, (1.0 - frac) * new_span)
        if self.axis.min_value is not None and new_start < self.axis.min_value:
            new_start = self.axis.min_value
            new_end = from_delta(new_start, new_span)
        if factor < 1 and new_end - new_start < self.axis.min_span:
            return  # already at single-byte resolution
        # May step the zoom level up/down (re-fitting around the pivot)
        self.view_start, self.view_end = self.axis.relevel(new_start, new_end, pivot_v)
        self._recompute_bins()
        self.viewRangeChanged.emit(self.view_start, self.view_end)

    def zoom_vertical(self, factor: float):
        """Zoom the frozen vertical-range ceiling (fixed_max_count) in/
        out, floor always pinned at 0 -- mirrors `zoom`'s horizontal
        pivot-based zoom, but the y-axis only ever has one end that
        moves (the ceiling; 0 is always the other), so there's no pivot
        to solve for. Only called while Auto Range is off (see
        wheelEvent) -- with it on, the ceiling is recomputed from the
        data every frame instead (_display_max_count) and there's
        nothing fixed here to zoom.

        In log_scale mode, bar height is proportional to log1p(count)
        (see _count_to_frac), so scaling fixed_max_count itself by
        `factor` would zoom by very different visual amounts depending
        on where the ceiling currently sits (10 -> 8 barely moves the
        axis; 10000 -> 8000 moves it far less than the same ratio
        suggests). Scaling in log1p-space instead keeps each wheel
        step's *visual* zoom the same regardless of the current
        ceiling -- the same "zoom feels consistent" reasoning behind
        gridline/bar placement already going through log1p for this
        axis."""
        current = max(self.fixed_max_count, 1)
        if self.log_scale:
            new_max = math.expm1(math.log1p(current) * factor)
        else:
            new_max = current * factor
        # Floor of 1 (not 0) - a max_count of 0 would make every bar's
        # _count_to_frac divide-by-zero/degenerate; 0 itself is already
        # always the axis's own floor (see _count_to_frac/_display_max_
        # count), never something you zoom the ceiling down onto.
        self.fixed_max_count = max(1, round(new_max))
        self.update()

    def pan_by_fraction(self, frac_of_span: float):
        """Pan by a fraction of the current visible span (negative = left).
        The span this fraction is taken of is the AXIS-SPACE span (see
        _shift_view) -- for every axis except SizeAxis's logarithmic mode
        that's identical to the raw span, so this reads the same as
        before there; log mode is exactly the case _shift_view's own
        docstring covers."""
        if self.view_start is None:
            return
        axis_span = self.axis.axis_delta(self.view_end, self.view_start)
        self._shift_view(frac_of_span * axis_span)

    def _shift_view(self, d_axis: float):
        """Shift the view by `d_axis`, given in AXIS SPACE (see
        axis.to_axis_space) rather than raw value space. For every axis
        except SizeAxis's logarithmic mode, axis space *is* raw value
        space (to_axis_space is the identity there), so this is no
        behavior change for those. For a log size axis, raw value space
        and axis space are related exponentially, not linearly (see
        SizeAxis.to_axis_space) -- shifting by a fixed *raw* amount, the
        old behaviour, covers a different number of decades depending on
        where in raw-value space the window currently sits, so the same
        fixed-pixel drag made bins visibly grow or shrink as the view
        panned toward/away from zero, instead of the constant per-decade
        pixel width "one bin per decade, every decade the same width"
        (see SizeAxis's own docstring) is supposed to guarantee. Shifting
        in axis space instead keeps the axis-space span -- and so each
        bin's pixel width -- constant regardless of where the view sits.

        `d_axis` is clamped, in axis space, to whatever keeps the result
        inside a sane range -- deliberately clamping the *shift itself*
        here rather than the resulting view_start/view_end afterward
        (the old approach, via _clamp_view_to_axis_min, which this
        replaces for panning specifically):

        - Low side: axis.min_value's own axis-space position (0 bytes,
          which to_axis_space represents the same as 1 byte -- see its
          docstring -- since log10 isn't defined at/below 0). Clamping
          the raw *result* after the fact doesn't work here: once
          view_start drops under 1, to_axis_space(view_start) clamps
          right back to that same floor on the *next* call regardless
          of how much further view_start actually dropped, so view_end
          (unclamped, still sliding normally) kept closing in on it --
          each further drag ate more of the axis-space span with
          nothing to show for it, which is exactly what read as the
          view continuously "zooming in" no matter how far you dragged.
          Clamping the shift instead means view_start simply stops
          dead at the floor, span intact, same as hitting a wall.
        - High side: global_end's axis-space position -- panning can't
          scroll the loaded data further out of view than "the whole
          thing has just scrolled past the left edge". Without this,
          nothing stopped a sustained drag from pushing view_start/
          view_end to arbitrarily large raw values (1e50+ observed) --
          harmless here, but fatal a step downstream: DirComp's own
          scrollbar sync (_on_timeline_view_range_changed) turns
          view_start into a scrollbar position via a plain int() cast,
          which overflows Qt's 32-bit QScrollBar.setValue() once
          view_start gets that large. Bounding the pan itself keeps
          view_start/view_end from ever reaching numbers that large in
          the first place."""
        if self.view_start is None:
            return
        # Distances (axis_delta) relative to the current view, not
        # absolute axis positions -- see _value_to_x.
        delta = self.axis.axis_delta
        from_delta = self.axis.from_axis_delta
        lo = delta(self.axis.min_value, self.view_start) if self.axis.min_value is not None else None
        hi = delta(self.global_end, self.view_start) if self.global_end is not None else None
        if lo is not None and d_axis < lo:
            d_axis = lo
        elif hi is not None and d_axis > hi:
            d_axis = hi
        if lo is not None and d_axis == lo:
            new_start = self.axis.min_value
        else:
            new_start = from_delta(self.view_start, d_axis)
        self.view_end = from_delta(self.view_end, d_axis)
        self.view_start = new_start
        self._recompute_bins()
        self.viewRangeChanged.emit(self.view_start, self.view_end)

    def set_view_start(self, new_start: float):
        """Used by an external scrollbar: keep the AXIS-SPACE span (see
        _shift_view), move the start to `new_start` (a raw value, as
        supplied by the scrollbar -- see DirComp's
        TimelineOpsMixin._on_statistics_scrollbar_moved). Deriving the new
        view_end from the axis-space span rather than the raw one keeps
        scrollbar-driven panning consistent with mouse-drag panning (see
        _shift_view) -- both now preserve on-screen bin width instead of
        raw value width. Routed through _shift_view (as a delta from the
        current position) rather than assigning view_start/view_end
        directly, so the scrollbar picks up the same low/high pan clamp
        _shift_view already applies -- a scrollbar thumb dragged to its
        own extreme is "panning" in exactly the same sense a mouse-drag
        is, and needs the same guard against the zoom-in-near-zero and
        scrollbar-overflow-near-infinity failure modes documented there."""
        if self.view_start is None:
            return
        self._shift_view(self.axis.axis_delta(new_start, self.view_start))

    # ------------------------------------------------------------- selection
    def clear_selection(self):
        self.selection_range = None
        self._last_click_anchor_value = None
        self._set_selection(set(), emit=True)

    def zoom_to_selection(self):
        if self.selection_range:
            v0, v1 = self.selection_range
            # The drag-range overlay itself is consumed by the zoom (it'd
            # just cover the whole view edge-to-edge afterwards), but the
            # file selection is not - it stays highlighted (recolored
            # against the new, finer bins) until something actually
            # clears it.
            self.selection_range = None
            self._zoom_to_range(v0, v1)
            return
        # No drag-range overlay -- fall back to the span covered by
        # whatever bins are currently selected (a plain or ctrl+click
        # bin selection, or an external selection with no range of its
        # own), so "Zoom to Selection" also does something useful for
        # those, not just an actual dragged range.
        bounds = self._selected_bins_bounds()
        if bounds is not None:
            self._zoom_to_range(*bounds)

    def _selected_bins_bounds(self) -> Optional[tuple]:
        """(min, max) value spanning every bin that currently has any
        selected files (full or partial) -- the zoom_to_selection
        fallback target when there's no active drag-range. None if
        nothing is selected."""
        edges = [
            (self.bin_edges[i], self.bin_edges[i + 1])
            for i in range(len(self.bin_counts))
            if self._bin_selection_state(i) != "none"
        ]
        if not edges:
            return None
        return min(e[0] for e in edges), max(e[1] for e in edges)

    def _zoom_to_range(self, v0: float, v1: float):
        """Make [v0, v1) the new visible window - shared by drag-selection
        zoom and click-to-zoom on an already-selected bin."""
        if v1 - v0 < self.axis.min_span:
            v1 = v0 + self.axis.min_span
        # zoom level for the new range (raw range kept as is)
        self.view_start, self.view_end = self.axis.relevel(v0, v1)
        self._recompute_bins()
        self.viewRangeChanged.emit(self.view_start, self.view_end)

    def _emit_selection(self):
        if not self.selection_range:
            self._set_selection(set(), emit=True)
            return
        v0, v1 = self.selection_range
        paths = {
            self._path_of(r) for r in self.records
            if v0 <= self._value_of(r) < v1 and self._path_of(r) is not None
        }
        self._set_selection(paths, emit=True)

    # --------------------------------------------------------------- mouse
    def mousePressEvent(self, event):
        if self.view_start is None:
            return
        x = event.position().x()
        btn = event.button()
        if btn not in (Qt.LeftButton, Qt.RightButton):
            return
        self._press_x = x
        self._press_moved = False
        self._press_button = btn
        select_intent = btn == Qt.RightButton or (
            btn == Qt.LeftButton and bool(event.modifiers() & Qt.ShiftModifier)
        )
        if select_intent:
            self._selecting = True
            self._select_start_x = x
            self._select_cur_x = x
            self.setCursor(Qt.CrossCursor)
        else:
            self._panning = True
            self._pan_last_x = x
            self.setCursor(Qt.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, event):
        if self.view_start is None:
            return
        x = event.position().x()
        zone = self._zoom_zone_at(x)
        if zone != self._hover_zoom_zone:
            self._hover_zoom_zone = zone
            self.update()
        if self._press_x is not None and abs(x - self._press_x) > DRAG_THRESHOLD_PX:
            self._press_moved = True
        if self._panning:
            dx = x - self._pan_last_x
            self._pan_last_x = x
            rect = self._plot_rect()
            if rect.width() > 0:
                # Axis-space delta, not raw -- see _shift_view's docstring
                # for why that distinction matters once a log size axis
                # is in play.
                axis_span = self.axis.axis_delta(self.view_end, self.view_start)
                d_axis = -dx / rect.width() * axis_span
                self._shift_view(d_axis)
        elif self._selecting:
            self._select_cur_x = x
            self.update()
        else:
            self._hover_bin = self._x_to_bin(x)
            self._hover_bin_value = self._bin_midpoint(self._hover_bin)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() not in (Qt.LeftButton, Qt.RightButton):
            return
        was_drag = self._press_moved
        button = self._press_button
        if self._panning:
            self._panning = False
            self.setCursor(Qt.OpenHandCursor)
        if self._selecting:
            self._selecting = False
            if was_drag:
                self._finish_selection()
            self._select_start_x = None
            self._select_cur_x = None
            self.setCursor(Qt.OpenHandCursor)
        if not was_drag:
            if button == Qt.RightButton:
                # a right-click with no drag zooms out, centred on the
                # click; dragging with the right button still selects
                # a range (handled above via _finish_selection).
                self._zoom_out_at(event.position().x())
            else:
                shift = bool(event.modifiers() & Qt.ShiftModifier)
                ctrl = bool(event.modifiers() & Qt.ControlModifier)
                self._handle_click(event.position().x(), event.position().y(), shift=shift, ctrl=ctrl)
        self._press_x = None
        self._press_moved = False
        self._press_button = None
        self.update()

    def _zoom_out_at(self, x: float):
        pivot_v = self._x_to_value(x)
        self.zoom(1 / 0.7, pivot_v)

    def _finish_selection(self):
        if self._select_start_x is None or self._select_cur_x is None:
            return
        x0, x1 = self._select_start_x, self._select_cur_x
        i0 = self._x_to_bin(min(x0, x1))
        i1 = self._x_to_bin(max(x0, x1))
        if i0 is None or i1 is None:
            return
        v0 = self.bin_edges[i0]
        v1 = self.bin_edges[i1 + 1]
        self.selection_range = (v0, v1)
        self._last_click_anchor_value = None
        self._emit_selection()

    def _handle_click(self, x: float, y: float, shift: bool = False, ctrl: bool = False):
        """A press+release with no meaningful drag in between."""
        idx = self._x_to_bin(x)
        anchor_idx = self._bin_index_for_value(self._last_click_anchor_value)

        if ctrl:
            # Ctrl+click: add/remove one bin's files to/from the current
            # selection instead of replacing it, so several (not
            # necessarily adjacent) bins can be built up one at a time.
            # Bypasses the shift-range-chain and click-inside-range-zooms
            # behavior below entirely -- ctrl+click is always "toggle
            # this bin", regardless of any active range or anchor.
            bar_hit = idx is not None and self._y_hits_bar(idx, y)
            if bar_hit:
                self._toggle_bin_selection(idx)
            return

        # click a bin, then shift+click another -> inclusive range select,
        # same result as dragging from one to the other.
        if shift and anchor_idx is not None and idx is not None and self.selection_range is None:
            i0, i1 = sorted((anchor_idx, idx))
            self.selection_range = (self.bin_edges[i0], self.bin_edges[i1 + 1])
            self._last_click_anchor_value = None
            self._emit_selection()
            self.update()
            return

        if self.selection_range is not None:
            v = self._x_to_value(x)
            v0, v1 = self.selection_range
            if v0 <= v <= v1:
                self.zoom_to_selection()
            else:
                self.clear_selection()
            return

        bar_hit = idx is not None and self._y_hits_bar(idx, y)

        if not bar_hit:
            # missed every bar - either past the axis edges, or inside a
            # bin's column but above its bar (the empty space above it).
            # Either way that's "outside", same as clicking beyond it.
            self._last_click_anchor_value = None
            self._set_selection(set(), emit=True)
            return

        if self._bin_selection_state(idx) == "full":
            # clicking an already (fully) selected bar zooms in, same as
            # clicking inside a drag-selected range - the selection itself
            # is untouched, only the view changes.
            v0, v1 = self.bin_edges[idx], self.bin_edges[idx + 1]
            self._last_click_anchor_value = None
            self._zoom_to_range(v0, v1)
            return

        self._last_click_anchor_value = self._bin_midpoint(idx)
        v0, v1 = self.bin_edges[idx], self.bin_edges[idx + 1]
        label = f"{self._bin_size_prefix()}{self.axis.format_bin(v0, v1)} - {self.bin_counts[idx]} {self._item_label}(s)"
        self.binInfo.emit(label)
        # clicking a bin highlights its files, same as a drag-selection
        paths = {
            self._path_of(r) for r in self.records
            if v0 <= self._value_of(r) < v1 and self._path_of(r) is not None
        }
        self._set_selection(paths, emit=True)

    def _toggle_bin_selection(self, idx: int):
        """Ctrl+click: add bin `idx`'s files to the current selection if
        it isn't (fully) selected yet, or remove them if it is --
        building a multi-bin selection one click at a time. Clears any
        active drag-range overlay first, same as a plain bin click
        implicitly does by never setting selection_range itself -- a
        ctrl+click selection is a discrete set of bins, not a range."""
        v0, v1 = self.bin_edges[idx], self.bin_edges[idx + 1]
        bin_paths = {
            self._path_of(r) for r in self.records
            if v0 <= self._value_of(r) < v1 and self._path_of(r) is not None
        }
        self.selection_range = None
        self._last_click_anchor_value = None
        if self._bin_selection_state(idx) == "full":
            new_paths = self.selected_paths - bin_paths
        else:
            new_paths = self.selected_paths | bin_paths
        self._set_selection(new_paths, emit=True)

    def _bin_size_prefix(self) -> str:
        label = self.current_bin_step_label()
        return f"{label} bin: " if label else ""

    def leaveEvent(self, event):
        self._hover_bin = None
        self._hover_bin_value = None
        self._hover_zoom_zone = None
        self.update()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path) or (os.path.isfile(path) and path.lower().endswith(".fss")):
                self.directoryDropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def _zoom_zone_at(self, x: float) -> str:
        """Which axis a wheel event at widget-x `x` would zoom -
        "vertical" over the y-axis/its left margin, "horizontal"
        everywhere else - shared by wheelEvent (to act on it) and
        mouseMoveEvent (to highlight it) so the two can't drift apart."""
        return "vertical" if x <= self._plot_rect().left() else "horizontal"

    def wheelEvent(self, event):
        if self.view_start is None:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        if self.invert_zoom_direction:
            delta = -delta
        factor = 0.85 if delta > 0 else 1 / 0.85
        x = event.position().x()
        if self._zoom_zone_at(x) == "vertical":
            # Over the y-axis itself or the margin to its left (a wider
            # active area than just the axis line, deliberately, so
            # this is easy to actually hit with the mouse) - only
            # meaningful with Auto Range off, since the ceiling is just
            # recomputed from the data every frame otherwise (see
            # _display_max_count) and there's nothing fixed to zoom.
            # With Auto Range on, swallow the event rather than falling
            # through to the horizontal zoom below - a pivot value
            # extrapolated from an x this far outside the plot would be
            # a meaningless place to zoom around horizontally anyway.
            if not self.auto_vrange:
                self.zoom_vertical(factor)
            return
        pivot_v = self._x_to_value(x)
        self.zoom(factor, pivot_v)
