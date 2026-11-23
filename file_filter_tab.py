"""
FileFilterTabMixin: the editable file type list behind the Main tab's
Global File Filter drop-down (Settings tab -> Global Settings -> File
type list), and the program-wide (not per theme) File Filter settings.

Two copies of the list exist on purpose:
  - self._file_filter_types: the *committed* list. It is what the Main tab's
    drop-down shows and what every save writes to the settings file.
  - self._file_filter_working: what the list widget currently
    shows, edits included. Kept in step with the widget on every change
    (so a window rebuild, e.g. a theme switch, does not lose unsaved edits).
It becomes the committed list on Save Settings; Load Settings and Load
Defaults replace both. The drop-down is only reloaded at those moments
and at startup, never while the list is being edited.

Persistence itself (the "file_filter_types" key in the settings file) is in
settings_ops.py, which calls the methods below.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)

from constants import DEFAULT_FILE_FILTER_TYPES, FILTER_SEPARATOR
from custom_widgets import _bevel_button_stylesheet


def _clean_types(types) -> list:
    """The entries as search terms: surrounding spaces removed, no blanks,
    no repeats (case-insensitive, first spelling wins), order kept. An
    entry is used exactly as written, so it cannot contain the filter's
    separator."""
    seen = set()
    result = []
    for raw in types:
        term = str(raw).strip()
        if term and FILTER_SEPARATOR not in term and term.lower() not in seen:
            seen.add(term.lower())
            result.append(term)
    return result


class FileFilterTabMixin:
    def _init_file_filter_state(self):
        """Called once before the UI is first built."""
        self._file_filter_types = list(DEFAULT_FILE_FILTER_TYPES)
        self._file_filter_working = list(DEFAULT_FILE_FILTER_TYPES)
        self._file_filter_list = None
        # "Folder counts include subfolders" (Settings tab); lives here so
        # it survives a UI rebuild, like the comparison settings do.
        self._folder_counts_include_subfolders = False

    # ----------------------------------------------------------- Building

    def _build_file_filter_group(self) -> QGroupBox:
        """The File type list's frame for the Settings tab (Global Settings
        -> "File type list"; the caller wraps it in a titled block): the
        list itself and its Add/Remove/Move/Sort/Defaults buttons.
        Save Settings / Load Settings on the Settings tab commit / reload
        the list along with everything else."""
        group = QGroupBox("")
        group_layout = QVBoxLayout(group)
        list_tip = (
            "The entries of the Global File Filter's drop-down on the Main tab. Each "
            "entry is one complete search term, used exactly as written: *.txt is "
            "every name ending in .txt, while a plain term like report matches "
            "anywhere in a name (* = any characters, ? = one character; with one of "
            "them the term must match the whole name).\n"
            "Double-click an entry to edit it, or use Add. The drop-down is updated "
            "by Save Settings, Load Settings and Defaults, not while you edit."
        )

        body = QHBoxLayout()
        self._file_filter_list = QListWidget()
        self._file_filter_list.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._file_filter_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._file_filter_list.setMinimumHeight(260)
        self._file_filter_list.setMinimumWidth(90)
        self._ff_fill_list(self._file_filter_working)
        # Edits (typing in an item) and everything else both end up here.
        self._file_filter_list.itemChanged.connect(lambda _item: self._ff_sync_working())
        self._file_filter_list.setToolTip(list_tip)
        body.addWidget(self._file_filter_list)

        buttons = QVBoxLayout()
        default_bg, default_fg = self._default_button_colors()
        uniform_w, uniform_h = self._uniform_button_size()
        for text, tip, handler in (
            ("Add", "Add a new entry below the selection (or at the end) and edit it.", self._ff_add),
            ("Remove", "Remove the selected entries.", self._ff_remove),
            ("Move up", "Move the selected entries up one step.", lambda: self._ff_move(-1)),
            ("Move down", "Move the selected entries down one step.", lambda: self._ff_move(1)),
            ("Sort A-Z", "Sort the whole list alphabetically.", self._ff_sort),
            ("Defaults",
             "Put the list back to the program's built-in file types and update the "
             "drop-down on the Main tab. The saved settings file isn't touched; Save "
             "Settings afterward if you want the reset to stick.",
             self._reset_file_filter_defaults),
        ):
            button = QPushButton(text)
            button.setFixedSize(uniform_w, uniform_h)
            button.setStyleSheet(_bevel_button_stylesheet(default_bg, default_fg))
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, h=handler: h())
            buttons.addWidget(button)
        buttons.addStretch(1)
        body.addLayout(buttons)
        group_layout.addLayout(body)
        return group

    # ------------------------------------------------------- List editing

    def _ff_fill_list(self, types):
        lst = self._file_filter_list
        lst.blockSignals(True)
        lst.clear()
        for ext in types:
            self._ff_make_item(ext)
        lst.blockSignals(False)

    def _ff_make_item(self, text: str, row: int = -1) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        if row < 0:
            self._file_filter_list.addItem(item)
        else:
            self._file_filter_list.insertItem(row, item)
        return item

    def _ff_sync_working(self):
        """Working copy := what the list widget shows right now (blank
        rows still count while being edited; they are dropped on commit)."""
        self._file_filter_working = [
            self._file_filter_list.item(i).text() for i in range(self._file_filter_list.count())
        ]

    def _ff_add(self):
        lst = self._file_filter_list
        selected = sorted(lst.row(i) for i in lst.selectedItems())
        row = selected[-1] + 1 if selected else lst.count()
        lst.blockSignals(True)
        item = self._ff_make_item("", row)
        lst.blockSignals(False)
        self._ff_sync_working()
        lst.setCurrentItem(item)
        lst.editItem(item)

    def _ff_remove(self):
        lst = self._file_filter_list
        lst.blockSignals(True)
        for item in lst.selectedItems():
            lst.takeItem(lst.row(item))
        lst.blockSignals(False)
        self._ff_sync_working()

    def _ff_move(self, step: int):
        lst = self._file_filter_list
        rows = sorted(lst.row(i) for i in lst.selectedItems())
        if not rows:
            return
        if step < 0 and rows[0] == 0 or step > 0 and rows[-1] == lst.count() - 1:
            return
        lst.blockSignals(True)
        for row in (rows if step < 0 else reversed(rows)):
            item = lst.takeItem(row)
            lst.insertItem(row + step, item)
        lst.clearSelection()
        for row in rows:
            lst.item(row + step).setSelected(True)
        lst.blockSignals(False)
        self._ff_sync_working()

    def _ff_sort(self):
        self._ff_sync_working()
        self._file_filter_working = sorted(
            _clean_types(self._file_filter_working), key=str.lower
        )
        self._ff_fill_list(self._file_filter_working)

    # ------------------------------------------ Commit / load / defaults

    def _file_filter_commit(self):
        """Save Settings: the list as edited becomes the committed one
        (cleaned), the widget is redrawn with the cleaned list, and the
        Main tab's drop-down is reloaded from it."""
        self._ff_sync_working()
        self._file_filter_types = _clean_types(self._file_filter_working)
        self._file_filter_working = list(self._file_filter_types)
        self._ff_fill_list(self._file_filter_working)
        self._refresh_file_type_dropdown()
        self._refresh_filter_metric_panes()

    def _file_filter_set(self, types):
        """Load Settings / Load Defaults / startup: replace both copies
        and reload the drop-down."""
        self._file_filter_types = _clean_types(types)
        self._file_filter_working = list(self._file_filter_types)
        if self._file_filter_list is not None:
            self._ff_fill_list(self._file_filter_working)
        self._refresh_file_type_dropdown()
        self._refresh_filter_metric_panes()

    def _reset_file_filter_defaults(self):
        """"Defaults" button under the file type list. Not saved until Save Settings."""
        self._file_filter_set(DEFAULT_FILE_FILTER_TYPES)
        self._set_settings_status("File Filter settings reset to defaults.")

    def _refresh_file_type_dropdown(self):
        """Reload the Main tab's drop-down from the committed list, with
        its signals blocked (it would otherwise add the entry at index 0
        to the filter text)."""
        combo = getattr(self, "file_type_dropdown", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("")
        combo.addItems(self._file_filter_types)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
