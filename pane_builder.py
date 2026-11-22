"""
PaneBuilderMixin: builds one pane's widgets -- the toolbar/tree/item-count
row/Select+Clipboard groups (_build_directory_widgets), its Statistics
view panel (_build_statistics_panel) and the File List/Statistics
splitter handling (_apply_file_list_ratio_to_splitter, _set_file_list_ratio,
...).

Split out of ui_builder.py unchanged -- same methods, still reached as
`self._...` because UiBuilderMixin inherits this class.
"""

from PySide6.QtCore import QPointF, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from constants import CHANGED_FIELD_LABEL, TIMELINE_SCROLLBAR_RANGE, _CLUSTER_GROUP_PAD, _CLUSTER_V_GAP, _PANE_SIDE_MARGIN, _DISABLED_CHECKBOX_STYLE
from custom_widgets import (
    ElidedLabel, InvertButton, ResizeNotifyWidget, _MAIN_BUTTON_ICON_KINDS, _SelectionColorDelegate, _SelectionMarkerScrollBar,
    _ThemedScrollBar, _bevel_button_stylesheet,
)
from dnd_widgets import DirDropComboBox, DirDropTreeWidget
from panes import DirectoryPane
from statistics_widget import TimelineWidget


class PaneBuilderMixin:


    def _make_icon(self, kind: str, color: str) -> QIcon:
        """Small hand-drawn icon (no theme/font dependency, so it looks the
        same on Linux and Windows) for the clipboard buttons."""
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        if kind == "copy":
            painter.drawRoundedRect(QRectF(2, 4, 9, 10), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(5, 1, 9, 10), 1.5, 1.5)
        elif kind == "cut":
            painter.drawLine(QPointF(5.5, 10.5), QPointF(14, 2))
            painter.drawLine(QPointF(10.5, 10.5), QPointF(2, 2))
            painter.drawEllipse(QPointF(4, 12), 2, 2)
            painter.drawEllipse(QPointF(12, 12), 2, 2)
        elif kind == "paste":
            painter.drawRoundedRect(QRectF(3, 3, 10, 12), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(6, 1, 4, 3), 1, 1)
            painter.drawLine(QPointF(5, 8), QPointF(11, 8))
            painter.drawLine(QPointF(5, 11), QPointF(11, 11))
        elif kind == "delete":
            painter.drawLine(QPointF(3, 4), QPointF(13, 4))
            painter.drawLine(QPointF(6, 2), QPointF(10, 2))
            painter.drawRoundedRect(QRectF(4, 4, 8, 10), 1, 1)
            painter.drawLine(QPointF(6, 7), QPointF(6, 11))
            painter.drawLine(QPointF(8, 7), QPointF(8, 11))
            painter.drawLine(QPointF(10, 7), QPointF(10, 11))

        painter.end()
        return QIcon(pixmap)

    def _build_directory_widgets(self, pane: DirectoryPane) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        margins = layout.contentsMargins()
        layout.setContentsMargins(_PANE_SIDE_MARGIN, margins.top(), _PANE_SIDE_MARGIN, margins.bottom())

        # File List view (entry field, toolbar, tree, count row) and
        # Statistics view (title row + _build_statistics_panel) each get
        # their own sub-layout/widget now, rather than being stacked
        # directly into `layout` -- see the QSplitter built near the end
        # of this method, which is what actually adds these two widgets
        # to `layout`. The Select/File Op/Clipboard cluster is not part of
        # this widget: it is built here but placed in the row under both
        # panes (UiBuilderMixin._build_cluster_row).
        file_list_layout = QVBoxLayout()
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout = QVBoxLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)

        entry = DirDropComboBox()
        entry.directoryDropped.connect(lambda path, p=pane: self._navigate_pane(p, path))
        # A dropdown pick is validated (the folder may have been deleted
        # since) before it's treated as a real navigation -- see
        # SyncMixin._on_history_entry_activated.
        entry.historyEntryActivated.connect(lambda path, p=pane: self._on_history_entry_activated(p, path))
        # DEL on a dropdown entry removes it from the history.
        entry.historyEntryDeleted.connect(lambda path, p=pane: self._on_history_entry_deleted(p, path))
        # Where a loaded snapshot came from (original path, root name); hidden
        # for live folders -- see SnapshotOpsMixin._update_snapshot_label.
        # Now a permanent upper status line (filled below, once the Hash /
        # Rehash buttons exist): snapshot origin while a snapshot is loaded,
        # else hash cache read/write messages.
        snapshot_info_label = ElidedLabel("")
        pane.snapshot_info_label = snapshot_info_label
        self._update_snapshot_label(pane)
        # The status line itself is not part of this pane's layout: it
        # sits in the Main tab's shared top row, either side of the
        # Duplicate finder / Swap pane / Compare Folders buttons (see
        # UiBuilderMixin._build_main_tab_top_row).
        file_list_layout.addWidget(entry)
        pane.entry = entry
        # Repopulate the freshly built combo's dropdown from this pane's
        # already-persisted history -- pane.folder_history itself is
        # plain pane-level state that survives a _rebuild_ui, but each
        # rebuild hands this widget a brand new, empty-dropdown instance.
        entry.set_history_colors(self._history_live_color, self._history_snapshot_color)
        entry.set_history(pane.folder_history)
        # Global File Filter is only available once at least one pane
        # has a folder set -- see _sync_global_filter_enabled.
        entry.textChanged.connect(lambda _text: self._sync_global_filter_enabled())

        # Built before the toolbar so Expand/Collapse All can reference it;
        # actually added to `layout` further down, after the toolbar row.
        tree = DirDropTreeWidget()
        tree.setColumnCount(7)
        tree.setHeaderLabels([
            "Name", "Size", "Type", "Modified Date", CHANGED_FIELD_LABEL, "Accessed Date", "Count",
        ])
        # "Count" is for the Duplicate Finder's "Unique files only" list
        # (see DuplicateFinderMixin._dup_style_right_pane); hidden otherwise.
        tree.setColumnHidden(6, True)
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tree.setRootIsDecorated(True)
        tree.setSortingEnabled(False)
        # Indentation guide-line coloring/hover-highlight, line style and
        # tick marks (Settings tab -> Indentation Line) -- see
        # DirDropTreeWidget.drawBranches, which re-reads this on every
        # paint rather than
        # once here, same reasoning as _SelectionColorDelegate's own
        # get_selection_tint just below.
        tree.indent_color_provider = lambda: (
            self._indent_lines_enabled, self._indent_line_thickness,
            self._indent_line_steps, self._indent_line_color1, self._indent_line_color2,
            self._indent_line_reverse, self._indent_hover_color, self._indent_hover_opacity,
            self._indent_line_style, *self._indent_tick_settings(),
        )
        # Settings tab -> File List View -> Indentation. Re-applied here on
        # every build (not just when the spin box changes) since a full
        # _rebuild_ui creates brand new trees. DirDropTreeWidget reads
        # self.indentation() for its guide lines/hit-testing on every
        # paint/click, so they follow this without any extra wiring.
        tree.setIndentation(self._tree_indentation)
        tree.header().setSectionResizeMode(QHeaderView.Interactive)
        tree.header().setStretchLastSection(False)
        tree.setColumnWidth(1, 90)
        tree.setColumnWidth(2, 70)
        tree.setColumnWidth(3, 130)
        tree.setColumnWidth(4, 130)
        tree.setColumnWidth(5, 130)
        tree.setColumnWidth(6, 70)
        # A strong, deliberately un-theme-like default for selection
        # (see self._selection_tint_color), kept even when the tree
        # isn't focused (Qt would otherwise dim it to the "inactive"
        # highlight color) -- selected rows should stand out clearly
        # from the diff-tag colors. This is the flat fallback: the
        # actual selected-row fill normally comes from
        # _SelectionColorDelegate instead (per-row, tinted toward this
        # same color -- see "Selection opacity" in File
        # List View settings), which also keeps files (black) and
        # folders (blue) distinguishable while selected, instead of
        # both flattening to one text color.
        tree_palette = tree.palette()
        selection_color = QColor(self._selection_tint_color)
        tree_palette.setColor(QPalette.Active, QPalette.Highlight, selection_color)
        tree_palette.setColor(QPalette.Inactive, QPalette.Highlight, selection_color)
        # The list's own background (Settings tab -> File List View ->
        # File List View Colors -> Default Background); the text fields
        # etc. keep following the app palette's Base.
        tree_palette.setColor(QPalette.Base, QColor(self._effective_flv_bg()))
        tree.setPalette(tree_palette)
        tree.setItemDelegate(_SelectionColorDelegate(tree, get_selection_tint=lambda: (
            self._selection_tint_color, self._selection_tint_opacity,
        )))
        # Selection markers along the scrollbar (see
        # _SelectionMarkerScrollBar) -- recomputed on selection change and
        # on every expand/collapse, since either can shift a marker's
        # relative position or (via lazy population) change the total row
        # count the fractions are computed against.
        tree.setVerticalScrollBar(_SelectionMarkerScrollBar(tree))
        tree.itemSelectionChanged.connect(lambda p=pane: self._schedule_selection_markers_update(p))
        tree.itemExpanded.connect(lambda item, p=pane: self._schedule_selection_markers_update(p))
        tree.itemCollapsed.connect(lambda item, p=pane: self._schedule_selection_markers_update(p))
        tree.header().setSectionsClickable(True)
        tree.header().setSortIndicatorShown(True)
        tree.header().sectionClicked.connect(lambda col, t=tree: self.sort_tree_by_column(t, col))
        # Keep the tree's layout cell reserved at its normal size even
        # while temporarily hidden -- see _select_paths_in_tree and
        # update_listing, both of which do tree.setVisible(False) for
        # the duration of a large bulk operation as a real performance
        # win (QAbstractItemView skips its own per-row bookkeeping while
        # invisible -- see their comments). Without this, the outer
        # QVBoxLayout treats a hidden tree as contributing nothing, so
        # its stretch=1 share of the pane's height (see below) gets
        # handed to whatever else in that layout can still grow -- in
        # practice the Select/File Op/Clipboard groups just under it,
        # whose QGridLayout/QVBoxLayout distribute that windfall as
        # extra vertical gaps between their (fixed-size) buttons. That's
        # what was making the button groups visibly grow -- differently
        # per pane, since each side's operation runs (and its tree
        # sits hidden) for a different, item-count-dependent stretch of
        # time. retainSizeWhenHidden only affects layout bookkeeping,
        # not isVisible(), so the actual perf win is untouched.
        tree_size_policy = tree.sizePolicy()
        tree_size_policy.setRetainSizeWhenHidden(True)
        tree.setSizePolicy(tree_size_policy)
        # Dropping a folder directly onto the tree navigates it, same as
        # the entry field's own drop handling. But unlike the entry
        # field, the tree stays enabled during an active progress cycle
        # (selection needs to keep working -- see
        # TransferMixin._set_operation_controls_enabled), so this needs
        # its own explicit guard against navigating mid-scan instead of
        # relying on being disabled.
        tree.directoryDropped.connect(
            lambda path, p=pane: self._navigate_pane(p, path)
            if getattr(self, "active_transfer_progress", None) is None else None
        )
        tree.itemDoubleClicked.connect(lambda item, col, p=pane: self._on_item_double_clicked(item, p))
        # Right-click menu (see context_menu_ops.ContextMenuMixin).
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(
            lambda pos, p=pane: self._show_flv_context_menu(p, pos)
        )
        tree.itemExpanded.connect(lambda item, t=tree, p=pane: self._on_item_expanded(item, t, p))
        tree.itemCollapsed.connect(lambda item, p=pane: self._on_item_collapsed(item, p))
        # Timeline's non-recursive view depends on what's actually
        # expanded (see TimelineOpsMixin._pane_timeline_records) -- keep
        # it live as folders open/close. No-op while Recursive Folder
        # Selections is on.
        tree.itemExpanded.connect(lambda item, p=pane: self._on_tree_expand_collapse_for_timeline(p))
        tree.itemCollapsed.connect(lambda item, p=pane: self._on_tree_expand_collapse_for_timeline(p))
        # "Synchronize Views" bookkeeping: mirror expand/collapse and
        # scrolling onto the other pane (no-ops while it's off).
        tree.itemExpanded.connect(lambda item, t=tree: self._on_item_expanded_sync(item, t))
        tree.itemCollapsed.connect(lambda item, t=tree: self._on_item_collapsed_sync(item, t))
        tree.verticalScrollBar().valueChanged.connect(lambda v, t=tree: self._on_scroll_sync(v, t))
        # Refresh both listings once a drag started from this tree
        # finishes -- e.g. dragging a file out to an external file
        # manager as a Move should stop showing it here. Deferred via
        # QTimer.singleShot(0, ...) rather than calling update_listings()
        # directly: after_drag is invoked from DirDropTreeWidget.startDrag
        # (see dnd_widgets.py) as literally its last line, right after
        # drag.exec() returns -- which means it's still running *inside*
        # startDrag() itself, a virtual method Qt's own mouse-press/drag-
        # detection machinery called on this exact tree and hasn't
        # finished unwinding from yet. update_listings() ends up all the
        # way down in update_listing()'s tree.clear() -- destroying every
        # item and replacing them with freshly rebuilt ones -- while
        # Qt's still mid-startDrag() internal bookkeeping for the very
        # same view, which likely still holds onto raw indexes/pointers
        # for whatever was just dragged. That's exactly the "touching a
        # live object that's mid-mutation on the C++ side" hazard every
        # other blockSignals/_tree_rebuild_pending guard in this codebase
        # exists to avoid (see listing_ops.update_listing's own comments)
        # -- just reached from a different direction: not a *signal*
        # firing reentrant, but this callback itself running synchronously
        # inside a Qt virtual method instead of after it. Queuing the
        # refresh for the next event-loop turn lets startDrag() -- and
        # whatever called it -- finish and unwind first, the same
        # "defer past the current call stack" fix
        # _schedule_selection_markers_update already relies on for a
        # near-identical reason.
        tree.after_drag = lambda action: QTimer.singleShot(
            0, lambda: self.update_listings()
            if getattr(self, "active_transfer_progress", None) is None else None
        )
        pane.tree = tree
        # This tree is a brand new, empty widget -- but pane.snapshot_root/
        # pane._last_listed_dir (see listing_ops.py) live on the
        # DirectoryPane object, which _rebuild_ui does NOT recreate, so
        # they'd otherwise still show the *previous* tree's directory/
        # snapshot as already-loaded. update_listings' unchanged-content
        # check (_pane_content_unchanged) would then see a matching dir
        # and identical snapshot and take the retag_only path -- which
        # only re-colors existing tree items rather than populating a
        # tree from scratch -- against this empty tree, leaving the pane
        # blank. Clearing it here forces the next update_listings() call
        # (e.g. from _rebuild_ui's _restore_ui_state, such as when
        # Symmetrical GUI is toggled) to treat this pane as needing a
        # full listing again.
        pane._last_listed_dir = None

        is_left = pane is self.left
        other_pane = self.right if is_left else self.left
        # Pure layout-ordering flag: True means "lay this pane out the
        # same way the left pane is laid out". The left pane always uses
        # its own order; the right pane mirrors it (the original,
        # "Symmetrical Buttons" behavior) unless that setting is off, in
        # which case the right pane copies the left pane's order too.
        # Used for every left/right *ordering* decision below -- `is_left`
        # itself is kept for pane *identity* (which pane, arrow direction,
        # which real edge a group ends up at).
        layout_left = is_left if self._symmetrical_buttons else True

        # Shared by every plain/neutral button built below (toolbar,
        # count row, Select group, File Op, Clipboard) so they all match
        # each other in color and size -- see _default_button_colors and
        # _uniform_button_size.
        default_bg, default_fg = self._default_button_colors()
        uniform_w, uniform_h = self._uniform_button_size()

        # Top toolbar. Left pane: Up-folder, Select Directory at the outer
        # (left) edge; free space in the middle; Make New Folder at the
        # inner (right) edge. Right pane mirrors this: Make New Folder at
        # its inner (left) edge, free space in the middle, Select
        # Directory / Up-folder at its outer (right) edge. (When
        # Symmetrical Buttons is off, the right pane uses the same order
        # as the left pane instead.)
        # A plain "^" rather than the style's standard "parent folder"
        # icon: the icon is a fixed image, so the button's Fg color
        # (File List View Colors -> Button Colors) had no effect on it.
        # Bold and 2pt larger, same as the Expand/Collapse +/- glyphs.
        up_button = QPushButton("^")
        up_font = QFont(self.font())
        up_font.setBold(True)
        if self.font().pointSize() > 0:
            up_font.setPointSize(self.font().pointSize() + 2)
        else:
            up_font.setPixelSize(self.font().pixelSize() + 3)  # ~2pt equivalent
        up_button.setFont(up_font)
        up_button.setToolTip("Go up one folder")
        up_button.setFixedWidth(32)
        up_button.setFixedHeight(uniform_h)
        up_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("up")))
        up_button.clicked.connect(lambda checked=False, p=pane: self.go_up_one_folder(p))
        pane.up_button = up_button

        select_button = QPushButton("Select Directory")
        select_button.setToolTip("Also load snapshot\n(or drag and drop into views)")
        select_button.setFixedHeight(uniform_h)
        select_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("select_dir")))
        select_button.clicked.connect(lambda checked=False, p=pane: self.select_directory(p))
        pane.select_button = select_button

        save_snapshot_button = QPushButton("Save Snapshot")
        save_snapshot_button.setToolTip(
            "Save what this pane shows (names, sizes, dates and, if hashed, hashes) "
            "to a snapshot file (.fss) that can be loaded later and compared against a folder."
        )
        save_snapshot_button.setFixedHeight(uniform_h)
        save_snapshot_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("save_snapshot")))
        save_snapshot_button.clicked.connect(lambda checked=False, p=pane: self._on_save_snapshot(p))
        pane.save_snapshot_button = save_snapshot_button

        # Empties this pane's directory/tree/timeline entirely -- see
        # clear_pane_view. Placed in the count row below the list, at the
        # outer edge opposite Expand/Collapse All -- right-aligned by
        # default like everything else that follows layout_left, mirrored
        # to the left edge for the right pane while Symmetrical GUI is on.
        clear_view_button = QPushButton("Clear view")
        clear_view_button.setToolTip("Empty this pane's file list and timeline, as if starting over.")
        clear_view_button.setFixedHeight(uniform_h)
        clear_view_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("clear_view")))
        clear_view_button.clicked.connect(lambda checked=False, p=pane: self.clear_pane_view(p))
        pane.clear_view_button = clear_view_button

        # Hashes this pane's files for the Hash value / Fingerprint
        # comparison types -- see HashOpsMixin._hash_pane. Only enabled
        # while one of those types is selected. Sits at the outer edge of
        # the toolbar row, next to this pane's Refresh.
        hash_button = QPushButton("Hash new")
        hash_button.setToolTip(
            "Hash only the files that are not hashed yet (new or changed ones); files "
            "already hashed -- or found in the hash cache -- are skipped. Refresh is what "
            "hashes everything again. Hash value / Fingerprint comparison types only. "
            "With Recursive Folder Selections on, every file is considered; otherwise only "
            "the visible ones."
        )
        hash_button.setFixedHeight(uniform_h)
        hash_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("hash")))
        hash_button.clicked.connect(lambda checked=False, p=pane: self._hash_pane(p))
        hash_button.setEnabled(self._hash_type_selected())
        pane.hash_button = hash_button

        # This pane's own Refresh (next to Clear view): re-reads this pane's
        # folder from disk; with a hash comparison type it also rehashes --
        # see HashOpsMixin._refresh_pane.
        pane_refresh_button = QPushButton("Refresh")
        pane_refresh_button.setToolTip(
            "Re-read this pane's folder from disk (the other pane is left alone).\n"
            "With Hash value / Fingerprint selected it also discards the stored hashes "
            "(every algorithm) of this pane's files and hashes them again, after a "
            "confirmation: every file with Recursive Folder Selections on, otherwise only "
            "the visible ones. Files whose hash changed although size and date did not "
            "are reported."
        )
        pane_refresh_button.setFixedHeight(uniform_h)
        pane_refresh_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("pane_refresh")))
        pane_refresh_button.clicked.connect(lambda checked=False, p=pane: self._refresh_pane(p))
        pane.pane_refresh_button = pane_refresh_button

        # Duplicate Finder mode: the right pane is the Duplicate Finder.
        # A big centered title takes the place of its folder entry; its
        # status line and toolbar buttons are hidden (see
        # DuplicateFinderMixin._dup_sync_right).
        pane.dup_title_label = None
        if pane is self.right and not self._dual_pane_enabled:
            title = QLabel("Duplicate Finder")
            title_font = title.font()
            title_font.setPointSizeF(title_font.pointSizeF() * 1.6)
            title_font.setBold(True)
            title.setFont(title_font)
            title.setAlignment(Qt.AlignCenter)
            title.setFixedHeight(entry.sizeHint().height())
            file_list_layout.insertWidget(0, title)
            for widget in (snapshot_info_label, entry):
                widget.setVisible(False)
            pane.dup_title_label = title

        free_space_label = QLabel("")
        free_space_label.setStyleSheet("font-weight: bold;")
        # Rich text so the size figure can carry the Title Numeric color
        # (see ListingMixin._update_free_space_label), same as the
        # "Total:" label next to it.
        free_space_label.setTextFormat(Qt.TextFormat.RichText)
        pane.free_space_label = free_space_label

        # Moved up from the count row below the tree -- just the raw
        # "Total: N items (size)" figure now (see update_type_counts),
        # bold to match free_space_label's own styling since the two
        # share this line. No hardcoded color here -- see
        # UiBuilderMixin._numeric_span's docstring: it used to be a flat
        # color: #F5F5F5, which only ever looked right on Dark, so the
        # numeric figure itself is colored at update_type_counts time
        # instead (via _numeric_span), leaving this initial "0" plain
        # until the first real count comes in.
        total_items_label = QLabel("Total: 0 items")
        total_items_label.setStyleSheet("font-weight: bold;")
        total_items_label.setTextFormat(Qt.TextFormat.RichText)
        pane.total_items_label = total_items_label
        pane._total_atoms = [("Total:", "Total:"), ("0\u00a0items", "0\u00a0items")]
        pane._free_atoms = []

        # Roughly a third/two-thirds of the way across the row between
        # Select Directory and Clear view -- three equal stretch spans
        # around the two labels lands each one at the boundary between
        # spans, close enough to 1/3 and 2/3 without needing to measure
        # actual pixel widths. Mirrored right pane (Symmetrical GUI on)
        # swaps which label comes first, so the two positions swap too.
        toolbar_layout = QHBoxLayout()
        pane._toolbar_layout = toolbar_layout
        if layout_left:
            toolbar_layout.addWidget(up_button)
            toolbar_layout.addWidget(select_button)
            toolbar_layout.addWidget(save_snapshot_button)
            toolbar_layout.addStretch(1)
            toolbar_layout.addWidget(total_items_label)
            toolbar_layout.addStretch(1)
            toolbar_layout.addWidget(free_space_label)
            toolbar_layout.addStretch(1)
            toolbar_layout.addWidget(pane_refresh_button)
            toolbar_layout.addWidget(hash_button)
        else:
            toolbar_layout.addWidget(hash_button)
            toolbar_layout.addWidget(pane_refresh_button)
            toolbar_layout.addStretch(1)
            toolbar_layout.addWidget(free_space_label)
            toolbar_layout.addStretch(1)
            toolbar_layout.addWidget(total_items_label)
            toolbar_layout.addStretch(1)
            toolbar_layout.addWidget(save_snapshot_button)
            toolbar_layout.addWidget(select_button)
            toolbar_layout.addWidget(up_button)

        if pane is self.right and not self._dual_pane_enabled:
            # Duplicate Finder: the list's two modes, at the toolbar's inner
            # end (the other end has Save Snapshot).
            list_group = QButtonGroup(tree)
            list_group.setExclusive(True)
            pane.dup_list_group = list_group
            pane.dup_list_buttons = []
            unique = bool(getattr(self, "_dup_unique_only", False))
            for text, is_unique, tip in (
                ("Include paths", False,
                 "List every copy of every duplicate, in its folder."),
                ("Unique files only", True,
                 "List one file per group of duplicates (the copy \"Extra copies keep\" "
                 "leaves alone), no folders, with a Count column of how many copies it has."),
            ):
                button = QPushButton(text)
                button.setCheckable(True)
                button.setToolTip(tip)
                button.setFixedHeight(uniform_h)
                button.setChecked(is_unique == unique)
                button.setStyleSheet(self._dup_list_button_sheet())
                list_group.addButton(button)
                button.clicked.connect(lambda checked=False, u=is_unique: self._on_dup_list_mode(u))
                pane.dup_list_buttons.append(button)
            if layout_left:
                for button in pane.dup_list_buttons:
                    toolbar_layout.addWidget(button)
            else:
                for button in reversed(pane.dup_list_buttons):
                    toolbar_layout.insertWidget(0, button)
        file_list_layout.addLayout(toolbar_layout)
        file_list_layout.addWidget(tree, stretch=1)

        # Item-count row. Left pane: Expand All / Collapse All at the
        # outer (left) edge, count centered. Right pane mirrors this:
        # Collapse All / Expand All at the outer (right) edge. (When
        # Symmetrical Buttons is off, the right pane uses the same order
        # as the left pane instead.) Rename now lives in the File Op
        # group instead of this row -- see the button_layout section
        # below.
        # Compact +/- glyphs rather than full "Expand"/"Collapse" labels
        # -- bold and narrower (matching up_button's fixed-32-wide
        # treatment above) since a single bold character reads fine at
        # a fraction of the uniform button width, and it's one less
        # thing crowding this row. Full names moved to the tooltip.
        _expand_collapse_font = QFont(self.font())
        _expand_collapse_font.setBold(True)
        # +2pt over the app's base font -- reads a bit small at default
        # size for a lone bold glyph. Both buttons already have a hard
        # setFixedSize(32, uniform_h) below, so this doesn't grow them.
        base_pt = self.font().pointSize()
        if base_pt > 0:
            _expand_collapse_font.setPointSize(base_pt + 2)
        else:
            _expand_collapse_font.setPixelSize(self.font().pixelSize() + 3)  # ~2pt equivalent

        expand_all_button = QPushButton("+")
        expand_all_button.setToolTip("Expand All")
        expand_all_button.setFont(_expand_collapse_font)
        expand_all_button.setFixedSize(32, uniform_h)
        expand_all_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("expand_all")))
        expand_all_button.clicked.connect(lambda checked=False, p=pane: self.expand_all_folders(p))
        pane.expand_all_button = expand_all_button

        collapse_all_button = QPushButton("-")
        collapse_all_button.setToolTip("Collapse All")
        collapse_all_button.setFont(_expand_collapse_font)
        collapse_all_button.setFixedSize(32, uniform_h)
        collapse_all_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("collapse_all")))
        collapse_all_button.clicked.connect(lambda checked=False, p=pane: self.collapse_all_folders(p))
        pane.collapse_all_button = collapse_all_button

        # The Missing/Matching/etc. comparison breakdown only now --
        # the "Total items" figure itself moved up to the toolbar row
        # above (see total_items_label) -- see update_type_counts.
        count_label = QLabel("")
        # Rich text (colored per-diff-type spans) -- see
        # SelectionMixin.update_type_counts; explicit since the initial
        # empty text above wouldn't otherwise trigger autodetection.
        count_label.setTextFormat(Qt.TextFormat.RichText)
        pane.count_label = count_label

        count_row = QHBoxLayout()
        if layout_left:
            count_row.addWidget(expand_all_button)
            count_row.addWidget(collapse_all_button)
            count_row.addStretch(1)
            count_row.addWidget(count_label)
            count_row.addStretch(1)
            count_row.addWidget(clear_view_button)
        else:
            count_row.addWidget(clear_view_button)
            count_row.addStretch(1)
            count_row.addWidget(count_label)
            count_row.addStretch(1)
            count_row.addWidget(collapse_all_button)
            count_row.addWidget(expand_all_button)
        file_list_layout.addLayout(count_row)

        # Statistics view's own title row: a static title (shows even
        # before any folder's been opened, unlike the counts, which need
        # a snapshot to mean anything), a Files/Folders toggle choosing
        # what the chart below actually plots (see
        # TimelineOpsMixin._on_timeline_mode_changed), and the live
        # file/folder counts -- always both, regardless of which one is
        # currently plotted (see _update_timeline_totals_label).
        # Three parts so the Files/Folders totals sit centered between the
        # nearest buttons on each side: [title + Files/Folders] <totals>
        # [Zoom to + Reset], equal stretches on both sides of the totals.
        timeline_title_row = QHBoxLayout()
        title_left_box = QWidget()
        title_left = QHBoxLayout(title_left_box)
        title_left.setContentsMargins(0, 0, 0, 0)
        title_right_box = QWidget()
        title_right = QHBoxLayout(title_right_box)
        title_right.setContentsMargins(0, 0, 0, 0)
        pane._stats_title_boxes = (title_left_box, title_right_box)
        pane._stats_title_box_layouts = (title_left, title_right)

        timeline_title_label = QLabel("Statistics view")
        timeline_title_label.setStyleSheet("font-weight: bold;")
        title_left.addWidget(timeline_title_label)
        title_left.addSpacing(10)

        # Reset View, retitled "Reset" and moved up here from the
        # footer row below the chart (see _build_statistics_panel) --
        # it's a "restore the default view" action, same category as
        # the title/mode-toggle row it now shares, and consolidating it
        # here frees up a slot in that footer row (now down to one row
        # instead of two). Connected to timeline.reset_view once the
        # TimelineWidget itself exists, in _build_statistics_panel below
        # -- this title row is built first, before that widget.
        default_bg, default_fg = self._default_button_colors()
        _, uniform_h = self._uniform_button_size()
        reset_button = QPushButton("Reset")
        reset_button.setFixedHeight(uniform_h)
        reset_button.setStyleSheet(_bevel_button_stylesheet(*self._stats_button_color_for("reset")))
        pane.tl_reset_button = reset_button
        # "Zoom to" (Zoom to selection) and Reset now sit together at the
        # right end of this row, where the across-pane Move-->/Copy-->
        # buttons used to be (added after the stretch below). Zoom to is
        # built here so it can go into the row in the right order; its
        # click is wired to the timeline in _build_statistics_panel,
        # along with the +/- zoom buttons.
        zoom_sel_button = QPushButton("Zoom to")
        zoom_sel_button.setFixedHeight(uniform_h)
        zoom_sel_button.setStyleSheet(_bevel_button_stylesheet(*self._stats_button_color_for("zoom_to")))
        pane.tl_zoom_sel_button = zoom_sel_button
        title_left.addSpacing(10)

        timeline_mode_group = QButtonGroup(self)
        timeline_mode_group.setExclusive(True)

        files_mode_button = QPushButton("Files")
        files_mode_button.setCheckable(True)
        files_mode_button.setChecked(True)  # default mode
        files_mode_button.setFixedHeight(uniform_h)
        files_mode_button.setStyleSheet(_bevel_button_stylesheet(*self._stats_button_color_for("files")))
        files_mode_button.toggled.connect(
            lambda checked, p=pane: self._on_timeline_mode_changed(p, False) if checked else None
        )

        folders_mode_button = QPushButton("Folders")
        folders_mode_button.setCheckable(True)
        folders_mode_button.setFixedHeight(uniform_h)
        folders_mode_button.setStyleSheet(_bevel_button_stylesheet(*self._stats_button_color_for("folders")))
        folders_mode_button.toggled.connect(
            lambda checked, p=pane: self._on_timeline_mode_changed(p, True) if checked else None
        )

        timeline_mode_group.addButton(files_mode_button)
        timeline_mode_group.addButton(folders_mode_button)
        pane.tl_files_mode_button = files_mode_button
        pane.tl_folders_mode_button = folders_mode_button
        title_left.addWidget(files_mode_button)
        title_left.addWidget(folders_mode_button)
        title_left.addStretch(1)

        timeline_totals_label = QLabel("")
        timeline_totals_label.setTextFormat(Qt.TextFormat.RichText)
        timeline_totals_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        pane.tl_totals_label = timeline_totals_label
        pane._stats_atoms = []
        pane._stats_title_row = timeline_title_row
        title_right.addStretch(1)
        title_right.addWidget(zoom_sel_button)
        title_right.addWidget(reset_button)
        timeline_title_row.addWidget(title_left_box, 0)
        timeline_title_row.addStretch(1)
        timeline_title_row.addWidget(timeline_totals_label, 0)
        timeline_title_row.addStretch(1)
        timeline_title_row.addWidget(title_right_box, 0)
        stats_layout.addLayout(timeline_title_row)
        # Stashed so the Copy-->/Move--> buttons (built further below,
        # once the pane_splitter/stats_widget this row already lives in
        # is in place) can be inserted into this same row afterward --
        # see the "Stashed on self" block near copy_across_button/
        # move_across_button's own construction.
        pane.tl_title_row = timeline_title_row

        # This pane's embedded Timeline Explorer, directly below the
        # count/totals rows -- same width (inherits it from this same
        # QVBoxLayout). Its height now comes from the draggable splitter
        # below rather than a fixed PANE_TIMELINE_HEIGHT.
        stats_layout.addWidget(self._build_statistics_panel(pane))

        # File List view / Statistics view split: a vertical QSplitter
        # holding the two sub-layouts built above, so the user can drag
        # the handle between them to resize both at once (bottom of File
        # List view == top of Statistics view move together, being the
        # same handle). The ratio this ends up at is shared, persisted
        # state (self._file_list_ratio) rather than something local to
        # this one pane -- see _set_file_list_ratio, which mirrors a drag
        # on either pane's splitter onto the other pane's splitter and
        # onto the Settings tab's file_list_ratio_spin, and vice versa.
        # Resizing it re-wraps the per-type counts under the tree (see
        # SelectionMixin._relayout_pane_labels).
        file_list_widget = ResizeNotifyWidget(self._relayout_pane_labels)
        file_list_widget.setLayout(file_list_layout)
        # Resizing it re-wraps the "Files: X - Folders: Y" count above the
        # chart (see SelectionMixin._render_stats_totals).
        stats_widget = ResizeNotifyWidget(self._relayout_pane_labels)
        stats_widget.setLayout(stats_layout)
        pane._stats_widget = stats_widget

        pane_splitter = QSplitter(Qt.Vertical)
        # Neither side should be draggable down to nothing -- both views
        # stay at least minimally usable/visible.
        pane_splitter.setChildrenCollapsible(False)
        pane_splitter.addWidget(file_list_widget)
        pane_splitter.addWidget(stats_widget)
        pane.file_stats_splitter = pane_splitter
        self._apply_file_list_ratio_to_splitter(pane_splitter)
        pane_splitter.splitterMoved.connect(
            lambda pos, index, p=pane: self._on_pane_splitter_moved(p)
        )
        layout.addWidget(pane_splitter, stretch=1)

        uniform_w, uniform_h = self._uniform_button_size()

        copy_across_button = QPushButton("Copy \u2192" if is_left else "\u2190 Copy")
        copy_across_button.setToolTip("Copy selected items to the other view")
        copy_across_button.setFixedSize(uniform_w, uniform_h)
        copy_across_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("copy_across")))
        copy_across_button.clicked.connect(
            lambda checked=False, sp=pane, dp=other_pane: self.transfer_between_panes(sp, dp, move=False)
        )

        move_across_button = QPushButton("Move \u2192" if is_left else "\u2190 Move")
        move_across_button.setToolTip("Move selected items to the other view")
        move_across_button.setFixedSize(uniform_w, uniform_h)
        move_across_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("move_across")))
        move_across_button.clicked.connect(
            lambda checked=False, sp=pane, dp=other_pane: self.transfer_between_panes(sp, dp, move=True)
        )

        # Stashed on self (older references; recoloring now goes through
        # pane._across_buttons in _apply_main_button_colors)
        # and on the pane (for the Clipboard group
        # layouts, see _layout_clipboard_group/_layout_controls_group).
        if is_left:
            self.copy_left_to_right_button = copy_across_button
            self.move_left_to_right_button = move_across_button
        else:
            self.copy_right_to_left_button = copy_across_button
            self.move_right_to_left_button = move_across_button

        # Copy-->/Move--> no longer sit in the Statistics view's title row:
        # they're the top row of the Clipboard group (wide layout) and,
        # for Copy only, the bottom-right slot of the narrow Controls grid
        # -- see _layout_clipboard_group/_layout_controls_group. Placed
        # into those layouts there, so nothing is added to the title row
        # for them here.
        pane._across_buttons = {"copy": copy_across_button, "move": move_across_button}

        # The title row itself (title, Reset, Files/Folders, totals...) is
        # still flipped for the right pane whenever Symmetrical GUI is on,
        # same RightToLeft approach row1 uses below.
        mirror_this_row = (not is_left) and self._symmetrical_buttons
        if mirror_this_row:
            pane.tl_title_row.setDirection(QBoxLayout.RightToLeft)
            for box_layout in pane._stats_title_box_layouts:
                box_layout.setDirection(QBoxLayout.RightToLeft)

        # --------------------------------------------------- Select group
        select_group = QGroupBox("Select")
        select_layout = QGridLayout(select_group)
        # Laid out left-to-right, top-to-bottom as (for the left pane):
        #   Row 1: Select All, Missing, Smaller, Newer
        #   Row 2: Invert, Matching, Larger, Older
        #   Row 3: Clear, Diff Folders, All Parents, File Parent
        # The right pane mirrors this column-for-column (and swaps the
        # Cancel/Diff Folders side) so it reads as a left-right reflection
        # of the left pane's group -- unless Symmetrical Buttons is off,
        # in which case it uses the same column order as the left pane.
        SELECT_COLS = 4

        # Uniform sizing for every Select-group button now comes from
        # the same app-wide (width, height) every other group's buttons
        # use -- see _uniform_button_size -- so this group matches File
        # Op/Clipboard/the toolbar/etc. exactly rather than sizing
        # itself independently.
        _select_button_width, _select_button_height = uniform_w, uniform_h

        # Plain, one-shot buttons -- Select All/Invert/Clear act on
        # whatever's in this pane right now rather than searching for a
        # type, so unlike the buttons below they have nothing to toggle
        # on/off. All three now sit stacked in column 0, one per row,
        # same fixed size as every other Select-group button (Clear no
        # longer spans two rows down into what used to be empty space --
        # that space is Select All's row now).
        plain_buttons = [
            # Select All now uses the same strong yellow as the tree's own
            # selection highlight (see selection_color in
            # _build_directory_widgets) so the button visually reads as
            # "this is what a selection looks like" -- Clear takes the
            # white/black styling Select All used to have. Colors come
            # from self._select_colors now (Settings tab -> Colors &
            # Theme -> Select) rather than being fixed here.
            ("Select All", "select_all", lambda checked=False, p=pane: self.select_all(p), 0, 0),
            ("Clear", "clear", lambda checked=False, p=pane: self._on_clear_button_clicked(p), 2, 0),
        ]
        pane.select_plain_buttons = {}
        for text, key, handler, row, col in plain_buttons:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            btn.setFixedSize(_select_button_width, _select_button_height)
            fg, bg = self._select_colors[key]
            btn.setStyleSheet(_bevel_button_stylesheet(bg, fg))
            if text == "Select All":
                btn.setToolTip(
                    "Select everything in this pane. With Recursive Folder "
                    "Selections on, a full recursive filesystem search of "
                    "the whole tree; off, just what's already "
                    "visible/expanded on screen."
                )
            pane.select_plain_buttons[key] = btn

        # Invert gets its own widget class -- a diagonal black/white
        # split face (see InvertButton) rather than a flat single-color
        # button -- so it visually demonstrates the invert it performs.
        invert_button = InvertButton("Invert", mirrored=not layout_left)
        invert_button.clicked.connect(lambda checked=False, p=pane: self._invert_selection_for_pane(p))
        invert_button.setFixedSize(_select_button_width, _select_button_height)
        pane.invert_button = invert_button

        # Checkable, type-driven buttons -- pressing one selects every
        # item of that type (recursive search or visible-only, see
        # SelectionMixin._run_select); releasing it deselects that exact
        # same set again, via the same search re-run with select=False.
        # A file matching two of these at once (e.g. both Larger and
        # Older) just follows whichever button was most recently pressed
        # or released -- see _select_paths_in_tree. Tracked on
        # pane.select_toggle_buttons so Clear can pop them all back up
        # again -- see clear_selection.
        pane.select_toggle_buttons = {}
        toggle_buttons = [
            ("Missing", "missing", 0, 1),
            ("Newer", "newer", 0, 2),
            ("Smaller", "smaller", 0, 3),
            ("Matching", "matching", 1, 1),
            ("Older", "older", 1, 2),
            ("Larger", "larger", 1, 3),
        ]
        for text, type_key, row, col in toggle_buttons:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.toggled.connect(lambda checked, p=pane, tk=type_key: self._select_by_type(p, tk, select=checked))
            btn.setFixedSize(_select_button_width, _select_button_height)
            fg, bg = self._select_colors[type_key]
            btn.setStyleSheet(_bevel_button_stylesheet(bg, fg))
            pane.select_toggle_buttons[type_key] = btn
            if type_key in ("larger", "smaller", "newer", "older"):
                use_size, use_date = self._compare_axes()
                axis_ok = use_size if type_key in ("larger", "smaller") else use_date
                btn.setEnabled(axis_ok and not self._hash_mode_active())

        # Duplicate Finder mode (Compare folders off): these two take the
        # place of the six compare buttons above (see _group_buttons).
        pane.dup_layout = not self._dual_pane_enabled
        for text, type_key, tip in (
            ("Duplicates", "dup_all",
             "Select every file that has an identical copy (the whole set of each group)."),
            ("Extra copies", "dup_extra",
             "Select all copies but one in each group of identical files -- the one "
             "left alone is set under Settings -> Options -> \"Extra copies keep\". "
             "With a name-based comparison type, \"identical\" means what that type "
             "compares, so check the selection before deleting."),
            ("Sel. dups", "dup_sel",
             "Duplicates of selection: of the files already selected (a selected folder "
             "counts for the files in it), select every copy in their groups of identical "
             "files -- the whole set of each group, same as \"Duplicates\"."),
            ("Sel. extras", "dup_sel_extra",
             "Extra copies of selection: for the groups of identical files the current "
             "selection touches, select all copies but one -- the one left alone is the "
             "same as for \"Extra copies\" (Settings -> Options -> \"Extra copies keep\") "
             "and ends up unselected even if it was selected before."),
        ):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.toggled.connect(lambda checked, p=pane, tk=type_key: self._select_by_type(p, tk, select=checked))
            btn.setFixedSize(_select_button_width, _select_button_height)
            fg, bg = self._select_colors[type_key]
            btn.setStyleSheet(_bevel_button_stylesheet(bg, fg))
            pane.select_toggle_buttons[type_key] = btn
            btn.setEnabled(False)  # DuplicateFinderMixin._dup_update_buttons decides

        diff_folders_button = QPushButton("Diff Items")
        diff_folders_button.setCheckable(True)
        diff_folders_button.toggled.connect(
            lambda checked, p=pane: self.select_diff_folders(p, select=checked)
        )
        diff_folders_button.setFixedSize(_select_button_width, _select_button_height)
        fg, bg = self._select_colors["diff_folders"]
        diff_folders_button.setStyleSheet(_bevel_button_stylesheet(bg, fg))
        pane.select_toggle_buttons["diff_folders"] = diff_folders_button

        # Progress/Cancel for Select-group searches now live in the
        # central Transfer group instead of a per-pane bar (see
        # SelectionMixin._run_select) -- only one operation can ever be
        # running at once anyway, so one shared bar/Cancel does for both.
        select_parents_button = QPushButton("Item Parent")
        select_parents_button.setToolTip(
            "Add the parent folder of every currently selected item "
            "(files and folders), one level up. The current selection "
            "stays; press again to climb another level. Works regardless "
            "of the Recursive Folder Selections setting."
        )
        select_parents_button.clicked.connect(lambda checked=False, p=pane: self.select_file_parents(p))
        select_parents_button.setFixedSize(_select_button_width, _select_button_height)
        fg, bg = self._select_colors["file_parent"]
        select_parents_button.setStyleSheet(_bevel_button_stylesheet(bg, fg))
        pane.select_plain_buttons["file_parent"] = select_parents_button

        # QOL sibling of File Parents: always the "walk the whole
        # ancestor chain, files and folders alike" behavior in one
        # click, regardless of Recursive Folder Selections -- see
        # SelectionMixin.select_folder_parents. Darker blue than File
        # Parents so the two are still easy to tell apart at a glance.
        # Named "All Parents" (rather than "Folder Parent") so the name
        # itself reads as the relationship to File Parents -- one level
        # vs. all levels -- rather than needing the tooltip to explain it.
        folder_parents_button = QPushButton("All Parents")
        folder_parents_button.setToolTip(
            "Select every currently selected item's entire ancestor "
            "chain, up to this pane's root -- files and folders alike -- "
            "regardless of the \"Recursive Folder Selections\" setting."
        )
        folder_parents_button.clicked.connect(lambda checked=False, p=pane: self.select_folder_parents(p))
        folder_parents_button.setFixedSize(_select_button_width, _select_button_height)
        fg, bg = self._select_colors["all_parents"]
        folder_parents_button.setStyleSheet(_bevel_button_stylesheet(bg, fg))
        pane.select_plain_buttons["all_parents"] = folder_parents_button

        pane.select_group = select_group
        # Places every button above into select_layout for the current
        # layout_left -- see _layout_select_group. Also the entry point
        # a later narrow-to-wide crossing calls back into, since Select
        # group buttons may have been folded into the narrow "Controls"
        # group (see _layout_controls_group) in between.

        # --------------------------------------------------- File Op group
        # Rename, New Folder, and Delete -- same vertical order for both
        # panes (there's no meaningful "mirror" for a single stacked
        # column), unaffected by Symmetrical Buttons.
        file_op_group = QGroupBox("File Op")
        file_op_layout = QGridLayout(file_op_group)

        rename_button = QPushButton("Rename")
        rename_button.setToolTip(
            "Rename the selected item (also renames its counterpart on "
            "the other side if Synchronize Views is enabled)."
        )
        rename_button.clicked.connect(lambda checked=False, p=pane: self.rename_selected(p))
        rename_button.setFixedSize(uniform_w, uniform_h)
        rename_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("rename")))
        pane.rename_button = rename_button

        new_folder_button = QPushButton("New Folder")
        new_folder_button.setToolTip(
            "Create a new folder here (in both panes at once if "
            "Synchronize Views is enabled)."
        )
        new_folder_button.clicked.connect(lambda checked=False, p=pane: self.create_new_folder(p))
        new_folder_button.setFixedSize(uniform_w, uniform_h)
        new_folder_button.setStyleSheet(_bevel_button_stylesheet(*self._main_button_color_for("new_folder")))
        pane.new_folder_button = new_folder_button

        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(lambda checked=False, p=pane: self.delete_selected(p))
        delete_button.setIconSize(QSize(14, 14))
        delete_button.setFixedSize(uniform_w, uniform_h)
        delete_bg, delete_fg = self._main_button_color_for("delete")
        delete_button.setStyleSheet(_bevel_button_stylesheet(delete_bg, delete_fg))
        delete_button.setIcon(self._make_icon("delete", delete_fg))
        pane.delete_button = delete_button
        pane.file_op_group = file_op_group

        # On the mirrored right pane (only while Symmetrical Buttons is
        # actually on -- with it off the right pane copies the left
        # pane's own left-aligned look, per layout_left above), the
        # whole cluster reads right-to-left, so its own group titles,
        # File Op's narrower buttons, and the Clipboard grid's own
        # column order (see below) all read as right-aligned/mirrored
        # too, to match. Select doesn't need this: its grid already
        # reads right-to-left via its own column-order swap (see
        # SELECT_COLS above), with no group-vs-button width mismatch to
        # correct for in the first place. Computed up here (rather than
        # after Clipboard, where this used to live) since the Clipboard
        # grid below now needs it to decide column order, not just
        # alignment.
        mirrored_right = (not is_left) and self._symmetrical_buttons
        # Places Rename/New Folder/Delete into file_op_layout, aligned
        # for mirrored_right -- see _layout_file_op_group. Also the
        # entry point a later narrow-to-wide crossing calls back into.

        # ------------------------------------------------- Clipboard group
        clipboard_group = QGroupBox("Clipboard")
        # Persistent outer layout -- only its *inner* content (a grid in
        # the wide arrangement, a single row in the narrow one) gets torn
        # down and rebuilt, by _layout_clipboard_group, same "swap the
        # inner layout, not the group's own top-level one" approach
        # _layout_pane_cluster uses for cluster_container. Needed because
        # Qt won't let a widget's top-level layout just be replaced with
        # a different one later.
        QGridLayout(clipboard_group)
        # The plain same-pane Cut/Copy/Paste/Paste Folders actions, plus --
        # placed by _layout_clipboard_group as its top row -- the
        # across-pane Move/Copy buttons built earlier in this method
        # (pane._across_buttons). See _layout_clipboard_group/
        # _layout_controls_group for how they're arranged, wide or narrow.
        cut_button = QPushButton("Cut")
        cut_button.clicked.connect(lambda checked=False, p=pane: self.cut_to_clipboard(p))
        cut_button.setIconSize(QSize(14, 14))
        cut_button.setFixedSize(uniform_w, uniform_h)

        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(lambda checked=False, p=pane: self.copy_to_clipboard(p))
        copy_button.setIconSize(QSize(14, 14))
        copy_button.setFixedSize(uniform_w, uniform_h)

        paste_button = QPushButton("Paste")
        paste_button.clicked.connect(lambda checked=False, p=pane: self.paste_from_clipboard(p))
        paste_button.setIconSize(QSize(14, 14))
        paste_button.setFixedSize(uniform_w, uniform_h)

        # Halfway between the blue used elsewhere for a "parents"-style
        # structural pick (e.g. select_parents_button's #3355DD) and
        # Paste's own brown (#A9784B) -- reads as a sibling of Paste
        # (same icon, same general warmth) while still visibly distinct
        # from it. Its own label is longer than the others', so it's
        # exempt from the shared uniform width (fixed height only) --
        # which is also why Clipboard's second column ends up a little
        # wider than its first, in the wide/grid arrangement.
        paste_folders_button = QPushButton("Paste Folders")
        paste_folders_button.clicked.connect(lambda checked=False, p=pane: self.paste_folders_from_clipboard(p))
        paste_folders_button.setIconSize(QSize(14, 14))
        paste_folders_button.setFixedHeight(uniform_h)

        pane._clipboard_buttons = {
            "copy": copy_button, "cut": cut_button, "paste": paste_button,
            "paste_folders": paste_folders_button,
        }
        # Colors (and icons, for the four with one) come from
        # self._main_button_colors -- customizable via the Settings
        # tab's new Clipboard/File Op Buttons tables -- rather than a
        # fixed setStyleSheet/setIcon call per button here, the same
        # indirection the Statistics-view buttons above already use.
        for key, btn in pane._clipboard_buttons.items():
            bg, fg = self._main_button_color_for(key)
            btn.setStyleSheet(_bevel_button_stylesheet(bg, fg))
            btn.setIcon(self._make_icon(_MAIN_BUTTON_ICON_KINDS[key], fg))

        # Internal order (which of Select/Clipboard sits closer to
        # center) follows layout_left, same as everywhere else -- File Op
        # always stays in the middle, between them, regardless. Which
        # side of the pane the whole cluster sits against follows
        # mirrored_right (see _layout_pane_cluster's _add_row): left
        # pane and (Symmetrical GUI off) right pane are left-aligned;
        # the right pane is right-aligned only while Symmetrical GUI is
        # on.
        # Which of Select/File Op's remaining buttons (Rename/New
        # Folder/Delete) hugs which edge also follows mirrored_right --
        # handled inside _layout_file_op_group (called above). The
        # groups' own titles follow it too, via QGroupBox.setAlignment
        # in _layout_pane_cluster's wide branch (re-applied on every
        # rebuild and width crossing, since that's what runs then).
        pane.clipboard_group = clipboard_group

        if is_left:
            self.left_clipboard_group = clipboard_group
        else:
            self.right_clipboard_group = clipboard_group

        # Select/File Op/Clipboard live in their own container, whose
        # content (which groups sit where, and how many columns each
        # group's buttons wrap into) is re-arranged to the pane's width
        # whenever it resizes -- see responsive_layout.py. The groups are
        # parented to it right away; the first arrangement happens at the
        # end of _build_ui (_update_responsive_layout(force=True)).
        # Horizontal size policy Ignored: its own minimum width must not
        # become the window's minimum (the groups wrap instead).
        cluster_container = ResizeNotifyWidget(self._relayout_all_clusters)
        cluster_container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        QVBoxLayout(cluster_container)
        cluster_container.layout().setContentsMargins(0, 0, 0, 0)
        cluster_container.layout().setSpacing(_CLUSTER_V_GAP)
        pane.cluster_container = cluster_container
        for group in (select_group, file_op_group, clipboard_group):
            group.setParent(cluster_container)
            margins = group.layout().contentsMargins()
            group.layout().setContentsMargins(
                _CLUSTER_GROUP_PAD, margins.top(), _CLUSTER_GROUP_PAD, margins.bottom()
            )
        pane._cluster_applied = None
        pane._cluster_size_cache = {}
        pane._cluster_arrangement_cache = {}
        # Not added to this pane's own layout: the clusters of both panes
        # sit in a row of their own under the panes, either side of the
        # central Folder Op group (UiBuilderMixin._build_cluster_row).

        tree.itemSelectionChanged.connect(
            lambda t=tree, p=pane: self.update_selection_count(t, p)
        )
        # "Synchronize Views": mirror this pane's selection onto the other
        # pane's tree (no-op while it's off).
        tree.itemSelectionChanged.connect(lambda t=tree: self._sync_selection_from(t))
        # Duplicate Finder mode: the right pane's selection is revealed in the
        # left pane (no-op for anything else).
        tree.itemSelectionChanged.connect(lambda p=pane: self._on_dup_right_selection(p))
        # A selection change not driven by the Select group's own
        # machinery -- a plain click/drag/keyboard selection right in
        # the tree -- means any checked Select-group toggle button no
        # longer describes what's actually selected; see
        # _on_manual_tree_selection.
        tree.itemSelectionChanged.connect(lambda p=pane: self._on_manual_tree_selection(p))
        # Tree -> timeline: mirror this pane's file selection onto its
        # timeline's bin highlighting. See
        # TimelineOpsMixin._sync_timeline_selection.
        tree.itemSelectionChanged.connect(lambda p=pane: self._sync_timeline_selection(p))

        return container

    def _build_statistics_panel(self, pane: DirectoryPane) -> QWidget:
        """This pane's embedded Timeline Explorer: a TimelineWidget fed
        from `pane`'s own snapshot (see
        TimelineOpsMixin._refresh_pane_timeline) plus the same controls
        Timeline Explorer's own MainWindow builds around one (see
        main_window.py) -- reused essentially as-is, just at pane width
        and (like Timeline Explorer's own copy) inside a splitter pane
        rather than a fixed height -- see the File List/Statistics
        QSplitter in _build_directory_widgets -- and without a FileTable
        of its own since the tree above it already is one.

        Unlike the Statistics view's own title line above (pane.
        tl_title_row -- see _build_directory_widgets, which now mirrors
        under Symmetrical GUI alone, and is also where the Reset button
        itself is built), this method's own footer -- row1, the single
        row of buttons/options below the chart -- normally isn't
        mirrored left/right for the right pane: same order both sides.
        The Settings tab's "Include footer buttons and options in
        Symmetrical GUI" option (Statistics View section) opts back into
        mirroring here too, when it and Symmetrical GUI are both on:
        row1's own RightToLeft direction flip (rather than rebuilding it
        in reverse) mirrors the widgets' visual order while every
        reference (pane.tl_auto_vrange_cb etc.) still points at the same
        real widget either way. set_mirrored on the TimelineWidget
        itself does the same for its own hover/selection text -- axis
        and bins are never mirrored, by design.

        The behavior these widgets trigger (data feed, selection sync,
        scrollbar<->view-range sync) all lives in TimelineOpsMixin;
        this method is construction and wiring only, same split as the
        rest of this file."""
        default_bg, default_fg = self._default_button_colors()
        _, uniform_h = self._uniform_button_size()
        is_left = pane is self.left
        mirror_this_pane = (not is_left) and self._symmetrical_buttons and self._mirror_statistics_buttons

        panel = QWidget()
        tl_layout = QVBoxLayout(panel)
        tl_layout.setContentsMargins(0, 4, 0, 4)

        timeline = TimelineWidget()
        # Already sets its own setMinimumHeight()/Expanding size policy
        # (see TimelineWidget.__init__) -- it was only ever given a
        # *fixed* height here, via PANE_TIMELINE_HEIGHT below. Now that
        # the Statistics view's actual on-screen height comes from the
        # draggable File List/Statistics splitter instead (see
        # _build_directory_widgets), drop the setFixedHeight entirely
        # and just add it with stretch=1 so it grows/shrinks with the
        # splitter rather than leaving blank space above/below a
        # fixed-size chart.
        pane.timeline = timeline
        timeline.set_mirrored(mirror_this_pane)
        # Statistics View Colors (Settings tab) -- see
        # TimelineOpsMixin._apply_stats_palette. File mode's palette
        # until the Files/Folders toggle (built above, in
        # _build_directory_widgets) says otherwise.
        self._apply_stats_palette(pane)
        tl_layout.addWidget(timeline, stretch=1)

        # Reset (pane.tl_reset_button) and Zoom to (pane.tl_zoom_sel_button)
        # were built up in the title row, before this TimelineWidget
        # existed -- see _build_directory_widgets. Wire their clicks up
        # now that it does (the +/- zoom buttons are wired further down).
        pane.tl_reset_button.clicked.connect(timeline.reset_view)

        # Same "open this folder" contract as the entry field and tree
        # (see their own directoryDropped wiring above) -- and the same
        # guard against navigating mid-scan while a transfer/enumeration
        # progress cycle is active.
        timeline.directoryDropped.connect(
            lambda path, p=pane: self._navigate_pane(p, path)
            if getattr(self, "active_transfer_progress", None) is None else None
        )

        scrollbar = _ThemedScrollBar(Qt.Horizontal)
        scrollbar.setRange(0, TIMELINE_SCROLLBAR_RANGE)
        pane.tl_scrollbar = scrollbar
        tl_layout.addWidget(scrollbar)

        # Row 1 (now the only row -- Reset moved up to the title row
        # above, see _build_directory_widgets, freeing up enough space
        # to fold everything else into one). Left to right: Log height
        # (leftmost) -> Auto Range (where Reset View used to sit) ->
        # Fit Range (dims while Auto Range is on, via readjust_normal_
        # style/readjust_disabled_style below) -> the zoom trio ->
        # Metric: + its dropdown -> Log size (the File-Size-only log-
        # size-axis option). Each of those clusters keeps fixed spacing
        # *within* itself (buttons/checkboxes never move relative to
        # their own neighbor), but the gaps *between* clusters are
        # stretch(1) rather than a fixed 16px -- the same reasoning the
        # pane cluster row used before it switched to fixed gaps (see
        # _layout_pane_cluster's _add_row): with only interior gaps
        # stretchy, Log height and Log size end up flush against this
        # row's own two edges for free (a QHBoxLayout always fills its
        # given width, and here only those gaps claim the leftover), so
        # the clusters spread out evenly as the pane widens instead of
        # leaving one dead strip after Log size like a single trailing
        # stretch would.
        row1 = QHBoxLayout()

        log_scale_cb = QCheckBox("Log height")
        log_scale_cb.setToolTip(
            "Scale bar heights by log(count) instead of count, so a few "
            "tall bins don't flatten everything else."
        )
        pane.tl_log_scale_cb = log_scale_cb
        log_scale_cb.toggled.connect(timeline.set_log_scale)
        row1.addWidget(log_scale_cb)

        row1.addStretch(1)

        auto_vrange_cb = QCheckBox("Auto Range")
        auto_vrange_cb.setChecked(True)
        auto_vrange_cb.setToolTip(
            "On: the chart's max count always tracks the tallest bin "
            "currently visible. Off: the max count is fixed at whatever it "
            "was last automatically determined to be."
        )
        pane.tl_auto_vrange_cb = auto_vrange_cb
        auto_vrange_cb.toggled.connect(timeline.set_auto_vrange)
        row1.addWidget(auto_vrange_cb)

        readjust_btn = QPushButton("Fit Range")
        readjust_btn.setToolTip(
            "Recalculate the fixed max count from what's on screen right "
            "now (same as toggling Auto Range off and on)."
        )
        readjust_btn.setFixedHeight(uniform_h)

        # Fit Range sits disabled for as long as Auto Range is on, so it
        # needs a visibly greyed-out look of its own -- see
        # _fit_range_stylesheet, which also gives it its own color
        # (Statistics View -> Buttons -> "Fit Range").
        readjust_btn.setEnabled(False)  # only meaningful while auto is off
        readjust_btn.setStyleSheet(self._fit_range_stylesheet(False))  # Auto Range starts checked (on)
        pane.tl_readjust_vrange_button = readjust_btn
        readjust_btn.clicked.connect(timeline.readjust_vrange)
        row1.addWidget(readjust_btn)

        auto_vrange_cb.toggled.connect(lambda checked: readjust_btn.setEnabled(not checked))
        auto_vrange_cb.toggled.connect(
            lambda checked, btn=readjust_btn: btn.setStyleSheet(self._fit_range_stylesheet(not checked))
        )

        row1.addStretch(1)

        zoom_buttons = [
            ("+", "tl_zoom_in_button", "zoom_in"),
            ("-", "tl_zoom_out_button", "zoom_out"),
        ]
        for text, attr, color_key in zoom_buttons:
            btn = QPushButton(text)
            btn.setFixedHeight(uniform_h)
            btn.setStyleSheet(_bevel_button_stylesheet(*self._stats_button_color_for(color_key)))
            if text in ("+", "-"):
                # +2pt larger font for just the +/- zoom glyphs (same
                # treatment as the tree view's own Expand/Collapse +/-
                # buttons) -- width locked to this button's current,
                # default-font sizeHint *first*, so the bigger glyph
                # doesn't also grow the button itself.
                btn.setFixedWidth(btn.sizeHint().width())
                bigger_font = QFont(btn.font())
                base_pt = btn.font().pointSize()
                if base_pt > 0:
                    bigger_font.setPointSize(base_pt + 2)
                else:
                    bigger_font.setPixelSize(btn.font().pixelSize() + 3)  # ~2pt equivalent
                btn.setFont(bigger_font)
            row1.addWidget(btn)
            setattr(pane, attr, btn)

        pane.tl_zoom_in_button.clicked.connect(lambda checked=False, t=timeline: t.zoom(0.7))
        pane.tl_zoom_sel_button.clicked.connect(timeline.zoom_to_selection)
        pane.tl_zoom_out_button.clicked.connect(lambda checked=False, t=timeline: t.zoom(1 / 0.7))
        # (Clear Selection button removed -- the Select group's own
        # "Clear" already does this job; see plain_buttons above.)

        row1.addStretch(1)
        row1.addWidget(QLabel("Metric:"))
        metric_combo = QComboBox()
        metric_combo.addItem("Modified", "modified")
        metric_combo.addItem(CHANGED_FIELD_LABEL, "changed")
        metric_combo.addItem("Accessed", "accessed")
        metric_combo.addItem("File Size", "size")
        metric_combo.addItem("File Filter types", "file_filter")
        pane.tl_metric_combo = metric_combo
        metric_combo.currentIndexChanged.connect(
            lambda idx, p=pane: self._on_statistics_metric_changed(p, idx)
        )
        # Narrow layout only: the file list's single remaining date
        # column follows whichever Metric is now selected -- see
        # _update_pane_date_columns. A no-op while wide (all three date
        # columns are shown regardless of Metric).
        metric_combo.currentIndexChanged.connect(lambda idx, p=pane: self._update_pane_date_columns(p))
        row1.addWidget(metric_combo)

        row1.addStretch(1)

        # Only meaningful for the "size" metric (a decade-based axis
        # doesn't apply to dates) -- disabled otherwise rather than
        # hidden, so its own state/tooltip stays visible and it doesn't
        # shift the rest of this row around every time the metric
        # changes. See TimelineWidget.set_log_size_axis / SizeAxis.log_mode.
        log_size_axis_cb = QCheckBox("Log size")
        log_size_axis_cb.setToolTip(
            "Only for the File Size metric. Bin edges land on powers of "
            "ten (or finer) instead of equal-width steps, so a view "
            "spanning KB to GB shows every scale at once instead of "
            "small files being crushed flat by the large end's width.\n"
            "Bin width then varies by position -- hover a bin to see its "
            "actual range."
        )
        pane.tl_log_size_axis_cb = log_size_axis_cb
        log_size_axis_cb.toggled.connect(timeline.set_log_size_axis)
        # Same "disabling alone doesn't visibly gray out a QCheckBox
        # here" issue as Synchronize Views/Symmetrical GUI/Expand To
        # Selection -- see _DISABLED_CHECKBOX_STYLE's own definition.
        # Without this, the checkbox was being correctly disabled
        # (clicks did nothing while Metric wasn't File Size) but looked
        # identical to enabled, which is what Ragnar was seeing.
        log_size_axis_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        log_size_axis_cb.setEnabled(metric_combo.currentData() == "size")
        metric_combo.currentIndexChanged.connect(
            lambda idx, cb=log_size_axis_cb, mc=metric_combo: cb.setEnabled(mc.currentData() == "size")
        )
        row1.addWidget(log_size_axis_cb)

        # "Bin size: ..." used to show here, but the timeline view's own
        # upper-left readout already shows the same text (see
        # statistics_widget._paint's "otherwise" branch) whenever nothing's
        # hovered/selected, so this toolbar copy was pure duplication.
        # tl_bin_size_label itself stays alive (just parented to the
        # panel, never added to a layout, and hidden) rather than being
        # deleted outright, in case TimelineOpsMixin still calls
        # .setText() on it somewhere I haven't seen.
        bin_size_label = QLabel("", panel)
        bin_size_label.hide()
        pane.tl_bin_size_label = bin_size_label

        # No trailing stretch after Log size -- with every gap between
        # clusters already stretchy (see this row's own opening comment
        # above), Log size lands flush against the row's right edge on
        # its own; an extra stretch here would just be dead space with
        # nothing after it to push.
        if mirror_this_pane:
            row1.setDirection(QBoxLayout.RightToLeft)
        tl_layout.addLayout(row1)

        # Selection sync (timeline -> tree) and view-range <-> scrollbar
        # sync -- see TimelineOpsMixin.
        timeline.selectionChanged.connect(lambda paths, p=pane: self._on_timeline_selection(p, paths))
        timeline.viewRangeChanged.connect(
            lambda v0, v1, p=pane: self._on_timeline_view_range_changed(p, v0, v1)
        )
        timeline.viewRangeChanged.connect(lambda v0, v1, p=pane: self._update_statistics_bin_size_label(p))
        scrollbar.valueChanged.connect(lambda v, p=pane: self._on_statistics_scrollbar_moved(p, v))
        # timeline.binInfo isn't connected to anything: TimelineWidget
        # already draws this itself, in the upper-left corner by the
        # axis (hover in near-white, selection count in orange) -- see
        # its own _paint(). binInfo is only for a *separate* status-bar
        # display, which DirComp doesn't have and doesn't need here.

        return panel

    def _apply_file_list_ratio_to_splitter(self, splitter: QSplitter):
        """Set `splitter`'s two sizes from self._file_list_ratio. Uses a
        fixed 1000-unit baseline rather than the splitter's actual
        current pixel height -- QSplitter.setSizes() normalizes whatever
        list of sizes it's given to fit the space actually available, so
        this works whether or not the splitter's even been shown/sized
        by its parent layout yet (e.g. right at construction time)."""
        ratio = max(0.05, min(0.95, getattr(self, "_file_list_ratio", 0.75)))
        total = 1000
        top = max(1, round(total * ratio))
        bottom = max(1, total - top)
        splitter.setSizes([top, bottom])

    def _on_pane_splitter_moved(self, pane: DirectoryPane):
        """A user just dragged one pane's File List/Statistics handle.
        Recompute the ratio from that splitter's own sizes and propagate
        it everywhere else that mirrors it -- see _set_file_list_ratio."""
        splitter = getattr(pane, "file_stats_splitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) != 2 or sum(sizes) <= 0:
            return
        self._set_file_list_ratio(sizes[0] / sum(sizes), source_pane=pane)

    def _set_file_list_ratio(self, ratio: float, source_pane: DirectoryPane = None):
        """Single source of truth for the File List/Statistics split,
        shared by both panes and the Settings tab's file_list_ratio_spin
        -- same "one shared setting, several reachable controls" pattern
        as common_font_size_spin/common_nice_bins_cb/common_bin_count_
        spin above. `source_pane`, when given, is the pane whose own
        splitter the user is actively dragging -- skipped when
        reapplying sizes so an in-progress drag isn't fought pixel-by-
        pixel by its own rounded-to-1000 mirror; the *other* pane and
        the spin box still pick the new ratio up immediately."""
        ratio = max(0.05, min(0.95, ratio))
        self._file_list_ratio = ratio
        for pane in (self.left, self.right):
            if pane is source_pane:
                continue
            splitter = getattr(pane, "file_stats_splitter", None)
            if splitter is not None:
                self._apply_file_list_ratio_to_splitter(splitter)
        spin = getattr(self, "file_list_ratio_spin", None)
        if spin is not None and abs(spin.value() - ratio) > 1e-6:
            spin.blockSignals(True)
            spin.setValue(ratio)
            spin.blockSignals(False)

    def _on_file_list_ratio_spin_changed(self, value: float):
        """Settings tab's own input field for the split -- see
        _set_file_list_ratio. No source_pane here: an edit from the
        spin box (rather than a drag) should update both panes'
        splitters, not just one."""
        self._set_file_list_ratio(value)

    def _on_clear_button_clicked(self, pane: DirectoryPane):
        """Select group's "Clear" button: clears the tree selection
        (self.clear_selection, elsewhere) as before, the active mode's
        Statistics view state (timeline.clear_selection(), both the
        drag-range overlay and the discrete selection -- self.
        clear_selection's tree-driven sync never touched the range),
        and now also the *other*, currently-hidden mode's own stashed
        selection (pane.tl_mode_selections) -- otherwise it would still
        be sitting there ready to reappear, additively unioned back into
        the tree, the next time the Files/Folders toggle switches modes
        (see TimelineOpsMixin._on_timeline_mode_changed) -- not the
        clean slate "Clear" is supposed to leave behind."""
        self.clear_selection(pane)
        timeline = getattr(pane, "timeline", None)
        if timeline is not None:
            timeline.clear_selection()
        pane.tl_mode_selections = {"file": None, "folder": None}
