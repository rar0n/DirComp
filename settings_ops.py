"""
SettingsMixin: Save Settings / Load Settings (see ui_builder's Status
group) persist every checkbox/spinbox/combobox/text field across the
whole GUI -- Global Options, File Filter, and each pane's own Statistics
view controls -- to a single JSON file, and restore them from it.
(A QButtonGroup of radio buttons counts as one control too: it's saved
as the id of its checked button.)

Deliberately generic rather than one get/set pair per named widget:
_settings_widgets() is the only place that lists *which* widgets are
persisted; save/load themselves dispatch purely on Qt widget *type*
(QCheckBox/QSpinBox/QDoubleSpinBox/QComboBox/QLineEdit/QButtonGroup), so adding a
new persisted control later is a one-line addition there, not a new
branch in both save_settings and load_settings.

Restoring a value goes through the widget's normal setChecked/setValue/
setCurrentIndex/setText rather than writing some internal state
directly, so every side effect a user's own change to that control
would trigger (update_listings, a layout mirror rebuild, a timeline
re-bucket, ...) still happens -- load_settings doesn't need its own
copy of that logic, it just re-plays the same widget-level change the
GUI already knows how to react to.
"""

from __future__ import annotations

import copy
import json
import os

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QMessageBox, QSpinBox,
)

from constants import CMP_FINGERPRINT, CMP_HASH, CMP_NAME_SIZE_DATE
from theme_ops import _THEMES

_SETTINGS_FILENAME = "dircomp_settings.json"

# The only widget settings NOT kept per theme: Global Options (the Main
# tab's checkboxes and their Settings-tab copies, plus the two window-
# width thresholds). Every other widget in _settings_widgets() is saved
# inside each theme's set instead of at the top level of the file -- see
# _per_theme_widgets.
#
# The File Filter box's own state (Invert Filter, the filter text) and
# each pane's Statistics View Metric dropdown aren't in _settings_widgets()
# at all, and so aren't saved anywhere, per-theme or shared -- they're
# working choices made in the moment on the Main tab, not a saved
# preference, so the app starts each session with an empty filter and
# whichever metric each combo box defaults to on its own (see
# ui_builder._build_directory_widgets).
_SHARED_WIDGET_KEYS = frozenset({
    "recursive_folder_contents", "expand_matching", "list_directories", "list_files",
    "sort_directories_first", "symmetrical_buttons", "synchronize_views",
    "dual_pane", "dual_pane_threshold", "single_pane_threshold", "unnatural_scrolling",
    "invert_statistics_zoom", "file_list_history_limit", "scrollbar_handle_style",
    "comparison_type", "hash_algorithm", "fingerprint_start_kb", "fingerprint_end_kb",
    "ignore_file_extensions", "first_name_only",
    "multi_select_matching", "multi_select_same_folder",
})


# The keys of one theme's color set (see UiBuilderMixin._builtin_color_set/
# _capture_color_set) -- also the names those same colors were stored
# under, flat at the top of "_raw", before each theme got its own set
# (see _apply_raw_settings_state's backward-compat branch), plus the
# even older shared "stats_background_color".
_COLOR_SET_KEYS = (
    "select_colors", "folder_color", "progress_bar_colors", "scrollbar_colors",
    "invert_button_colors", "custom_default_button_colors", "tag_colors",
    "selection_tint_color", "title_numeric_color", "indent_line_color1",
    "indent_line_color2", "indent_hover_color", "stats_colors",
    "stats_button_colors", "main_button_colors", "stats_background_color",
)


class SettingsMixin:
    def _settings_widgets(self) -> dict:
        """name -> widget for every control Save/Load Settings persists.
        Names are stable identifiers for the JSON file, not tied to the
        widgets' own Qt object names."""
        # The opacity spin boxes (selection tint, Statistics View
        # selection, indentation-line hover) and the indentation-line
        # options (enable, thickness, steps, reverse) are per theme, so
        # they're saved inside each theme's color set (see _raw_settings_
        # state) instead of here. Invert Filter/the filter text itself,
        # and each pane's Statistics View Metric combo below, are left
        # out of this dict entirely -- see the comment above
        # _SHARED_WIDGET_KEYS -- so they're never saved at all.
        widgets = {
            "recursive_folder_contents": getattr(self, "recursive_folder_contents_cb", None),
            "expand_matching": getattr(self, "expand_matching_cb", None),
            "list_directories": getattr(self, "list_directories_cb", None),
            "list_files": getattr(self, "list_files_cb", None),
            "sort_directories_first": getattr(self, "sort_directories_first_cb", None),
            "symmetrical_buttons": getattr(self, "symmetrical_buttons_cb", None),
            "synchronize_views": getattr(self, "synchronize_views_cb", None),
            "dual_pane": getattr(self, "dual_pane_cb", None),
            "dual_pane_threshold": getattr(self, "dual_pane_threshold_spin", None),
            "single_pane_threshold": getattr(self, "single_pane_threshold_spin", None),
            "unnatural_scrolling": getattr(self, "unnatural_scrolling_cb", None),
            "invert_statistics_zoom": getattr(self, "invert_statistics_zoom_cb", None),
            "force_single_date_column": getattr(self, "force_single_date_column_cb", None),
            "mirror_statistics_buttons": getattr(self, "mirror_statistics_buttons_cb", None),
            "file_list_ratio": getattr(self, "file_list_ratio_spin", None),
            "title_numeric_bold": getattr(self, "title_numeric_bold_cb", None),
            "file_list_indentation": getattr(self, "tree_indentation_spin", None),
            "flv_font_size": getattr(self, "flv_font_size_spin", None),
            "file_list_history_limit": getattr(self, "file_list_history_limit_spin", None),
            # A QButtonGroup, not a widget: see _widget_value below.
            "scrollbar_handle_style": getattr(self, "scrollbar_handle_style_group", None),
            "comparison_type": getattr(self, "comparison_type_combo", None),
            "hash_algorithm": getattr(self, "hash_algorithm_combo", None),
            "fingerprint_start_kb": getattr(self, "fingerprint_start_spin", None),
            "fingerprint_end_kb": getattr(self, "fingerprint_end_spin", None),
            "ignore_file_extensions": getattr(self, "ignore_file_extensions_cb", None),
            "first_name_only": getattr(self, "first_name_only_cb", None),
            "multi_select_matching": getattr(self, "multi_select_matching_cb", None),
            "multi_select_same_folder": getattr(self, "multi_select_same_folder_cb", None),
        }
        for side in ("left", "right"):
            pane = getattr(self, side, None)
            if pane is None:
                continue
            widgets[f"{side}_timeline_bin_count"] = pane.tl_bin_count_spin
            widgets[f"{side}_timeline_nice_bins"] = pane.tl_nice_bins_cb
            widgets[f"{side}_timeline_log_scale"] = pane.tl_log_scale_cb
            widgets[f"{side}_timeline_log_size_axis"] = pane.tl_log_size_axis_cb
            widgets[f"{side}_timeline_auto_vrange"] = pane.tl_auto_vrange_cb
            widgets[f"{side}_timeline_font_size"] = pane.tl_font_size_spin
            widgets[f"{side}_timeline_tick_angle"] = pane.tl_tick_angle_spin
            widgets[f"{side}_timeline_auto_tick_angle"] = getattr(pane, "tl_auto_tick_angle_cb", None)
            widgets[f"{side}_timeline_strip_units"] = getattr(pane, "tl_strip_units_cb", None)
            widgets[f"{side}_timeline_strip_names"] = getattr(pane, "tl_strip_names_cb", None)
            widgets[f"{side}_timeline_strip_abbreviate"] = getattr(pane, "tl_strip_abbreviate_cb", None)
            widgets[f"{side}_timeline_strip_week_number"] = getattr(pane, "tl_strip_week_number_cb", None)
        return {name: w for name, w in widgets.items() if w is not None}

    def _per_theme_widgets(self) -> dict:
        """The subset of _settings_widgets() that's kept per theme."""
        return {n: w for n, w in self._settings_widgets().items() if n not in _SHARED_WIDGET_KEYS}

    @staticmethod
    def _widget_value(widget):
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return widget.value()
        if isinstance(widget, QComboBox):
            # Index, not currentData()/currentText() -- stable across a
            # session (the combo's items don't change) and avoids caring
            # whether a given combo's data role holds a string, enum, or
            # something else.
            return widget.currentIndex()
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, QButtonGroup):
            # The checked radio button's id (-1 = none checked -> not saved).
            checked = widget.checkedId()
            return checked if checked >= 0 else None
        return None

    @staticmethod
    def _set_widget_value(widget, value):
        """Through the widget's own normal setter, so every side effect
        of a user's own change still happens -- see module docstring."""
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value))
        elif isinstance(widget, QComboBox):
            index = int(value)
            if 0 <= index < widget.count():
                widget.setCurrentIndex(index)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))
        elif isinstance(widget, QButtonGroup):
            button = widget.button(int(value))
            if button is not None:
                button.setChecked(True)

    def _capture_theme_widget_values(self) -> dict:
        """Current value of every per-theme widget, keyed by its stable
        settings name -- stored in the active theme's set (see
        UiBuilderMixin._capture_color_set)."""
        values = {}
        for name, widget in self._per_theme_widgets().items():
            value = self._widget_value(widget)
            if value is not None:
                values[name] = value
        return values

    def _apply_theme_widget_values(self, values: dict):
        """Puts a set's per-theme widget values onto the live widgets.
        Called after anything that rebuilds the UI (a rebuild recreates
        these widgets at their defaults, or at the previous theme's
        values), so it always has real widgets to write to."""
        for name, widget in self._per_theme_widgets().items():
            if name in values:
                self._set_widget_value(widget, values[name])

    def _raw_settings_state(self) -> dict:
        """Theme, the per-theme color sets, and other plain
        self-attribute state not backed by any single widget's own
        setChecked/setValue/... the way _settings_widgets' entries are,
        so it can't go through that same generic dispatch. Every
        theme's complete color set is stored ("color_sets") -- the
        active theme's freshly captured from the live attributes, the
        others' as last parked when the theme was switched away from
        or loaded from this file -- so switching theme never needs the
        file re-read. Nothing here has a widget-level side effect of
        its own to replay, so load_settings re-applies the whole lot at
        once via _apply_raw_settings_state below instead."""
        self._color_sets[self._theme] = self._capture_color_set()
        return {
            "theme": self._theme,
            "gui_colors": {t: (list(v) if v else None) for t, v in self._gui_colors.items()},
            "color_sets": copy.deepcopy(self._color_sets),
            # Window size -- plain self.width()/height() rather than a
            # dedicated widget, so it goes through the same "no setter
            # to dispatch on" _raw path as the color state above.
            # Restored via self.resize() in _apply_raw_settings_state,
            # which is also what re-triggers _update_responsive_layout
            # (see ui_builder.resizeEvent) so the narrow/wide cluster
            # arrangement matches the restored size immediately.
            "window_width": self.width(),
            "window_height": self.height(),
        }

    def _legacy_option_values(self, top: dict | None) -> dict:
        """Those old top-level widget values, in a color set's shape."""
        top = top or {}
        values = {}
        for key in ("selection_tint_opacity", "indent_line_steps",
                    "indent_line_reverse", "indent_hover_opacity"):
            if key in top:
                values[key] = top[key]
        stats = {mode: top[f"stats_{mode}_selection_opacity"]
                 for mode in ("file", "folder") if f"stats_{mode}_selection_opacity" in top}
        if stats:
            values["stats_selection_opacity"] = stats
        # Widget settings that are per theme now but used to be plain
        # top-level values (Use Only 1 Date Column, File List share,
        # Statistics View font size/bins/..., Bold, Indentation, ...).
        widgets = {n: top[n] for n in self._per_theme_widgets() if n in top}
        if widgets:
            values["widgets"] = widgets
        return values

    def _legacy_flat_color_set(self, raw: dict) -> dict:
        """A color set built from the flat color keys older settings
        files kept directly in "_raw" (before per-theme sets existed),
        including the pre-per-mode shared "stats_background_color" --
        used for whichever theme that file was saved with."""
        flat = {k: copy.deepcopy(raw[k]) for k in _COLOR_SET_KEYS if k in raw}
        legacy_bg = flat.pop("stats_background_color", None)
        if legacy_bg is not None:
            # Fall back to it for whichever mode a "stats_colors" block
            # didn't already supply a background for, so loading an old
            # file doesn't silently reset an existing background choice.
            stats = flat.setdefault("stats_colors", {})
            for mode in ("file", "folder"):
                stats.setdefault(mode, {}).setdefault("background_color", legacy_bg)
        return flat

    def _apply_raw_settings_state(self, raw: dict, top: dict | None = None):
        """Reverse of _raw_settings_state -- restores every theme's color
        sets and makes the saved theme's the live one (see
        UiBuilderMixin._load_color_set_into_live: no widget setter to
        go through), then re-applies the same live updates each
        individual color-swatch handler already applies for itself
        (_recolor_all_items, _repaint_all_scrollbars, ...) rather than
        always doing a full _rebuild_ui(): a rebuild tears down and
        repopulates the pane trees from scratch, which doesn't reliably
        re-tag items with the colors just set below, undoing the
        recolor. Only a theme change, custom GUI colors, or the custom
        default-button colors actually need a rebuild -- the same cases
        _on_theme_changed/_on_gui_color_changed/_on_default_button_
        color_changed rebuild for themselves, for the same reasons (a
        new QPalette; _default_button_colors() feeding many unrelated
        _build_* methods) -- so a rebuild here is limited to those.

        `top` is the whole settings file's top level, only consulted for
        the older opacity/indentation-line values that used to live
        there as plain widget values (see _legacy_option_values)."""
        needs_rebuild = False
        old_gui = self._effective_gui_colors(self._theme)
        old_default_buttons = self._custom_default_button_colors
        # GUI window colors (per theme) go in before the theme's palette
        # is applied below, so that palette is built from them.
        if "gui_colors" in raw:
            for t in _THEMES:
                v = raw["gui_colors"].get(t)
                self._gui_colors[t] = tuple(v) if v else None

        saved_theme = raw.get("theme")
        if saved_theme not in _THEMES:
            saved_theme = self._theme
        # The pre-per-theme opacity/indentation-line values belong to
        # whichever theme the file was saved with, filling in only what
        # that theme's saved set doesn't have itself.
        legacy_options = self._legacy_option_values(top)
        sets_loaded = False
        if "color_sets" in raw:
            for t in _THEMES:
                saved = raw["color_sets"].get(t)
                if t == saved_theme and legacy_options:
                    saved = {**legacy_options, **(saved or {})}
                self._color_sets[t] = self._merged_color_set(t, saved)
            sets_loaded = True
        elif any(k in raw for k in _COLOR_SET_KEYS) or legacy_options:
            # Backward compat: a file saved before each theme had its
            # own set -- its flat colors are the saved theme's set, and
            # every other theme starts from its built-in defaults.
            legacy = {**legacy_options, **self._legacy_flat_color_set(raw)}
            for t in _THEMES:
                self._color_sets[t] = self._merged_color_set(t, legacy if t == saved_theme else None)
            sets_loaded = True

        theme = raw.get("theme", self._theme)
        if theme not in _THEMES:
            theme = self._theme
        theme_changed = theme != self._theme
        if theme_changed and not sets_loaded:
            # Nothing color-related in the file, but the theme itself
            # differs -- park the outgoing theme's live colors, as a
            # normal theme switch would.
            self._color_sets[self._theme] = self._capture_color_set()
        self._theme = theme
        if sets_loaded or theme_changed:
            self._load_color_set_into_live(self._color_sets[theme])
        if "theme" in raw or "gui_colors" in raw:
            self._apply_theme_palette(self._theme)
        if (theme_changed
                or self._effective_gui_colors(self._theme) != old_gui
                or self._custom_default_button_colors != old_default_buttons):
            needs_rebuild = True

        if "window_width" in raw and "window_height" in raw:
            # Applied regardless of needs_rebuild below -- a theme/
            # button-color rebuild doesn't touch the window's own size,
            # so this doesn't need to wait on (or repeat across) that
            # branch.
            self.resize(int(raw["window_width"]), int(raw["window_height"]))

        apply_widgets = sets_loaded or theme_changed
        if needs_rebuild:
            self._rebuild_ui()
            if apply_widgets:
                self._apply_theme_widget_values(self._color_sets[self._theme].get("widgets", {}))
            return

        # The per-theme widget values first, so the bulk refresh below
        # sees the attributes their handlers update.
        if apply_widgets:
            self._apply_theme_widget_values(self._color_sets[self._theme].get("widgets", {}))
        # Everything else here has no rebuild-worthy side effect of its
        # own -- same live-update calls their individual swatch
        # handlers already make, all done together (see that method).
        self._refresh_color_dependent_widgets()

    def _settings_path(self) -> str:
        """The program's own folder (where main.py/this file live), not a
        per-OS config directory -- keeps the settings file next to the
        app itself, portable with it (e.g. on a USB drive or a synced
        folder) rather than tucked away somewhere OS-specific."""
        program_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(program_dir, _SETTINGS_FILENAME)

    def save_settings(self, quiet: bool = False):
        """Write every widget in _settings_widgets() to disk, keyed by
        its stable name -- read back by load_settings, either from the
        Load Settings button or automatically at startup (see
        main.DirectoryComparisonApp.__init__). `quiet` skips the status-
        line message either way (success or failure) -- used for the
        automatic saves _record_folder_history/_on_clear_folder_history
        trigger, which shouldn't spam "Settings saved to ..." on every
        folder open the way an explicit Save Settings click should."""
        data = {}
        if not quiet:
            # Explicit Save Settings: this is what makes a dropdown DEL
            # permanent -- see DirectoryPane.saved_folder_history.
            for side in ("left", "right"):
                pane = getattr(self, side, None)
                if pane is not None:
                    pane.saved_folder_history = list(pane.folder_history)
        for name, widget in self._settings_widgets().items():
            if name not in _SHARED_WIDGET_KEYS:
                continue  # per-theme: saved inside each theme's set below
            value = self._widget_value(widget)
            if value is not None:
                data[name] = value
        # Each pane's folder-open history isn't a single-value widget, so
        # it can't go through _settings_widgets()/_widget_value above --
        # saved explicitly instead, shared (not per-theme) like the rest
        # of Global Options. See panes.DirectoryPane.folder_history.
        for side in ("left", "right"):
            pane = getattr(self, side, None)
            if pane is not None:
                data[f"{side}_folder_history"] = list(pane.saved_folder_history)
        data["_raw"] = self._raw_settings_state()
        path = self._settings_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as exc:
            if not quiet:
                self._set_settings_status(f"Could not save settings: {exc}")
            return
        if not quiet:
            self._set_settings_status(f"Settings saved to {path}")

    def load_settings(self, quiet: bool = False):
        """Apply every value found in the settings file to its matching
        widget, via that widget's own normal setter -- see module
        docstring for why. `quiet`, used for the automatic startup load
        (see main.py), skips both the transfer_label status message and
        the "no settings file yet" one, since a brand new install
        having nothing to load yet isn't worth announcing every time
        the app opens; the buttoned-triggered load still reports both,
        since that's a deliberate user action."""
        path = self._settings_path()
        if not os.path.isfile(path):
            if not quiet:
                self._set_settings_status("No saved settings found.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            if not quiet:
                self._set_settings_status(f"Could not load settings: {exc}")
            return
        for name, widget in self._settings_widgets().items():
            if name not in data or name not in _SHARED_WIDGET_KEYS:
                continue  # per-theme values are applied with their set below
            self._set_widget_value(widget, data[name])
        for side in ("left", "right"):
            pane = getattr(self, side, None)
            if pane is None:
                continue
            key = f"{side}_folder_history"
            if key in data:
                pane.folder_history = list(data[key])
                pane.saved_folder_history = list(data[key])
                self._refresh_history_combo(pane)
        if "_raw" in data:
            self._apply_raw_settings_state(data["_raw"], data)
        else:
            # Older than the "_raw" block altogether: the per-theme
            # widgets were plain top-level values then.
            self._apply_theme_widget_values(data)
        if not quiet:
            self._set_settings_status(f"Settings loaded from {path}")

    def _record_folder_history(self, pane, path: str):
        """Push `path` (normalized) to the top of `pane`'s folder-open
        history -- moving it there instead of duplicating it if it's
        already present -- trimmed to file_list_history_limit_spin's
        current value, and saved to disk right away (quiet -- see
        save_settings) rather than waiting for the next Save Settings
        click, so an entry survives even if the app closes before that
        button is ever pressed. Called from SyncMixin._navigate_pane,
        before it does any actual listing/disk-scanning work -- see its
        record_history param."""
        norm = os.path.normpath(path)
        history = pane.folder_history
        history[:] = [p for p in history if p != norm]
        history.insert(0, norm)
        limit = self.file_list_history_limit_spin.value() if hasattr(self, "file_list_history_limit_spin") else 10
        del history[limit:]
        # The on-disk copy gets the same push (it may still hold entries
        # deleted from the dropdown but not yet saved -- see
        # _on_history_entry_deleted).
        saved = pane.saved_folder_history
        saved[:] = [p for p in saved if p != norm]
        saved.insert(0, norm)
        del saved[limit:]
        self._refresh_history_combo(pane)
        self.save_settings(quiet=True)

    def _refresh_history_combo(self, pane):
        """Repopulate `pane`'s entry combo box's dropdown from its
        current folder_history -- see DirDropComboBox.set_history, which
        leaves whatever path the box is currently displaying untouched."""
        if pane.entry is not None:
            pane.entry.set_history_colors(self._history_live_color, self._history_snapshot_color)
            pane.entry.set_history(pane.folder_history)

    def _on_history_entry_deleted(self, pane, path: str):
        """DEL on a single entry in `pane`'s history dropdown (see
        DirDropComboBox.historyEntryDeleted). The combo has already
        removed the row itself, so only the backing list is updated --
        no _refresh_history_combo, which would rebuild the dropdown
        under the still-open popup. Deliberately NOT saved: the entry
        stays in pane.saved_folder_history (what every automatic save
        writes) until an explicit Save Settings click, so Load Settings
        brings it back after an accidental delete."""
        pane.folder_history[:] = [p for p in pane.folder_history if p != path]

    def _on_clear_folder_history(self):
        """Settings tab -> Global Options -> "Clear history" button:
        confirms first (this can't be undone -- no "Cancel Save" to walk
        it back, since it's persisted immediately below), then wipes
        both panes' folder-open history (not just one -- per Ragnar's
        spec) and persists that right away, same reasoning as
        _record_folder_history -- an explicit "clear" that only took
        effect in memory until the next Save Settings click would be a
        surprising way for it to behave."""
        reply = QMessageBox.question(
            self, "Clear History",
            "Remove every entry from both panes' File List View history?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for pane in (self.left, self.right):
            pane.folder_history.clear()
            pane.saved_folder_history.clear()
            self._refresh_history_combo(pane)
        self.save_settings(quiet=True)
        self._set_settings_status("Folder history cleared.")

    def _on_file_list_history_limit_changed(self, value):
        """Settings tab -> Global Options -> "Nr. of history entries in
        File List View" spin box. Trims both panes' history down to the
        new limit right away (same as any other live Settings-tab
        control), but -- unlike _record_folder_history/_on_clear_folder_
        history above -- doesn't force an immediate disk save on its own:
        a plain value change here behaves like every other Settings-tab
        spin box (in memory until the next Save Settings click, or until
        a folder's next opened, whichever comes first)."""
        self._file_list_history_limit = value
        for pane in (self.left, self.right):
            del pane.saved_folder_history[value:]
            if len(pane.folder_history) > value:
                del pane.folder_history[value:]
                self._refresh_history_combo(pane)

    def _update_comparison_controls_enabled(self):
        """Which comparison controls apply to the chosen type: Ignore
        file extensions only to Name, size and date; the algorithm to
        both hash types; the chunk sizes to Fingerprint alone."""
        ctype = self.comparison_type_combo.currentIndex()
        hashing = ctype in (CMP_HASH, CMP_FINGERPRINT)
        self.ignore_file_extensions_cb.setEnabled(ctype == CMP_NAME_SIZE_DATE)
        self.first_name_only_cb.setEnabled(ctype == CMP_NAME_SIZE_DATE)
        self.hash_algorithm_combo.setEnabled(hashing)
        self.hash_algorithm_label.setEnabled(hashing)
        self.multi_select_matching_cb.setEnabled(hashing)
        self.multi_select_same_folder_cb.setEnabled(hashing and self.multi_select_matching_cb.isChecked())
        for spin, label in ((self.fingerprint_start_spin, self.fingerprint_start_label),
                            (self.fingerprint_end_spin, self.fingerprint_end_label)):
            spin.setEnabled(ctype == CMP_FINGERPRINT)
            label.setEnabled(ctype == CMP_FINGERPRINT)

    def _on_multi_select_setting_changed(self, *_args):
        """Multi Select options (no effect on how files compare, so no
        re-tagging): copy into state and fix which one is enabled."""
        self._multi_select_matching = self.multi_select_matching_cb.isChecked()
        self._multi_select_same_folder = self.multi_select_same_folder_cb.isChecked()
        self._update_comparison_controls_enabled()

    def _on_name_match_option_toggled(self, other_cb, checked):
        """"Ignore file extensions" and "Only first part of file name" are
        mutually exclusive: checking one unchecks the other (silently --
        the regular change handler runs once, right after)."""
        if checked and other_cb.isChecked():
            other_cb.blockSignals(True)
            other_cb.setChecked(False)
            other_cb.blockSignals(False)
        self._on_comparison_setting_changed()

    def _on_comparison_setting_changed(self, *_args):
        """Any comparison control (Settings tab -> Global Options) changed:
        copy all of them into the self._cmp_* state, fix which controls are
        enabled, and -- only if the result can actually differ (see
        DiffEngineMixin._comparison_key) -- re-tag both panes in place from
        the snapshots already in memory (no disk rescan: nothing on disk
        changed, and update_listings' retag_only path just recolors)."""
        self._cmp_type = self.comparison_type_combo.currentIndex()
        self._cmp_algorithm = self.hash_algorithm_combo.currentIndex()
        self._cmp_chunk_start_kb = self.fingerprint_start_spin.value()
        self._cmp_chunk_end_kb = self.fingerprint_end_spin.value()
        self._cmp_ignore_ext = self.ignore_file_extensions_cb.isChecked()
        self._cmp_first_name = self.first_name_only_cb.isChecked()
        self._update_comparison_controls_enabled()
        self._update_hash_buttons_enabled()
        key = self._comparison_key()
        if key == self._cmp_applied_key:
            return
        self._cmp_applied_key = key
        self._invalidate_dir_rank_cache()
        # Nothing to re-tag until a folder has been loaded (settings are
        # applied at startup, before any listing exists).
        if any(getattr(p, "snapshot_root", None) is not None for p in (self.left, self.right)):
            self.update_listings(rescan_disk=False)

    def _set_settings_status(self, message: str):
        """Settings tab's own status line (see ui_builder._build_status_
        line's Settings-tab counterpart, self.settings_status_label) --
        separate from self.transfer_label on the Main tab, since a
        save/load result isn't a transfer/enumeration/search message and
        the two tabs no longer share a status line now that there's more
        than one tab. Falls back to the transfer_label silently if the
        Settings tab hasn't been built yet for some reason, rather than
        raising, since save/load can in principle be called very early
        (e.g. the automatic startup load in main.py)."""
        label = getattr(self, "settings_status_label", None)
        if label is not None:
            label.setText(message)
        else:
            self._set_transfer_label(message)
