"""
ResponsiveLayoutMixin: the resize-driven part of the UI -- resizeEvent/showEvent,
_update_responsive_layout (wide vs narrow switching) and the per-group
layout helpers it drives (_layout_pane_cluster, _layout_controls_group,
_layout_select_group/_layout_file_op_group/_layout_clipboard_group,
_relayout_shared_controls, _update_pane_date_columns).

Split out of ui_builder.py unchanged -- same methods, still reached as
`self._...` because UiBuilderMixin inherits this class.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QGridLayout, QHBoxLayout

from constants import _CLUSTER_GROUP_GAP
from custom_widgets import _MAIN_BUTTON_ICON_KINDS
from listing_ops import COL_ACCESSED, COL_CHANGED, COL_MODIFIED
from panes import DirectoryPane

# Which file-list date column a given Statistics-view Metric maps to
# when the narrow layout collapses the three date columns to one.
# "size" has no date column of its own, so it falls back to Modified --
# the one date column that means the same thing on both Windows and
# POSIX (unlike the Changed column, which is ctime: "created" on
# Windows, "metadata changed" on POSIX) and the one most file managers
# already default to.
_METRIC_TO_DATE_COLUMN = {
    "modified": COL_MODIFIED,
    "changed": COL_CHANGED,
    "accessed": COL_ACCESSED,
    "size": COL_MODIFIED,
}
_DATE_COLUMNS = (COL_MODIFIED, COL_CHANGED, COL_ACCESSED)


class ResponsiveLayoutMixin:

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_responsive_layout()

    # --------------------------------------------------- Responsive layout

    def _update_responsive_layout(self, force: bool = False):
        """Cheap on every resize: only actually reflows anything the
        moment the window's width crosses the narrow-layout threshold,
        not on every pixel of resize in between. `force=True` (used once,
        right after _build_ui, and whenever Dual Pane is toggled) applies
        the current width's arrangement even though _narrow_layout hasn't
        "changed" yet -- it has no prior value at that point, or (Dual
        Pane) the window's own width just changed too so last time's
        narrow/wide verdict doesn't necessarily still hold.

        The thresholds themselves are now user-settable (Global Options'
        "Dual Pane Width Threshold" / "Single Pane Width Threshold"
        spinboxes) rather than fixed at DUAL_PANE_NARROW_THRESHOLD and
        half of it -- those two spinboxes' current values are what's
        actually read here now; DUAL_PANE_NARROW_THRESHOLD only supplies
        the starting default."""
        if (
            not getattr(self, "left", None)
            or not getattr(self.left, "cluster_container", None)
            or not getattr(self, "_bottom_row_layout", None)
            or not getattr(self, "filter_frame_group", None)
            or not getattr(self, "global_options_group", None)
        ):
            return  # called mid-construction, before panes/bottom_row exist yet

        threshold = self._dual_pane_threshold if self._dual_pane_enabled else self._single_pane_threshold
        narrow = self.width() < threshold
        if not force and getattr(self, "_narrow_layout", None) == narrow:
            return
        self._narrow_layout = narrow

        # Drop these groups' titles entirely while narrow, to save a
        # little vertical space -- each one otherwise reserves a title
        # bar's height purely for a label whose *content* isn't
        # essential (unlike, say, the Select group's live selection
        # count used to be -- see _set_transfer_label/_build_status_
        # line for where that moved instead). margin_rule below also
        # collapses the (still-reserved-regardless-of-title-text)
        # native top margin QGroupBox leaves for its title bar -- flat
        # alone (still set, in case a given Qt style does shrink it
        # further on its own) wasn't enough to close that gap.
        # margin-top *and* padding-top both zeroed, not just margin --
        # Fusion's own QGroupBox stylesheet reserves padding-top based
        # on the title font's height regardless of whether the title
        # string is actually empty, so a margin-only rule still left a
        # thin blank strip above these two boxes' own contents (reading
        # as a stray empty title/label line sitting right above the
        # status line's progress bars once the boxes were embedded at
        # the very bottom of the narrow layout).
        margin_rule = "QGroupBox { margin-top: 0px; padding-top: 0px; } " if narrow else ""
        # Same idea, one level up: with titles gone and each QGroupBox's
        # own title-bar margin already collapsed to 2px above, the
        # normal (wide-layout) gap *between* these boxes -- both the
        # Clipboard/Select+File-Op rows stacked inside each pane's own
        # cluster_container (see _layout_pane_cluster below) and, one
        # level further out, the gap from those down to Global Options/
        # Global File Filter and the status line (main_layout's own
        # spacing, between self.splitter/bottom_row/the status line --
        # see _build_main_tab) -- reads as an oversized leftover gap
        # where a title bar used to visually justify it. Matches
        # margin_rule's own 2px so everything narrow collapses to one
        # consistent tight spacing instead of each shrinking by a
        # different, unrelated amount.
        self._main_layout.setSpacing(2 if narrow else 6)
        # Same tightening for the overall margin around everything --
        # the fixed 10px this always had regardless of narrow/wide
        # left a noticeable, uncompacted gap above the splitter and
        # below the status line's progress bars, on top of (not
        # instead of) the row-to-row spacing above.
        if narrow:
            self._main_layout.setContentsMargins(4, 4, 4, 4)
        else:
            self._main_layout.setContentsMargins(10, 10, 10, 10)
        if getattr(self, "_status_line_layout", None):
            self._status_line_layout.setSpacing(2 if narrow else 6)
        # Select/File Op/Clipboard no longer need title-collapsing here --
        # while narrow they're hidden outright, folded into the single
        # "Controls" group per pane instead (see _layout_controls_group),
        # so their own titles never render either way. Global File
        # Filter still goes through this unconditionally: it's always
        # embedded (flat, titleless) into the left pane's Controls group
        # while narrow, dual pane or not. Global Options only collapses
        # when narrow *and* Dual Pane is on -- that's the only
        # combination where it actually gets embedded into the right
        # pane's Controls group (see _layout_controls_group's own
        # `if self._dual_pane_enabled` there); narrow + single-pane
        # instead leaves it sitting alone in bottom_row (see
        # _relayout_shared_controls), where it keeps its normal
        # wide-style title/border/size rather than reading as an
        # embedded sub-section of a pane that, in single-pane mode,
        # doesn't have anything to embed it into.
        filter_narrow = narrow
        # Global Options' own title/border/top margin now drop out
        # whenever narrow, single pane included -- in single pane it sits
        # alone in bottom_row, and its title bar was the empty strip above
        # it. What still depends on Dual Pane is only its *content*:
        # collapsing to one row with shortened labels happens when it's
        # embedded into the right pane's Controls group
        # (global_options_narrow); narrow + single pane keeps the normal
        # two rows and full labels.
        global_options_narrow = narrow and self._dual_pane_enabled
        if getattr(self, "filter_frame_group", None) is not None:
            self.filter_frame_group.setTitle("" if filter_narrow else "Global File Filter")
            self.filter_frame_group.setFlat(filter_narrow)
            self.filter_frame_group.setStyleSheet(margin_rule if filter_narrow else "")
        if getattr(self, "global_options_group", None) is not None:
            self.global_options_group.setTitle("" if narrow else "Global Options")
            self.global_options_group.setFlat(narrow)
            self.global_options_group.setStyleSheet(margin_rule if narrow else "")

        self._relayout_shared_controls(narrow)
        self._relayout_global_options_group(global_options_narrow)

        panes = (self.left,) if not self._dual_pane_enabled else (self.left, self.right)
        for pane in panes:
            is_left = pane is self.left
            layout_left = is_left if self._symmetrical_buttons else True
            self._layout_pane_cluster(pane, is_left, layout_left)
            self._update_pane_date_columns(pane)

        # Filter/Global Options are two differently-tall shared groups
        # embedded below each pane's own button grid -- Filter is a
        # single row, and Global Options is now also collapsed to a
        # single row while narrow (see _relayout_global_options_group)
        # -- but their
        # exact heights can still differ a little (different font
        # metrics/checkbox counts), which would otherwise throw left
        # and right panes' Controls groups out of sync with each other
        # even though neither pane's own content changed. Pin both to
        # whichever is taller.
        if narrow and self._dual_pane_enabled:
            self.left.controls_group.setMinimumHeight(0)
            self.right.controls_group.setMinimumHeight(0)
            h = max(
                self.left.controls_group.sizeHint().height(),
                self.right.controls_group.sizeHint().height(),
            )
            self.left.controls_group.setMinimumHeight(h)
            self.right.controls_group.setMinimumHeight(h)
        else:
            self.left.controls_group.setMinimumHeight(0)
            self.right.controls_group.setMinimumHeight(0)

        # (No more Status-group geometry to recompute here -- the status
        # line is a normal row in main_layout now, not a floating widget
        # anchored to the Clipboard groups' edges.)

    def _set_paste_folders_compact(self, pane: DirectoryPane, compact: bool):
        """Toggles Paste Folders between its normal look (full "Paste
        Folders" label, icon, natural width -- see its own construction
        in _build_directory_widgets for why it's exempt from the
        shared uniform width there) and a compact one matching every
        other button's fixed uniform size: shortened to "Paste Fol.",
        icon dropped, sized the same as its row-mates. Narrow mode's
        single-row Controls group is a fixed-size grid every other
        button in that row already fits without truncating or
        overflowing -- Paste Folders was the one exception, both
        widening its own column and, since Qt sizes a shared column to
        its widest occupant, the same column in every other row of that
        grid along with it.

        Called from _layout_controls_group (compact=True) and
        _layout_clipboard_group (compact=False) -- both already run
        exactly once per narrow/wide transition (see
        _layout_pane_cluster), so no separate call site is needed just
        for this."""
        btn = pane._clipboard_buttons["paste_folders"]
        uniform_w, uniform_h = self._uniform_button_size()
        if compact:
            btn.setText("Paste Fol.")
            btn.setIcon(QIcon())
            btn.setFixedSize(uniform_w, uniform_h)
        else:
            btn.setText("Paste Folders")
            _, fg = self._main_button_color_for("paste_folders")
            btn.setIcon(self._make_icon(_MAIN_BUTTON_ICON_KINDS["paste_folders"], fg))
            # Un-fixes the width _set_paste_folders_compact's own
            # compact branch set, letting it size naturally to its
            # (longer) label again -- setFixedHeight alone doesn't
            # touch whatever setFixedSize last did to the width.
            btn.setMinimumWidth(0)
            btn.setMaximumWidth(16777215)
            btn.setFixedHeight(uniform_h)

    def _layout_clipboard_group(self, pane: DirectoryPane, mirrored_right: bool):
        """(Re)builds the Clipboard group's own internal button layout:
        a 3-row, 2-column grid (Move-->/Copy-->, Copy/Cut, Paste/Paste Folders), mirrored
        column-for-column for a mirrored right pane. Only ever called
        while wide -- narrow instead folds these same buttons into the
        pane's single "Controls" group (a single row there instead --
        see _layout_controls_group), Clipboard's own group hidden
        entirely (see _layout_pane_cluster). Same detach-then-rebuild
        approach as _layout_pane_cluster: the group's own top-level
        layout is a persistent QVBoxLayout (see its construction), and
        only the inner layout actually holding the buttons gets torn
        down and replaced -- the buttons themselves
        (pane._clipboard_buttons) are untouched, just re-parented into
        the fresh inner layout."""
        outer = pane.clipboard_group.layout()
        self._detach_layout_widgets(outer)
        self._set_paste_folders_compact(pane, False)

        b = pane._clipboard_buttons
        # Explicit alignment matters here because "Paste Folders" is
        # wider than the other buttons (no setFixedWidth, just
        # setFixedHeight -- see its own construction), so its column
        # ends up wider than its neighbor's -- without an explicit
        # alignment the narrower buttons just sit at Qt's default
        # (left) within that extra width, which reads as left-aligned
        # even in the mirrored right-pane cluster where everything else
        # (titles, File Op's buttons) hugs the right edge instead.
        button_align = Qt.AlignRight if mirrored_right else Qt.AlignLeft

        #   Row 1: Move-->, Copy-->      (left pane) / <--Copy, <--Move      (mirrored right)
        #   Row 2: Copy,    Cut                      / Cut,     Copy
        #   Row 3: Paste,   Paste Folders            / Paste Folders, Paste
        # Row 1 is the across-pane pair (pane._across_buttons) -- same
        # left-to-right sequence they had in the Statistics title row.
        across = pane._across_buttons
        grid = QGridLayout()
        clipboard_rows = [
            (across["move"], across["copy"]),
            (b["copy"], b["cut"]),
            (b["paste"], b["paste_folders"]),
        ]
        for row_index, (col_a, col_b) in enumerate(clipboard_rows):
            first, second = (col_b, col_a) if mirrored_right else (col_a, col_b)
            grid.addWidget(first, row_index, 0, alignment=button_align)
            grid.addWidget(second, row_index, 1, alignment=button_align)
        outer.addLayout(grid)

    def _detach_layout_widgets(self, layout):
        """Empties `layout`, detaching (not deleting) every widget it
        directly contains, and recursing into (then discarding) any
        dynamically-created sub-layouts found along the way -- e.g.
        Clipboard's own inner grid/row (_layout_clipboard_group) or a
        pane cluster's row(s) (_layout_pane_cluster). Leaves `layout`
        itself intact and empty, ready for fresh addWidget/addLayout
        calls; the detached widgets are untouched (same parent, just no
        longer positioned by this layout) until re-added somewhere."""
        while layout.count():
            item = layout.takeAt(0)
            sub = item.layout()
            if sub is not None:
                self._detach_layout_widgets(sub)
                sub.deleteLater()

    def _layout_select_group(self, pane: DirectoryPane, layout_left: bool):
        """(Re)populates the Select group's own grid for the given
        layout_left -- the same 4-column arrangement this group has
        always used (Row 1: Select All/Missing/Newer/Smaller, Row 2:
        Invert/Matching/Older/Larger, Row 3: Clear/Diff Folders/All
        Parents/File Parent -- mirrored column-for-column when not
        layout_left), just replayed on demand so a narrow-to-wide
        crossing (see _layout_controls_group, which is where these
        buttons live while narrow) can put them back without a full
        _rebuild_ui. Safe to call repeatedly -- always clears the grid
        first."""
        grid = pane.select_group.layout()
        self._detach_layout_widgets(grid)

        sp = pane.select_plain_buttons
        st = pane.select_toggle_buttons
        SELECT_COLS = 4

        def col(c):
            return c if layout_left else (SELECT_COLS - 1) - c

        # Same edge rule as the File Op/Clipboard groups (see
        # _layout_file_op_group/_layout_clipboard_group): hug the right
        # edge of each cell when mirrored, left otherwise. Every Select
        # button is currently the same width so this is a no-op today,
        # but it keeps the rule from silently breaking if one ever isn't.
        align = Qt.AlignLeft if layout_left else Qt.AlignRight

        grid.addWidget(sp["select_all"], 0, col(0), alignment=align)
        grid.addWidget(st["missing"], 0, col(1), alignment=align)
        grid.addWidget(st["newer"], 0, col(2), alignment=align)
        grid.addWidget(st["smaller"], 0, col(3), alignment=align)
        grid.addWidget(pane.invert_button, 1, col(0), alignment=align)
        grid.addWidget(st["matching"], 1, col(1), alignment=align)
        grid.addWidget(st["older"], 1, col(2), alignment=align)
        grid.addWidget(st["larger"], 1, col(3), alignment=align)
        grid.addWidget(sp["clear"], 2, col(0), alignment=align)
        grid.addWidget(st["diff_folders"], 2, col(1), alignment=align)
        grid.addWidget(sp["all_parents"], 2, col(2), alignment=align)
        grid.addWidget(sp["file_parent"], 2, col(3), alignment=align)

    def _layout_file_op_group(self, pane: DirectoryPane, mirrored_right: bool):
        """(Re)populates the File Op group's own layout -- always the
        same Rename/New Folder/Delete vertical order (no meaningful
        "mirror" for a single stacked column), right-aligned instead of
        the default left when mirrored_right. Same detach-then-rebuild
        approach as _layout_select_group, for the same reason -- these
        buttons live in the narrow "Controls" group instead while
        narrow (see _layout_controls_group)."""
        layout = pane.file_op_group.layout()
        self._detach_layout_widgets(layout)
        align = Qt.AlignRight if mirrored_right else Qt.AlignLeft
        for btn in (pane.rename_button, pane.new_folder_button, pane.delete_button):
            layout.addWidget(btn, alignment=align)

    def _layout_controls_group(self, pane: DirectoryPane, is_left: bool, mirrored: bool):
        """(Re)builds the consolidated narrow-layout "Controls" group
        for one pane: every Select/File Op/Clipboard button collapsed
        into a single 5-column grid --

            Row 1: Select All, Missing, Newer, Smaller, Rename
            Row 2: Invert (Select), Matching, Older, Larger, New Folder
            Row 3: Clear, Diff Folders, All Parents, File Parent, Delete
            Row 4: Copy, Cut, Paste, Paste Folders, Copy-->
                   (Copy--> in the bottom-right corner slot; the
                   across-pane Move--> is left out while narrow)

        -- plus, below that grid (as its own full-width widget on the
        group's outer layout, not a 5th grid row -- see
        _build_directory_widgets' controls_outer comment for why),
        Global File Filter (left pane) / Global Options (right pane),
        embedded wholesale. In place of the three separately-titled
        Select/File Op/Clipboard groups (hidden entirely while narrow --
        see _layout_pane_cluster). Filter/Global Options are the two
        actual shared QGroupBox widgets (self.filter_frame_group /
        self.global_options_group), already detached from bottom_row by
        _relayout_shared_controls -- this call just embeds whichever one
        belongs to this pane. Filter's own Invert (File Filter) checkbox
        travels along with filter_frame_group itself now, repositioned
        into that group's own row 0 by _relayout_filter_frame rather
        than pulled out into this grid, so it's not one of the widgets
        placed here.

        `mirrored` reverses each button row left-to-right (Symmetrical
        GUI on the right pane), same convention as every other row in
        this cluster -- except the embedded Filter/Global Options
        group itself, which never moves: "only buttons are mirrored,
        not the options."

        Always starts by clearing the group's own outer layout (which
        also discards the previous call's button grid -- see
        _detach_layout_widgets -- so a fresh one is built here each
        time) -- safe to call repeatedly."""
        outer = pane.controls_group.layout()
        self._detach_layout_widgets(outer)
        self._set_paste_folders_compact(pane, True)

        sp = pane.select_plain_buttons
        st = pane.select_toggle_buttons
        b = pane._clipboard_buttons
        across_copy = pane._across_buttons["copy"]

        grid = QGridLayout()
        grid.setSpacing(3)
        grid.setContentsMargins(0, 0, 0, 0)

        # Buttons narrower than their column (the compact clipboard buttons
        # sit in columns sized by the full-width Select/File Op buttons
        # above them) hug the column's outer edge instead of Qt's default
        # left -- right for a mirrored right pane (Symmetrical GUI on), left
        # otherwise, so the right pane is a true mirror image of the left
        # and, with Symmetrical GUI off, an exact copy of it.
        cell_align = Qt.AlignRight if mirrored else Qt.AlignLeft

        def _row(row_index, widgets):
            order = list(reversed(widgets)) if mirrored else list(widgets)
            for col, w in enumerate(order):
                grid.addWidget(w, row_index, col, alignment=cell_align)

        _row(0, [sp["select_all"], st["missing"], st["newer"], st["smaller"], pane.rename_button])
        _row(1, [pane.invert_button, st["matching"], st["older"], st["larger"], pane.new_folder_button])
        _row(2, [sp["clear"], st["diff_folders"], sp["all_parents"], sp["file_parent"], pane.delete_button])

        if is_left:
            # Row 4: the four Clipboard buttons left-aligned in cols 0-3,
            # leaving col 4 -- the same column Rename/New Folder/Delete
            # use above -- for the across-pane Copy--> button.
            for col, w in enumerate([b["copy"], b["cut"], b["paste"], b["paste_folders"]]):
                grid.addWidget(w, 3, col, alignment=cell_align)
            grid.addWidget(across_copy, 3, 4, alignment=cell_align)
            # grid itself is wrapped in its own row with a trailing
            # stretch, rather than added straight to `outer` (a
            # QVBoxLayout, which would otherwise hand it the *entire*
            # width controls_group now stretches to -- see
            # _layout_pane_cluster's own controls_group.addWidget(...,
            # 1)). A QGridLayout given more width than its columns'
            # combined natural size distributes that leftover across its
            # columns since none of them have an explicit stretch factor
            # of their own, which is exactly what was spreading these
            # Fixed-size buttons apart as the window resized. Boxing the
            # grid at its own natural size and letting a real stretch
            # item soak up the leftover instead keeps button-to-button
            # spacing constant, same as wide mode's own groups now.
            grid_row = QHBoxLayout()
            grid_row.addLayout(grid)
            grid_row.addStretch(1)
            outer.addLayout(grid_row)
            outer.addWidget(self.filter_frame_group)
            self.filter_frame_group.setVisible(True)
        else:
            if mirrored:
                # Column-for-column mirror of the left pane's row 4:
                # col c -> 4 - c, so the across-pane <--Copy button
                # takes col 0 and the four Clipboard buttons sit at
                # cols 1-4 (Copy outermost, at col 4).
                grid.addWidget(across_copy, 3, 0, alignment=cell_align)
                for col, w in enumerate([b["paste_folders"], b["paste"], b["cut"], b["copy"]], start=1):
                    grid.addWidget(w, 3, col, alignment=cell_align)
            else:
                # Symmetrical GUI off: an exact copy of the left pane's row.
                for col, w in enumerate([b["copy"], b["cut"], b["paste"], b["paste_folders"]]):
                    grid.addWidget(w, 3, col, alignment=cell_align)
                grid.addWidget(across_copy, 3, 4, alignment=cell_align)
            # Same grid-stays-fixed-size reasoning as the is_left branch
            # above -- stretch on whichever side keeps the grid hugging
            # the same edge Select/File Op/Clipboard's own wide-mode
            # buttons would (mirrored: right edge, matching File Op's
            # own mirrored_right alignment; unmirrored: left edge, same
            # as the left pane's own grid).
            grid_row = QHBoxLayout()
            if mirrored:
                grid_row.addStretch(1)
                grid_row.addLayout(grid)
            else:
                grid_row.addLayout(grid)
                grid_row.addStretch(1)
            outer.addLayout(grid_row)
            if self._dual_pane_enabled:
                outer.addWidget(self.global_options_group)
                self.global_options_group.setVisible(True)

    def _relayout_shared_controls(self, narrow: bool):
        """Places the shared Global File Filter / Global Options groups
        (and Filter's own Invert checkbox) for the current narrow/wide
        state. Called once per _update_responsive_layout pass, before
        the per-pane loop below, so _layout_controls_group just finds
        them already detached from bottom_row and ready to embed,
        rather than each pane racing to detach them itself (which,
        called once per pane, would risk double-adding them back to
        bottom_row on the way to wide).

        Wide: both groups sit in bottom_row as normal -- Filter with the
        row's stretch factor so it (and, inside it, just its text entry)
        widens into any extra width, Global Options at its natural size,
        flush right. Invert back below the filter entry in Filter's own
        grid.
        Narrow, Dual Pane on: Filter (Invert included -- see
        _relayout_filter_frame, now moved into Filter's own row 0
        between the entry and Apply rather than detached out of the
        group entirely) embeds into the left Controls group, below its
        button grid; Global Options embeds into the right Controls
        group the same way -- see _layout_controls_group. Both then
        stretch to their own pane's full width (and, inside Filter,
        just its text entry) via that group's own outer-layout widget
        placement, same mechanism as Filter's wide-mode stretch above.
        Narrow, Dual Pane off (single pane -- no right Controls group
        to hold it): Global Options stays in bottom_row untouched, at
        its normal wide-mode title/size (see _update_responsive_layout's
        narrow_global_options exception) rather than the embedded
        narrow look, pushed to the row's right edge by a leading
        stretch since it's otherwise the only thing left in an
        already-empty row. Only Filter folds into the left pane's
        Controls group, same as dual-pane narrow -- and, since that's
        the pane's *only* visible group at single-pane width, ends up
        stretching to the GUI's full width rather than just half of it.

        Always starts by fully clearing bottom_row_layout (not just
        removing the two group widgets) so a stray stretch item from a
        previous single-pane-narrow pass can't accumulate across
        repeated narrow/wide or Dual Pane toggles -- widgets themselves
        are only detached, not deleted, same as _detach_layout_widgets."""
        while self._bottom_row_layout.count():
            self._bottom_row_layout.takeAt(0)
        self._relayout_filter_frame(narrow)

        if not narrow:
            self._bottom_row_layout.addWidget(self.filter_frame_group, 1)
            self._bottom_row_layout.addWidget(self.global_options_group)
        elif not self._dual_pane_enabled:
            self._bottom_row_layout.addStretch(1)
            self._bottom_row_layout.addWidget(self.global_options_group)
        # else: narrow + Dual Pane on -- both stay detached here, for
        # the per-pane loop below (_layout_controls_group) to embed.

    def _apply_narrow_pane_gaps(self, pane: DirectoryPane, narrow: bool):
        """Narrow layout only: zero the default margins/spacing on the
        pane's own outer layout (the one holding the tree splitter and
        the button cluster) and on the cluster's inner layout. Those were
        the last untouched Qt defaults -- together roughly a full empty
        line above the button groups and another between them and the
        Global Options/status line below. The wide layout's original
        values were remembered at build time (see _build_directory_
        widgets) and come back on the way out. Safe to call before the
        pane is fully built (the first _layout_pane_cluster call happens
        ahead of that) -- it just does nothing then."""
        pane_layout = getattr(pane, "_pane_layout", None)
        wide = getattr(pane, "_pane_layout_wide", None)
        wide_cluster = getattr(pane, "_cluster_layout_wide_margins", None)
        if pane_layout is None or wide is None or wide_cluster is None:
            return
        cluster_layout = pane.cluster_container.layout()
        if narrow:
            pane_layout.setContentsMargins(0, 0, 0, 0)
            pane_layout.setSpacing(2)
            cluster_layout.setContentsMargins(0, 0, 0, 0)
        else:
            pane_layout.setContentsMargins(wide[0])
            pane_layout.setSpacing(wide[1])
            cluster_layout.setContentsMargins(wide_cluster)

    def _layout_pane_cluster(self, pane: DirectoryPane, is_left: bool, layout_left: bool):
        """(Re)builds the row(s) inside pane.cluster_container holding
        Select/File Op/Clipboard/Controls, for the current
        _narrow_layout state. Safe to call repeatedly -- clears
        whatever rows are there first without deleting the group-box
        widgets themselves, then adds them back to fresh row layouts.
        `is_left` still decides which pane's own Controls-group calls
        this makes downstream (_layout_controls_group's per-pane column
        arrangement); it no longer picks an edge for the narrow row's
        stretch, since controls_group fills its own full width there
        (see its stretch-factor placement below). In the wide branch,
        alignment follows mirrored_right instead -- see _add_row's own
        comment: groups are left-aligned to their pane, except a
        mirrored right pane (Symmetrical GUI on), which is right-aligned.
        `layout_left` decides internal left-to-right order within a row
        (mirrored when Symmetrical Buttons is on and this is the right
        pane).

        Narrow: Select/File Op/Clipboard are hidden entirely and their
        buttons folded into the single Controls group instead -- see
        _layout_controls_group. Wide: the reverse -- Controls is
        hidden/emptied and Select/File Op/Clipboard get their buttons
        back via _layout_select_group/_layout_file_op_group/
        _layout_clipboard_group."""
        outer = pane.cluster_container.layout()
        narrow = getattr(self, "_narrow_layout", False)

        # Same collapse as _update_responsive_layout's margin_rule/
        # main_layout tightening -- see there.
        outer.setSpacing(2 if narrow else 6)
        self._apply_narrow_pane_gaps(pane, narrow)

        # Detach every widget from whatever row layout(s) currently hold
        # them, then discard those now-empty row layouts. The group-box
        # widgets themselves are untouched (still children of
        # cluster_container) and get re-added below.
        self._detach_layout_widgets(outer)

        select_group = pane.select_group
        file_op_group = pane.file_op_group
        clipboard_group = pane.clipboard_group
        controls_group = pane.controls_group
        mirrored_right = (not is_left) and self._symmetrical_buttons

        if narrow:
            select_group.setVisible(False)
            file_op_group.setVisible(False)
            clipboard_group.setVisible(False)
            # Detach first -- otherwise, e.g., a Clipboard button added
            # to Controls' grid below would still also be referenced by
            # clipboard_group's own (now-hidden) inner layout from a
            # previous wide pass.
            self._detach_layout_widgets(select_group.layout())
            self._detach_layout_widgets(file_op_group.layout())
            self._detach_layout_widgets(clipboard_group.layout())

            self._layout_controls_group(pane, is_left, mirrored_right)
            controls_group.setVisible(True)

            # controls_group gets the row's own stretch factor (rather
            # than a separate addStretch spacer soaking up any leftover
            # width) so it's the one that actually grows to fill the
            # pane -- and, inside it, Filter's own text entry is what
            # grows within *that* (see controls_outer's own comment in
            # _build_directory_widgets). The previous addStretch-based
            # version left controls_group pinned to its own sizeHint
            # width regardless of how wide the pane actually was, which
            # is why Filter's entry never visibly resized here even
            # after the controls_outer restructuring below. is_left no
            # longer needs to pick which edge controls_group hugs, since
            # it now fills the whole row either way.
            row = QHBoxLayout()
            row.addWidget(controls_group, 1)
            outer.addLayout(row)
        else:
            controls_group.setVisible(False)
            # Detach first -- same reasoning as above, reversed: a
            # button living in Controls' grid from a previous narrow
            # pass must be freed before _layout_select_group etc. below
            # try to place it back into their own groups.
            self._detach_layout_widgets(controls_group.layout())

            select_group.setVisible(True)
            file_op_group.setVisible(True)
            clipboard_group.setVisible(True)
            # Titles hug the same edge the buttons inside do: right for a
            # mirrored right pane (Symmetrical GUI on), left otherwise.
            title_align = Qt.AlignRight if mirrored_right else Qt.AlignLeft
            for group in (select_group, file_op_group, clipboard_group):
                group.setAlignment(title_align)
            self._layout_select_group(pane, layout_left)
            self._layout_file_op_group(pane, mirrored_right)
            self._layout_clipboard_group(pane, mirrored_right)

            def _add_row(widgets):
                # Fixed gap between each pair of groups (_CLUSTER_GROUP_GAP)
                # and a single stretch on the side the row is aligned
                # *away* from, so the groups sit together against their
                # pane's own edge instead of spreading apart across it.
                # Fixed gaps (rather than the interior stretches this used
                # to have) also mean the gaps are identical in both panes
                # no matter how wide each currently is -- and, since a
                # mirrored right pane just reverses the same widget list,
                # each gap still lands between the same two groups as on
                # the left. Alignment follows mirrored_right, not is_left
                # alone: the left pane is always left-aligned, and the
                # right pane is right-aligned only under Symmetrical GUI
                # (leading stretch) -- with it off, the right pane is a
                # plain copy of the left (same order, left-aligned).
                # addSpacing (not QHBoxLayout.setSpacing) so the edge
                # stretch doesn't also pick up a gap of its own, which
                # would eat into the row's minimum width for nothing.
                row = QHBoxLayout()
                row.setSpacing(0)
                if mirrored_right:
                    row.addStretch(1)
                for i, w in enumerate(widgets):
                    if i > 0:
                        row.addSpacing(_CLUSTER_GROUP_GAP)
                    row.addWidget(w)
                if not mirrored_right:
                    row.addStretch(1)
                outer.addLayout(row)

            cluster = (
                (select_group, file_op_group, clipboard_group) if layout_left
                else (clipboard_group, file_op_group, select_group)
            )
            _add_row(cluster)

    def _update_pane_date_columns(self, pane: DirectoryPane):
        """Narrow layout (or "Use Only 1 Date Column" forcing it
        regardless of width -- see that Settings-tab checkbox): collapse
        the file list's three date columns (Modified/Changed/Accessed)
        down to just the one matching this pane's own Statistics-view
        Metric selection (_METRIC_TO_DATE_COLUMN above) -- Name/Size/
        Type stay as they are either way. Otherwise: all three date
        columns shown, as originally."""
        tree = pane.tree
        if tree is None:
            return
        if not (getattr(self, "_narrow_layout", False) or getattr(self, "_force_single_date_column", False)):
            for col in _DATE_COLUMNS:
                tree.setColumnHidden(col, False)
            return
        metric = pane.tl_metric_combo.currentData() if pane.tl_metric_combo else "modified"
        keep_col = _METRIC_TO_DATE_COLUMN.get(metric, COL_MODIFIED)
        for col in _DATE_COLUMNS:
            tree.setColumnHidden(col, col != keep_col)
