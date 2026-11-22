"""
ResponsiveLayoutMixin: the resize-driven part of the UI -- how each pane's
Select / File Op / Clipboard button groups are arranged for the width their
pane currently has (_relayout_pane_cluster and friends), and which of the
file list's date columns are shown (_update_pane_date_columns).

How the arrangement works
-------------------------
There is no wide/narrow switch. Every group (Select, File Op, Clipboard)
is an ordered list of buttons (_group_buttons) that is wrapped into a given
number of columns, and the cluster picks -- for the width of its pane -- the
combination of

  * where the groups sit relative to each other (_TOPOLOGIES), and
  * how many columns each group's buttons wrap into

that fits the width with the least total height. Select is always the first
group (leftmost, or rightmost for the mirrored right pane) and keeps its 4
semantic columns for as long as any arrangement allows it; only then does it
give up columns, one at a time. Among arrangements of (nearly) equal height
the earlier one in _TOPOLOGIES wins, which gives the order:

  1. Select | File Op | Clipboard, all in one row
  2. File Op moves under Select (its 3 buttons side by side), Clipboard
     stays on the right
  3. Clipboard moves under Select -- as wide as the pane allows -- and
     File Op takes its place next to Select
  ... then File Op and Clipboard side by side under Select, and finally
  everything stacked in one column.

Buttons wrap in the order they are listed in: a group with fewer columns
than buttons-per-row pushes the last button of a row to the start of the
next one (Select instead wraps whole semantic columns). The mirrored right
pane (Symmetrical GUI on) is the exact mirror image: groups right-aligned,
rows starting at the right edge.

Adding a button later only means appending it to its group's list in
_group_buttons -- the wrapping takes care of the rest.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout

from constants import _CLUSTER_GROUP_GAP, _CLUSTER_V_GAP, _PANE_GAP
from listing_ops import COL_ACCESSED, COL_CHANGED, COL_MODIFIED
from panes import DirectoryPane

# Which file-list date column a given Statistics-view Metric maps to
# when "Use Only 1 Date Column" collapses the three date columns to one.
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

# The three groups, in the order their sizes are measured.
_GROUP_KEYS = ("select", "file_op", "clipboard")
# Columns each group has when there is room for everything (Select: 4
# semantic columns of 4 cells; File Op: one vertical stack; Clipboard: 2
# columns of 3 rows).
_NATURAL_COLS = {"select": 4, "file_op": 1, "clipboard": 2}
# The column counts a group may use, so no row is left ragged (File Op: 3
# buttons -> 1 or 3 columns; Clipboard: 6 buttons -> 1, 2, 3 or 6 columns;
# Select wraps whole semantic columns, so any count up to 4 works).
_VALID_COLS = {"select": (1, 2, 3, 4), "file_op": (1, 3), "clipboard": (1, 2, 3, 6)}
_MAX_COLS = {key: max(cols) for key, cols in _VALID_COLS.items()}
# Cells per semantic Select column (the last one is empty in the first column).
_SELECT_COLUMN_HEIGHT = 4

# Candidate arrangements, in order of preference (used to break ties
# between arrangements of about the same height).
#   "A": a row of vertical stacks -- the first group of each stack keeps its
#        natural column count, every later group of a stack takes as many
#        columns as fit within the stack's width.
#   "B": a column of rows -- groups sharing a row keep their natural column
#        counts; File Op/Clipboard alone in a row take as many columns as
#        fit within the pane's width.
_TOPOLOGIES = (
    ("A", (("select",), ("file_op",), ("clipboard",))),
    ("A", (("select", "file_op"), ("clipboard",))),
    ("B", (("select", "file_op"), ("clipboard",))),
    ("A", (("select", "clipboard"), ("file_op",))),
    ("B", (("select", "clipboard"), ("file_op",))),
    ("B", (("select",), ("file_op", "clipboard"))),
    ("A", (("select", "file_op", "clipboard"),)),
    ("A", (("select", "clipboard", "file_op"),)),
)
_ALL_STACKED = 6


class ResponsiveLayoutMixin:

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layout()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_responsive_layout()

    # --------------------------------------------------- Responsive layout

    def _update_responsive_layout(self, force: bool = False):
        """Re-arranges every visible pane's button groups for its current
        width (a no-op for a pane whose arrangement doesn't change) and
        updates the date columns. `force=True` (right after _build_ui)
        applies the arrangement even though nothing has been applied yet.
        The panes' cluster containers also call _relayout_pane_cluster
        themselves whenever they are resized, so this is mostly for
        construction time."""
        if not getattr(self, "left", None) or not getattr(self.left, "cluster_container", None):
            return  # called mid-construction, before the panes exist yet
        panes = (self.left,) if not self._dual_pane_enabled else (self.left, self.right)
        for pane in panes:
            self._relayout_pane_cluster(pane, force)
        for pane in (self.left, self.right):  # the right pane lists duplicates in both modes
            self._update_pane_date_columns(pane)

    # ------------------------------------------------------------ Groups

    @staticmethod
    def _group_widget(pane: DirectoryPane, key: str):
        return {
            "select": pane.select_group,
            "file_op": pane.file_op_group,
            "clipboard": pane.clipboard_group,
        }[key]

    @staticmethod
    def _group_buttons(pane: DirectoryPane, key: str) -> list:
        """The group's buttons in the order they wrap in. Select's list is
        its 3 x 4 grid read row by row (so with 4 columns it reads
        Select All/Missing/Newer/Smaller, Invert/Matching/Older/Larger,
        Clear/Diff Items/All Parents/Item Parent, -/-/All Childs/Item Childs -- see
        _cell_positions for how fewer columns wrap)."""
        if key == "select":
            sp = pane.select_plain_buttons
            st = pane.select_toggle_buttons
            if getattr(pane, "dup_layout", False):
                # Duplicate Finder mode: the compare buttons give way to
                # the Duplicates / Extra copies pairs (None = empty cell).
                return [
                    sp["select_all"], st["dup_all"], st["dup_sel"], sp["all_parents"],
                    pane.invert_button, st["dup_extra"], st["dup_sel_extra"], sp["file_parent"],
                    sp["clear"], None, None, sp["all_childs"],
                    None, None, None, sp["item_childs"],
                ]
            return [
                sp["select_all"], st["missing"], st["newer"], st["smaller"],
                pane.invert_button, st["matching"], st["older"], st["larger"],
                sp["clear"], st["diff_folders"], sp["all_parents"], sp["file_parent"],
                None, None, sp["all_childs"], sp["item_childs"],
            ]
        if key == "file_op":
            return [pane.rename_button, pane.new_folder_button, pane.delete_button]
        b = pane._clipboard_buttons
        across = pane._across_buttons
        return [across["move"], across["copy"], b["copy"], b["cut"], b["paste"], b["paste_folders"]]

    @staticmethod
    def _cell_positions(key: str, count: int, cols: int, mirrored: bool) -> list:
        """[(button index, row, column)] for `count` buttons wrapped into
        `cols` columns. Select keeps its semantic columns (button i belongs
        to column i % 4) together: with 2 or 1 columns whole semantic
        columns wrap below each other; with 3 the first semantic column
        becomes the top row above the other three (a compact 3 x 4). File
        Op and Clipboard simply wrap row by row. `mirrored` flips every row, so
        the first button of a row sits at the right edge."""
        if key == "select":
            units = _NATURAL_COLS["select"]
            cols = max(1, min(cols, units))
            cells = []
            for i in range(count):
                unit, level = i % units, i // units
                if cols == 3:
                    # 3 columns x 5 rows, compact: the first semantic
                    # column (Select All/Invert/Clear + an empty cell) lies
                    # down as the top row and the other three keep their
                    # rows below it.
                    cells.append((i, 0, level) if unit == 0 else (i, 1 + level, unit - 1))
                else:
                    block, col = divmod(unit, cols)
                    cells.append((i, block * _SELECT_COLUMN_HEIGHT + level, col))
        else:
            cols = max(1, min(cols, count))
            cells = [(i, i // cols, i % cols) for i in range(count)]
        if mirrored:
            cells = [(i, row, cols - 1 - col) for i, row, col in cells]
        return cells

    def _fill_group(self, pane: DirectoryPane, key: str, cols: int, mirrored: bool):
        """(Re)populates a group's grid with its buttons wrapped into
        `cols` columns -- detaches whatever it held before, then re-adds
        every button (the buttons themselves are never deleted)."""
        group = self._group_widget(pane, key)
        grid = group.layout()
        self._detach_layout_widgets(grid)
        # Same edge rule as ever: buttons hug the right edge of their cell
        # when mirrored, the left one otherwise (matters where a column
        # holds a wider button, e.g. Paste Folders).
        align = Qt.AlignRight if mirrored else Qt.AlignLeft
        buttons = self._group_buttons(pane, key)
        for i, row, col in self._cell_positions(key, len(buttons), cols, mirrored):
            if buttons[i] is not None:
                grid.addWidget(buttons[i], row, col, alignment=align)
        group.setAlignment(Qt.AlignRight if mirrored else Qt.AlignLeft)

    def _ensure_cluster_sizes(self, pane: DirectoryPane):
        """Measures every group at every column count once (and keeps the
        results on the pane): {(key, cols): (width, height)}. Filling a
        group to measure it leaves it filled that way, which is why the
        chosen arrangement is always applied after a measuring pass."""
        cache = pane._cluster_size_cache
        if cache:
            return False
        for key in _GROUP_KEYS:
            group = self._group_widget(pane, key)
            for cols in _VALID_COLS[key]:
                self._fill_group(pane, key, cols, False)
                group.layout().invalidate()
                size = group.sizeHint().expandedTo(group.minimumSizeHint())
                cache[(key, cols)] = (size.width(), size.height())
        pane._cluster_arrangement_cache = {}
        return True

    # ------------------------------------------------------ Arrangements

    def _choose_arrangement(self, pane: DirectoryPane, avail: int):
        """(topology index, {group key: columns}) -- the least tall
        arrangement that fits `avail` px, see the module docstring."""
        sizes = pane._cluster_size_cache
        gap, vgap = _CLUSTER_GROUP_GAP, _CLUSTER_V_GAP
        # Heights within this of the lowest count as equal (the tie goes
        # to the earlier, i.e. more original, topology): a few px of
        # group-title overhead must not outvote the intended order.
        tolerance = self._uniform_button_size()[1] // 2

        def widest_fit(key, limit):
            for cols in reversed(_VALID_COLS[key]):
                if sizes[(key, cols)][0] <= limit:
                    return cols
            return 1

        def evaluate(index, select_cols):
            kind, structure = _TOPOLOGIES[index]
            cols = {}

            def natural(key):
                return select_cols if key == "select" else _NATURAL_COLS[key]

            widths, heights = [], []
            if kind == "A":
                for stack in structure:
                    first = stack[0]
                    cols[first] = natural(first)
                    stack_width = sizes[(first, cols[first])][0]
                    for key in stack[1:]:
                        cols[key] = widest_fit(key, stack_width)
                    widths.append(max(sizes[(k, cols[k])][0] for k in stack))
                    heights.append(sum(sizes[(k, cols[k])][1] for k in stack) + vgap * (len(stack) - 1))
                return sum(widths) + gap * (len(widths) - 1), max(heights), cols
            for row in structure:
                if len(row) == 1 and row[0] != "select":
                    cols[row[0]] = widest_fit(row[0], avail)
                else:
                    for key in row:
                        cols[key] = natural(key)
                widths.append(sum(sizes[(k, cols[k])][0] for k in row) + gap * (len(row) - 1))
                heights.append(max(sizes[(k, cols[k])][1] for k in row))
            return max(widths), sum(heights) + vgap * (len(heights) - 1), cols

        for select_cols in reversed(_VALID_COLS["select"]):
            fitting = []
            for index in range(len(_TOPOLOGIES)):
                width, height, cols = evaluate(index, select_cols)
                if width <= avail:
                    fitting.append((height, index, cols))
            if fitting:
                lowest = min(h for h, _i, _c in fitting)
                for height, index, cols in fitting:
                    if height <= lowest + tolerance:
                        return index, cols
        # Not even everything in one column fits: use that anyway (the
        # pane clips it).
        _w, _h, cols = evaluate(_ALL_STACKED, 1)
        return _ALL_STACKED, cols

    def _cluster_available_width(self) -> int:
        """The width each pane's button cluster gets, computed from the
        cluster row's own width (not from either container's current
        width) so both panes always get the same value and re-arrange in
        the same pass, even when one of them is a pixel wider or has not
        received its own resize event yet."""
        layout = getattr(self, "_cluster_row_layout", None)
        if layout is None:
            return 150
        total = layout.total_width
        if total < 300:
            total = self.width() - 20
        return max(150, layout.side_width(total))

    def _relayout_all_clusters(self, force: bool = False):
        """Re-arranges every visible pane's cluster in one go -- called
        from either pane's cluster container whenever it is resized."""
        if not getattr(self, "left", None) or getattr(self.left, "cluster_container", None) is None:
            return
        panes = (self.left,) if not self._dual_pane_enabled else (self.left, self.right)
        for pane in panes:
            self._relayout_pane_cluster(pane, force)

    def _relayout_pane_cluster(self, pane: DirectoryPane, force: bool = False, _retry: bool = False):
        """Picks and (if it changed) applies the arrangement of one pane's
        button groups for the width its cluster container has right now.
        Cheap enough to run on every resize: the arrangement is cached per
        width, and nothing is rebuilt unless it differs from what's
        showing. Both panes use the same width (_cluster_available_width),
        so they change together."""
        container = getattr(pane, "cluster_container", None)
        if container is None or getattr(pane, "select_group", None) is None:
            return
        if getattr(pane, "_cluster_busy", False):
            return
        if getattr(pane, "invert_button", None) is None or not pane._clipboard_buttons:
            return  # mid-construction
        is_left = pane is self.left
        mirrored = (not is_left) and self._symmetrical_buttons

        avail = self._cluster_available_width()

        pane._cluster_busy = True
        try:
            measured = self._ensure_cluster_sizes(pane)
            arrangement = pane._cluster_arrangement_cache.get(avail)
            if arrangement is None:
                arrangement = self._choose_arrangement(pane, avail)
                pane._cluster_arrangement_cache[avail] = arrangement
            index, cols = arrangement
            applied_key = (index, tuple(sorted(cols.items())), mirrored)
            if not force and not measured and pane._cluster_applied == applied_key:
                return
            self._apply_cluster_arrangement(pane, index, cols, mirrored)
            pane._cluster_applied = applied_key
        finally:
            pane._cluster_busy = False
        # Applying an arrangement can itself change the container's size
        # (and so the width this was chosen for) while the busy guard above
        # swallows the resize notification -- check once more, so the pane
        # never stays arranged for a stale width.
        if not _retry and self._cluster_available_width() != avail:
            self._relayout_pane_cluster(pane, False, _retry=True)

    def _apply_cluster_arrangement(self, pane: DirectoryPane, index: int, cols: dict, mirrored: bool):
        """Builds the container's row/stack layouts for arrangement
        `index` with the given per-group column counts. Groups are left-
        aligned to their pane, or right-aligned (and ordered from the
        right) for a mirrored right pane."""
        outer = pane.cluster_container.layout()
        self._detach_layout_widgets(outer)
        for key in _GROUP_KEYS:
            self._fill_group(pane, key, cols[key], mirrored)

        kind, structure = _TOPOLOGIES[index]
        h_align = Qt.AlignRight if mirrored else Qt.AlignLeft
        gap = _CLUSTER_GROUP_GAP

        def new_row():
            row = QHBoxLayout()
            row.setSpacing(0)
            row.setContentsMargins(0, 0, 0, 0)
            return row

        def add_groups_to_row(row, keys):
            """The groups side by side, in pane order; a stretch on the side
            the row is aligned away from."""
            ordered = list(reversed(keys)) if mirrored else list(keys)
            if mirrored:
                row.addStretch(1)
            for position, key in enumerate(ordered):
                if position:
                    row.addSpacing(gap)
                group = self._group_widget(pane, key)
                row.addWidget(group, 0, Qt.AlignTop)
                group.show()
            if not mirrored:
                row.addStretch(1)

        if kind == "A":
            row = new_row()
            stacks = list(reversed(structure)) if mirrored else list(structure)
            if mirrored:
                row.addStretch(1)
            for position, stack in enumerate(stacks):
                if position:
                    row.addSpacing(gap)
                column = QVBoxLayout()
                column.setSpacing(_CLUSTER_V_GAP)
                column.setContentsMargins(0, 0, 0, 0)
                for key in stack:
                    group = self._group_widget(pane, key)
                    column.addWidget(group, 0, h_align)
                    group.show()
                column.addStretch(1)
                row.addLayout(column)
            if not mirrored:
                row.addStretch(1)
            outer.addLayout(row)
        else:
            for keys in structure:
                row = new_row()
                add_groups_to_row(row, keys)
                outer.addLayout(row)

    def _detach_layout_widgets(self, layout):
        """Empties `layout`, detaching (not deleting) every widget it
        directly contains, and recursing into (then discarding) any
        dynamically-created sub-layouts found along the way -- e.g. the
        rows/stacks of a pane's button cluster. Leaves `layout` itself
        intact and empty, ready for fresh addWidget/addLayout calls; the
        detached widgets are untouched (same parent, just no longer
        positioned by this layout) until re-added somewhere."""
        while layout.count():
            item = layout.takeAt(0)
            sub = item.layout()
            if sub is not None:
                self._detach_layout_widgets(sub)
                sub.deleteLater()

    def _update_pane_date_columns(self, pane: DirectoryPane):
        """"Use Only 1 Date Column" (Settings tab): collapse the file
        list's three date columns (Modified/Changed/Accessed) down to just
        the one matching this pane's own Statistics-view Metric selection
        (_METRIC_TO_DATE_COLUMN above) -- Name/Size/Type stay as they are
        either way. Otherwise: all three date columns shown."""
        tree = pane.tree
        if tree is None:
            return
        if not getattr(self, "_force_single_date_column", False):
            for col in _DATE_COLUMNS:
                tree.setColumnHidden(col, False)
            return
        metric = pane.tl_metric_combo.currentData() if pane.tl_metric_combo else "modified"
        keep_col = _METRIC_TO_DATE_COLUMN.get(metric, COL_MODIFIED)
        for col in _DATE_COLUMNS:
            tree.setColumnHidden(col, col != keep_col)
