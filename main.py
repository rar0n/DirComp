"""
Directory Comparison App (PySide6 port)

Ported from a Tkinter/tkinterdnd2 original. Adds native Qt drag-and-drop
(no external dnd library needed) and a working Clipboard group:
Copy / Cut / Paste, interoperable with GNOME/Nemo/Nautilus/Dolphin via the
"x-special/gnome-copied-files" mime type (same approach used in the Timeline
File Explorer project, which is incorporated as Statistics View here).

This file is the entry point. It composes DirectoryComparisonApp out of
mixins that each own one concern, so a change usually only touches one
file:

  constants.py     - MIME type, tree item data roles, tag colors, diff ranks
  trash_compat.py  - optional send2trash dependency shim
  dnd_widgets.py   - DirDropLineEdit / DirDropTreeWidget (drag-and-drop)
  progress.py      - _CancellableProgress (progress bar + Cancel token)
  panes.py         - DirectoryPane (one side's widgets/state)
  diff_engine.py   - DiffEngineMixin: how two items are compared (tags,
                      ranks, sizes, dates) -- e.g. a future change to how
                      dates are compared/typed belongs here
  listing_ops.py   - ListingMixin: populating/refreshing/sorting each tree
  selection_ops.py - SelectionMixin: the Select group's buttons + search
  transfer_ops.py  - TransferMixin: Clipboard group, Move/Copy, New Folder
  sync_views.py    - SyncMixin: "Synchronize Views"
  statistics_ops.py  - TimelineOpsMixin: feeds each pane's embedded
                      TimelineWidget from its snapshot, syncs selection
                      with that pane's tree
  settings_ops.py  - SettingsMixin: Save Settings / Load Settings --
                      persists every option/spinbox on the GUI
  context_menu_ops.py - ContextMenuMixin: File List View right-click menu
                      (Copy/Cut/Paste, Paste special, Open, Open with, Properties)
  ui_builder.py     - UiBuilderMixin: all widget/layout construction

Mixins are listed before QMainWindow in the class bases below so their
methods (e.g. resizeEvent/showEvent) take precedence over QMainWindow's
defaults in the MRO.
"""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QGroupBox

from panes import DirectoryPane
from progress import _CancellableProgress
from diff_engine import DiffEngineMixin
from hash_ops import HashOpsMixin
from listing_ops import ListingMixin
from selection_ops import SelectionMixin
from transfer_ops import TransferMixin
from sync_views import SyncMixin
from statistics_ops import TimelineOpsMixin
from settings_ops import SettingsMixin
from context_menu_ops import ContextMenuMixin
from ui_builder import UiBuilderMixin, PANE_TIMELINE_HEIGHT
from custom_widgets import install_spinbox_wheel_guard


class DirectoryComparisonApp(
    UiBuilderMixin,
    ListingMixin,
    SelectionMixin,
    TransferMixin,
    SyncMixin,
    DiffEngineMixin,
    HashOpsMixin,
    TimelineOpsMixin,
    SettingsMixin,
    ContextMenuMixin,
    QMainWindow,
):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Directory Comparison")
        # +PANE_TIMELINE_HEIGHT: since each pane grew a timeline panel of
        # roughly that height, start the window that much taller so it
        # doesn't eat into room the file list used to have.
        self.resize(2000, 800 + PANE_TIMELINE_HEIGHT)

        self.left = DirectoryPane()
        self.right = DirectoryPane()

        # Tracks an in-progress "cut" so we can clear the visual mark and
        # the system clipboard once the paste completes.
        self._cut_source_tree = None
        self._cut_items = []
        # Base directory the current clipboard contents were copied/cut
        # from, used to preserve relative subfolder structure on paste.
        self._clipboard_source_base_dir = None
        # (removed) The "Recursive Folder Contents" checkbox now lives once
        # in the shared File Filter frame -- see _build_filter_frame.
        # "Expand Folders Matching Selection" lives there too now, right of
        # "Recursive Folder Contents" -- it's only meaningful while that's on.

        # Reentrancy guard for "Synchronize Views": prevents a selection,
        # expand/collapse, or scroll change that we're propagating to the
        # other pane from bouncing back and re-triggering itself.
        self._sync_guard = False

        # Set once _build_directory_widgets runs for each side. No longer
        # used to position anything (the old floating Status/Transfer
        # group is gone -- see ui_builder._build_status_line), but kept
        # around in case a future layout piece needs a live reference to
        # each pane's Clipboard group.
        self.left_clipboard_group: QGroupBox | None = None
        self.right_clipboard_group: QGroupBox | None = None
        # Set while a move/paste is in progress, so the central Cancel
        # button has something to flag.
        self.active_transfer_progress: "_CancellableProgress | None" = None

        # Applied *before* _build_ui() rather than left for load_settings
        # (below) to handle -- _build_ui computes every custom button/
        # group-box/spinbox stylesheet from the current QPalette at
        # construction time (see ui_builder._default_button_colors and
        # its Settings-tab counterparts), so building against whatever
        # default palette Qt/the OS hands back, then only correcting the
        # *palette* itself afterward (which is all load_settings's own
        # _apply_theme_palette call does), left every one of those
        # already-baked stylesheets stuck with the wrong shade until
        # something else forced a real _rebuild_ui() -- load_settings
        # only does that when the saved theme actually *differs* from
        # this default, which it normally doesn't. Setting the palette
        # first means the very first _build_ui() already computes every
        # color from the right palette, so nothing downstream needs to
        # correct itself later.
        self._theme = "dark"
        self._apply_theme_palette(self._theme)

        self._build_ui()

        # Restore every saved option/spinbox from the last Save Settings
        # (or a previous session's automatic one) -- quiet=True since a
        # brand new install with nothing saved yet shouldn't announce
        # that on every launch (see SettingsMixin.load_settings).
        self.load_settings(quiet=True)


def main():
    app = QApplication(sys.argv)
    # Spin boxes only take the mouse wheel while focused (see
    # custom_widgets._SpinBoxWheelGuard) -- installed before the window
    # is built so every spin box passes through it.
    install_spinbox_wheel_guard()
    window = DirectoryComparisonApp()
    # print(QApplication.style().objectName())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
