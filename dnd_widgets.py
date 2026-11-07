"""
Small custom Qt widgets shared by both panes: a QLineEdit that accepts a
directory dropped on it (from a file manager), and the QTreeWidget used
for each pane's listing -- native Qt drag-and-drop (no external dnd
library needed), a name column that resizes to fill leftover space, and a
GNOME/Nemo/Nautilus/Dolphin-compatible drag source (see
"x-special/gnome-copied-files" in constants.py).
"""

import math
import os

from PySide6.QtCore import Qt, QEvent, QUrl, Signal, QMimeData, QRect, QPointF
from PySide6.QtGui import QBrush, QColor, QCursor, QDrag, QPainter, QPalette, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QLineEdit, QTreeWidget, QAbstractItemView, QStyle, QStyleOptionViewItem,
)

from constants import IS_DIR_ROLE, PATH_ROLE
from custom_widgets import _press_blend, _indent_wheel_color


# Indentation line styles (Settings tab -> Indentation Line -> Line style).
# The integer values are what the settings store (per theme) and what the
# color provider's 9th element carries -- see DirDropTreeWidget.drawBranches.
LINE_STYLE_STRAIGHT = 0
LINE_STYLE_DIAGONAL = 1
LINE_STYLE_CURVED = 2
LINE_STYLE_SCURVE = 3
_CURVED_STYLES = (LINE_STYLE_CURVED, LINE_STYLE_SCURVE)

# Points sampled along a curved piece -- plenty for a piece that is only
# one row tall, and cheap enough to redo on every paint.
_CURVE_SEGMENTS = 16

# S-curve style: how sharply the line changes sideways in the middle of
# its slide. The curve is x = t^k / (t^k + (1-t)^k) (t = how far down
# the row): k = 1 is a plain diagonal, and the higher it is the longer
# the line stays vertical at both ends and the steeper it moves across
# in between -- 2.0 is roughly the plain cosine S, 4-5 a clearly
# S-shaped one even on a single 20-odd pixel row.
_SCURVE_SHARPNESS = 5.0

# How far the diagonal/curved/S-curve styles slide their lines sideways
# for the rows between a folder's first and last row, as a divisor of
# the Indentation setting: 2 = half an indentation, 3 = a third, 4 = a
# quarter, 1.5 = two thirds (not below 1: a full indentation would put
# the line on top of its neighbour's column). Expand arrows move out of
# the way by the same amount (see drawBranches), and the hover/click
# bands follow (see _line_at). Applied through _line_shift below, which
# keeps it a whole number of pixels.
_LINE_SHIFT_DIVISOR = 3


def _shift_profile(dy, h, style):
    """The path a guide line takes to move sideways by `h` pixels over a
    vertical distance of `dy`, as [(x_offset, u), ...] with u running
    0..dy downwards from the end that stays at the line's normal x
    (x_offset 0, the line's free start/end) to the end that has moved
    (x_offset h, where it joins the straight run through the middle).

    Diagonal: just the two end points.
    Curved: a circular arc that is vertical where it meets the straight
    run (the moved end) and leaves the free end at a shallow angle --
    convex towards the right, i.e. the line flares out sideways at its
    very start and end. The circle through both ends whose centre lies
    on the horizontal line through the moved end. When the piece is
    shorter than it is wide (dy < h: a huge indentation on short rows)
    that circle would turn back on itself, so a quarter ellipse with
    the same tangents is used instead -- the two coincide at dy == h.
    S-curve: vertical at both ends (see _SCURVE_SHARPNESS).
    """
    if style not in _CURVED_STYLES or dy <= 0 or h <= 0:
        return [(0.0, 0.0), (float(h), float(dy))]
    n = _CURVE_SEGMENTS
    if style == LINE_STYLE_SCURVE:
        k = _SCURVE_SHARPNESS
        pts = []
        for i in range(n + 1):
            t = i / n
            a, b = t ** k, (1.0 - t) ** k
            pts.append((h * a / (a + b), dy * t))
        return pts
    pts = []
    if dy >= h:
        r = (h * h + dy * dy) / (2.0 * h)
        theta_end = math.atan2(dy, r - h)
        for i in range(n + 1):
            th = theta_end * (1.0 - i / n)
            pts.append((h - r + r * math.cos(th), dy - r * math.sin(th)))
    else:
        for i in range(n + 1):
            phi = (math.pi / 2.0) * (1.0 - i / n)
            pts.append((h * math.cos(phi), dy * (1.0 - math.sin(phi))))
    return pts


def _tip_arc_profile(height, h):
    """Curved style's single-row folder: one smooth ")" arc from the
    row's top edge out to `h` at its middle and back to the bottom edge,
    as [(x_offset, u), ...] with u running 0..height. The line has no
    vertical run before or after it (it exists only on this one row),
    so the ends don't need to be vertical -- the arc's own tangent is
    vertical at the apex, which is what makes it read as a ")".
    Circle through the three points while the row is at least twice as
    tall as the bump is deep (h <= height / 2); a half ellipse otherwise
    (the circle would spill out of the row) -- the two coincide at
    h == height / 2."""
    n = _CURVE_SEGMENTS * 2
    half = height / 2.0
    if height <= 0 or h <= 0:
        return [(0.0, 0.0), (float(h), half), (0.0, float(height))]
    pts = []
    if h <= half:
        r = (h * h + half * half) / (2.0 * h)
        cx = h - r
        theta_end = math.asin(min(1.0, half / r))
        for i in range(n + 1):
            th = -theta_end + 2.0 * theta_end * i / n
            pts.append((cx + r * math.cos(th), half + r * math.sin(th)))
    else:
        for i in range(n + 1):
            phi = math.pi * i / n
            pts.append((h * math.sin(phi), height * (1.0 - math.cos(phi)) / 2.0))
    return pts


def _shifted_line_polygon(kind, style, x0, h, thickness, y1, y2):
    """Filled outline of one row's piece of a guide line that isn't just
    a straight vertical run: the line's own x is x0 where it stays put,
    x0 + h where it has moved out.
      "out"  -- first row under the folder: x0 at the top edge, x0 + h
                at the bottom edge.
      "back" -- last row of the folder's contents: x0 + h at the top
                edge, back to x0 at the bottom edge.
      "tip"  -- the folder's only row: out to x0 + h at the row's
                middle and back again (">" when diagonal, one smooth
                ")" arc when curved, a double S when S-curve).
    Thickness is horizontal (the polygon is the path plus the same path
    shifted `thickness` pixels right), so it matches what the straight
    style's fillRect does, and the horizontal edges land exactly on the
    row's top/bottom pixel boundaries -- neighbouring rows' pieces meet
    without seams because they share the same x there.
    """
    height = y2 - y1
    if kind == "out":
        pts = [(x0 + xo, y1 + u) for xo, u in _shift_profile(height, h, style)]
    elif kind == "back":
        pts = [(x0 + xo, y2 - u) for xo, u in reversed(_shift_profile(height, h, style))]
    elif style == LINE_STYLE_CURVED:  # "tip", one smooth ")" arc
        pts = [(x0 + xo, y1 + u) for xo, u in _tip_arc_profile(height, h)]
    else:
        # "tip" of the diagonal style (straight legs: ">") and of the
        # S-curve style: an S-curve out over the top half of the row
        # and its mirror image back over the bottom half -- a miniature
        # double S, a little like a "}".
        half = height / 2.0
        profile = _shift_profile(half, h, style)
        top = [(x0 + xo, y1 + u) for xo, u in profile]
        bottom = [(x0 + xo, y2 - u) for xo, u in reversed(profile)]
        pts = top + bottom[1:]  # both halves end on the same apex point
    outline = [QPointF(x, y) for x, y in pts]
    outline += [QPointF(x + thickness, y) for x, y in reversed(pts)]
    return QPolygonF(outline)


def _line_shift(indent):
    """Sideways slide of the shifted line styles in whole pixels:
    indent / _LINE_SHIFT_DIVISOR (whole so the straight runs stay
    crisp; below one full indentation so a line never reaches its
    neighbour's column)."""
    return max(0, min(indent - 1, int(indent / max(1.0, float(_LINE_SHIFT_DIVISOR)))))


def _line_kind(level, depth, first_child, next_depth):
    """How level `level`'s guide line behaves on a row at `depth`:
    "mid" (plain run), "out" (first row under its folder), "back" (last
    row of the folder's visible contents) or "tip" (both at once -- the
    folder has exactly one visible row underneath). Only the row's
    immediate parent's line can be "first" (any deeper row is further
    into that folder's contents); any ancestor's line is "last" when the
    next visible row is no longer inside that ancestor (its depth is at
    most the ancestor's own)."""
    first = first_child and level == depth - 1
    last = next_depth <= level
    if first and last:
        return "tip"
    if first:
        return "out"
    if last:
        return "back"
    return "mid"


def _line_shift_fraction(kind, frac):
    """How far along its sideways shift (0 = normal x, 1 = fully out) a
    line of this kind is at `frac` (0 top .. 1 bottom) of its row --
    straight-line approximation, only used for hit-testing."""
    if kind == "mid":
        return 1.0
    if kind == "out":
        return frac
    if kind == "back":
        return 1.0 - frac
    return 1.0 - abs(2.0 * frac - 1.0)


def _profile_x_at(profile, u):
    """x offset of a _shift_profile path at distance `u` from its free
    end, linearly between the sampled points (u ascends along every
    profile, see _shift_profile)."""
    x_prev, u_prev = profile[0]
    if u <= u_prev:
        return x_prev
    for x, u_cur in profile[1:]:
        if u <= u_cur:
            span = u_cur - u_prev
            return x if span <= 0 else x_prev + (x - x_prev) * (u - u_prev) / span
        x_prev, u_prev = x, u_cur
    return profile[-1][0]


def _line_path(kind, style, h, height):
    """Where a guide line of this `kind` (see _line_kind) is, sideways,
    over a row `height` pixels tall, as [(x_offset, u), ...] with u
    running 0..height down from the row's top edge and x_offset the
    line's left edge relative to its normal x -- exactly the path
    _shifted_line_polygon draws (0 on a straight run in the straight
    style, `h` on a middle run, the slide/tip shapes on the first/last/
    only row). Read with _profile_x_at."""
    if kind == "mid":
        return [(float(h), 0.0), (float(h), float(height))]
    if kind == "out":
        return _shift_profile(height, h, style)
    if kind == "back":
        return [(xo, height - u) for xo, u in reversed(_shift_profile(height, h, style))]
    if style == LINE_STYLE_CURVED:  # "tip": one smooth ")" arc
        return _tip_arc_profile(height, h)
    # "tip" of the diagonal/S-curve styles: the slide out over the top
    # half of the row and its mirror image back over the bottom half.
    profile = _shift_profile(height / 2.0, h, style)
    top = list(profile)
    bottom = [(xo, height - u) for xo, u in reversed(profile)]
    return top + bottom[1:]


def _tick_rects(path, outer_path, has_outer, x0, indent, thickness, length,
                left_side, y1, y2):
    """The tick mark beside a row, as a list of QRects to fill (empty
    if there's no room for one): a horizontal bar `thickness` tall,
    centered on the row, attached to its parent's guide line and
    reaching `length` pixels beyond that line -- to the right of it
    (default) or, with `left_side`, to the left of it.

    `path` is that line's path through this row (see _line_path; None =
    a plain vertical line at x0, the straight style) and `outer_path`
    the line one level further out (None = a plain one at x0 - indent;
    `has_outer` False = there is none, depth 1). On the slanted or
    curved pieces of line (first/last row of a folder in the diagonal/
    curved/S-curve styles) the line is a tilted band, so the tick's
    pixel rows meet it at different x. Each pixel row of the tick
    therefore gets its own start (right side) or end (left side), in
    the middle of the band where that row crosses it: the tick then
    never sticks out on the wrong side of the line or leaves a gap,
    whatever the tilt. The far end is the same for every row, taken
    where the line is at the row's middle. On a straight run all rows
    come out alike and merge into one rectangle.

    Right side: cut off at the right edge of the row's own arrow
    column (x0 + indent), so it never runs into the icon/name.
    Left side: cut off at the right edge of the line one level further
    out (the left edge of the arrow column when there is none), so it
    never runs into that line.
    """
    length = int(length)
    if length <= 0:
        return []
    height = y2 - y1

    def line_x(line, u):
        return 0.0 if line is None else _profile_x_at(line, u)

    reach = int(math.floor(x0 + line_x(path, height / 2.0) + 0.5))
    far = reach - length if left_side else min(reach + thickness + length, x0 + indent)
    top = (y1 + y2 - thickness) // 2
    runs = []  # [start, end, first_row, row_count], consecutive equal rows merged
    for row in range(top, top + thickness):
        u = row + 0.5 - y1
        centre = x0 + line_x(path, u) + thickness / 2.0
        if left_side:
            limit = x0 - indent
            if has_outer:
                limit = int(math.ceil(x0 - indent + line_x(outer_path, u) + thickness))
            start, end = max(far, limit), int(math.ceil(centre))
        else:
            start, end = int(math.floor(centre)), far
        if end <= start:
            continue
        if runs and runs[-1][0] == start and runs[-1][1] == end and runs[-1][2] + runs[-1][3] == row:
            runs[-1][3] += 1
        else:
            runs.append([start, end, row, 1])
    return [QRect(start, row, end - start, count) for start, end, row, count in runs]


def _droppable(path: str) -> bool:
    """A dropped path DirComp can open: a folder, or a folder snapshot file."""
    return os.path.isdir(path) or (os.path.isfile(path) and path.lower().endswith(".fss"))


class DirDropLineEdit(QLineEdit):
    """QLineEdit that accepts a dropped directory (from a file manager)."""
    directoryDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if _droppable(path):
                self.directoryDropped.emit(path)
                event.acceptProposedAction()
                return


class DirDropComboBox(QComboBox):
    """Editable QComboBox that replaces the plain entry field above
    "Select Directory": it still shows the pane's current folder as its
    edit text and still accepts a directory dropped on it (from a file
    manager), exactly like DirDropLineEdit above -- but its dropdown now
    doubles as the pane's folder-open history (most-recent-first; see
    UiBuilderMixin._record_folder_history/_refresh_history_combo).

    .text()/.setText()/.textChanged are kept as thin QLineEdit-compatible
    shims (setEditText/currentText/the internal line edit's own signal)
    so every existing call site that still treats `pane.entry` as a plain
    QLineEdit (pane.entry.text(), pane.entry.setText(""), .textChanged.
    connect(...)) keeps working unchanged.

    Drops are handled on the combo box itself rather than its internal
    line edit -- setAcceptDrops(False) on the line edit below means a
    drop lands the same way whether it's aimed at the text or the arrow
    button beside it, same as dropping directly on the tree."""
    directoryDropped = Signal(str)
    # A real dropdown pick (mouse or keyboard), never a programmatic
    # setText()/set_history() -- see _on_activated.
    historyEntryActivated = Signal(str)
    # DEL pressed on an entry in the open dropdown -- see eventFilter.
    # Already removed from this widget's own list by the time it fires;
    # the owner only needs to drop it from its backing history list.
    historyEntryDeleted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setAcceptDrops(True)
        self.lineEdit().setAcceptDrops(False)
        # Re-exposed under the QLineEdit-compatible name every existing
        # `entry.textChanged.connect(...)` call site already uses.
        self.textChanged = self.lineEdit().textChanged
        self.activated.connect(self._on_activated)
        # The pane's actual current folder, tracked separately from
        # whatever the combo box happens to be *displaying* right now --
        # see setText() and _on_activated's invalid-path case, which
        # needs to roll the displayed text back to this after Qt's own
        # auto-select-on-pick has already overwritten it with a path
        # that turned out not to exist.
        self._current_valid_text = ""
        # The dropdown's list view is a separate popup widget that owns
        # the keyboard while it is open, so DEL is caught there.
        self.view().installEventFilter(self)

    def eventFilter(self, obj, event):
        """DEL while the dropdown is open removes the entry under the
        mouse cursor (or, if the cursor isn't over an entry, the
        keyboard-highlighted one) -- no confirmation. The popup stays
        open so several entries can be removed in a row."""
        if (obj is self.view() and event.type() == QEvent.KeyPress
                and event.key() == Qt.Key_Delete):
            view = self.view()
            index = view.indexAt(view.viewport().mapFromGlobal(QCursor.pos()))
            if not index.isValid():
                index = view.currentIndex()
            if index.isValid():
                self._delete_history_row(index.row())
            return True  # swallow DEL either way: never reaches the line edit
        return super().eventFilter(obj, event)

    def _delete_history_row(self, row: int):
        path = self.itemData(row, Qt.UserRole) or self.itemText(row)
        # removeItem can move the current index, which makes the combo
        # overwrite the edit text -- block signals and restore it, same
        # reasoning as set_history.
        current = self.currentText()
        line_edit = self.lineEdit()
        self.blockSignals(True)
        line_edit.blockSignals(True)
        self.removeItem(row)
        self.setEditText(current)
        line_edit.blockSignals(False)
        self.blockSignals(False)
        if path:
            self.historyEntryDeleted.emit(path)

    def text(self) -> str:
        return self.currentText()

    def setText(self, value: str):
        self._current_valid_text = value or ""
        self.setEditText(self._current_valid_text)

    def set_history_colors(self, live_color, snapshot_color):
        """Text colors of the dropdown entries: `snapshot_color` for
        folder-snapshot files (.fss), `live_color` for live folders.
        Applied by the next set_history()."""
        self._history_live_color = live_color
        self._history_snapshot_color = snapshot_color

    def set_history(self, paths):
        """Repopulate the dropdown list from `paths` (most-recent-first),
        leaving the box's own displayed text untouched. Signals are
        blocked on both this widget and its internal line edit for the
        duration -- clear()/addItems() can otherwise sync the line edit's
        text to whatever item ends up "current" for a moment, which
        would fire a spurious textChanged with the wrong value before
        setEditText below puts the real one back."""
        current = self.currentText()
        line_edit = self.lineEdit()
        self.blockSignals(True)
        line_edit.blockSignals(True)
        self.clear()
        live = getattr(self, "_history_live_color", None)
        snap = getattr(self, "_history_snapshot_color", None)
        for i, path in enumerate(paths):
            # The real path lives in the item's user data (that is what a
            # pick or DEL acts on); a snapshot file shows just its file
            # name, the full path as tooltip.
            is_snapshot = path.lower().endswith(".fss")
            self.addItem(os.path.basename(path) if is_snapshot else path, path)
            if is_snapshot:
                self.setItemData(i, path, Qt.ToolTipRole)
            color = snap if is_snapshot else live
            if color:
                self.setItemData(i, QBrush(QColor(color)), Qt.ForegroundRole)
        self.setEditText(current)
        line_edit.blockSignals(False)
        self.blockSignals(False)

    def _on_activated(self, index):
        path = self.itemData(index, Qt.UserRole) or self.itemText(index)
        if path:
            self.historyEntryActivated.emit(path)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if _droppable(path):
                self.directoryDropped.emit(path)
                event.acceptProposedAction()
                return


class DirDropTreeWidget(QTreeWidget):
    """QTreeWidget that:
    - accepts a dropped directory (from outside, or from the other pane) and
      navigates this view into it
    - lets selected items be dragged out as real files/folders, e.g. onto a
      file manager window
    """
    directoryDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setTextElideMode(Qt.ElideRight)
        self.header().sectionResized.connect(self._on_section_resized)
        # Set by the owner to get notified once an outgoing drag finishes.
        self.after_drag = None
        # Set by the owner (UiBuilderMixin._build_directory_widgets) to a
        # zero-arg callable returning the current indentation-line
        # settings as a tuple: (enabled, thickness_px, steps, color1_hex,
        # color2_hex, reverse, hover_color_hex, hover_opacity_pct,
        # line_style, tick_enabled, tick_files, tick_files_left,
        # tick_file_length, tick_folders, tick_folders_left,
        # tick_folder_length). line_style: LINE_STYLE_STRAIGHT/
        # DIAGONAL/CURVED/SCURVE; the tick values are the tick marks
        # beside each row, see drawBranches. Everything from line_style
        # on is optional (missing = straight, no ticks)
        # -- re-read on every paint
        # (see drawBranches) rather than fixed at construction, same
        # reasoning as custom_widgets._make_color_swatch's get_hex,
        # since these settings can change from the Settings tab after
        # this tree is built.
        self.indent_color_provider = None
        # Mouse tracking is off by default on QAbstractItemView (it only
        # gets move events while a button is held) -- needed here so
        # drawBranches can highlight the indentation line nearest the
        # cursor as it moves, not just while dragging/selecting.
        self.setMouseTracking(True)
        # Which indentation guide line is currently hover-highlighted,
        # identified by the QTreeWidgetItem that "owns" it (the
        # ancestor whose own row is at that column's depth -- its line
        # is what runs continuously down through every one of its
        # visible descendants) -- see mouseMoveEvent/leaveEvent/
        # drawBranches. Storing the owning item rather than a (row,
        # level) pair is what lets a single hover state highlight that
        # whole vertical run across every row it passes through,
        # without drawBranches needing to know row ranges: each row's
        # own paint call independently checks whether its ancestor at a
        # given level is this item, so the highlight "just happens" to
        # line up across every row that shares that ancestor.
        self._hover_ancestor = None

    def _ancestor_chain(self, item) -> list:
        """This item's ancestors, root-most first (chain[0] is the
        top-level ancestor, chain[-1] is the immediate parent) -- built
        once per item rather than re-walking parent() once per
        indentation level, since a row with N ancestors needs to look
        up all N of them (see drawBranches). chain[level] is exactly
        the item that owns the guide line drawn at that level for any
        row descended from it."""
        chain = []
        node = item.parent()
        while node is not None:
            chain.append(node)
            node = node.parent()
        chain.reverse()
        return chain

    def _line_settings(self):
        """The color provider's current values as a 16-tuple (enabled,
        thickness, steps, color1, color2, reverse, hover_color,
        hover_opacity, line_style, tick_enabled, tick_files,
        tick_files_left, tick_file_length, tick_folders,
        tick_folders_left, tick_folder_length), or None without a
        provider. A provider that predates line styles (8 values) reads
        as straight, one that predates tick marks as ticks off."""
        provider = self.indent_color_provider
        if provider is None:
            return None
        values = tuple(provider())
        style = int(values[8]) if len(values) > 8 else LINE_STYLE_STRAIGHT
        if len(values) >= 16:
            ticks = (bool(values[9]), bool(values[10]), bool(values[11]), int(values[12]),
                     bool(values[13]), bool(values[14]), int(values[15]))
        else:
            ticks = (False, True, False, 0, False, True, 0)
        return values[:8] + (style,) + ticks

    def _row_line_context(self, item, index):
        """(ancestor chain, first_child, next_depth) for the row at
        `index` -- what _line_kind needs to decide how each guide line
        passes through this row. Both come from the view's own visible
        row order (indexAbove/indexBelow), so hidden/filtered rows and
        collapsed folders are accounted for automatically, and it costs
        O(depth) per row instead of scanning siblings.
        first_child: the row directly above is this row's parent, i.e.
        this is the first visible row of the parent's contents.
        next_depth: depth of the next visible row (-1 if none)."""
        chain = self._ancestor_chain(item)
        first_child = False
        if chain:
            above = self.indexAbove(index)
            first_child = above.isValid() and self.itemFromIndex(above) is item.parent()
        next_depth = -1
        below = self.indexBelow(index)
        if below.isValid():
            next_depth = 0
            node = below.parent()
            while node.isValid():
                next_depth += 1
                node = node.parent()
        return chain, first_child, next_depth

    def _line_at(self, pos):
        """Returns (owning_ancestor_item, level) for the indentation
        guide line under `pos`, or (None, None) if `pos` isn't over any
        row's guide-line area -- that includes the row's own
        expand/collapse arrow column (level == depth) and everything to
        its right (the item's own icon/text), which must fall through
        to Qt's normal click/hover handling rather than being claimed
        here. Shared by mouseMoveEvent (hover) and mousePressEvent
        (click-to-reveal) so the two always agree on hit area -- what
        lights up on hover is exactly what's clickable."""
        settings = self._line_settings()
        if settings is not None and not settings[0]:
            # Indentation lines switched off in Settings: nothing is
            # drawn, so nothing should hover-highlight or be clickable
            # either.
            return None, None
        style = settings[8] if settings is not None else LINE_STYLE_STRAIGHT
        item = self.itemAt(pos)
        if item is None:
            return None, None
        index = self.indexFromItem(item)
        chain, first_child, next_depth = self._row_line_context(item, index)
        depth = len(chain)
        if depth == 0:
            return None, None
        indent = self.indentation()
        x = pos.x()
        shifted = style != LINE_STYLE_STRAIGHT
        half = _line_shift(indent)
        thickness = 1
        if settings is not None:
            thickness = max(1, min(int(settings[1]), indent - 1))
        # The row's own arrow column starts at depth * indent in the
        # straight style, so everything from there rightwards (its
        # arrow, then its icon/text) is never a guide line -- checked
        # first, since the last guide line's hit band (below) would
        # otherwise reach into the left half of that arrow column. In
        # the diagonal/curved styles the arrow sits in the right half of
        # its column, after the line's own shifted position (see
        # drawBranches), so that's where the cut-off moves to.
        arrow_left = depth * indent + (half + thickness if shifted else 0)
        if x >= arrow_left:
            return None, None
        if not shifted:
            # Level L's line is drawn at (L + 1) * indent (see
            # drawBranches -- the boundary between its owner's arrow
            # column and its owner's name), so its hit band is that x
            # +/- indent // 2: the same "indent"-wide band the line has
            # always had, just centered on where the line now actually
            # is. Shifting by indent // 2 before dividing is what
            # centers it.
            level = (x + indent // 2) // indent - 1
            if level < 0 or level >= depth:
                return None, None
            return chain[level], level
        # Shifted styles: each line's x now depends on where in its row
        # the cursor is, so take the line that's nearest at this y (its
        # band is the same indent-wide one, centered on its current x).
        rect = self.visualItemRect(item)
        top, bottom = rect.top(), rect.bottom() + 1
        frac = 0.5 if bottom <= top else min(1.0, max(0.0, (pos.y() - top) / (bottom - top)))
        best = None
        for level in range(depth):
            kind = _line_kind(level, depth, first_child, next_depth)
            line_x = (level + 1) * indent + half * _line_shift_fraction(kind, frac)
            low = line_x - indent // 2
            if low <= x < low + indent:
                dist = abs(x - line_x)
                if best is None or dist < best[0]:
                    best = (dist, level)
        if best is None:
            return None, None
        return chain[best[1]], best[1]

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        ancestor, _ = self._line_at(event.position().toPoint())
        if ancestor is not self._hover_ancestor:
            self._hover_ancestor = ancestor
            self.viewport().update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            ancestor, _ = self._line_at(event.position().toPoint())
            if ancestor is not None:
                # Clicking a guide line jumps to its owning folder
                # instead of the usual select-this-row/start-a-drag
                # handling -- consumed here rather than passed to
                # super() so neither of those also fire.
                self._reveal_ancestor(ancestor)
                event.accept()
                return
        super().mousePressEvent(event)

    def _reveal_ancestor(self, ancestor):
        """Scrolls just enough to bring `ancestor`'s own row into view,
        anchored at the top of the viewport -- e.g. when its guide line
        is clicked further down the tree, after scrolling has carried
        the folder itself off the top. Only scrolls if the row isn't
        already fully on-screen, so clicking a line whose folder is
        already visible doesn't yank the current scroll position
        around for no reason."""
        rect = self.visualItemRect(ancestor)
        viewport_height = self.viewport().height()
        if rect.top() < 0 or rect.bottom() > viewport_height:
            self.scrollToItem(ancestor, QAbstractItemView.PositionAtTop)

    def leaveEvent(self, event):
        if self._hover_ancestor is not None:
            self._hover_ancestor = None
            self.viewport().update()
        super().leaveEvent(event)

    def _paint_selected_indent_tint(self, painter, rect, index):
        """Gives the indentation area of a selected row (this `rect`,
        everything left of the name, where the guide lines and expand
        arrows go) the same treatment _SelectionColorDelegate gives the
        cells to its right: the tree's normal row color tinted toward the
        selection color by Settings -> File List View -> Selection
        opacity. Qt fills that area itself with the flat Highlight color
        -- at full strength, i.e. ignoring the opacity -- before calling
        drawBranches, so this paints over that fill; everything
        drawBranches draws goes on top of it. An unselected row: nothing
        to do. (At 100% the tint is the flat Highlight color anyway.)"""
        selection = self.selectionModel()
        if selection is None or not selection.isSelected(index):
            return
        delegate = self.itemDelegateForColumn(0) or self.itemDelegate()
        get_tint = getattr(delegate, "selection_tint", None)
        if get_tint is None:
            return
        tint_hex, opacity_pct = get_tint()
        if not tint_hex:
            return
        # Unlike a cell, this area never carries a diff-tag background,
        # so it always blends from the plain list background.
        base_hex = self.palette().color(QPalette.Base).name()
        painter.fillRect(rect, _press_blend(base_hex, tint_hex, opacity_pct / 100.0))

    def drawBranches(self, painter, rect, index):
        """Replaces Qt's native branch painting (previously just QSS
        border-image tiles set on QTreeView::branch -- see the removed
        UiBuilderMixin._tree_branch_stylesheet) with hand-drawn guide
        lines, one per ancestor depth, colored per the Settings tab's
        Indentation Line Colors group and shaded toward a hover color
        for whichever line the cursor is currently nearest -- the
        whole line, i.e. every row descended from that same ancestor,
        not just the row directly under the cursor (see
        mouseMoveEvent/_ancestor_chain). QSS branch selectors have no
        way to target a line based on cursor proximity or per-depth
        color, so this needed to move to real painting either way.

        Still responsible for the expand/collapse arrow itself (Qt
        normally draws that as part of the same native branch paint
        this replaces) -- drawn last, in its own rightmost column, via
        the style's own PE_IndicatorBranch so it still matches the
        active Qt style rather than being hand-drawn from scratch.

        No more has-siblings/adjoins-item "T"/"L" connector shapes --
        just one line per ancestor depth through the whole height of
        this row, which is what makes uniform per-depth/hover coloring
        possible without hand-tracking sibling geometry too.

        Line style (Settings -> Indentation Line -> Line style):
        straight is a plain vertical line at the left edge of the
        column after its owner's arrow column. Diagonal/curved/S-curve
        lines
        keep that same x where they start and end, but sit half an
        indentation further right for the rows in between: they slide
        out over the first row under their folder, run straight, and
        slide back over the last visible row of the folder's contents
        (a folder with a single visible row underneath gets a ">"/")"
        tip instead). Each row only draws its own piece (see
        _line_kind), and the pieces meet at the row edges, so nothing
        needs to be remembered between rows. In those two styles every
        row's arrow moves to the right half of its column (past that
        line) as well.

        Tick marks (Settings -> Indentation Line -> Line style ->
        Enable Tick Marks): a short horizontal tick from the parent's
        guide line towards each row, in that line's color (hover blend
        included) and thickness, at the vertical middle of the row.
        Files and folders are switched on, sided (left or right of the
        line) and sized independently. A folder's own expand arrow is
        drawn over its tick, so a right-hand folder tick runs behind
        it. The tick joins the line wherever it really is on that row
        in the active line style (see _line_path/_tick_rects), so it
        meets the straight line, the slides, the curves and the
        single-row tips alike."""
        item = self.itemFromIndex(index)
        if item is not None:
            self._paint_selected_indent_tint(painter, rect, index)
        settings = self._line_settings()
        if item is None or settings is None:
            return
        (enabled, thickness, steps, color1, color2, reverse,
         hover_color, hover_opacity, style, tick_enabled, tick_files,
         tick_files_left, tick_file_length, tick_folders,
         tick_folders_left, tick_folder_length) = settings
        chain, first_child, next_depth = self._row_line_context(item, index)
        depth = len(chain)
        indent = self.indentation()
        # Never wider than the gap between two neighboring lines, or
        # they'd merge into each other -- the Settings tab's thickness
        # spin box is capped the same way, this just keeps a value
        # loaded from a file (or a later indentation decrease) safe too.
        thickness = max(1, min(int(thickness), indent - 1))
        half = _line_shift(indent)
        shifted = bool(enabled) and style in (LINE_STYLE_DIAGONAL,) + _CURVED_STYLES
        if enabled and depth > 0:
            painter.save()
            if shifted:
                # Only the slanted/curved pieces need it (vertical runs
                # are whole-pixel rectangles either way).
                painter.setRenderHint(QPainter.Antialiasing, True)
            y1, y2 = rect.top(), rect.bottom() + 1
            parent_color = None
            for level in range(depth):
                base_color = _indent_wheel_color(color1, color2, steps, level, reverse)
                if chain[level] is self._hover_ancestor:
                    base_color = _press_blend(
                        base_color.name(), hover_color, hover_opacity / 100.0,
                    )
                if level == depth - 1:
                    # The immediate parent's line (hover blend and all):
                    # the color of this row's file tick, below.
                    parent_color = base_color
                # At the boundary between this ancestor's own arrow
                # column and its name (i.e. the left edge of the
                # column to its right), rather than centered under
                # its arrow -- so each line starts directly below the
                # beginning of the folder name it belongs to. Derived
                # purely from `indent`, so it follows the Settings
                # tab's Indentation spin box at any value.
                x = rect.left() + indent * (level + 1)
                if not shifted:
                    # Leftmost pixel column always at x (where the line
                    # has always been), so a thicker line grows to the
                    # right.
                    painter.fillRect(QRect(x, y1, thickness, y2 - y1), base_color)
                    continue
                kind = _line_kind(level, depth, first_child, next_depth)
                if kind == "mid":
                    painter.fillRect(QRect(x + half, y1, thickness, y2 - y1), base_color)
                else:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(base_color)
                    painter.drawPolygon(_shifted_line_polygon(
                        kind, style, x, half, thickness, y1, y2,
                    ))
            if tick_enabled and parent_color is not None:
                is_dir = bool(index.sibling(index.row(), 0).data(IS_DIR_ROLE))
                wanted, left_side, length = (
                    (tick_folders, tick_folders_left, tick_folder_length) if is_dir
                    else (tick_files, tick_files_left, tick_file_length)
                )
                if wanted and length > 0:
                    height = y2 - y1
                    # Level depth-1's line is the one at x0 = this row's
                    # own arrow column's left edge (see the loop above);
                    # a tick on the left of it must also know where the
                    # line one level further out is (none at depth 1).
                    x0 = rect.left() + indent * depth
                    path = outer_path = None
                    if shifted:
                        path = _line_path(
                            _line_kind(depth - 1, depth, first_child, next_depth),
                            style, half, height,
                        )
                        if left_side and depth >= 2:
                            outer_path = _line_path(
                                _line_kind(depth - 2, depth, first_child, next_depth),
                                style, half, height,
                            )
                    for tick in _tick_rects(
                        path, outer_path, depth >= 2, x0, indent, thickness, length,
                        left_side, y1, y2,
                    ):
                        painter.fillRect(tick, parent_color)
            painter.restore()
        # This item's own expand/collapse arrow, in the rightmost
        # indentation column (i.e. at its own depth). Decided by the
        # model's own hasChildren(index) -- the exact same test Qt's
        # native drawBranches, and QTreeView's own click-to-expand
        # hit-testing, both use -- rather than childCount() > 0: that
        # one only sees real child items, but hasChildren also honors
        # QTreeWidgetItem.setChildIndicatorPolicy (ShowIndicator = "draw
        # an arrow even though there are no children yet", i.e. a
        # lazily-populated folder that hasn't been expanded/populated
        # once). Using childCount() here made such a folder still
        # *expand* on a click before its name (Qt's own hit-testing
        # said it had children) while drawing no arrow for it until
        # that first expansion actually added real children.
        if self.model().hasChildren(index):
            # Diagonal/curved: the parent's line now runs through the
            # middle of this column, so the arrow gets the part of the
            # column to its right instead of the whole width.
            arrow_shift = (half + thickness) if shifted else 0
            opt = QStyleOptionViewItem()
            opt.rect = QRect(
                rect.left() + indent * depth + arrow_shift, rect.top(),
                max(1, indent - arrow_shift), rect.height(),
            )
            opt.state = QStyle.State_Enabled | QStyle.State_Item | QStyle.State_Children
            if item.isExpanded():
                opt.state |= QStyle.State_Open
            self.style().drawPrimitive(QStyle.PE_IndicatorBranch, opt, painter, self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_name_column()

    def _on_section_resized(self, index, old_size, new_size):
        if index != 0:
            self._fit_name_column()

    def _fit_name_column(self):
        """Keep the Name column filling any leftover width (and shrinking,
        text elided, when the window gets smaller), while the other columns
        stay at whatever width the user set for them."""
        header = self.header()
        count = self.columnCount()
        if count < 2:
            return
        other_width = sum(header.sectionSize(i) for i in range(1, count))
        min_width = 80
        available = self.viewport().width() - other_width
        header.resizeSection(0, max(min_width, available))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.source() is self:
            # Dragging a folder within its own view shouldn't navigate;
            # double-click is used for that instead.
            event.ignore()
            return
        urls = event.mimeData().urls()
        if len(urls) == 1:
            path = urls[0].toLocalFile()
            if _droppable(path):
                self.directoryDropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return
        urls = [QUrl.fromLocalFile(p) for p in
                (item.data(0, PATH_ROLE) for item in items) if p]
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        result = drag.exec(Qt.CopyAction | Qt.MoveAction, Qt.CopyAction)
        if self.after_drag:
            self.after_drag(result)

