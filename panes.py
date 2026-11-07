"""
DirectoryPane: bundles the widgets and per-pane state that make up one
side (left or right) of the app. Built by pane_builder.py (through
UiBuilderMixin, ui_builder.py); read/written by every other mixin.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QGroupBox

from dnd_widgets import DirDropComboBox, DirDropTreeWidget


class DirectoryPane:
    """Bundles the widgets that make up one side (left or right) of the app."""

    def __init__(self):
        self.entry: DirDropComboBox | None = None
        self.tree: DirDropTreeWidget | None = None
        self.count_label: QLabel | None = None
        # This pane's most recently loaded directory snapshot -- set
        # together by listing_ops.py once a root folder is actually
        # loaded; None here (rather than the attribute simply not
        # existing yet) lets TimelineOpsMixin (statistics_ops.py) treat
        # "no root folder loaded yet" as a normal, checkable state (see
        # e.g. _pane_timeline_records) instead of hitting an
        # AttributeError the first time something -- like switching the
        # Files/Folders toggle -- touches it before any folder's been
        # opened.
        self.snapshot_root = None
        self.snapshot_index = None
        # This pane's folder-open history -- most-recent-first list of
        # normalized paths, backing entry's dropdown (see DirDropComboBox.
        # set_history and UiBuilderMixin._record_folder_history). Plain
        # pane-level state like snapshot_root above, so it survives a
        # cosmetic _rebuild_ui (Settings tab -> Global Options -> "Nr. of
        # history entries in File List View" only trims/persists it, it
        # doesn't own it); persisted to Settings explicitly, since it
        # isn't a single-value widget _settings_widgets() can handle
        # generically -- see settings_ops.save_settings/load_settings.
        self.folder_history: list = []
        self.select_group: QGroupBox | None = None
        # File Op / Clipboard groups, tracked the same way select_group
        # already was -- needed now so UiBuilderMixin._layout_pane_cluster
        # (responsive_layout.py) can reflow all three between the wide
        # and narrow (<1930px) arrangements after construction, not just
        # place them once. See constants.py's DUAL_PANE_NARROW_THRESHOLD.
        self.file_op_group: QGroupBox | None = None
        self.clipboard_group: QGroupBox | None = None
        # This pane's own copy of the Invert button -- tracked so a
        # color change to _INVERT_BUTTON_COLORS (Settings tab -> Colors
        # & Theme) can repaint both panes' copies without a full rebuild.
        self.invert_button = None  # InvertButton | None
        # The QWidget (VBoxLayout) that holds the Select/File Op/
        # Clipboard row(s) -- _layout_pane_cluster tears down and
        # rebuilds its rows in place on every narrow/wide crossing,
        # rather than rebuilding the whole pane.
        self.cluster_container = None  # QWidget | None
        # The six Clipboard-group buttons themselves (Copy/Cut/Paste/
        # Paste Folders/Move -->/Copy -->), keyed by name -- built once,
        # then re-laid-out (grid vs. single row) by
        # UiBuilderMixin._layout_clipboard_group (responsive_layout.py)
        # on every narrow/wide crossing, same as cluster_container's own
        # rows are.
        self._clipboard_buttons: dict = {}
        self.free_space_label: QLabel | None = None

        # File List view / Statistics view split -- the draggable
        # QSplitter built in UiBuilderMixin._build_directory_widgets
        # (pane_builder.py), holding this pane's file-list widget
        # (entry/toolbar/tree/count row) above and its statistics widget
        # (title row + timeline panel) below. Its two sizes are kept in
        # sync with the *other* pane's splitter and with the Settings
        # tab's file_list_ratio_spin via
        # UiBuilderMixin._set_file_list_ratio (pane_builder.py), all
        # driven off the one shared self._file_list_ratio.
        self.file_stats_splitter = None  # QSplitter | None

        # This pane's embedded Timeline Explorer -- see
        # UiBuilderMixin._build_statistics_panel (pane_builder.py,
        # construction) and statistics_ops.TimelineOpsMixin (data feed +
        # selection sync).
        # timeline itself is the plotting widget; the rest are the
        # controls built around it, kept on the pane the same way every
        # other per-pane widget is (count_label, select_group, ...) so
        # any mixin can reach them without a fresh lookup.
        self.timeline = None  # TimelineWidget | None
        self.tl_scrollbar = None  # QScrollBar | None
        self.tl_metric_combo = None  # QComboBox | None
        self.tl_bin_count_spin = None  # QSpinBox | None
        self.tl_nice_bins_cb = None  # QCheckBox | None
        self.tl_log_scale_cb = None  # QCheckBox | None
        self.tl_log_size_axis_cb = None  # QCheckBox | None -- SizeAxis.log_mode
        self.tl_auto_vrange_cb = None  # QCheckBox | None
        self.tl_readjust_vrange_button = None  # QPushButton | None
        self.tl_bin_size_label = None  # QLabel | None
        self.tl_totals_label = None  # QLabel | None -- "Total: Files: X - Folders: Y"
        self.tl_font_size_spin = None  # QSpinBox | None -- timeline's own text size
        self.tl_tick_angle_spin = None  # QSpinBox | None -- x-axis tick label rotation
        self.tl_reset_button = None  # QPushButton | None
        self.tl_zoom_in_button = None  # QPushButton | None
        self.tl_zoom_out_button = None  # QPushButton | None
        self.tl_zoom_sel_button = None  # QPushButton | None
        # (tl_clear_sel_button removed -- Select group's own "Clear"
        # already does this job; see pane_builder._build_statistics_panel.)
        # Statistics view mode: False (default) plots files, True plots
        # folders (each folder's own recursive total size, excluding the
        # pane's own root folder -- see TimelineOpsMixin._flatten_folders).
        # Set by the Files/Folders toggle next to the view's title -- see
        # UiBuilderMixin._build_statistics_panel (pane_builder.py).
        self.tl_folder_mode: bool = False
        # Independent per-mode Statistics-view drag-range overlay, so
        # switching the Files/Folders toggle doesn't leave one mode's
        # overlay stuck showing over the other mode's (different, or
        # nonexistent) files/folders -- see
        # TimelineOpsMixin._on_timeline_mode_changed. Keyed "file"/
        # "folder"; each value is {"range": tuple|None} or None (never
        # visited that mode yet, same as one with no range set). Holds
        # only the range now, not a discrete-selection snapshot -- the
        # discrete file/folder selection is read live off the tree
        # itself wherever it's needed (see
        # TimelineOpsMixin._combined_mode_selection_paths), since the
        # tree already persists both modes' selections at once and
        # doesn't need a second, separately-tracked copy that could
        # drift out of step with it.
        self.tl_mode_selections: dict = {"file": None, "folder": None}
        # Reentrancy guard for the scrollbar<->timeline view sync, same
        # idea as _sync_guard elsewhere -- see
        # TimelineOpsMixin._on_timeline_view_range_changed.
        self._tl_syncing_scrollbar = False
        # Cached item count for this pane's current root, used as the
        # Select group's progress bar total: with Recursive Folder
        # Contents off it's just this directory's own listing size; with
        # it on, it's a full recursive count -- see _recount_pane_items.
        self.item_count: int = 0
        # With "Synchronize Views" on, double-click/"up one folder"
        # normally treat the two panes' *current* roots as corresponding
        # to each other (per-spec, they're the one thing sync doesn't try
        # to keep equal). But if a navigation ever has to leave the other
        # pane behind (its counterpart doesn't exist there), the two live
        # roots stop actually corresponding -- so this remembers the root
        # this pane had at the moment that happened, to keep computing
        # counterparts correctly relative to that fixed point instead of
        # the (no longer meaningful) live roots. None means "in sync,
        # trust the live roots" -- see _sync_reference_roots.
        self.sync_anchor: str | None = None
