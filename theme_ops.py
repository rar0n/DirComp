"""
ThemeOpsMixin: what the Settings tab's controls DO -- the color/theme
change handlers, palette application (_apply_theme_palette), the
per-theme color sets (_builtin_color_set/_capture_color_set/
_load_color_set_into_live), _refresh_color_dependent_widgets and
_reset_to_defaults.

Split out of ui_builder.py unchanged -- same methods, still reached as
`self._...` because UiBuilderMixin inherits this class.
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from constants import IS_DIR_ROLE, TAG_COLORS
from custom_widgets import (
    SELECTION_HIGHLIGHT_COLOR, _DEFAULT_MAIN_BUTTON_COLORS, _DEFAULT_SELECT_COLORS,
    _DEFAULT_SCROLLBAR_HANDLE_OPACITY_PCT, _DEFAULT_SCROLLBAR_TRACK_OPACITY_PCT,
    _INVERT_BUTTON_COLORS, _SCROLLBAR_COLORS,
    _SCROLLBAR_HANDLE_STYLES, _SCROLLBAR_STYLE, _SELECT_KEY_TO_TAG_COLOR_KEYS,
    _STATS_BUTTON_KEYS, _THEME_DEFAULT_MAIN_BUTTON_KEYS,
    _bevel_button_stylesheet, _press_blend,
)


# Tick mark settings (Settings -> File List View -> Indentation Line ->
# Line style): live attribute, color-set key (per theme, see
# _builtin_color_set/_capture_color_set/_load_color_set_into_live) and
# built-in default -- one table so the three of them can't drift apart.
# `_indent_tick_length` / "indent_tick_length" is the FILE tick's length
# (the name from when files were the only ticks, kept so settings files
# saved back then still load).
_INDENT_TICK_STATE = (
    ("_indent_tick_enabled", "indent_tick_enabled", False),
    ("_indent_tick_files", "indent_tick_files", True),
    ("_indent_tick_files_left", "indent_tick_files_left", False),
    ("_indent_tick_length", "indent_tick_length", 6),
    ("_indent_tick_folders", "indent_tick_folders", False),
    ("_indent_tick_folders_left", "indent_tick_folders_left", True),
    ("_indent_tick_folder_length", "indent_tick_folder_length", 6),
)

# Their Settings-tab controls (attribute names, see _build_settings_tab).
_INDENT_TICK_CHECKBOXES = (
    ("indent_tick_enabled_cb", "_indent_tick_enabled"),
    ("indent_tick_files_cb", "_indent_tick_files"),
    ("indent_tick_files_left_cb", "_indent_tick_files_left"),
    ("indent_tick_folders_cb", "_indent_tick_folders"),
    ("indent_tick_folders_left_cb", "_indent_tick_folders_left"),
)
_INDENT_TICK_SPINS = (
    ("indent_tick_length_spin", "_indent_tick_length"),
    ("indent_tick_folder_length_spin", "_indent_tick_folder_length"),
)

# Every theme this app knows about, and the handful of colors that
# differ theme-to-theme for things that AREN'T themselves a per-theme
# color-set entry (GUI window fg/bg -- _effective_gui_colors; the list/
# field background -- _default_base_color) plus the same handful of
# Statistics View/progress-bar/scrollbar tones _builtin_color_set pulls
# from here instead of hardcoding its own per-theme branches. Adding a
# theme is adding one entry here -- give it its own values, or copy an
# existing theme's dict and just change the background/text tones (the
# way "gloaming_grey" below reuses "dark"'s accent colors) if it's
# meant as a low-stakes neutral variant rather than a from-scratch
# look. Nothing else in this file, settings_ops.py, or settings_tab.py
# needs to change -- _THEMES right below is *derived* from this dict's
# keys, and both of those files import it from here, so its length/
# order/membership can never drift out of step with this table.
#
# folder_fg_override: the Comparison Type Colors table's Folder Fg
# override (None to leave every mode's default alone -- see
# _builtin_color_set). file_bin_override: File mode's Statistics View
# bin color override (None to keep the shared blue default).
_THEME_DEFAULTS = {
    "dark": {
        "gui_fg": "#F5F5F5", "gui_bg": "#3A3A3A", "base": "#2A2A2A",
        "stats_bg": "#1E1E20", "stats_hover": "#FFFFFF", "stats_text": "#D2D2D2",
        "stats_grid": "#3C3C3C", "selection_info": "#E68C14",
        "progress_bg": "#2A2A2A",
        "scrollbar_groove": "#1A1A1A", "scrollbar_handle": "#E8E8E8",
        "folder_fg_override": None, "file_bin_override": None,
    },
    "light": {
        "gui_fg": "#000000", "gui_bg": "#EFEFEF", "base": "#FFFFFF",
        "stats_bg": "#FFFFFF", "stats_hover": "#000000", "stats_text": "#303030",
        "stats_grid": "#D0D0D0", "selection_info": "#B35C00",
        "progress_bg": "#D0D0D0",
        "scrollbar_groove": "#D8D8D8", "scrollbar_handle": "#7A7A7A",
        "folder_fg_override": "blue", "file_bin_override": "#808080",
    },
    # A spare, low-stakes theme meant for experimenting in without
    # touching Dark or Light -- Dark's set (see _builtin_color_set)
    # with its near-black tones swapped for genuinely grey ones here;
    # its accent colors (selection orange, indent-line blue, ...) stay
    # Dark's, unchanged, since a neutral theme doesn't need its own
    # accent palette too, just its own backdrop. Every value below is
    # still a plain Settings-tab color like any other, so it's all
    # editable by hand from there afterward.
    "gloaming_grey": {
        "gui_fg": "#E4E4E6", "gui_bg": "#55555A", "base": "#404044",
        "stats_bg": "#3A3A3E", "stats_hover": "#F2F2F2", "stats_text": "#E4E4E6",
        "stats_grid": "#57575C", "selection_info": "#E68C14",
        "progress_bg": "#4A4A4E",
        "scrollbar_groove": "#4A4A4E", "scrollbar_handle": "#E8E8E8",
        "folder_fg_override": None, "file_bin_override": None,
    },
}
_THEMES = tuple(_THEME_DEFAULTS.keys())


class ThemeOpsMixin:

    def _recolor_all_items(self):
        """Re-applies each item's already-stored diff tag through
        _apply_tag (ListingMixin) -- picks up whatever just changed in
        TAG_COLORS, since an item's background/foreground brushes were
        set once at populate time and don't otherwise notice the shared
        dict they were built from changing later.

        With Dual Pane off, the right pane's last-loaded listing is
        still sitting there under the hood (just hidden -- see
        _on_dual_pane_changed), so a stored diff tag like "missing" or
        "newer" may really be describing a comparison against data
        that's no longer visibly on screen. Substituting the plain
        "file"/"directory" tag for display only (never touching the
        item's real stored tag) falls back to the same "nothing to
        diff against" look _update_selection_markers already treats a
        missing right-pane listing as, without losing what the item's
        real tag was for whenever Dual Pane comes back on."""
        for pane in (self.left, self.right):
            if not pane.tree:
                continue
            for item in self._iter_items(pane.tree):
                tag = item.data(0, Qt.UserRole)
                if not tag:
                    continue
                if not self._dual_pane_enabled and tag not in ("file", "directory"):
                    is_dir = item.data(0, IS_DIR_ROLE)
                    tag = "directory" if is_dir else "file"
                self._apply_tag(item, tag)

    def _repaint_all_scrollbars(self):
        for pane in (self.left, self.right):
            if pane.tree:
                pane.tree.verticalScrollBar().update()
            if pane.tl_scrollbar:
                pane.tl_scrollbar.update()

    def _refresh_color_swatches(self):
        """Re-paints every Settings-tab color swatch (see
        _make_color_swatch) from its live underlying value. Needed
        after anything that changes self._select_colors/TAG_COLORS/
        _SCROLLBAR_COLORS/... in bulk, directly, rather than through one
        of the _on_*_color_changed handlers a swatch click itself goes
        through -- load_settings and "Reload Defaults" are
        currently the only two."""
        for refresh in self._color_swatches:
            try:
                refresh()
            except RuntimeError:
                pass

    def _refresh_select_button_styles(self):
        """Re-applies every Select-group button's stylesheet, both
        panes, from self._select_colors -- the bulk-update counterpart
        to _on_select_color_changed's own per-button update, needed for
        the same reason _refresh_color_swatches is (load_settings/"Use
        Default Settings" change self._select_colors directly, without
        going through that handler)."""
        for key, (fg, bg) in self._select_colors.items():
            stylesheet = _bevel_button_stylesheet(bg, fg)
            for pane in (self.left, self.right):
                btn = pane.select_toggle_buttons.get(key) or pane.select_plain_buttons.get(key)
                if btn:
                    btn.setStyleSheet(stylesheet)

    def _refresh_color_dependent_widgets(self):
        """Every live-update call an individual color-swatch/theme
        handler makes for its own single change, all at once -- shared
        by _reset_to_defaults and settings_ops._apply_raw_settings_state,
        the two places that change color/theme state in bulk (without
        going through those per-color handlers, and therefore without
        their usual side effects) but don't need a full _rebuild_ui()
        (theme and custom-default-button-color changes still do -- see
        both callers' own needs_rebuild check)."""
        self._recolor_all_items()
        self._repaint_all_scrollbars()
        for pane in (self.left, self.right):
            if pane.invert_button:
                pane.invert_button.update()
            # The "Total items" breakdown label's per-type colors need
            # the same re-derive-from-self._select_colors treatment as
            # everything else here -- _on_select_color_changed already
            # does this for its own single-color case (see that
            # method); load_settings/"Reload Defaults" go through
            # this bulk path instead and were leaving the label's old
            # colors in place until something else happened to trigger
            # a recount.
            self.update_type_counts(pane)
            # Same reasoning, for tl_totals_label's own numeric-colored
            # "Files: X - Folders: Y" (see _numeric_span/Title Numeric
            # Colors) -- not covered by update_type_counts above, which
            # is total_items_label/count_label's own refresh.
            if getattr(pane, "tl_totals_label", None) is not None:
                self._update_timeline_totals_label(pane)
        stylesheet = self._status_progress_bar_stylesheet()
        self.left_progress_bar.setStyleSheet(stylesheet)
        self.right_progress_bar.setStyleSheet(stylesheet)
        self._refresh_select_button_styles()
        self._apply_stats_button_colors()
        self._apply_main_button_colors()
        # Each pane's status-line summary (Selected info/Unselected colors).
        self._apply_status_line_colors()
        self._refresh_color_swatches()
        self._apply_selection_highlight_color(self._selection_tint_color)
        self._apply_flv_background()
        # Selection opacity is a plain widget, not a swatch --
        # _refresh_color_swatches doesn't cover it, and the bulk color-
        # set load (load_settings, _reset_to_defaults) only ever sets
        # the self._selection_tint_opacity attribute underneath, never
        # the widget. blockSignals so setting it back doesn't re-fire
        # its own handler and redo the refresh this method is already
        # in the middle of.
        if getattr(self, "selection_tint_opacity_spin", None):
            self.selection_tint_opacity_spin.blockSignals(True)
            self.selection_tint_opacity_spin.setValue(self._selection_tint_opacity)
            self.selection_tint_opacity_spin.blockSignals(False)
        # Scrollbar handle opacity: same plain-widget reasoning (its
        # value lives in custom_widgets._SCROLLBAR_STYLE, which the bulk
        # color-set load just updated; the repaint is already done above
        # by _repaint_all_scrollbars).
        if getattr(self, "scrollbar_handle_opacity_spin", None):
            self.scrollbar_handle_opacity_spin.blockSignals(True)
            self.scrollbar_handle_opacity_spin.setValue(_SCROLLBAR_STYLE["handle_opacity"])
            self.scrollbar_handle_opacity_spin.blockSignals(False)
        # Scrollbar track opacity: same plain-widget reasoning as handle
        # opacity just above (its value lives in _SCROLLBAR_STYLE too).
        if getattr(self, "scrollbar_track_opacity_spin", None):
            self.scrollbar_track_opacity_spin.blockSignals(True)
            self.scrollbar_track_opacity_spin.setValue(_SCROLLBAR_STYLE["track_opacity"])
            self.scrollbar_track_opacity_spin.blockSignals(False)
        # Indentation Line Colors: color-1/color-2/hover swatches are
        # covered by _refresh_color_swatches above; steps/reverse/hover-
        # opacity are plain widgets, same reasoning as Selection
        # opacity's spinbox just above.
        if getattr(self, "indent_line_steps_spin", None):
            self.indent_line_steps_spin.blockSignals(True)
            self.indent_line_steps_spin.setValue(self._indent_line_steps)
            self.indent_line_steps_spin.blockSignals(False)
        if getattr(self, "indent_line_reverse_cb", None):
            self.indent_line_reverse_cb.blockSignals(True)
            self.indent_line_reverse_cb.setChecked(self._indent_line_reverse)
            self.indent_line_reverse_cb.blockSignals(False)
        self._sync_indent_steps_dependents()
        if getattr(self, "indent_hover_opacity_spin", None):
            self.indent_hover_opacity_spin.blockSignals(True)
            self.indent_hover_opacity_spin.setValue(self._indent_hover_opacity)
            self.indent_hover_opacity_spin.blockSignals(False)
        # File List View Indentation: plain spin box (not covered by
        # _refresh_color_swatches), and the trees themselves need the
        # reset value applied directly since signals are blocked here.
        if getattr(self, "tree_indentation_spin", None):
            self.tree_indentation_spin.blockSignals(True)
            self.tree_indentation_spin.setValue(self._tree_indentation)
            self.tree_indentation_spin.blockSignals(False)
        for pane in (self.left, self.right):
            if pane is not None and getattr(pane, "tree", None) is not None:
                pane.tree.setIndentation(self._tree_indentation)
        # Indentation Line: Enable/Line thickness (plain widgets, same
        # reasoning as the steps/opacity spin boxes above). The maximum
        # is set first, from the indentation just applied, so the value
        # isn't clamped against a stale limit.
        if getattr(self, "indent_lines_enabled_cb", None):
            self.indent_lines_enabled_cb.blockSignals(True)
            self.indent_lines_enabled_cb.setChecked(self._indent_lines_enabled)
            self.indent_lines_enabled_cb.blockSignals(False)
        if getattr(self, "indent_line_thickness_spin", None):
            self.indent_line_thickness_spin.blockSignals(True)
            self.indent_line_thickness_spin.setMaximum(max(1, self._tree_indentation - 1))
            self.indent_line_thickness_spin.setValue(self._indent_line_thickness)
            self._indent_line_thickness = self.indent_line_thickness_spin.value()
            self.indent_line_thickness_spin.blockSignals(False)
        # Line style radio buttons: same reasoning as the plain widgets
        # above (signals blocked, attribute is the source of truth).
        style_group = getattr(self, "indent_line_style_group", None)
        if style_group is not None:
            style_button = style_group.button(self._indent_line_style)
            if style_button is not None:
                style_group.blockSignals(True)
                style_button.setChecked(True)
                style_group.blockSignals(False)
        # Tick marks: same reasoning again (see _sync_indent_tick_widgets).
        self._sync_indent_tick_widgets()
        self._sync_indent_line_enabled_states()
        self._refresh_indent_line_display()
        # Statistics View Colors: swatches are covered by
        # _refresh_color_swatches above (every _make_color_swatch button
        # registers its own refresh); the two opacity spinboxes aren't
        # swatches, same reasoning as Selection opacity's
        # spinbox just above.
        for pane in (self.left, self.right):
            self._apply_stats_palette(pane)
        for mode, spin_attr, key in (
            ("file", "stats_file_selection_opacity_spin", "selection_opacity"),
            ("folder", "stats_folder_selection_opacity_spin", "selection_opacity"),
            ("file", "stats_file_hover_opacity_spin", "hover_opacity"),
            ("folder", "stats_folder_hover_opacity_spin", "hover_opacity"),
        ):
            spin = getattr(self, spin_attr, None)
            if spin is not None:
                spin.blockSignals(True)
                spin.setValue(self._stats_colors[mode][key])
                spin.blockSignals(False)

    def _on_select_color_changed(self, key: str, is_fg: bool, hex_color: str):
        fg, bg = self._select_colors[key]
        fg, bg = (hex_color, bg) if is_fg else (fg, hex_color)
        self._select_colors[key] = (fg, bg)
        stylesheet = _bevel_button_stylesheet(bg, fg)
        for pane in (self.left, self.right):
            btn = pane.select_toggle_buttons.get(key) or pane.select_plain_buttons.get(key)
            if btn:
                btn.setStyleSheet(stylesheet)

    def _on_summary_color_changed(self, key: str, hex_color: str):
        """Comparison Type Colors table -> Summary Text Color swatch."""
        self._summary_colors[key] = hex_color
        for pane in (self.left, self.right):
            if pane is not None:
                self._render_type_counts(pane)

    def _apply_selection_highlight_color(self, hex_color: str):
        """Sets the tree's own flat Highlight palette color -- what Qt
        itself fills a selected row with, at full strength. Selection
        opacity (below 100%) is applied on top of that by
        _SelectionColorDelegate (the cells) and DirDropTreeWidget.
        drawBranches (the indentation area), which each compute their
        own tint from the selection color and paint over it."""
        color = QColor(hex_color)
        for pane in (self.left, self.right):
            tree = pane.tree
            if not tree:
                continue
            palette = tree.palette()
            palette.setColor(QPalette.Active, QPalette.Highlight, color)
            palette.setColor(QPalette.Inactive, QPalette.Highlight, color)
            tree.setPalette(palette)
        self._refresh_selection_highlighting()

    def _refresh_selection_highlighting(self):
        """Repaints every already-visible selected row (so
        _SelectionColorDelegate re-derives its per-row tint from
        whatever just changed) and recomputes the scrollbar's selection
        markers -- needed after either of Default Selection
        Color/Selection opacity change, since none of those are
        re-read until the next paint on their own."""
        for pane in (self.left, self.right):
            if pane.tree:
                pane.tree.viewport().update()
            self._schedule_selection_markers_update(pane)

    def _on_selection_tint_color_changed(self, hex_color):
        self._selection_tint_color = hex_color
        self._apply_selection_highlight_color(hex_color)
        # Defensive: whatever that does to the trees' palettes, the
        # background set below it has to survive.
        self._apply_flv_background()

    @staticmethod
    def _default_flv_font_pt() -> int:
        size = QApplication.font().pointSize()
        return size if size > 0 else 10

    def _on_flv_font_size_changed(self, value):
        self._apply_flv_font(value)

    def _apply_flv_font(self, point_size):
        """Point size of both file lists' text (rows and, since the
        header inherits from its tree, the column headers). Qt relays the
        rows out itself on a font change."""
        for pane in (self.left, self.right):
            tree = getattr(pane, "tree", None)
            if tree is None:
                continue
            font = QFont(tree.font())
            font.setPointSize(int(point_size))
            tree.setFont(font)

    def _default_base_color(self, theme: str) -> QColor:
        """The theme's list/text-field background (the palette's Base):
        the built-in fixed color while the GUI colors are the theme's own,
        otherwise derived from the chosen window color."""
        _text_hex, window_hex = self._effective_gui_colors(theme)
        window = QColor(window_hex)
        if getattr(self, "_gui_colors", {}).get(theme) is None:
            return QColor(_THEME_DEFAULTS.get(theme, _THEME_DEFAULTS["dark"])["base"])
        # Custom GUI colors: the list/text-field background (Base)
        # follows the chosen window background the way the two
        # built-in themes already relate to each other -- a shade
        # darker than the window on a dark one, lighter on a light
        # one -- rather than staying at the built-in theme's own
        # fixed color, which could leave the file lists' text
        # unreadable against a very different window color.
        return window.darker(138) if window.lightness() < 128 else window.lighter(107)

    def _effective_flv_bg(self, theme: str | None = None) -> str:
        """Hex of the file lists' background: the explicitly chosen
        Default Background if there is one, else the theme's list
        background."""
        return self._flv_bg_color or self._default_base_color(theme or self._theme).name()

    def _on_flv_bg_color_changed(self, hex_color):
        self._flv_bg_color = hex_color
        self._apply_flv_background()

    def _apply_flv_background(self):
        color = QColor(self._effective_flv_bg())
        for pane in (self.left, self.right):
            tree = getattr(pane, "tree", None)
            if tree is None:
                continue
            palette = tree.palette()
            palette.setColor(QPalette.Base, color)
            tree.setPalette(palette)
            tree.viewport().update()

    def _on_selection_tint_opacity_changed(self, value):
        self._selection_tint_opacity = int(value)
        self._refresh_selection_highlighting()

    def _refresh_indent_line_display(self):
        """Repaints both trees' indentation guide lines -- needed after
        any of the Indentation Line Colors settings (steps, either
        color, direction, hover color/opacity) change, since
        DirDropTreeWidget.drawBranches only re-reads its color
        provider at paint time, not on its own when that state changes
        elsewhere. Mirrors _refresh_selection_highlighting's own
        viewport().update() pattern."""
        for pane in (self.left, self.right):
            if pane.tree:
                pane.tree.viewport().update()

    def _on_indent_lines_enabled_changed(self, checked):
        self._indent_lines_enabled = bool(checked)
        self._sync_indent_line_enabled_states()
        self._refresh_indent_line_display()

    def _sync_indent_line_enabled_states(self):
        """Everything in the Indentation Line group below Enable (the
        indentation, thickness and the whole Line colors block, titles
        included) is only available while the lines are enabled.
        Greyed by the palette's Disabled colors (see
        _apply_theme_palette) and the swatches' own :disabled style
        (see _make_color_swatch)."""
        for widget in getattr(self, "_indent_line_dependents", ()):
            widget.setEnabled(self._indent_lines_enabled)

    def _on_indent_line_style_changed(self, style_id):
        self._indent_line_style = int(style_id)
        self._refresh_indent_line_display()

    def _ensure_indent_tick_state(self):
        """Gives every tick mark attribute its built-in default if it
        doesn't exist yet (the ones added after the first tick option
        aren't initialized anywhere else)."""
        for attr, _key, default in _INDENT_TICK_STATE:
            if not hasattr(self, attr):
                setattr(self, attr, default)

    def _indent_tick_settings(self):
        """The tick mark settings as a tuple (enabled, files,
        files_left, file_length, folders, folders_left, folder_length)
        -- the tail of the tree's color provider (see
        PaneBuilderMixin._build_directory_widgets), read on every
        paint, so it only reads (defaults for any attribute that
        doesn't exist yet)."""
        return tuple(getattr(self, attr, default) for attr, _key, default in _INDENT_TICK_STATE)

    def _on_indent_tick_option_changed(self, attr, value):
        """Any one tick mark option (a checkbox or a length) changed;
        `attr` is its live attribute (see _INDENT_TICK_STATE)."""
        setattr(self, attr, value)
        self._sync_indent_tick_enabled_states()
        self._refresh_indent_line_display()

    def _sync_indent_tick_enabled_states(self):
        """Which tick controls are available: everything below Enable
        Tick Marks needs it on; each type's Left side, length spin box
        and slider (and its title) need that type's own checkbox on as
        well. Set explicitly on each control, so they keep their own
        state when the whole Line style block is re-enabled by the
        Indentation Line's Enable checkbox (see
        _sync_indent_line_enabled_states)."""
        master = bool(self._indent_tick_enabled)
        for kind, on_attr in (("files", "_indent_tick_files"),
                              ("folders", "_indent_tick_folders")):
            type_on = getattr(self, on_attr)
            for name, enabled in (
                (f"indent_tick_{kind}_cb", master),
                (f"indent_tick_{kind}_left_cb", master and type_on),
                (f"indent_tick_{kind}_length_label", master and type_on),
                (f"indent_tick_{'length' if kind == 'files' else 'folder_length'}_spin",
                 master and type_on),
            ):
                widget = getattr(self, name, None)
                if widget is not None:
                    widget.setEnabled(enabled)

    def _sync_indent_tick_widgets(self):
        """Puts every tick mark control back to the live attributes
        (signals blocked, so nothing re-fires) -- for a reset/load that
        changed the attributes directly. The length spin boxes' maximum
        follows the indentation just applied (as for the thickness), so
        it is set before the value."""
        self._ensure_indent_tick_state()
        for name, attr in _INDENT_TICK_CHECKBOXES:
            box = getattr(self, name, None)
            if box is not None:
                box.blockSignals(True)
                box.setChecked(getattr(self, attr))
                box.blockSignals(False)
        for name, attr in _INDENT_TICK_SPINS:
            spin = getattr(self, name, None)
            if spin is not None:
                spin.blockSignals(True)
                spin.setMaximum(max(1, self._tree_indentation - 1))
                spin.setValue(getattr(self, attr))
                setattr(self, attr, spin.value())
                spin.blockSignals(False)
        self._sync_indent_tick_enabled_states()

    def _on_indent_line_thickness_changed(self, value):
        self._indent_line_thickness = int(value)
        self._refresh_indent_line_display()

    def _sync_indent_thickness_limit(self):
        """Line thickness may be at most one less than the tree's
        indentation. Qt clamps the spin box's value itself when the
        maximum drops below it, which fires the thickness handler and
        so keeps the attribute in step."""
        spin = getattr(self, "indent_line_thickness_spin", None)
        if spin:
            spin.setMaximum(max(1, self._tree_indentation - 1))
        # The tick lengths share that ceiling (_tick_rects trims them
        # further where a shifted line style leaves less room).
        for name, _attr in _INDENT_TICK_SPINS:
            tick_spin = getattr(self, name, None)
            if tick_spin:
                tick_spin.setMaximum(max(1, self._tree_indentation - 1))

    def _on_indent_line_steps_changed(self, value):
        self._indent_line_steps = int(value)
        self._sync_indent_steps_dependents()
        self._refresh_indent_line_display()

    def _sync_indent_steps_dependents(self):
        """With Nr. of Steps at 1 every line uses Color 1 (see
        _indent_wheel_color's own steps<=1 shortcut), so Color 2, Swap
        and Reverse Color Wheel Direction have nothing to act on -- they
        are only available from 2 steps up. (Disabled explicitly, so
        they stay disabled when the whole Line colors block is
        re-enabled by the Enable checkbox.)"""
        enabled = self._indent_line_steps > 1
        for name in (
            "indent_line_color2_label", "indent_line_color2_swatch",
            "indent_line_swap_button", "indent_line_reverse_cb",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _on_indent_line_color1_changed(self, hex_color):
        self._indent_line_color1 = hex_color
        self._refresh_indent_line_display()

    def _on_indent_line_color2_changed(self, hex_color):
        self._indent_line_color2 = hex_color
        self._refresh_indent_line_display()

    def _on_indent_line_colors_swap(self):
        """Swap Color 1/Color 2 *and* flip Reverse Color Wheel Direction.
        Swapping the colors alone switches the sweep to the opposite arc
        of the wheel (short way <-> long way round), since the sweep
        direction stays fixed while its start/end trade places (see
        custom_widgets._indent_wheel_color). Flipping the direction too
        cancels that out: the range then covers exactly the same hues as
        before, just from the other end -- the deepest guide line gets
        what the shallowest one had, and vice versa.

        The checkbox is updated with signals blocked and the attribute
        set directly, so there's one refresh at the end rather than a
        second one from _on_indent_line_reverse_changed; the checkbox's
        own state is what Save Settings persists (see settings_ops)."""
        self._indent_line_color1, self._indent_line_color2 = (
            self._indent_line_color2, self._indent_line_color1,
        )
        self._indent_line_reverse = not self._indent_line_reverse
        if getattr(self, "indent_line_reverse_cb", None):
            self.indent_line_reverse_cb.blockSignals(True)
            self.indent_line_reverse_cb.setChecked(self._indent_line_reverse)
            self.indent_line_reverse_cb.blockSignals(False)
        # Both swatches read their color via a get_hex callable rather
        # than a value baked in at construction (see _make_color_swatch),
        # so refreshing every registered swatch is what actually shows
        # the swap -- same bulk-refresh already used after Load
        # Settings/Reload Defaults.
        self._refresh_color_swatches()
        self._refresh_indent_line_display()

    def _on_indent_line_reverse_changed(self, checked):
        self._indent_line_reverse = bool(checked)
        self._refresh_indent_line_display()

    def _on_indent_hover_color_changed(self, hex_color):
        self._indent_hover_color = hex_color
        self._refresh_indent_line_display()

    def _on_indent_hover_opacity_changed(self, value):
        self._indent_hover_opacity = int(value)
        self._refresh_indent_line_display()

    def _on_tree_indentation_changed(self, value):
        """Settings tab -> File List View -> Indentation: apply the new
        per-level indent to both panes' trees right away (setIndentation
        re-lays out and repaints them itself; the guide lines and their
        hover hit-testing read self.indentation() live -- see
        DirDropTreeWidget)."""
        self._tree_indentation = int(value)
        for pane in (self.left, self.right):
            if pane is not None and getattr(pane, "tree", None) is not None:
                pane.tree.setIndentation(self._tree_indentation)
        self._sync_indent_thickness_limit()

    def _on_stats_color_changed(self, mode: str, key: str, hex_color: str):
        self._stats_colors[mode][key] = hex_color
        # Only the pane(s) actually plotting `mode` right now need a live
        # repaint -- the other mode's palette is stored either way, just
        # not pushed onto a timeline until that mode becomes active (see
        # _on_timeline_mode_changed).
        want_folder = mode == "folder"
        for pane in (self.left, self.right):
            if pane.tl_folder_mode == want_folder:
                self._apply_stats_palette(pane)

    def _on_stats_opacity_changed(self, mode: str, value, key: str = "selection_opacity"):
        """`key` is which opacity: "selection_opacity" (Ranged Opacity,
        the drag-range fill) or "hover_opacity" (Hover Opacity)."""
        self._stats_colors[mode][key] = int(value)
        want_folder = mode == "folder"
        for pane in (self.left, self.right):
            if pane.tl_folder_mode == want_folder:
                self._apply_stats_palette(pane)

    def _tag_color_hex(self, tag_key: str, is_fg: bool) -> str:
        """Live Fg or Bg hex of one constants.TAG_COLORS entry (stored
        (bg, fg)) for a Comparison Type Colors swatch -- falls back to
        black/white only if an entry somehow has no color at all, so a
        swatch never gets an invalid stylesheet color."""
        bg, fg = TAG_COLORS.get(tag_key, (None, None))
        return (fg if is_fg else bg) or ("#000000" if is_fg else "#FFFFFF")

    def _on_comparison_color_changed(self, tag_key: str, is_fg: bool, hex_color: str):
        """Comparison Type Colors table: one file OR folder Fg/Bg cell
        -> that single TAG_COLORS entry, then recolor every row already
        on screen. The scrollbar's selection markers take their color
        from the same entries (see _update_selection_markers), so those
        are refreshed too."""
        bg, fg = TAG_COLORS.get(tag_key, (None, None))
        bg, fg = (bg, hex_color) if is_fg else (hex_color, fg)
        TAG_COLORS[tag_key] = (bg, fg)
        self._recolor_all_items()
        for pane in (self.left, self.right):
            if pane is not None and pane.tree:
                self._schedule_selection_markers_update(pane)

    def _on_folder_color_changed(self, is_fg: bool, hex_color: str):
        fg, bg = self._folder_color
        fg, bg = (hex_color, bg) if is_fg else (fg, hex_color)
        self._folder_color = (fg, bg)
        TAG_COLORS["directory"] = (bg, fg)
        self._recolor_all_items()

    def _on_default_file_color_changed(self, is_fg: bool, hex_color: str):
        bg, fg = TAG_COLORS.get("file", (None, None))
        bg, fg = (bg, hex_color) if is_fg else (hex_color, fg)
        TAG_COLORS["file"] = (bg, fg)
        self._recolor_all_items()

    def _on_invert_button_color_changed(self, side: str, hex_color: str):
        _INVERT_BUTTON_COLORS[side] = hex_color
        for pane in (self.left, self.right):
            if pane.invert_button:
                pane.invert_button.update()

    def _on_default_button_color_changed(self, is_fg: bool, hex_color: str):
        bg, fg = self._custom_default_button_colors or self._default_button_colors()
        bg, fg = (bg, hex_color) if is_fg else (hex_color, fg)
        self._custom_default_button_colors = (bg, fg)
        # Which buttons actually use _default_button_colors() is spread
        # across many _build_* methods rather than tracked in one place
        # (unlike the Select-group registry above), so a full rebuild is
        # the reliable way to get every one of them re-styled -- same
        # approach _on_symmetrical_buttons_changed/_on_dual_pane_changed
        # already use for their own app-wide effects.
        self._rebuild_ui()

    def _on_scrollbar_color_changed(self, role: str, hex_color: str):
        _SCROLLBAR_COLORS[role] = hex_color
        self._repaint_all_scrollbars()

    def _on_scrollbar_handle_opacity_changed(self, value):
        _SCROLLBAR_STYLE["handle_opacity"] = int(value)
        self._repaint_all_scrollbars()

    def _on_scrollbar_track_opacity_changed(self, value):
        _SCROLLBAR_STYLE["track_opacity"] = int(value)
        self._repaint_all_scrollbars()

    def _on_scrollbar_handle_style_toggled(self, style_id: int, checked: bool):
        """Settings tab -> Global Options -> "Scrollbar handle style"
        radio group (QButtonGroup.idToggled: fires for the button that
        just got checked AND the one that just got unchecked, so only
        the former is acted on). style_id indexes _SCROLLBAR_HANDLE_STYLES.
        Not a per-theme setting, so it lives in _SCROLLBAR_STYLE but not
        in a color set (see _capture_color_set)."""
        if not checked or not 0 <= style_id < len(_SCROLLBAR_HANDLE_STYLES):
            return
        _SCROLLBAR_STYLE["handle_style"] = _SCROLLBAR_HANDLE_STYLES[style_id]
        self._repaint_all_scrollbars()

    def _on_progress_bar_color_changed(self, role: str, hex_color: str):
        self._progress_bar_colors[role] = hex_color
        stylesheet = self._status_progress_bar_stylesheet()
        self.left_progress_bar.setStyleSheet(stylesheet)
        self.right_progress_bar.setStyleSheet(stylesheet)

    def _on_title_numeric_color_changed(self, hex_color: str):
        self._title_numeric_color = hex_color
        self._refresh_title_numeric_labels()

    def _on_status_selected_color_changed(self, hex_color: str):
        """Settings tab -> Misc Colors -> "Status line Selected info"."""
        self._status_selected_color = hex_color
        self._apply_status_line_colors()

    def _on_status_unselected_color_changed(self, hex_color: str):
        """Settings tab -> Misc Colors -> "Status line Unselected"."""
        self._status_unselected_color = hex_color
        self._apply_status_line_colors()

    def _on_history_color_changed(self, snapshot: bool, hex_color: str):
        """Settings tab -> Misc Colors -> "History: live folder" /
        "History: snapshot file": text color of that kind of entry in the
        folder-history drop-downs."""
        if snapshot:
            self._history_snapshot_color = hex_color
        else:
            self._history_live_color = hex_color
        for pane in (self.left, self.right):
            self._refresh_history_combo(pane)

    def _on_title_numeric_bold_changed(self, checked: bool):
        self._title_numeric_bold = checked
        self._refresh_title_numeric_labels()

    def _refresh_title_numeric_labels(self):
        """Re-render every label _numeric_span() feeds into (see Settings
        tab -> Misc Colors -> "Title Numeric Colors"), for both
        panes -- update_type_counts/_update_timeline_totals_label
        rebuild each label's whole rich-text string from scratch anyway
        (there's no isolated "just the span" to patch in place), so
        just calling them again is simplest, same as _recolor_all_items
        does for the full tree recolor case. Guarded with getattr since
        a Settings-tab color change can in principle fire before both
        panes' widgets exist (very early load_settings call)."""
        for pane in (self.left, self.right):
            if pane is None:
                continue
            if getattr(pane, "total_items_label", None) is not None:
                self.update_type_counts(pane)
            if getattr(pane, "tl_totals_label", None) is not None:
                self._update_timeline_totals_label(pane)

    def _on_theme_changed(self, theme: str):
        if theme == self._theme:
            return
        # Park the outgoing theme's colors (including anything edited
        # since the last save) and bring in the incoming theme's set
        # before the rebuild, which reads everything from the live
        # attributes.
        self._color_sets[self._theme] = self._capture_color_set()
        self._theme = theme
        self._load_color_set_into_live(self._color_sets[theme])
        self._apply_theme_palette(theme)
        self._rebuild_ui()
        # The rebuild recreated the per-theme widgets (carrying over the
        # outgoing theme's values); now the incoming theme's go on top.
        self._apply_theme_widget_values(self._color_sets[theme].get("widgets", {}))

    def _apply_theme_palette(self, theme: str):
        """The actual dark/light QPalette construction -- factored out
        of _on_theme_changed so load_settings/_reset_to_defaults can
        apply a theme without going through that method's early-return-
        if-unchanged guard (loading a saved theme that happens to match
        self._theme's current default still needs the palette applied,
        since nothing else would have applied it yet this session).

        Forces the "Fusion" style the first time this runs. Fusion is
        the one Qt style that actually paints every widget from the
        QPalette we're about to set -- the platform-native styles
        (windowsvista/windows11 on Windows, macOS's own) largely ignore
        QPalette for their own chrome (tab pages, group boxes, etc.) and
        draw from the OS's own light/dark setting instead, which is why
        a from-scratch Windows run stayed visibly light-themed after
        switching to Dark here even though every explicitly-
        stylesheeted button (_bevel_button_stylesheet, which doesn't
        depend on QPalette at all) themed correctly. Only set once --
        QApplication.setStyle() tears down and rebuilds every widget's
        style object, which isn't needed on every subsequent theme
        toggle and would just be wasted work.

        Also stashes the QColor this call just built as
        self._theme_window_color, read directly by
        _default_button_colors/_cancel_button_stylesheet instead of
        self.palette().color(QPalette.Window): QApplication.setPalette
        propagates to top-level widgets asynchronously, so immediately
        rebuilding buttons off self.palette() (as _on_theme_changed does
        right after calling this) could still see the *previous*
        theme's color -- which is exactly why button colors used to lag
        one theme-toggle behind, catching up only on the next switch."""
        app = QApplication.instance()
        if not getattr(self, "_fusion_style_applied", False):
            app.setStyle("Fusion")
            self._fusion_style_applied = True
        palette = QPalette()
        text_hex, window_hex = self._effective_gui_colors(theme)
        window = QColor(window_hex)
        text = QColor(text_hex)
        base = self._default_base_color(theme)
        palette.setColor(QPalette.Window, window)
        palette.setColor(QPalette.WindowText, text)
        palette.setColor(QPalette.Base, base)
        palette.setColor(QPalette.Text, text)
        palette.setColor(QPalette.Button, window)
        palette.setColor(QPalette.ButtonText, text)
        # setColor(role, color) above applies to all three color groups,
        # Disabled included -- so a disabled label/checkbox/spin box
        # looked exactly like an enabled one. Text roles in the Disabled
        # group get their own washed-out color (the text color, mostly
        # faded into the window color).
        disabled_text = _press_blend(text.name(), window.name(), 0.55)
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, disabled_text)
        app.setPalette(palette)
        self._theme_window_color = window
        # By the actual window color rather than the theme name, so a
        # custom light-on-dark or dark-on-light GUI color still gets the
        # matching title bar.
        self._sync_windows_titlebar_theme("dark" if window.lightness() < 128 else "light")

    # ------------------------------------------------ per-theme color sets

    def _builtin_color_set(self, theme: str) -> dict:
        """The program's own default for every color setting that
        belongs to a theme -- built from _THEME_DEFAULTS[theme] above
        for the handful of values that vary theme-to-theme, so adding a
        theme only means adding an entry there, not touching this
        method. Everything else here (the Select/button/comparison
        colors, indent-line/title accents, ...) is a pastel/fixed
        design color that reads fine on any theme, so it's the same
        regardless of which theme this is building a set for. Same
        JSON-friendly shape _capture_color_set produces, so a set is
        just a dict either way."""
        tag_colors = {
            "directory": ["#E0E090", "blue"], "file": [None, None], "error": ["#FFB6C1", "red"],
            "unhashed_file": ["#C8C8C8", "black"], "unhashed_dir": ["#B0B0B0", "blue"],
        }
        for key, tag_keys in _SELECT_KEY_TO_TAG_COLOR_KEYS.items():
            fg, bg = _DEFAULT_SELECT_COLORS[key]
            for tag_key in tag_keys:
                tag_colors[tag_key] = [bg, fg]
        defaults = _THEME_DEFAULTS.get(theme, _THEME_DEFAULTS["dark"])
        if defaults["folder_fg_override"]:
            # Text on the folder rows, for every comparison type (the
            # Comparison Type Colors table's Folder Fg column) -- the
            # Select buttons' own colors stay as they are.
            for tag_keys in _SELECT_KEY_TO_TAG_COLOR_KEYS.values():
                tag_colors[tag_keys[-1]][1] = defaults["folder_fg_override"]
            tag_colors["unhashed_dir"][1] = defaults["folder_fg_override"]
        stats_palette = {
            "background_color": defaults["stats_bg"],
            "selection_color": "#E68C14",
            # Hover tint (color + opacity) live in this same dict, so
            # they're saved/merged/loaded with the rest of stats_colors
            # -- see _capture_color_set/_merged_color_set, which only
            # split selection_opacity out of it.
            "hover_color": defaults["stats_hover"],
            "hover_opacity": 35,
            # Upper-left readout, split into its three segments -- see
            # TimelineWidget._draw_readout_segments -- each with its own
            # color; the same text tone every plain axis/readout label
            # uses by default.
            "bin_info_color": defaults["stats_text"],
            "stripped_info_color": defaults["stats_text"],
            "bin_color": "#4682DC",
            "grid_color": defaults["stats_grid"],
            "axis_label_color": defaults["stats_text"],
            "hover_label_color": defaults["stats_text"],
            "selection_info_color": defaults["selection_info"],
        }
        main_button_colors = {k: list(v) for k, v in _DEFAULT_MAIN_BUTTON_COLORS.items()}
        # Rename, Copy -->/Move -->, Refresh and the File List View's own
        # buttons: None = the theme's default button colors until picked.
        for key in _THEME_DEFAULT_MAIN_BUTTON_KEYS:
            main_button_colors[key] = None
        stats_colors = {"file": dict(stats_palette), "folder": dict(stats_palette)}
        if defaults["file_bin_override"]:
            # File mode's bins (Folder mode keeps the shared blue ones).
            stats_colors["file"]["bin_color"] = defaults["file_bin_override"]
        return {
            "select_colors": {k: list(v) for k, v in _DEFAULT_SELECT_COLORS.items()},
            "summary_colors": {
                k: _DEFAULT_SELECT_COLORS[k][1] for k in _SELECT_KEY_TO_TAG_COLOR_KEYS
            },
            "folder_color": ["blue", "#E0E090"],
            "progress_bar_colors": {
                "fg": "#4A90D9", "bg": defaults["progress_bg"], "busy": "#E0A030",
            },
            "scrollbar_colors": {
                "groove": defaults["scrollbar_groove"], "handle": defaults["scrollbar_handle"],
            },
            "invert_button_colors": {"dark": "#000000", "light": "#FFFFFF"},
            "custom_default_button_colors": None,
            "tag_colors": tag_colors,
            "selection_tint_color": SELECTION_HIGHLIGHT_COLOR,
            "flv_bg_color": None,
            "title_numeric_color": "#C05A00" if theme == "light" else "#E68C14",
            # Status-line summary colors (Misc Colors): the yellow that used
            # to be hardcoded for "something selected"; None = no color of
            # its own for "nothing selected", i.e. the GUI text color.
            "status_selected_color": "#F5D400",
            "status_unselected_color": None,
            # Folder-history drop-down text (Misc Colors): live folders
            # and snapshot (.fss) files. Fg only.
            "history_live_color": "#000000" if theme == "light" else "#FFFFFF",
            "history_snapshot_color": "#707070" if theme == "light" else "#C8C8C8",
            "indent_line_color1": "#909090" if theme == "light" else "#A0A0A0",
            "indent_line_color2": "#2E6BC6" if theme == "light" else "#4682DC",
            "indent_hover_color": "#000000" if theme == "light" else SELECTION_HIGHLIGHT_COLOR,
            "stats_colors": stats_colors,
            "stats_button_colors": {key: None for key in _STATS_BUTTON_KEYS},
            "main_button_colors": main_button_colors,
            # Opacities and the indentation-line options are per theme
            # too (same defaults for both) -- see _capture_color_set.
            "selection_tint_opacity": 50,
            "scrollbar_handle_opacity": _DEFAULT_SCROLLBAR_HANDLE_OPACITY_PCT,
            "scrollbar_track_opacity": _DEFAULT_SCROLLBAR_TRACK_OPACITY_PCT,
            "stats_selection_opacity": {"file": 35, "folder": 35},
            "indent_line_steps": 1,
            "indent_line_reverse": False,
            "indent_hover_opacity": 50,
            "indent_lines_enabled": True,
            "indent_line_thickness": 1,
            "indent_line_style": 0,
            **{key: default for _attr, key, default in _INDENT_TICK_STATE},
            # Every other per-theme setting that lives in a widget
            # (see settings_ops._per_theme_widgets), at its built-in
            # default -- empty only during the very first build, until
            # _build_ui has captured them.
            "widgets": dict(getattr(self, "_widget_defaults", {})),
        }

    def _capture_color_set(self) -> dict:
        """The live color state (whichever theme is active) as a set --
        copies throughout, so later edits to the live attributes never
        leak into the stored set. Beyond the colors themselves, a set
        also carries the per-theme opacities (selection tint,
        scrollbar handle, scrollbar track, Statistics View selection,
        indentation-line hover), the
        indentation-line options (enable, thickness, line style, tick
        marks, steps, reverse direction) and, under "widgets", the value of every other
        Settings-tab control that isn't a Global Option (see
        settings_ops._SHARED_WIDGET_KEYS) -- those are applied by
        _apply_theme_widget_values once the UI they live in exists."""
        return {
            "select_colors": {k: list(v) for k, v in self._select_colors.items()},
            "summary_colors": dict(self._summary_colors),
            "folder_color": list(self._folder_color),
            "progress_bar_colors": dict(self._progress_bar_colors),
            "scrollbar_colors": dict(_SCROLLBAR_COLORS),
            "invert_button_colors": dict(_INVERT_BUTTON_COLORS),
            "custom_default_button_colors": (
                list(self._custom_default_button_colors) if self._custom_default_button_colors else None
            ),
            "tag_colors": {k: list(v) for k, v in TAG_COLORS.items()},
            "selection_tint_color": self._selection_tint_color,
            "flv_bg_color": self._flv_bg_color,
            "title_numeric_color": self._title_numeric_color,
            "status_selected_color": self._status_selected_color,
            "status_unselected_color": self._status_unselected_color,
            "history_live_color": self._history_live_color,
            "history_snapshot_color": self._history_snapshot_color,
            "indent_line_color1": self._indent_line_color1,
            "indent_line_color2": self._indent_line_color2,
            "indent_hover_color": self._indent_hover_color,
            "stats_colors": {
                mode: {k: v for k, v in colors.items() if k != "selection_opacity"}
                for mode, colors in self._stats_colors.items()
            },
            "stats_button_colors": {
                k: (list(v) if v else None) for k, v in self._stats_button_colors.items()
            },
            "main_button_colors": {
                k: (list(v) if v else None) for k, v in self._main_button_colors.items()
            },
            "selection_tint_opacity": self._selection_tint_opacity,
            "scrollbar_handle_opacity": _SCROLLBAR_STYLE["handle_opacity"],
            "scrollbar_track_opacity": _SCROLLBAR_STYLE["track_opacity"],
            "stats_selection_opacity": {
                mode: colors["selection_opacity"] for mode, colors in self._stats_colors.items()
            },
            "indent_line_steps": self._indent_line_steps,
            "indent_line_reverse": self._indent_line_reverse,
            "indent_hover_opacity": self._indent_hover_opacity,
            "indent_lines_enabled": self._indent_lines_enabled,
            "indent_line_thickness": self._indent_line_thickness,
            "indent_line_style": self._indent_line_style,
            **{key: getattr(self, attr, default) for attr, key, default in _INDENT_TICK_STATE},
            "widgets": self._capture_theme_widget_values(),
        }

    def _merged_color_set(self, theme: str, saved: dict | None) -> dict:
        """`theme`'s built-in set with whatever `saved` (a set read
        back from the settings file) supplies laid over it -- per key
        inside each dict, so a saved set from before some newer color
        existed just leaves that one at its default instead of failing
        or resetting the rest."""
        merged = self._builtin_color_set(theme)
        for key, value in (saved or {}).items():
            if key not in merged:
                continue
            if key == "stats_colors" and isinstance(value, dict):
                for mode, colors in value.items():
                    if mode in merged[key] and isinstance(colors, dict):
                        merged[key][mode].update(
                            {k: v for k, v in colors.items() if k != "selection_opacity"}
                        )
            elif isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key].update(value)
            else:
                merged[key] = value
        # Copy -->/Move --> moved from the Statistics View's Buttons table
        # to Clipboard Buttons (stats_button_colors -> main_button_colors).
        # A set saved before that still has them under stats_button_colors:
        # carry a picked color over, unless the saved set already has one
        # of its own for it in the new place.
        saved_main = (saved or {}).get("main_button_colors") or {}
        for key in ("copy_across", "move_across"):
            moved = merged["stats_button_colors"].pop(key, None)
            if moved and not saved_main.get(key):
                merged["main_button_colors"][key] = moved
        return merged

    def _load_color_set_into_live(self, cs: dict):
        """Makes `cs` (a full set) the live color state -- the module-
        level dicts (TAG_COLORS, scrollbar, invert button) updated in
        place since other modules hold references to those very
        objects. Doesn't repaint anything by itself: callers follow up
        with a rebuild or _refresh_color_dependent_widgets."""
        self._select_colors = {k: tuple(v) for k, v in cs["select_colors"].items()}
        self._summary_colors = dict(cs.get("summary_colors", self._summary_colors))
        self._folder_color = tuple(cs["folder_color"])
        self._progress_bar_colors = dict(cs["progress_bar_colors"])
        _SCROLLBAR_COLORS.update(cs["scrollbar_colors"])
        _INVERT_BUTTON_COLORS.update(cs["invert_button_colors"])
        custom = cs["custom_default_button_colors"]
        self._custom_default_button_colors = tuple(custom) if custom else None
        TAG_COLORS.update({k: tuple(v) for k, v in cs["tag_colors"].items()})
        self._selection_tint_color = cs["selection_tint_color"]
        self._flv_bg_color = cs.get("flv_bg_color")
        self._title_numeric_color = cs["title_numeric_color"]
        self._status_selected_color = cs.get("status_selected_color", "#F5D400")
        self._status_unselected_color = cs.get("status_unselected_color")
        self._history_live_color = cs.get("history_live_color", "#FFFFFF")
        self._history_snapshot_color = cs.get("history_snapshot_color", "#C8C8C8")
        self._indent_line_color1 = cs["indent_line_color1"]
        self._indent_line_color2 = cs["indent_line_color2"]
        self._indent_hover_color = cs["indent_hover_color"]
        for mode, colors in cs["stats_colors"].items():
            if mode in self._stats_colors:
                self._stats_colors[mode].update(
                    {k: v for k, v in colors.items() if k != "selection_opacity"}
                )
        self._stats_button_colors = {
            k: (list(v) if v else None) for k, v in cs["stats_button_colors"].items()
        }
        self._main_button_colors = {
            k: (list(v) if v else None) for k, v in cs["main_button_colors"].items()
        }
        self._selection_tint_opacity = int(cs["selection_tint_opacity"])
        _SCROLLBAR_STYLE["handle_opacity"] = int(
            cs.get("scrollbar_handle_opacity", _DEFAULT_SCROLLBAR_HANDLE_OPACITY_PCT)
        )
        _SCROLLBAR_STYLE["track_opacity"] = int(
            cs.get("scrollbar_track_opacity", _DEFAULT_SCROLLBAR_TRACK_OPACITY_PCT)
        )
        for mode, opacity in cs["stats_selection_opacity"].items():
            if mode in self._stats_colors:
                self._stats_colors[mode]["selection_opacity"] = int(opacity)
        self._indent_line_steps = int(cs["indent_line_steps"])
        self._indent_line_reverse = bool(cs["indent_line_reverse"])
        self._indent_hover_opacity = int(cs["indent_hover_opacity"])
        self._indent_lines_enabled = bool(cs["indent_lines_enabled"])
        self._indent_line_thickness = int(cs["indent_line_thickness"])
        self._indent_line_style = int(cs.get("indent_line_style", 0))
        for attr, key, default in _INDENT_TICK_STATE:
            setattr(self, attr, type(default)(cs.get(key, default)))

    def _effective_gui_colors(self, theme: str) -> tuple[str, str]:
        """(fg, bg) hex of the window text/background for `theme` --
        the custom pair from Settings -> Misc Colors -> GUI if one's
        set for that theme, else that theme's entry in _THEME_DEFAULTS
        above."""
        custom = getattr(self, "_gui_colors", {}).get(theme)
        if custom is not None:
            return custom[0], custom[1]
        defaults = _THEME_DEFAULTS.get(theme, _THEME_DEFAULTS["dark"])
        return defaults["gui_fg"], defaults["gui_bg"]

    def _on_gui_color_changed(self, is_fg: bool, hex_color: str):
        fg, bg = self._effective_gui_colors(self._theme)
        fg, bg = (hex_color, bg) if is_fg else (fg, hex_color)
        self._gui_colors[self._theme] = (fg, bg)
        # Same sequence as _on_theme_changed: new palette, then a full
        # rebuild so every button/label derived from the window color
        # (see _default_button_colors) is re-styled against it.
        self._apply_theme_palette(self._theme)
        self._rebuild_ui()

    def _sync_windows_titlebar_theme(self, theme: str):
        """Windows 10/11 only: explicitly tells DWM whether this
        window's title bar should render dark or light, right after
        the palette itself is set, instead of leaving it to Qt's own
        automatic version of this (present on recent Qt/Windows
        combinations, driven by whether QPalette::Window ends up dark
        or light). That automatic path runs off Qt's own palette-
        changed processing -- the same "QApplication.setPalette
        propagates to top-level widgets asynchronously" issue this
        method's caller already has to work around for button colors
        (see _theme_window_color above) -- which is exactly why the
        title bar visibly lagged one toggle behind and only ever
        caught up once something else (minimize/restore) forced
        Windows to re-query the window's frame attributes, or after an
        unpredictable delay on its own. Setting the DWM attribute
        ourselves, synchronously, right here, means the title bar
        updates on this exact call instead of waiting on Qt's own
        delayed handling of it.

        Best-effort only: no-ops immediately on any non-Windows
        platform, and swallows any failure (missing dwmapi, an
        unsupported Windows build, a window handle DWM rejects) --
        this is a cosmetic touch-up on top of an already-correct-
        eventually palette, never something worth crashing theme
        switching over."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if theme == "dark" else 0)
            # DWMWA_USE_IMMERSIVE_DARK_MODE is attribute 20 on Windows
            # 10 20H1+ and Windows 11; 19 on the earlier Windows 10
            # builds that still supported the attribute at all under a
            # different ID. Try both -- an unsupported ID just fails
            # (nonzero HRESULT) rather than raising, so trying 19 after
            # 20 fails costs nothing on the (now typical) builds where
            # 20 already succeeded.
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                )
                if result == 0:
                    break
        except Exception:
            pass

    def _reset_to_defaults(self):
        """"Reload Defaults": every setting of the ACTIVE theme (its
        whole set -- colors, opacities, indentation-line options and
        every other per-theme Settings-tab control -- plus its custom
        GUI colors) back to its built-in starting value. Does not touch
        any other theme's set, the Global Options, or the theme itself,
        and does not save to disk on its own (Save Settings afterward
        if you want it to stick)."""
        # Only the active theme's colors -- every other theme's set
        # (and its custom GUI colors) is left exactly as it is, and the
        # theme itself isn't switched.
        needs_rebuild = (
            self._custom_default_button_colors is not None
            or self._gui_colors.get(self._theme) is not None
        )
        self._gui_colors[self._theme] = None
        self._load_color_set_into_live(self._builtin_color_set(self._theme))
        self._apply_theme_palette(self._theme)
        # Only rebuild the whole window if the custom GUI/default-button
        # colors actually changed (same cases settings_ops.
        # _apply_raw_settings_state rebuilds for, and for the same
        # reasons: a new QPalette; _default_button_colors() feeding many
        # unrelated _build_* methods) -- otherwise _refresh_color_
        # dependent_widgets already covers everything this touched
        # (including re-applying the just-reset selection-tint state),
        # without tearing down and repopulating the pane trees for
        # nothing (see that fix's own history for why that's costly).
        # The per-theme widget settings (Indentation, File List share,
        # Statistics View bins/font size, Bold, ...) back to the values
        # the very first build gave them -- through the widgets' own
        # setters, after any rebuild so they're the live ones, and
        # before the bulk refresh so it sees what their handlers set.
        if needs_rebuild:
            self._rebuild_ui()
            self._apply_theme_widget_values(self._widget_defaults)
        else:
            self._apply_theme_widget_values(self._widget_defaults)
            self._refresh_color_dependent_widgets()
        self._set_settings_status(f"{self._theme.replace('_', ' ').title()} theme reset to defaults.")
