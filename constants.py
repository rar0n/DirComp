"""
Shared constants for the Directory Comparison app: the clipboard MIME type
used for GNOME/Nemo/Nautilus/Dolphin interop, the custom QTreeWidgetItem
data roles that back the nested/expandable folder view, the per-tag
color table, the tag -> (severity rank, direction) lookup used by the
Select group's search buttons, the platform-dependent "changed date"
column label, and -- in the last section -- the layout/style constants
shared by ui_builder.py and the mixins split out of it.
"""

import sys

from PySide6.QtCore import Qt

GNOME_COPIED_FILES_MIME = "x-special/gnome-copied-files"

# Timeline Explorer is the old name of Statistics View.
# Scrollbar-to-timeline-view mapping resolution for each pane's embedded
# TimelineWidget -- same value Timeline Explorer's own MainWindow uses
# (SCROLLBAR_RANGE there), kept as a named constant here since it's now
# shared between ui_builder.py (building the scrollbar) and
# timeline_ops.py (converting between its value and the timeline's
# view_start/view_end).
TIMELINE_SCROLLBAR_RANGE = 100_000

# Shown in the window title: YYYY.MM.DD.N -- the date of the change, N
# counting the versions made on the same day (1, 2, ...). Bump on every batch.
APP_VERSION = "2026.11.21.7"

# st_ctime means different things on different platforms (same split as
# Timeline Explorer's file_scanner.py, which this mirrors so the two
# apps describe the same column the same way):
#  - Linux/macOS (POSIX): "metadata changed" time (renamed, chmod'd,
#    written to, etc) -- NOT a creation/birth time. Python's os.stat()
#    has no portable way to get a real birth time on Linux.
#  - Windows: st_ctime IS the file's creation time.
IS_WINDOWS = sys.platform.startswith("win")
CHANGED_FIELD_IS_CREATION = IS_WINDOWS
CHANGED_FIELD_LABEL = "Created" if IS_WINDOWS else "Changed (ctime)"

# The diff-outcome entries below (everything but "directory"/"file"/
# "error") are what the Settings tab's "Comparison type colors (File
# List View)" table edits, one file entry and one folder entry per
# comparison type -- independent of the Select-group button colors, but
# seeded with the very same (bg, fg) values as custom_widgets.
# _DEFAULT_SELECT_COLORS for the matching button, for files and folders
# alike (which is also what "Reload Defaults" restores them to -- see
# UiBuilderMixin._reset_to_defaults (theme_ops.py) -- so a fresh start and a reset look
# the same).
# Duplicate Finder summary (under the file lists): label and default color per
# category. Stored per theme in the same dict as the Comparison Type summary
# colors (DirectoryComparisonApp._summary_colors); missing keys fall back here.
DUP_SUMMARY_TYPES = {
    "dup_groups": ("Groups", "#F0D080"),
    "dup_files": ("Duplicate files", "#F0C060"),
    "dup_folders": ("Folders", "#9CC8F0"),
    "dup_wasted": ("Wasted space", "#E58E8E"),
}

TAG_COLORS = {
    # tag: (background, foreground)
    "common_file": ("#90E0C0", "black"),
    "common_dir": ("#90E0C0", "black"),
    "missing_file": ("#FFA0A0", "black"),
    "missing_dir": ("#FFA0A0", "black"),
    "diff_size_smaller_file": ("#D9BCF0", "black"),
    "diff_size_larger_file": ("#B98CE0", "black"),
    "diff_date_newer_file": ("#DECFA3", "black"),
    "diff_date_older_file": ("#C9A96E", "black"),
    "diff_count_smaller_dir": ("#D9BCF0", "black"),
    "diff_count_larger_dir": ("#B98CE0", "black"),
    "diff_date_newer_dir": ("#DECFA3", "black"),
    "diff_date_older_dir": ("#C9A96E", "black"),
    "diff_content_dir": ("#7777DD", "blue"),
    # Hash comparison: same-name files whose content differs while size
    # and date agree (the file counterpart of diff_content_dir).
    "diff_content_file": ("#7777DD", "blue"),
    "directory": ("#E0E090", "blue"),
    "file": (None, None),
    # "Ignore hard links": a file with more than one hard link, left out of
    # every comparison (Settings -> File List View Colors -> "Hard link").
    "hardlink_file": ("#D8C0E8", "black"),
    # Duplicate Finder mode (Compare folders off): a folder with no files
    # anywhere below it (Settings -> File List View Colors -> "Empty folder").
    "empty_dir": (None, "blue"),  # no Bg: the file list's own (Duplicate Finder mode only)
    # Compare folders mode: a matching / uncompared folder with no files
    # anywhere below it (display only -- the stored tag, and so the Select
    # buttons, stay as they were; see ListingMixin._display_tag).
    "empty_dir_cmp": ("#D4DCE8", "blue"),
    # Duplicate Finder mode's plain folder: no background of its own (the
    # file list's), so it does not read as a highlighted find.
    "dupfolder": (None, "blue"),
    "error": ("#FFB6C1", "red"),
    # Hash comparison types only: a file/folder that has no hash yet (see
    # Settings -> Comparison Type Colors -> "Unhashed"). Neutral greys.
    "unhashed_file": ("#C8C8C8", "black"),
    "unhashed_dir": ("#B0B0B0", "blue"),
    # Hash comparison types: a file whose cached hash is out of date (the
    # size or date changed since it was hashed) and folders holding one.
    "changed_file": ("#E8C888", "black"),
    "changed_dir": ("#D4A85C", "blue"),
    # Hash comparison types: hashed, but with nothing to compare against
    # (the other pane is empty, or Dual Pane is off).
    "hashed_file": ("#B8E0E8", "black"),
    "hashed_dir": ("#9CCBD6", "blue"),
}

# Custom item-data roles (stored on column 0) that support the nested,
# expandable folder view: an item's absolute path, whether it's a
# directory, the absolute path of its counterpart on the other pane (used
# for diff coloring at any depth, not just the top level), and whether its
# children have been lazily populated yet.
PATH_ROLE = Qt.UserRole + 1
IS_DIR_ROLE = Qt.UserRole + 2
OTHER_DIR_ROLE = Qt.UserRole + 3
POPULATED_ROLE = Qt.UserRole + 4

# Maps a leaf diff tag to (severity rank, direction), used to check whether
# an item matches a given Select button by tag instead of by cross-tree name
# lookup. Note "missing" is rank 3 here but folders never actually store
# that rank internally for aggregate/recursive comparison (see
# _dir_content_rank) -- only a literal "this doesn't exist on the other
# side at all" uses "missing_dir"/"missing_file".
#
# Priority order when an item differs from its counterpart in more than
# one way at once (e.g. both larger AND older): Missing > Newer/Older >
# Smaller/Larger > Matching -- so rank 2 (date) outranks rank 1 (size),
# same as the top-level severity 3 (missing) outranks both. See
# diff_engine._diff_tag/_file_diff_rank, which apply this same priority
# when deciding which single tag an item that differs both ways gets.
# Which snapshot date field (mtime/ctime/atime) a given Statistics-view
# Metric corresponds to -- used to tie diff coloring's Newer/Older
# comparison (and the Select group's own Newer/Older searches) to
# whichever date type a pane's Metric combo currently has selected,
# instead of always comparing Modified regardless of what's actually
# being plotted. "size" has no date of its own and isn't a key here --
# every caller falls back to "mtime" (Modified) for it, the same
# fallback responsive_layout.py's own _METRIC_TO_DATE_COLUMN already uses for
# the narrow-layout single collapsed date column, for the same reason.
# See statistics_ops.TimelineOpsMixin._pane_diff_date_field.
METRIC_DATE_FIELDS = {"modified": "mtime", "changed": "ctime", "accessed": "atime"}

_DIFF_RANK = {
    "common_file": (0, None), "common_dir": (0, None),
    "directory": (0, None), "file": (0, None),
    "diff_size_smaller_file": (1, "smaller"), "diff_size_larger_file": (1, "larger"),
    "diff_count_smaller_dir": (1, "smaller"), "diff_count_larger_dir": (1, "larger"),
    "diff_date_newer_file": (2, "newer"), "diff_date_older_file": (2, "older"),
    "diff_date_newer_dir": (2, "newer"), "diff_date_older_dir": (2, "older"),
    "missing_file": (3, None), "missing_dir": (3, None),
}


# ------------------------------------------------------------------
# UI layout / style constants -- shared by ui_builder.py, settings_tab.py,
# pane_builder.py and responsive_layout.py (all one UiBuilderMixin at
# runtime). Plain literals only, so this file stays free of imports
# from the rest of the app.
# ------------------------------------------------------------------

# No longer a fixed height for each pane's embedded TimelineWidget --
# its height now tracks the draggable File List/Statistics splitter
# (see _build_directory_widgets; TimelineWidget sets its own 65px
# minimum height itself, in statistics_widget.py). Kept only as the
# mirrored/right-pane layout math elsewhere in this file still refers
# to it.
PANE_TIMELINE_HEIGHT = 220

# How far the window is allowed to shrink. The button groups' own
# dynamic wrapping (see responsive_layout.py) keeps working all the way
# down to this width; the other widgets' own minimum size hints can still
# keep the window from actually getting this small.
MINIMUM_WINDOW_WIDTH = 800

# Explicit disabled-state color for the Global Options dependent
# checkboxes (Synchronize Views/Symmetrical GUI/Expand To Selection/
# Sort Directories First) -- the app's hand-built dark/light QPalette
# doesn't visibly distinguish its Disabled color group from
# Active/Inactive, so a disabled checkbox's label reads the same as an
# enabled one without this. Applied directly rather than fixed at the
# palette level, since that palette isn't built in this file.
_DISABLED_CHECKBOX_STYLE = "QCheckBox:disabled { color: #7A7A7A; }"
# Gap (px) between the two panes (and, to keep Global File Filter's right
# edge on the left pane's right edge, between the bottom row's two halves).
_PANE_GAP = 6
# Vertical gap (px) between button groups stacked on top of each other
# inside a pane's cluster (see responsive_layout._apply_cluster_arrangement).
_CLUSTER_V_GAP = 4
# Fixed gap (px) between the Select/File Op/Clipboard groups inside a
# pane's cluster row (see responsive_layout._apply_cluster_arrangement). Fixed rather than
# stretchy so both panes always show identical gaps regardless of how
# wide each pane currently is -- the leftover width goes to one edge
# stretch instead, on whichever side the row is aligned away from. 0:
# the groups sit as close together as they can (their own frames
# touching); raise it for breathing room between them.
_CLUSTER_GROUP_GAP = 0
# Left/right padding between a button group's frame and its buttons (the
# style's default is 9 px plus the frame) -- kept small so neighbouring
# groups' buttons sit close together.
_CLUSTER_GROUP_PAD = 3
# Left/right margin inside each pane (the style's default is 9 px); together
# with _PANE_GAP it sets how far apart the two panes' contents sit.
_PANE_SIDE_MARGIN = 3
# Settings tab's small color-table column headers (Item/Fg/Bg/...) --
# bold alone still read at the same point size as the rest of the row,
# so a size bump goes here too, distinct from (and smaller than) the
# section headers below.
_TABLE_HEADER_STYLE = "font-size: 11pt; font-weight: bold;"
# Settings tab's section headers -- rendered as a real QLabel above
# each (now title-less) QGroupBox rather than through QGroupBox's own
# native title text. font-size on QGroupBox::title turned out not to
# be respected by whatever Qt style is active here even at 18pt (see
# _build_settings_tab's now-vestigial page-level stylesheet), so this
# sidesteps that platform quirk entirely -- a plain QLabel reliably
# honors font-size via QSS the way the small table headers above
# already do.
_SECTION_HEADER_PT = 14
_SECTION_HEADER_STYLE = f"font-size: {_SECTION_HEADER_PT}pt; font-weight: bold;"
# Settings tab's smaller sub-headers -- above an individual table within
# a larger section (e.g. "Select Colors" under the "Button Colors"
# section) rather than a whole section on its own.
_SUBTITLE_PT = 12
_SUBTITLE_STYLE = f"font-size: {_SUBTITLE_PT}pt; font-weight: bold;"
# Settings tab title levels: 1 = section (_SECTION_HEADER_STYLE, 14pt),
# 2 = subsection (_SUBTITLE_STYLE, 12pt), 3 = sub-subsection (this one,
# 10pt -- still bold, so it reads as a heading against the normal-weight
# controls around it). Every title has an empty band above it as tall as
# the title itself (see _settings_titled_block).
_SUBSUBTITLE_PT = 10
_SUBSUBTITLE_STYLE = f"font-size: {_SUBSUBTITLE_PT}pt; font-weight: bold;"
# Horizontal gap between Level 2 blocks side by side in a row.
_SUBSECTION_GAP = 16
# Same issue, for the Opacity spinbox (only meaningful -- and only
# enabled -- while Transparent Selection Highlight is on).
_DISABLED_SPINBOX_STYLE = "QSpinBox:disabled { color: #7A7A7A; }"
# File List View tree indentation (px per nesting level) -- Settings tab ->
# File List View -> "Indentation". 20 is what Qt's own QTreeView default
# comes to (nothing in this app overrode it before this setting existed),
# so the default leaves the tree looking exactly as it always has.
_DEFAULT_TREE_INDENTATION = 20
# Lower bound keeps the expand/collapse arrow column (drawn one indent wide
# -- see DirDropTreeWidget.drawBranches) at least barely usable, and
# never 0, which DirDropTreeWidget._line_at would divide by.
_MIN_TREE_INDENTATION, _MAX_TREE_INDENTATION = 4, 100


# Comparison type (Settings tab -> Global Options). The combo box's item
# order IS the saved value (settings_ops saves a combo by index), so only
# ever append. Hash value and Fingerprint have no hashes to compare yet
# (hashing is a later step), so they currently behave exactly like
# CMP_NAME_SIZE_DATE -- see DiffEngineMixin._comparison_key.
CMP_NAME_SIZE_DATE, CMP_HASH, CMP_FINGERPRINT = 0, 1, 2
# Added later, so their ids come after the saved ones (settings and
# snapshots store the id, not the position in the drop-down):
CMP_NAME_SIZE, CMP_NAME = 3, 4
# (id, label) in the order the drop-downs show them.
COMPARISON_TYPE_ITEMS = (
    (CMP_NAME_SIZE_DATE, "Name, size and date"),
    (CMP_NAME_SIZE, "Name and size"),
    (CMP_NAME, "Name"),
    (CMP_HASH, "Hash value"),
    (CMP_FINGERPRINT, "Fingerprint"),
)
# Same rule for the algorithm combo (index saved). SHA-256 is the default.
HASH_ALGORITHMS = ("MD5", "SHA-1", "SHA-256", "BLAKE2b")
DEFAULT_HASH_ALGORITHM = 2
# Fingerprint = file size + this many kB from the start + this many from the end.
DEFAULT_FINGERPRINT_CHUNK_KB = 64
MIN_FINGERPRINT_CHUNK_KB, MAX_FINGERPRINT_CHUNK_KB = 1, 1024
