"""
UiBuilderMixin: constructs every widget and layout in the app -- the two
panes (toolbar, tree, item-count row, Select/Clipboard groups), the
central "Transfer" group (operation label, progress bar, Copy/Move
across-pane buttons, Cancel -- kept aligned with the Clipboard/Select
groups' outer edges as the window/splitter resize), the shared "File
Filter" group, and the shared "Global Options" group at the bottom. The
Transfer group's bar/Cancel are shared by both actual transfers and the
Select group's recursive searches (see TransferMixin/SelectionMixin) --
there's no per-pane progress bar anymore. Pure layout/construction; the
behavior wired up here lives in the other mixins.

Two runtime-toggleable layout settings live here:
  - self._symmetrical_buttons: when True (default), the right pane's
    Select/Clipboard groups and surrounding buttons mirror the left
    pane's around the vertical center. When False, the right pane uses
    the same left-to-right order as the left pane instead of mirroring
    it. Toggling it rebuilds the whole central widget (see _rebuild_ui).
Split across files -- all still reached as `self._...`, since
UiBuilderMixin inherits the mixins below:
  - settings_tab.py      Settings tab construction (SettingsTabMixin)
  - theme_ops.py         color/theme handlers, palette, color sets (ThemeOpsMixin)
  - pane_builder.py      one pane's widgets + Statistics panel (PaneBuilderMixin)
  - responsive_layout.py dynamic button-group layout (ResponsiveLayoutMixin)
  - constants.py         layout/style constants shared by all of the above
                         (last section; the date-column ones live in responsive_layout.py)
What stays in this file: the window skeleton (_build_ui/_build_main_tab/
_rebuild_ui, UI state capture/restore), button colors/sizes, the status
line, the File Filter and Global Options groups, and the small
filter/clear/select-directory actions.
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette, QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton, QCheckBox,
    QComboBox, QGroupBox, QSplitter, QFileDialog, QProgressBar, QSpinBox, QTabWidget, QButtonGroup, QSizePolicy,
)

# Project-module imports below that this file itself no longer uses are kept
# on purpose: other modules may still import them via ui_builder.
from constants import IS_DIR_ROLE, CHANGED_FIELD_LABEL, TIMELINE_SCROLLBAR_RANGE, TAG_COLORS
from constants import CMP_NAME_SIZE_DATE, DEFAULT_HASH_ALGORITHM, DEFAULT_FINGERPRINT_CHUNK_KB
from constants import COMPARISON_TYPE_ITEMS
from panes import DirectoryPane
from snapshot_ops import is_snapshot_file_name
from dnd_widgets import DirDropLineEdit, DirDropTreeWidget
from statistics_widget import TimelineWidget, DEFAULT_BIN_COUNT, DEFAULT_LABEL_FONT_PT, TICK_LABEL_ANGLE_DEG
from listing_ops import COL_MODIFIED, COL_CHANGED, COL_ACCESSED
from custom_widgets import (
    SELECTION_HIGHLIGHT_COLOR, EqualSplitLayout, CenterSplitLayout, _INVERT_BUTTON_COLORS, _SELECT_KEY_TO_TAG_COLOR_KEYS,
    _DEFAULT_SELECT_COLORS, _SCROLLBAR_COLORS,
    _DEFAULT_MAIN_BUTTON_COLORS, _MAIN_BUTTON_ICON_KINDS,
    _STATS_BUTTON_KEYS, _THEME_DEFAULT_MAIN_BUTTON_KEYS,
    _press_blend, _darken_hex, _bevel_edge_colors, _bevel_button_stylesheet, _mode_button_stylesheet,
    InvertButton, _SelectionColorDelegate, _ThemedScrollBar, _SelectionMarkerScrollBar,
    install_wrapped_tooltips, set_unnatural_scrolling, is_unnatural_scrolling_enabled,
)

# Layout/style constants live in constants.py now (shared with the mixins below);
# re-imported here so `from ui_builder import X` keeps working elsewhere. The two
# date-column ones moved into responsive_layout.py, their only user.
from constants import (  # noqa: F401
    PANE_TIMELINE_HEIGHT, MINIMUM_WINDOW_WIDTH, _PANE_GAP, _PANE_SIDE_MARGIN, _CLUSTER_V_GAP,
    _DISABLED_CHECKBOX_STYLE, _CLUSTER_GROUP_GAP, _CLUSTER_GROUP_PAD, _TABLE_HEADER_STYLE, _SECTION_HEADER_PT,
    _SECTION_HEADER_STYLE, _SUBTITLE_PT, _SUBTITLE_STYLE, _SUBSUBTITLE_PT, _SUBSUBTITLE_STYLE,
    _SUBSECTION_GAP, _DISABLED_SPINBOX_STYLE, _DEFAULT_TREE_INDENTATION, _MIN_TREE_INDENTATION,
    _MAX_TREE_INDENTATION,
)
from settings_tab import SettingsTabMixin
from theme_ops import ThemeOpsMixin
from pane_builder import PaneBuilderMixin
from responsive_layout import ResponsiveLayoutMixin, _METRIC_TO_DATE_COLUMN, _DATE_COLUMNS  # noqa: F401


class UiBuilderMixin(SettingsTabMixin, ThemeOpsMixin, PaneBuilderMixin, ResponsiveLayoutMixin):
    def _schedule_selection_markers_update(self, pane: DirectoryPane):
        """Coalesce rapid-fire selection/expand signals (a batched bulk
        selection ticking through hundreds of items, a whole subtree's
        worth of expand signals) into a single deferred recompute per
        pane, run once the current call stack unwinds instead of
        synchronously inside whatever signal triggered it.

        _update_selection_markers walks live QTreeWidgetItems
        (childCount/isSelected/data). QTreeWidgetItem isn't a QObject, so
        it doesn't get the usual "touching a deleted Qt object raises a
        clean Python exception" safety net -- calling into it while the
        tree is still mid-mutation on the C++ side (tree.clear() and
        rebuild on double-click navigation, a bulk select, drag-and-
        drop's own nested event loop) risks dereferencing an item that's
        already gone, which segfaults rather than raising. Deferring to
        the next event-loop turn lets whatever's mutating the tree finish
        first. It also fixes a smaller ordering issue: connected directly,
        an itemExpanded handler here would run before the *other*
        itemExpanded handler that actually lazily populates the folder's
        children (see _on_item_expanded, connected later in
        _build_directory_widgets), computing markers against children
        that don't exist yet -- deferred, population has already
        happened by the time this runs.

        Deferring to "the next event-loop turn" isn't automatically
        "safely after whatever was mutating the tree has finished",
        though: ListingMixin.update_listing's own rebuild ticks a
        progress object that periodically pumps
        QApplication.processEvents() to keep Cancel responsive on a
        large folder, and that pump can let an already-scheduled call
        like this one through reentrant, mid-rebuild -- walking a tree
        that's either already been cleared or is still only half-
        repopulated. pane._tree_rebuild_pending (set for the whole
        rebuild, not just around tree.clear() itself -- see there) is
        checked inside run() for exactly that: if it's set, this
        re-defers itself instead of touching the tree, and
        update_listing calls _update_selection_markers directly, once,
        right at the end of its own rebuild (see its tail) -- so this
        still ends up correct, just doesn't run mid-flight."""
        if getattr(pane, "_markers_update_pending", False):
            return
        pane._markers_update_pending = True

        def run():
            pane._markers_update_pending = False
            if pane.tree is None:
                return
            if getattr(pane, "_tree_rebuild_pending", False):
                pane._markers_update_pending = True
                QTimer.singleShot(0, run)
                return
            self._update_selection_markers(pane)

        QTimer.singleShot(0, run)

    def _update_selection_markers(self, pane: DirectoryPane):
        """Recompute (pane.tree's) scrollbar selection markers -- see
        _SelectionMarkerScrollBar -- from whatever's currently selected.
        Always go through _schedule_selection_markers_update rather than
        calling this directly from a signal handler -- see its docstring.

        Positions are fractions along _iter_visible_items' own order --
        the same "on-screen row order, respecting collapse state" the
        scrollbar itself is actually scrolling through -- rather than
        _iter_items' full, collapse-blind order, which would place a
        marker at the wrong height (or show one at all for a row that's
        currently hidden inside a collapsed folder)."""
        tree = pane.tree
        scrollbar = tree.verticalScrollBar()
        if not isinstance(scrollbar, _SelectionMarkerScrollBar):
            return
        items = list(self._iter_visible_items(tree))
        markers = []
        total = len(items)
        if total > 1:
            for i, item in enumerate(items):
                if not item.isSelected():
                    continue
                tag = item.data(0, Qt.UserRole)
                # Same simplification ListingMixin._apply_tag itself
                # applies before coloring the row, so a marker always
                # matches what that row is actually showing on screen --
                # a recursive-mode differing folder reads as the single
                # "folder differs" color there, not whichever finer-
                # grained smaller/larger/newer/older tag it's really
                # storing underneath.
                display_tag = self._display_tag(item, tag) if tag else tag
                if display_tag and display_tag.startswith("dup_"):
                    bg = self._duplicate_group_colors(int(display_tag[4:]))[0]
                else:
                    bg, _fg = TAG_COLORS.get(display_tag, (None, None))
                # A "file"/"directory" tag (TAG_COLORS has no background
                # for either -- see constants.py) means there's nothing
                # on the other side to diff against right now, most
                # commonly because the other pane has no folder open at
                # all. The row itself still shows selected (via the
                # tree's own selection color -- see selection_color
                # above), so fall back to that same color here rather
                # than silently showing no marker for a real selection.
                if bg is None:
                    bg = self._selection_tint_color
                markers.append((i / (total - 1), QColor(bg)))
        scrollbar.set_markers(markers, total)

    def _build_ui(self):
        # Every tooltip in the app gets hard line breaks at ~56
        # characters (see custom_widgets._WrappedToolTipFilter) --
        # installed once, whichever build pass gets here first.
        install_wrapped_tooltips()
        if not hasattr(self, "_symmetrical_buttons"):
            self._symmetrical_buttons = True
        if not hasattr(self, "_dual_pane_enabled"):
            self._dual_pane_enabled = True
        if not hasattr(self, "_force_single_date_column"):
            self._force_single_date_column = False
        if not hasattr(self, "_selection_tint_color"):
            self._selection_tint_color = SELECTION_HIGHLIGHT_COLOR
        if not hasattr(self, "_selection_tint_opacity"):
            self._selection_tint_opacity = 50
        if not hasattr(self, "_flv_bg_color"):
            # Settings tab -> File List View -> File List View Colors ->
            # "Default Background": the file lists' own background. None
            # = follow the theme's list background (_default_base_color,
            # i.e. what it has always been); a hex string once it's been
            # set explicitly. Per theme.
            self._flv_bg_color = None
        if not hasattr(self, "_indent_line_steps"):
            self._indent_line_steps = 1
        if not hasattr(self, "_indent_line_color1"):
            self._indent_line_color1 = "#A0A0A0"
        if not hasattr(self, "_indent_line_color2"):
            self._indent_line_color2 = "#4682DC"
        if not hasattr(self, "_indent_line_reverse"):
            self._indent_line_reverse = False
        if not hasattr(self, "_indent_hover_color"):
            self._indent_hover_color = SELECTION_HIGHLIGHT_COLOR
        if not hasattr(self, "_indent_hover_opacity"):
            self._indent_hover_opacity = 50
        if not hasattr(self, "_indent_lines_enabled"):
            # Settings tab -> File List View -> Indentation Line ->
            # Enable / Line thickness (see DirDropTreeWidget.drawBranches).
            self._indent_lines_enabled = True
        if not hasattr(self, "_indent_line_thickness"):
            self._indent_line_thickness = 1
        if not hasattr(self, "_indent_line_style"):
            # Settings tab -> File List View -> Indentation Line -> Line
            # style: 0 straight, 1 diagonally indented, 2 curved
            # indented, 3 S-curve indented (dnd_widgets.LINE_STYLE_*).
            # Per theme.
            self._indent_line_style = 0

        if not hasattr(self, "_dup_steps"):
            # Settings tab -> File List View Colors -> Default / Misc colors -> Duplicate File Colors (Duplicate
            # Finder mode: one background color per set of identical files,
            # from a color-wheel range like the indentation lines'; see
            # ThemeMixin._duplicate_group_colors). Per theme. _dup_fg_mode:
            # 0 = the chosen Fg color, 1 = inverse of the background,
            # 2 = black or white by the background's luminance.
            self._dup_steps = 7
            self._dup_color1 = "#F0A0A0"
            self._dup_color2 = "#A0A0F0"
            self._dup_reverse = False
            self._dup_fg_mode = 2
            self._dup_fg_color = "#000000"

        if not hasattr(self, "_indent_tick_enabled"):
            # Settings tab -> File List View -> Indentation Line -> Line
            # style -> Enable file tick marks / Tick mark length. Per theme.
            self._indent_tick_enabled = False
            self._indent_tick_length = 6

        if not hasattr(self, "_tree_indentation"):
            self._tree_indentation = _DEFAULT_TREE_INDENTATION
        if not hasattr(self, "_file_list_history_limit"):
            # Settings tab -> Global Options -> "Nr. of history entries in
            # File List View" (see panes.DirectoryPane.folder_history /
            # SettingsMixin._record_folder_history).
            self._file_list_history_limit = 10
        if not hasattr(self, "_cmp_type"):
            # Settings tab -> Global Options -> comparison type controls.
            # Plain attributes (not read back from the widgets) so they
            # survive a UI rebuild; see DiffEngineMixin._comparison_key
            # and SettingsMixin._on_comparison_setting_changed.
            self._cmp_type = CMP_NAME_SIZE_DATE
            self._cmp_algorithm = DEFAULT_HASH_ALGORITHM
            self._cmp_chunk_start_kb = DEFAULT_FINGERPRINT_CHUNK_KB
            self._cmp_chunk_end_kb = DEFAULT_FINGERPRINT_CHUNK_KB
            self._cmp_ignore_ext = False
            self._cmp_first_name = False
            self._cmp_applied_key = None
            # Settings tab -> Options -> "Ignore empty files" / "Ignore hard
            # links" (see DiffEngineMixin._ignored_kind): off by default.
            self._ignore_empty = False
            self._ignore_hardlinks = False
            self._ignore_applied = (False, False)
            # Settings tab -> Global Options -> "Scan expanded folders" and
            # "Hash only same-size files" (see DuplicateFinderMixin).
            self._dup_survivor = 0  # Extra copies keep: 0 shortest path, 1 oldest, 2 newest, 3 first
            self._scan_expanded = True
            self._hash_same_size = True
            # Settings tab -> Options -> "Cache hashed folders" (see
            # HashOpsMixin._hash_cache_enabled): off by default.
            self._cache_hashed_folders = False
        if not hasattr(self, "_select_colors"):
            self._select_colors = dict(_DEFAULT_SELECT_COLORS)
        if not hasattr(self, "_summary_colors"):
            # Settings tab -> Comparison Type Colors -> "Summary Text Color":
            # the colors of the per-type summary under each file list. Per theme.
            self._summary_colors = {k: c for k, (_label, c) in self._SELECT_TYPE_COLORS.items()}
        if not hasattr(self, "_folder_color"):
            self._folder_color = TAG_COLORS.get("directory", (None, "#E0E090"))[::-1]  # (fg, bg)
        if not hasattr(self, "_progress_bar_colors"):
            self._progress_bar_colors = {"fg": "#4A90D9", "bg": "#2A2A2A", "busy": "#E0A030"}
        if not hasattr(self, "_custom_default_button_colors"):
            self._custom_default_button_colors = None  # None -> palette-derived (see _default_button_colors)
        if not hasattr(self, "_theme"):
            self._theme = "dark"
        if not hasattr(self, "_gui_colors"):
            # Settings tab -> Misc Colors -> "GUI": custom (fg, bg)
            # for the window text/background, kept separately for each
            # theme -- None -> that theme's built-in colors (see
            # _effective_gui_colors/_apply_theme_palette).
            self._gui_colors = {"dark": None, "light": None}
        if not hasattr(self, "_mirror_statistics_buttons"):
            self._mirror_statistics_buttons = False
        if not hasattr(self, "_file_list_ratio"):
            # Fraction of each pane's vertical space the File List view
            # gets, the rest going to the Statistics view below it --
            # see _apply_file_list_ratio_to_splitter/_set_file_list_ratio
            # and the draggable QSplitter built in
            # _build_directory_widgets. 0.75 matches the old, fixed
            # visual proportions reasonably closely.
            self._file_list_ratio = 0.75
        if not hasattr(self, "_stats_colors"):
            # Statistics view palette, split by mode (file/folder) -- see
            # Settings tab's "Statistics View Colors" and
            # TimelineOpsMixin._apply_stats_palette/_stats_palette_for,
            # which push these straight onto TimelineWidget.set_palette.
            # Keys match set_palette's own keyword names (background_
            # color included -- background is per-mode too, not a value
            # shared across both); values mirror statistics_widget.py's
            # module-level defaults exactly, so an untouched install
            # looks exactly as it always did.
            _default_stats_palette = {
                "background_color": "#1E1E20",
                "selection_color": "#E68C14",
                "selection_opacity": 35,
                "bin_info_color": "#d2d2d2",
                "stripped_info_color": "#00ff7f",
                "hover_color": "#D2D2D2",
                "hover_opacity": 35,
                "bin_color": "#4682DC",
                "grid_color": "#3C3C3C",
                "axis_label_color": "#D2D2D2",
                "hover_label_color": "#D2D2D2",
                "selection_info_color": "#E68C14",
            }
            self._stats_colors = {
                "file": dict(_default_stats_palette),
                "folder": dict(_default_stats_palette),
            }
        if not hasattr(self, "_stats_button_colors"):
            # (bg, fg) per button, or None -> falls back to
            # _default_button_colors() (see _stats_button_color_for)
            # until the user actually customizes one via the Settings
            # tab's Statistics View Buttons table -- same "None means
            # automatic" convention as _custom_default_button_colors.
            self._stats_button_colors = {key: None for key in _STATS_BUTTON_KEYS}
        if not hasattr(self, "_main_button_colors"):
            self._main_button_colors = {k: list(v) for k, v in _DEFAULT_MAIN_BUTTON_COLORS.items()}
            # Rename, Copy -->/Move -->, Refresh and the File List View's
            # own buttons have no fixed design color of their own -- None
            # here means "use _default_button_colors()" until customized,
            # same convention self._stats_button_colors uses.
            for key in _THEME_DEFAULT_MAIN_BUTTON_KEYS:
                self._main_button_colors[key] = None
        if not hasattr(self, "_title_numeric_color"):
            # Settings tab -> "Misc Colors" -> "Title Numeric Colors":
            # the color applied to just the numeric portion of a Main-
            # tab/Statistics-view title (see _numeric_span) -- e.g. the
            # "1,234" and "56.7 MB" in "Total: 1,234 items (56.7 MB)", or the file/
            # folder counts in "Files: X - Folders: Y". Defaults to the
            # same orange used elsewhere for "something worth noticing"
            # (the Statistics view's own selection-info color, Fixed
            # Range readout, ...) rather than inventing a new accent.
            self._title_numeric_color = "#E68C14"
        if not hasattr(self, "_title_numeric_bold"):
            self._title_numeric_bold = True
        if not hasattr(self, "_status_selected_color"):
            # Settings tab -> Global Settings -> "Misc Colors" -> "Status
            # line Selected info" / "Status line Unselected" (foreground
            # only): the color of each pane's status-line summary at the
            # bottom of the Main tab (left_selection_label/
            # right_selection_label, see SelectionMixin.
            # update_selection_count) while something is / nothing is
            # selected. Selected defaults to the yellow that was
            # hardcoded there before; None for Unselected means "no color
            # of its own", i.e. the widget's normal palette text color,
            # which is what that label always had.
            self._status_selected_color = "#F5D400"
        if not hasattr(self, "_status_unselected_color"):
            self._status_unselected_color = None
        if not hasattr(self, "_status_warning_color"):
            # Misc Colors -> "Status line warning": e.g. a loaded snapshot
            # that cannot be compared by hash.
            self._status_warning_color = "#FF5050"
        if not hasattr(self, "_history_live_color"):
            # Misc Colors -> "History: live folder" / "History: snapshot
            # file" (Fg only): drop-down text colors of the entry combos.
            self._history_live_color = "#FFFFFF"
            self._history_snapshot_color = "#C8C8C8"
        if not hasattr(self, "_color_sets"):
            # One full set of every color setting per theme (see
            # _builtin_color_set). Only the *inactive* theme's entry is
            # authoritative: the live self._select_colors/TAG_COLORS/...
            # attributes above are the active theme's set, and get
            # captured back into this dict (see _capture_color_set) when
            # the theme switches or settings are saved.
            self._color_sets = {
                "dark": self._builtin_color_set("dark"),
                "light": self._builtin_color_set("light"),
            }

        self.setMinimumWidth(MINIMUM_WINDOW_WIDTH)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_main_tab(), "Main")
        self.tabs.addTab(self._build_settings_tab(), "Settings")
        if not self._dual_pane_enabled:
            self._fill_comparison_copy_holder()

        # Apply the right button-group arrangement and date-column
        # visibility for the window's actual current width --
        # resizeEvent/showEvent keep this current from here on, but
        # neither is guaranteed to fire before the first paint.
        self._update_responsive_layout(force=True)

        if not hasattr(self, "_widget_defaults"):
            # The per-theme widgets' values exactly as this very first
            # build created them -- i.e. the built-in defaults, before
            # any settings file is loaded. Seeds both themes' sets, and
            # is what Reload Defaults puts back.
            self._widget_defaults = self._capture_theme_widget_values()
            for color_set in self._color_sets.values():
                color_set["widgets"] = dict(self._widget_defaults)

    def _build_main_tab(self) -> QWidget:
        """Everything that used to be the whole central widget, before
        the Main/Settings tab split -- both panes, the filter/global-
        options row, and the status line. Unchanged in substance; just
        living inside a tab now instead of being the entire window."""
        central = QWidget()
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        self._main_layout = main_layout

        # The two panes sit in a plain row with equal stretch factors, so
        # they always split the width exactly in the center (no draggable
        # divider any more). Dual Pane off: the right pane's widgets still
        # exist underneath (every other mixin can keep assuming
        # self.right.tree etc. are real, live widgets) -- just not shown,
        # so the left pane gets the freed width.
        panes_row = QWidget()
        panes_layout = EqualSplitLayout(panes_row, _PANE_GAP)
        left_widget = self._build_directory_widgets(self.left)
        right_widget = self._build_directory_widgets(self.right)
        # Above both panes: each pane's status line either side of the
        # centered Compare Folders / Duplicate Finder buttons
        # (built after the panes, which create the status labels).
        main_layout.addWidget(self._build_main_tab_top_row())
        panes_layout.addWidget(left_widget)
        panes_layout.addWidget(right_widget)
        # Duplicate Finder mode shows it too: it lists the duplicates found
        # in the left pane (DuplicateFinderMixin._dup_sync_right).
        right_widget.setVisible(True)
        if not self._dual_pane_enabled:
            # Duplicate Finder mode: the right pane looks right from the
            # start, with or without a folder loaded.
            self._dup_style_right_pane()
        self._panes_row = panes_row
        main_layout.addWidget(panes_row, stretch=1)
        # Under the panes: each pane's Select/File Op/Clipboard cluster
        # either side of the central Folder Op group.
        filter_frame = self._build_filter_frame()
        global_options = self._build_global_options_group()
        if not self._dual_pane_enabled:
            # Duplicate Finder mode: the filter goes under the left pane's
            # button groups, and Quick Options and the Comparison Options
            # copy share one block at the lower right, spanning this row and
            # the one that used to be below it (see _build_cluster_row).
            global_options.setParent(central)
            main_layout.addWidget(self._build_cluster_row(filter_frame))
            self._bottom_row_layout = None
        else:
            main_layout.addWidget(self._build_cluster_row())

            # Bottom row: Global File Filter and Quick Options. The two
            # halves of the row mirror the two panes above (same equal
            # stretch, same gap), so the filter ends exactly at the left
            # pane's right edge and Quick Options sits flush right in the
            # right half.
            bottom_row = EqualSplitLayout(None, _PANE_GAP)
            bottom_row.addWidget(filter_frame)
            options_holder = QWidget()
            holder_layout = QHBoxLayout(options_holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.addWidget(global_options)
            holder_layout.setAlignment(global_options, Qt.AlignRight)
            bottom_row.addWidget(options_holder)
            main_layout.addLayout(bottom_row)
            self._bottom_row_layout = bottom_row

        # Bottom status line: two directional progress bars flanking a
        # shared Cancel button, with the operation label centered next
        # to Cancel -- see _build_status_line. Replaces the old floating
        # "Status" group entirely; no positioning upkeep needed since
        # it's a normal row in main_layout like the one above it.
        main_layout.addWidget(self._build_status_line())

        return central

    def _mode_button_sheet(self) -> str:
        """Stylesheet of the two mode-switch buttons: Settings tab ->
        Misc Buttons -> "Mode switch (unpressed)" / "(pressed)"."""
        bg, fg = self._main_button_color_for("mode_unpressed")
        pressed_bg, pressed_fg = self._main_button_color_for("mode_pressed")
        return _mode_button_stylesheet(bg, fg, pressed_bg, pressed_fg)

    def _build_main_tab_top_row(self) -> QWidget:
        """The row above both panes (where each pane's Hash button used to
        sit): its left half holds the left pane's status line, its right
        half the right pane's, and centered between them the mode switch --
        [Compare Folders] [Duplicate Finder] -- which is not
        mirrored by Symmetrical GUI. The two mode buttons behave like radio
        buttons (exactly one is pressed in, always the current mode) and
        share one width, wide enough for the longer title; clicking the
        other one switches mode, same as the "Compare folders" checkbox
        on the Settings tab (self.dual_pane_cb, which stays the real
        setting -- see _build_global_options_group)."""
        _uniform_w, uniform_h = self._uniform_button_size()
        row = QWidget()
        grid = QGridLayout(row)
        # Same side inset as each pane's own contents, so the status lines
        # line up with the folder entries below them.
        grid.setContentsMargins(_PANE_SIDE_MARGIN, 0, _PANE_SIDE_MARGIN, 0)
        grid.setSpacing(6)

        cluster = QWidget()
        cluster_layout = QHBoxLayout(cluster)
        cluster_layout.setContentsMargins(0, 0, 0, 0)
        cluster_layout.setSpacing(6)

        metrics = QFontMetrics(self.font())
        mode_w = max(metrics.horizontalAdvance("Duplicate Finder"),
                     metrics.horizontalAdvance("Compare Folders")) + 2 * (8 + 2) + 16

        mode_group = QButtonGroup(cluster)
        mode_group.setExclusive(True)

        self.mode_duplicate_button = QPushButton("Duplicate Finder", cluster)
        self.mode_duplicate_button.setToolTip(
            "Duplicate Finder mode: the left pane is searched for duplicate files, "
            "listed in the right pane."
        )
        self.mode_compare_button = QPushButton("Compare Folders", cluster)
        self.mode_compare_button.setToolTip(
            "Compare Folders mode: the two panes' folders are compared with each other."
        )
        for button in (self.mode_duplicate_button, self.mode_compare_button):
            button.setCheckable(True)
            button.setFixedSize(mode_w, uniform_h)
            mode_group.addButton(button)
        self.mode_compare_button.setChecked(self._dual_pane_enabled)
        self.mode_duplicate_button.setChecked(not self._dual_pane_enabled)
        # clicked, not toggled: only a real click on the *other* button
        # changes mode (the exclusive group already keeps the current
        # one pressed in); the checkbox's own handler does the rebuild.
        self.mode_duplicate_button.clicked.connect(lambda checked=False: self._request_mode(False))
        self.mode_compare_button.clicked.connect(lambda checked=False: self._request_mode(True))

        cluster_layout.addWidget(self.mode_compare_button)
        cluster_layout.addWidget(self.mode_duplicate_button)

        self._apply_mode_button_colors()

        for pane, column in ((self.left, 0), (self.right, 2)):
            label = pane.snapshot_info_label
            label.setParent(row)
            # Ignored: the label's own text length must not shift the
            # centered cluster -- both halves stay equal, the text elides.
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            grid.addWidget(label, 0, column)
        grid.addWidget(cluster, 0, 1, Qt.AlignCenter)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)
        return row

    def _build_cluster_row(self, filter_frame=None) -> QWidget:
        """The row under both panes: the left pane's button cluster, the
        central "Folder Op" group (Swap; room for more folder-level
        actions later, e.g. a one-go synchronize), and the right pane's
        cluster. The two clusters share the width the central group leaves
        over equally (see CenterSplitLayout and
        ResponsiveLayoutMixin._cluster_available_width), so they
        re-arrange their buttons for that narrower width. Not mirrored by
        Symmetrical GUI.

        Duplicate Finder mode (`filter_frame` given): the left cell holds
        the left cluster with the Global File Filter below it, and the right
        cell is one block, as tall as both, for the Comparison Options copy
        and Quick Options (ComparisonCopyMixin), lowered to end just above
        the progress bar."""
        row = QWidget()
        layout = CenterSplitLayout(row, _PANE_GAP)
        layout.setContentsMargins(_PANE_SIDE_MARGIN, 0, _PANE_SIDE_MARGIN, 0)
        if self._dual_pane_enabled:
            layout.addWidget(self.left.cluster_container)
            layout.addWidget(self._build_folder_op_group())
            layout.addWidget(self.right.cluster_container)
        else:
            left_cell = QWidget()
            left_cell.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            left_layout = QVBoxLayout(left_cell)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.setSpacing(_PANE_GAP)
            left_layout.addWidget(self.left.cluster_container)
            left_layout.addStretch(1)
            left_layout.addWidget(filter_frame)
            layout.addWidget(left_cell)
            layout.addWidget(self._build_folder_op_group())
            # The right pane is only a list of the duplicates, so its button
            # groups are not shown (and never get arranged -- see
            # _update_responsive_layout). In their place: the block with a
            # copy of the Settings tab's Comparison Options and Quick
            # Options, filled in once that tab exists (see
            # ComparisonCopyMixin).
            self.right.cluster_container.setParent(row)
            self.right.cluster_container.setVisible(False)
            holder = QWidget()
            holder.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            QHBoxLayout(holder).setContentsMargins(0, 0, 0, 0)
            self._cmp_copy_holder = holder
            layout.addWidget(holder)
        self._cluster_row = row
        self._cluster_row_layout = layout
        self._apply_mode_button_colors()
        return row

    def _build_folder_op_group(self) -> QGroupBox:
        """The central "Folder Op" button group -- one button column for
        now (self._folder_op_buttons, top to bottom; append further buttons
        there)."""
        uniform_w, uniform_h = self._uniform_button_size()
        group = QGroupBox("Folder Op")
        grid = QGridLayout(group)
        margins = grid.contentsMargins()
        grid.setContentsMargins(_CLUSTER_GROUP_PAD, margins.top(), _CLUSTER_GROUP_PAD, margins.bottom())
        group.setAlignment(Qt.AlignHCenter)

        self.swap_pane_button = QPushButton("Swap")
        self.swap_pane_button.setToolTip(
            "Swap the two panes: their folders and folder histories (and what is expanded "
            "and selected in them). In Duplicate Finder mode the left pane swaps with the "
            "folder the right pane has in Compare Folders mode."
        )
        self.swap_pane_button.setFixedSize(uniform_w, uniform_h)
        self.swap_pane_button.clicked.connect(lambda checked=False: self._swap_panes())
        self._folder_op_buttons = [self.swap_pane_button]
        for row, button in enumerate(self._folder_op_buttons):
            grid.addWidget(button, row, 0, alignment=Qt.AlignHCenter)
        self.folder_op_group = group
        return group

    def _apply_mode_button_colors(self):
        """Restyle the mode switch and Swap pane buttons from the Settings
        tab's Misc Buttons colors (also called by _apply_main_button_colors)."""
        mode_sheet = self._mode_button_sheet()
        for attr in ("mode_duplicate_button", "mode_compare_button"):
            button = getattr(self, attr, None)
            if button is not None:
                button.setStyleSheet(mode_sheet)
        swap = getattr(self, "swap_pane_button", None)
        if swap is not None:
            swap.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("swap_pane")))

    def _request_mode(self, compare_folders: bool):
        """A mode button was clicked: switch to that mode if it isn't the
        current one (through the Compare folders setting, so the Settings
        tab's checkbox follows)."""
        if compare_folders != self._dual_pane_enabled:
            self.dual_pane_cb.setChecked(compare_folders)

    # ------------------------------------------------------- Rebuild/state

    def _rebuild_ui(self):
        """Full teardown/rebuild of the central widget -- used when a
        setting that affects layout construction (Symmetrical Buttons,
        Dual Pane, a color/theme change, ...) is toggled at runtime.
        Preserves pane paths and the other option
        checkboxes' states across the rebuild -- and which tab
        (Main/Settings) was active, since _build_ui creates a brand new
        QTabWidget each time, which would otherwise always default back
        to tab 0 (Main) regardless of which tab you actually triggered
        this rebuild from. setCurrentIndex happens right after
        _build_ui, before _restore_ui_state -- that call's own
        update_listings()/_recount_all_panes() can pump enough events to
        actually repaint the freshly built tab 0 on screen, so doing it
        after would show that flash of the Main tab before flipping back
        to whichever tab was really active."""
        state = self._capture_ui_state()
        current_tab = self.tabs.currentIndex() if getattr(self, "tabs", None) else 0
        self._build_ui()
        self.tabs.setCurrentIndex(current_tab)
        self._restore_ui_state(state)

    def _capture_ui_state(self) -> dict:
        return {
            "left_path": self.left.entry.text() if getattr(self.left, "entry", None) else "",
            "right_path": self.right.entry.text() if getattr(self.right, "entry", None) else "",
            "filter_text": self.file_filter_entry.text() if hasattr(self, "file_filter_entry") else "",
            "invert_filter": self.invert_filter_cb.isChecked() if hasattr(self, "invert_filter_cb") else False,
            "synchronize_views": self.synchronize_views_cb.isChecked() if hasattr(self, "synchronize_views_cb") else False,
            "recursive_folder_contents": self.recursive_folder_contents_cb.isChecked() if hasattr(self, "recursive_folder_contents_cb") else True,
            "expand_matching": self.expand_matching_cb.isChecked() if hasattr(self, "expand_matching_cb") else True,
            "list_directories": self.list_directories_cb.isChecked() if hasattr(self, "list_directories_cb") else True,
            "list_files": self.list_files_cb.isChecked() if hasattr(self, "list_files_cb") else True,
            "sort_directories_first": self.sort_directories_first_cb.isChecked() if hasattr(self, "sort_directories_first_cb") else True,
            # Statistics View (common) settings -- common_font_size_spin/
            # common_nice_bins_cb/common_bin_count_spin/common_tick_angle_
            # spin are recreated fresh at their hardcoded defaults every
            # _build_ui() (see _build_settings_tab), same as every other
            # widget a rebuild tears down -- unlike the checkboxes above,
            # though, nothing previously carried their actual current
            # value across that rebuild, so any full rebuild (Symmetrical
            # GUI, Dual Pane, Include footer buttons and options in
            # Symmetrical GUI, ...) silently reset the axis font size/
            # nice bins/bin count/tick angle back to defaults. Captured
            # here purely defensively (hasattr guards for the very first
            # build, before these exist yet) and restored below.
            "common_font_size": self.common_font_size_spin.value() if hasattr(self, "common_font_size_spin") else None,
            "common_nice_bins": self.common_nice_bins_cb.isChecked() if hasattr(self, "common_nice_bins_cb") else None,
            "common_bin_count": self.common_bin_count_spin.value() if hasattr(self, "common_bin_count_spin") else None,
            "common_tick_angle": self.common_tick_angle_spin.value() if hasattr(self, "common_tick_angle_spin") else None,
            "common_auto_tick_angle": self.common_auto_tick_angle_cb.isChecked() if hasattr(self, "common_auto_tick_angle_cb") else None,
            "flv_font_size": self.flv_font_size_spin.value() if hasattr(self, "flv_font_size_spin") else None,
            # Invert scrollwheel zoom direction -- a Global Option (see
            # _build_global_options_group), but pushed onto both panes'
            # TimelineWidgets the same way the common_* Statistics View
            # settings above are, so it needs the same capture/restore
            # treatment: the freshly-rebuilt TimelineWidgets otherwise
            # default back to invert_zoom_direction=False.
            "invert_statistics_zoom": self.invert_statistics_zoom_cb.isChecked() if hasattr(self, "invert_statistics_zoom_cb") else None,
            # Settings tab's own scroll position -- _build_settings_tab
            # builds a brand new QScrollArea every rebuild, which always
            # starts scrolled to the top; restored (deferred - see
            # _restore_ui_state) so a rebuild triggered from partway down
            # the tab (e.g. clicking Symmetrical GUI or Include footer
            # buttons and options in Symmetrical GUI there) doesn't throw
            # you back up to the top of it.
            "settings_scroll_pos": (
                self._settings_scroll.verticalScrollBar().value()
                if getattr(self, "_settings_scroll", None) else None
            ),
        }

    def _restore_ui_state(self, state: dict):
        self.file_filter_entry.setText(state["filter_text"])

        self.invert_filter_cb.blockSignals(True)
        self.invert_filter_cb.setChecked(state["invert_filter"])
        self.invert_filter_cb.blockSignals(False)

        self.synchronize_views_cb.setChecked(state["synchronize_views"])

        self.recursive_folder_contents_cb.blockSignals(True)
        self.recursive_folder_contents_cb.setChecked(state["recursive_folder_contents"])
        self.recursive_folder_contents_cb.blockSignals(False)

        self.expand_matching_cb.setChecked(state["expand_matching"])

        self.list_directories_cb.blockSignals(True)
        self.list_directories_cb.setChecked(state["list_directories"])
        self.list_directories_cb.blockSignals(False)

        self.list_files_cb.blockSignals(True)
        self.list_files_cb.setChecked(state["list_files"])
        self.list_files_cb.blockSignals(False)

        self.sort_directories_first_cb.blockSignals(True)
        self.sort_directories_first_cb.setChecked(state["sort_directories_first"])
        self.sort_directories_first_cb.blockSignals(False)

        self.expand_matching_cb.setEnabled(self.recursive_folder_contents_cb.isChecked())

        # Statistics View (common) settings -- restore onto the freshly-
        # rebuilt widgets (see _capture_ui_state) before anything below
        # might repaint the panes; setValue/setChecked's own signal
        # (_on_common_font_size_changed etc.) re-applies the restored
        # value to both panes' TimelineWidgets the same way the user's
        # own change would, so there's no separate "push this onto
        # pane.timeline" step needed here.
        if state.get("common_font_size") is not None and hasattr(self, "common_font_size_spin"):
            self.common_font_size_spin.setValue(state["common_font_size"])
        if state.get("common_nice_bins") is not None and hasattr(self, "common_nice_bins_cb"):
            self.common_nice_bins_cb.setChecked(state["common_nice_bins"])
        if state.get("common_bin_count") is not None and hasattr(self, "common_bin_count_spin"):
            self.common_bin_count_spin.setValue(state["common_bin_count"])
        if state.get("common_tick_angle") is not None and hasattr(self, "common_tick_angle_spin"):
            self.common_tick_angle_spin.setValue(state["common_tick_angle"])
        if state.get("common_auto_tick_angle") is not None and hasattr(self, "common_auto_tick_angle_cb"):
            self.common_auto_tick_angle_cb.setChecked(state["common_auto_tick_angle"])
        if state.get("flv_font_size") is not None and hasattr(self, "flv_font_size_spin"):
            self.flv_font_size_spin.setValue(state["flv_font_size"])
        if state.get("invert_statistics_zoom") is not None and hasattr(self, "invert_statistics_zoom_cb"):
            self.invert_statistics_zoom_cb.setChecked(state["invert_statistics_zoom"])
        self._sync_global_filter_enabled()

        # Settings tab scroll position -- deferred a beat via
        # QTimer.singleShot(0, ...) since the freshly-built scroll area's
        # content hasn't been laid out yet at this exact point in
        # _rebuild_ui/_on_dual_pane_changed, so its scrollbar range isn't
        # necessarily final yet; restoring on the next event-loop turn
        # (same idiom as update_listings elsewhere -- see qt-pitfalls)
        # guarantees the range is real before we set a position against it.
        scroll_pos = state.get("settings_scroll_pos")
        if scroll_pos is not None:
            def _restore_scroll(scroll=self._settings_scroll, pos=scroll_pos):
                if scroll is not None:
                    scroll.verticalScrollBar().setValue(pos)
            QTimer.singleShot(0, _restore_scroll)

        if state["left_path"]:
            self._navigate_pane(self.left, state["left_path"], refresh=False, record_history=False)
        if state["right_path"]:
            self._navigate_pane(self.right, state["right_path"], refresh=False, record_history=False)
        # Rebuilding here doesn't mean either pane's directory could
        # have actually changed -- this only ever runs after a purely
        # cosmetic full rebuild (Symmetrical GUI, Dual Pane, a color/
        # theme change, ...), so there's no reason to re-walk the
        # filesystem the way a real update_listings() call would.
        # _relist_from_existing_snapshots repopulates both (freshly
        # emptied) trees straight from each pane's still-valid, already-
        # in-memory snapshot instead -- see its own docstring.
        if state["left_path"] or state["right_path"]:
            self._relist_from_existing_snapshots()
            self._recount_all_panes()

        # Re-sync each pane's Files/Folders toggle to its actual
        # persisted mode (pane.tl_folder_mode -- set by
        # TimelineOpsMixin._on_timeline_mode_changed, survives the
        # rebuild since the DirectoryPane objects themselves aren't
        # recreated) -- the toggle buttons themselves ARE recreated
        # fresh every rebuild (see _build_directory_widgets) and always
        # default to Files checked, which otherwise left a pane still
        # actually plotting Folder bins with its buttons silently
        # showing Files selected instead. Done last, after the data
        # refresh above, so a genuine mode change this triggers (Files
        # checked -> False, since only an actual state flip emits
        # toggled) has current data to plot rather than whatever the
        # fresh TimelineWidget started with.
        for pane in (self.left, self.right):
            if getattr(pane, "tl_folder_mode", False):
                pane.tl_folders_mode_button.setChecked(True)

    def _on_symmetrical_buttons_changed(self, checked_state):
        self._symmetrical_buttons = bool(checked_state)
        self._rebuild_ui()

    def _on_unnatural_scrolling_changed(self, checked_state):
        # No self._ flag needed -- the app-wide filter's install state
        # (see custom_widgets.set_unnatural_scrolling/
        # is_unnatural_scrolling_enabled) already is that flag, and
        # survives _rebuild_ui the same way.
        set_unnatural_scrolling(bool(checked_state))

    def _on_invert_statistics_zoom_changed(self, checked_state):
        checked = bool(checked_state)
        for pane in (self.left, self.right):
            pane.timeline.set_invert_zoom_direction(checked)

    def _on_dual_pane_changed(self, checked_state):
        enabled = bool(checked_state)
        if enabled == self._dual_pane_enabled:
            return
        self._dual_pane_enabled = enabled
        # Both panes stay on screen in both modes (the right one lists the
        # duplicates in Duplicate Finder mode), so the window keeps its size.
        # The right pane's real snapshot is put away while it shows the
        # duplicates, and back when Compare folders returns.
        if not enabled:
            self._dup_stashed_right = (self.right.snapshot_root, self.right.snapshot_index)
        else:
            stashed = getattr(self, "_dup_stashed_right", None)
            if stashed is not None:
                self.right.snapshot_root, self.right.snapshot_index = stashed
            self._dup_stashed_right = None
            self._set_upper_status(self.right, "")

        state = self._capture_ui_state()
        # The left pane keeps its expanded folders and its selection across
        # the switch (e.g. to pick things in Duplicate Finder mode and then
        # copy them elsewhere in Compare folders mode).
        left_tree = self.left.tree if getattr(self.left, "tree", None) is not None else None
        kept_expanded = self._collect_expanded_paths(left_tree) if left_tree is not None else set()
        kept_selected = self._collect_selected_paths(left_tree) if left_tree is not None else set()
        current_tab = self.tabs.currentIndex() if getattr(self, "tabs", None) else 0
        self._build_ui()
        self._restore_ui_state(state)
        self.tabs.setCurrentIndex(current_tab)
        if kept_expanded or kept_selected:
            # Left pane only: with Synchronize Views the selection must not
            # be mirrored onto the right pane (whose own selection is gone).
            guard_was_set = self._sync_guard
            self._sync_guard = True
            try:
                self._restore_expanded_paths(self.left.tree, kept_expanded)
                if kept_selected:
                    self._select_paths_in_tree(self.left.tree, kept_selected, additive=False,
                                                auto_expand=False, select_subtree=False)
            finally:
                self._sync_guard = guard_was_set
            self.update_selection_count(self.left.tree, self.left)
        # Re-apply the "no dual pane -> no comparison coloring" fallback
        # (see _recolor_all_items) now that the just-rebuilt tree is
        # freshly repopulated -- _restore_ui_state's own navigate call
        # doesn't know about this override on its own.
        self._recolor_all_items()

    def _on_force_single_date_column_changed(self, checked):
        self._force_single_date_column = bool(checked)
        for pane in (self.left, self.right):
            self._update_pane_date_columns(pane)

    # ------------------------------------------------------- Transfer group

    def _numeric_span(self, value) -> str:
        """Rich-text <span> wrapping one numeric value for a Main-tab/
        File-List-View/Statistics-view title (e.g. the "1,234 (56.7 MB)"
        of "Total: 1,234 items (56.7 MB)" -- each figure separately -- or a "Files: X" count) --
        self._title_numeric_color/_title_numeric_bold (Settings tab ->
        Misc Colors -> "Title Numeric Colors"). The surrounding label
        text (the "Total:"/"items"/"Files:" part, parentheses included) is left with no color of
        its own so it always follows the widget's normal palette text
        color instead of a hardcoded, theme-blind hex -- see
        _build_directory_widgets/_build_statistics_panel, which used to
        set color: #F5F5F5 directly on these labels and looked fine on
        Dark but washed out on Light."""
        # Explicit either way: some of these titles (the Main tab's
        # "Total: ..." line) are already bold as a whole, so a span with
        # no weight of its own just inherits that -- which made the Bold
        # setting look like it did nothing there.
        weight = "font-weight:bold;" if self._title_numeric_bold else "font-weight:normal;"
        return f'<span style="color:{self._title_numeric_color};{weight}">{value}</span>'

    def _status_line_style(self, has_selection: bool) -> str:
        """Stylesheet for a pane's status-line summary label (see
        SelectionMixin.update_selection_count): the "Status line Selected
        info" color while something is selected, "Status line Unselected"
        otherwise (no color rule at all when that one is None -- the
        widget's own palette text color)."""
        color = self._status_selected_color if has_selection else self._status_unselected_color
        return f"color: {color}; padding: 2px 0;" if color else "padding: 2px 0;"

    def _apply_status_line_colors(self):
        """Re-applies the two status-line colors to both panes' summary
        labels right away -- called by the Settings tab's color swatches
        when either changes, and after Load Settings/Reload Defaults/a
        theme switch. Restyles only (the text itself may currently be an
        operation message rather than the selection summary, and isn't
        this method's to overwrite)."""
        for pane in (self.left, self.right):
            tree = getattr(pane, "tree", None)
            label = self.left_selection_label if pane is self.left else getattr(self, "right_selection_label", None)
            if tree is None or label is None:
                continue
            label.setStyleSheet(self._status_line_style(bool(tree.selectedItems())))

    def _default_button_colors(self) -> tuple[str, str]:
        """Neutral color for buttons that don't already have a
        meaningful color of their own (Expand/Collapse/Rename/Refresh/
        etc.) -- self._custom_default_button_colors (Settings tab ->
        Colors & Theme -> "Default Button Colors"), when set, overrides
        the automatic computation below entirely. Otherwise: computed as
        just a shade brighter than the window background itself, rather
        than a fixed light gray, so on a dark theme these buttons read
        as "a bit lighter than the surrounding GUI" instead of standing
        out starkly bright against it. Falls back to slightly *darker*
        than the background on a light theme, for the same reason in
        reverse."""
        if self._custom_default_button_colors is not None:
            return self._custom_default_button_colors
        bg = getattr(self, "_theme_window_color", None) or self.palette().color(QPalette.Window)
        if bg.lightness() < 128:
            default_bg = bg.lighter(130)
            default_fg = "#F5F5F5"
        else:
            default_bg = bg.darker(110)
            default_fg = "black"
        return default_bg.name(), default_fg

    def _stats_button_color_for(self, key: str) -> tuple[str, str]:
        """(bg, fg) for one of the four Statistics-view buttons (Files/
        Folders mode toggle, Copy--> /Move--> ) -- self._stats_button_
        colors[key], when set, overrides _default_button_colors()
        entirely, same "explicit override or fall back to the neutral
        default" pattern _default_button_colors itself uses for
        self._custom_default_button_colors."""
        custom = self._stats_button_colors.get(key)
        if custom is not None:
            return tuple(custom)
        return self._default_button_colors()

    def _fit_range_stylesheet(self, enabled: bool) -> str:
        """Stylesheet for a Statistics view's Fit Range button, in its
        own color (Statistics View -> Buttons -> "Fit Range"). It sits
        disabled for as long as Auto Range is on; the disabled look is
        _bevel_button_stylesheet's own :disabled rule (the same one the
        Hash button gets), so `enabled` no longer changes the sheet. The
        parameter stays so the existing call sites keep working."""
        bg, fg = self._stats_button_color_for("fit_range")
        return _bevel_button_stylesheet(bg, fg)

    def _apply_stats_button_colors(self):
        """Re-applies self._stats_button_colors to the live Statistics-
        view buttons on both panes (Files/Folders, Zoom to, Reset, Fit
        Range, Zoom in/out), without a full rebuild -- called by each
        color swatch's own change handler (see
        _on_stats_button_color_changed) and once after Load Settings.
        One shared color per button regardless of which pane -- same
        convention the rest of this app's button colors use."""
        for pane in (self.left, self.right):
            for key, attr in (
                ("files", "tl_files_mode_button"), ("folders", "tl_folders_mode_button"),
                ("zoom_to", "tl_zoom_sel_button"), ("reset", "tl_reset_button"),
                ("zoom_in", "tl_zoom_in_button"), ("zoom_out", "tl_zoom_out_button"),
            ):
                button = getattr(pane, attr, None)
                if button is not None:
                    button.setStyleSheet(_bevel_button_stylesheet(*self._stats_button_color_for(key)))
            fit_range = getattr(pane, "tl_readjust_vrange_button", None)
            if fit_range is not None:
                fit_range.setStyleSheet(self._fit_range_stylesheet(fit_range.isEnabled()))

    def _on_stats_button_color_changed(self, key: str, is_bg: bool, hex_color: str):
        bg, fg = self._stats_button_color_for(key)
        bg, fg = (hex_color, fg) if is_bg else (bg, hex_color)
        self._stats_button_colors[key] = [bg, fg]
        self._apply_stats_button_colors()

    def _main_button_color_for(self, key: str) -> tuple[str, str]:
        """(bg, fg) for one of Clipboard's Copy/Cut/Paste/Paste Folders/
        Copy -->/Move -->, File Op's Rename/New Folder/Delete, Refresh, or
        the File List View's Up one folder/Select Directory/Clear view/
        Expand/Collapse. An explicit self._main_button_colors[key] (list,
        not None) always wins; failing that, _DEFAULT_MAIN_BUTTON_COLORS
        [key]'s fixed design color for the ones that have one, or
        _default_button_colors() (the same theme-neutral fallback
        _stats_button_color_for uses) for the rest (see custom_widgets.
        _THEME_DEFAULT_MAIN_BUTTON_KEYS)."""
        custom = self._main_button_colors.get(key)
        if custom is not None:
            return tuple(custom)
        if key in _DEFAULT_MAIN_BUTTON_COLORS:
            return tuple(_DEFAULT_MAIN_BUTTON_COLORS[key])
        return self._default_button_colors()

    def _apply_main_button_colors(self):
        """Re-applies self._main_button_colors to every live button it
        colors -- Clipboard (incl. Copy -->/Move -->), File Op, the File
        List View's own buttons (both panes) and Global Options' Refresh
        -- button fg and, where that button has one, its icon's color too,
        without a full rebuild. Called by each color swatch's own change
        handler (see _on_main_button_color_changed) and once after Load
        Settings."""
        refresh = getattr(self, "refresh_button", None)
        if refresh is not None:
            refresh.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("refresh")))
        self._apply_mode_button_colors()
        for pane in (self.left, self.right):
            across = getattr(pane, "_across_buttons", {})
            buttons = {
                **pane._clipboard_buttons,
                "copy_across": across.get("copy"), "move_across": across.get("move"),
                "new_folder": pane.new_folder_button, "delete": pane.delete_button,
                "rename": pane.rename_button,
                "up": getattr(pane, "up_button", None),
                "select_dir": getattr(pane, "select_button", None),
                "hash": getattr(pane, "hash_button", None),
                "pane_refresh": getattr(pane, "pane_refresh_button", None),
                "save_snapshot": getattr(pane, "save_snapshot_button", None),
                "clear_view": getattr(pane, "clear_view_button", None),
                "expand_all": getattr(pane, "expand_all_button", None),
                "collapse_all": getattr(pane, "collapse_all_button", None),
            }
            for key, button in buttons.items():
                if button is None:
                    continue
                bg, fg = self._main_button_color_for(key)
                button.setStyleSheet(_bevel_button_stylesheet(bg, fg))
                icon_kind = _MAIN_BUTTON_ICON_KINDS.get(key)
                if icon_kind is not None:
                    button.setIcon(self._make_icon(icon_kind, fg))

    def _on_main_button_color_changed(self, key: str, is_bg: bool, hex_color: str):
        bg, fg = self._main_button_color_for(key)
        bg, fg = (hex_color, fg) if is_bg else (bg, hex_color)
        self._main_button_colors[key] = [bg, fg]
        self._apply_main_button_colors()

    def _uniform_button_size(self) -> tuple[int, int]:
        """(width, height) shared by essentially every button in the app
        -- sized to fit the longest label among the Select group's own
        buttons ("Diff Folders"/"Select All"/"Item Parent"), plus
        padding for the 3D bevel border and a bit of breathing room (one
        average character width extra, past the tightest fit, since
        "Diff Folders" was still clipping at the previous padding) --
        rather than stretching to fit far longer labels elsewhere
        ("Select Directory", "Make New Folder"), which made every button
        needlessly big. Computed once and cached, since it depends only
        on the app's font, not on anything that changes per pane/group."""
        if not hasattr(self, "_uniform_button_dims"):
            fm = QFontMetrics(self.font())
            longest = max(
                fm.horizontalAdvance(s) for s in ("Diff Folders", "Select All", "Item Parent", "All Parents")
            )
            width = longest + 14 + fm.averageCharWidth()
            height = fm.height() + 10
            self._uniform_button_dims = (width, height)
        return self._uniform_button_dims

    def _stats_across_button_size(self, kind: str = "copy") -> tuple[int, int]:
        """Fixed size for the Statistics view's Copy--> / Move--> buttons
        -- computed from each action's own actual labels (`kind` is
        "copy" or "move"), unlike _uniform_button_size's, which is sized
        for the Select group's much longer "Diff Folders"/"Select All"
        and left these two short arrow-labeled buttons sitting in a wide
        band of dead space on either side of their text. Takes the max
        width across both arrow directions for the given kind rather
        than each pane computing its own from just the label it happens
        to show, so the two panes' buttons for the same action stay the
        same width as each other regardless of which side's arrow points
        which way. Both kinds get the same one-extra-average-character
        pad on top of their own text width -- Ragnar found the plain
        text-hugging width read a touch cramped for both. Cached per
        kind, same way _uniform_button_size caches its own -- depends
        only on the app's font."""
        cache_attr = f"_stats_across_button_dims_{kind}"
        if not hasattr(self, cache_attr):
            fm = QFontMetrics(self.font())
            labels = ("Copy \u2192", "\u2190 Copy") if kind == "copy" else ("Move \u2192", "\u2190 Move")
            longest = max(fm.horizontalAdvance(s) for s in labels)
            width = longest + 8 + 2 * fm.averageCharWidth()
            _, height = self._uniform_button_size()
            setattr(self, cache_attr, (width, height))
        return getattr(self, cache_attr)

    def _refresh_button_size(self) -> tuple[int, int]:
        """Fixed size for the Refresh button -- its own actual label
        width plus a small fixed pad, rather than _uniform_button_size's
        (sized for the Select group's much longer "Diff Folders"/"Select
        All", which left Refresh sitting in a wide band of dead space on
        either side of its own short text). Same one-extra-average-
        character pad _stats_across_button_size now gives Copy/Move, on
        top of that base pad -- Ragnar found the first pass a touch too
        narrow. Cached the same way those do -- depends only on the
        app's font."""
        if not hasattr(self, "_refresh_button_dims"):
            fm = QFontMetrics(self.font())
            width = fm.horizontalAdvance("Refresh") + 14 + fm.averageCharWidth()
            _, height = self._uniform_button_size()
            self._refresh_button_dims = (width, height)
        return self._refresh_button_dims

    def _cancel_button_stylesheet(self) -> str:
        """Cancel buttons are red while active; while disabled they fade
        toward the window's own background color instead of staying a
        flat/dark red, for a proper "grayed out" look regardless of the
        active Qt palette/theme."""
        active = QColor("#C0392B")
        bg = getattr(self, "_theme_window_color", None) or self.palette().color(QPalette.Window)
        t = 0.65  # how far toward the background color to blend
        disabled = QColor(
            int(active.red() + (bg.red() - active.red()) * t),
            int(active.green() + (bg.green() - active.green()) * t),
            int(active.blue() + (bg.blue() - active.blue()) * t),
        )
        disabled_fg = bg.lighter(160) if bg.lightness() < 128 else bg.darker(160)
        pressed = _darken_hex("#C0392B")
        return (
            "QPushButton {"
            " background-color: #C0392B; color: #F5F5F5;"
            " border: 2px solid #C0392B; border-style: outset;"
            " border-radius: 3px; padding: 4px 8px;"
            "}"
            f"QPushButton:pressed {{"
            f" background-color: {pressed}; color: #F5F5F5;"
            f" border: 2px solid #C0392B; border-style: inset;"
            f" border-radius: 3px; padding: 4px 8px;"
            f"}}"
            f"QPushButton:disabled {{"
            f" background-color: {disabled.name()}; color: {disabled_fg.name()};"
            f" border: 2px solid {disabled.name()}; border-style: outset;"
            f" border-radius: 3px; padding: 4px 8px;"
            f"}}"
        )

    def _status_progress_bar_stylesheet(self) -> str:
        """Shared look for both status-line progress bars -- identical to
        the old single Transfer-group bar's style, just factored out
        since there are two of them now. Colors come from
        self._progress_bar_colors (Settings tab: foreground = chunk,
        background = track, busy = indeterminate-mode chunk) rather than
        being fixed, so a color change there just means re-applying this
        same stylesheet string to both bars again -- see
        _on_progress_bar_color_changed."""
        colors = self._progress_bar_colors
        return (
            "QProgressBar {"
            f" background-color: {colors['bg']}; border: 1px solid #555555;"
            " border-radius: 3px;"
            "}"
            "QProgressBar::chunk {"
            f" background-color: {colors['fg']}; border-radius: 2px;"
            "}"
            "QProgressBar[busy=\"true\"]::chunk {"
            f" background-color: {colors['busy']};"
            "}"
        )

    def _build_status_line(self) -> QWidget:
        """Bottom status line -- replaces the old floating "Status" group
        entirely. No group box anymore: this is a plain full-width area,
        added directly to main_layout at the very bottom of the window
        like the filter/global-options row above it -- unlike the old
        floating group, it needs no positioning upkeep of its own on
        resize/splitter-move.

        Two rows: the bars/Cancel row (rebuilt by _rebuild_status_bars_row
        below, whose exact contents depend on Dual Pane/Symmetrical GUI --
        see there) sits on top, with left_selection_label/
        right_selection_label -- doing double duty as both each side's
        live selection count AND that side's own operation status text
        ("Enumerating...", "Copying...", a live "N items" tick, etc, set
        by TransferMixin/SelectionMixin -- see _active_status_label) --
        on their own row underneath. There's no longer a single shared
        center label: each side's status is independent, matching that
        side's own progress bar/Cancel button, and a load/operation's
        result just sits in that label until the next thing overwrites
        it (a selection change, Clear, another operation on that side).

        Dual Pane on: left_progress_bar fills left-to-right, right_
        progress_bar fills right-to-left (via setInvertedAppearance) --
        driven by whichever pane's own operations are running. Each side
        gets its own Cancel button right next to its own bar rather than
        one shared button in the middle -- see _rebuild_status_bars_row
        for exactly where each one sits (which also depends on
        Symmetrical GUI). Dual Pane off: right_progress_bar/right_cancel_
        button are hidden and left_cancel_button sits flush at the row's
        right edge, same as it always has.
        """
        default_bg, default_fg = self._default_button_colors()
        uniform_w, uniform_h = self._uniform_button_size()
        cancel_w = uniform_w + QFontMetrics(self.font()).horizontalAdvance("M")

        self.left_progress_bar = QProgressBar()
        self.left_progress_bar.setTextVisible(False)
        self.left_progress_bar.setFixedHeight(20)
        self.left_progress_bar.setRange(0, 1)
        self.left_progress_bar.setValue(0)
        self.left_progress_bar.setStyleSheet(self._status_progress_bar_stylesheet())

        self.left_cancel_button = QPushButton("Cancel")
        self.left_cancel_button.setFixedSize(cancel_w, uniform_h)
        self.left_cancel_button.setStyleSheet(self._cancel_button_stylesheet())
        self.left_cancel_button.setToolTip("Stop the current operation")
        self.left_cancel_button.clicked.connect(self._cancel_transfer_progress)
        self.left_cancel_button.setEnabled(False)

        self.right_progress_bar = QProgressBar()
        self.right_progress_bar.setTextVisible(False)
        self.right_progress_bar.setFixedHeight(20)
        self.right_progress_bar.setRange(0, 1)
        self.right_progress_bar.setValue(0)
        self.right_progress_bar.setInvertedAppearance(True)
        self.right_progress_bar.setStyleSheet(self._status_progress_bar_stylesheet())

        self.right_cancel_button = QPushButton("Cancel")
        self.right_cancel_button.setFixedSize(cancel_w, uniform_h)
        self.right_cancel_button.setStyleSheet(self._cancel_button_stylesheet())
        self.right_cancel_button.setToolTip("Stop the current operation")
        self.right_cancel_button.clicked.connect(self._cancel_transfer_progress)
        self.right_cancel_button.setEnabled(False)

        # Left/right pane selection counts -- moved here from the
        # Select group's own title (see selection_ops.py, wherever it
        # currently formats that string) so the title itself can be
        # dropped in the narrow layout without losing the count. Now
        # also where that side's own operation status text lives (see
        # docstring above) -- left-aligned toward the left pane's own
        # edge, right-aligned toward the right pane's.
        self.left_selection_label = QLabel("")
        self.left_selection_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.left_selection_label.setStyleSheet("padding: 2px 0;")

        self.right_selection_label = QLabel("")
        self.right_selection_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.right_selection_label.setStyleSheet("padding: 2px 0;")

        # Both labels get one fixed height, whatever they show. An empty
        # QLabel asks for almost no height, and the rich-text layout
        # QLabel switches to for markup (colored/bold spans -- e.g. an
        # appended operation duration) asks for a pixel or two more than
        # plain text does. With the row free to follow its tallest label,
        # every time a pane's operation emptied its own label or flipped it
        # between plain and rich text, the row shrank or grew, and the main
        # layout handed those pixels to and from the splitter above: the
        # File List View and Statistics View visibly stuttered. Once the
        # *other* label had shown the tallest variant it held the row
        # open, which is why the stutter stopped after the first action
        # on the other pane (and, with Dual Pane off, never would have,
        # the right label being hidden). So: the tallest of plain text,
        # rich text, and one font line plus the "padding: 2px 0" every
        # stylesheet these labels get (see _status_line_style) -- measured
        # through sizeHint, which includes that stylesheet padding. Never
        # clips anything: neither label word-wraps.
        probe = self.left_selection_label
        heights = [QFontMetrics(probe.font()).height() + 4]
        for sample in ("Ag", '<span style="font-weight:bold;color:#E68C14;">Ag</span> Ag'):
            probe.setText(sample)
            heights.append(probe.sizeHint().height())
        probe.setText("")
        status_line_h = max(heights)
        self.left_selection_label.setFixedHeight(status_line_h)
        self.right_selection_label.setFixedHeight(status_line_h)

        self._status_bars_row = QWidget()
        QHBoxLayout(self._status_bars_row).setContentsMargins(0, 0, 0, 0)
        self._rebuild_status_bars_row()

        transfer_row = QHBoxLayout()
        transfer_row.setContentsMargins(0, 0, 0, 0)
        transfer_row.addWidget(self.left_selection_label, 1)
        transfer_row.addWidget(self.right_selection_label, 1)

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._status_line_layout = outer
        outer.addWidget(self._status_bars_row)
        outer.addLayout(transfer_row)
        return container

    def _rebuild_status_bars_row(self):
        """(Re)lays out left_progress_bar/left_cancel_button/
        right_cancel_button/right_progress_bar inside self._status_
        bars_row for the current Dual Pane/Symmetrical GUI state --
        called once from _build_status_line, which itself reruns on
        every Dual Pane/Symmetrical GUI change (both go through a full
        _build_ui -- see _on_dual_pane_changed/
        _on_symmetrical_buttons_changed), so this always reflects
        whichever state is current. Safe to call repeatedly, same
        detach-then-rebuild approach as _layout_pane_cluster: the row is
        cleared of whatever it currently holds (widgets just get
        detached, not deleted) and rebuilt fresh.

        Each Cancel button always sits right next to its own bar
        (left_cancel_button right after left_progress_bar, same as
        single-pane mode always looked) -- what changes is where right_
        cancel_button falls relative to right_progress_bar: with
        Symmetrical GUI on, it sits on the *inner* side (beside left_
        cancel_button in the middle, mirroring the left pane's own
        bar-then-Cancel order); with it off, right_cancel_button instead
        sits on the *outer* side (the row's far right edge), the same
        bar-then-Cancel order as the left side rather than a mirrored
        one."""
        row = self._status_bars_row.layout()
        while row.count():
            row.takeAt(0)

        row.addWidget(self.left_progress_bar, 1)
        row.addWidget(self.left_cancel_button)

        if not self._dual_pane_enabled:
            # Duplicate Finder: nothing runs on the right, so the left bar
            # takes the full width with Cancel at its right end.
            self.right_progress_bar.hide()
            self.right_cancel_button.hide()
        else:
            if self._symmetrical_buttons:
                row.addWidget(self.right_cancel_button)
                row.addWidget(self.right_progress_bar, 1)
            else:
                row.addWidget(self.right_progress_bar, 1)
                row.addWidget(self.right_cancel_button)
            self.right_progress_bar.show()
            self.right_cancel_button.show()

        # Same dual-pane-off treatment for the right selection count
        # underneath -- no right pane means nothing for it to report.
        self.right_selection_label.setVisible(True)

    def _build_filter_frame(self) -> QGroupBox:
        """File Filter group: just the filter controls themselves plus
        Invert (Invert applies to the filter, so it stays here rather
        than moving to Global Options with the other checkboxes) --
        row 1 is Clear/dropdown/entry; row 2 is Invert (left) and Apply
        (right), aligned directly below the text entry via a QGridLayout
        column."""
        frame = QGroupBox("Global File Filter")
        self.filter_frame_group = frame
        grid = QGridLayout(frame)

        default_bg, default_fg = self._default_button_colors()
        uniform_w, uniform_h = self._uniform_button_size()

        self.clear_filter_button = QPushButton("Clear")
        self.clear_filter_button.setFixedSize(uniform_w, uniform_h)
        self.clear_filter_button.setStyleSheet(_bevel_button_stylesheet(default_bg, default_fg))
        self.clear_filter_button.clicked.connect(self.clear_filter)
        grid.addWidget(self.clear_filter_button, 0, 0)

        self.common_types = sorted([
            "txt", "safetensors", "ckpt", "pt", "pdf", "json", "doc", "docx",
            "xls", "xlsx", "jpg", "jpeg", "png", "gif", "mp3", "mp4", "zip",
            "html", "css", "js", "py", "java", "cpp", "h",
        ])
        self.file_type_dropdown = QComboBox()
        self.file_type_dropdown.addItem("")
        self.file_type_dropdown.addItems(self.common_types)
        self.file_type_dropdown.currentIndexChanged.connect(self.add_file_type)
        grid.addWidget(self.file_type_dropdown, 0, 1)

        self.file_filter_entry = QLineEdit()
        # Not a fixed width: a minimum width (so it never collapses to
        # nothing) plus the column stretch below makes the entry the one
        # that grows/narrows with the window.
        self.file_filter_entry.setMinimumWidth(40)
        self.file_filter_entry.returnPressed.connect(self.apply_filter)
        grid.addWidget(self.file_filter_entry, 0, 2)
        grid.setColumnStretch(2, 1)

        self.apply_filter_button = QPushButton("Apply")
        self.apply_filter_button.setFixedSize(uniform_w, uniform_h)
        self.apply_filter_button.setStyleSheet(_bevel_button_stylesheet(default_bg, default_fg))
        self.apply_filter_button.clicked.connect(self.apply_filter)

        # Row 2, directly below the text entry: Invert on the left, Apply
        # right-aligned at the entry's right edge.
        self.invert_filter_cb = QCheckBox("Invert")
        self.invert_filter_cb.stateChanged.connect(self.apply_filter)
        invert_apply_row = QHBoxLayout()
        invert_apply_row.setContentsMargins(0, 0, 0, 0)
        invert_apply_row.addWidget(self.invert_filter_cb)
        invert_apply_row.addStretch(1)
        invert_apply_row.addWidget(self.apply_filter_button)
        grid.addLayout(invert_apply_row, 1, 2)

        self._sync_global_filter_enabled()
        return frame

    def _any_folder_set(self) -> bool:
        """True if at least one pane's directory entry holds an existing
        folder -- the same test update_listings uses for a pane's
        directory being valid."""
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            entry = getattr(pane, "entry", None)
            if entry is None:
                continue
            try:
                if self._isdir(entry.text()):
                    return True
            except RuntimeError:
                # The entry from before a UI rebuild, already deleted.
                continue
        return False

    def _sync_global_filter_enabled(self):
        """Global File Filter (the whole group: Clear, type dropdown,
        text, Apply, Invert) only works against loaded folders -- applying
        it before any is set crashes -- so it stays disabled until at
        least one pane has a folder, and is disabled again (and cleared,
        see clear_pane_view) when the last one goes."""
        frame = getattr(self, "filter_frame_group", None)
        if frame is None:
            return
        enabled = self._any_folder_set()
        try:
            if frame.isEnabled() != enabled:
                frame.setEnabled(enabled)
            frame.setToolTip("" if enabled else "Available once a folder is loaded.")
        except RuntimeError:
            pass  # the frame from before a UI rebuild, already deleted

    def _global_options_row_indents(self, row1: QHBoxLayout, row2: QHBoxLayout) -> tuple[int, int]:
        """Computed leading-space amounts for Global Options' two-row
        wide layout: (recursive_indent, expand_extra_indent).
        recursive_indent is how much blank space Row 1 needs before
        "Recursive Folder Selections" so it lands above "Expand To
        Selection" in Row 2 -- derived from Synchronize Views/
        Symmetrical GUI's own actual rendered widths (plus Row 2's own
        fixed 20px lead-in and both rows' real inter-widget spacing)
        rather than a guessed pixel constant, so it keeps tracking
        correctly if the app's font or DPI changes. expand_extra_indent
        is a small additional nudge applied to Row 2's own spacing right
        before Expand To Selection, so it reads as sitting a bit
        indented under Recursive Folder Selections rather than exactly
        flush with it. Both rows share the same left margin (the same
        outer QVBoxLayout), so this only produces a correct result if
        Row 1/Row 2 are assembled the same way both here and in
        _relayout_global_options_group's own wide branch -- both call
        this same method (passing their own not-yet-populated row1/row2)
        rather than each hand-computing/duplicating the spacing, so the
        two can't drift out of sync. Callable before row1/row2 have any
        widgets added -- only their own .spacing() is used, which
        reflects Qt's style default even when empty."""
        row1_spacing = row1.spacing() if row1.spacing() >= 0 else 6
        row2_spacing = row2.spacing() if row2.spacing() >= 0 else 6
        row2_leading = 20
        expand_extra_indent = 16
        sync_w = self.synchronize_views_cb.sizeHint().width()
        sym_w = self.symmetrical_buttons_cb.sizeHint().width()
        dual_w = self.dual_pane_cb.sizeHint().width()
        target_x = row2_leading + sync_w + row2_spacing + sym_w + row2_spacing
        recursive_indent = max(int(target_x - dual_w - row1_spacing), 8)
        return recursive_indent, expand_extra_indent

    def _build_global_options_group(self) -> QGroupBox:
        """Global Options group: a handful of quick-access toggles next
        to the File Filter, in two rows -- row 1 is options that either
        stand alone or decide another option's enabled state (Dual Pane,
        Recursive Folder Selections) plus the Refresh action; row 2,
        indented, is the options whose enabled state actually depends on
        one of those (Synchronize Views/Symmetrical GUI on Dual Pane,
        Expand To Selection on Recursive Folder Selections). Recursive
        Folder Selections itself gets pushed right (see
        _global_options_row_indents) to land above Expand To Selection
        rather than sitting flush after Dual Pane, which is why this
        group is noticeably wider than its four checkboxes' own text
        would otherwise need. Everything else that used to live in this
        row (List Directories/List Files/Sort Directories First, the two
        unnatural-scrolling/zoom checkboxes) still exists as real, live widgets --
        just parented here without being added to the visible row, so
        they stay valid primaries for _link_checkboxes/_link_spinboxes'
        Settings-tab copies (and for settings_ops.py's persistence) --
        the full set is reachable from the Settings tab's own Global
        Options section instead of cluttering this quick-access row."""
        # Duplicate Finder mode: no title, to leave the lists more height.
        group = QGroupBox("Quick Options" if self._dual_pane_enabled else "")
        self.global_options_group = group
        outer = QVBoxLayout(group)
        row1 = QHBoxLayout()
        row2 = QHBoxLayout()

        # Not shown on the Main tab any more (the mode switch buttons above
        # the panes replace it -- see _build_main_tab_top_row), but still the
        # real, live setting: the Settings tab's "Compare folders" checkbox
        # is linked to it, and so are the mode buttons.
        self.dual_pane_cb = QCheckBox("Compare folders", group)
        self.dual_pane_cb.setToolTip(
            "On: both panes shown side by side and compared, as normal. Off: Duplicate "
            "Finder mode -- only the left pane is shown, and files are no longer "
            "compared with the other pane. The window's width halves when you turn "
            "this off, and doubles back when you turn it on again."
        )
        self.dual_pane_cb.blockSignals(True)
        self.dual_pane_cb.setChecked(self._dual_pane_enabled)
        self.dual_pane_cb.blockSignals(False)
        self.dual_pane_cb.stateChanged.connect(self._on_dual_pane_changed)
        # Also disabled (via the whole group) while a listing runs -- see
        # TransferMixin._set_operation_controls_enabled -- and the app's
        # palette doesn't visibly grey a disabled checkbox on its own.
        self.dual_pane_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        self.dual_pane_cb.hide()

        # Recursive Folder Selections and Refresh aren't added to row1
        # yet -- both are constructed here (so their own signal wiring
        # happens in the usual place/order), but the actual
        # row1.addWidget calls for them are deferred to the end of this
        # method, once Synchronize Views/Symmetrical GUI exist for
        # _global_options_row_indents to measure against.
        self.recursive_folder_contents_cb = QCheckBox("Recursive Folder Selections")
        self.recursive_folder_contents_cb.setToolTip(
            "Compare folders -- and the Select group's searches and \"Total items\" "
            "breakdown -- by their actual contents, at any depth, even before you expand "
            "them. When off, an unexpanded folder is compared by its immediate item count "
            "and modified date only, and both searches and totals are scoped to whatever's "
            "currently visible/expanded on screen instead (faster on very large trees)."
        )
        self.recursive_folder_contents_cb.setChecked(True)
        self.recursive_folder_contents_cb.stateChanged.connect(self._on_recursive_folder_contents_changed)
        # Same reason as dual_pane_cb above.
        self.recursive_folder_contents_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-read both panes' listings from disk.")
        self.refresh_button.setFixedSize(*self._refresh_button_size())
        # Settings tab -> Global Settings -> Misc Colors -> "Refresh button"
        # (the theme's default button colors until one is picked).
        self.refresh_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("refresh")))
        self.refresh_button.clicked.connect(lambda checked=False: self._refresh_all())

        # A copy of the Settings tab's "Comparison type" drop-down (kept in
        # step both ways; algorithm and chunk sizes stay in the Settings
        # tab). Sits before Refresh.
        self.main_comparison_type_combo = QComboBox()
        for type_id, label in COMPARISON_TYPE_ITEMS:
            self.main_comparison_type_combo.addItem(label, type_id)
        self.main_comparison_type_combo.setCurrentIndex(
            max(0, self.main_comparison_type_combo.findData(getattr(self, "_cmp_type", 0))))
        self.main_comparison_type_combo.setFixedHeight(self.refresh_button.height())
        self.main_comparison_type_combo.setToolTip(
            "How a file is judged to match its counterpart in the other pane "
            "(same setting as Settings > Options > Comparison type, where the "
            "hash algorithm and fingerprint chunk sizes are also set)."
        )
        self.main_comparison_type_combo.activated.connect(self._on_main_comparison_type_activated)

        # Duplicate Finder mode (Compare folders off): Quick Options is laid
        # out differently (_quick_options_outer below). Synchronize
        # Views (disabled in this mode anyway) and the comparison type
        # drop-down (the Comparison Options copy below the right pane has
        # one) stay as live widgets, just not shown.
        one_line = not self._dual_pane_enabled

        row2.addSpacing(20)

        self.synchronize_views_cb = QCheckBox("Synchronize Views")
        self.synchronize_views_cb.setToolTip(
            "Keep the two panes in step with each other -- scrolling, selection, and "
            "expanded/collapsed folders -- and make \"Make New Folder\" create the new "
            "folder in both locations at once. Items with no counterpart on the other "
            "side are simply left alone. The two panes' own root folders are never "
            "affected by this. Meaningless (and disabled) with Compare folders off, since "
            "there's no second pane to stay in step with."
        )
        self.synchronize_views_cb.setEnabled(self._dual_pane_enabled)
        self.dual_pane_cb.toggled.connect(self.synchronize_views_cb.setEnabled)
        self.synchronize_views_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        if one_line:
            self.synchronize_views_cb.setParent(group)
            self.synchronize_views_cb.hide()
            self.main_comparison_type_combo.setParent(group)
            self.main_comparison_type_combo.hide()
        else:
            row2.addWidget(self.synchronize_views_cb)
        self.synchronize_views_cb.stateChanged.connect(self._on_synchronize_views_changed)
        # Keeps both panes' Statistics-view Metric combos in step the
        # moment Synchronize Views is switched on (copies left's Metric
        # onto the right) -- see statistics_ops.TimelineOpsMixin._on_
        # synchronize_views_toggled_for_metric. A second, independent
        # connection to the same signal rather than folding this into
        # _on_synchronize_views_changed itself (defined in sync_views.py)
        # so this Metric-specific behavior lives entirely in
        # statistics_ops.py, next to _propagate_metric_to_other_pane
        # (the ongoing "whoever changes it last wins" half of the same
        # feature, for after this initial sync).
        self.synchronize_views_cb.stateChanged.connect(self._on_synchronize_views_toggled_for_metric)

        self.symmetrical_buttons_cb = QCheckBox("Symmetrical GUI")
        self.symmetrical_buttons_cb.setToolTip(
            "When on, the right pane's Select/Clipboard groups and surrounding buttons "
            "mirror the left pane's around the vertical center, and the right pane's "
            "Statistics view title line mirrors too (title right-aligned, Files/Folders "
            "swapped). When off, the right pane uses the same left-to-right order as the "
            "left pane instead. Disabled with Compare folders off, since there's no right pane "
            "to mirror against."
        )
        self.symmetrical_buttons_cb.blockSignals(True)
        self.symmetrical_buttons_cb.setChecked(self._symmetrical_buttons)
        self.symmetrical_buttons_cb.blockSignals(False)
        # Applies to both panes in both modes (the right pane's buttons are
        # hidden in Duplicate Finder mode, but its layout still mirrors).
        self.symmetrical_buttons_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        self.symmetrical_buttons_cb.stateChanged.connect(self._on_symmetrical_buttons_changed)
        if not one_line:
            row2.addWidget(self.symmetrical_buttons_cb)

        self.expand_matching_cb = QCheckBox("Expand To Selection")
        self.expand_matching_cb.setToolTip(
            "When Missing/Smaller/Larger/Newer/Older/Diff Folders are clicked, also "
            "auto-expand folders (at any depth) to visually reveal their actual matches. "
            "When off, the same full search still runs and selects everything it finds -- "
            "it just leaves folders as they are rather than expanding them open. Requires "
            "Recursive Folder Selections."
        )
        self.expand_matching_cb.setChecked(True)
        self.expand_matching_cb.setEnabled(self.recursive_folder_contents_cb.isChecked())
        self.recursive_folder_contents_cb.toggled.connect(self.expand_matching_cb.setEnabled)
        self.expand_matching_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)

        if one_line:
            # Filled in once the Settings tab exists (it needs a copy of one
            # of its checkboxes) -- see ComparisonCopyMixin.
            self._quick_options_outer = outer
        else:
            # Recursive Folder Selections now sits close to Dual Pane (no longer
            # pushed over Expand To Selection, which is indented by its own
            # amount below it).
            _recursive_indent, expand_extra_indent = self._global_options_row_indents(row1, row2)
            row2.addSpacing(expand_extra_indent)
            row2.addWidget(self.expand_matching_cb)
            row2.addStretch(1)

            row1.addWidget(self.recursive_folder_contents_cb)
            row1.addStretch(1)
            row1.addWidget(self.main_comparison_type_combo)
            row1.addWidget(self.refresh_button)

            outer.addLayout(row1)
            outer.addLayout(row2)

        # ---- Not shown on the Main tab, but still real/live -- see
        # this method's docstring. ----
        self.list_directories_cb = QCheckBox("List Directories", group)
        self.list_directories_cb.setChecked(True)
        # Lambda, not a direct connect: stateChanged fires with the new
        # Qt::CheckState int (0/1/2), which a direct connect silently
        # bound to update_listings' own initiating_pane parameter --
        # every other checkbox in the app instead routes through its
        # own dedicated _on_..._changed(state) handler built to receive
        # that int, but these three had nothing of the sort and passed
        # it straight through as if it were a DirectoryPane. That int
        # then reached _start_transfer_progress(pane=...) before
        # update_listings' own try/finally even began, so whatever went
        # wrong in there happened outside the block that guarantees the
        # tree's visibility gets restored -- explaining why the tree
        # stayed gone afterward no matter what else was clicked.
        # rescan_disk=False on all three below -- same reasoning as
        # apply_filter/add_file_type: these change what's shown of a
        # pane's contents, not the contents themselves, so there's a
        # snapshot already in memory that's just as valid after
        # flipping one of these as it was before.
        # Deferred via QTimer.singleShot(0, ...) for the same reason
        # apply_filter's own call is -- see there. These three fire
        # update_listings() straight from their own checkbox click,
        # same hazard as Apply/Clear Filter/the file-type dropdown.
        self.list_directories_cb.stateChanged.connect(
            lambda _state: QTimer.singleShot(0, lambda: self.update_listings(rescan_disk=False))
        )
        self.list_directories_cb.hide()

        self.list_files_cb = QCheckBox("List Files", group)
        self.list_files_cb.setChecked(True)
        self.list_files_cb.stateChanged.connect(
            lambda _state: QTimer.singleShot(0, lambda: self.update_listings(rescan_disk=False))
        )
        self.list_files_cb.hide()

        self.sort_directories_first_cb = QCheckBox("Sort Directories First", group)
        self.sort_directories_first_cb.setChecked(True)
        self.sort_directories_first_cb.stateChanged.connect(
            lambda _state: QTimer.singleShot(0, lambda: self.update_listings(rescan_disk=False))
        )
        self.sort_directories_first_cb.hide()

        # Unnatural scrolling: reverses mouse-wheel scroll direction
        # app-wide (see custom_widgets._UnnaturalScrollFilter). Same
        # "real, live, but only shown via its Settings-tab copy" pattern
        # as List Directories/List Files/Sort Directories First above --
        # no natural place for it in the Main tab's quick-access row.
        self.unnatural_scrolling_cb = QCheckBox("Unnatural scrolling", group)
        self.unnatural_scrolling_cb.setToolTip(
            "Reverses the mouse wheel's scroll direction everywhere in the app -- "
            "file lists, the Settings tab, and anywhere else you can scroll with "
            "the wheel. Independent of \"Invert scrollwheel zoom direction\" next "
            "to it, which only affects zooming in Statistics View -- so the two "
            "can be combined to cancel out for zooming while scrolling stays "
            "reversed, or vice versa."
        )
        self.unnatural_scrolling_cb.blockSignals(True)
        self.unnatural_scrolling_cb.setChecked(is_unnatural_scrolling_enabled())
        self.unnatural_scrolling_cb.blockSignals(False)
        self.unnatural_scrolling_cb.stateChanged.connect(self._on_unnatural_scrolling_changed)
        self.unnatural_scrolling_cb.hide()

        # Invert scrollwheel zoom direction: a Global Option (not
        # per-theme -- moved here from a Statistics View common control
        # at Ragnar's request) so it sits alongside Unnatural scrolling,
        # which it's meant to be combinable/independent with (see that
        # tooltip). Pushed onto both panes' TimelineWidgets by
        # _on_invert_statistics_zoom_changed below, same as any other
        # Statistics View setting that isn't captured on the widget
        # itself -- see _capture_ui_state/_restore_ui_state for how it
        # survives a rebuild.
        self.invert_statistics_zoom_cb = QCheckBox("Invert scrollwheel zoom direction", group)
        self.invert_statistics_zoom_cb.setToolTip(
            "Reverses which way the mouse wheel zooms the Statistics view: scroll "
            "up to zoom out and down to zoom in, instead of the default. Only "
            "affects zooming there -- see \"Unnatural scrolling\" for scrolling "
            "lists and everything else."
        )
        self.invert_statistics_zoom_cb.stateChanged.connect(self._on_invert_statistics_zoom_changed)
        self.invert_statistics_zoom_cb.hide()

        # Save/Load Settings moved to the Settings tab (bottom, same
        # height as Cancel on the Main tab's status line) -- see
        # _build_settings_tab. Everything they need to save/load now
        # lives in that tab anyway (colors/theme, the common Statistics
        # View settings, the synced copies of these very checkboxes),
        # so that's a more natural home than Global Options.

        # Now that recursive_folder_contents_cb and expand_matching_cb both
        # exist, wire up the enable/disable relationship between them (the
        # latter only means anything while the former is on).
        self.expand_matching_cb.setEnabled(self.recursive_folder_contents_cb.isChecked())

        return group

    def clear_filter(self):
        """Clears the filter text and turns off Invert, then applies
        that. invert_filter_cb.stateChanged is connected straight to
        apply_filter (see _build_filter_frame), so setChecked(False)
        below already fires a full apply_filter() pass on its own
        whenever Invert was actually on -- blockSignals around it avoids
        scheduling that pass twice back to back (once from the signal,
        once from the explicit call just below). Both are deferred (see
        apply_filter's own docstring), so "twice" here would otherwise
        mean two separate queued update_listings() calls landing back
        to back on the next event-loop turn -- redundant, not unsafe by
        itself now that neither runs reentrant inside this click
        anymore, but still pointless work worth skipping."""
        self.file_filter_entry.clear()
        self.invert_filter_cb.blockSignals(True)
        self.invert_filter_cb.setChecked(False)
        self.invert_filter_cb.blockSignals(False)
        self.apply_filter()

    def apply_filter(self):
        # Deferred via QTimer.singleShot(0, ...) rather than called
        # directly: apply_filter is reached synchronously from real
        # button/checkbox click and combobox-selection events (Apply,
        # Clear Filter, Invert, the file-type dropdown, List Directories/
        # Files, Sort Directories First), and update_listings tears down
        # and rebuilds the tree -- including whatever item Qt's own
        # internal event-handling machinery still considers "current"
        # at that exact moment, several native frames up the same call
        # stack (QAbstractButton::clicked/QComboBox's own popup-close
        # handling, both themselves invoked from real
        # QApplicationPrivate::sendMouseEvent delivery). Rebuilding the
        # tree reentrant, still inside that delivery, was confirmed (via
        # a matching, independently-reported PySide/Qt crash with the
        # identical shape: QMetaObject::activate -> PySide::
        # SignalManager::callPythonMetaMethod -> a QTreeWidget rebuild,
        # landing several frames deeper inside Qt's own C++ -- not a
        # deep-recursion/stack-depth crash, a shallow one) to corrupt
        # something in that still-in-flight event handling and segfault,
        # regardless of how carefully the rebuild itself detaches/
        # reorders things beforehand -- the same class of hazard this
        # codebase already defers around elsewhere (see dnd_widgets.
        # DirDropTreeWidget.startDrag, and _schedule_selection_markers_
        # update). Scheduling this for the next event-loop turn instead
        # lets the click/selection event finish unwinding through Qt's
        # own machinery first, so the rebuild runs on a fresh stack with
        # nothing above it still expecting the old tree/items to exist.
        # rescan_disk=False: the filter text/Invert changing doesn't mean
        # either pane's on-disk contents did -- see update_listings'
        # own docstring. Reuses whatever snapshot is already in memory
        # from the last real disk read (initial open, Refresh, or a
        # transfer's post-op refresh) and just re-filters/re-diffs the
        # visible tree against it.
        QTimer.singleShot(0, lambda: self.update_listings(rescan_disk=False))

    def add_file_type(self, index):
        selected_type = self.file_type_dropdown.currentText()
        if not selected_type:
            return
        current = self.file_filter_entry.text().strip()
        if current:
            self.file_filter_entry.setText(f"{current}, {selected_type}")
        else:
            self.file_filter_entry.setText(selected_type)
        self.file_type_dropdown.blockSignals(True)
        self.file_type_dropdown.setCurrentIndex(0)
        self.file_type_dropdown.blockSignals(False)
        # Deferred for the same reason apply_filter's own call is -- see
        # there. This one's reached from the file-type dropdown's own
        # currentIndexChanged, itself fired while Qt's combobox popup-
        # close/mouse-release handling for that same click is still
        # unwinding.
        QTimer.singleShot(0, lambda: self.update_listings(rescan_disk=False))

    # ------------------------------------------------------------ Listings

    def clear_pane_view(self, pane: DirectoryPane):
        """"Clear view": empty this pane's directory entry, file list, and
        embedded timeline entirely -- the same blank state a pane starts
        in before any folder's ever been opened. The other pane's own
        folder stays put; only its diff coloring resets to plain, the
        same as it would if this pane's directory had simply gone
        invalid (see update_listings).

        update_listings() alone only clears a pane's *tree* when its
        directory is actually valid (see update_listing's own
        tree.clear()) -- an invalid/empty directory on its own would
        leave the tree sitting on stale, now-orphaned items, so that
        part's done explicitly here. The snapshot/timeline reset
        update_listings() triggers via _refresh_pane_snapshot is enough
        on its own now that it unconditionally refreshes the timeline
        too."""
        self._unload_snapshot(pane)
        pane.entry.setText("")
        if not self._any_folder_set():
            # That was the last folder: the Global File Filter goes with
            # it (text and Invert, same as its Clear button -- just
            # without its own apply_filter pass, since update_listings
            # right below re-lists anyway).
            self.file_filter_entry.clear()
            self.invert_filter_cb.blockSignals(True)
            self.invert_filter_cb.setChecked(False)
            self.invert_filter_cb.blockSignals(False)
        pane.tree.clear()
        self.update_listings(initiating_pane=pane)

    def select_directory(self, pane: DirectoryPane):
        start_dir = pane.entry.text()
        if is_snapshot_file_name(start_dir) or self._is_snapshot_path(start_dir):
            start_dir = os.path.dirname(getattr(pane, "snapshot_file", None) or start_dir)
        if not os.path.isdir(start_dir):
            start_dir = ""
        # A folder, or a folder snapshot file (.fss) -- see
        # SnapshotOpsMixin._choose_directory_or_snapshot.
        directory = self._choose_directory_or_snapshot(start_dir)
        if directory:
            self._navigate_pane(pane, directory)
