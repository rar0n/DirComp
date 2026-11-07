"""
Custom-painted widgets and their shared color constants/stylesheet
helpers -- the pieces of ui_builder.py that don't depend on
UiBuilderMixin or any `self.` state at all: the 3D-bevel button look
(_bevel_button_stylesheet, InvertButton's own hand-painted diagonal
fill), the themed dark-groove/light-handle scrollbars
(_ThemedScrollBar/_SelectionMarkerScrollBar), and _SelectionColorDelegate
(keeps file/folder text color distinguishable on a selected row), and the
Settings tab's spin-box sliders (SliderSpinBox/SliderDoubleSpinBox +
SpinSlider, plus the two small layout helpers that go with them).

Split out of ui_builder.py as a first, low-risk step toward
modularizing that file -- everything here is self-contained (only
PySide6 + constants.IS_DIR_ROLE), so nothing in ui_builder.py or any
other mixin needs to change how it imports these names: ui_builder.py
re-imports them below, which binds them in its own namespace exactly
as if they were still defined there.
"""

import textwrap

from PySide6.QtCore import Qt, QPoint, QPointF, QObject, QEvent, QRect, QSize
from PySide6.QtGui import (
    QColor, QPainter, QPen, QPolygonF, QPainterPath, QPalette, QBrush, QRegion, QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QAbstractItemView, QPushButton, QScrollBar, QStyle, QProxyStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QStyleOptionSlider, QStylePainter, QToolTip, QWidget,
    QDoubleSpinBox, QHBoxLayout, QLayout, QSizePolicy, QSlider, QSpinBox, QToolButton, QVBoxLayout,
)

from constants import IS_DIR_ROLE

# The tree's own selection-highlight yellow (see the palette setup in
# ui_builder._build_directory_widgets) and Select All's matching button
# color -- also used by selection_ops._update_selection_markers as the
# scrollbar-marker color for a selected row that has no real diff color
# of its own (a plain "file"/"directory" tag -- TAG_COLORS.get returns
# None for those -- which is what every row looks like whenever the
# *other* pane has nothing loaded to diff against). One shared constant
# so all three places stay in sync rather than three copies of the same
# literal.
SELECTION_HIGHLIGHT_COLOR = "#F5D400"

# The InvertButton's two diagonal fill colors (see that class) -- shared
# by both panes' copies of the button, since they're visually the same
# button split two ways rather than two independently-styled buttons.
_INVERT_BUTTON_COLORS = {"dark": "#000000", "light": "#FFFFFF"}


def _press_blend(color_hex: str, other_hex: str, amount: float = 0.15) -> QColor:
    """Nudges color_hex `amount` of the way toward other_hex -- the
    "pressed" cue InvertButton gives both its fill colors (each moves
    toward the other, so the two halves converge slightly rather than
    either one lightening/darkening in isolation). QColor.lighter()/
    .darker() don't work here since they scale HSL lightness, which is a
    no-op on pure black or pure white -- exactly the two default colors
    this needs to handle."""
    c, o = QColor(color_hex), QColor(other_hex)
    return QColor(
        int(c.red() + (o.red() - c.red()) * amount),
        int(c.green() + (o.green() - c.green()) * amount),
        int(c.blue() + (o.blue() - c.blue()) * amount),
    )


def _indent_wheel_color(color1_hex: str, color2_hex: str, steps: int, depth_index: int, reverse: bool = False) -> QColor:
    """Depth-based color for the File List View's indentation guide
    lines (see DirDropTreeWidget.drawBranches in dnd_widgets.py).
    `steps` colors are laid out between color1_hex (position 0) and
    color2_hex (position steps-1); `depth_index` (0-based ancestor
    depth -- the leftmost guide line is 0) wraps via modulo once the
    tree goes deeper than `steps`, so a subtree deeper than the
    configured range just repeats it rather than running out of
    colors. steps <= 1 always returns color1_hex outright -- there's
    no second color to range toward, matching the Settings tab's own
    "steps of 1 disables color 2" rule.

    Interpolates around the HSV color wheel (hue) rather than blending
    RGB in a straight line, so a genuinely rainbow-like range is
    possible rather than just a muddy fade between two colors. The hue
    sweep always goes the same fixed rotational direction (increasing
    hue, wrapping past 359 back to 0 -- or decreasing, if `reverse`)
    rather than always taking the shorter arc between the two hues --
    so which color is 1 vs 2 (and `reverse`) together decide whether
    the sweep is the short way or the long way around the wheel.
    Saturation/value are interpolated linearly between the two colors'
    own values, so a deliberately desaturated or dim endpoint still
    blends sensibly instead of always snapping to full saturation."""
    steps = max(1, steps)
    position = depth_index % steps
    if steps == 1:
        return QColor(color1_hex)
    t = position / (steps - 1)
    c1, c2 = QColor(color1_hex), QColor(color2_hex)
    # QColor.hue() reports -1 for fully desaturated colors (gray/black/
    # white), where hue has no real meaning -- treat as 0 so the
    # interpolation below has a defined starting point rather than
    # propagating a negative value through the math.
    h1 = c1.hue() if c1.hue() >= 0 else 0
    h2 = c2.hue() if c2.hue() >= 0 else 0
    if reverse:
        delta = (h1 - h2) % 360
        hue = (h1 - delta * t) % 360
    else:
        delta = (h2 - h1) % 360
        hue = (h1 + delta * t) % 360
    sat = c1.saturation() + (c2.saturation() - c1.saturation()) * t
    val = c1.value() + (c2.value() - c1.value()) * t
    return QColor.fromHsv(int(hue) % 360, int(sat), int(val))


# The comparison types (the diff-outcome Select-group buttons, in the
# buttons' own top-to-bottom, left-to-right placement) and the
# constants.TAG_COLORS entries that make up each one's file-list row
# coloring: (file tag, folder tag), or just (folder tag,) for Diff
# Folders, which only exists for folders. Rows of the Settings tab's
# "Comparison type colors (File List View)" table are built from this,
# each entry editing its own TAG_COLORS entry independently -- and it's
# also what "Reload Defaults" seeds those entries from, using the
# matching button's default in _DEFAULT_SELECT_COLORS below. Select
# All/Clear/File Parent/All Parents aren't here -- they don't
# correspond to any file-list row coloring.
_SELECT_KEY_TO_TAG_COLOR_KEYS = {
    "missing": ("missing_file", "missing_dir"),
    "matching": ("common_file", "common_dir"),
    "newer": ("diff_date_newer_file", "diff_date_newer_dir"),
    "older": ("diff_date_older_file", "diff_date_older_dir"),
    "smaller": ("diff_size_smaller_file", "diff_count_smaller_dir"),
    "larger": ("diff_size_larger_file", "diff_count_larger_dir"),
    "diff_folders": ("diff_content_dir",),
}

# Default (fg, bg) for every Select-group button, seeded from what used
# to be hardcoded per-button -- now the starting point for
# self._select_colors, which the Settings tab's color pickers mutate.
_DEFAULT_SELECT_COLORS = {
    "select_all": ("black", SELECTION_HIGHLIGHT_COLOR),
    "clear": ("black", "#FFFFFF"),
    "missing": ("black", "#FFA0A0"),
    "matching": ("black", "#90E0C0"),
    "newer": ("black", "#DECFA3"),
    "older": ("black", "#C9A96E"),
    "smaller": ("black", "#D9BCF0"),
    "larger": ("black", "#B98CE0"),
    "diff_folders": ("blue", "#7777DD"),
    "file_parent": ("white", "#3355DD"),
    "all_parents": ("white", "#1A237E"),
}

# Clipboard/File Op buttons' original hardcoded (bg, fg) colors -- fixed
# design colors rather than theme-derived ones (unlike Rename/Up/etc,
# which use _default_button_colors() and are customizable via the
# Settings tab's Default Button Colors swatch instead) -- note the
# (bg, fg) order here, opposite of _DEFAULT_SELECT_COLORS' (fg, bg)
# above, matching _bevel_button_stylesheet(bg, fg)'s own argument order
# instead. self._main_button_colors (ui_builder.py) starts as a copy of
# this, plus a "rename" entry seeded to None (falls back to
# _default_button_colors() -- see _main_button_color_for), and can be
# overridden per-button from the Settings tab's Clipboard/File Op
# Buttons tables.
_DEFAULT_MAIN_BUTTON_COLORS = {
    "copy": ["#D0D0D0", "black"],
    "cut": ["#FFA0A0", "black"],
    "paste": ["#A9784B", "#F5F5F5"],
    "paste_folders": ["#6E6694", "#F5F5F5"],
    "new_folder": ["#3355DD", "black"],
    "delete": ["#C0392B", "#F5F5F5"],
}
# kind passed to _make_icon for whichever of the above (or "rename",
# which has none) also draws an icon -- so a color change updates the
# icon's fg right along with the button's own.
_MAIN_BUTTON_ICON_KINDS = {"cut": "cut", "copy": "copy", "paste": "paste", "paste_folders": "paste", "delete": "delete"}

# Main-tab buttons whose color is customizable (self._main_button_colors)
# but that have no fixed design color of their own -- each starts as None,
# i.e. the theme's default button colors (_default_button_colors()),
# until one is picked in the Settings tab. Rename (File Op Buttons),
# Copy -->/Move --> (Clipboard Buttons), Refresh (Misc Colors), and the
# buttons around the File List View (File List View Colors -> Button
# Colors).
_THEME_DEFAULT_MAIN_BUTTON_KEYS = (
    "rename", "copy_across", "move_across", "refresh",
    "up", "select_dir", "clear_view", "expand_all", "collapse_all",
)

# Statistics View buttons (self._stats_button_colors, Settings tab ->
# Statistics View -> Colors (both panes) -> Buttons) -- all start as None,
# the theme's default button colors. Copy -->/Move --> used to be here;
# they moved to _THEME_DEFAULT_MAIN_BUTTON_KEYS along with the buttons
# themselves (into the Clipboard group).
_STATS_BUTTON_KEYS = ("files", "folders", "zoom_to", "reset", "fit_range", "zoom_in", "zoom_out")

def _darken_hex(hex_color: str, factor: float = 0.82) -> str:
    """Darken a '#RRGGBB' color by `factor` (0-1), for a pressed-looking
    fill under the checked state's inset bevel below."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, int(c * factor))) for c in (r, g, b))
    return f"#{r:02X}{g:02X}{b:02X}"


def _bevel_edge_colors(hex_color: str, delta: int = 60) -> tuple[str, str]:
    """The (light_edge, dark_edge) border colors for a 3D bevel around
    `hex_color`, guaranteed to actually show *some* contrast even at the
    extremes -- a pure white or pure black button base. _darken_hex's
    multiplicative shading (and Qt's own automatic outset/inset border
    shading, which this replaces) both effectively clamp at one end:
    white can't get any lighter, black can't get any darker, so a
    button in either of those colors used to render with just a flat
    single-tone border and no visible shine/shadow at all. Additive
    +/-delta instead always pushes one edge or the other into visible
    territory: white still clamps to itself on the light edge (nothing
    lighter than white), but the dark edge genuinely darkens; black
    mirrors that the other way. Ordinary mid-tone colors get a light
    edge and a dark edge on both sides, same as before."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def clamp(c):
        return max(0, min(255, c))

    light = "".join(f"{clamp(c + delta):02X}" for c in (r, g, b))
    dark = "".join(f"{clamp(c - delta):02X}" for c in (r, g, b))
    return f"#{light}", f"#{dark}"


# Neutral base color for any button that doesn't already carry its own
# meaningful color (Cut/Delete's red, Paste's brown, Invert's gray,
# etc.) is now computed per-window from the palette -- see
# UiBuilderMixin._default_button_colors -- rather than fixed here, so it
# stays "a shade brighter than the window background" on both dark and
# light themes instead of a flat, theme-blind gray.


def _bevel_button_stylesheet(bg: str, fg: str) -> str:
    """Shared 3D-bevel stylesheet for every QPushButton in the app, in
    that button's own base color. Originally only the checkable
    Select-group buttons (Missing/Matching/Smaller/Larger/Newer/Older/
    Diff Folders) got this look, staying sunken via :checked while
    toggled on; :pressed extends the same look to every ordinary,
    non-checkable button too, sinking only for the moment it's actually
    held down since it has nothing to stay toggled on.

    Border width and padding are identical between the unchecked and
    checked/pressed rules on purpose: leaving them unspecified let the
    native style partially take over checked buttons -- drawing its own
    sunken frame *inside* whatever we set, on top of padding we never
    actually controlled -- which is what made a pressed button's colored
    face shrink so much relative to its released size. Spelling out the
    same border-width/padding for every state keeps the button's whole
    box (and its visible face) an identical size throughout.

    border-style outset/inset gives a real 3D bevel automatically
    computed by Qt from a single border-color -- except at the extremes
    (a pure white or pure black button), where that automatic shading
    has nowhere left to go on one side and the button reads flat; see
    _bevel_edge_colors for the explicit light/dark edge colors used
    instead, which stay visible even then. The fill itself also darkens
    slightly in the checked/pressed state (see _darken_hex), on top of
    the bevel, for a proper pressed-button look rather than just a
    border change. Disabled buttons keep the same raised look (in their
    own color) rather than falling back to a flat native gray."""
    pressed_bg = _darken_hex(bg)
    light_edge, dark_edge = _bevel_edge_colors(bg)
    return (
        f"QPushButton {{"
        f" background-color: {bg}; color: {fg};"
        f" border-width: 2px; border-style: solid; border-radius: 3px;"
        f" border-top-color: {light_edge}; border-left-color: {light_edge};"
        f" border-bottom-color: {dark_edge}; border-right-color: {dark_edge};"
        f" padding: 4px 8px;"
        f"}}"
        f"QPushButton:checked, QPushButton:pressed {{"
        f" background-color: {pressed_bg}; color: {fg};"
        f" border-width: 2px; border-style: solid; border-radius: 3px;"
        f" border-top-color: {dark_edge}; border-left-color: {dark_edge};"
        f" border-bottom-color: {light_edge}; border-right-color: {light_edge};"
        f" padding: 4px 8px;"
        f"}}"
        f"QPushButton:disabled {{"
        f" background-color: {bg}; color: {fg};"
        f" border-width: 2px; border-style: solid; border-radius: 3px;"
        f" border-top-color: {light_edge}; border-left-color: {light_edge};"
        f" border-bottom-color: {dark_edge}; border-right-color: {dark_edge};"
        f" padding: 4px 8px;"
        f"}}"
    )


class InvertButton(QPushButton):
    """The Select group's "Invert" button, painting its own face instead
    of using _bevel_button_stylesheet: a diagonal line splits it into a
    black-background/white-text quadrilateral and a white-background/
    black-text quadrilaterals, so the button visually demonstrates the invert
    it performs rather than just sitting there in a single flat color.
    Text is drawn twice, each copy clipped to its own quad, so the
    label reads correctly in both halves no matter where the diagonal
    happens to cross it.

    `mirrored` (default False) flips it horizontally.
    For the right pane's copy of this button when Symmetrical GUI
    has mirrored everything else about that pane's
    layout (see the call site: `mirrored=not layout_left`). Purely
    cosmetic, to keep the diagonal's "reflection" consistent with the
    rest of the mirrored layout around it; doesn't change what the
    button does."""

    def __init__(self, text="Invert", parent=None, mirrored=False):
        super().__init__(text, parent)
        self._mirrored = mirrored
        # This class paints border and fill entirely itself in
        # paintEvent below -- an empty stylesheet keeps the native/
        # style-sheet machinery from drawing anything underneath it.
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")

    def paintEvent(self, event):
        rect = self.rect().adjusted(1, 1, -1, -1)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        pressed = self.isDown()

        # Pressed state converges both fill colors slightly toward each
        # other, the same "sunken" cue _bevel_button_stylesheet gives
        # every other button via :pressed. Colors themselves come from
        # _INVERT_BUTTON_COLORS (Settings tab -> Colors & Theme) rather
        # than being fixed black/white.
        black = _press_blend(_INVERT_BUTTON_COLORS["dark"], _INVERT_BUTTON_COLORS["light"]) if pressed else QColor(_INVERT_BUTTON_COLORS["dark"])
        white = _press_blend(_INVERT_BUTTON_COLORS["light"], _INVERT_BUTTON_COLORS["dark"]) if pressed else QColor(_INVERT_BUTTON_COLORS["light"])

        # Define an inset for the left/right sides so the diagonal
        # doesn't touch the vertical corners, creating a "floating" look.
        inset_ratio = 0.25
        inset = w * inset_ratio  # Calculate absolute pixel value from ratio

        if self._mirrored:
            # Diagonal from Top-Left-ish to Bottom-Right-ish
            # Start point on Top Edge (inset from left)
            start_pt = QPointF(x + inset, y)
            # End point on Bottom Edge (inset from right)
            end_pt = QPointF(x + w - inset, y + h)

            # Shape 1 (Left/Bottom side):
            # Points: TL Corner -> Start Pt -> End Pt -> BL Corner
            first_quad = QPolygonF([
                QPointF(x, y),               # Top-Left
                start_pt,                    # On Top Edge
                end_pt,                      # On Bottom Edge
                QPointF(x, y + h)            # Bottom-Left
            ])

            # Shape 2 (Right/Top side):
            # Points: Start Pt -> TR Corner -> BR Corner -> End Pt
            second_quad = QPolygonF([
                start_pt,                    # On Top Edge
                QPointF(x + w, y),           # Top-Right
                QPointF(x + w, y + h),       # Bottom-Right
                end_pt                       # On Bottom Edge
            ])

        else:
            # Mirrored Diagonal from Top-Right-ish to Bottom-Left-ish
            # Start point on Top Edge (inset from right)
            start_pt = QPointF(x + w - inset, y)
            # End point on Bottom Edge (inset from left)
            end_pt = QPointF(x + inset, y + h)

            # Shape 1 (Right/Bottom side):
            # Points: TR Corner -> Start Pt -> End Pt -> BR Corner
            first_quad = QPolygonF([
                QPointF(x + w, y),           # Top-Right
                start_pt,                    # On Top Edge
                end_pt,                      # On Bottom Edge
                QPointF(x + w, y + h)        # Bottom-Right
            ])

            # Shape 2 (Left/Top side):
            # Points: Start Pt -> TL Corner -> BL Corner -> End Pt
            second_quad = QPolygonF([
                start_pt,                    # On Top Edge
                QPointF(x, y),               # Top-Left
                QPointF(x, y + h),           # Bottom-Left
                end_pt                       # On Bottom Edge
            ])


        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.setPen(Qt.NoPen)
        painter.setBrush(black)
        painter.drawPolygon(first_quad)
        painter.setBrush(white)
        painter.drawPolygon(second_quad)

        # 1.2x the normal button-label size and bold, for legibility --
        # button itself stays the same fixed size (see the call site's
        # setFixedSize), only the label grows.
        font = painter.font()
        font.setPointSizeF(font.pointSizeF() * 1.2)
        font.setBold(True)
        painter.setFont(font)

        # Text drawn twice, clipped to each shape (quadriliterals) in turn
        # in the color that stays readable against that shape's fill.
        painter.setClipping(True)
        for shape, color in (
            (first_quad, white),
            (second_quad, black),
        ):
            path = QPainterPath()
            path.addPolygon(shape)
            painter.setClipPath(path)
            painter.setPen(color)
            #painter.drawText(rect, Qt.AlignCenter, self.text())
            painter.drawText(rect, Qt.AlignCenter, self.text().upper())
        painter.setClipping(False)

        # A light bevel border in a neutral gray, same recipe as
        # _bevel_edge_colors uses elsewhere -- but unlike the single
        # flat-colored border this used to draw (light_edge OR dark_edge
        # around all four sides at once, which is why it read as "off"
        # next to every other button's genuine top/left-vs-bottom/right
        # shading), this now draws top+left in one edge color and
        # bottom+right in the other, same split _paint_handle uses for
        # the themed scrollbar handle. Straight-line edges rather than a
        # single rounded stroke -- at this button's small size and 3px
        # corner radius the difference is imperceptible, and it keeps
        # the two-tone split simple.
        light_edge, dark_edge = _bevel_edge_colors("#808080")
        top_left_color = dark_edge if pressed else light_edge
        bottom_right_color = light_edge if pressed else dark_edge
        pen = QPen()
        pen.setWidth(2)
        painter.setBrush(Qt.NoBrush)
        pen.setColor(QColor(top_left_color))
        painter.setPen(pen)
        painter.drawLine(rect.topLeft(), rect.topRight())
        painter.drawLine(rect.topLeft(), rect.bottomLeft())
        pen.setColor(QColor(bottom_right_color))
        painter.setPen(pen)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomRight())


class _SelectionColorDelegate(QStyledItemDelegate):
    """Makes selected rows easier to tell apart from everything else --
    the tree's Highlight color is set to a strong yellow by default (see
    ui_builder._build_directory_widgets) -- while still keeping files
    and folders visually distinguishable *while selected*: black text
    for files, blue for folders, instead of every selected row's text
    flattening to one color the way the default delegate would. Only
    touches selected rows; unselected rows still go through the
    existing diff-tag coloring untouched.

    Also (Settings tab -> File List View -> "Selection opacity", 100%
    being the plain flat fill) replaces that flat Highlight fill with a
    per-row one: each selected row's own diff-tag background (already sitting
    on it via Qt.BackgroundRole -- see ListingMixin._apply_tag, which
    sets that for every column) tinted toward a chosen color by a
    chosen percentage, so a selected row still shows what its diff
    outcome was instead of every selected row flattening to one solid
    color the way the plain Highlight fill would. `get_selection_tint`
    is a zero-arg callable returning (tint_hex, opacity_pct) -- re-read
    on every paint rather than fixed at construction, same reasoning as
    _make_color_swatch's get_hex, since these 2 settings can change
    after this delegate is built."""

    def __init__(self, parent=None, get_selection_tint=None):
        super().__init__(parent)
        self._get_selection_tint = get_selection_tint or (lambda: (None, 100))

    def selection_tint(self):
        """(tint_hex, opacity_pct) as of right now -- the same 2
        settings paint() below uses. Public so the tree's own
        drawBranches (dnd_widgets.py) can tint the indentation area of
        a selected row exactly like the cells to its right."""
        return self._get_selection_tint()

    def paint(self, painter, option, index):
        if option.state & QStyle.State_Selected:
            option = QStyleOptionViewItem(option)
            is_dir = index.sibling(index.row(), 0).data(IS_DIR_ROLE)
            option.palette.setColor(QPalette.HighlightedText, QColor("blue" if is_dir else "black"))
            tint_hex, opacity_pct = self._get_selection_tint()
            if tint_hex:
                base_brush = index.data(Qt.BackgroundRole)
                # A real per-item color (a diff-tag background) blends
                # from itself; an item with none (see _apply_tag -- an
                # empty QBrush() for a plain, undiffed file/directory)
                # blends from the tree's own normal row color instead,
                # so a plain row still gets a consistent tint rather
                # than blending from nothing.
                if isinstance(base_brush, QBrush) and base_brush.style() != Qt.NoBrush:
                    base_hex = base_brush.color().name()
                else:
                    base_hex = option.palette.color(QPalette.Base).name()
                option.palette.setColor(QPalette.Highlight, _press_blend(base_hex, tint_hex, opacity_pct / 100.0))
        super().paint(painter, option, index)







import math

# Groove (track) and handle base colors shared by every themed scrollbar
# in this app (both the file list trees' vertical _SelectionMarkerScrollBar
# and the Statistics view's horizontal one) -- deliberately the reverse
# of a stock Qt scrollbar (usually a light track with a mid/dark handle):
# a dark track reads as "empty space", a light handle reads as "the
# thing you grab", and on the file trees specifically, the dark track
# also makes the selection-marker tick colors (mostly saturated
# TAG_COLORS reds/purples/tans) stand out far more than they did on the
# previous light-gray-on-light-gray native look.
_SCROLLBAR_COLORS = {"groove": "#1A1A1A", "handle": "#E8E8E8"}
# Opacity the handle is filled at, over whatever's already been painted
# in the groove beneath it -- low enough that content underneath (the
# file trees' marker ticks; nothing, currently, on the Statistics view's
# scrollbar) is still clearly visible-but-dimmed through the handle,
# high enough that the handle itself still unambiguously reads as "the
# handle" and not just a faint tint. The live value (a per-theme
# setting -- Settings tab -> File List View -> "Scrollbar handle
# opacity", see ThemeOpsMixin's color sets) is _SCROLLBAR_STYLE below,
# a dict updated in place like _SCROLLBAR_COLORS; the constants here are
# the built-in default (77.5% before it became a setting).
_DEFAULT_SCROLLBAR_HANDLE_OPACITY_PCT = 78
_SCROLLBAR_HANDLE_OPACITY = _DEFAULT_SCROLLBAR_HANDLE_OPACITY_PCT / 100.0
# How much the track itself covers the selection markers drawn in it (the
# groove, outside/under the handle, and the magnified window inside a
# clamped handle -- see _SelectionMarkerScrollBar) -- independent of the
# handle's own opacity above, same as that one dims the markers rather
# than the handle it's named for. Same direction as handle_opacity: 0%
# leaves the markers fully solid, higher fades them out. Capped at 90
# (not 100) so the track can never fully hide them the way the handle
# can -- there'd be no point to a marker setting that can erase the
# marker outright. Also a per-theme setting (Settings tab -> File List
# View -> "Scrollbar track opacity"), alongside handle_opacity in
# _SCROLLBAR_STYLE.
_DEFAULT_SCROLLBAR_TRACK_OPACITY_PCT = 0
# How the FLV scrollbar handle shows the selection markers beneath it
# (Settings tab -> Global Options -> "Scrollbar handle style"). Unlike
# the two opacities above this is NOT per theme -- it's a Global Option,
# saved once at the top level of the settings file (see
# settings_ops._SHARED_WIDGET_KEYS). The index of a name here is also
# its radio button's id in that Settings-tab QButtonGroup, and what gets
# persisted, so append rather than reorder.
#   "plain"   -- no magnification: markers under the handle are drawn at
#                their plain track position, but solid and before the
#                handle, so only handle_opacity dims them.
#   "fisheye" -- raised-cosine fisheye (see _SelectionMarkerScrollBar).
#   "circle"  -- the experimental mirrored quarter-circle transfer curve.
# ("hard_cutoff", the original on-screen-rows-only magnifier, is still
# implemented in _SelectionMarkerScrollBar but deliberately not offered.)
_SCROLLBAR_HANDLE_STYLES = ("plain", "fisheye", "circle")
_DEFAULT_SCROLLBAR_HANDLE_STYLE = "fisheye"
_SCROLLBAR_STYLE = {
    "handle_opacity": _DEFAULT_SCROLLBAR_HANDLE_OPACITY_PCT,
    "track_opacity": _DEFAULT_SCROLLBAR_TRACK_OPACITY_PCT,
    "handle_style": _DEFAULT_SCROLLBAR_HANDLE_STYLE,
}


# Factor applied to the platform style's own minimum handle length
# (QStyle.PM_ScrollBarSliderMin) for every themed scrollbar. The handle
# is clamped to that minimum on long lists, and the fisheye works better
# with a bit more room to spread markers over.
_SLIDER_MIN_FACTOR = 1.5


class _SliderMinStyle(QProxyStyle):
    """The platform's own style, with only the scrollbar handle's minimum
    length scaled by _SLIDER_MIN_FACTOR. A proxy style rather than
    resizing handle_rect by hand in paintEvent: Qt's mouse hit-testing
    and drag math use the style's own subControlRect, so overriding the
    metric here keeps the drawn handle and the grabbable one identical.

    (Needs QProxyStyle, QStyleFactory and QApplication imported at the
    top of the real custom_widgets.py.)"""

    def pixelMetric(self, metric, option=None, widget=None):
        value = super().pixelMetric(metric, option, widget)
        if metric == QStyle.PM_ScrollBarSliderMin:
            value = int(round(value * _SLIDER_MIN_FACTOR))
        return value


class _ThemedScrollBar(QScrollBar):
    """Base scrollbar look shared by every scrollbar this app themes:
    dark groove/track, light handle, a raised 3D bevel on the handle and
    a sunken one on the groove -- matching the button bevel look (see
    _bevel_button_stylesheet) instead of the platform's native scrollbar,
    which read as barely-there gray-on-gray next to it. Works for either
    orientation -- the bevel math (light top+left edges, dark bottom+
    right, as if lit from the upper left) doesn't care which way the bar
    runs, and _paint_groove_and_handle below draws whichever of
    SC_ScrollBarAddLine/SubLine apply for that orientation via the style
    itself rather than anything orientation-specific of its own.

    Fully custom-painted rather than a QSS-styled native paint, because
    of the handle's opacity (see _SCROLLBAR_STYLE): Qt's own
    native handle paint (whatever it delegates to drawComplexControl for
    SC_ScrollBarSlider) is always fully opaque, QSS included, so
    there's no way to get "translucent handle over hand-painted content"
    out of the native path at all. This widget paints the groove, then
    lets a subclass paint anything of its own into that same groove
    (see _SelectionMarkerScrollBar's marker ticks), then paints the
    handle itself last, on top, translucent -- every time, by hand."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Own the proxy from Python: QWidget.setStyle doesn't take
        # ownership, so without this reference it would be collected.
        self._slider_min_style = _SliderMinStyle(QApplication.style().objectName())
        self.setStyle(self._slider_min_style)

    def _paint_groove_and_handle(self, painter: QPainter, style, opt):
        """Draws the dark groove + sunken bevel and the native arrow
        buttons, and returns (groove_rect, handle_rect) so a caller can
        paint its own content into groove_rect *before* calling
        _paint_handle to finish with the translucent handle on top --
        that ordering is what lets that content show through the handle
        rather than being hidden under it."""
        groove_rect = style.subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarGroove, self)
        handle_rect = style.subControlRect(QStyle.CC_ScrollBar, opt, QStyle.SC_ScrollBarSlider, self)

        painter.fillRect(self.rect(), QColor(_SCROLLBAR_COLORS["groove"]))
        g_light, g_dark = _bevel_edge_colors(_SCROLLBAR_COLORS["groove"])
        pen = QPen()
        pen.setWidth(1)
        pen.setColor(QColor(g_dark))
        painter.setPen(pen)
        painter.drawLine(self.rect().topLeft(), self.rect().topRight())
        painter.drawLine(self.rect().topLeft(), self.rect().bottomLeft())
        pen.setColor(QColor(g_light))
        painter.setPen(pen)
        painter.drawLine(self.rect().bottomLeft(), self.rect().bottomRight())
        painter.drawLine(self.rect().topRight(), self.rect().bottomRight())

        # Arrow buttons only -- native style, left alone; this never
        # touches SC_ScrollBarGroove or SC_ScrollBarSlider, both drawn
        # by hand (here, and in _paint_handle below).
        opt.subControls = QStyle.SC_ScrollBarAddLine | QStyle.SC_ScrollBarSubLine
        style.drawComplexControl(QStyle.CC_ScrollBar, opt, painter, self)
        return groove_rect, handle_rect

    def _paint_handle(self, painter: QPainter, handle_rect):
        """Light, raised bevel, translucent fill -- meant to be called
        last, after anything else this paintEvent draws into the groove,
        so it's the topmost layer and whatever's underneath still shows
        through it, dimmed, rather than being fully hidden."""
        if not handle_rect.isValid():
            return
        painter.setOpacity(_SCROLLBAR_STYLE["handle_opacity"] / 100.0)
        painter.fillRect(handle_rect, QColor(_SCROLLBAR_COLORS["handle"]))
        painter.setOpacity(1.0)
        h_light, h_dark = _bevel_edge_colors(_SCROLLBAR_COLORS["handle"])
        pen = QPen()
        pen.setWidth(1)
        pen.setColor(QColor(h_light))
        painter.setPen(pen)
        painter.drawLine(handle_rect.topLeft(), handle_rect.topRight())
        painter.drawLine(handle_rect.topLeft(), handle_rect.bottomLeft())
        pen.setColor(QColor(h_dark))
        painter.setPen(pen)
        painter.drawLine(handle_rect.bottomLeft(), handle_rect.bottomRight())
        painter.drawLine(handle_rect.topRight(), handle_rect.bottomRight())

    def paintEvent(self, event):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        painter = QPainter(self)
        _groove_rect, handle_rect = self._paint_groove_and_handle(painter, style, opt)
        self._paint_handle(painter, handle_rect)
        painter.end()


class _SelectionMarkerScrollBar(_ThemedScrollBar):
    """A file list tree's vertical scrollbar: the dark-groove/light-
    handle 3D theme from _ThemedScrollBar, plus a thin colored tick
    drawn at the relative position of every currently-selected row --
    each in that row's own diff-tag color (see constants.TAG_COLORS) --
    so a long tree with scattered selections (e.g. a recursive Missing/
    Matching search that hit files all over the tree) shows at a glance
    where they all are without having to scroll around looking. Doesn't
    change how scrolling itself behaves.

    Markers are supplied by UiBuilderMixin._update_selection_markers,
    recomputed whenever the owning tree's selection or expand/collapse
    state changes (see ui_builder._build_directory_widgets) -- expand/
    collapse shifts every row below it, and lazy population
    (ListingMixin) only ever happens on expand, so both need to trigger
    a recompute, not just a selection change on its own.

    Handle "magnifier": on a long list the handle is clamped to the
    style's minimum length, so it covers more of the track than the rows
    actually on screen do (natural handle length = groove * page / span,
    which can be a pixel or two). Left alone, the markers seen through
    such a handle are a much wider stretch of the list than the FLV is
    showing. _magnifier_style (Settings tab -> Global Options ->
    "Scrollbar handle style", held in _SCROLLBAR_STYLE["handle_style"])
    picks what the handle does about that -- peak magnification itself
    is the same number for the magnifying styles: handle length /
    natural length (= (M/T) / (V/L) in handle-min / track / visible /
    total terms).
      - "plain": no magnification. Markers under the handle stay at
        their plain track position; they're just painted solid before
        the handle (rather than with the ordinary markers' track
        opacity) so only handle_opacity dims them.
      - "circle": the same window and independent top/bottom halves as
        the fisheye below, but with the experimental mirrored quarter-
        circle transfer curve of _circle_row_to_y in place of the
        raised cosine (which ignores `mag`, and clumps rows near the
        window's edges instead of tapering smoothly).
      - "fisheye" (the default, see _fisheye_window/
        _fisheye_row_to_y): a window of rows *symmetric around the row
        at the middle of the FLV's view*, as wide as the (clamped,
        oversized) handle spans at the plain track's per-row rate, is
        redrawn spread across the handle instead of being hidden under
        it: most of the handle's space goes to the rows near the view's
        center, tapering by a raised-cosine curve down towards no
        distortion at the handle's own top/bottom edges. The mapping
        depends only on a row's distance from the view's center, never
        on where the handle sits on the track, and is mirror-symmetric
        around the handle's center. A row outside the window is left
        as an ordinary track marker at its plain position.

        Nominal peak `mag` and nominal edge 1x are the *shape* this is
        built from, not literally what's displayed: the handle has a
        fixed pixel budget (its own height) for however many rows fall
        in its covered span, so the density has to average out to
        exactly 1x across that whole span -- a mathematical
        certainty, not a tuning choice. A curve that's >= 1x
        everywhere (never actually shrinking a row's rate below the
        plain track's own rate) can only average to 1x if it's
        constant 1x throughout -- so hitting a real peak above 1x
        necessarily means the achieved edge value comes out *below*
        1x too, both scaled down from their nominal targets by the same
        factor, 2/(1 + mag) (e.g. at mag=5, nominal peak 5x and nominal
        edge 1x actually render as ~1.67x and ~0.33x). That leaves a
        small step right at the handle's edge, up to the ordinary
        track's full 1x rate just outside it -- much smaller than the
        old hard-cutoff's all-or-nothing jump, but not literally
        seamless. (A shape that hits the nominal peak and edge
        exactly, by dipping below 1x somewhere in between to
        compensate, was tried and rejected: the compensating dip goes
        negative -- meaningless as a density -- for any mag much above
        ~3, which ordinary large trees exceed easily, so it would cap
        out well below where this feature is actually needed.)
      - "hard_cutoff" (the original approach, kept in
        _hard_cutoff_row_to_y but not offered as a Settings-tab
        choice): only the rows
        currently on screen [pos, pos + page] are shown at all, spread
        linearly over the full handle; everything outside that window
        is simply not drawn on the handle. Simpler, but a selection
        outside the current view vanishes from the handle instead of
        just shrinking.
    Either way: because the visible rows are the fraction [pos, pos +
    page] of the whole span, the magnified content moves on its own as
    the handle moves, from top edge (scrolled to the top) to bottom
    edge (scrolled to the bottom) -- no separate "window position"
    formula needed. Track markers that would lie under the handle are
    not drawn there (the magnified ones replace them); markers outside
    the handle are unchanged. If the handle is already its natural
    length (short list) the fisheye/circle still run, at mag 1 (a plain
    linear mapping), so markers under the handle are always drawn the
    same way and get handle_opacity only. Markers inside the window are
    also drawn at their true position on the track outside the handle
    -- the handle is a lens on top of the overview, not a replacement
    for that part of it (skipping them left a gap in the track that led
    or trailed the handle).

    Marker opacity: the ordinary track markers and the magnified ones
    are dimmed by two different, non-compounding settings -- each gets
    exactly one:
      - Ordinary track markers fade according to
        _SCROLLBAR_STYLE["track_opacity"] (Settings tab -> File List
        View -> "Scrollbar track opacity", see its own comment above
        _SCROLLBAR_STYLE for the 0=solid/90=faded direction). They
        never overlap the handle (see the clip region below), so a
        plain painter.setOpacity blend against the groove color already
        painted there is enough, and handle_opacity plays no part in
        how solid they look.
      - Magnified markers, being right where the handle sits, instead
        pick up *only* the handle's own opacity (_SCROLLBAR_STYLE
        ["handle_opacity"]) -- track_opacity has no effect on them at
        all, same as handle_opacity has none on the ordinary ones. This
        falls out for free from paint order rather than any hand-
        rolled blend: they're painted solid, in the groove, *before*
        _paint_handle -- exactly like the groove color itself -- so
        when the handle's own translucent fill is painted over them
        next, Qt's normal alpha compositing dims them by handle_opacity
        the same way it dims the groove underneath the rest of the
        handle. (An earlier version painted them *after* the handle
        with a hand-rolled blend to avoid double-dimming; this ordering
        avoids the double-dimming a different way, by construction,
        while also making the dimming be handle_opacity's rather than
        track_opacity's.)"""

    @property
    def _magnifier_style(self) -> str:
        """Which row->handle-position mapping the handle uses -- "plain",
        "fisheye" or "circle" (the Settings-tab choice, held in
        _SCROLLBAR_STYLE["handle_style"] so every scrollbar follows it
        at once), or the still-implemented but not offered "hard_cutoff"
        (see the class docstring)."""
        return _SCROLLBAR_STYLE["handle_style"]

    def __init__(self, parent=None):
        super().__init__(Qt.Vertical, parent)
        self._markers: list[tuple[float, QColor]] = []
        self._total_rows = 0

    def set_markers(self, markers: "list[tuple[float, QColor]]", total_rows: int = 0):
        """markers: (fraction 0..1 along the visible rows, color) per
        selected row -- fraction = row / (total_rows - 1). total_rows:
        number of visible rows those fractions refer to; needed for the
        handle magnifier (0 = unknown -> no magnifier, markers just show
        through the handle as before)."""
        self._markers = markers
        self._total_rows = total_rows
        self.update()

    @staticmethod
    def _track_marker_alpha() -> float:
        """0..1 alpha a marker tick is drawn at -- see _SCROLLBAR_STYLE's
        "track_opacity" comment for the 0=solid/90=faded direction (the
        reverse of a plain "marker opacity" would read, but the same
        direction as handle_opacity, which is the point)."""
        return (100 - _SCROLLBAR_STYLE["track_opacity"]) / 100.0

    def _magnifier_window(self, groove_rect, handle_rect):
        """(offset, page, span, mag) in scrollbar units if the handle
        should act as a magnifier right now, else None. offset..offset+
        page is the part of 0..span that's on screen in the FLV; mag is
        the peak magnification factor (handle length / natural length),
        used as-is by the fisheye mapping and implicitly by the hard-
        cutoff one (which achieves the same peak by construction,
        rather than reading this value)."""
        page = self.pageStep()
        span = self.maximum() - self.minimum() + page
        if self._total_rows < 2 or page <= 0 or span <= 0 or not handle_rect.isValid():
            return None
        natural = groove_rect.height() * page / span
        if natural <= 0:
            return None
        if handle_rect.height() <= natural + 1.0 and self._magnifier_style == "hard_cutoff":
            return None
        # The fisheye runs even when the handle isn't clamped (mag 1,
        # i.e. a plain linear mapping) so markers under the handle are
        # always drawn the same way -- and get only handle_opacity.
        mag = max(1.0, handle_rect.height() / natural)
        return self.sliderPosition() - self.minimum(), page, span, mag

    def _hard_cutoff_row_to_y(self, row, offset, page, unit_per_row, handle_rect):
        """Original magnifier mapping: row's y inside handle_rect if
        it's one of the rows currently on screen (linear, filling the
        whole handle), else None to mean "don't draw this one" -- the
        window [offset, offset + page] simply clips off everything
        else. Kept for a possible future "magnifier style" setting;
        not called anywhere right now (see _magnifier_style)."""
        rel = ((row + 0.5) * unit_per_row - offset) / page
        if rel < 0.0 or rel > 1.0:
            return None
        h_top = handle_rect.top()
        h_height = handle_rect.height()
        # Keep clear of the handle's 1px bevel edges, so a row at the
        # very top/bottom of the view still shows.
        return min(max(h_top + int(rel * h_height), h_top + 2), h_top + h_height - 2)

    def _fisheye_window(self, groove_rect, handle_rect, rows, center_row):
        """(row_lo, row_hi): the rows the fisheye covers, a window of
        fixed size *symmetric around center_row* (the row in the middle
        of the FLV's current view). Its half-width is however many rows
        the (clamped, oversized) handle spans at the plain track's
        per-row rate: handle_height / 2 / (groove_height / (rows-1)).

        Deliberately NOT derived from where the handle happens to sit
        on the track, and NOT clamped to 0..rows-1: an earlier version
        took the rows whose plain position fell under the handle's
        screen rect, which made the window lopsided around center_row
        (and squeezed against the list's ends), so how a marker moved
        across the handle depended on the handle's position. This way
        a marker N rows from the view's center lands at the same place
        on the handle wherever the handle is on the track, and the
        mapping is mirror-symmetric around the handle's center.

        Rows outside this window are ordinary track markers."""
        half = handle_rect.height() * (rows - 1) / (2.0 * groove_rect.height())
        return center_row - half, center_row + half

    def _circle_row_to_y(self, row, center_row, mag, row_lo, row_hi, handle_rect):
        """Experimental alternative to _fisheye_row_to_y: a quarter-
        circle transfer curve instead of the raised-cosine one --
        the "circle" _magnifier_style, labelled experimental in the
        Settings tab. Ignores `mag` entirely, same as the prototype
        it's fixed from.

        x^2 + y^2 = 1, with x = normalized *distance from center_row*
        (0 at center_row, 1 at whichever edge is on that side) and
        y = sqrt(1 - (1-x)^2) = the corresponding *output* distance
        from the handle's own center (also 0..1, but at a different
        rate than x -- that's the whole point of using a curved
        transfer function instead of a straight line). This is the
        mirror image of the first attempt (y = 1 - sqrt(1-x^2)), which
        had it backwards: that version had a *flat* (zero-slope)
        tangent at x=0/center_row and a *vertical* (infinite-slope) one
        at x=1/the edge -- rows right at the center, where you're
        actually looking, got crammed together, while rows right at
        the edge got stretched apart more and more steeply. Flipping
        which end of the arc the vertical tangent sits on fixes the
        direction (steep at the center, easing off toward the edges)
        at the cost of moving the other pathology to the opposite end:
        the edge tangent is now exactly flat (zero slope), so rows
        right next to row_lo/row_hi will visually clump together
        rather than tapering smoothly into the plain track markers
        just outside the handle, the way the raised-cosine version
        does. Each side is computed independently and halved onto its
        own half of the handle -- same reasoning as _fisheye_row_to_y:
        that's what pins center_row to exactly the handle's middle
        regardless of how (a)symmetric row_lo/center_row/row_hi are,
        rather than letting whichever side has more row-mass pull the
        center off 0.5."""
        if row <= center_row:
            radius = center_row - row_lo
            t = (center_row - row) / radius if radius > 0.0 else 0.0
            t = min(max(t, 0.0), 1.0)  # guard float overshoot before sqrt
            circle_out = math.sqrt(max(0.0, 1.0 - (1.0 - t) * (1.0 - t)))
            out_y = 0.5 * (1.0 - circle_out)  # t=0 (center) -> 0.5; t=1 (row_lo) -> 0.0
        else:
            radius = row_hi - center_row
            t = (row - center_row) / radius if radius > 0.0 else 0.0
            t = min(max(t, 0.0), 1.0)
            circle_out = math.sqrt(max(0.0, 1.0 - (1.0 - t) * (1.0 - t)))
            out_y = 0.5 + 0.5 * circle_out  # t=0 (center) -> 0.5; t=1 (row_hi) -> 1.0

        h_top = handle_rect.top()
        h_height = handle_rect.height()
        return min(max(h_top + int(out_y * h_height), h_top + 2), h_top + h_height - 2)

    def _fisheye_row_to_y(self, row, center_row, mag, row_lo, row_hi, handle_rect):
        """row's y inside handle_rect under the raised-cosine fisheye,
        for a row already known to be within [row_lo, row_hi] (see
        _fisheye_window -- callers must filter first, this doesn't
        check).

        The two sides of center_row are mapped *independently*, each
        onto exactly its own half of the handle (top half for rows
        above center_row, bottom half for rows below) -- rather than
        joining them into one shared budget split by however much row-
        mass happens to sit on each side. That's what pins center_row
        to the handle's exact vertical middle regardless of how
        (a)symmetric row_lo/center_row/row_hi are, which a shared-
        budget version doesn't: it lets whichever side has more row-
        mass claim more than half the handle, dragging center_row's own
        drawn position off-middle. One consequence: since each side's
        own row-count (radius_l vs radius_r) is generally different but
        each still fills exactly half the handle, the two sides end up
        with different achieved magnification strengths from each
        other -- the side with fewer hidden rows to place gets *more*
        per-row space than the side with more, not the same amount.

        Within one side, unnormalized density is
        1 + (mag-1)*0.5*(1+cos(pi*t)) (t = distance from center_row /
        that side's own radius) -- nominal peak `mag` at center_row,
        tapering to nominal 1x at that side's own edge (row_lo or
        row_hi). Rescaling that side's cumulative total to exactly fill
        its half of the handle -- required, fixed pixel budget for a
        fixed row range -- is what pulls its *achieved* peak and edge
        below those nominal numbers, both by the same factor 2/(1+mag)
        (see the class docstring's "Handle magnifier" section for why:
        a curve that's never below 1x can only average to 1x by being
        constant 1x, so real magnification above 1x anywhere pulls the
        true edge value below nominal 1x too). This factor is the same
        on both sides even though radius_l != radius_r -- it falls out
        of the shape alone, not the width it's stretched over -- so
        what differs between the two sides is the *baseline* rate each
        is scaled from (handle_height/2 divided by that side's own row
        count), not this factor itself.

        Closed-form rather than summing density row by row (which
        would be an O(total_rows) scan per repaint on huge trees):
        density(x) = A + B*cos(pi*x/radius) has antiderivative
        F(x) = A*x + B*(radius/pi)*sin(pi*x/radius), so the cumulative
        distribution up to any row is one direct evaluation of F, not a
        loop."""
        a = 1.0 + (mag - 1.0) / 2.0
        b = (mag - 1.0) / 2.0

        def f(x, radius):
            if radius <= 0.0:
                return 0.0
            x = min(max(x, 0.0), radius)
            return a * x + b * (radius / math.pi) * math.sin(math.pi * x / radius)

        h_top = handle_rect.top()
        h_height = handle_rect.height()

        if row <= center_row:
            radius = center_row - row_lo
            total = f(radius, radius)
            cdf = total - f(center_row - row, radius)
            half_frac = cdf / total if total > 0.0 else (row - row_lo) / max(radius, 1e-9)
            frac = 0.5 * half_frac
        else:
            radius = row_hi - center_row
            total = f(radius, radius)
            cdf = f(row - center_row, radius)
            half_frac = cdf / total if total > 0.0 else (row - center_row) / max(radius, 1e-9)
            frac = 0.5 + 0.5 * half_frac

        return min(max(h_top + int(frac * h_height), h_top + 2), h_top + h_height - 2)

    def paintEvent(self, event):
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        style = self.style()
        painter = QPainter(self)
        groove_rect, handle_rect = self._paint_groove_and_handle(painter, style, opt)

        if self._markers and groove_rect.isValid() and groove_rect.height() > 0:
            handle_style = self._magnifier_style
            plain = handle_style == "plain"
            window = None if plain else self._magnifier_window(groove_rect, handle_rect)
            mpen = QPen()
            mpen.setWidth(2)
            x1 = groove_rect.left() + 1
            x2 = groove_rect.right() - 1
            groove_top = groove_rect.top()
            groove_height = groove_rect.height()

            fisheye = None  # (row_lo, row_hi, center_row) for fisheye/circle
            if window is not None and handle_style != "hard_cutoff":
                w_offset, w_page, w_span, _w_mag = window
                w_rows = self._total_rows
                center_row = (w_offset + w_page / 2.0) / (w_span / w_rows)
                row_lo, row_hi = self._fisheye_window(groove_rect, handle_rect, w_rows, center_row)
                fisheye = (row_lo, row_hi, center_row)

            # Ordinary track markers, at their plain position, dimmed by
            # track_opacity only. Whenever the handle draws its own
            # markers (every style but a magnifier that had to give up),
            # the handle's rect is clipped out here so nothing painted
            # with track_opacity ever ends up under the handle's own
            # translucent fill -- that overlap is what used to stack
            # track_opacity on top of handle_opacity.
            handle_draws_markers = plain or window is not None
            painter.save()
            painter.setOpacity(self._track_marker_alpha())
            if handle_draws_markers:
                painter.setClipRegion(QRegion(groove_rect).subtracted(QRegion(handle_rect)))
            for fraction, color in self._markers:
                y = groove_top + int(fraction * groove_height)
                mpen.setColor(color)
                painter.setPen(mpen)
                painter.drawLine(x1, y, x2, y)
            painter.restore()

            # Markers under the handle. Painted *before* the handle (same
            # side of the paint order as the groove and the ordinary
            # track markers above), solid/full-color, no opacity applied
            # here at all -- so that when _paint_handle fills this same
            # rect next with its own translucent color, Qt composites
            # the handle over these markers exactly the way it
            # composites the handle over the plain groove elsewhere.
            # That's what makes handle_opacity (and only handle_opacity)
            # the thing that dims them, in every style; track_opacity,
            # applied only to the ordinary markers above, never touches
            # this block.
            if handle_draws_markers:
                painter.save()
                painter.setClipRect(handle_rect)
                if plain:
                    # No magnification: each marker at the same plain
                    # position the ordinary pass would have drawn it at
                    # (clipped to the handle here, to the rest of the
                    # groove above).
                    for fraction, color in self._markers:
                        y = groove_top + int(fraction * groove_height)
                        mpen.setColor(color)
                        painter.setPen(mpen)
                        painter.drawLine(x1, y, x2, y)
                elif handle_style == "hard_cutoff":
                    offset, page, span, _mag = window
                    rows = self._total_rows
                    unit_per_row = span / rows
                    for fraction, color in self._markers:
                        row = round(fraction * (rows - 1))
                        y = self._hard_cutoff_row_to_y(row, offset, page, unit_per_row, handle_rect)
                        if y is None:
                            continue
                        mpen.setColor(color)
                        painter.setPen(mpen)
                        painter.drawLine(x1, y, x2, y)
                else:
                    _offset, _page, _span, mag = window
                    rows = self._total_rows
                    row_lo, row_hi, center_row = fisheye
                    row_to_y = self._circle_row_to_y if handle_style == "circle" else self._fisheye_row_to_y
                    for fraction, color in self._markers:
                        row = fraction * (rows - 1)
                        # Only rows inside the symmetric window around
                        # the view's center go through the transfer
                        # curve -- everything else was already painted
                        # above, at its ordinary track position.
                        if row < row_lo or row > row_hi:
                            continue
                        y = row_to_y(row, center_row, mag, row_lo, row_hi, handle_rect)
                        mpen.setColor(color)
                        painter.setPen(mpen)
                        painter.drawLine(x1, y, x2, y)
                painter.restore()

        self._paint_handle(painter, handle_rect)

        painter.end()










# ------------------------------------------------ spin boxes with sliders

# Slider width rule (see SpinSlider._apply_width): every slider's
# slide area is the same _SPIN_SLIDER_WIDTH px (the two end buttons come
# on top of that), whatever its spin box's range -- so a column of them
# lines up. A huge range (800-5000, 2-500) therefore has fewer pixels
# than values and leans on its arrow buttons, the spin box itself and,
# where it makes sense, a coarser slider_step.
_SPIN_SLIDER_WIDTH = 200
# Older names, kept in case anything still imports them: same value.
_SPIN_SLIDER_MIN_WIDTH = _SPIN_SLIDER_MAX_WIDTH = _SPIN_SLIDER_WIDTH
# Left indent (px) of a slider that sits under its spin box's title: none,
# it lines up with the title's left edge.
SPIN_SLIDER_INDENT = 0
_SPIN_SLIDER_MIN_HEIGHT = 22


class _SliderHost:
    """Mixin for SliderSpinBox/SliderDoubleSpinBox: tells every attached
    SpinSlider whenever the spin box changes. valueChanged alone is not
    enough -- this app sets spin boxes from code under blockSignals()
    in several places (selection tint, hover opacity, the file-list
    split following its splitter handle, ...), which would leave a
    signal-driven slider showing a stale position. Python-level
    setValue/setRange/setToolTip/... overrides see those calls regardless
    of blocked signals (enabled state, being an event, comes through
    changeEvent); user edits (typing, arrows, wheel) come through
    valueChanged instead, which SpinSlider is connected to as well.
    """

    _sliders = None

    def attach_slider(self, slider):
        if self._sliders is None:
            self._sliders = []
        self._sliders.append(slider)

    def _notify_sliders(self, state_only: bool = False):
        for slider in list(self._sliders or ()):
            try:
                if state_only:
                    slider.mirror_spin_state()
                else:
                    slider.sync_from_spin()
            except RuntimeError:  # slider's C++ object already gone (UI rebuild)
                self._sliders.remove(slider)

    def setValue(self, value):
        super().setValue(value)
        self._notify_sliders()

    def setRange(self, minimum, maximum):
        super().setRange(minimum, maximum)
        self._notify_sliders()

    def setMinimum(self, minimum):
        super().setMinimum(minimum)
        self._notify_sliders()

    def setMaximum(self, maximum):
        super().setMaximum(maximum)
        self._notify_sliders()

    def setToolTip(self, text):
        super().setToolTip(text)
        self._notify_sliders(state_only=True)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self._notify_sliders(state_only=True)


class SliderSpinBox(_SliderHost, QSpinBox):
    """QSpinBox that a SpinSlider can follow -- otherwise identical."""


class SliderDoubleSpinBox(_SliderHost, QDoubleSpinBox):
    """QDoubleSpinBox that a SpinSlider can follow -- otherwise identical."""


# How far a disabled slider's track is tinted toward the GUI background
# (0 = untouched, 1 = invisible) -- see _FocusWheelSlider.paintEvent.
_DISABLED_SLIDER_TRACK_TINT = 0.65


class _FocusWheelSlider(QSlider):
    """Horizontal slider that only takes the mouse wheel while it has
    keyboard focus (click or Tab to it). The Settings tab is one long
    scroll area, and a slider that grabs every wheel tick passing over
    it makes scrolling the page change settings by accident; unfocused,
    the wheel is handed on so the page scrolls instead.

    While disabled, its track is also tinted toward the GUI background
    (_DISABLED_SLIDER_TRACK_TINT), so the whole slider reads as
    disabled -- the style itself only dims the handle, and the end
    buttons' arrows (SpinSlider) dim on their own."""

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, event):
        if self.isEnabled():
            super().paintEvent(event)
            return
        # Groove first, the tint over just the groove, then the handle
        # on top of that -- so only the track fades, not the handle.
        painter = QStylePainter(self)
        style = self.style()
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle_only = QStyleOptionSlider(opt)
        handle_only.subControls = QStyle.SC_SliderHandle
        opt.subControls = QStyle.SC_SliderGroove | QStyle.SC_SliderTickmarks
        painter.drawComplexControl(QStyle.CC_Slider, opt)
        groove = style.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        tint = QColor(self.palette().color(QPalette.Window))
        tint.setAlphaF(_DISABLED_SLIDER_TRACK_TINT)
        painter.fillRect(groove, tint)
        painter.drawComplexControl(QStyle.CC_Slider, handle_only)
        painter.end()

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class SpinSlider(QWidget):
    """A slider with a small step button at each end, bound two-way to
    an existing SliderSpinBox/SliderDoubleSpinBox (which stays in charge:
    same object, same valueChanged signal, same range -- every existing
    handler, setValue/blockSignals call and settings load keeps working
    exactly as before, and the slider just follows). The buttons step by
    the spin box's own singleStep and are as tall as the spin box itself,
    since the spin box's built-in arrows are too small to hit
    comfortably. Enabled state and tooltip are mirrored from the spin
    box, so greying one greys the other.

    slider_step: distance between slider positions, in spin box units
    (default: 1 for a QSpinBox, one last-decimal for a QDoubleSpinBox).
    A coarser one (e.g. 50 on an 800-5000 px range) keeps the slider
    from needing pixel precision; the buttons and the spin box still
    give the fine steps.

    live: True applies every step of a drag right away. False shows the
    new number in the spin box while dragging but holds back its
    valueChanged until the handle is released -- for settings whose
    handler is expensive (re-fonting or re-binning whole views).
    Clicks on the groove, the arrow buttons and the keyboard always
    apply immediately either way.

    Width: always _SPIN_SLIDER_WIDTH (see there); `width` overrides it."""

    def __init__(self, spin, width: int = None, slider_step: float = None,
                 live: bool = True, parent=None):
        super().__init__(parent)
        if not isinstance(spin, _SliderHost):
            raise TypeError("SpinSlider needs a SliderSpinBox or SliderDoubleSpinBox")
        self._spin = spin
        self._is_double = isinstance(spin, QDoubleSpinBox)
        self._step = slider_step or (10 ** -spin.decimals() if self._is_double else 1)
        self._width_override = width
        self._live = live
        self._pending_release = False

        size = max(spin.sizeHint().height(), _SPIN_SLIDER_MIN_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._down_button = self._make_step_button(Qt.LeftArrow, -1, size)
        self._slider = _FocusWheelSlider(self)
        self._slider.setFixedHeight(size)
        self._up_button = self._make_step_button(Qt.RightArrow, +1, size)
        layout.addWidget(self._down_button)
        layout.addWidget(self._slider)
        layout.addWidget(self._up_button)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._slider.valueChanged.connect(self._on_slider_moved)
        self._slider.sliderReleased.connect(self._on_slider_released)
        spin.valueChanged.connect(self.sync_from_spin)
        spin.attach_slider(self)
        self.sync_from_spin()
        self.mirror_spin_state()

    def _make_step_button(self, arrow, direction: int, size: int) -> QToolButton:
        button = QToolButton(self)
        button.setArrowType(arrow)
        button.setFixedSize(size, size)
        button.setFocusPolicy(Qt.NoFocus)
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(350)
        button.setAutoRepeatInterval(60)
        button.clicked.connect(lambda _checked=False, d=direction: self._spin.stepBy(d))
        return button

    # -- value <-> slider position. Positions are 0..n (n = range / step),
    # so one integer slider serves int and float spin boxes alike.

    def _to_position(self, value) -> int:
        return int(round((value - self._spin.minimum()) / self._step))

    def _to_value(self, position: int):
        spin = self._spin
        value = spin.minimum() + position * self._step
        value = min(max(value, spin.minimum()), spin.maximum())
        return round(value, spin.decimals()) if self._is_double else int(round(value))

    def sync_from_spin(self, *_):
        """Range + position from the spin box, without emitting anything.
        Called on every spin box change (see _SliderHost) and by its
        valueChanged."""
        top = max(1, self._to_position(self._spin.maximum()))
        slider = self._slider
        slider.blockSignals(True)
        if slider.maximum() != top:
            slider.setRange(0, top)
            slider.setPageStep(max(1, top // 10))
        slider.setValue(self._to_position(self._spin.value()))
        slider.blockSignals(False)
        self._apply_width(top)

    def mirror_spin_state(self):
        self.setEnabled(self._spin.isEnabled())
        self.setToolTip(self._spin.toolTip())

    def _apply_width(self, top: int):
        width = self._width_override or _SPIN_SLIDER_WIDTH
        if self._slider.width() != width or self._slider.minimumWidth() != width:
            self._slider.setFixedWidth(width)

    def _on_slider_moved(self, position: int):
        value = self._to_value(position)
        if not self._live and self._slider.isSliderDown():
            # Dragging: update the number, hold the handlers back until release.
            self._pending_release = True
            self._spin.blockSignals(True)
            self._spin.setValue(value)
            self._spin.blockSignals(False)
            return
        self._spin.setValue(value)
        self.sync_from_spin()  # snaps the handle back if the value was clamped

    def _on_slider_released(self):
        if self._pending_release:
            self._pending_release = False
            self._spin.valueChanged.emit(self._spin.value())


def spin_slider_cell(title, spin, slider: "SpinSlider") -> QWidget:
    """[title][spin box] on one line with the slider on a line of its
    own underneath, lined up with the title's left edge
    (SPIN_SLIDER_INDENT = 0) -- the layout for a spin box that shares
    its line with other controls.
    `title` is the spin box's QLabel (or None). Add the returned cell to
    the row with Qt.AlignTop, and put the row's other controls through
    spin_row_strip so they line up with the spin box's own line."""
    cell = QWidget()
    outer = QVBoxLayout(cell)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(2)
    top = QHBoxLayout()
    top.setContentsMargins(0, 0, 0, 0)
    if title is not None:
        top.addWidget(title)
    top.addWidget(spin)
    top.addStretch(1)
    outer.addLayout(top)
    below = QHBoxLayout()
    below.setContentsMargins(0, 0, 0, 0)
    below.addSpacing(SPIN_SLIDER_INDENT)
    below.addWidget(slider)
    below.addStretch(1)
    outer.addLayout(below)
    return cell


def spin_slider_inline(title, spin, slider: "SpinSlider", spacing: int = 12) -> QWidget:
    """[title][spin box][slider] all on one line -- the layout for a
    spin box that is the only one on its row and sits at the end of it
    (nothing follows it), so the slider has room next to it instead of
    below. `title` is the spin box's QLabel (or None). The row's other
    controls just go into the row layout directly, all vertically
    centered (use Qt.AlignVCenter when adding the returned widget)."""
    cell = QWidget()
    layout = QHBoxLayout(cell)
    layout.setContentsMargins(0, 0, 0, 0)
    if title is not None:
        layout.addWidget(title, 0, Qt.AlignVCenter)
    layout.addWidget(spin, 0, Qt.AlignVCenter)
    layout.addSpacing(spacing)
    layout.addWidget(slider, 0, Qt.AlignVCenter)
    return cell


def spin_row_strip(like_spin, *items, spacing: int = 6) -> QWidget:
    """The other controls of a row that also holds a spin_slider_cell,
    in one strip as tall as `like_spin` with each item vertically
    centered -- so they sit level with the spin box's own line rather
    than centered on the (taller) cell. `items` are widgets, or ints for
    extra spacing. Add the strip to the row with Qt.AlignTop."""
    strip = QWidget()
    layout = QHBoxLayout(strip)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for item in items:
        if isinstance(item, int):
            layout.addSpacing(item)
        else:
            layout.addWidget(item, 0, Qt.AlignVCenter)
    strip.setMinimumHeight(like_spin.sizeHint().height())
    return strip


# --------------------------------------------------------------- tooltips

_TOOLTIP_WRAP_WIDTH = 56


def _wrap_tooltip(text: str, width: int = _TOOLTIP_WRAP_WIDTH) -> str:
    """Plain tooltip text with hard line breaks at word boundaries, so
    a long one shows as a compact block about `width` characters wide
    instead of one very long line. Line breaks already in the text are
    kept as they are; a single word longer than the width (a path, say)
    is left whole rather than chopped."""
    lines = []
    for paragraph in text.split("\n"):
        lines.extend(
            textwrap.wrap(paragraph, width=width, break_long_words=False, break_on_hyphens=False)
            or [""]
        )
    return "\n".join(lines)


class _WrappedToolTipFilter(QObject):
    """Application-wide event filter that shows every plain-text
    tooltip -- a widget's own toolTip(), or a table/tree item's
    ToolTipRole -- through _wrap_tooltip, so no individual setToolTip
    call anywhere in the app needs to wrap its own text. Rich-text
    tooltips (starting with a tag) are left to Qt, which lays those
    out itself; tooltips with nothing to show fall through to the
    normal handling (including the item views' elided-text tooltips)."""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.ToolTip or not isinstance(obj, QWidget):
            return False
        text = obj.toolTip()
        duration = obj.toolTipDuration()
        rect = QRect()
        if not text:
            # Item views deliver the event to their viewport, which has
            # no tooltip of its own -- the text lives on the item under
            # the cursor.
            view = obj.parent()
            if isinstance(view, QAbstractItemView) and obj is view.viewport():
                index = view.indexAt(event.pos())
                if index.isValid():
                    text = index.data(Qt.ToolTipRole) or ""
        if not text or text.lstrip().startswith("<"):
            return False
        QToolTip.showText(event.globalPos(), _wrap_tooltip(text), obj, rect, duration)
        return True


_tooltip_filter = None


class _SpinBoxWheelGuard(QObject):
    """Application-wide event filter that makes every spin box (plain
    QSpinBox/QDoubleSpinBox and the Slider* ones alike, wherever in the
    app they live) take the mouse wheel only while it has keyboard
    focus (click or Tab into it) -- the same rule _FocusWheelSlider
    follows. Without this a wheel tick passing over a spin box, e.g.
    while scrolling the Settings tab, changes its value by accident.

    Two parts. Spin boxes are polished with StrongFocus instead of
    Qt's default WheelFocus: with WheelFocus the very first wheel tick
    over an unfocused box would hand it focus (and the next tick then
    spin it). And a wheel event that reaches an unfocused spin box (or
    its inner line edit) is marked ignored, which is what makes Qt pass
    it on to the parent -- so the page scrolls instead."""

    @staticmethod
    def _owning_spin(obj):
        if isinstance(obj, QAbstractSpinBox):
            return obj
        parent = obj.parent() if isinstance(obj, QWidget) else None
        return parent if isinstance(parent, QAbstractSpinBox) else None

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Polish and isinstance(obj, QAbstractSpinBox):
            obj.setFocusPolicy(Qt.StrongFocus)
        elif kind == QEvent.Wheel:
            spin = self._owning_spin(obj)
            # hasFocus() also covers the inner line edit (the spin box's
            # focus proxy), so a box being typed into keeps its wheel.
            if spin is not None and not spin.hasFocus():
                event.ignore()
                return True
        return False


_spinbox_wheel_guard = None


def install_spinbox_wheel_guard():
    """Installs the application-wide spin box wheel guard, once. Call it
    right after the QApplication is created, before any window is
    built, so every spin box gets polished through it."""
    global _spinbox_wheel_guard
    app = QApplication.instance()
    if app is None or _spinbox_wheel_guard is not None:
        return
    _spinbox_wheel_guard = _SpinBoxWheelGuard(app)
    app.installEventFilter(_spinbox_wheel_guard)


def install_wrapped_tooltips():
    """Installs the application-wide tooltip wrapping filter, once."""
    global _tooltip_filter
    app = QApplication.instance()
    if app is None or _tooltip_filter is not None:
        return
    _tooltip_filter = _WrappedToolTipFilter(app)
    app.installEventFilter(_tooltip_filter)


class _UnnaturalScrollFilter(QObject):
    """Application-wide event filter that reverses the mouse wheel's
    scroll direction everywhere in the app -- file lists, the Settings
    tab, any other scroll area or wheel-driven control (Settings tab ->
    Global Options -> "Unnatural scrolling").

    A QWheelEvent's own delta fields are read-only, so this can't just
    flip the one Qt already built: instead it swallows the original
    (accepting it and returning True, which marks it as filtered) and
    builds a negated replacement -- pixelDelta/angleDelta flipped, scroll
    phase normalized to NoScrollPhase (see the eventFilter body for why),
    everything else copied -- to deliver in its place.

    Where that replacement goes took two rounds to get right, confirmed
    empirically each time (throwaway PySide6 scripts, not just read from
    Qt's docs) rather than assumed:

    Round 1 -- obj isn't reliably the widget under the cursor. A real
    wheel tick often lands on whatever's directly under the mouse, which
    is usually some plain non-scrolling child -- a label, a checkbox, a
    spin box -- not the scroll area itself. Normally that child ignores
    it and Qt's own native dispatch walks the ignored event up through
    ancestors until one accepts it; that walk is native-dispatch
    machinery that only runs for events actually delivered by the
    platform, and QApplication.sendEvent does not reproduce it -- a
    sendEvent to that same child alone just dead-ends there, confirmed by
    sending a synthetic wheel event straight to a label buried in a
    QScrollArea with no filter involved at all: sendEvent returns True,
    but the event comes back unaccepted and nothing scrolls.

    Round 2 -- obj can be the top-level QWindow itself, not a widget at
    all. Confirmed via diagnostic prints on a real Windows run (PySide6
    6.11): hovering plain content delivered the wheel event to this
    filter with obj already a QWindow, before QWidgetWindow's own
    hit-testing had picked a specific child widget. Resending a
    synthetic event to that QWindow comes back "accepted" immediately
    without anything actually scrolling -- its internal hit-test-and-
    bubble cascade apparently doesn't reliably re-run for a manually
    constructed, non-native-dispatched event the way it does for a
    genuinely platform-delivered one. So the hit-testing needed doing
    here instead, with QApplication.widgetAt(global_pos) -- the same
    kind of lookup QWidgetWindow would do internally -- to find the
    actual widget under the cursor and start the walk there; obj itself
    is only a fallback for the rare case that returns nothing.

    Either way, the walk itself is: send the negated event to the
    current target widget, and if it comes back unaccepted, retry on
    target.parentWidget(), then its parent, and so on, until something
    accepts it or there's no parent left -- each hop remapped to that
    widget's own local coordinates (the global position is what stays
    meaningful across the walk). On a scrollbar or a tree view/viewport
    (both handle wheel input directly themselves) this still resolves in
    a single hop.

    _resending guards against re-catching and re-negating any of these
    sends (QApplication.sendEvent re-enters the whole filter chain for
    each one), which would otherwise flip them straight back and loop
    forever; other filters later in the chain (e.g. _SpinBoxWheelGuard)
    still see and can act on each hop normally, since only the negation
    itself is skipped while it's set.

    Accepting the *original* event before any of this matters too, and
    isn't just belt-and-braces: left unaccepted, Qt's own native walk
    (the same mechanism described above) would also re-offer the
    original to this filter again at each ancestor, on top of the
    negated walk this filter already does -- one extra pass goes
    unnoticed on a shallow hierarchy (a file list's tree + viewport), but
    was enough competing resends on the Settings tab's much deeper
    nesting to jam scrolling there completely before this was added.
    Accepting the original the moment this filter claims it stops that
    second walk after a single step, exactly as if a normal widget had
    accepted the event itself."""

    _MAX_HOPS = 50  # generous; real widget trees never come close

    def __init__(self, parent=None):
        super().__init__(parent)
        self._resending = False

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel or self._resending:
            return False
        global_pos = event.globalPosition()
        pixel_delta = event.pixelDelta() * -1
        angle_delta = event.angleDelta() * -1
        buttons, modifiers = event.buttons(), event.modifiers()
        inverted, source = event.inverted(), event.source()
        event.accept()
        self._resending = True
        try:
            # obj is not reliably the widget under the cursor -- on
            # PySide6 6.11 (confirmed via the DEBUG prints above, on a
            # real Windows run) a global filter sees the wheel event at
            # the top-level QWindow first, before QWidgetWindow's own
            # hit-testing has picked out a specific child widget yet.
            # Resending a synthetic event to that QWindow and hoping its
            # internal dispatch cascades correctly does not work
            # reliably (it comes back "accepted" immediately without
            # anything actually happening), presumably because that
            # internal cascade depends on native/spontaneous delivery in
            # ways a manually constructed QApplication.sendEvent doesn't
            # trigger. So the hit-testing is done here instead, with the
            # same call QWidgetWindow would use internally, and the walk
            # starts from the real widget it finds -- obj itself is only
            # a last-resort fallback, for the case this returns nothing
            # (already a QWidget, e.g. when a per-object filter forwards
            # a wheel event directly rather than it arriving fresh at
            # the window level).
            start = QApplication.widgetAt(global_pos.toPoint())
            target = start if start is not None else obj
            hops = 0
            while target is not None and hops < self._MAX_HOPS:
                hops += 1
                local_pos = QPointF(target.mapFromGlobal(global_pos.toPoint()))
                # Phase is deliberately normalized to NoScrollPhase on
                # every hop, not copied from the original: a touchpad's
                # ScrollBegin/Update/End sequence is tracked per widget
                # by Qt's own scroll-area/kinetic-scroll internals, and
                # each ancestor this walks to only ever sees this one
                # resent event, never the original's ScrollBegin -- an
                # orphaned mid-gesture phase on a widget that never saw
                # the gesture start could plausibly be dropped as
                # spurious. NoScrollPhase sidesteps that risk entirely by
                # presenting each hop as a complete, atomic wheel tick
                # (a plain mouse's wheel already reports events this way,
                # and this app's own scrollbar/tree handling doesn't
                # depend on phase continuity) -- the trade-off being any
                # smooth/kinetic per-pixel feel a touchpad's own phased
                # events might otherwise have driven.
                negated = QWheelEvent(
                    local_pos, global_pos, pixel_delta, angle_delta,
                    buttons, modifiers, Qt.NoScrollPhase, inverted, source,
                )
                # A freshly constructed QWheelEvent defaults to accepted
                # (confirmed empirically -- it isn't documented either
                # way). That default is wrong for what's being checked
                # below: a disabled widget's own event() handling never
                # actually runs its wheelEvent() override at all (disabled
                # widgets don't process input events), so it never calls
                # ignore() explicitly -- the event simply keeps its
                # untouched default of "accepted", and without this line
                # that default gets misread as "handled here", stopping
                # the walk one hop too early instead of continuing to the
                # parent the same way it would for a plain enabled widget
                # that doesn't want the event. Forcing it to ignored
                # first means only an explicit accept() from something
                # that actually handled it will stop the walk.
                negated.ignore()
                QApplication.sendEvent(target, negated)
                if negated.isAccepted():
                    break
                target = target.parentWidget() if hasattr(target, "parentWidget") else None
        except Exception:
            # Never fail silently: if something above still goes wrong
            # on a real system this sandbox can't fully replicate, this
            # at least surfaces it instead of just eating the wheel
            # tick with no visible symptom at all.
            import traceback
            traceback.print_exc()
        finally:
            self._resending = False
        return True


_unnatural_scroll_filter = None
_unnatural_scrolling_enabled = False


def set_unnatural_scrolling(enabled: bool):
    """Installs or removes the application-wide reversed-scroll filter
    (Settings tab -> Global Options -> "Unnatural scrolling"). Unlike
    install_spinbox_wheel_guard/install_wrapped_tooltips above, this one
    toggles at runtime rather than being installed once at startup, so
    it's safe -- and expected -- to call repeatedly either way."""
    global _unnatural_scroll_filter, _unnatural_scrolling_enabled
    _unnatural_scrolling_enabled = bool(enabled)
    app = QApplication.instance()
    if app is None:
        return
    if enabled:
        if _unnatural_scroll_filter is None:
            _unnatural_scroll_filter = _UnnaturalScrollFilter(app)
            app.installEventFilter(_unnatural_scroll_filter)
    elif _unnatural_scroll_filter is not None:
        app.removeEventFilter(_unnatural_scroll_filter)
        _unnatural_scroll_filter = None


def is_unnatural_scrolling_enabled() -> bool:
    """Current "Unnatural scrolling" state -- used to initialize the
    checkbox's checked state (including across _rebuild_ui) without a
    separate self._ flag, since the filter's install state already is
    that flag."""
    return _unnatural_scrolling_enabled


class CascadeFlowLayout(QLayout):
    """A short, ordered run of sub-blocks that belong together (e.g.
    Statistics View Colors' Graph/Info/Buttons, or File List View
    Colors' Default/Comparison tables): side by side in one row while
    there's room for all of them (see sizeHint -- the preferred size is
    that one full row); as the width available narrows, an item that no
    longer fits to the right of the previous column stacks directly
    below it INSTEAD of starting a whole new row at the block's own left
    edge -- a new column only starts when there's room for one to the
    right of the current rightmost column. Only once even a single
    column (every item stacked, at the width of the widest one -- see
    minimumSize) doesn't fit does this whole block hand back to its own
    container's FlowLayout, which then wraps the block onto its own
    line -- where it re-tries this same cascade at whatever width that
    line now has.

    Unlike FlowLayout, item order and grouping matters here (later
    items stack under whichever column has room for them), so this
    isn't a drop-in replacement for it -- see
    SettingsTabMixin._settings_subsection_cascade, used only for a
    couple of tightly-related same-topic sub-blocks, not a whole
    section's worth of independent ones."""

    def __init__(self, parent=None, h_spacing: int = 16, v_spacing: int = 12):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self):
        # The preferred size is everything in one row -- the widest
        # this block ever wants to be; used by the containing
        # FlowLayout to decide whether the whole block still fits next
        # to whatever precedes it.
        width = height = count = 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            width += hint.width()
            height = max(height, hint.height())
            count += 1
        if count > 1:
            width += self._h_spacing * (count - 1)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self):
        # The narrowest this block can ever be: every item in one
        # column, stacked -- width is its widest single item, height
        # the sum of all of them.
        width = height = 0
        for item in self._items:
            if item.isEmpty():
                continue
            size = item.minimumSize()
            width = max(width, size.width())
            height += size.height()
        if self.count():
            height += self._v_spacing * (self.count() - 1)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def _do_layout(self, rect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        columns = []  # [x, width, bottom_y] per column, left to right
        for item in self._items:
            if item.isEmpty():  # a hidden widget takes no room
                continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            if columns:
                last = columns[-1]
                new_x = last[0] + last[1] + self._h_spacing
                fits_new_column = new_x + size.width() <= area.right() + 1
            else:
                new_x, fits_new_column = area.x(), True
            if fits_new_column:
                # A new column, top-aligned with the whole block, to
                # the right of every column so far.
                if apply:
                    item.setGeometry(QRect(new_x, area.y(), size.width(), size.height()))
                columns.append([new_x, size.width(), area.y() + size.height()])
            else:
                # Doesn't fit beside the last column: stack below its
                # last item instead, in that same column.
                x, y = last[0], last[2] + self._v_spacing
                if apply:
                    item.setGeometry(QRect(x, y, size.width(), size.height()))
                last[1] = max(last[1], size.width())
                last[2] = y + size.height()
        bottom = max((c[2] for c in columns), default=area.y())
        return bottom - rect.y() + margins.bottom()


class FlowLayout(QLayout):
    """Lays its items out left to right, top-aligned, going onto a new
    line (or into free space further down) when the next one doesn't fit
    in the width it's given -- so blocks sit side by side when there's
    room and stack when there isn't, following the window as it is
    resized (it reports a height for a given width, which QVBoxLayout/
    QScrollArea use).

    Placement (see _pack): each item, in turn, goes where its bottom edge
    ends up highest -- ties to the higher, then the more leftward spot --
    either beside the items already placed, or down in the free space
    below a shorter one (e.g. a small block tucked in under a short block
    on the left, beside a tall one on its right, rather than on a new
    line below both). An item that can reflow (one holding a nested Flow/
    CascadeFlowLayout -- see _item_info) may be narrowed for that, down
    to its own minimum, which makes it taller.

    With `reorder` on, every order of the items is tried as well, and the
    lowest overall result wins, as long as nothing goes below its minimum
    width. What an order may NOT do is swap blocks that end up side by
    side at the same height: those always keep the original left-to-right
    order -- a block only comes before an earlier one by moving down,
    below something. Among equally low results: the original order (the
    fewest blocks moved out of it), then everything as far up and left as
    possible.

    Its preferred size is everything on one line and its minimum width
    that of its widest item, so it only ever pushes the page wider than
    the window for a single block that is itself too wide.

    The Settings tab uses one per section for that section's Level 2
    blocks (and one per Level 2 block that has Level 3 blocks) -- see
    SettingsTabMixin._settings_subsection_flow."""

    # Above this many items only the original order is tried (still with
    # the gap-filling placement) -- n! orders grow fast, and every
    # Settings-tab flow is well below it.
    _MAX_REORDER_ITEMS = 5

    def __init__(self, parent=None, h_spacing: int = 16, v_spacing: int = 0, reorder: bool = True):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self._reorder = reorder
        # width -> best arrangement, and (item index, width) -> height;
        # both only valid until the layout is next invalidated (an item
        # added/removed/shown/hidden, or a size hint somewhere inside
        # changing -- Qt propagates that up to here).
        self._arrange_cache = {}
        self._height_cache = {}
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def invalidate(self):
        self._arrange_cache = {}
        self._height_cache = {}
        super().invalidate()

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self):
        width = height = count = 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            width += hint.width()
            height = max(height, hint.height())
            count += 1
        if count > 1:
            width += self._h_spacing * (count - 1)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # ------------------------------------------------------------ arranging

    def _item_info(self):
        """(item index, preferred width, minimum width, reflows) for each
        visible item. An item that can reflow (one with a nested Flow/
        CascadeFlowLayout inside it -- Qt propagates hasHeightForWidth up
        through the QVBoxLayouts/QGroupBoxes in between) may be narrowed
        down to its own minimum; one that can't (a plain table/frame)
        always gets its preferred width."""
        info = []
        for index, item in enumerate(self._items):
            if item.isEmpty():  # a hidden widget takes no room
                continue
            hint = item.sizeHint().expandedTo(item.minimumSize())
            reflows = item.hasHeightForWidth()
            min_width = min(item.minimumSize().width(), hint.width()) if reflows else hint.width()
            info.append((index, hint.width(), min_width, reflows))
        return info

    def _item_height(self, entry, width: int) -> int:
        index, _pref, _min_width, reflows = entry
        if not reflows:
            item = self._items[index]
            return item.sizeHint().expandedTo(item.minimumSize()).height()
        key = (index, width)
        height = self._height_cache.get(key)
        if height is None:
            # heightForWidth rather than sizeHint().height(): for an item
            # with a hasHeightForWidth chain nested a couple of
            # QVBoxLayouts deep, Qt's own box-layout sizeHint() doesn't
            # always match what heightForWidth returns for the same
            # width -- heightForWidth is the one Qt keeps consistent.
            height = self._items[index].heightForWidth(width)
            self._height_cache[key] = height
        return height

    def _pack(self, entries, avail: int):
        """Places `entries`, in this order, into a content width of
        `avail` -- see the class docstring. Returns ([(entry, x, y, width,
        height)] relative to the content area's top-left, total height),
        or None if some item can't be placed anywhere without going below
        its minimum width."""
        hs, vs = self._h_spacing, self._v_spacing
        placed = []
        for entry in entries:
            _index, pref, min_width, reflows = entry
            best = None
            # Where it could start: the left edge, or just right of
            # something already placed (with the usual gap).
            for x in sorted({0} | {px + pw + hs for _e, px, _py, pw, _ph in placed}):
                # How wide: its preferred width, or -- if it can reflow --
                # narrowed to end at the content edge, or just short of
                # something already placed further right.
                widths = set()
                for bound in {avail} | {px - hs for _e, px, _py, _pw, _ph in placed if px > x}:
                    room = bound - x
                    if pref <= room:
                        widths.add(pref)
                    elif reflows and room >= min_width:
                        widths.add(room)
                if not widths and x == 0:
                    # Alone on a line and still too wide: one that can't
                    # reflow keeps its own width (overflowing, as a single
                    # too-wide block always has); one that can goes down
                    # to its minimum.
                    widths.add(pref if not reflows else min_width)
                for width in widths:
                    # Just below whatever it would otherwise overlap.
                    y = 0
                    for _e, px, py, pw, ph in placed:
                        if px < x + width + hs and x < px + pw + hs:
                            y = max(y, py + ph + vs)
                    height = self._item_height(entry, width)
                    key = (y + height, y, x, -width)
                    if best is None or key < best[0]:
                        best = (key, x, y, width, height)
            if best is None:
                return None
            _key, x, y, width, height = best
            placed.append((entry, x, y, width, height))
        total = max((py + ph for _e, _px, py, _pw, ph in placed), default=0)
        return placed, total

    @staticmethod
    def _same_row_order_kept(placed) -> bool:
        """True if every pair of items placed side by side at the same
        height is in its original left-to-right order."""
        for i, (a, ax, ay, _aw, _ah) in enumerate(placed):
            for b, bx, by, _bw, _bh in placed[i + 1:]:
                if ay == by and (ax < bx) != (a[0] < b[0]):
                    return False
        return True

    @staticmethod
    def _inversions(order) -> int:
        """How far `order` (item indexes) is from the original order."""
        return sum(1 for i in range(len(order)) for j in range(i + 1, len(order)) if order[i] > order[j])

    def _arrange(self, avail: int):
        """The lowest placement of the visible items for a content width
        of `avail`: ([(entry, x, y, width, height)], total height). Cached
        per width until the next invalidate()."""
        cached = self._arrange_cache.get(avail)
        if cached is not None:
            return cached
        from itertools import permutations
        entries = tuple(self._item_info())
        if self._reorder and 1 < len(entries) <= self._MAX_REORDER_ITEMS:
            orders = permutations(entries)
        else:
            orders = (entries,)
        best_key, best = None, ([], 0)
        for order in orders:
            result = self._pack(list(order), avail)
            if result is None:
                continue
            placed, height = result
            if order != entries and not self._same_row_order_kept(placed):
                continue
            key = (
                height,
                self._inversions([e[0] for e in order]),
                sum(py for _e, _px, py, _pw, _ph in placed),
                sum(px for _e, px, _py, _pw, _ph in placed),
            )
            if best_key is None or key < best_key:
                best_key, best = key, (placed, height)
        self._arrange_cache[avail] = best
        return best

    def _do_layout(self, rect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        placed, height = self._arrange(max(0, area.width()))
        if apply:
            for entry, x, y, width, item_h in placed:
                self._items[entry[0]].setGeometry(
                    QRect(QPoint(area.x() + x, area.y() + y), QSize(width, item_h))
                )
        return height + margins.top() + margins.bottom()
