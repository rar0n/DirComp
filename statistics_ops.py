"""
TimelineOpsMixin: wires each pane's embedded TimelineWidget (see
ui_builder.UiBuilderMixin._build_statistics_panel) to that pane's
in-memory snapshot (DiffEngineMixin._build_snapshot) and to that pane's
tree selection -- the same bidirectional-selection pattern Timeline
Explorer's own MainWindow uses between its TimelineWidget and its
FileTable (see main_window.py), just adapted to read files out of a
snapshot node tree instead of a flat FileRecord list, and to select
paths in a QTreeWidget instead of table rows.

DirComp's snapshot nodes are plain dicts (name/full_path/is_dir/mtime/
ctime/atime/size/children -- see _build_snapshot), not FileRecord
objects, so this bypasses TimelineWidget.set_records()'s built-in
attribute-lookup defaults entirely via its value_of/path_of parameters
rather than constructing FileRecord instances just to satisfy them.

The Statistics view plots one of two things at a time, per pane
(pane.tl_folder_mode, toggled via the Files/Folders buttons next to its
title -- see ui_builder._build_directory_widgets): files (the default),
or folders. Never both at once -- a folder's own recursive-total size
would otherwise sit right next to the very files it's the sum of,
double-counting the same bytes twice on the same chart. In Folder mode,
the pane's own opened root folder is deliberately excluded from the
plotted points -- see _flatten_folders -- since its size is always
100% of everything else in the pane combined, and its own mtime just
means "something directly inside it last changed"; neither is
comparable to any other folder in the same chart. This does mean the
timeline's own total will generally read lower than the corresponding
figure in pane.total_items_label/pane.count_label, by design -- it's
never showing both files and folders at once the way those do.

File mode and Folder mode also keep entirely independent Statistics-
view drag-range overlays (see pane.tl_mode_selections and
_on_timeline_mode_changed) -- the discrete file/folder selection
itself is shared, persistent tree state instead, not tracked per mode
at all."""

from __future__ import annotations

import os
import time

from PySide6.QtWidgets import QAbstractItemView

from panes import DirectoryPane
from constants import PATH_ROLE, IS_DIR_ROLE, TIMELINE_SCROLLBAR_RANGE, METRIC_DATE_FIELDS


def _value_of_for_metric(metric: str):
    """The value_of callable TimelineWidget needs for `metric`, reading
    straight out of a snapshot node dict instead of a FileRecord
    attribute -- see set_records()'s own default lambdas, which this
    replaces rather than extends. mtime/ctime/atime are already plain
    epoch floats in the snapshot (see _build_snapshot), so unlike
    set_records()'s own "modified"/"changed"/"accessed" defaults, no
    getattr(...).timestamp() conversion is needed."""
    if metric == "size":
        return lambda r: float(r["size"] or 0)
    field = METRIC_DATE_FIELDS.get(metric, "mtime")
    return lambda r, f=field: r[f]


def _size_of(r):
    """size_of callable for TimelineWidget.set_records() -- the record's
    own byte size, independent of whichever metric is currently being
    plotted (unlike value_of, which *is* the size when metric == "size"
    but a date otherwise). Used for the widget's own selected/total-size
    readout (see TimelineWidget's docstring), which always means actual
    file size regardless of what's on the x-axis."""
    return r["size"]


def _path_of(r):
    return r["full_path"]


class TimelineOpsMixin:
    # ---------------------------------------------------------- data feed
    def _pane_diff_date_field(self, pane: DirectoryPane | None) -> str:
        """Which snapshot date field (mtime/ctime/atime) diff coloring's
        Newer/Older comparison -- and the Select group's own Newer/Older
        searches -- use for `pane`'s own side of the comparison. Tied to
        that pane's Statistics-view Metric combo, so "Newer"/"Older"
        always means the same date type the Timeline is currently
        plotting for that pane, instead of always meaning Modified
        regardless of what's actually selected there.

        Falls back to "mtime" (Modified) both when the Metric is "size"
        (no date of its own) and when `pane` or its combo isn't
        available yet (very early construction, or a caller that hasn't
        got a specific pane to ask) -- the same fallback ui_builder.py's
        own _METRIC_TO_DATE_COLUMN already uses for the narrow-layout's
        single collapsed date column, for the same reason."""
        combo = getattr(pane, "tl_metric_combo", None) if pane is not None else None
        metric = combo.currentData() if combo is not None else None
        return METRIC_DATE_FIELDS.get(metric or "modified", "mtime")

    def _snapshot_node_visible(self, node) -> bool:
        """Whether `node` would actually be listed in the tree at all --
        List Files/List Directories and the name filter (+
        Invert, Ignore case), the exact same checks ListingMixin._build_entries
        applies. Used while walking the raw snapshot directly
        (_flatten_files, _pane_folder_count's recursive branch) so
        recursive-mode results match "the file list view (minus
        folders)" the same way non-recursive mode already gets for
        free by reading real, already-filtered tree items (see
        _pane_timeline_records's non-recursive branch) -- without this,
        Recursive Folder Selections being on meant the timeline (and
        its totals) silently ignored every one of these filters,
        showing files the file list itself was hiding."""
        name_filter = self._name_filter()
        if node["is_dir"]:
            return self.list_directories_cb.isChecked() and name_filter.dir_passes(node)
        if not self.list_files_cb.isChecked():
            return False
        return name_filter.name_passes(node["name"])

    def _flatten_files(self, node):
        """Yield every file (non-directory) node in `node`'s subtree
        that would actually be listed in the tree (see
        _snapshot_node_visible), depth-first -- `node` itself if it's a
        visible file, otherwise just recursion into its children
        (folders contribute nothing of their own -- see module
        docstring -- and are recursed into regardless of their own
        List Directories visibility, so hiding folder *rows* doesn't
        also hide the files inside them).

        Iterative (an explicit stack), not a recursive generator -- a
        directory tree deep enough (a stray node_modules/.git/venv, deep
        build output, ...) can chain enough `yield from` delegations to
        threaten the real C stack in a way Python's own recursion limit
        doesn't reliably catch first: each `yield from` frame stays
        alive on the stack for the whole pull-through, unlike an
        ordinary function call, so depth here adds up on top of
        whatever stack Qt's own signal dispatch already has at the
        moment this runs (a checkbox toggle, a Refresh click, ...). An
        explicit stack has no such limit, however deep the tree goes."""
        if node is None:
            return
        stack = [node]
        while stack:
            current = stack.pop()
            if not current["is_dir"]:
                if self._snapshot_node_visible(current):
                    yield current
                continue
            stack.extend((current["children"] or {}).values())

    def _flatten_folders(self, node):
        """Yield every directory node in `node`'s subtree that would
        actually be listed in the tree (see _snapshot_node_visible),
        excluding `node` itself. Folder-mode counterpart of
        _flatten_files: each directory contributes its own point --
        its own mtime/ctime/atime and its own recursive total size,
        both already sitting on the node from _build_snapshot, no
        separate computation needed -- not its children's. Every
        subdirectory is still walked into regardless of its own List
        Directories visibility, the same "hiding a folder's row
        doesn't hide what's nested inside it" rule _flatten_files
        already follows for files.

        `node` itself is never yielded, deliberately: it's the pane's
        own opened directory, whose size is always the full recursive
        total (100% of everything else in the pane combined) and whose
        mtime just means "something directly inside it last changed" --
        neither is really comparable to any other folder in the same
        chart, so including it would just be one permanent,
        uninformative outlier rather than a real data point.

        Iterative, same reasoning as _flatten_files -- see there."""
        if node is None:
            return
        stack = list((node["children"] or {}).values())
        while stack:
            current = stack.pop()
            if not current["is_dir"]:
                continue
            if self._snapshot_node_visible(current):
                yield current
            stack.extend((current["children"] or {}).values())

    def _pane_timeline_records(self, pane: DirectoryPane) -> list:
        """The nodes the timeline should currently show for `pane` --
        files or folders, depending on pane.tl_folder_mode (see the
        Files/Folders toggle in _build_directory_widgets) -- respects
        "Recursive Folder Selections" the same way the Select group's
        own search buttons do (see _run_select/_iter_visible_items):

        - On: every matching node anywhere in the snapshot (full
          recursive flatten via _flatten_files/_flatten_folders),
          regardless of what's actually expanded on screen.
        - Off: only nodes that are actually visible right now -- the
          root folder's own immediate entries, plus (recursively) the
          contents of whichever folders have actually been manually
          expanded, same "visible (as in expanded in the file list
          view)" semantics _iter_visible_items defines. A folder
          collapsed back down stops contributing its entries here
          again, exactly like it stops counting toward the Select
          group's non-recursive searches.

        Folder mode never yields the pane's own root folder -- see
        _flatten_folders -- but that exclusion is moot in non-recursive
        mode anyway: the tree never has a row for its own root to begin
        with, only the root's contents."""
        if pane.snapshot_root is None:
            return []
        folder_mode = pane.tl_folder_mode
        if self._recursive_folder_contents_enabled():
            if folder_mode:
                return list(self._flatten_folders(pane.snapshot_root))
            return list(self._flatten_files(pane.snapshot_root))
        records = []
        index = pane.snapshot_index or {}
        for item in self._iter_visible_items(pane.tree):
            if bool(item.data(0, IS_DIR_ROLE)) != folder_mode:
                continue
            path = item.data(0, PATH_ROLE)
            if not path:
                continue
            node = index.get(os.path.normpath(path))
            if node is not None:
                records.append(node)
        return records

    def _pane_folder_count(self, pane: DirectoryPane) -> int:
        """Folder count in the same scope Folder mode uses (see
        _flatten_folders/_pane_timeline_records) -- shown in the
        totals row (_update_timeline_totals_label) regardless of which
        mode is actually active, so switching the Files/Folders toggle
        never changes what the two counts themselves read."""
        if pane.snapshot_root is None:
            return 0
        if self._recursive_folder_contents_enabled():
            return sum(1 for _ in self._flatten_folders(pane.snapshot_root))
        return sum(
            1 for item in self._iter_visible_items(pane.tree)
            if item.data(0, IS_DIR_ROLE)
        )

    def _pane_file_count(self, pane: DirectoryPane) -> int:
        """File-mode counterpart of _pane_folder_count -- see there."""
        if pane.snapshot_root is None:
            return 0
        if self._recursive_folder_contents_enabled():
            return sum(1 for _ in self._flatten_files(pane.snapshot_root))
        return sum(
            1 for item in self._iter_visible_items(pane.tree)
            if not item.data(0, IS_DIR_ROLE)
        )

    def _update_timeline_totals_label(self, pane: DirectoryPane):
        """Refresh the "Files: X - Folders: Y" counts next to the
        Files/Folders toggle -- see _build_directory_widgets. Always
        both counts, independent of pane.tl_folder_mode, and computed
        fresh (_pane_file_count/_pane_folder_count) rather than read
        off whatever's currently loaded into the chart
        (timeline.records): now that Folder mode exists, records only
        ever holds ONE of the two, so len(timeline.records) alone can
        no longer stand in for "the file count" regardless of which
        mode happens to be active."""
        label = getattr(pane, "tl_totals_label", None)
        if label is None:
            return
        file_count = self._pane_file_count(pane)
        folder_count = self._pane_folder_count(pane)
        # Numeric values get the Settings tab's "Title Numeric Colors"
        # swatch (self._title_numeric_color/_title_numeric_bold) via
        # _numeric_span, applied via an explicit <span> -- see
        # ui_builder._numeric_span's docstring; the surrounding "Files:"/
        # "Folders:" text is left with no color of its own so it always
        # follows the theme's normal text color.
        # "Files: X" and "Folders: Y" are the two units the label can
        # wrap between (see SelectionMixin._render_stats_totals).
        nbsp = "\u00a0"
        pane._stats_atoms = [
            (f"Files:{nbsp}{file_count}", f"Files:{nbsp}{self._numeric_span(file_count)}"),
            (f"Folders:{nbsp}{folder_count}", f"Folders:{nbsp}{self._numeric_span(folder_count)}"),
        ]
        self._render_stats_totals()

    def _refresh_pane_timeline(self, pane: DirectoryPane):
        """(Re)load `pane`'s timeline from its current snapshot, in
        whichever mode (files/folders) is currently active -- see
        pane.tl_folder_mode/_pane_timeline_records. Called whenever the
        snapshot is rebuilt -- see ListingMixin._refresh_pane_snapshot --
        so the timeline never shows a stale listing after a folder's
        opened/refreshed/navigated."""
        timeline = getattr(pane, "timeline", None)
        if timeline is None:
            return
        metric = pane.tl_metric_combo.currentData() if pane.tl_metric_combo else "modified"
        metric = metric or "modified"
        timeline.set_records(
            self._pane_timeline_records(pane),
            metric=metric,
            value_of=_value_of_for_metric(metric),
            path_of=_path_of,
            size_of=_size_of,
            item_label="folder" if pane.tl_folder_mode else "file",
        )
        self._update_statistics_bin_size_label(pane)
        self._update_timeline_totals_label(pane)

    def _on_timeline_mode_changed(self, pane: DirectoryPane, folder_mode: bool):
        """Files/Folders toggle next to the Statistics view title (see
        _build_directory_widgets): switches what pane.tl_folder_mode
        means for every other Timeline-related method
        (_pane_timeline_records, _sync_timeline_selection, ...) and
        reloads the chart to match. Each button's own toggled connection
        only calls this when *that* button is becoming checked (see
        _build_directory_widgets) -- QButtonGroup's exclusivity always
        fires toggled on both buttons for a single click (one turning
        off, one turning on), so without that this would run twice, once
        with the stale mode. The equality check below is just a cheap
        extra guard against a redundant reload if it's ever called with
        the mode that's already active.

        File mode and Folder mode keep entirely independent Statistics
        view drag-range overlays (see pane.tl_mode_selections) -- the
        one being left gets stashed, the one being entered gets whatever
        was last stashed for it (nothing, the first time). The *discrete*
        selection isn't stashed/restored this way at all, on either
        side: it's read fresh from the tree itself, both here and in
        _combined_mode_selection_paths, since the tree is the one
        genuinely persistent record of "what's selected" across both
        modes at once -- it already holds both files and folders
        selected simultaneously without issue, and nothing here ever
        needs to replace or rebuild its selection, just recolor the
        chart to whichever of it belongs to the mode just entered (see
        _sync_timeline_selection below). A stashed *paths* snapshot,
        by contrast, only reflects what the timeline itself last
        applied to the tree -- it goes stale the moment something gets
        selected directly in the File List View (or via the Select
        group) while a *different* mode's chart happens to be the one
        on screen, since nothing else ever touches that stash. Trusting
        it here used to mean: select a file while Folder mode's chart
        is showing, switch to File mode, and that fresh tree selection
        would vanish -- replaced outright by whatever File mode's stash
        last remembered, which had no way of knowing about it."""
        if pane.tl_folder_mode == folder_mode:
            return
        timeline = pane.timeline
        old_key = "folder" if pane.tl_folder_mode else "file"
        new_key = "folder" if folder_mode else "file"
        pane.tl_mode_selections[old_key] = {"range": timeline.selection_range}
        pane.tl_folder_mode = folder_mode
        self._apply_stats_palette(pane)
        self._refresh_pane_timeline(pane)
        saved_range = (pane.tl_mode_selections.get(new_key) or {}).get("range")
        timeline.set_external_selection_range(saved_range)
        # Re-lights whichever of the tree's *already-selected* items
        # belong to the mode just entered -- same computation (and the
        # same guard against a mid-rebuild tree) a plain manual tree
        # selection change always triggers on its own; nothing about
        # the tree's own selection needs restoring or replacing here,
        # only redisplaying under the new chart. update_selection_count
        # isn't called here either, for the same reason: the tree's
        # selection -- and so the count of it -- doesn't change on a
        # mode switch, only which part of it the chart is showing.
        self._sync_timeline_selection(pane)

    # -------------------------------------------------------- color palette
    def _stats_palette_for(self, pane: DirectoryPane) -> dict:
        """The color settings (Settings tab -> Statistics View Colors)
        for whichever mode `pane`'s timeline is currently plotting --
        see self._stats_colors (ui_builder.UiBuilderMixin's own
        default/state dict, "file"/"folder" -> the same keyword names
        TimelineWidget.set_palette takes, background_color included --
        background is per-mode too, not a value shared across both)."""
        key = "folder" if pane.tl_folder_mode else "file"
        return self._stats_colors[key]

    def _apply_stats_palette(self, pane: DirectoryPane):
        """Push the current color settings onto `pane`'s timeline --
        whichever mode's whole palette (background included) is
        currently active (see _stats_palette_for). Called at
        construction, on every mode switch, and by every Statistics
        View Colors swatch/opacity handler in ui_builder.py."""
        timeline = getattr(pane, "timeline", None)
        if timeline is None:
            return
        timeline.set_palette(**self._stats_palette_for(pane))

    def _on_tree_expand_collapse_for_timeline(self, pane: DirectoryPane):
        """Called on every itemExpanded/itemCollapsed for `pane`'s tree
        (see ui_builder._build_directory_widgets). Only actually
        recomputes anything while Recursive Folder Selections is off --
        that's the only mode where the timeline's contents depend on
        expand state at all (see _pane_timeline_records); recursive
        mode always shows the whole snapshot regardless, so skipping
        the rebuild here avoids redoing that same flatten + set_records
        on every single expand/collapse click while browsing a fully-
        recursive, already-fully-shown timeline.

        Also skipped while pane._tree_rebuild_pending is set (see
        listing_ops.update_listing): _refresh_pane_timeline's non-
        recursive branch walks the tree via _iter_visible_items, and
        reaching this reentrant -- a leftover, already-scheduled Qt
        event/timer getting a chance to run during the rebuild's own
        progress-driven processEvents() pumping -- would do that while
        the tree is either already cleared or still only half-
        repopulated. _refresh_pane_snapshot already calls
        _refresh_pane_timeline itself once the rebuild's snapshot is
        actually ready, so this isn't needed to catch up.

        Also skipped while pane._bulk_expand_pending is set (see
        listing_ops._set_all_folders_state) -- a bulk Expand All/
        Collapse All walk fires this signal once per folder it touches,
        and recomputing the full flatten every single time would make
        the whole walk O(n^2) over a big tree; that walk drives its own
        throttled refresh directly instead (see
        _maybe_refresh_pane_timeline_throttled below)."""
        if self._recursive_folder_contents_enabled() or getattr(pane, "_tree_rebuild_pending", False):
            return
        if getattr(pane, "_bulk_expand_pending", False):
            return  # bulk walk in progress -- see docstring above
        self._refresh_pane_timeline(pane)

    _BULK_TIMELINE_REFRESH_INTERVAL = 0.2  # seconds

    def _maybe_refresh_pane_timeline_throttled(self, pane: DirectoryPane):
        """Time-gated stand-in for _refresh_pane_timeline, for a bulk
        expand walk (see listing_ops._set_all_folders_state) that would
        otherwise want to call it once per folder -- an O(visible-items)
        flatten repeated enough times to make the whole walk O(n^2).
        Same time-based-rather-than-count-based throttling philosophy as
        progress._CancellableProgress's own pump()/on_tick intervals,
        and for the same reason: per-item populate cost varies (a
        directory needing real filesystem stats vs. one already cached),
        so gating on wall-clock time keeps the live-updating look
        feeling continuous without tying its rate to how expensive any
        particular folder turns out to be. Doesn't attempt its own
        cancellation/pumping -- the walk's own progress token already
        handles that. The caller is still responsible for one final,
        unthrottled _refresh_pane_timeline call once its walk is done,
        so the last state actually shown is always accurate rather than
        wherever this last happened to land."""
        now = time.monotonic()
        last = getattr(pane, "_tl_last_bulk_refresh", None)
        if last is not None and now - last < self._BULK_TIMELINE_REFRESH_INTERVAL:
            return
        pane._tl_last_bulk_refresh = now
        self._refresh_pane_timeline(pane)

    def _on_statistics_metric_changed(self, pane: DirectoryPane, _index: int = 0):
        """Metric combo changed: re-bucket the records already loaded
        (no need to re-flatten the snapshot -- same files, just a
        different axis/value extractor).

        This Metric is also what _pane_diff_date_field ties Newer/Older
        diff coloring (and the Select group's own Newer/Older searches)
        to, so a change here can flip which files in *this pane's own
        tree* should show as Newer vs. Older against their counterpart.
        update_listings() re-derives that per pane fresh (see
        _retag_listing_tree), and since nothing on disk actually
        changed, both panes take the cheap retag-only path rather than a
        full rescan -- see listing_ops.py's module docstring. It also
        retags the *other* pane, which is unaffected by this pane's own
        Metric; harmless, just a little more work than strictly needed
        for what's a rare, deliberate settings change, not a hot path.

        With Synchronize Views on, also mirrors this Metric onto the
        other pane's combo (see _propagate_metric_to_other_pane) so the
        two panes' idea of "Newer"/"Older" can't quietly disagree --
        that mirrored change re-enters this same method for the other
        pane, which is exactly what re-buckets/retags it too."""
        timeline = pane.timeline
        metric = pane.tl_metric_combo.currentData()
        timeline.set_records(
            timeline.records, metric=metric,
            value_of=_value_of_for_metric(metric), path_of=_path_of, size_of=_size_of,
        )
        self._update_statistics_bin_size_label(pane)
        self.update_listings()
        self._propagate_metric_to_other_pane(pane)

    def _propagate_metric_to_other_pane(self, pane: DirectoryPane):
        """With Synchronize Views on, keep both panes' Statistics-view
        Metric combos matched -- since Newer/Older is now tied to
        Metric (_pane_diff_date_field), the two panes disagreeing about
        which date type "Newer"/"Older" means would make a cross-pane
        comparison (e.g. Larger on the left vs. its Smaller counterpart
        on the right, or Synchronize Views' own mirroring generally)
        compare against two different date fields without anything on
        screen indicating that's happening. Whichever pane's Metric was
        just changed -- by the user directly, or by this same method
        having just run for the *other* pane a moment ago -- wins.

        self._sync_guard (the same reentrancy guard Synchronize Views'
        selection/expand/scroll mirroring uses -- see sync_views.py)
        stops the second case from bouncing back and forth
        between the two combos forever: setting the other combo below
        fires its own currentIndexChanged, which re-enters
        _on_statistics_metric_changed for that pane and calls back into
        this method -- with the guard set, that second call just no-ops
        instead of trying to mirror the change straight back."""
        if not self._synchronize_views_enabled() or self._sync_guard:
            return
        other_pane = self.right if pane is self.left else self.left
        combo = getattr(pane, "tl_metric_combo", None)
        other_combo = getattr(other_pane, "tl_metric_combo", None)
        if combo is None or other_combo is None:
            return
        if other_combo.currentIndex() == combo.currentIndex():
            return
        self._sync_guard = True
        try:
            other_combo.setCurrentIndex(combo.currentIndex())
        finally:
            self._sync_guard = False

    def _on_synchronize_views_toggled_for_metric(self, *_args):
        """Synchronize Views checkbox toggled: if it's now on, immediately
        copy the left pane's Statistics-view Metric onto the right pane,
        rather than leaving the two to silently disagree until the next
        time either one's Metric combo happens to change (see
        _propagate_metric_to_other_pane for that ongoing "whoever
        changed last wins" case, which takes over from here). Left
        always wins at this moment -- arbitrary, but consistent, and
        matches this method only ever needing to run in one direction.
        No-ops on toggling Synchronize Views off, and on any signal
        arguments -- state is re-read via _synchronize_views_enabled()
        rather than trusted from whatever stateChanged happened to pass,
        the same as this checkbox's own other handler already does."""
        if not self._synchronize_views_enabled():
            return
        left_combo = getattr(self.left, "tl_metric_combo", None)
        right_combo = getattr(self.right, "tl_metric_combo", None)
        if left_combo is None or right_combo is None:
            return
        if right_combo.currentIndex() == left_combo.currentIndex():
            return
        self._sync_guard = True
        try:
            right_combo.setCurrentIndex(left_combo.currentIndex())
        finally:
            self._sync_guard = False

    def _update_statistics_bin_size_label(self, pane: DirectoryPane, *_args):
        label = pane.timeline.current_bin_step_label()
        pane.tl_bin_size_label.setText(f"Bin size: {label}" if label else "")

    # -------------------------------------------------------- scrollbar sync
    def _on_timeline_view_range_changed(self, pane: DirectoryPane, view_start: float, view_end: float):
        """Timeline panned/zoomed -> reposition its scrollbar thumb to
        match, same math as Timeline Explorer's own MainWindow. Guarded
        by pane._tl_syncing_scrollbar so the setValue() below (which
        would otherwise fire _on_statistics_scrollbar_moved right back)
        doesn't loop."""
        timeline = pane.timeline
        if timeline.global_start is None:
            return
        pane._tl_syncing_scrollbar = True
        g0, g1 = timeline.global_start, timeline.global_end
        full_span = max(g1 - g0, 1e-6)
        pos = (view_start - g0) / full_span
        page = (view_end - view_start) / full_span
        sb = pane.tl_scrollbar
        sb.blockSignals(True)
        # All values (e.g. identical timestamps) can make full_span tiny
        # while the view is wider: clamp so the ints stay in range.
        page = min(max(page, 0.0), 1.0)
        pos = min(max(pos, 0.0), 1.0)
        sb.setPageStep(max(1, int(page * TIMELINE_SCROLLBAR_RANGE)))
        sb.setValue(int(pos * TIMELINE_SCROLLBAR_RANGE))
        sb.blockSignals(False)
        pane._tl_syncing_scrollbar = False

    def _on_statistics_scrollbar_moved(self, pane: DirectoryPane, value: int):
        timeline = pane.timeline
        if getattr(pane, "_tl_syncing_scrollbar", False) or timeline.global_start is None:
            return
        g0, g1 = timeline.global_start, timeline.global_end
        new_start = g0 + (value / TIMELINE_SCROLLBAR_RANGE) * (g1 - g0)
        timeline.set_view_start(new_start)

    # ---------------------------------------------------- selection sync
    def _combined_mode_selection_paths(self, pane: DirectoryPane, active_paths: set) -> set:
        """`active_paths` (whatever's just been clicked/drag-selected in
        whichever mode's timeline is currently active) unioned with
        whatever's *already selected in the tree* that belongs to the
        other, currently-hidden mode -- so a fresh click in the active
        mode never wipes out a selection of the other type sitting in
        the File List View, regardless of whether that selection came
        from the hidden mode's own timeline earlier, or straight from
        the tree/Select group while the active mode's chart was the one
        on screen. Read live off pane.tree itself rather than
        pane.tl_mode_selections (which no longer tracks discrete paths
        at all -- see _on_timeline_mode_changed's own docstring for why
        a separately-tracked snapshot goes stale the moment a selection
        is made directly in the tree): the tree's current selection,
        right before this call's own caller replaces it, is exactly
        "whatever's selected of the hidden mode's type", with nothing
        to go stale.

        update_selection_count's own existing shadowed-ancestor check
        (selection_ops.SelectionMixin) already keeps this additive
        combination from double-counting a folder's size alongside one
        of its own selected children -- it walks up the tree from every
        selected item and skips it if any ancestor is *also* selected,
        which needs nothing special here to apply correctly: it works
        purely off the tree's own live isSelected() state, regardless
        of whether that state came from one mode or, as here, the union
        of both."""
        want_hidden_dir = not pane.tl_folder_mode
        hidden_paths = {
            item.data(0, PATH_ROLE) for item in pane.tree.selectedItems()
            if bool(item.data(0, IS_DIR_ROLE)) == want_hidden_dir
        }
        return set(active_paths) | hidden_paths

    def _on_timeline_selection(self, pane: DirectoryPane, paths: set):
        """Timeline -> tree: the bin/range just clicked or drag-selected
        in `pane`'s timeline becomes `pane`'s tree selection, replacing
        whatever was selected before -- same "who drives whom" split as
        Timeline Explorer's own MainWindow (_on_timeline_selection
        there), routed through _select_paths_in_tree so it gets the
        same tree-hiding/batched-signal handling a Select-group search
        does, rather than one setSelected() per path.

        Wrapped in the shared central progress bar/Cancel cycle (see
        TransferMixin._start_transfer_progress) instead of running fully
        untracked, the way this used to: a bin covering many scattered
        files can mean lazily populating a lot of never-before-expanded
        ancestor folders (see _select_paths_in_tree's own ancestor-
        populate step), and with nothing pumping the event loop during
        that, the whole app used to just look frozen, with no progress
        bar to show for it either. Reuses an already-active cycle
        instead of starting a nested one, same defensive pattern
        _on_item_expanded/update_listings already use -- nothing in the
        app currently kicks one of these off from inside another, but
        the tree's own selection stays clickable during any progress-
        driven operation (see _set_operation_controls_enabled), so it's
        not actually impossible.

        total=len(combined_paths) * (2 if sync_active else 1) is exact,
        not approximate, for what it's meant to measure: `combined_paths`
        (this pane's own click plus whatever the hidden mode already had
        selected -- see _combined_mode_selection_paths) is precisely what
        _select_paths_in_tree is about to search for here, and
        selection_ops._select_paths_in_tree's walk() now only ticks the
        bar on an actual target match, merely pumping the event loop
        (progress.pump() -- see progress._CancellableProgress.pump())
        for ancestor-only traversal instead. Doubled when Synchronize
        Views is going to mirror this onto the other pane too (see
        sync_views._sync_selection_from, which picks up this same token
        via active_transfer_progress rather than running its own pass
        untracked, and so ticks against this same total): that side's
        real match count isn't knowable ahead of time -- some targets
        may have no counterpart there at all -- but it can never exceed
        this side's own count, so assuming the same number is a safe,
        deterministic-ish stand-in; worst case the bar lands a little
        short of full, and _finish_transfer_progress below leaves it at
        wherever the live tick actually reached rather than forcing it
        to 100%.

        Neither pane.item_count (the root listing's own count when
        Recursive Folder Selections is off, a real recursive count when
        it's on -- see listing_ops._recount_pane_items) nor
        _pane_file_count/_pane_folder_count (visible/expanded-only in
        that same off case) was used for the total instead: neither one
        measures what this walk actually ticks against, and a
        Folder-mode bin makes that starkest -- selecting a folder ticks
        exactly once, however big the pane is.

        select_subtree=False: `paths` is always a flat set of individual
        records -- files in File mode, folders in Folder mode (see
        pane.tl_folder_mode/_flatten_folders) -- so a folder that's one
        of `paths` gets selected on its own, not its contents too. That
        matters more now that folders can actually be timeline records
        themselves: selecting a folder's point should highlight just
        that folder in the tree, not cascade into everything nested
        under it -- a *nested* folder only ends up selected alongside it
        if that nested folder is separately one of `paths` too (its own
        point, its own bin/range match), never as a side effect of its
        parent being selected.

        expand_matched_dirs=False, for the same reason: a folder that's
        itself a target should become visible and selected, not spring
        open to reveal its own contents -- Recursive Folder Selections
        being on or off doesn't come into it either way, since this
        isn't recursing into anything, just revealing the match itself.
        auto_expand instead follows Expand To Selection (see
        _expand_folders_matching_selection_enabled), same as every other
        selection-producing control in the app: with it off, the match
        is still found and selected, it just doesn't reshape the tree to
        show it -- see _select_paths_in_tree's own docstring for both
        params. _scroll_tree_to_first_selected below still brings the
        first one into view regardless, since that's a plain scroll, not
        an expand.

        update_selection_count is called explicitly rather than relying
        on _select_paths_in_tree's own internal one (which only fires
        when toggled_count > 0): clearing the timeline's selection down
        to an empty set is a real, common case here (the Clear
        Selection button) and should still refresh the Select group's
        displayed count to 0, even though nothing was "toggled" for a
        target-free call.

        The tree gets `combined_paths` (paths unioned with the other
        (hidden) mode's own stashed selection, not `paths` alone -- see
        _combined_mode_selection_paths -- so making a fresh selection in
        whichever mode is active never wipes out whatever's already
        selected in the other one."""
        sync_active = self._synchronize_views_enabled() and not self._sync_guard
        combined_paths = self._combined_mode_selection_paths(pane, paths)

        progress = getattr(self, "active_transfer_progress", None)
        owns_progress = progress is None
        if owns_progress:
            total = len(combined_paths) * (2 if sync_active else 1)
            progress = self._start_transfer_progress(total=(total or None), label="Selecting...", pane=pane)
        try:
            self._select_paths_in_tree(
                pane.tree, combined_paths, additive=False,
                auto_expand=self._expand_folders_matching_selection_enabled(),
                select_subtree=False, expand_matched_dirs=False, select=True,
                progress=progress,
            )
            self.update_selection_count(pane.tree, pane)
            if paths and not progress.cancelled:
                self._scroll_tree_to_first_selected(pane.tree)
            # Mirror onto the other pane's own Statistics view -- see
            # SyncMixin._sync_timeline_selection_to_other_pane. A no-op
            # whenever Synchronize Views is off (checked there). The
            # tree-selection mirror itself still happens synchronously,
            # driven off pane.tree's own itemSelectionChanged ->
            # _sync_selection_from (see there) -- only what that call
            # does with the progress token changed, not when it runs.
            self._sync_timeline_selection_to_other_pane(pane, paths)
        finally:
            if owns_progress:
                self._finish_transfer_progress()
                if not progress.cancelled:
                    self._append_operation_duration()

        # Any Statistics-view selection change -- a bin/range click, or a
        # click outside every bin clearing it -- leaves the tree holding
        # something none of the Select group's toggle buttons describe
        # anymore, so pop them up, same as Clear/Invert. The generic
        # handler for that (selection_ops._on_manual_tree_selection)
        # never gets the chance: _select_paths_in_tree holds
        # _select_group_guard for its whole walk, which is what keeps a
        # Select button from un-toggling itself -- and this path goes
        # through the same method. Done even after a Cancel: a partly
        # applied selection doesn't match any button's set either.
        #
        # Rows this deselected also drop any last-used diff color back
        # to the default ranking (see selection_ops._reset_diff_color_
        # overrides) -- rows still selected keep theirs, the same rule a
        # Select button's release follows.
        #
        # With Synchronize Views on, the same for the other pane: its
        # selection was just replaced by the mirror of this one (see
        # sync_views._sync_selection_from).
        self._pop_toggle_buttons(pane)
        self._reset_diff_color_overrides(pane, only_unselected=True)
        if sync_active:
            other_pane = self.right if pane is self.left else self.left
            self._pop_toggle_buttons(other_pane)
            self._reset_diff_color_overrides(other_pane, only_unselected=True)

    def _scroll_tree_to_first_selected(self, tree):
        """Bring the first already-*visible* selected row (in on-screen
        top-to-bottom order, not path/set order, which is arbitrary) to
        the vertical center of `tree`'s viewport -- the second half of
        _on_timeline_selection's "go look at this file" job.

        Deliberately skips any selected item that's currently hidden
        behind a collapsed ancestor, rather than just grabbing whatever
        comes back first: QTreeView.scrollTo (which QTreeWidget.
        scrollToItem wraps) silently expands every collapsed ancestor
        standing between the viewport and the target row so it has
        something to scroll to -- contrary to this method's old
        assumption that it was "a plain scroll, not an expand." With
        Expand To Selection off, auto_expand above is False and
        _select_paths_in_tree deliberately leaves those ancestors
        collapsed; scrollToItem was then quietly reopening them anyway,
        which is exactly the reveal that setting is meant to opt out
        of. It also explains why a multi-folder Folder-mode selection
        used to look "half expanded": only the first item in traversal
        order ever got this treatment, leaving every other selected
        folder's still-collapsed ancestors untouched.

        Finding the first *visible* one instead means: when auto_expand
        already opened a path (or it was open already), behavior is
        unchanged; when it didn't, this only scrolls to whatever's
        already on screen and leaves the tree's expand state alone.

        Walked here via _iter_visible_items (plain top-level-item/
        child() recursion -- same helper update_type_counts and the
        Select group's search buttons already use), which only ever
        descends into an item once every ancestor above it is confirmed
        expanded -- the same "reachable on screen" condition the old
        per-item ancestor-walking check here used to test for each
        candidate individually, just derived the other way around.
        Swapped out for a QTreeWidgetItemIterator, which used to do
        this walk instead: that class has a long-documented history of
        exactly this kind of intermittent, hard-to-reproduce native
        crash in PySide once the tree it's iterating gets torn down and
        rebuilt shortly after (confirmed here too -- a search that both
        selects a lot of scattered items *and* runs this scroll-to-
        first afterward, then has its tree fully rebuilt twice in a row
        by an unrelated filter change, segfaulted reliably with the
        iterator in place and never did once this was swapped out for
        a plain traversal). Nothing else about this method changed --
        same visibility check, same "first match wins" result, same
        scrollToItem call at the end -- only the walk that finds that
        first match."""
        for item in self._iter_visible_items(tree):
            if item.isSelected():
                tree.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return

    def _sync_timeline_selection(self, pane: DirectoryPane):
        """Tree -> timeline: mirror the pane's current tree selection
        onto its timeline's bin highlighting -- files while in File mode,
        folders while in Folder mode (pane.tl_folder_mode), whichever
        one the timeline is actually plotting right now. The other type
        is never part of timeline.records at all in that mode (see
        _pane_timeline_records), so including it here would just be
        selected_paths the widget could never match to any bin.

        Guarded by _bulk_select_guard for the same reason
        update_selection_count is (see there): during a Select-group
        search/Select All this is connected to the same
        itemSelectionChanged that would otherwise fire it once per
        setSelected() call -- an O(selected items) scan on every one of
        potentially thousands of calls. Skipped here under the guard
        and recomputed once explicitly wherever update_selection_count
        already is post-bulk-op (see selection_ops.py).

        set_external_selection deliberately doesn't itself emit
        selectionChanged, so this can't loop back into
        _on_timeline_selection.

        Also skipped while pane._tree_rebuild_pending is set (see
        listing_ops.update_listing) -- pane.tree.selectedItems() below
        touches live QTreeWidgetItems, and reaching this reentrant (a
        leftover, already-scheduled Qt event/timer getting a chance to
        run during the rebuild's own progress-driven processEvents()
        pumping) would do that while the tree is either already cleared
        or still only half-repopulated. update_listing calls this
        itself, unconditionally, once it's actually finished (see its
        tail), so this still ends up correct either way."""
        if getattr(self, "_bulk_select_guard", False) or getattr(pane, "_tree_rebuild_pending", False):
            return
        timeline = getattr(pane, "timeline", None)
        if timeline is None:
            return
        want_dir = pane.tl_folder_mode
        paths = {
            item.data(0, PATH_ROLE) for item in pane.tree.selectedItems()
            if bool(item.data(0, IS_DIR_ROLE)) == want_dir
        }
        timeline.set_external_selection(paths)
