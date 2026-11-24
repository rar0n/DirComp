"""
SettingsTabMixin: the Settings tab -- _build_settings_tab and everything that
exists only to build or feed it: the title-level helpers
(_settings_section/_subsection/_subsubsection), the Main<->Settings copy
linkers (_link_checkboxes/_link_spinboxes), the color swatch and
color-table helpers, and the handlers of the Statistics View (common)
controls.

Split out of ui_builder.py unchanged -- same methods, still reached as
`self._...` because UiBuilderMixin inherits this class.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpinBox, QStyle, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from constants import (
    COMPARISON_TYPE_ITEMS, HASH_ALGORITHMS, CMP_NAME_SIZE_DATE, CMP_HASH, CMP_FINGERPRINT,
    MIN_FINGERPRINT_CHUNK_KB, MAX_FINGERPRINT_CHUNK_KB,
    DUP_SUMMARY_TYPES, TAG_COLORS, _DISABLED_CHECKBOX_STYLE, _DISABLED_SPINBOX_STYLE,
    _MAX_TREE_INDENTATION, _MIN_TREE_INDENTATION, _SECTION_HEADER_PT, _SECTION_HEADER_STYLE,
    _SUBSECTION_GAP, _SUBSUBTITLE_PT, _SUBSUBTITLE_STYLE, _SUBTITLE_PT, _SUBTITLE_STYLE,
    _TABLE_HEADER_STYLE,
)
from custom_widgets import (
    _DEFAULT_SELECT_COLORS, _INVERT_BUTTON_COLORS, _SCROLLBAR_COLORS, _SCROLLBAR_HANDLE_STYLES,
    _SCROLLBAR_STYLE, FlowLayout,
    _SELECT_KEY_TO_TAG_COLOR_KEYS, _bevel_button_stylesheet, _press_blend,
    SliderDoubleSpinBox, SliderSpinBox, SpinSlider,
    spin_slider_inline,
)
from statistics_widget import DEFAULT_BIN_COUNT, DEFAULT_LABEL_FONT_PT, TICK_LABEL_ANGLE_DEG
from theme_ops import _THEMES


class SettingsTabMixin:

    # -------------------------------------------------------------- Settings tab

    def _link_checkboxes(self, primary: QCheckBox, secondary: QCheckBox):
        """Bidirectionally sync two checkboxes representing the same
        underlying setting -- a Main-tab control and its Settings-tab
        copy. Toggling either one sets the other to match; since that's
        done through the PRIMARY's own setChecked (not just a value
        copy), the primary's own existing handler chain (e.g.
        _on_synchronize_views_changed, or the auto-vrange/readjust-
        button enable relationship) still runs exactly as if you'd
        clicked the primary directly, no matter which of the two you
        actually clicked. blockSignals guards against the obvious
        infinite ping-pong; the `!=` checks mean a toggle that didn't
        actually change anything (e.g. the primary's own handler setting
        it right back) doesn't recurse further than one hop."""
        def sync_to_secondary(checked, secondary=secondary):
            if secondary.isChecked() != checked:
                secondary.blockSignals(True)
                secondary.setChecked(checked)
                secondary.blockSignals(False)

        def sync_to_primary(checked, primary=primary):
            if primary.isChecked() != checked:
                primary.setChecked(checked)

        primary.toggled.connect(sync_to_secondary)
        secondary.toggled.connect(sync_to_primary)
        secondary.blockSignals(True)
        secondary.setChecked(primary.isChecked())
        secondary.blockSignals(False)

    def _link_spinboxes(self, primary: QSpinBox, secondary: QSpinBox):
        """Same idea as _link_checkboxes, for a spinbox pair -- e.g. the
        a spinbox pair's Main/Settings-tab copies."""
        def sync_to_secondary(value, secondary=secondary):
            if secondary.value() != value:
                secondary.blockSignals(True)
                secondary.setValue(value)
                secondary.blockSignals(False)

        def sync_to_primary(value, primary=primary):
            if primary.value() != value:
                primary.setValue(value)

        primary.valueChanged.connect(sync_to_secondary)
        secondary.valueChanged.connect(sync_to_primary)
        secondary.blockSignals(True)
        secondary.setValue(primary.value())
        secondary.blockSignals(False)

    def _settings_titled_block(
        self, title: str, group: QWidget, pt: int, style: str, space_above: bool, bands: int = 1,
    ) -> QWidget:
        """A title label (`style`, `pt` points, bold) directly above
        `group`, with an empty band above the title exactly as tall as
        the title itself when `space_above` -- the same rule at every
        title level, so blocks start clearly apart from whatever comes
        before them (or, in a FlowLayout, from the line above when they
        wrap), in proportion to how big their own title is. Only the
        very first title on the page (Global Settings) goes without. `bands`:
        how many title-heights of empty band (Level 1 uses 2: its own
        blank line plus one more, to set the sections clearly apart)."""
        container = QWidget()
        layout = QVBoxLayout(container)
        title_font = QFont(self.font())
        title_font.setPointSizeF(float(pt))
        title_font.setBold(True)
        layout.setContentsMargins(0, QFontMetrics(title_font).height() * bands if space_above else 0, 0, 0)
        layout.setSpacing(4)
        header = QLabel(title)
        header.setStyleSheet(style)
        layout.addWidget(header)
        layout.addWidget(group)
        return container

    def _settings_section(self, title: str, group: QGroupBox, space_above: bool = True) -> QWidget:
        """Level 1: wraps a Settings-tab section's (title-less) QGroupBox
        with a real QLabel above it acting as that section's header --
        see _SECTION_HEADER_STYLE. Callers build `group` and its
        contents, constructed with QGroupBox(""); the returned container
        is a drop-in substitute for `group` at whichever addWidget call
        adds it."""
        return self._settings_titled_block(
            title, group, _SECTION_HEADER_PT, _SECTION_HEADER_STYLE, space_above, bands=2
        )

    def _settings_subsection(self, title: str, group: QGroupBox, space_above: bool = True) -> QWidget:
        """Level 2 (_SUBTITLE_STYLE): an individual table or group of
        controls sitting *within* a larger section's frame (e.g.
        "Select Buttons" under "Panel Button Colors")."""
        return self._settings_titled_block(title, group, _SUBTITLE_PT, _SUBTITLE_STYLE, space_above)

    def _settings_subsubsection(self, title: str, group: QWidget, space_above: bool = True) -> QWidget:
        """Level 3 (_SUBSUBTITLE_STYLE): a block nested inside a Level 2
        one (currently "Line colors" and "Line style" under
        "Indentation Line")."""
        return self._settings_titled_block(
            title, group, _SUBSUBTITLE_PT, _SUBSUBTITLE_STYLE, space_above
        )

    def _settings_subsection_flow(self, *blocks: QWidget, reorder: bool = True) -> FlowLayout:
        """The layout for a section's sub-blocks (Level 2 blocks from
        _settings_subsection inside a Level 1 section's frame, or Level
        3 ones from _settings_subsubsection inside a Level 2 block):
        side by side, top-aligned, with _SUBSECTION_GAP between them
        while the window is wide enough for them, wrapping onto more
        lines when it isn't -- in whichever arrangement makes the whole
        block lowest (see FlowLayout). With `reorder` on (the default)
        that may also change which block comes first, so a block that
        stacks up when squeezed next to a wide neighbour can move to a
        line where it has room to spread out -- except the FIRST block,
        which always stays top-left; turn reorder off for blocks whose
        order means something (Left pane / Right pane). `blocks`
        are added right away; more can be added later with addWidget,
        in the order they should appear when nothing's gained by moving
        them. Add the returned layout to the section's own frame, which
        is what gives the blocks their indent under the section's
        title."""
        flow = FlowLayout(h_spacing=_SUBSECTION_GAP, reorder=reorder)
        for block in blocks:
            flow.addWidget(block)
        return flow


    def _build_settings_tab(self) -> QWidget:
        """Settings tab: a scrollable stack of Level 1 sections, each
        with its title on a line of its own at the left and its frame
        (a QGroupBox) below it, built AFTER the Main tab so every
        widget it links against or shares already exists. Inside a
        frame, the Level 2 blocks (and Level 3 ones inside those) sit
        side by side while the window is wide enough for them and
        wrap below each other when it isn't -- each section has a
        FlowLayout for them (see _settings_subsection_flow).

        - Global Settings: Level 2 blocks --
          - Theme: a Dark/Light switch.
          - Options: synced copies of the Main tab's (Quick Options)
            checkboxes (via _link_checkboxes) -- same settings, just also
            reachable here -- plus the options that only exist here.
          - Comparison Options: the comparison type and everything that
            follows from it (hashing, ignore options, hash cache, ...).
          - Misc Colors: GUI, default button, progress and busy bar
            colors.
        - Statistics View (common): Font size / Nice bins / Bin count /
          tick label angle (+ Auto angle), now ONE setting apiece feeding both panes'
          views at once, rather than set per-pane on the Main tab
          (removed from there -- see _build_statistics_panel).
          pane.tl_font_size_spin/tl_nice_bins_cb/tl_bin_count_spin/
          tl_tick_angle_spin/tl_auto_tick_angle_cb on both panes get pointed at these exact
          same shared widgets afterward (end of _build_ui), for anything
          that still expects to find them on the pane.
        - Specific options: synced copies of each pane's own Log height /
          Log / Auto Range -- these stay individual per view (unlike the
          ones above), just with a reachable copy here too.
        - Statistics View Colors: per-mode (file/folder) palette for
          each pane's chart, plus one shared background color -- see
          TimelineOpsMixin._apply_stats_palette.
        - Panel Button Colors: Select-group, Clipboard and
          File Op button colors -- buttons only; the file-list row
          colors for each comparison type (Missing, Matching, ...) are
          their own "Comparison Type Colors" table under File List
          View, independent of the Select buttons.

        Save Settings / Load Settings / Reload Defaults live at the
        bottom of THIS tab now (moved off Global Options -- see there),
        same height as the Main tab's Cancel button, with their own
        status line underneath for save/load confirmation messages --
        separate from the Main tab's transfer_label, since these are two
        unrelated kinds of status now that they don't share a tab. Kept
        outside the scroll area (the returned widget is the scroll area
        plus this row/status line stacked below it, not the scroll area
        itself), so they -- and any confirmation message -- stay visible
        regardless of scroll position rather than requiring a scroll to
        the bottom to reach or see them.
        """
        default_bg, default_fg = self._default_button_colors()
        uniform_w, uniform_h = self._uniform_button_size()

        # Cleared and rebuilt fresh every time this tab is (re)built --
        # see _make_color_swatch/_refresh_color_swatches.
        self._color_swatches = []

        # Every plain "[option label][spin box][slider]" row on this tab
        # (Global Options' thresholds, Indentation Line's Indentation/
        # Line thickness, Line colors' Nr. of Steps) collects its label
        # and spin box here as it's built; once the whole tab exists, a
        # pass right before the final stretch (see below, near "outer.
        # addStretch(1)") fixes every one of them to the same label/spin
        # width -- the widest label/spin actually used -- so their
        # sliders all land at the same x, however far apart their rows
        # actually are on the page. Rows with other structure (a
        # leading checkbox, a color swatch, ...) opt out of this and
        # keep their own local alignment instead -- see each site.
        self._aligned_option_rows = []
        # (block, group, width class) of every Level 2 block that takes part
        # in the width grid -- see _l2_block / _apply_l2_widths.
        self._l2_blocks = []
        self._l2_base_block = None
        # Label/spin box pairs whose labels share one width (Comparison
        # Options, Context Menu, Sync Settings -- the two-thirds class).
        self._sibling_aligned_rows = []

        page = QWidget()
        outer = QVBoxLayout(page)
        # Larger, bold section headers -- QGroupBox's native title is
        # small/plain enough on most platforms/styles that a long
        # scrollable stack of them (this whole tab) reads as one long
        # blur rather than clearly separated sections. Bumped to 18pt
        # (up from an earlier 14pt that wasn't visibly registering --
        # possibly a native-style QSS quirk on QGroupBox::title; if 18pt
        # still doesn't show, the title's font is getting overridden
        # somewhere else entirely and this stylesheet isn't the culprit).
        self._settings_page = page
        page.setStyleSheet(self._settings_page_stylesheet())

        # ---- Theme (Level 2, first block of Global Settings) -- applies
        # a hand-built dark/light QPalette, then
        # rebuilds -- simplest way to guarantee every palette-derived
        # color (e.g. _default_button_colors when not overridden below)
        # actually recomputes everywhere it's used, rather than trying
        # to track down and re-style each one by hand. First on the tab,
        # since it's the one setting here that reshapes every other
        # color on this whole tab, not just its own. ----
        theme_group = QGroupBox("")
        theme_outer = QVBoxLayout(theme_group)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        # One radio button per theme in _THEMES (theme_ops.py) -- adding
        # a theme there is what adds it here too, nothing to keep in
        # sync by hand. self.theme_radios[theme] replaces what used to
        # be separate named attributes (theme_dark_radio, ...) -- if
        # anything outside this method ever needs a specific one, index
        # this dict rather than adding those back.
        self.theme_radios = {}
        theme_button_group = QButtonGroup(theme_group)
        for theme in _THEMES:
            radio = QRadioButton(theme.replace("_", " ").title())
            radio.setChecked(theme == self._theme)
            radio.toggled.connect(lambda checked, t=theme: checked and self._on_theme_changed(t))
            theme_button_group.addButton(radio)
            theme_row.addWidget(radio)
            self.theme_radios[theme] = radio
        theme_row.addStretch(1)
        theme_outer.addLayout(theme_row)
        theme_outer.addWidget(self._make_italic_note(
            "Each theme keeps its own set of colors -- everything on this tab edits the "
            "set of the theme that's currently selected."
        ))

        # ---- Global Settings (Level 1): Theme, Misc Colors and Global
        # Options, in that order, as Level 2 blocks side by side while
        # the window is wide enough and wrapping below each other when
        # it isn't. Global Options and Misc Colors are added to this
        # flow further down, where they're built. ----
        global_settings_group = QGroupBox("")
        global_settings_outer = QVBoxLayout(global_settings_group)
        global_settings_flow = self._settings_subsection_flow()
        global_settings_outer.addLayout(global_settings_flow)
        outer.addWidget(self._settings_section("Global Settings", global_settings_group, space_above=False))
        global_settings_flow.addWidget(self._l2_block("Theme", theme_group, "two_thirds"))

        # ---- Global Options (Level 2 of Global Settings; synced with
        # the Main tab's own group) ----
        # A 4-row grid, laid out so each dependent option sits directly
        # (and indented) below the option that decides its enabled
        # state, rather than all dependents being lined up together in
        # one row: col 0 is Dual Pane and its two dependents
        # (Synchronize Views, Symmetrical GUI); col 2 is Recursive
        # Folder Selections and its one dependent (Expand To
        # Selection), pushed a column to the right (col 1 is a bare
        # spacer) so its column doesn't crowd col 0's now-taller stack;
        # col 3 is List Directories and its one dependent (Sort
        # Directories First). The two width-threshold spinboxes get
        # their own row at the bottom, spanning the full width.
        global_group = QGroupBox("")
        global_outer = QVBoxLayout(global_group)
        global_grid = QGridLayout()
        global_grid.setColumnMinimumWidth(1, 20)
        global_grid.setColumnStretch(6, 1)
        # The comparison type options: built into this grid, shown in their
        # own group (comparison_group, below).
        comparison_grid = QGridLayout()
        comparison_grid.setColumnMinimumWidth(1, 20)
        comparison_grid.setColumnStretch(6, 1)

        def _add_synced(primary, label, row, col, indent=0):
            secondary = QCheckBox(label)
            if primary.toolTip():
                secondary.setToolTip(primary.toolTip())
            self._link_checkboxes(primary, secondary)
            if indent:
                wrapper = QHBoxLayout()
                wrapper.setContentsMargins(0, 0, 0, 0)
                wrapper.addSpacing(indent)
                wrapper.addWidget(secondary)
                global_grid.addLayout(wrapper, row, col)
            else:
                global_grid.addWidget(secondary, row, col)
            return secondary

        # Row 1: independent/decider options.
        _add_synced(self.dual_pane_cb, "Compare folders", 0, 0)
        _add_synced(self.recursive_folder_contents_cb, "Recursive Folder Selections", 0, 2)
        _add_synced(self.list_directories_cb, "List Directories", 0, 3)
        _add_synced(self.list_files_cb, "List Files", 0, 4)

        # Row 1, column 4 (under List Files, not indented): "Use Binary
        # sizes". Only exists on this tab (no Main-tab primary).
        self.use_binary_sizes_cb = QCheckBox("Use Binary sizes")
        self.use_binary_sizes_cb.setChecked(self._binary_sizes)
        self.use_binary_sizes_cb.setToolTip(
            "On: sizes are shown in binary units (1024 bytes = 1 KiB, MiB, GiB, TiB).\n"
            "Off: decimal units (1000 bytes = 1 kB, MB, GB, TB).")
        self.use_binary_sizes_cb.toggled.connect(self._on_binary_sizes_toggled)
        global_grid.addWidget(self.use_binary_sizes_cb, 1, 4)
        self._apply_binary_sizes(self._binary_sizes)

        # Row 2: dependents, each indented below its decider.
        # Symmetrical GUI/Synchronize Views depend on Dual Pane; Expand
        # To Selection depends on Recursive Folder Selections; Sort
        # Directories First depends on List Directories -- same
        # disable-wiring (plus the shared dim-when-disabled style) as
        # their Main-tab primaries where they have one, just applied to
        # these secondary copies too (_link_checkboxes only mirrors
        # checked state, not enabled state).
        symmetrical_secondary = _add_synced(self.symmetrical_buttons_cb, "Symmetrical (ish) GUI", 2, 0)
        symmetrical_secondary.setStyleSheet(_DISABLED_CHECKBOX_STYLE)

        expand_secondary = _add_synced(self.expand_matching_cb, "Expand To Selection", 1, 2, indent=20)
        expand_secondary.setEnabled(self.recursive_folder_contents_cb.isChecked())
        self.recursive_folder_contents_cb.toggled.connect(expand_secondary.setEnabled)
        expand_secondary.setStyleSheet(_DISABLED_CHECKBOX_STYLE)

        sort_dirs_secondary = _add_synced(self.sort_directories_first_cb, "Sort Directories First", 1, 3, indent=20)
        sort_dirs_secondary.setEnabled(self.list_directories_cb.isChecked())
        self.list_directories_cb.toggled.connect(sort_dirs_secondary.setEnabled)
        sort_dirs_secondary.setStyleSheet(_DISABLED_CHECKBOX_STYLE)

        # Duplicate Finder options (DuplicateFinderMixin). No Main-tab
        # primary: these two only exist here. "Scan expanded folders" sits
        # under Expand To Selection (its sibling dependent of Recursive
        # Folder Selections, but enabled when that is OFF); "Hash only
        # same-size files" is placed in the Comparison Options group.
        self.scan_expanded_cb = QCheckBox("Scan expanded folders")
        self.scan_expanded_cb.setChecked(self._scan_expanded)
        self.scan_expanded_cb.setToolTip(
            "Duplicate Finder mode with Recursive Folder Selections off: only the "
            "files in the top folder and in the folders you have expanded are "
            "searched for duplicates.\n"
            "On: expanding or collapsing a folder updates the search at once.\n"
            "Off: the search keeps the folders that were expanded at the last "
            "Refresh / Hash / folder load, until the next one."
        )
        self.scan_expanded_cb.setEnabled(not self.recursive_folder_contents_cb.isChecked())
        self.recursive_folder_contents_cb.toggled.connect(lambda on: self.scan_expanded_cb.setEnabled(not on))
        self.scan_expanded_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        scan_wrapper = QHBoxLayout()
        scan_wrapper.setContentsMargins(0, 0, 0, 0)
        scan_wrapper.addSpacing(20)
        scan_wrapper.addWidget(self.scan_expanded_cb)
        global_grid.addLayout(scan_wrapper, 2, 2)

        # Row 3, under Sort Directories First (same column, same indent): the
        # folder-count option. Only exists on this tab (no Main-tab primary).
        self.folder_counts_include_subfolders_cb = QCheckBox("Folder counts include subfolders")
        self.folder_counts_include_subfolders_cb.setChecked(self._folder_counts_include_subfolders)
        self.folder_counts_include_subfolders_cb.setToolTip(
            "The \"N items\" shown for a folder in the File List View. Off: only the "
            "folder's own entries. On: everything in it at any depth, files and "
            "folders alike. Either way only what the File Filter and List "
            "Directories / List Files let through is counted."
        )
        self.folder_counts_include_subfolders_cb.toggled.connect(self._on_folder_counts_subfolders_toggled)
        folder_counts_wrapper = QHBoxLayout()
        folder_counts_wrapper.setContentsMargins(0, 0, 0, 0)
        folder_counts_wrapper.addSpacing(20)
        folder_counts_wrapper.addWidget(self.folder_counts_include_subfolders_cb)
        global_grid.addLayout(folder_counts_wrapper, 2, 3)
        # Folder rows only exist while List Directories is on -- same
        # enable-wiring and dim-when-disabled style as Sort Directories First.
        self.folder_counts_include_subfolders_cb.setEnabled(self.list_directories_cb.isChecked())
        self.list_directories_cb.toggled.connect(self.folder_counts_include_subfolders_cb.setEnabled)
        self.folder_counts_include_subfolders_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)

        self.hash_same_size_cb = QCheckBox("Hash only same-size files")
        self.hash_same_size_cb.setChecked(self._hash_same_size)
        self.hash_same_size_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        self.hash_same_size_cb.setToolTip(
            "Duplicate Finder mode with Hash value / Fingerprint: files with a "
            "size no other file in the search has cannot be duplicates, so they "
            "are not hashed (they keep the plain file color). Saves time on big "
            "folders. Off: every file in the search is hashed.\n"
            "Compare folders mode always hashes every file in view."
        )
        # Shown in the Comparison Options group (it only matters for the hash
        # types), to the right of the hash algorithm.
        self.scan_expanded_cb.toggled.connect(self._on_dup_scope_option_changed)
        self.hash_same_size_cb.toggled.connect(self._on_dup_scope_option_changed)

        # Row 2, column 0: Synchronize Views (indented under Compare
        # folders); Symmetrical (ish) GUI sits below it, not indented.
        sync_secondary = _add_synced(self.synchronize_views_cb, "Synchronize Views", 1, 0, indent=20)
        sync_secondary.setEnabled(self.dual_pane_cb.isChecked())
        self.dual_pane_cb.toggled.connect(sync_secondary.setEnabled)
        sync_secondary.setStyleSheet(_DISABLED_CHECKBOX_STYLE)

        # Row 4: Unnatural scrolling -- its own independent option, not
        # indented/dependent on anything above (unlike Synchronize Views
        # just above it, which IS indented under Dual Pane). Invert
        # scrollwheel zoom direction sits next to it, same row -- also
        # independent, and deliberately placed alongside Unnatural
        # scrolling since the two are meant to be combinable (see either
        # one's tooltip).
        # A blank line (row 3) sets the two scroll options apart from the
        # options above them.
        scroll_gap = QWidget()
        scroll_gap.setFixedHeight(QFontMetrics(self.font()).height())
        global_grid.addWidget(scroll_gap, 3, 0, 1, 7)
        _add_synced(self.unnatural_scrolling_cb, "Unnatural scrolling", 4, 0)
        _add_synced(self.invert_statistics_zoom_cb, "Invert scrollwheel zoom direction", 4, 2)

        # Row 7: File List View folder-open history -- how many entries
        # each pane's entry combo box keeps (see panes.DirectoryPane.
        # folder_history / SettingsMixin._record_folder_history), plus a
        # button to wipe both panes' history outright. No Main-tab
        # primary/_link_spinboxes here -- same as flv_font_size_spin/
        # tree_indentation_spin above, this control only exists on this
        # tab. Joins _aligned_option_rows so its slider lands at the same
        # x as the two threshold rows just above (its label's short
        # enough now, after the "File List View History" rename, not to
        # blow that alignment out for every other row sharing it).
        history_gap = QWidget()
        history_gap.setFixedHeight(6)
        global_grid.addWidget(history_gap, 7, 0, 1, 7)

        history_row = QHBoxLayout()
        history_row.setContentsMargins(0, 0, 0, 0)
        self.file_list_history_limit_spin = SliderSpinBox()
        self.file_list_history_limit_spin.setRange(0, 100)
        self.file_list_history_limit_spin.setValue(self._file_list_history_limit)
        self.file_list_history_limit_spin.setToolTip(
            "How many recently opened folders each pane's drop-down keeps, "
            "most recent first. 0 turns history off entirely."
        )
        self.file_list_history_limit_spin.valueChanged.connect(self._on_file_list_history_limit_changed)
        history_label = QLabel("File List View History:")
        history_row.addWidget(spin_slider_inline(
            history_label, self.file_list_history_limit_spin,
            SpinSlider(self.file_list_history_limit_spin, live=False),
        ), 0, Qt.AlignVCenter)
        history_row.addSpacing(20)
        clear_history_button = QPushButton("Clear history")
        clear_history_button.setToolTip("Removes every entry from both panes' folder-history drop-downs.")
        clear_history_button.clicked.connect(self._on_clear_folder_history)
        history_row.addWidget(clear_history_button)
        history_row.addStretch(1)
        self._aligned_option_rows.append((history_label, self.file_list_history_limit_spin))
        global_grid.addLayout(history_row, 8, 0, 1, 7)

        # Rows 11-14: how two files are judged "the same" (see
        # DiffEngineMixin._comparison_key). Shared by both panes, no
        # Main-tab primary. The state lives in self._cmp_* (so it
        # survives a UI rebuild); these widgets are built from it and
        # write back through _on_comparison_setting_changed.
        comparison_type_row = QHBoxLayout()
        comparison_type_row.setContentsMargins(0, 0, 0, 0)
        comparison_type_row.addWidget(QLabel("Comparison type:"))
        self.comparison_type_combo = QComboBox()
        for type_id, label in COMPARISON_TYPE_ITEMS:
            self.comparison_type_combo.addItem(label, type_id)
        self.comparison_type_combo.setCurrentIndex(max(0, self.comparison_type_combo.findData(self._cmp_type)))
        self.comparison_type_combo.setToolTip(
            "How a file is judged to match its counterpart in the other pane.\n"
            "Name, size and date: same name, size and modification date (the "
            "classic comparison).\n"
            "Name and size: same name and size; dates are not compared.\n"
            "Name: same name only; size and dates are not compared (e.g. to see "
            "which files belong together).\n"
            "Hash value: same file contents (a hash of the whole file).\n"
            "Fingerprint: same file size plus the first and last chunk of "
            "the contents (much faster for big files).\n"
            "Both need the files hashed first (the Hash button); files "
            "that are not hashed yet cannot be compared by content."
        )
        comparison_type_row.addWidget(self.comparison_type_combo)
        comparison_type_row.addStretch(1)
        comparison_grid.addLayout(comparison_type_row, 12, 0, 1, 7)

        hash_row = QHBoxLayout()
        hash_row.setContentsMargins(0, 0, 0, 0)
        hash_row.addSpacing(20)
        self.hash_algorithm_label = QLabel("Hash algorithm:")
        hash_row.addWidget(self.hash_algorithm_label)
        self.hash_algorithm_combo = QComboBox()
        self.hash_algorithm_combo.addItems(HASH_ALGORITHMS)
        self.hash_algorithm_combo.setCurrentIndex(self._cmp_algorithm)
        self.hash_algorithm_combo.setToolTip("The hash function used by the Hash value and Fingerprint types.")
        hash_row.addWidget(self.hash_algorithm_combo)
        hash_row.addSpacing(10)
        hash_row.addWidget(self.hash_same_size_cb)
        hash_row.addStretch(1)
        comparison_grid.addLayout(hash_row, 14, 0, 1, 7)

        def _chunk_row(attr, label_text, value, tip):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addSpacing(20)
            spin = SliderSpinBox()
            spin.setRange(MIN_FINGERPRINT_CHUNK_KB, MAX_FINGERPRINT_CHUNK_KB)
            spin.setSuffix(" kB")
            spin.setValue(value)
            spin.setToolTip(tip)
            setattr(self, attr, spin)
            label = QLabel(label_text)
            row.addWidget(spin_slider_inline(label, spin, SpinSlider(spin, live=False)), 0, Qt.AlignVCenter)
            row.addStretch(1)
            return row, label

        fp_start_row, self.fingerprint_start_label = _chunk_row(
            "fingerprint_start_spin", "Fingerprint start chunk:", self._cmp_chunk_start_kb,
            "How much of the beginning of each file the Fingerprint type reads. "
            "A file smaller than both chunks together is read whole.")
        fp_end_row, self.fingerprint_end_label = _chunk_row(
            "fingerprint_end_spin", "Fingerprint end chunk:", self._cmp_chunk_end_kb,
            "How much of the end of each file the Fingerprint type reads. "
            "A file smaller than both chunks together is read whole.")
        # Same label width so both spin boxes and sliders line up.
        _lw = max(self.fingerprint_start_label.sizeHint().width(),
                  self.fingerprint_end_label.sizeHint().width())
        self.fingerprint_start_label.setFixedWidth(_lw)
        self.fingerprint_end_label.setFixedWidth(_lw)
        # Same label column as Context Menu / Sync Settings (same width class).
        self._sibling_aligned_rows.append((self.fingerprint_start_label, self.fingerprint_start_spin))
        self._sibling_aligned_rows.append((self.fingerprint_end_label, self.fingerprint_end_spin))
        comparison_grid.addLayout(fp_start_row, 16, 0, 1, 7)
        comparison_grid.addLayout(fp_end_row, 17, 0, 1, 7)

        ignore_ext_row = QHBoxLayout()
        ignore_ext_row.setContentsMargins(0, 0, 0, 0)
        ignore_ext_row.addSpacing(20)
        self.ignore_file_extensions_cb = QCheckBox("Ignore file extensions")
        self.ignore_file_extensions_cb.setChecked(self._cmp_ignore_ext)
        self.ignore_file_extensions_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        self.ignore_file_extensions_cb.setToolTip(
            "Name-based comparison types only: files match by name without the "
            "extension (a.txt matches a.doc); what else is compared (size, "
            "date) follows the comparison type. A file matches if any file in "
            "the other folder has the same name minus extension. Folders still "
            "match by full name."
        )
        ignore_ext_row.addWidget(self.ignore_file_extensions_cb)
        ignore_ext_row.addSpacing(20)
        self.first_name_only_cb = QCheckBox("Only first part of file name")
        self.first_name_only_cb.setChecked(self._cmp_first_name)
        self.first_name_only_cb.setStyleSheet(_DISABLED_CHECKBOX_STYLE)
        self.first_name_only_cb.setToolTip(
            "Name-based comparison types only: files match by the part of the "
            "name before the first dot (a.tar.gz matches a.txt and a.bak.zip), "
            "so names with several extensions match too; what else is compared "
            "(size, date) follows the comparison type. Cannot be combined with "
            "Ignore file extensions."
        )
        ignore_ext_row.addWidget(self.first_name_only_cb)
        ignore_ext_row.addStretch(1)
        comparison_grid.addLayout(ignore_ext_row, 13, 0, 1, 7)

        # Row 18: files left out of every comparison (DiffEngineMixin.
        # _ignored_kind), in both Dual Pane and single pane mode and with
        # every comparison type.
        ignore_row = QHBoxLayout()
        ignore_row.setContentsMargins(0, 0, 0, 0)
        ignore_row.addSpacing(20)
        self.ignore_empty_files_cb = QCheckBox("Ignore empty files")
        self.ignore_empty_files_cb.setChecked(self._ignore_empty)
        self.ignore_empty_files_cb.setToolTip(
            "Files of 0 bytes take no part in any comparison, selection or "
            "hashing: they are neither missing, matching nor different (and "
            "never a duplicate of each other). They keep the plain file colors."
        )
        ignore_row.addWidget(self.ignore_empty_files_cb)
        ignore_row.addSpacing(20)
        self.ignore_hard_links_cb = QCheckBox("Ignore hard links")
        self.ignore_hard_links_cb.setChecked(self._ignore_hardlinks)
        self.ignore_hard_links_cb.setToolTip(
            "Files that have more than one hard link (link count above 1) take "
            "no part in any comparison, selection or hashing, and show the Hard "
            "link colors (Default / Misc colors). A file whose other link is "
            "outside the opened folders counts too.\n"
            "Off: two links to one file are reported as identical, although "
            "deleting one of them frees no space."
        )
        ignore_row.addWidget(self.ignore_hard_links_cb)
        ignore_row.addStretch(1)
        comparison_grid.addLayout(ignore_row, 18, 0, 1, 7)
        self.ignore_empty_files_cb.toggled.connect(self._on_comparison_setting_changed)
        self.ignore_hard_links_cb.toggled.connect(self._on_comparison_setting_changed)

        # Row 19: the persistent hash cache (HashOpsMixin / hash_cache_store).
        cache_row = QHBoxLayout()
        cache_row.setContentsMargins(0, 0, 0, 0)
        cache_row.addSpacing(20)
        self.cache_hashed_folders_cb = QCheckBox("Cache hashed folders")
        self.cache_hashed_folders_cb.setChecked(self._cache_hashed_folders)
        self.cache_hashed_folders_cb.setToolTip(
            "Hash value / Fingerprint only: remember computed hashes between "
            "sessions, so files that are unchanged (same size and date) need "
            "not be hashed again. Only hashed and fingerprinted files are "
            "cached; with Name, size and date nothing is read or written.\n"
            "The cache is one database file holding the file names and hashes "
            "of the folders you hashed (Cache/hashes.sqlite in the program "
            "folder, else your user cache folder). Opening a folder never "
            "writes to it."
        )
        cache_row.addWidget(self.cache_hashed_folders_cb)
        cache_row.addSpacing(20)
        clear_cache_button = QPushButton("Clear cache")
        clear_cache_button.setToolTip(
            "Deletes every stored hash (the cache file's contents) and the "
            "hashes held in memory, so open folders show as unhashed.")
        clear_cache_button.clicked.connect(self._on_clear_hash_cache)
        cache_row.addWidget(clear_cache_button)
        cache_row.addStretch(1)
        comparison_grid.addLayout(cache_row, 19, 0, 1, 7)
        self.cache_hashed_folders_cb.toggled.connect(self._on_cache_hashed_folders_toggled)

        # Row 20: which copy "Extra copies" leaves alone in every group of
        # identical files (Duplicate Finder mode, DuplicateFinderMixin).
        keep_row = QHBoxLayout()
        keep_row.setContentsMargins(0, 0, 0, 0)
        keep_row.addSpacing(20)
        keep_row.addWidget(QLabel("Extra copies keep:"))
        self.extra_copies_keep_combo = QComboBox()
        self.extra_copies_keep_combo.addItems(
            ["Shortest path", "Oldest", "Newest", "First in tree order"])
        self.extra_copies_keep_combo.setCurrentIndex(self._dup_survivor)
        self.extra_copies_keep_combo.setToolTip(
            "Duplicate Finder mode: the \"Extra copies\" button selects every "
            "copy in a group of identical files except one. This decides which "
            "one is left alone:\n"
            "Shortest path: the copy with the shortest full path (the first "
            "of them in tree order on a tie).\n"
            "Oldest / Newest: the copy with the oldest / newest modified date.\n"
            "First in tree order: the copy listed first in the file list."
        )
        keep_row.addWidget(self.extra_copies_keep_combo)
        keep_row.addStretch(1)
        comparison_grid.addLayout(keep_row, 20, 0, 1, 7)
        self.extra_copies_keep_combo.currentIndexChanged.connect(self._on_dup_survivor_changed)

        self._update_comparison_controls_enabled()
        self._cmp_applied_key = self._comparison_key()
        self._ignore_applied = self._ignore_flags()
        self.comparison_type_combo.currentIndexChanged.connect(self._on_comparison_setting_changed)
        self.hash_algorithm_combo.currentIndexChanged.connect(self._on_comparison_setting_changed)
        self.fingerprint_start_spin.valueChanged.connect(self._on_comparison_setting_changed)
        self.fingerprint_end_spin.valueChanged.connect(self._on_comparison_setting_changed)
        self.ignore_file_extensions_cb.toggled.connect(
            lambda checked: self._on_name_match_option_toggled(self.first_name_only_cb, checked))
        self.first_name_only_cb.toggled.connect(
            lambda checked: self._on_name_match_option_toggled(self.ignore_file_extensions_cb, checked))

        global_outer.addLayout(global_grid)

        # The comparison type options (from "Comparison type:" down) are a
        # group of their own, beside/after Options in the Global Settings flow.
        comparison_group = QGroupBox("")
        comparison_outer = QVBoxLayout(comparison_group)
        comparison_outer.addLayout(comparison_grid)

        # ---- Default / Out-of-Group Colors -- a real QTableWidget (see
        # _style_table_widget/_size_table_to_contents), not the
        # QGridLayout-of-QLabels-and-swatches every one of these tables
        # used to be -- converted first since Misc Colors is where
        # the mismatched-cell-height/empty-cell inconsistency that
        # motivated the switch was most visible, between Busy Bar's
        # missing Bg swatch and the Title Numeric Colors row's odd
        # Fg-swatch/Bold-checkbox shape. Same Item | Fg | Bg column
        # convention as Panel Button Colors below, and every
        # other Colors & Theme table has since been converted the same
        # way (Select Buttons, Statistics View Colors/Buttons, Clipboard
        # Buttons/File Op Buttons). A row with only one real color
        # (Busy Bar) just leaves its Background cell empty -- a real
        # QTableWidget grid line still runs across it either way,
        # instead of needing a hand-placed blank-QLabel placeholder to
        # fake one.
        #
        # Each row supplies a get_fg/get_bg callable (re-derived from
        # live state every time it's called) rather than a frozen value
        # -- see _make_color_swatch -- so these swatches stay correct
        # after load_settings/"Reload Defaults" change this state
        # in bulk, not just after a click here. ----
        _DEFAULT_COLOR_ROWS = [
            ("GUI",
             lambda: self._effective_gui_colors(self._theme)[0],
             lambda: self._effective_gui_colors(self._theme)[1],
             lambda hexc: self._on_gui_color_changed(True, hexc),
             lambda hexc: self._on_gui_color_changed(False, hexc)),
            ("Default Button",
             lambda: (self._custom_default_button_colors or self._default_button_colors())[1],
             lambda: (self._custom_default_button_colors or self._default_button_colors())[0],
             lambda hexc: self._on_default_button_color_changed(True, hexc),
             lambda hexc: self._on_default_button_color_changed(False, hexc)),
            ("Progress Bar",
             lambda: self._progress_bar_colors["fg"], lambda: self._progress_bar_colors["bg"],
             lambda hexc: self._on_progress_bar_color_changed("fg", hexc),
             lambda hexc: self._on_progress_bar_color_changed("bg", hexc)),
            ("Busy Bar",
             lambda: self._progress_bar_colors["busy"], None,
             lambda hexc: self._on_progress_bar_color_changed("busy", hexc), None),
            # Title Numeric moved here from File List View Colors: it colors
            # the numbers in titles all over the Main tab (item counts, sizes,
            # the "Free:" figure), not just the file lists. Its Bg cell holds
            # the Bold checkbox instead of a swatch -- built specially below.
            ("Numeric titles", None, None, None, None),
            # The two colors of each pane's status-line summary at the bottom
            # of the Main tab (see SelectionMixin.update_selection_count) --
            # Fg only. Unselected shows the GUI's own text color until one is
            # picked.
            ("Status line Selected info",
             lambda: self._status_selected_color, None,
             self._on_status_selected_color_changed, None),
            ("Status line Unselected",
             lambda: self._status_unselected_color or self._effective_gui_colors(self._theme)[0], None,
             self._on_status_unselected_color_changed, None),
            # Main tab -> Global Options' Refresh button. The theme's
            # default button colors until one is picked.
            ("Refresh (global)",
             lambda: self._main_button_color_for("refresh")[1],
             lambda: self._main_button_color_for("refresh")[0],
             lambda hexc: self._on_main_button_color_changed("refresh", False, hexc),
             lambda hexc: self._on_main_button_color_changed("refresh", True, hexc)),
            ("Status line warning",
             lambda: self._status_warning_color, None,
             self._on_status_warning_color_changed, None),
            # Text colors of the folder-history drop-downs (Fg only).
            ("History: live folder",
             lambda: self._history_live_color, None,
             lambda hexc: self._on_history_color_changed(False, hexc), None),
            ("History: snapshot file",
             lambda: self._history_snapshot_color, None,
             lambda hexc: self._on_history_color_changed(True, hexc), None),
            # Edge of the Main tab's button groups (Fg only).
            ("Group border",
             lambda: self._group_border_color, None,
             self._on_group_border_color_changed, None),
        ]
        default_colors_table = QTableWidget(len(_DEFAULT_COLOR_ROWS), 3)
        self._style_table_widget(default_colors_table, ["Item", "Fg", "Bg"])

        for i, (label, get_fg, get_bg, on_fg, on_bg) in enumerate(_DEFAULT_COLOR_ROWS):
            default_colors_table.setItem(i, 0, self._table_item(label))
            if label == "Numeric titles":
                default_colors_table.setCellWidget(
                    i, 1,
                    self._center_in_cell(self._make_color_swatch(
                        lambda: self._title_numeric_color, self._on_title_numeric_color_changed,
                    )),
                )
                # Just "B" (in bold): the Bg column is only as wide as a swatch.
                title_numeric_bold_cb = QCheckBox("B")
                title_numeric_bold_cb.setStyleSheet("font-weight: bold;")
                title_numeric_bold_cb.setToolTip(
                    "Show the numeric values in the titles (item counts, sizes, "
                    "file and folder totals, free space) in bold. When off, they're "
                    "shown in normal weight."
                )
                title_numeric_bold_cb.setChecked(self._title_numeric_bold)
                title_numeric_bold_cb.toggled.connect(self._on_title_numeric_bold_changed)
                self.title_numeric_bold_cb = title_numeric_bold_cb
                default_colors_table.setCellWidget(i, 2, self._center_in_cell(title_numeric_bold_cb))
                continue
            default_colors_table.setCellWidget(i, 1, self._center_in_cell(self._make_color_swatch(get_fg, on_fg)))
            if on_bg is not None:
                default_colors_table.setCellWidget(
                    i, 2, self._center_in_cell(self._make_color_swatch(get_bg, on_bg))
                )
        default_colors_table.item(0, 0).setToolTip(
            "Text and background color of the whole GUI, for the current theme "
            "(the file lists' and text fields' background follows the Bg)."
        )
        default_colors_table.item(4, 0).setToolTip(
            "Color of the numbers in the titles (item counts, sizes, file and "
            "folder totals, free space). The Bold option is in the Bg column."
        )
        default_colors_table.item(5, 0).setToolTip(
            "Text color of each pane's status line at the bottom of the window "
            "while something is selected (\"N selected, X files, Y folders, size\")."
        )
        default_colors_table.item(6, 0).setToolTip(
            "Text color of that status line while nothing is selected, and of "
            "the operation messages shown there. Follows the GUI text color "
            "until you pick one."
        )
        default_colors_table.item(7, 0).setToolTip(
            "The Refresh button under Quick Options on the Main tab. Uses the "
            "theme's default button colors until you pick one."
        )
        default_colors_table.item(8, 0).setToolTip(
            "Text color of a warning in a pane's status line, e.g. when a loaded snapshot "
            "cannot be compared by hash and the comparison falls back to name, size and date."
        )
        default_colors_table.item(9, 0).setToolTip(
            "Text color of live-folder entries in each pane's folder-history drop-down."
        )
        default_colors_table.item(10, 0).setToolTip(
            "Text color of folder-snapshot (.fss) entries in each pane's folder-history drop-down."
        )
        default_colors_table.item(11, 0).setToolTip(
            "Color of the edge around the Main tab's button groups (Select, File Op, "
            "Clipboard, Both Panes, Quick Options, Global File Filter)."
        )
        # (Default Folder/Default File and Scrollbar used to be rows here --
        # they moved to File List View -> File List View Colors below,
        # since they only color the file lists. Title Numeric started in
        # File List View Colors and is back here.)

        self._size_table_to_contents(default_colors_table)
        # Wrapped in a QGroupBox like every other table's subsection
        # (Comparison Type Colors, Select Buttons, ...) -- handed straight
        # to _settings_subsection it sat unframed and flush-left, unlike
        # them.
        default_colors_group = QGroupBox("")
        default_colors_group_outer = QVBoxLayout(default_colors_group)
        default_colors_group_outer.addWidget(default_colors_table)

        # Level 2 blocks of Global Settings: Theme, Options, Comparison Options, Misc Colors.
        options_block = self._l2_block("Options", global_group, "full")
        self._l2_base_block = options_block  # its width is the grid's base (L2BW)
        global_settings_flow.addWidget(options_block)
        global_settings_flow.addWidget(self._l2_block("Comparison Options", comparison_group, "two_thirds"))
        # Context Menu and Sync Settings have the same kind of rows: their
        # labels, spin boxes and sliders, and the two groups themselves,
        # are made equally wide below (see _align_sibling_groups) so that
        # they line up when they sit above each other.
        context_menu_block = self._l2_block("Context Menu", self._build_context_menu_group(), "two_thirds")
        sync_settings_block = self._l2_block("Sync Settings", self._build_sync_settings_group(), "two_thirds")
        global_settings_flow.addWidget(context_menu_block)
        global_settings_flow.addWidget(sync_settings_block)
        # File type list: the entries of the Main tab's Global File Filter
        # drop-down (this used to be the File Filter tab).
        global_settings_flow.addWidget(
            self._l2_block("File type list", self._build_file_filter_group(), "third")
        )
        global_settings_flow.addWidget(self._l2_block("Misc Colors", default_colors_group, "third"))

        # ---- Misc Buttons (Level 2 of Global Settings, after Misc Colors):
        # colors of the Main tab's mode switch (Duplicate finder / Compare
        # Folders -- released and pressed-in, i.e. the active mode) and its
        # Swap pane button. Same Item | Fg | Bg swatch table as the other
        # button tables; kept per theme in self._main_button_colors. The
        # released/Swap rows start at the theme's default button colors. ----
        misc_buttons_group = self._main_button_color_table([
            ("mode_unpressed", "Mode switch (released)"),
            ("mode_pressed", "Mode switch (pressed)"),
            ("filter_clear", "File Filter: Clear"),
            ("filter_apply", "File Filter: Apply"),
        ])
        global_settings_flow.addWidget(self._l2_block("Misc Buttons", misc_buttons_group, "third"))

        # ---- File List View (Settings-tab only -- no Main-tab copy,
        # unlike everything else in this section) ----
        date_col_group = QGroupBox("")
        date_col_outer = QVBoxLayout(date_col_group)

        # "General" (Level 2, always first in this section): font size
        # and the date column option.
        flv_general_group = QGroupBox("")
        flv_general_outer = QVBoxLayout(flv_general_group)

        # Row 1: font size of both file lists (per theme; see
        # _apply_flv_font).
        flv_font_row = QHBoxLayout()
        self.flv_font_size_label = QLabel("Font size:")
        self.flv_font_size_spin = SliderSpinBox()
        self.flv_font_size_spin.setRange(6, 32)
        self.flv_font_size_spin.setValue(self._default_flv_font_pt())
        self.flv_font_size_spin.setToolTip(
            "Point size of the text in both panes' File List View (rows and column "
            "headers). The default is the application's own font size."
        )
        self.flv_font_size_spin.valueChanged.connect(self._on_flv_font_size_changed)
        # Not live: re-fonting both (possibly huge) file lists on every step
        # of a drag would stall it -- applied on release. In the aligned
        # rows, so its spin box and slider line up with Selection opacity's.
        flv_font_row.addWidget(spin_slider_inline(
            self.flv_font_size_label, self.flv_font_size_spin,
            SpinSlider(self.flv_font_size_spin, live=False),
        ), 0, Qt.AlignVCenter)
        flv_font_row.addStretch(1)
        self._aligned_option_rows.append((self.flv_font_size_label, self.flv_font_size_spin))
        flv_general_outer.addLayout(flv_font_row)
        # The trees were built before this spin box existed, at the
        # application's own size -- bring them to whatever it holds now
        # (same as the Statistics View's font size is applied further
        # down).
        self._apply_flv_font(self.flv_font_size_spin.value())

        # Row 2
        date_col_row = QHBoxLayout()
        self.force_single_date_column_cb = QCheckBox("Use Only 1 Date Column")
        self.force_single_date_column_cb.setToolTip(
            "Always collapse the file list's 3 date columns down to just the one "
            "matching each pane's own Statistics-view Metric, regardless of window "
            "width."
        )
        self.force_single_date_column_cb.setChecked(self._force_single_date_column)
        self.force_single_date_column_cb.toggled.connect(self._on_force_single_date_column_changed)
        date_col_row.addWidget(self.force_single_date_column_cb)
        date_col_row.addStretch(1)
        flv_general_outer.addLayout(date_col_row)

        # The section's Level 2 blocks (Indentation Line, File List View
        # Colors, Comparison Type Colors, in that order), side by side
        # while they fit and wrapping below each other when they don't
        # -- added to this flow where they're built below (File List
        # View Colors' block only once Indentation Line's is in, since
        # that one is built further down).
        flv_flow = self._settings_subsection_flow()
        date_col_outer.addLayout(flv_flow)
        flv_flow.addWidget(self._l2_block("General", flv_general_group, "two_thirds"))

        # ---- Row 3: File List View Colors -- same Item | Fg | Bg
        # table shape as Misc Colors (see there); a row with only one
        # real color leaves its other cell empty. Rows: Default
        # Background (the lists' own background),
        # Default Selection (the selected-row highlight; a selected row's
        # text color is its Default File/Folder Fg, so there's no Fg
        # here), Default File, Default Folder and the lists' scrollbars.
        _FLV_COLOR_ROWS = [
            ("Background (Default)",
             None, lambda: self._effective_flv_bg(),
             None, self._on_flv_bg_color_changed),
            ("Selection (Default)",
             None, lambda: self._selection_tint_color,
             None, self._on_selection_tint_color_changed),
            ("File (Default)",
             lambda: TAG_COLORS.get("file", (None, None))[1] or "#000000",
             lambda: TAG_COLORS.get("file", (None, None))[0] or "#FFFFFF",
             lambda hexc: self._on_default_file_color_changed(True, hexc),
             lambda hexc: self._on_default_file_color_changed(False, hexc)),
            ("Folder (Default)",
             lambda: self._folder_color[0], lambda: self._folder_color[1],
             lambda hexc: self._on_folder_color_changed(True, hexc),
             lambda hexc: self._on_folder_color_changed(False, hexc)),
            ("Hard link",
             lambda: self._tag_color_hex("hardlink_file", True),
             lambda: self._tag_color_hex("hardlink_file", False),
             lambda hexc: self._on_comparison_color_changed("hardlink_file", True, hexc),
             lambda hexc: self._on_comparison_color_changed("hardlink_file", False, hexc)),
            ("Empty folder",
             lambda: self._tag_color_hex("empty_dir_cmp", True),
             lambda: self._tag_color_hex("empty_dir_cmp", False),
             lambda hexc: self._on_comparison_color_changed("empty_dir_cmp", True, hexc),
             lambda hexc: self._on_comparison_color_changed("empty_dir_cmp", False, hexc)),
            ("Empty folder (Duplicate Finder)",
             lambda: self._tag_color_hex("empty_dir", True),
             lambda: TAG_COLORS.get("empty_dir", (None, None))[0] or self._effective_flv_bg(),
             lambda hexc: self._on_comparison_color_changed("empty_dir", True, hexc),
             lambda hexc: self._on_comparison_color_changed("empty_dir", False, hexc)),
            ("Folder (Duplicate Finder)",
             lambda: self._tag_color_hex("dupfolder", True),
             lambda: TAG_COLORS.get("dupfolder", (None, None))[0] or self._effective_flv_bg(),
             lambda hexc: self._on_comparison_color_changed("dupfolder", True, hexc),
             lambda hexc: self._on_comparison_color_changed("dupfolder", False, hexc)),
            ("Scrollbar (Handle/Track)",
             lambda: _SCROLLBAR_COLORS["handle"], lambda: _SCROLLBAR_COLORS["groove"],
             lambda hexc: self._on_scrollbar_color_changed("handle", hexc),
             lambda hexc: self._on_scrollbar_color_changed("groove", hexc)),
        ]
        flv_colors_table = QTableWidget(len(_FLV_COLOR_ROWS), 3)
        self._style_table_widget(flv_colors_table, ["Item", "Fg", "Bg"])
        for i, (label, get_fg, get_bg, on_fg, on_bg) in enumerate(_FLV_COLOR_ROWS):
            flv_colors_table.setItem(i, 0, self._table_item(label))
            if on_fg is not None:
                flv_colors_table.setCellWidget(i, 1, self._center_in_cell(self._make_color_swatch(get_fg, on_fg)))
            if on_bg is not None:
                flv_colors_table.setCellWidget(i, 2, self._center_in_cell(self._make_color_swatch(get_bg, on_bg)))
        flv_colors_table.item(0, 0).setToolTip(
            "Background of the file lists. Follows the theme's list background "
            "until you pick a color here."
        )
        flv_colors_table.item(1, 0).setToolTip(
            "Highlight color of selected rows (see Selection opacity "
            "below). The text color of a selected row is its File / Folder (Default) "
            "Fg."
        )
        flv_colors_table.item(4, 0).setToolTip(
            "Files that have more than one hard link, while Options -> Ignore "
            "hard links is on."
        )
        flv_colors_table.item(5, 0).setToolTip(
            "Compare folders mode: a folder with no files anywhere below it "
            "that is either the same on both sides (Matching) or has nothing to "
            "compare against. Missing and differing folders keep their own "
            "colors. Display only: the Select buttons still see such a folder "
            "as Matching."
        )
        flv_colors_table.item(6, 0).setToolTip(
            "Duplicate Finder mode (Compare folders off) only: a folder with no "
            "files anywhere below it. Its background follows the file list's own "
            "(Background (Default)) until you pick one here."
        )
        flv_colors_table.item(7, 0).setToolTip(
            "Duplicate Finder mode (Compare folders off) only: the color of a "
            "plain folder. Its background follows the file list's own "
            "(Background (Default)) until you pick one here, so folders do not "
            "look like highlighted finds next to the duplicate groups."
        )
        self._size_table_to_contents(flv_colors_table)
        flv_colors_group = QGroupBox("")
        flv_colors_group_outer = QVBoxLayout(flv_colors_group)
        flv_colors_group_outer.addWidget(flv_colors_table)

        # ---- Duplicate File Colors (Level 3, at the bottom of File List View -> Default / Misc colors):
        # Duplicate Finder mode gives each set of identical files its own
        # background, from a color-wheel range like the indentation lines'
        # (Nr. of steps, Color 1/2, Swap, Reverse) -- without the hover
        # color. The text color is its own choice: a fixed Fg color, the
        # background's inverse color, or black/white by its luminance. ----
        dup_group = QGroupBox("")
        dup_outer = QVBoxLayout(dup_group)
        dup_steps_row = QHBoxLayout()
        self.dup_steps_label = QLabel("Nr. of Steps:")
        self.dup_steps_spin = SliderSpinBox()
        self.dup_steps_spin.setRange(1, 32)
        self.dup_steps_spin.setValue(self._dup_steps)
        self.dup_steps_spin.setToolTip(
            "How many different colors the sets of duplicate files cycle through. "
            "With more sets than steps the colors repeat."
        )
        self.dup_steps_spin.valueChanged.connect(self._on_dup_steps_changed)
        dup_steps_row.addWidget(spin_slider_inline(
            self.dup_steps_label, self.dup_steps_spin, SpinSlider(self.dup_steps_spin),
        ), 0, Qt.AlignVCenter)
        dup_steps_row.addStretch(1)
        dup_outer.addLayout(dup_steps_row)
        self._aligned_option_rows.append((self.dup_steps_label, self.dup_steps_spin))

        dup_grid = QGridLayout()
        dup_grid.setHorizontalSpacing(6)
        dup_grid.setColumnStretch(6, 1)
        dup_grid.addWidget(QLabel("Color 1:"), 0, 0, Qt.AlignVCenter)
        self.dup_color1_swatch = self._make_color_swatch(
            lambda: self._dup_color1, lambda hexc: self._on_dup_color_changed(1, hexc))
        dup_grid.addWidget(self.dup_color1_swatch, 0, 1, Qt.AlignVCenter)
        self.dup_color2_label = QLabel("Color 2:")
        dup_grid.addWidget(self.dup_color2_label, 0, 2, Qt.AlignVCenter)
        self.dup_color2_swatch = self._make_color_swatch(
            lambda: self._dup_color2, lambda hexc: self._on_dup_color_changed(2, hexc))
        dup_grid.addWidget(self.dup_color2_swatch, 0, 3, Qt.AlignVCenter)
        dup_swap_bg, dup_swap_fg = self._default_button_colors()
        self.dup_swap_button = QPushButton("Swap")
        self.dup_swap_button.setFixedHeight(self._uniform_button_size()[1])
        self.dup_swap_button.setStyleSheet(
            _bevel_button_stylesheet(dup_swap_bg, dup_swap_fg)
            + " QPushButton:disabled { background-color: "
            + _press_blend(dup_swap_bg, self._effective_gui_colors(self._theme)[1], 0.65).name()
            + "; color: " + _press_blend(dup_swap_fg, self._effective_gui_colors(self._theme)[1], 0.55).name()
            + "; }"
        )
        self.dup_swap_button.setToolTip(
            "Swap Color 1 and Color 2, and flip Reverse Color Wheel Direction along with "
            "them, so the range covers the same hues the other way round."
        )
        self.dup_swap_button.clicked.connect(self._on_dup_colors_swap)
        dup_grid.addWidget(self.dup_swap_button, 0, 4, Qt.AlignVCenter)
        self.dup_reverse_cb = QCheckBox("Reverse Color Wheel Direction")
        self.dup_reverse_cb.setToolTip(
            "Which way the range between Color 1 and Color 2 runs around the color wheel "
            "(the short or the long way round for the same two colors)."
        )
        self.dup_reverse_cb.setChecked(self._dup_reverse)
        self.dup_reverse_cb.toggled.connect(self._on_dup_reverse_changed)
        dup_grid.addWidget(self.dup_reverse_cb, 0, 5, Qt.AlignVCenter)
        dup_outer.addLayout(dup_grid)

        # Text color: exclusive options; the swatch belongs to the first.
        dup_fg_row = QHBoxLayout()
        dup_fg_row.addWidget(QLabel("Text:"))
        self.dup_fg_group = QButtonGroup(dup_group)
        for mode, text, tip in (
            (0, "Set Fg color", "Always use the color picked here as the text color."),
            (1, "Inverse color", "The text color is the inverse of the background color "
                                 "(it can be hard to read on mid-tones)."),
            (2, "Inverse luminance", "The text is black on a light background and white on a "
                                     "dark one."),
        ):
            radio = QRadioButton(text)
            radio.setToolTip(tip)
            self.dup_fg_group.addButton(radio, mode)
            radio.setChecked(mode == self._dup_fg_mode)
            dup_fg_row.addWidget(radio)
            if mode == 0:
                self.dup_fg_swatch = self._make_color_swatch(
                    lambda: self._dup_fg_color, self._on_dup_fg_color_changed)
                dup_fg_row.addWidget(self.dup_fg_swatch)
        dup_fg_row.addStretch(1)
        self.dup_fg_group.idClicked.connect(self._on_dup_fg_mode_changed)
        dup_outer.addLayout(dup_fg_row)

        self.dup_preview_label = QLabel()
        self.dup_preview_label.setTextFormat(Qt.RichText)
        self.dup_preview_label.setWordWrap(True)
        self.dup_preview_label.setToolTip("One chip per step: the background and text color of each set of duplicates.")
        dup_outer.addWidget(self.dup_preview_label)
        self._sync_dup_dependents()
        self._refresh_dup_preview()
        # This is now a Level 3 block ("Default / Misc colors"), nested
        # with Comparison Type Colors inside one merged "File List View
        # Colors" Level 2 block -- see flv_colors_super_block below,
        # built once Comparison Type Colors' own block exists too.
        flv_default_misc_block = self._l2_block("Default / Misc colors", flv_colors_group, "third")

        # ---- (Inside the File List View Colors block, below its table.)
        # Selection opacity: how strongly the selection color
        # (Default Selection above) covers a selected row -- each row's
        # own diff-tag color, and the list background in the indentation
        # area left of the names, are tinted toward it by this many %
        # (see _SelectionColorDelegate/DirDropTreeWidget.drawBranches),
        # so a selected row keeps showing what its diff outcome was.
        # 100% is the plain flat highlight. ----
        flv_scrollbar_group = QGroupBox("")
        flv_scrollbar_outer = QVBoxLayout(flv_scrollbar_group)
        tint_row = QHBoxLayout()
        self.selection_tint_opacity_label = QLabel("Selection opacity:")
        self.selection_tint_opacity_spin = SliderSpinBox()
        self.selection_tint_opacity_spin.setRange(10, 100)
        self.selection_tint_opacity_spin.setSuffix("%")
        self.selection_tint_opacity_spin.setValue(self._selection_tint_opacity)
        self.selection_tint_opacity_spin.setToolTip(
            "How strongly the Selection (Default) color covers a selected row. Lower "
            "tints each row's own diff-tag color toward it, so a selected row still "
            "shows what its diff outcome was; the indentation area to the left of "
            "the names is tinted the same way, from the list background. 100% is "
            "one flat highlight color."
        )
        self.selection_tint_opacity_spin.valueChanged.connect(self._on_selection_tint_opacity_changed)
        # The only spin box on its line, at the end of it: its slider
        # goes right next to it (spin_slider_inline).
        tint_row.addWidget(spin_slider_inline(
            self.selection_tint_opacity_label, self.selection_tint_opacity_spin,
            SpinSlider(self.selection_tint_opacity_spin),
        ), 0, Qt.AlignVCenter)
        tint_row.addStretch(1)
        flv_general_outer.addLayout(tint_row)
        self._aligned_option_rows.append((self.selection_tint_opacity_label, self.selection_tint_opacity_spin))

        # ---- Scrollbar handle style: how the File List View scrollbar handle shows the
        # selection markers beneath it -- custom_widgets._SCROLLBAR_STYLE
        # ["handle_style"], not an attribute of self, so it survives a UI
        # rebuild by itself (the radios are just re-checked from it
        # below). A QButtonGroup whose button ids index
        # custom_widgets._SCROLLBAR_HANDLE_STYLES; settings_ops persists
        # it as the checked id (see _widget_value), shared, not per
        # theme, like the Global Options. No Main-tab primary -- this
        # control only exists here. ----
        scrollbar_style_row = QHBoxLayout()
        scrollbar_style_row.setContentsMargins(0, 0, 0, 0)
        scrollbar_style_row.addWidget(QLabel("Scrollbar handle style:"))
        # Parented to the Scrollbar Tweaks box so it goes away with it on
        # a UI rebuild instead of piling up on the main window.
        self.scrollbar_handle_style_group = QButtonGroup(flv_scrollbar_group)
        for style_id, text, tip in (
            (0, "Plain",
             "No magnification: the selection markers under the scrollbar handle stay "
             "at their ordinary position on the track."),
            (1, "Fisheye",
             "Magnifies the selection markers around the middle of what the File List View "
             "currently shows, spreading them over the whole handle and easing back to "
             "normal towards its top and bottom edges."),
            (2, "Quarter circle (test)",
             "Like Fisheye, but with a quarter-circle curve: strongly magnified in the middle "
             "of the view, while markers near the handle's edges bunch together."),
        ):
            radio = QRadioButton(text)
            radio.setToolTip(tip)
            self.scrollbar_handle_style_group.addButton(radio, style_id)
            scrollbar_style_row.addWidget(radio)
            if _SCROLLBAR_HANDLE_STYLES[style_id] == _SCROLLBAR_STYLE["handle_style"]:
                radio.setChecked(True)
        scrollbar_style_row.addStretch(1)
        self.scrollbar_handle_style_group.idToggled.connect(self._on_scrollbar_handle_style_toggled)
        flv_scrollbar_outer.addLayout(scrollbar_style_row)

        # ---- Scrollbar handle opacity: how much of the handle's own
        # color covers whatever sits under it in the scrollbar track
        # (the selection markers, on the file lists) -- 100% hides them
        # completely. Applies to every themed scrollbar (both File List
        # Views and the Statistics View's), and is saved per theme with
        # the rest of the color set. ----
        handle_opacity_row = QHBoxLayout()
        self.scrollbar_handle_opacity_label = QLabel("Scrollbar handle opacity:")
        self.scrollbar_handle_opacity_spin = SliderSpinBox()
        self.scrollbar_handle_opacity_spin.setRange(10, 100)
        self.scrollbar_handle_opacity_spin.setSuffix("%")
        self.scrollbar_handle_opacity_spin.setValue(_SCROLLBAR_STYLE["handle_opacity"])
        self.scrollbar_handle_opacity_spin.setToolTip(
            "How opaque the scrollbar handle is. Lower shows the selection "
            "markers under the handle more clearly; 100% hides them behind it."
        )
        self.scrollbar_handle_opacity_spin.valueChanged.connect(
            self._on_scrollbar_handle_opacity_changed
        )
        handle_opacity_row.addWidget(spin_slider_inline(
            self.scrollbar_handle_opacity_label, self.scrollbar_handle_opacity_spin,
            SpinSlider(self.scrollbar_handle_opacity_spin),
        ), 0, Qt.AlignVCenter)
        handle_opacity_row.addStretch(1)
        flv_scrollbar_outer.addLayout(handle_opacity_row)
        self._aligned_option_rows.append((self.scrollbar_handle_opacity_label, self.scrollbar_handle_opacity_spin))

        # ---- Scrollbar track opacity: how much the track covers the
        # selection markers drawn in it (both the ordinary ones and the
        # handle-magnifier ones -- see _SelectionMarkerScrollBar) -- same
        # direction as "Scrollbar handle opacity" above (higher fades the
        # markers more), fully independent of it either way, never
        # compounded together even under the handle. Capped at 90 rather
        # than 100 -- see _DEFAULT_SCROLLBAR_TRACK_OPACITY_PCT's comment.
        # Also saved per theme with the rest of the color set. ----
        track_opacity_row = QHBoxLayout()
        self.scrollbar_track_opacity_label = QLabel("Scrollbar track opacity:")
        self.scrollbar_track_opacity_spin = SliderSpinBox()
        self.scrollbar_track_opacity_spin.setRange(0, 90)
        self.scrollbar_track_opacity_spin.setSuffix("%")
        self.scrollbar_track_opacity_spin.setValue(_SCROLLBAR_STYLE["track_opacity"])
        self.scrollbar_track_opacity_spin.setToolTip(
            "How much the track fades the selection markers drawn in it. "
            "0% leaves them fully solid; higher fades them out."
        )
        self.scrollbar_track_opacity_spin.valueChanged.connect(
            self._on_scrollbar_track_opacity_changed
        )
        track_opacity_row.addWidget(spin_slider_inline(
            self.scrollbar_track_opacity_label, self.scrollbar_track_opacity_spin,
            SpinSlider(self.scrollbar_track_opacity_spin),
        ), 0, Qt.AlignVCenter)
        track_opacity_row.addStretch(1)
        flv_scrollbar_outer.addLayout(track_opacity_row)

        # ---- Marker luminance difference threshold: a selection marker
        # in the scrollbar track or handle whose luminance (0..255) is
        # within this of the track's / handle's own gets its luminance
        # inverted, so e.g. a black row on a dark track stays visible
        # (custom_widgets._contrasting_marker_color). 0 = off. Per theme. ----
        marker_luma_row = QHBoxLayout()
        self.scrollbar_marker_luma_label = QLabel("Marker Δ luminance threshold:")
        self.scrollbar_marker_luma_spin = SliderSpinBox()
        self.scrollbar_marker_luma_spin.setRange(0, 255)
        self.scrollbar_marker_luma_spin.setValue(_SCROLLBAR_STYLE["marker_luma_threshold"])
        self.scrollbar_marker_luma_spin.setToolTip(
            "Keeps selection markers visible in the scrollbar. If a marker, as it is "
            "actually seen, stands out from the track by less than this many luminance "
            "steps (0 = black ... 255 = white), its luminance is inverted (a black marker "
            "on a dark track turns light, a white one on a light track turns dark; if "
            "inverting does not help, e.g. a mid-gray marker, black or white is used). "
            "\"As seen\" counts the Scrollbar track opacity (and, for the markers under "
            "the handle, the Scrollbar handle opacity): a marker faded to 50% differs only "
            "half as much from the track as its own color does. 0 turns it off."
        )
        self.scrollbar_marker_luma_spin.valueChanged.connect(self._on_scrollbar_marker_luma_changed)
        marker_luma_row.addWidget(spin_slider_inline(
            self.scrollbar_marker_luma_label, self.scrollbar_marker_luma_spin,
            SpinSlider(self.scrollbar_marker_luma_spin),
        ), 0, Qt.AlignVCenter)
        marker_luma_row.addStretch(1)
        flv_scrollbar_outer.addLayout(marker_luma_row)

        # Duplicate File Colors (built above) closes the Default / Misc colors block.
        flv_scrollbar_block = self._l2_block("Scrollbar Tweaks", flv_scrollbar_group, "two_thirds")
        flv_dup_colors_block = self._l2_block("Duplicate File Colors", dup_group, "two_thirds")
        self._aligned_option_rows.append((self.scrollbar_track_opacity_label, self.scrollbar_track_opacity_spin))
        self._aligned_option_rows.append((self.scrollbar_marker_luma_label, self.scrollbar_marker_luma_spin))

        # ---- Line colors (inside the Indentation Line subsection built
        # just below): nested at the bottom of File
        # List View rather than its own top-level subsection -- same
        # "Nr. of Steps" spin box + 2 swatches (see DirDropTreeWidget.
        # drawBranches in dnd_widgets.py) plus hover color/opacity.
        # Steps=1 acts as "static" (only Color 1 matters, Color 2
        # disabled), Steps=2 is "every other line" alternating between
        # the two, and higher values are a color-wheel range between
        # them, cycling by depth once the tree goes deeper than the
        # step count. ----
        indent_colors_group = QGroupBox("")
        indent_colors_outer = QVBoxLayout(indent_colors_group)

        # Nr. of Steps alone on its own line -- only spin box on the
        # line and last on it, so its slider sits next to it instead
        # of below; also part of the page-wide aligned-rows group (see
        # _aligned_option_rows), same as Indentation/Line thickness and
        # the Global Options thresholds above.
        indent_steps_row = QHBoxLayout()
        self.indent_line_steps_label = QLabel("Nr. of Steps:")
        self.indent_line_steps_spin = SliderSpinBox()
        # Upper bound is a practical cap, not a technical one --
        # _indent_wheel_color itself has no limit, but a range this
        # wide already cycles through the whole color wheel several
        # times over for any tree depth this app will realistically see.
        self.indent_line_steps_spin.setRange(1, 32)
        self.indent_line_steps_spin.setValue(self._indent_line_steps)
        self.indent_line_steps_spin.valueChanged.connect(self._on_indent_line_steps_changed)
        indent_steps_row.addWidget(spin_slider_inline(
            self.indent_line_steps_label, self.indent_line_steps_spin,
            SpinSlider(self.indent_line_steps_spin),
        ), 0, Qt.AlignVCenter)
        indent_steps_row.addStretch(1)
        indent_colors_outer.addLayout(indent_steps_row)
        self._aligned_option_rows.append((self.indent_line_steps_label, self.indent_line_steps_spin))

        # Color 1 / Color 2 / Swap / Reverse (row 0) and Hover color
        # (row 1) below, in one QGridLayout so column 0 (label) and
        # column 1 (swatch) come out the same width in both rows --
        # that's what lines "Color 1"/"Hover color" up at the same
        # indentation and puts one swatch directly above the other,
        # without hand-measuring either.
        indent_colors_grid = QGridLayout()
        indent_colors_grid.setHorizontalSpacing(6)
        indent_colors_grid.setColumnStretch(6, 1)

        indent_colors_grid.addWidget(QLabel("Color 1:"), 0, 0, Qt.AlignVCenter)
        self.indent_line_color1_swatch = self._make_color_swatch(
            lambda: self._indent_line_color1, self._on_indent_line_color1_changed,
        )
        indent_colors_grid.addWidget(self.indent_line_color1_swatch, 0, 1, Qt.AlignVCenter)

        self.indent_line_color2_label = QLabel("Color 2:")
        indent_colors_grid.addWidget(self.indent_line_color2_label, 0, 2, Qt.AlignVCenter)
        self.indent_line_color2_swatch = self._make_color_swatch(
            lambda: self._indent_line_color2, self._on_indent_line_color2_changed,
        )
        indent_colors_grid.addWidget(self.indent_line_color2_swatch, 0, 3, Qt.AlignVCenter)

        swap_bg, swap_fg = self._default_button_colors()
        self.indent_line_swap_button = QPushButton("Swap")
        self.indent_line_swap_button.setFixedHeight(self._uniform_button_size()[1])
        self.indent_line_swap_button.setStyleSheet(
            _bevel_button_stylesheet(swap_bg, swap_fg)
            + " QPushButton:disabled { background-color: "
            + _press_blend(swap_bg, self._effective_gui_colors(self._theme)[1], 0.65).name()
            + "; color: " + _press_blend(swap_fg, self._effective_gui_colors(self._theme)[1], 0.55).name()
            + "; }"
        )
        self.indent_line_swap_button.setToolTip(
            "Swap Color 1 and Color 2, and flip Reverse Color Wheel Direction "
            "along with them -- so the range keeps covering the same stretch "
            "of the color wheel, just running the other way (from what was "
            "Color 2 to what was Color 1)."
        )
        self.indent_line_swap_button.clicked.connect(self._on_indent_line_colors_swap)
        indent_colors_grid.addWidget(self.indent_line_swap_button, 0, 4, Qt.AlignVCenter)

        self.indent_line_reverse_cb = QCheckBox("Reverse Color Wheel Direction")
        self.indent_line_reverse_cb.setToolTip(
            "The range between Color 1 and Color 2 always sweeps the color "
            "wheel the same rotational direction -- this flips which way, "
            "which changes between the short-way-round and long-way-round "
            "range for the same two colors. The Swap button flips this "
            "along with the colors."
        )
        self.indent_line_reverse_cb.setChecked(self._indent_line_reverse)
        self.indent_line_reverse_cb.toggled.connect(self._on_indent_line_reverse_changed)
        indent_colors_grid.addWidget(self.indent_line_reverse_cb, 0, 5, Qt.AlignVCenter)
        self._sync_indent_steps_dependents()

        # Hover color (renamed from "Hover Highlight Color") -- row 1,
        # column 0/1 shared with Color 1 above.
        indent_colors_grid.addWidget(QLabel("Hover color:"), 1, 0, Qt.AlignVCenter)
        self.indent_hover_color_swatch = self._make_color_swatch(
            lambda: self._indent_hover_color, self._on_indent_hover_color_changed,
        )
        indent_colors_grid.addWidget(self.indent_hover_color_swatch, 1, 1, Qt.AlignVCenter)

        self.indent_hover_opacity_spin = SliderSpinBox()
        self.indent_hover_opacity_spin.setRange(10, 90)
        self.indent_hover_opacity_spin.setSuffix("%")
        self.indent_hover_opacity_spin.setValue(self._indent_hover_opacity)
        self.indent_hover_opacity_spin.valueChanged.connect(self._on_indent_hover_opacity_changed)
        indent_colors_grid.addWidget(spin_slider_inline(
            QLabel("Opacity:"), self.indent_hover_opacity_spin,
            SpinSlider(self.indent_hover_opacity_spin),
        ), 1, 2, 1, 4, Qt.AlignVCenter)

        indent_colors_outer.addLayout(indent_colors_grid)

        # ---- Indentation Line (Level 2): Enable on a row of its own,
        # then Indentation and Line thickness side by side below it,
        # then the colors above as their own Level 3 "Line colors" block. All of it is per theme. Enable off greys
        # out everything below it (see _sync_indent_line_enabled_states)
        # and DirDropTreeWidget stops drawing and hit-testing the lines
        # altogether. ----
        indent_line_group = QGroupBox("")
        indent_line_outer = QVBoxLayout(indent_line_group)

        indent_enable_row = QHBoxLayout()
        self.indent_lines_enabled_cb = QCheckBox("Enable")
        self.indent_lines_enabled_cb.setToolTip(
            "Draw the indentation guide lines in the File List View. "
            "When this is off, no lines are drawn, and they can't be "
            "hovered or clicked either; the rest of these settings are "
            "greyed out."
        )
        self.indent_lines_enabled_cb.setChecked(self._indent_lines_enabled)
        self.indent_lines_enabled_cb.toggled.connect(self._on_indent_lines_enabled_changed)
        indent_enable_row.addWidget(self.indent_lines_enabled_cb)
        indent_enable_row.addStretch(1)
        indent_line_outer.addLayout(indent_enable_row)

        # Indentation: how far each nesting level is indented in the
        # file list (and so how far apart the indentation guide lines
        # sit). Applied live to both trees. Own line now -- see
        # _aligned_option_rows -- with Line thickness on its own line
        # below rather than sharing this one.
        tree_indent_row = QHBoxLayout()
        self.tree_indent_label = QLabel("Indentation:")
        self.tree_indentation_spin = SliderSpinBox()
        self.tree_indentation_spin.setRange(_MIN_TREE_INDENTATION, _MAX_TREE_INDENTATION)
        self.tree_indentation_spin.setSuffix(" px")
        self.tree_indentation_spin.setValue(self._tree_indentation)
        self.tree_indentation_spin.setToolTip(
            "How far each nested folder level is indented in the File List View, in "
            "pixels (also the spacing between the indentation guide lines)."
        )
        self.tree_indentation_spin.valueChanged.connect(self._on_tree_indentation_changed)
        tree_indent_row.addWidget(spin_slider_inline(
            self.tree_indent_label, self.tree_indentation_spin,
            SpinSlider(self.tree_indentation_spin),
        ), 0, Qt.AlignVCenter)
        tree_indent_row.addStretch(1)
        indent_line_outer.addLayout(tree_indent_row)
        self._aligned_option_rows.append((self.tree_indent_label, self.tree_indentation_spin))

        # Line thickness, on its own line below Indentation now.
        line_thickness_row = QHBoxLayout()
        self.indent_line_thickness_label = QLabel("Line thickness:")
        self.indent_line_thickness_spin = SliderSpinBox()
        # One less than the Indentation spin box's own value, so two
        # neighboring lines can never touch or overlap -- kept in step
        # with that spin box by _on_tree_indentation_changed.
        self.indent_line_thickness_spin.setRange(1, max(1, self._tree_indentation - 1))
        self.indent_line_thickness_spin.setSuffix(" px")
        self.indent_line_thickness_spin.setValue(self._indent_line_thickness)
        self.indent_line_thickness_spin.setToolTip(
            "Width of each indentation guide line, in pixels. The line's "
            "left edge stays where it is, so a thicker line grows to the "
            "right. Limited to one pixel less than the Indentation "
            "setting, so neighboring lines never touch."
        )
        self.indent_line_thickness_spin.valueChanged.connect(self._on_indent_line_thickness_changed)
        # (Its maximum follows the Indentation value -- see _sync_indent_
        # thickness_limit; the slider follows the spin box's range itself.)
        line_thickness_row.addWidget(spin_slider_inline(
            self.indent_line_thickness_label, self.indent_line_thickness_spin,
            SpinSlider(self.indent_line_thickness_spin),
        ), 0, Qt.AlignVCenter)
        line_thickness_row.addStretch(1)
        indent_line_outer.addLayout(line_thickness_row)
        self._aligned_option_rows.append((self.indent_line_thickness_label, self.indent_line_thickness_spin))

        self.indent_line_colors_block = self._settings_subsubsection("Line colors", indent_colors_group)
        indent_line_outer.addWidget(self.indent_line_colors_block)

        # ---- Line style (below Line colors): mutually exclusive shapes
        # for the guide lines, see DirDropTreeWidget.drawBranches. The
        # button ids are the stored values (dnd_widgets.LINE_STYLE_*).
        # Underneath: the tick marks options (short ticks from the line
        # to each file and/or folder, in the line's color and thickness,
        # following whichever style is chosen, each type on its own
        # side of the line). ----
        indent_style_group = QGroupBox("")
        indent_style_outer = QVBoxLayout(indent_style_group)
        indent_style_row = QHBoxLayout()
        # Parented to the group box so it goes away with it on a UI
        # rebuild instead of piling up on the main window.
        self.indent_line_style_group = QButtonGroup(indent_style_group)
        for style_id, text, tip in (
            (0, "Straight line",
             "Plain vertical line, one per nesting level."),
            (1, "Diagonally indented",
             "Each line slides half an indentation to the right over the first row "
             "under its folder, runs straight, and slides back over the last row "
             "(a single row gets a \">\" tip). Expand arrows move to the right half "
             "of their column to stay clear of the line."),
            (2, "Curved indented",
             "Like Diagonally indented, but the slides are arcs that flare out "
             "sideways at the very start and end of the line."),
            (3, "S-curve indented",
             "Like Diagonally indented, but the slides are smooth S-curves that "
             "are vertical at both ends."),
        ):
            radio = QRadioButton(text)
            radio.setToolTip(tip)
            self.indent_line_style_group.addButton(radio, style_id)
            indent_style_row.addWidget(radio)
            if style_id == self._indent_line_style:
                radio.setChecked(True)
        indent_style_row.addStretch(1)
        indent_style_outer.addLayout(indent_style_row)
        self.indent_line_style_group.idClicked.connect(self._on_indent_line_style_changed)

        # Tick marks: a master checkbox on a line of its own, then one
        # line each for files and folders -- [type checkbox] [Left side]
        # [length spin box + slider] -- in a grid so the columns line up.
        # Each type's Left side/length are only available while its own
        # checkbox is on, and all of it while the master is on (see
        # _sync_indent_tick_enabled_states). The lengths' maximum follows
        # the Indentation (see _sync_indent_thickness_limit), like the
        # line thickness. All of it is stored per theme.
        self._ensure_indent_tick_state()
        indent_tick_master_row = QHBoxLayout()
        self.indent_tick_enabled_cb = QCheckBox("Enable Tick Marks")
        self.indent_tick_enabled_cb.setToolTip(
            "Draw a short tick from the indentation line to each file and/or "
            "folder, in the line's color and thickness. The ticks follow the "
            "chosen line style, starting where the line actually is on that "
            "row. Files and folders are set up separately below."
        )
        self.indent_tick_enabled_cb.setChecked(self._indent_tick_enabled)
        self.indent_tick_enabled_cb.toggled.connect(
            lambda checked: self._on_indent_tick_option_changed("_indent_tick_enabled", bool(checked))
        )
        indent_tick_master_row.addWidget(self.indent_tick_enabled_cb)
        indent_tick_master_row.addStretch(1)
        indent_style_outer.addLayout(indent_tick_master_row)

        indent_tick_grid = QGridLayout()
        indent_tick_grid.setContentsMargins(20, 0, 0, 0)
        indent_tick_grid.setHorizontalSpacing(16)
        indent_tick_grid.setColumnStretch(3, 1)
        for grid_row, (kind, type_text, type_tip, length_text, length_tip,
                       on_attr, left_attr, length_attr, length_spin_name) in enumerate((
            ("files", "Files",
             "Draw a tick to each file.",
             "File tick mark length:",
             "How far each file tick reaches out from the indentation line, in "
             "pixels. A tick never goes past its own indentation column, so in "
             "the diagonal/curved styles (where the line has moved to the right) "
             "long ticks on the right side are cut off sooner.",
             "_indent_tick_files", "_indent_tick_files_left", "_indent_tick_length",
             "indent_tick_length_spin"),
            ("folders", "Folders",
             "Draw a tick to each folder. A folder's expand arrow is drawn on top "
             "of a tick on the right side of the line, so the left side is "
             "usually the cleaner choice.",
             "Folder tick mark length:",
             "How far each folder tick reaches out from the indentation line, in "
             "pixels. A tick never goes past its own indentation column (right "
             "side) or the line before it (left side), so long ticks are cut off "
             "sooner in the diagonal/curved styles.",
             "_indent_tick_folders", "_indent_tick_folders_left", "_indent_tick_folder_length",
             "indent_tick_folder_length_spin"),
        )):
            type_cb = QCheckBox(type_text)
            type_cb.setToolTip(type_tip)
            type_cb.setChecked(getattr(self, on_attr))
            type_cb.toggled.connect(
                lambda checked, a=on_attr: self._on_indent_tick_option_changed(a, bool(checked))
            )
            left_cb = QCheckBox("Left side")
            left_cb.setToolTip(
                "Put the tick on the left side of the indentation line instead of "
                "the right side."
            )
            left_cb.setChecked(getattr(self, left_attr))
            left_cb.toggled.connect(
                lambda checked, a=left_attr: self._on_indent_tick_option_changed(a, bool(checked))
            )
            length_label = QLabel(length_text)
            length_spin = SliderSpinBox()
            length_spin.setRange(1, max(1, self._tree_indentation - 1))
            length_spin.setSuffix(" px")
            length_spin.setValue(getattr(self, length_attr))
            length_spin.setStyleSheet(_DISABLED_SPINBOX_STYLE)
            length_spin.setToolTip(length_tip)
            length_spin.valueChanged.connect(
                lambda value, a=length_attr: self._on_indent_tick_option_changed(a, int(value))
            )
            setattr(self, f"indent_tick_{kind}_cb", type_cb)
            setattr(self, f"indent_tick_{kind}_left_cb", left_cb)
            setattr(self, f"indent_tick_{kind}_length_label", length_label)
            setattr(self, length_spin_name, length_spin)
            indent_tick_grid.addWidget(type_cb, grid_row, 0, Qt.AlignVCenter)
            indent_tick_grid.addWidget(left_cb, grid_row, 1, Qt.AlignVCenter)
            indent_tick_grid.addWidget(spin_slider_inline(
                length_label, length_spin, SpinSlider(length_spin),
            ), grid_row, 2, Qt.AlignVCenter)
        indent_style_outer.addLayout(indent_tick_grid)
        self._sync_indent_tick_enabled_states()
        self.indent_line_style_block = self._settings_subsubsection("Line style", indent_style_group)
        indent_line_outer.addWidget(self.indent_line_style_block)

        # ---- Selected folders indentation line highlight (Level 3): the
        # lines of folders that are selected (and expanded) get thicker.
        # Everything but its own Enable is greyed while that is off. ----
        indent_sel_group = QGroupBox("")
        indent_sel_outer = QVBoxLayout(indent_sel_group)
        self.indent_sel_enabled_cb = QCheckBox("Enable")
        self.indent_sel_enabled_cb.setToolTip(
            "Make the indentation line of every selected, expanded folder "
            "thicker, so its contents stand out."
        )
        self.indent_sel_enabled_cb.setChecked(self._indent_sel_enabled)
        self.indent_sel_enabled_cb.toggled.connect(
            lambda checked: self._on_indent_sel_option_changed("_indent_sel_enabled", bool(checked))
        )
        indent_sel_outer.addWidget(self.indent_sel_enabled_cb)
        self.indent_sel_increase_label = QLabel("Line thickness increase:")
        self.indent_sel_increase_spin = SliderSpinBox()
        self.indent_sel_increase_spin.setRange(
            0, max(0, self._tree_indentation - self._indent_line_thickness))
        self.indent_sel_increase_spin.setSuffix(" px")
        self.indent_sel_increase_spin.setValue(self._indent_sel_increase)
        self.indent_sel_increase_spin.setStyleSheet(_DISABLED_SPINBOX_STYLE)
        self.indent_sel_increase_spin.setToolTip(
            "How many pixels thicker the line gets. At most the indentation "
            "minus the normal line thickness, so it never reaches the next "
            "indentation level."
        )
        self.indent_sel_increase_spin.valueChanged.connect(
            lambda value: self._on_indent_sel_option_changed("_indent_sel_increase", int(value))
        )
        indent_sel_outer.addWidget(spin_slider_inline(
            self.indent_sel_increase_label, self.indent_sel_increase_spin,
            SpinSlider(self.indent_sel_increase_spin),
        ))
        self.indent_sel_ticks_cb = QCheckBox("Include tickmarks")
        self.indent_sel_ticks_cb.setToolTip(
            "Tick marks on the highlighted lines get thicker too."
        )
        self.indent_sel_ticks_cb.setChecked(self._indent_sel_ticks)
        self.indent_sel_ticks_cb.toggled.connect(
            lambda checked: self._on_indent_sel_option_changed("_indent_sel_ticks", bool(checked))
        )
        indent_sel_outer.addWidget(self.indent_sel_ticks_cb)
        self._sync_indent_sel_enabled_states()
        self.indent_sel_block = self._settings_subsubsection(
            "Selected folders indentation line highlight", indent_sel_group)
        indent_line_outer.addWidget(self.indent_sel_block)

        # Everything below Enable, greyed out together while it's off.
        self._indent_line_dependents = [
            self.tree_indent_label, self.tree_indentation_spin,
            self.indent_line_thickness_label, self.indent_line_thickness_spin,
            self.indent_line_colors_block, self.indent_line_style_block,
            self.indent_sel_block,
        ]
        self._sync_indent_line_enabled_states()

        flv_flow.addWidget(self._settings_subsection("Indentation Line", indent_line_group))

        # ---- Comparison Type Colors: the file-list row Fg/Bg for each
        # diff outcome, set independently for files and folders
        # (constants.TAG_COLORS entries) -- separate from the Select-
        # group button colors under Panel Button Colors below
        # (which only color the buttons themselves). Rows in the
        # Select buttons' own order, built from custom_widgets.
        # _SELECT_KEY_TO_TAG_COLOR_KEYS: (file tag, folder tag), or just
        # (folder tag,) for Diff Folders, which has no file counterpart
        # -- those two cells are left as a plain dash. Persisted through
        # the existing "tag_colors" block (settings_ops._raw_settings_
        # state), no separate settings key needed. ----
        comparison_colors_group = QGroupBox("")
        comparison_colors_outer = QVBoxLayout(comparison_colors_group)
        _COMPARISON_LABELS = {
            "missing": "Missing", "matching": "Matching", "newer": "Newer", "older": "Older",
            "smaller": "Smaller", "larger": "Larger", "diff_folders": "Content differs",
        }
        comparison_colors_table = QTableWidget(len(_SELECT_KEY_TO_TAG_COLOR_KEYS) + 3, 6)
        self._style_table_widget(
            comparison_colors_table,
            ["Comparison type", "File Fg", "File Bg", "Folder Fg", "Folder Bg", "Comparison Type\nSummary Text Color"],
        )
        for i, (key, tags) in enumerate(_SELECT_KEY_TO_TAG_COLOR_KEYS.items()):
            file_tag = tags[0] if len(tags) == 2 else None
            dir_tag = tags[-1]
            comparison_colors_table.setItem(i, 0, self._table_item(_COMPARISON_LABELS[key]))
            for col, (tag, is_fg) in enumerate(
                ((file_tag, True), (file_tag, False), (dir_tag, True), (dir_tag, False)), start=1,
            ):
                if tag is None:
                    dash = self._table_item("\u2014")
                    dash.setTextAlignment(Qt.AlignCenter)
                    comparison_colors_table.setItem(i, col, dash)
                    continue
                comparison_colors_table.setCellWidget(i, col, self._center_in_cell(self._make_color_swatch(
                    lambda t=tag, f=is_fg: self._tag_color_hex(t, f),
                    lambda hexc, t=tag, f=is_fg: self._on_comparison_color_changed(t, f, hexc),
                )))
            comparison_colors_table.setCellWidget(i, 5, self._center_in_cell(self._make_color_swatch(
                lambda k=key: self._summary_colors[k],
                lambda hexc, k=key: self._on_summary_color_changed(k, hexc),
            )))
        # Extra row: files/folders without a hash yet (hash comparison
        # types only). Not a Select-button type, so no summary color.
        unhashed_row = len(_SELECT_KEY_TO_TAG_COLOR_KEYS)
        unhashed_label = self._table_item("Unhashed")
        unhashed_label.setToolTip(
            "Hash value / Fingerprint comparison only: files and folders that "
            "have not been hashed yet."
        )
        comparison_colors_table.setItem(unhashed_row, 0, unhashed_label)
        for col, (tag, is_fg) in enumerate(
            (("unhashed_file", True), ("unhashed_file", False),
             ("unhashed_dir", True), ("unhashed_dir", False)), start=1,
        ):
            comparison_colors_table.setCellWidget(unhashed_row, col, self._center_in_cell(self._make_color_swatch(
                lambda t=tag, f=is_fg: self._tag_color_hex(t, f),
                lambda hexc, t=tag, f=is_fg: self._on_comparison_color_changed(t, f, hexc),
            )))
        dash = self._table_item("\u2014")
        dash.setTextAlignment(Qt.AlignCenter)
        comparison_colors_table.setItem(unhashed_row, 5, dash)
        changed_row = unhashed_row + 1
        changed_label = self._table_item("Changed since cached")
        changed_label.setToolTip(
            "Hash value / Fingerprint comparison only: files whose cached hash is "
            "out of date because their size or date changed since they were hashed, "
            "and folders holding such files. Takes priority over Unhashed."
        )
        comparison_colors_table.setItem(changed_row, 0, changed_label)
        for col, (tag, is_fg) in enumerate(
            (("changed_file", True), ("changed_file", False),
             ("changed_dir", True), ("changed_dir", False)), start=1,
        ):
            comparison_colors_table.setCellWidget(changed_row, col, self._center_in_cell(self._make_color_swatch(
                lambda t=tag, f=is_fg: self._tag_color_hex(t, f),
                lambda hexc, t=tag, f=is_fg: self._on_comparison_color_changed(t, f, hexc),
            )))
        dash3 = self._table_item("\u2014")
        dash3.setTextAlignment(Qt.AlignCenter)
        comparison_colors_table.setItem(changed_row, 5, dash3)
        hashed_row = changed_row + 1
        hashed_label = self._table_item("Hashed, not compared")
        hashed_label.setToolTip(
            "Hash value / Fingerprint comparison only: files and folders that "
            "have been hashed but have nothing to be compared with (the other "
            "pane is empty, or Compare folders is off)."
        )
        comparison_colors_table.setItem(hashed_row, 0, hashed_label)
        for col, (tag, is_fg) in enumerate(
            (("hashed_file", True), ("hashed_file", False),
             ("hashed_dir", True), ("hashed_dir", False)), start=1,
        ):
            comparison_colors_table.setCellWidget(hashed_row, col, self._center_in_cell(self._make_color_swatch(
                lambda t=tag, f=is_fg: self._tag_color_hex(t, f),
                lambda hexc, t=tag, f=is_fg: self._on_comparison_color_changed(t, f, hexc),
            )))
        dash2 = self._table_item("\u2014")
        dash2.setTextAlignment(Qt.AlignCenter)
        comparison_colors_table.setItem(hashed_row, 5, dash2)
        comparison_colors_table.horizontalHeaderItem(5).setToolTip(
            "Text color of this type's count in the summary line below each File List View "
            "(\"Missing: 3 · Matching: 12 ...\"). Independent of the Select button colors."
        )
        self._size_table_to_contents(comparison_colors_table)
        comparison_colors_outer.addWidget(comparison_colors_table)
        comparison_colors_block = self._l2_block("Comparison Type Colors", comparison_colors_group, "full")

        # ---- Duplicate Finder Summary Colors: text colors of the Groups /
        # Duplicate files / Folders / Wasted space figures in the summary under
        # the file lists in Duplicate Finder mode. Per theme (kept in
        # _summary_colors next to the Comparison Type summary colors). ----
        dup_summary_group = QGroupBox("")
        dup_summary_outer = QVBoxLayout(dup_summary_group)
        dup_summary_table = QTableWidget(len(DUP_SUMMARY_TYPES), 2)
        self._style_table_widget(dup_summary_table, ["Summary item", "Text color"])
        for i, (key, (label, default)) in enumerate(DUP_SUMMARY_TYPES.items()):
            dup_summary_table.setItem(i, 0, self._table_item(label))
            dup_summary_table.setCellWidget(i, 1, self._center_in_cell(self._make_color_swatch(
                lambda k=key, d=default: self._summary_colors.get(k, d),
                lambda hexc, k=key: self._on_summary_color_changed(k, hexc),
            )))
        self._size_table_to_contents(dup_summary_table)
        dup_summary_outer.addWidget(dup_summary_table)
        dup_summary_block = self._settings_subsubsection("Duplicate Finder Summary Colors", dup_summary_group)
        self._l2_blocks.append((dup_summary_block, dup_summary_group, "third"))

        # ---- Duplicate Finder Buttons (Level 3, after the summary colors):
        # buttons only that mode has -- for now the Include paths / Unique
        # files only list-mode switch (released and pressed-in = the active
        # list mode). Kept per theme in self._main_button_colors. ----
        dup_buttons_group = self._main_button_color_table([
            ("dup_list_unpressed", "List mode (released)"),
            ("dup_list_pressed", "List mode (pressed)"),
        ])
        dup_buttons_block = self._settings_subsubsection("Duplicate Finder Buttons", dup_buttons_group)
        self._l2_blocks.append((dup_buttons_block, dup_buttons_group, "third"))

        # ---- Button Colors (Level 3, after Comparison Type Colors): the
        # buttons around each pane's File List View. They start at the
        # theme's default button colors -- see _main_button_color_for. ----
        flv_buttons_group = self._main_button_color_table([
            ("up", "Up one folder"), ("select_dir", "Select Directory"),
            ("save_snapshot", "Save Snapshot"),
            ("hash", "Hash new"), ("pane_refresh", "Refresh"), ("clear_view", "Clear View"), ("expand_all", "Expand (+)"),
            ("collapse_all", "Collapse (-)"),
        ])
        flv_buttons_block = self._l2_block("Button Colors", flv_buttons_group, "third")

        # "Duplicate Finder Misc Colors" (Level 2): the summary colors and
        # the Duplicate Finder buttons as two equally wide Level 3 tables
        # (a third each, stacked in this 1/3-wide block). Flat and
        # zero-margin: it only holds the two framed blocks.
        dup_misc_group = QGroupBox("")
        dup_misc_group.setFlat(True)
        dup_misc_outer = QVBoxLayout(dup_misc_group)
        dup_misc_outer.setContentsMargins(0, 0, 0, 0)
        dup_misc_outer.addLayout(self._settings_subsection_flow(
            dup_summary_block, dup_buttons_block, reorder=False,
        ))
        dup_misc_block = self._l2_block("Duplicate Finder Misc Colors", dup_misc_group, "third")

        # The former "File List View Colors" Level 2 block is gone: its
        # parts are Level 2 blocks of their own, packed by the flow.
        for block in (flv_default_misc_block, flv_scrollbar_block, flv_dup_colors_block,
                      comparison_colors_block, flv_buttons_block, dup_misc_block):
            flv_flow.addWidget(block)

        outer.addWidget(self._settings_section("File List View", date_col_group))

        # ---- File List / Statistics Split (both panes) -- the same
        # ratio the draggable handle between the two views (Main tab)
        # controls; dragging that handle updates this spin box and vice
        # versa. See _set_file_list_ratio. ----
        split_group = QGroupBox("")
        split_row = QHBoxLayout(split_group)
        split_row.addWidget(QLabel("File List share:"))
        self.file_list_ratio_spin = SliderDoubleSpinBox()
        self.file_list_ratio_spin.setRange(0.05, 0.95)
        self.file_list_ratio_spin.setSingleStep(0.05)
        self.file_list_ratio_spin.setDecimals(2)
        self.file_list_ratio_spin.setValue(self._file_list_ratio)
        self.file_list_ratio_spin.setToolTip(
            "Fraction of each pane's vertical space the File List view gets; the "
            "rest goes to the Statistics view below it. 0.75 means the File List "
            "gets 3/4 of the height and Statistics view gets 1/4. Same as dragging "
            "the handle between the two views on the Main tab -- either one "
            "updates the other, and both panes stay in sync."
        )
        self.file_list_ratio_spin.valueChanged.connect(self._on_file_list_ratio_spin_changed)
        split_row.addWidget(self.file_list_ratio_spin)
        split_row.addSpacing(12)
        split_row.addWidget(SpinSlider(self.file_list_ratio_spin))
        split_row.addStretch(1)
        outer.addWidget(self._settings_section("File List / Statistics Split (both panes)", split_group))

        # ---- Statistics View (common to both panes) ----
        # The section's frame holds a flow of Level 2 blocks: "General
        # options" first (everything common to both panes' views --
        # font size, label angle, bins, calendar-unit stripping, the
        # Symmetrical GUI footer option), then the Left/Right Statistics
        # View and Statistics View Colors blocks further down.
        stats_group = QGroupBox("")
        stats_group_outer = QVBoxLayout(stats_group)
        stats_options_group = QGroupBox("")
        stats_options_outer = QVBoxLayout(stats_options_group)

        # Row 0: Font size alone (slider next to it). Row 1: Auto angle
        # alone. Row 2: Axis label angle + spin box (slider next to
        # it). Row 3: Nice bins + Bin count + spin box (slider next to
        # it, unchanged otherwise). A QGridLayout rather than separate
        # QHBoxLayouts -- column 0 holds each row's "everything before
        # the slider", column 1 the slider itself, so column 0's width
        # (set by its widest row) is what lines Axis label angle's
        # slider up directly above Bin count's, without hand-measuring
        # either row's leading content.
        stats_top_grid = QGridLayout()
        stats_top_grid.setHorizontalSpacing(16)
        stats_top_grid.setVerticalSpacing(6)
        stats_top_grid.setColumnStretch(2, 1)

        self.common_font_size_spin = SliderSpinBox()
        self.common_font_size_spin.setRange(6, 24)
        self.common_font_size_spin.setValue(DEFAULT_LABEL_FONT_PT)
        self.common_font_size_spin.setToolTip(
            "Point size for both panes' Statistics view text -- the "
            "angled date/size labels along the bottom, the vertical-"
            "axis counts, and the upper-left hover/selection readout."
        )
        self.common_font_size_spin.valueChanged.connect(self._on_common_font_size_changed)
        font_size_lead = QWidget()
        font_size_lead_row = QHBoxLayout(font_size_lead)
        font_size_lead_row.setContentsMargins(0, 0, 0, 0)
        font_size_lead_row.addWidget(QLabel("Font size:"), 0, Qt.AlignVCenter)
        font_size_lead_row.addWidget(self.common_font_size_spin, 0, Qt.AlignVCenter)
        stats_top_grid.addWidget(font_size_lead, 0, 0, Qt.AlignVCenter)
        stats_top_grid.addWidget(SpinSlider(self.common_font_size_spin), 0, 1, Qt.AlignVCenter)

        self.common_auto_tick_angle_cb = QCheckBox("Auto angle")
        self.common_auto_tick_angle_cb.setToolTip(
            "On: each pane's axis labels use the shallowest angle that keeps \n"
            "every label clear of its neighbours -- flat (0\u00b0) whenever a label \n"
            "plus about one character fits between its tick and the next, \n"
            "otherwise tilted only as far as needed -- and re-fit as you zoom, \n"
            "resize, or change the bin count/font size. Disables Axis label \n"
            "angle.\n"
            "Off: the fixed Axis label angle is used."
        )
        self.common_auto_tick_angle_cb.toggled.connect(self._on_common_auto_tick_angle_changed)
        stats_top_grid.addWidget(self.common_auto_tick_angle_cb, 1, 0, Qt.AlignVCenter)

        self.common_tick_angle_label = QLabel("Axis label angle:")
        self.common_tick_angle_spin = SliderSpinBox()
        self.common_tick_angle_spin.setRange(0, 90)
        self.common_tick_angle_spin.setSuffix("\u00b0")
        self.common_tick_angle_spin.setValue(TICK_LABEL_ANGLE_DEG)
        self.common_tick_angle_spin.setToolTip(
            "How far down-right the angled date/size labels along the "
            "bottom of both panes' Statistics views are rotated from "
            "their tick mark. A shallower angle also needs less room "
            "below the chart -- see TimelineWidget._recompute_bottom_margin."
        )
        self.common_tick_angle_spin.valueChanged.connect(self._on_common_tick_angle_changed)
        tick_angle_lead = QWidget()
        tick_angle_lead_row = QHBoxLayout(tick_angle_lead)
        tick_angle_lead_row.setContentsMargins(0, 0, 0, 0)
        tick_angle_lead_row.addWidget(self.common_tick_angle_label, 0, Qt.AlignVCenter)
        tick_angle_lead_row.addWidget(self.common_tick_angle_spin, 0, Qt.AlignVCenter)
        stats_top_grid.addWidget(tick_angle_lead, 2, 0, Qt.AlignVCenter)
        stats_top_grid.addWidget(SpinSlider(self.common_tick_angle_spin), 2, 1, Qt.AlignVCenter)

        self.common_nice_bins_cb = QCheckBox("Nice bins")
        self.common_nice_bins_cb.setChecked(True)
        self.common_nice_bins_cb.setToolTip(
            "On: bins snap to calendar units / decimal magnitudes (1 month, "
            "10 MB, ...), sized to roughly fit bin_count bins across the "
            "current view.\n"
            "Off: the visible span is split into an exact bin count, so "
            "widths change as you pan/zoom."
        )
        self.common_nice_bins_cb.toggled.connect(self._on_common_nice_bins_changed)
        self.common_bin_count_spin = SliderSpinBox()
        self.common_bin_count_spin.setRange(2, 500)
        self.common_bin_count_spin.setValue(DEFAULT_BIN_COUNT)
        self.common_bin_count_spin.valueChanged.connect(self._on_common_bin_count_changed)
        bin_count_lead = QWidget()
        bin_count_lead_row = QHBoxLayout(bin_count_lead)
        bin_count_lead_row.setContentsMargins(0, 0, 0, 0)
        bin_count_lead_row.addWidget(self.common_nice_bins_cb, 0, Qt.AlignVCenter)
        bin_count_lead_row.addSpacing(16)
        bin_count_lead_row.addWidget(QLabel("Bin count:"), 0, Qt.AlignVCenter)
        bin_count_lead_row.addWidget(self.common_bin_count_spin, 0, Qt.AlignVCenter)
        stats_top_grid.addWidget(bin_count_lead, 3, 0, Qt.AlignVCenter)
        # Slider not live: every step re-bins both panes' data.
        stats_top_grid.addWidget(
            SpinSlider(self.common_bin_count_spin, live=False), 3, 1, Qt.AlignVCenter
        )

        stats_options_outer.addLayout(stats_top_grid)

        # Depends on Nice bins (greyed out with it off -- see
        # _on_common_nice_bins_changed), so indented under it.
        stats_strip_row = QHBoxLayout()
        stats_strip_row.addSpacing(20)
        self.common_strip_units_cb = QCheckBox("Strip most significant calendar units from horizontal labels")
        self.common_strip_units_cb.setToolTip(
            "Time axis with Nice bins on: leaves the parts of the x-axis labels that are \n"
            "larger than the bin size out of the labels (the year from month bins, \n"
            "year-month from day bins, the date from hour bins, ...) and shows them \n"
            "once in the Statistics view's upper-left corner instead, next to the bin \n"
            "size, e.g. \"Bin: 1 hour   Chart: 2024-03-22 - 23\".\n"
            "Filesize labels are never changed."
        )
        self.common_strip_units_cb.setEnabled(self.common_nice_bins_cb.isChecked())
        self.common_strip_units_cb.toggled.connect(self._on_common_strip_units_changed)
        stats_strip_row.addWidget(self.common_strip_units_cb, 0, Qt.AlignVCenter)
        stats_strip_row.addStretch(1)
        stats_options_outer.addLayout(stats_strip_row)

        # Only meaningful (and only clickable) while the option above is
        # on -- see _on_common_strip_units_changed, which flips these
        # two's enabled state the same way Nice bins flips the row above.
        stats_names_row = QHBoxLayout()
        stats_names_row.addSpacing(20)
        self.common_strip_names_cb = QCheckBox("Use calendar unit names instead of numbers")
        self.common_strip_names_cb.setToolTip(
            "With the option above on: weekday names for day bins (\"Friday\" rather \n"
            "than \"22\"), month names for month bins and for the month part of week \n"
            "bins (\"March\" rather than \"03\"). Also changes how the upper-left \n"
            "\"Chart: ...\" text writes the dates it covers."
        )
        self.common_strip_names_cb.setEnabled(self.common_strip_units_cb.isChecked())
        self.common_strip_names_cb.toggled.connect(self._on_common_strip_names_changed)
        stats_names_row.addWidget(self.common_strip_names_cb, 0, Qt.AlignVCenter)
        stats_names_row.addSpacing(12)
        self.common_strip_abbreviate_cb = QCheckBox("Abbreviate names")
        self.common_strip_abbreviate_cb.setToolTip(
            "Shorten the names the option to its left uses, e.g. \"January\" -> \"Jan\", \n"
            "\"Monday\" -> \"Mon\"."
        )
        self.common_strip_abbreviate_cb.setEnabled(self.common_strip_units_cb.isChecked())
        self.common_strip_abbreviate_cb.toggled.connect(self._on_common_strip_abbreviate_changed)
        stats_names_row.addWidget(self.common_strip_abbreviate_cb, 0, Qt.AlignVCenter)
        stats_names_row.addStretch(1)
        stats_options_outer.addLayout(stats_names_row)

        stats_week_row = QHBoxLayout()
        stats_week_row.addSpacing(20)
        self.common_strip_week_number_cb = QCheckBox("Use week nr. for 1 week bins")
        self.common_strip_week_number_cb.setToolTip(
            "With \"Strip most significant calendar units...\" on: for week bins, \n"
            "label each with the week number of the year it starts on (\"W12\") \n"
            "instead of its month and day. \"W\" is dropped if Abbreviate names \n"
            "is also on (\"12\")."
        )
        self.common_strip_week_number_cb.setEnabled(self.common_strip_units_cb.isChecked())
        self.common_strip_week_number_cb.toggled.connect(self._on_common_strip_week_number_changed)
        stats_week_row.addWidget(self.common_strip_week_number_cb, 0, Qt.AlignVCenter)
        stats_week_row.addStretch(1)
        stats_options_outer.addLayout(stats_week_row)

        stats_footer_row = QHBoxLayout()
        self.mirror_statistics_buttons_cb = QCheckBox("Include footer buttons/options in Symmetrical (ish) GUI")
        self.mirror_statistics_buttons_cb.setToolTip(
            "Only while the main Symmetrical (ish) GUI setting is also on: mirror the right "
            "pane's Statistics view footer -- the buttons/options row(s) below the "
            "chart -- and its hover/selection text, horizontally -- same left/right "
            "mirroring Symmetrical (ish) GUI already applies to the Select/Clipboard/File Op "
            "groups. The Statistics view's own title line (above the chart) mirrors "
            "whenever Symmetrical (ish) GUI is on, regardless of this setting. The axis and "
            "bins themselves are never mirrored, on either pane."
        )
        self.mirror_statistics_buttons_cb.setChecked(self._mirror_statistics_buttons)
        self.mirror_statistics_buttons_cb.toggled.connect(self._on_mirror_statistics_buttons_changed)
        stats_footer_row.addWidget(self.mirror_statistics_buttons_cb)
        stats_footer_row.addStretch(1)
        stats_options_outer.addLayout(stats_footer_row)

        # The section's Level 2 blocks (General options, Left/Right
        # Statistics View, Statistics View Colors), side by side while
        # they fit and wrapping below each other when they don't --
        # the rest are added to this flow where they're built, below.
        stats_flow = self._settings_subsection_flow(
            self._l2_block("General options", stats_options_group, "two_thirds")
        )
        stats_group_outer.addLayout(stats_flow)

        outer.addWidget(self._settings_section("Statistics View", stats_group))

        # Apply the just-created common values to both panes' views right
        # away, and point pane.tl_font_size_spin/tl_nice_bins_cb/
        # tl_bin_count_spin/tl_tick_angle_spin at these same shared
        # widgets so anything that still looks for them there finds the
        # real, live controls.
        for pane in (self.left, self.right):
            pane.timeline.set_label_font_size(self.common_font_size_spin.value())
            pane.timeline.set_nice_bins(self.common_nice_bins_cb.isChecked())
            pane.timeline.set_bin_count(self.common_bin_count_spin.value())
            pane.timeline.set_tick_label_angle(self.common_tick_angle_spin.value())
            pane.timeline.set_auto_tick_angle(self.common_auto_tick_angle_cb.isChecked())
            pane.timeline.set_strip_calendar_units(self.common_strip_units_cb.isChecked())
            pane.timeline.set_strip_use_names(self.common_strip_names_cb.isChecked())
            pane.timeline.set_strip_abbreviate_names(self.common_strip_abbreviate_cb.isChecked())
            pane.timeline.set_strip_use_week_number(self.common_strip_week_number_cb.isChecked())
            pane.tl_strip_units_cb = self.common_strip_units_cb
            pane.tl_strip_names_cb = self.common_strip_names_cb
            pane.tl_strip_abbreviate_cb = self.common_strip_abbreviate_cb
            pane.tl_strip_week_number_cb = self.common_strip_week_number_cb
            pane.tl_font_size_spin = self.common_font_size_spin
            pane.tl_nice_bins_cb = self.common_nice_bins_cb
            pane.tl_bin_count_spin = self.common_bin_count_spin
            pane.tl_tick_angle_spin = self.common_tick_angle_spin
            pane.tl_auto_tick_angle_cb = self.common_auto_tick_angle_cb

        # ---- Specific options (still individual, synced copies): one
        # Level 2 block holding the left pane's column and the right
        # pane's next to it, each its own Level 3 block ("Left pane"/
        # "Right pane") -- normal space_above spacing, same as any other
        # Level 3 pair, so they stack with the usual gap between them
        # rather than sitting flush when there's no room side by side. ----
        view_group = QGroupBox("")
        view_group_outer = QVBoxLayout(view_group)
        # Left pane before Right pane, always -- see reorder.
        view_flow = self._settings_subsection_flow(reorder=False)
        view_group_outer.addLayout(view_flow)
        for pane, side_label in ((self.left, "Left pane"), (self.right, "Right pane")):
            view_col_widget = QWidget()
            view_col = QVBoxLayout(view_col_widget)
            view_col.setContentsMargins(0, 0, 0, 0)
            for primary, label in (
                (pane.tl_log_scale_cb, "Log height"),
                (pane.tl_log_size_axis_cb, "Log (Filesize only)"),
                (pane.tl_auto_vrange_cb, "Auto Range"),
            ):
                secondary = QCheckBox(label)
                if primary.toolTip():
                    secondary.setToolTip(primary.toolTip())
                self._link_checkboxes(primary, secondary)
                view_col.addWidget(secondary)
            view_flow.addWidget(self._settings_subsubsection(side_label, view_col_widget))
        stats_flow.addWidget(self._l2_block("Specific options", view_group, "third"))

        # ---- Statistics View Colors -- Background/Selection/Bins/Grid/
        # Axis Labels/Hover Info/Selection Info, split by mode (file/
        # folder) since the two share one chart per pane but never plot
        # at the same time (see statistics_ops.TimelineOpsMixin module
        # docstring) -- Background included, now that it's per-mode too
        # rather than a value shared across both. Applied live via
        # TimelineOpsMixin._apply_stats_palette -> TimelineWidget.
        # set_palette. Sits right below the Left/Right Statistics View
        # settings above -- the last of the Statistics-view-specific
        # controls -- rather than down inside Colors && Theme with the
        # rest of the app's color tables. ----
        # Flat and zero-margin, same reasoning as flv_colors_super_group
        # above: exists only to give "Statistics View Colors" its Level
        # 2 frame around the Graph/Info/Buttons blocks, which are
        # already framed themselves.
        def _build_stats_color_table(rows) -> QTableWidget:
            """One "Item / File Mode / Folder Mode" swatch table for the
            given (stats_colors key, row label) pairs -- shared by the
            Graph colors and Info text colors tables below, which only
            differ in which keys they list."""
            table = QTableWidget(len(rows), 3)
            self._style_table_widget(table, ["Item", "File Mode", "Folder Mode"])
            for i, (key, label) in enumerate(rows):
                table.setItem(i, 0, self._table_item(label))
                table.setCellWidget(i, 1, self._center_in_cell(self._make_color_swatch(
                    lambda k=key: self._stats_colors["file"][k],
                    lambda hexc, k=key: self._on_stats_color_changed("file", k, hexc),
                )))
                table.setCellWidget(i, 2, self._center_in_cell(self._make_color_swatch(
                    lambda k=key: self._stats_colors["folder"][k],
                    lambda hexc, k=key: self._on_stats_color_changed("folder", k, hexc),
                )))
            self._size_table_to_contents(table)
            return table

        def _narrow_opacity_spin(value: int, minimum: int, changed) -> SliderSpinBox:
            """A 10-100%/0-100% opacity spin box sized for its own 3
            digits + '%' rather than the page's usual wider spin boxes
            -- see the Ranged/Hover Opacity rows below, which also give
            it a narrower (90 px) slider to match."""
            spin = SliderSpinBox()
            spin.setRange(minimum, 100)
            spin.setSuffix("%")
            spin.setValue(value)
            spin.setMaximumWidth(QFontMetrics(self.font()).horizontalAdvance("100%") + 30)
            spin.valueChanged.connect(changed)
            return spin

        # One label column width for every row of both opacity pairs
        # below (Ranged Opacity: File/Folder, Hover Opacity: File/
        # Folder): the widest "<title>: File:" label, so all four spin
        # boxes and sliders start at the same x.
        _OPACITY_TITLES = ("Ranged Opacity", "Hover Opacity", "Partial Bin Lerp")
        opacity_label_w = max(QLabel(f"{t}: File:").sizeHint().width() for t in _OPACITY_TITLES)

        def _opacity_file_folder_rows(
            title: str, file_spin: SliderSpinBox, folder_spin: SliderSpinBox
        ) -> QVBoxLayout:
            """'<title>: File' then, on the line below, an indented
            'Folder' -- both share one label column (opacity_label_w,
            the same for every pair, so the pairs line up with each
            other too), with "Folder" right-aligned in it, so it reads
            as indented under "File" while its spin box/slider still
            land in exactly the same columns as the row above, rather
            than needing a hand-picked indent value."""
            file_label = QLabel(f"{title}: File:")
            file_label.setFixedWidth(opacity_label_w)
            folder_label = QLabel("Folder:")
            folder_label.setFixedWidth(opacity_label_w)
            folder_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

            rows = QVBoxLayout()
            rows.setSpacing(2)
            file_row = QHBoxLayout()
            file_row.addWidget(spin_slider_inline(
                file_label, file_spin, SpinSlider(file_spin),
            ), 0, Qt.AlignVCenter)
            file_row.addStretch(1)
            rows.addLayout(file_row)

            folder_row = QHBoxLayout()
            folder_row.addWidget(spin_slider_inline(
                folder_label, folder_spin, SpinSlider(folder_spin),
            ), 0, Qt.AlignVCenter)
            folder_row.addStretch(1)
            rows.addLayout(folder_row)
            return rows

        # Bars/gridlines/axes -- what the chart itself is drawn with --
        # plus the Ranged/Hover Opacity rows stacked directly below the
        # table itself (not spanning the full width of the row all
        # three tables sit in), all under one "Graph colors" Level 3
        # title. Narrow and two-line (rather than side by side) so this
        # whole block stays no wider than the table above it, letting
        # Info text colors sit right beside it instead of leaving a gap.
        _GRAPH_COLOR_ROWS = [
            ("background_color", "Background"),
            ("selection_color", "Selection"),
            ("hover_color", "Hover"),
            ("bin_color", "Bins"),
            ("grid_color", "Grid"),
            ("axis_label_color", "Axis Labels"),
        ]
        graph_colors_table = _build_stats_color_table(_GRAPH_COLOR_ROWS)

        # Ranged opacity: the alpha the Selection color above is drawn
        # at for the drag-range fill specifically (see set_palette) --
        # same "swatch + opacity" split as Selection opacity
        # above, so doesn't fit the table as a swatch
        # of its own.
        self.stats_file_selection_opacity_spin = _narrow_opacity_spin(
            self._stats_colors["file"]["selection_opacity"], 10,
            lambda v: self._on_stats_opacity_changed("file", v),
        )
        self.stats_folder_selection_opacity_spin = _narrow_opacity_spin(
            self._stats_colors["folder"]["selection_opacity"], 10,
            lambda v: self._on_stats_opacity_changed("folder", v),
        )
        ranged_opacity_rows = _opacity_file_folder_rows(
            "Ranged Opacity",
            self.stats_file_selection_opacity_spin, self.stats_folder_selection_opacity_spin,
        )

        # Hover opacity: how far a hovered bin's bar and x-axis label
        # are tinted toward the Hover color above (see set_palette).
        self.stats_file_hover_opacity_spin = _narrow_opacity_spin(
            self._stats_colors["file"]["hover_opacity"], 0,
            lambda v: self._on_stats_opacity_changed("file", v, "hover_opacity"),
        )
        self.stats_folder_hover_opacity_spin = _narrow_opacity_spin(
            self._stats_colors["folder"]["hover_opacity"], 0,
            lambda v: self._on_stats_opacity_changed("folder", v, "hover_opacity"),
        )
        hover_opacity_rows = _opacity_file_folder_rows(
            "Hover Opacity",
            self.stats_file_hover_opacity_spin, self.stats_folder_hover_opacity_spin,
        )

        # Lerp selected partial bin color: how far a partially selected
        # bin's bar moves from the Bins color toward the Selection color.
        self.stats_file_partial_lerp_spin = _narrow_opacity_spin(
            self._stats_colors["file"]["partial_lerp"], 10,
            lambda v: self._on_stats_opacity_changed("file", v, "partial_lerp"),
        )
        self.stats_folder_partial_lerp_spin = _narrow_opacity_spin(
            self._stats_colors["folder"]["partial_lerp"], 10,
            lambda v: self._on_stats_opacity_changed("folder", v, "partial_lerp"),
        )
        for _sp in (self.stats_file_partial_lerp_spin, self.stats_folder_partial_lerp_spin):
            _sp.setRange(10, 90)
            _sp.setToolTip("Lerp selected partial bin color: how far a partially selected bin moves from the Bins color toward the Selection color.")
        partial_lerp_rows = _opacity_file_folder_rows(
            "Partial Bin Lerp",
            self.stats_file_partial_lerp_spin, self.stats_folder_partial_lerp_spin,
        )

        graph_colors_group = QGroupBox("")
        QVBoxLayout(graph_colors_group).addWidget(graph_colors_table)
        graph_colors_block = self._l2_block("Graph colors", graph_colors_group, "half")

        # "Color tuning" (Level 2): the opacity/lerp rows that go with the
        # Graph colors above.
        color_tuning_group = QGroupBox("")
        color_tuning_layout = QVBoxLayout(color_tuning_group)
        color_tuning_layout.addLayout(ranged_opacity_rows)
        color_tuning_layout.addLayout(hover_opacity_rows)
        color_tuning_layout.addLayout(partial_lerp_rows)
        color_tuning_block = self._l2_block("Color tuning", color_tuning_group, "two_thirds")

        # The upper-left readout's text, split by which of its segments
        # each color paints -- see TimelineWidget._draw_readout_segments
        # -- plus the selected-count readout's own color.
        _INFO_COLOR_ROWS = [
            ("selection_info_color", "Selection Info"),
            ("hover_label_color", "Hover Info"),
            ("bin_info_color", "Bin Info"),
            ("stripped_info_color", "Stripped Info"),
        ]
        info_colors_table = _build_stats_color_table(_INFO_COLOR_ROWS)
        info_colors_group = QGroupBox("")
        QVBoxLayout(info_colors_group).addWidget(info_colors_table)
        info_colors_block = self._l2_block("Info text colors", info_colors_group, "half")

        # ---- Buttons -- every button in the Statistics view: the Files/
        # Folders mode switch and Zoom to/Reset view in its title line, Fit Range and the
        # +/- zoom buttons in its footer. A third Level 3 table next to
        # Graph colors/Info text colors. None of them has a color set
        # here until the user actually picks one -- see
        # _stats_button_color_for's fallback to _default_button_colors(),
        # the theme's default button colors. (Copy -->/Move --> used to be
        # here; they moved to Panel Button Colors -> Clipboard Buttons,
        # along with the buttons themselves.) ----
        _STATS_BUTTON_ROWS = [
            ("mode_unpressed", "Mode switch (released)"), ("mode_pressed", "Mode switch (pressed)"),
            ("zoom_to", "Zoom to"), ("reset", "Reset view"), ("fit_range", "Fit Range"),
            ("zoom_in", "Zoom in (+)"), ("zoom_out", "Zoom out (-)"),
        ]
        stats_buttons_table = QTableWidget(len(_STATS_BUTTON_ROWS), 3)
        self._style_table_widget(stats_buttons_table, ["Item", "Fg", "Bg"])
        for i, (key, label) in enumerate(_STATS_BUTTON_ROWS):
            stats_buttons_table.setItem(i, 0, self._table_item(label))
            stats_buttons_table.setCellWidget(i, 1, self._center_in_cell(self._make_color_swatch(
                lambda k=key: self._stats_button_color_for(k)[1],
                lambda hexc, k=key: self._on_stats_button_color_changed(k, False, hexc),
            )))
            stats_buttons_table.setCellWidget(i, 2, self._center_in_cell(self._make_color_swatch(
                lambda k=key: self._stats_button_color_for(k)[0],
                lambda hexc, k=key: self._on_stats_button_color_changed(k, True, hexc),
            )))
        self._size_table_to_contents(stats_buttons_table)
        stats_buttons_group = QGroupBox("")
        QVBoxLayout(stats_buttons_group).addWidget(stats_buttons_table)
        buttons_block = self._l2_block("Buttons", stats_buttons_group, "third")

        # The former "Colors (both panes)" container is gone: its tables
        # and the tuning rows are Level 2 blocks of their own.
        for block in (graph_colors_block, color_tuning_block, info_colors_block, buttons_block):
            stats_flow.addWidget(block)

        # ---- Panel Button Colors -- Theme and Misc Colors live in Global
        # Settings at the top of the tab (see there); what's here is
        # just the Select-group button
        # colors -- the buttons themselves only; the matching file-list
        # row colors are their own table (Comparison Type Colors, under
        # File List View above). The Invert button gets its own row at
        # the end -- its
        # two diagonal fill colors (dark side/light side) stand in for
        # Fg/Bg here, same as every other row, even though it's not a
        # genuine foreground/background pair. ----
        select_colors_group = QGroupBox("")
        select_colors_outer = QVBoxLayout(select_colors_group)
        _SELECT_LABELS = {
            "select_all": "Select All", "clear": "Clear", "missing": "Missing",
            "newer": "Newer", "smaller": "Smaller", "matching": "Matching",
            "older": "Older", "larger": "Larger", "diff_folders": "Diff Items",
            "dup_all": "Duplicates", "dup_extra": "Extra copies",
            "dup_sel": "Duplicates of selection", "dup_sel_extra": "Extra copies of selection",
            "file_parent": "Item Parent", "all_parents": "All Parents",
            "item_childs": "Item Childs", "all_childs": "All Childs",
        }
        select_colors_table = QTableWidget(len(_DEFAULT_SELECT_COLORS) + 1, 3)
        self._style_table_widget(select_colors_table, ["Button", "Fg", "Bg"])
        for i, key in enumerate(_DEFAULT_SELECT_COLORS.keys()):
            select_colors_table.setItem(i, 0, self._table_item(_SELECT_LABELS[key]))
            select_colors_table.setCellWidget(i, 1, self._center_in_cell(self._make_color_swatch(
                lambda k=key: self._select_colors[k][0],
                lambda hexc, k=key: self._on_select_color_changed(k, True, hexc),
            )))
            select_colors_table.setCellWidget(i, 2, self._center_in_cell(self._make_color_swatch(
                lambda k=key: self._select_colors[k][1],
                lambda hexc, k=key: self._on_select_color_changed(k, False, hexc),
            )))

        invert_row = len(_DEFAULT_SELECT_COLORS)
        select_colors_table.setItem(invert_row, 0, self._table_item("Invert"))
        select_colors_table.setCellWidget(invert_row, 1, self._center_in_cell(self._make_color_swatch(
            lambda: _INVERT_BUTTON_COLORS["dark"],
            lambda hexc: self._on_invert_button_color_changed("dark", hexc),
        )))
        select_colors_table.setCellWidget(invert_row, 2, self._center_in_cell(self._make_color_swatch(
            lambda: _INVERT_BUTTON_COLORS["light"],
            lambda hexc: self._on_invert_button_color_changed("light", hexc),
        )))
        self._size_table_to_contents(select_colors_table)
        select_colors_outer.addWidget(select_colors_table)

        # ---- Clipboard Buttons / File Op Buttons -- Copy/Cut/Paste/
        # Paste Folders/Copy -->/Move --> and Rename/New Folder/Delete.
        # Rename and Copy -->/Move --> fall back to the theme's default
        # button colors rather than a fixed design color of their own
        # (see _main_button_color_for) until customized here. ----
        clipboard_colors_group = self._main_button_color_table([
            ("copy", "Copy"), ("copy_flat", "Copy flat"), ("cut", "Cut"), ("paste", "Paste"), ("paste_folders", "Paste Folders"),
            ("copy_across", "Copy -->"), ("move_across", "Move -->"),
        ])
        file_op_colors_group = self._main_button_color_table(
            [("rename", "Rename"), ("new_folder", "New Folder"), ("delete", "Delete")]
        )

        # Both Panes Buttons: the central Both Panes group's Swap and Sync
        # (moved here from Misc Buttons).
        both_panes_colors_group = self._main_button_color_table(
            [("swap_pane", "Swap"), ("sync", "Sync")]
        )

        # "Panel Button Colors" is one section whose frame holds the
        # tables side by side, each with its own Level 2 title (see
        # _settings_subsection) since together they form one section
        # rather than three independent ones.
        buttons_group = QGroupBox("")
        buttons_group_outer = QVBoxLayout(buttons_group)
        buttons_group_outer.addLayout(self._settings_subsection_flow(
            self._l2_block("Select Buttons", select_colors_group, "third"),
            self._l2_block("Clipboard Buttons", clipboard_colors_group, "third"),
            self._l2_block("File Op Buttons", file_op_colors_group, "third"),
            self._l2_block("Both Panes Buttons", both_panes_colors_group, "third"),
        ))
        outer.addWidget(self._settings_section("Panel Button Colors", buttons_group))

        # ---- Line up every collected "[option label][spin box][slider]"
        # row's slider at the same x, however far apart the rows
        # actually are on the page -- see _aligned_option_rows. Sized to
        # the widest label and widest spin box actually in play (not a
        # guessed constant), so it stays correct if a label's wording or
        # a spin box's range/suffix ever changes. Both fixed post hoc,
        # after each row's own tooltip/range/connect setup above, rather
        # than threading a shared width down into every site that builds
        # one of these rows. ----
        self._align_sibling_groups(self._sibling_aligned_rows, (context_menu_block, sync_settings_block))
        if self._aligned_option_rows:
            aligned_label_w = max(label.sizeHint().width() for label, _ in self._aligned_option_rows)
            aligned_spin_w = max(spin.sizeHint().width() for _, spin in self._aligned_option_rows)
            for label, spin in self._aligned_option_rows:
                label.setFixedWidth(aligned_label_w)
                spin.setFixedWidth(aligned_spin_w)
        self._apply_l2_widths(page)

        outer.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        scroll.setFrameShape(QFrame.NoFrame)
        # Stashed so _capture_ui_state/_restore_ui_state can save/restore
        # scroll position across a rebuild -- this method builds a brand
        # new QScrollArea every time, which otherwise always starts back
        # at the top.
        self._settings_scroll = scroll

        # ---- Save/Load Settings/Reload Defaults + their own status
        # line -- kept outside the scroll area (added to tab_container
        # below, not to `outer`/`page`), so they -- and any save/load/
        # reset confirmation message -- stay visible regardless of
        # scroll position, instead of requiring a scroll to the very
        # bottom to reach the buttons or notice a message.
        save_load_row, (self.save_settings_button, self.load_settings_button,
                        self.reset_settings_button) = self._build_save_load_row(
            "Reload Defaults", self._reset_to_defaults,
            "Reset the current theme's colors (and the color-related options) back "
            "to the program's own built-in defaults -- the other theme's colors are "
            "left alone, and the saved settings file isn't touched; Save Settings "
            "afterward if you want the reset to stick.",
        )

        self.settings_status_label = QLabel("")
        self.settings_status_label.setAlignment(Qt.AlignCenter)
        self.settings_status_label.setStyleSheet("padding: 2px 0;")

        tab_container = QWidget()
        tab_layout = QVBoxLayout(tab_container)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(scroll, stretch=1)
        tab_layout.addLayout(save_load_row)
        tab_layout.addWidget(self.settings_status_label)
        return tab_container

    def _build_context_menu_group(self) -> QGroupBox:
        """Global Settings -> Context Menu: "Copy File List formatting",
        how the File List View's "Copy File List View Data" entries lay
        out their text -- tab-separated (for spreadsheets), or padded with
        spaces (and optionally lined up in columns)."""
        group = QGroupBox("")
        outer = QVBoxLayout(group)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)

        self.ctx_separator_group = QButtonGroup(group)
        sep_row = QHBoxLayout()
        sep_row.addWidget(QLabel("Column separation:"))
        for rid, text, tip in (
            (0, "Tab", "Columns are separated by a single tab and nothing is indented -- pastes straight into a spreadsheet."),
            (1, "Spaces", "Columns are separated by spaces; folder contents are indented."),
        ):
            radio = QRadioButton(text)
            radio.setToolTip(tip)
            self.ctx_separator_group.addButton(radio, rid)
            sep_row.addWidget(radio)
            radio.setChecked(rid == (1 if self._ctx_sep_spaces else 0))
        sep_row.addStretch(1)
        lay.addLayout(sep_row)

        self.ctx_col_spaces_spin = SliderSpinBox()
        self.ctx_col_spaces_spin.setRange(1, 20)
        self.ctx_col_spaces_spin.setValue(self._ctx_col_spaces)
        self.ctx_col_spaces_spin.valueChanged.connect(lambda v: setattr(self, "_ctx_col_spaces", int(v)))
        col_label = QLabel("Column separation spaces:")
        self._sibling_aligned_rows.append((col_label, self.ctx_col_spaces_spin))
        col_row = spin_slider_inline(col_label, self.ctx_col_spaces_spin,
                                     SpinSlider(self.ctx_col_spaces_spin, live=False))
        lay.addWidget(col_row, 0, Qt.AlignLeft)

        self.ctx_indent_spaces_spin = SliderSpinBox()
        self.ctx_indent_spaces_spin.setRange(0, 20)
        self.ctx_indent_spaces_spin.setValue(self._ctx_indent_spaces)
        self.ctx_indent_spaces_spin.valueChanged.connect(lambda v: setattr(self, "_ctx_indent_spaces", int(v)))
        indent_label = QLabel("Folder indentation spaces:")
        self._sibling_aligned_rows.append((indent_label, self.ctx_indent_spaces_spin))
        indent_row = spin_slider_inline(indent_label, self.ctx_indent_spaces_spin,
                                        SpinSlider(self.ctx_indent_spaces_spin, live=False))
        lay.addWidget(indent_row, 0, Qt.AlignLeft)

        self.ctx_nice_format_cb = QCheckBox("Nice plain-text formatting")
        self.ctx_nice_format_cb.setToolTip(
            "Lines the columns up under each other (for a constant-width font), padding "
            "shorter entries with spaces up to the widest one plus the column separation.")
        self.ctx_nice_format_cb.setChecked(self._ctx_nice_format)
        self.ctx_nice_format_cb.toggled.connect(lambda c: setattr(self, "_ctx_nice_format", bool(c)))
        lay.addWidget(self.ctx_nice_format_cb)

        space_widgets = (col_row, indent_row, self.ctx_nice_format_cb)

        def on_sep(rid, checked):
            if not checked:
                return
            self._ctx_sep_spaces = rid == 1
            for w in space_widgets:
                w.setEnabled(self._ctx_sep_spaces)
        self.ctx_separator_group.idToggled.connect(on_sep)
        for w in space_widgets:
            w.setEnabled(self._ctx_sep_spaces)

        outer.addWidget(self._settings_subsubsection("Copy File List formatting", box, space_above=False))
        return group

    @staticmethod
    def _align_sibling_groups(rows, blocks):
        """Gives the labels and spin boxes of `rows` ((label, spin) pairs of
        sibling groups) one common width, so their sliders start at the
        same x, and makes the `blocks` (the groups' Level 2 widgets) equally
        wide."""
        if rows:
            label_w = max(label.sizeHint().width() for label, _ in rows)
            spin_w = max(spin.sizeHint().width() for _, spin in rows)
            for label, spin in rows:
                label.setFixedWidth(label_w)
                spin.setFixedWidth(spin_w)
        widest = max(block.sizeHint().width() for block in blocks)
        for block in blocks:
            block.setMinimumWidth(widest)

    def _build_sync_settings_group(self) -> QGroupBox:
        """Global Settings -> Sync Settings: the Sync button's "folders look
        unrelated" warning (see FolderSyncMixin._sync_mismatch) -- shown in
        the overview when both folders have more than this share of files
        without a counterpart -- and the small-folder limit under which it
        is not shown, since a percentage of a handful of files means little."""
        group = QGroupBox("")
        outer = QVBoxLayout(group)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)

        self.sync_warn_cb = QCheckBox("Warn when the folders look unrelated")
        self.sync_warn_cb.setToolTip(
            "Sync's overview gets an extra prominent warning when more than the threshold below "
            "of the files in BOTH folders have no counterpart at the same path -- a sign that the "
            "two folders are not meant to be copies of each other. (An empty or much smaller "
            "folder, as in a first sync, does not trigger it.)")
        self.sync_warn_cb.setChecked(self._sync_warn_enabled)
        self.sync_warn_cb.toggled.connect(lambda c: setattr(self, "_sync_warn_enabled", bool(c)))
        lay.addWidget(self.sync_warn_cb)

        self.sync_warn_spin = SliderSpinBox()
        self.sync_warn_spin.setRange(0, 100)
        self.sync_warn_spin.setSuffix(" %")
        self.sync_warn_spin.setValue(self._sync_warn_percent)
        self.sync_warn_spin.valueChanged.connect(lambda v: setattr(self, "_sync_warn_percent", int(v)))
        warn_label = QLabel("Mismatch warning threshold:")
        self._sibling_aligned_rows.append((warn_label, self.sync_warn_spin))
        warn_row = spin_slider_inline(warn_label, self.sync_warn_spin,
                                      SpinSlider(self.sync_warn_spin, live=False))
        lay.addWidget(warn_row, 0, Qt.AlignLeft)

        self.sync_min_cb = QCheckBox("Skip the warning for small folders")
        self.sync_min_cb.setToolTip(
            "No unrelated-folders warning when either folder has fewer files than the limit below "
            "(counting only the files the File Filter lets through): a percentage of a handful of "
            "files says little.")
        self.sync_min_cb.setChecked(self._sync_min_enabled)
        self.sync_min_cb.toggled.connect(lambda c: setattr(self, "_sync_min_enabled", bool(c)))
        lay.addWidget(self.sync_min_cb)

        self.sync_min_spin = SliderSpinBox()
        self.sync_min_spin.setRange(1, 1000)
        self.sync_min_spin.setValue(self._sync_min_files)
        self.sync_min_spin.valueChanged.connect(lambda v: setattr(self, "_sync_min_files", int(v)))
        min_label = QLabel("Fewer files than:")
        self._sibling_aligned_rows.append((min_label, self.sync_min_spin))
        min_row = spin_slider_inline(min_label, self.sync_min_spin,
                                     SpinSlider(self.sync_min_spin, live=False))
        lay.addWidget(min_row, 0, Qt.AlignLeft)

        def update_enabled():
            warn = self.sync_warn_cb.isChecked()
            warn_row.setEnabled(warn)
            self.sync_min_cb.setEnabled(warn)
            min_row.setEnabled(warn and self.sync_min_cb.isChecked())
        self.sync_warn_cb.toggled.connect(lambda _c: update_enabled())
        self.sync_min_cb.toggled.connect(lambda _c: update_enabled())
        update_enabled()

        outer.addWidget(box)
        return group

    @staticmethod
    def _apply_binary_sizes(enabled: bool):
        import statistics_widget
        from constants import SIZE_STYLE
        SIZE_STYLE["binary"] = bool(enabled)
        statistics_widget.set_binary_sizes(enabled)

    def _on_binary_sizes_toggled(self, checked):
        self._binary_sizes = bool(checked)
        self._apply_binary_sizes(checked)
        for pane in (self.left, self.right):
            timeline = getattr(pane, "timeline", None)
            if timeline is not None:
                timeline.update()
        self.apply_filter()  # relists (deferred) so every size text is rebuilt

    def _on_folder_counts_subfolders_toggled(self, checked):
        self._folder_counts_include_subfolders = bool(checked)
        self.apply_filter()  # relists (deferred, see there) so the counts update

    def _l2_block(self, title: str, group: QGroupBox, width_class: str) -> QWidget:
        """A Level 2 block (see _settings_subsection) that takes part in the
        width grid: `width_class` is "full", "two_thirds", "half" or
        "third" -- see _apply_l2_widths."""
        block = self._settings_subsection(title, group)
        self._l2_blocks.append((block, group, width_class))
        return block

    def _apply_l2_widths(self, page: QWidget):
        """The Level 2 width grid. The base width L2BW is the width the
        Options block needs (its content decides it); the other classes are
        fractions of it, with the flow's gap taken out so that, e.g.,
        2/3 + 1/3, 1/2 + 1/2 or three 1/3 blocks fill one full-width row
        exactly:  third = (B - 2g)/3,  half = (B - g)/2,  2/3 = 2*third + g.
        Every spin box on the page also gets the one standard width (the
        widest any of them needs)."""
        spins = page.findChildren(SliderSpinBox) + page.findChildren(SliderDoubleSpinBox)
        if spins:
            spin_w = max(spin.sizeHint().width() for spin in spins)
            for spin in spins:
                spin.setFixedWidth(spin_w)
        base = self._l2_base_block
        if base is None or not self._l2_blocks:
            return
        base.ensurePolished()
        base_w = max(base.sizeHint().width(), base.minimumSizeHint().width())
        gap = _SUBSECTION_GAP
        third = (base_w - 2 * gap) // 3
        widths = {
            "full": base_w,
            "half": (base_w - gap) // 2,
            "third": third,
            "two_thirds": 2 * third + gap,
        }
        self._l2_widths = widths
        for block, group, width_class in self._l2_blocks:
            width = widths[width_class]
            block.setFixedWidth(width)
            for table in group.findChildren(QTableWidget):
                # A table inside a nested registered block (e.g. the two
                # tables of "Duplicate Finder Misc Colors") is fitted to
                # that block, not to this container.
                if any(other is not group and group.isAncestorOf(other) and other.isAncestorOf(table)
                       for _b, other, _w in self._l2_blocks):
                    continue
                self._fit_table_to_width(group, table, width)

    @staticmethod
    def _fit_table_to_width(group: QGroupBox, table: QTableWidget, block_width: int):
        """Makes a content-sized swatch table exactly as wide as its group
        has room for. Cell padding is tightened, the Fg/Bg columns are set
        to what their swatches (and header text) need, and the Item column
        takes up the difference (long names are elided)."""
        margins = group.layout().contentsMargins()
        inner = block_width - margins.left() - margins.right() - 2 * group.style().pixelMetric(
            QStyle.PM_DefaultFrameWidth
        )
        table.setStyleSheet(table.styleSheet().replace("padding: 1px 9px", "padding: 1px 4px"))
        header = table.horizontalHeader()
        header.setStyleSheet(header.styleSheet().replace("padding: 1px 9px", "padding: 1px 4px"))
        table.ensurePolished()
        header.ensurePolished()
        for col in range(1, table.columnCount()):
            need = header.sectionSizeHint(col)
            for row in range(table.rowCount()):
                cell = table.cellWidget(row, col)
                if cell is not None:
                    cell.ensurePolished()
                    need = max(need, cell.sizeHint().width() + 6)
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            table.setColumnWidth(col, need)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        table.setWordWrap(False)
        table.setFixedWidth(inner)
        table.updateGeometry()

    def _settings_page_stylesheet(self) -> str:
        """The Settings page's stylesheet: the bold group titles, and every
        group's edge in the Misc Colors "Group border" color. The groups
        here have no title of their own (see _settings_section), hence no
        title margin, and no title-position handling like the Main tab's
        (_style_group_border) is needed."""
        return (
            "QGroupBox::title { font-size: 14pt; font-weight: bold; } "
            f"QGroupBox {{ border: 1px solid {self._group_border_color};"
            " border-radius: 3px; margin-top: 0; }"
        )

    def _build_save_load_row(self, reset_text: str, reset_handler, reset_tooltip: str):
        """The Save Settings / Load Settings / <reset> button row centered
        under the Settings tab's scroll area. Save and Load act on the one
        settings file for the whole program (the file type list included);
        the reset button is set by `reset_text`/`reset_handler`/`reset_tooltip`.
        Returns (the row layout, (save, load, reset buttons))."""
        uniform_w, uniform_h = self._uniform_button_size()
        status_btn_w = uniform_w + QFontMetrics(self.font()).horizontalAdvance("M")

        row = QHBoxLayout()
        row.addStretch(1)

        # Fixed (bg, fg) copies of the Select group's current Matching/
        # Missing/File Parent colors at the moment the tab is built
        # (Save Settings gets Missing's, Load Settings Matching's) --
        # a literal snapshot, not a live reference: these buttons keep
        # this look even if Matching/Missing/File Parent get recolored
        # afterward in Panel Button Colors.
        matching_fg, matching_bg = self._select_colors["matching"]
        missing_fg, missing_bg = self._select_colors["missing"]
        file_parent_fg, file_parent_bg = self._select_colors["file_parent"]

        save_button = QPushButton("Save Settings")
        save_button.setFixedSize(status_btn_w, uniform_h)
        save_button.setStyleSheet(_bevel_button_stylesheet(missing_bg, missing_fg))
        save_button.setToolTip("Save every option and spinbox on this GUI to disk.")
        save_button.clicked.connect(self.save_settings)
        row.addWidget(save_button)

        load_button = QPushButton("Load Settings")
        load_button.setFixedSize(status_btn_w, uniform_h)
        load_button.setStyleSheet(_bevel_button_stylesheet(matching_bg, matching_fg))
        load_button.setToolTip("Reload every option and spinbox on this GUI from disk.")
        load_button.clicked.connect(lambda checked=False: self.load_settings())
        row.addWidget(load_button)

        reset_button = QPushButton(reset_text)
        reset_btn_w = uniform_w + QFontMetrics(self.font()).horizontalAdvance("Reload Defaults") // 2
        reset_button.setFixedSize(reset_btn_w, uniform_h)
        reset_button.setStyleSheet(_bevel_button_stylesheet(file_parent_bg, file_parent_fg))
        reset_button.setToolTip(reset_tooltip)
        reset_button.clicked.connect(lambda checked=False: reset_handler())
        row.addWidget(reset_button)

        row.addStretch(1)
        return row, (save_button, load_button, reset_button)

    def _on_common_font_size_changed(self, value):
        for pane in (self.left, self.right):
            pane.timeline.set_label_font_size(value)

    def _on_common_nice_bins_changed(self, checked):
        for pane in (self.left, self.right):
            pane.timeline.set_nice_bins(checked)
            self._update_statistics_bin_size_label(pane)
        # Stripping calendar units only makes sense with nice bins (the
        # value itself is kept while greyed out).
        self.common_strip_units_cb.setEnabled(checked)

    def _on_common_strip_units_changed(self, checked):
        for pane in (self.left, self.right):
            pane.timeline.set_strip_calendar_units(checked)
        for cb in (self.common_strip_names_cb, self.common_strip_abbreviate_cb,
                   self.common_strip_week_number_cb):
            cb.setEnabled(checked)

    def _on_common_strip_names_changed(self, checked):
        for pane in (self.left, self.right):
            pane.timeline.set_strip_use_names(checked)

    def _on_common_strip_abbreviate_changed(self, checked):
        for pane in (self.left, self.right):
            pane.timeline.set_strip_abbreviate_names(checked)

    def _on_common_strip_week_number_changed(self, checked):
        for pane in (self.left, self.right):
            pane.timeline.set_strip_use_week_number(checked)

    def _on_common_bin_count_changed(self, value):
        for pane in (self.left, self.right):
            pane.timeline.set_bin_count(value)
            self._update_statistics_bin_size_label(pane)

    def _on_common_tick_angle_changed(self, value):
        for pane in (self.left, self.right):
            pane.timeline.set_tick_label_angle(value)

    def _on_common_auto_tick_angle_changed(self, checked):
        # Axis label angle (label + spin box) is what auto mode replaces,
        # so it's greyed out while auto is on -- its value is left alone
        # and takes effect again as soon as auto goes back off.
        self.common_tick_angle_label.setEnabled(not checked)
        self.common_tick_angle_spin.setEnabled(not checked)
        for pane in (self.left, self.right):
            pane.timeline.set_auto_tick_angle(checked)

    def _on_mirror_statistics_buttons_changed(self, checked):
        self._mirror_statistics_buttons = bool(checked)
        # Row1's RightToLeft direction flip and the TimelineWidget's
        # own set_mirrored are both applied once at construction time in
        # _build_statistics_panel -- a full rebuild is the simplest way
        # to get both panes' Statistics views re-evaluated against the
        # new setting, same as every other app-wide toggle that affects
        # more than one already-built widget.
        self._rebuild_ui()

    # -------------------------------------------------------------- Colors & Theme

    def _make_color_swatch(self, get_hex, on_pick) -> QPushButton:
        """A small flat-colored button that opens a QColorDialog on
        click and reports the chosen color back via on_pick(hex_string)
        -- used for every color picker in the Colors & Theme section
        instead of building a picker dialog by hand each time.

        `get_hex` is a zero-arg callable that re-derives the color from
        its actual live source (self._select_colors/TAG_COLORS/
        _SCROLLBAR_COLORS/...) every time it's called, rather than a
        fixed hex string frozen at construction -- this button registers
        its own refresh (alongside every other swatch, in
        self._color_swatches) so _refresh_color_swatches can re-paint
        all of them to whatever their underlying setting currently
        holds. That's needed for the couple of places (load_settings,
        "Reload Defaults") that change this state in bulk directly,
        bypassing the individual _on_*_color_changed handlers a click
        here goes through -- a plain fixed string would have no way to
        notice a change made that way."""
        btn = QPushButton()
        # A little taller than a single line of label text plus the
        # QTableWidget cell padding (padding: 1px 9px, 2px of that
        # vertical) that _style_table_widget sets on every Item cell in
        # these tables -- a label cell's *row* therefore ends up a
        # couple px taller than an 18px-tall swatch, and _center_in_cell
        # centers a fixed-size widget rather than stretching it, so a
        # smaller swatch's own border would stop short of the row's
        # top/bottom edge -- a plain background gap where a grid line
        # should be. 22px clears that gap on every platform/font this
        # app's default sizes need to support.
        btn.setFixedSize(22, 22)

        def _refresh():
            # border/margin baked into the same setStyleSheet() call as
            # background-color (rather than set once at construction
            # and left alone) since setStyleSheet() replaces the whole
            # string -- without repeating them here every refresh (a
            # color pick, or the bulk refresh load_settings/"Reload
            # Defaults" trigger) would silently drop them, leaving the
            # button unbordered/adrift of its cell's grid line after
            # the first refresh. 0 margin sits the swatch's own border
            # flush against the table's own gridline-color border
            # instead of leaving a visible gap on every side.
            # Disabled look: the same color, mostly faded into the window
            # background (a plain stylesheet background otherwise looks
            # identical whether the button is enabled or not).
            faded = _press_blend(get_hex(), self._effective_gui_colors(self._theme)[1], 0.65).name()
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {get_hex()}; border: 1px solid #555555;"
                f" margin: 0px; }}"
                f"QPushButton:disabled {{ background-color: {faded}; border: 1px solid #777777; }}"
            )

        _refresh()
        self._color_swatches.append(_refresh)

        def _on_click(checked=False):
            # Qt's own dialog rather than QColorDialog.getColor: on
            # GTK-themed systems that call opens the platform-native
            # chooser, whose "+" (custom color) editor always starts from
            # a fixed default color, whatever color it was given. Qt's
            # own one opens with the current color already loaded into
            # its editor (and has a "Pick Screen Color" button too).
            dialog = QColorDialog(QColor(get_hex()), self)
            dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            dialog.setWindowTitle("Pick a color")
            color = dialog.currentColor() if dialog.exec() else QColor()
            if color.isValid():
                on_pick(color.name())
                try:
                    _refresh()
                except RuntimeError:
                    # on_pick can itself trigger a full _rebuild_ui() (see
                    # _on_default_button_color_changed) which tears this
                    # very button down as part of rebuilding the whole
                    # Settings tab -- nothing left here to refresh.
                    pass

        btn.clicked.connect(_on_click)
        return btn

    def _make_italic_note(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #A0A0A0; font-style: italic;")
        return label

    def _style_table_widget(self, table: QTableWidget, header_labels: list):
        """Common QTableWidget setup shared by every Colors & Theme
        table (Misc Colors, and everything converted the same way
        after it) -- header labels/styling, read-only/no-selection/
        no-focus behavior (these are display + click-to-pick tables,
        not something to navigate or edit cell text in), and real grid
        lines instead of the old QGridLayout+per-cell-border-QSS trick
        every one of these tables used before conversion. Doesn't size
        the table -- call _size_table_to_contents once every row/cell
        widget has actually been added, since it needs the real content
        in place to measure."""
        table.setHorizontalHeaderLabels(header_labels)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setShowGrid(True)
        table.setStyleSheet(
            "QTableWidget { gridline-color: #555555; border: 1px solid #555555; } "
            "QTableWidget::item { padding: 1px 9px; }"
        )
        table.horizontalHeader().setStyleSheet(
            f"QHeaderView::section {{ {_TABLE_HEADER_STYLE} "
            "border: 1px solid #555555; padding: 1px 9px; background-color: transparent; }"
        )
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionsClickable(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _main_button_color_table(self, rows: list) -> QGroupBox:
        """A framed "Item | Fg | Bg" swatch table for buttons whose colors
        live in self._main_button_colors -- `rows` is (key, label) pairs.
        Shared by Panel Button Colors' Clipboard/File Op Buttons tables and
        File List View Colors' Button Colors table. A button without a
        color of its own shows (and starts from) the theme's default
        button colors -- see _main_button_color_for."""
        group = QGroupBox("")
        group_outer = QVBoxLayout(group)
        table = QTableWidget(len(rows), 3)
        self._style_table_widget(table, ["Item", "Fg", "Bg"])
        for i, (key, label) in enumerate(rows):
            table.setItem(i, 0, self._table_item(label))
            table.setCellWidget(i, 1, self._center_in_cell(self._make_color_swatch(
                lambda k=key: self._main_button_color_for(k)[1],
                lambda hexc, k=key: self._on_main_button_color_changed(k, False, hexc),
            )))
            table.setCellWidget(i, 2, self._center_in_cell(self._make_color_swatch(
                lambda k=key: self._main_button_color_for(k)[0],
                lambda hexc, k=key: self._on_main_button_color_changed(k, True, hexc),
            )))
        self._size_table_to_contents(table)
        group_outer.addWidget(table)
        return group

    def _table_item(self, text: str) -> QTableWidgetItem:
        """A plain, non-interactive label cell -- Qt.ItemIsEnabled only
        (no ItemIsSelectable/ItemIsEditable), matching the read-only
        QLabel cells these tables used before conversion."""
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        return item

    def _center_in_cell(self, widget: QWidget) -> QWidget:
        """Wraps `widget` (a fixed-size swatch button, a checkbox) in a
        plain container so it can be centered inside a QTableWidget
        cell. setCellWidget resizes/positions its widget to fill the
        cell's own rect directly rather than respecting alignment or a
        fixed size the way QGridLayout.addWidget(..., Qt.AlignCenter)
        does, so a fixed-size widget passed to it straight sits pinned
        to the cell's top-left corner instead of centered -- wrapping
        it in an otherwise-empty QHBoxLayout with Qt.AlignCenter is
        what actually centers it.

        1px top/bottom margin (rather than 0) so the swatch's own
        22x22 fixed size is what drives each row's height via
        resizeRowsToContents (see _size_table_to_contents) with a
        little headroom on top of it, instead of the swatch exactly
        filling the row -- flush top-to-bottom left it looking a touch
        cramped/short against the row's grid lines."""
        wrap = QWidget()
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.addWidget(widget, 0, Qt.AlignCenter)
        return wrap

    def _size_table_to_contents(self, table: QTableWidget):
        """Sizes `table` to exactly fit its own header + rows + columns,
        with no internal scrollbars -- it's meant to sit statically
        alongside its row's other widgets (see _settings_subsection/
        "own row + trailing stretch"), never to scroll internally.

        First cut of this hand-summed frameWidth()/columnWidth()/
        rowHeight() into a setFixedSize() call -- close, but
        consistently landed a bit short (only 5 of 7 Misc Colors
        rows and part of the Bg column visible, needing the table's
        own scrollbars to reach the rest) once actually on screen
        rather than in an offscreen smoke test. Root cause: hand-
        summing pixel metrics is exactly the fragile approach
        AdjustToContents exists to replace -- it's QAbstractScrollArea's
        own sanctioned mechanism for "size this scroll area to its
        content, not a fixed request," so it's what every future table
        conversion should use here too rather than another manual sum."""
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.resizeColumnsToContents()
        # resizeColumnsToContents only measures header/item text, never
        # cell widgets -- so a column holding nothing but a widget wider
        # than its header (the "Bold" checkbox under the Misc Colors
        # table's "Bg" header) came out too narrow and clipped the
        # widget's own text.
        for col in range(table.columnCount()):
            needed = table.columnWidth(col)
            for row in range(table.rowCount()):
                cell_widget = table.cellWidget(row, col)
                if cell_widget is not None:
                    # Polished first, so the size hint reflects the
                    # style/stylesheet the widget will actually be
                    # drawn with rather than an unstyled default.
                    cell_widget.ensurePolished()
                    for child in cell_widget.findChildren(QWidget):
                        child.ensurePolished()
                    hint = max(cell_widget.sizeHint().width(), cell_widget.minimumSizeHint().width())
                    needed = max(needed, hint + 12)
            table.setColumnWidth(col, needed)
        table.resizeRowsToContents()
        table.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        table.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
