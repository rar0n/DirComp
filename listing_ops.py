"""
ListingMixin: populates and refreshes each pane's tree (talking to
DiffEngineMixin for the actual per-item comparison), lazy-loads folders on
expand, keeps the item counts and free-space labels current, and handles
column sorting. This is the "what's on screen" layer -- selection and
transfer are handled elsewhere.

Six columns per row: Name, Size, Type, Modified Date, the platform-
dependent Changed/Created date (see constants.CHANGED_FIELD_LABEL), and
Accessed Date -- see COL_* below. Diff coloring (_diff_tag) still only
ever compares size and Modified Date, same as before; the two extra
columns are informational, not part of what makes an item "differ" from
its counterpart.
"""

import os
import shutil
import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from constants import PATH_ROLE, IS_DIR_ROLE, OTHER_DIR_ROLE, POPULATED_ROLE, TAG_COLORS

# Column indices, named for clarity everywhere a specific column matters
# (sorting, tag coloring, item construction) instead of a bare int.
COL_NAME, COL_SIZE, COL_TYPE, COL_MODIFIED, COL_CHANGED, COL_ACCESSED = range(6)
COLUMN_COUNT = 6
from panes import DirectoryPane

# Which tree column shows a given snapshot date field (mtime/ctime/
# atime) -- used by _retag_listing_tree to re-read a diff-relevant date
# string back off an item that's already built, following whichever
# field the item's own pane's Statistics-view Metric currently maps to
# (see statistics_ops.TimelineOpsMixin._pane_diff_date_field) instead of
# always re-reading COL_MODIFIED regardless of that Metric.
_DATE_FIELD_TO_COLUMN = {"mtime": COL_MODIFIED, "ctime": COL_CHANGED, "atime": COL_ACCESSED}

# Which _build_entries() dict key already holds a given snapshot date
# field's formatted string -- _build_entries computes all three
# (date_str/changed_str/accessed_str) unconditionally regardless of
# Metric, so picking the right one back out by date_field is free; no
# separate "diff date string" needs to be computed on top of that. Used
# by _make_child_items and selection_ops.py's recursive filesystem
# searches, both of which need the date_str matching whatever
# date_field _diff_tag is about to compare against.
_DATE_FIELD_TO_ENTRY_KEY = {"mtime": "date_str", "ctime": "changed_str", "atime": "accessed_str"}


class ListingMixin:
    def expand_all_folders(self, pane: DirectoryPane):
        """Expand All. With Synchronize Views on, also expands the other
        pane's tree directly (see _sync_all_folders_state) rather than
        relying on the per-item expand sync (SyncMixin._on_item_expanded_sync)
        -- that handler only ever visits items that exist in *this*
        pane's tree and maps forward to a counterpart, so a folder that
        exists only on the other side is never reached that way at all.
        Expanding both trees outright sidesteps that asymmetry."""
        self._sync_all_folders_state(pane, expand=True)

    def collapse_all_folders(self, pane: DirectoryPane):
        """Collapse All -- see expand_all_folders."""
        self._sync_all_folders_state(pane, expand=False)

    def _sync_all_folders_state(self, pane: DirectoryPane, expand: bool):
        """Expand All shares one busy progress/Cancel cycle across
        however many folders it ends up populating (potentially both
        panes' worth, with Synchronize Views on) -- each individual
        folder's own populate still goes through _on_item_expanded (Qt
        fires itemExpanded synchronously per setExpanded(True) call
        below), which finds this cycle already active via
        active_transfer_progress and ticks against it instead of
        starting its own. Collapse is always instant, so it gets no
        progress cycle at all."""
        sync_active = self._synchronize_views_enabled() and not self._sync_guard
        guard_was_set = self._sync_guard
        if sync_active:
            self._sync_guard = True
        progress = None
        if expand:
            # Sized the same way a Select-group search's own bar is (see
            # SelectionMixin._start_select_progress) -- pane.item_count
            # is already the real total of what expanding will visit,
            # so this can be a normal determinate bar instead of the
            # "unknown total" busy/indeterminate style -- but only when
            # Recursive Folder Contents is on: with it off, item_count
            # is just the root folder's own immediate (non-recursive)
            # entry count (see _recount_pane_items), nowhere near what
            # this walk actually visits, since the walk itself always
            # recurses through every depth regardless of that checkbox.
            # Rather than compute a real recursive total just for this
            # bar, run it busy in that case -- confirmed fine with the
            # user, who'd rather see it move than see a wrong number.
            other_pane = self.right if pane is self.left else self.left
            if self._recursive_folder_contents_enabled():
                total = pane.item_count or 0
                if sync_active:
                    total += other_pane.item_count or 0
            else:
                total = None
            progress = self._start_transfer_progress(total=total, label="Expanding...", pane=pane)
        try:
            self._set_all_folders_state(pane, expand, progress=progress)
            if sync_active and not (progress and progress.cancelled):
                other_pane = self.right if pane is self.left else self.left
                self._set_all_folders_state(other_pane, expand, progress=progress)
        finally:
            self._sync_guard = guard_was_set
            if progress is not None:
                # progress.count (real ticks so far), not the pre-
                # computed `total` above -- that's now None in busy
                # mode (Recursive Folder Contents off), and even in
                # determinate mode this is more honest if the walk was
                # cancelled partway through.
                self._finish_transfer_progress(result_count=progress.count)

    def _set_all_folders_state(self, pane: DirectoryPane, expand: bool, progress=None):
        tree = pane.tree
        # Hide the tree for the duration of a genuinely bulk populate,
        # same reasoning (and the same measured ~10x-per-call cost while
        # visible) as update_listing's own initial build and
        # SelectionMixin._select_paths_in_tree's large-operation path --
        # this walk populates thousands of items via _ensure_item_
        # populated/QTreeWidgetItem.setData() exactly like those do, and
        # was the one bulk-populate path in the app that hadn't been
        # given this treatment yet. Collapse is cheap either way
        # (collapseAll() doesn't populate anything), but hiding around
        # it too costs nothing and keeps this one try/finally simple.
        tree_was_visible = tree.isVisible()
        do_hide = tree_was_visible
        if do_hide:
            tree.setVisible(False)
        tree.setUpdatesEnabled(False)
        # Whenever Recursive Folder Contents is off, two different
        # itemExpanded/itemCollapsed listeners would otherwise redo an
        # O(visible-items) walk on every single signal this walk fires
        # (once per folder, so O(n^2) over a big tree): _on_item_
        # expanded's own update_type_counts call below, and statistics_
        # ops._on_tree_expand_collapse_for_timeline's timeline recompute.
        # pane._bulk_expand_pending tells both to stand down for the
        # duration -- this method drives the timeline's own throttled
        # refresh directly instead (see
        # _maybe_refresh_pane_timeline_throttled), and the single
        # update_type_counts call after this method's try/finally below
        # already catches that up once, unthrottled, when it's done.
        # Recursive mode's timeline/counts never depend on expand state
        # at all (see _pane_timeline_records/_count_recursive_types), so
        # none of this matters -- and isn't done -- when it's on.
        throttle_timeline = not self._recursive_folder_contents_enabled()
        if throttle_timeline:
            pane._bulk_expand_pending = True
        try:
            if expand:
                def walk(item):
                    if progress is not None and not progress.tick():
                        return
                    if item.data(0, IS_DIR_ROLE):
                        # Populate directly, untracked, rather than relying
                        # on setExpanded(True) alone -- which populates via
                        # _on_item_expanded's own progress-ticking path. This
                        # walk's own recursion below already visits (and
                        # ticks) every child exactly once in a moment, so
                        # letting population *also* tick here would double-
                        # count every newly-populated child -- exactly the
                        # ~2x counts (and a bar that fills halfway through
                        # the real work) this used to produce on a folder
                        # that hadn't been expanded before. Once populated,
                        # the setExpanded(True) below just flips the visual
                        # state -- _on_item_expanded's own populate call is a
                        # harmless no-op by then (see POPULATED_ROLE).
                        self._ensure_item_populated(item, tree, progress=None)
                        if not item.isExpanded():
                            item.setExpanded(True)
                    if throttle_timeline:
                        self._maybe_refresh_pane_timeline_throttled(pane)
                    for i in range(item.childCount()):
                        if progress is not None and progress.cancelled:
                            return
                        walk(item.child(i))
                for i in range(tree.topLevelItemCount()):
                    if progress is not None and progress.cancelled:
                        return
                    walk(tree.topLevelItem(i))
            else:
                tree.collapseAll()
        finally:
            tree.setUpdatesEnabled(True)
            if do_hide:
                tree.setVisible(True)
            if throttle_timeline:
                pane._bulk_expand_pending = False
                # One last, unthrottled refresh so whatever's shown at
                # the end is always accurate, regardless of where the
                # throttle's own timing last happened to land (or
                # whether the walk was cancelled partway through).
                self._refresh_pane_timeline(pane)
        # Only matters for the "Total items" breakdown when Recursive
        # Folder Contents is off -- with it on, the breakdown is already a
        # full recursive filesystem tally regardless of what's expanded on
        # screen, so there's nothing for expanding/collapsing to change
        # (see update_type_counts).
        if not self._recursive_folder_contents_enabled():
            self.update_type_counts(pane)

    def update_listings(self, initiating_pane: "DirectoryPane | None" = None, rescan_disk: bool = True,
                        rescan_panes=None):
        """Refresh both views. If both directories are set, each listing is
        diff-colored against its counterpart on the other side -- and that
        now applies recursively as subfolders get expanded too -- so there's
        no need for a separate Compare step after browsing or file operations.

        `rescan_disk`, when False, skips _refresh_pane_snapshot for both
        panes entirely -- neither pane's snapshot_root/snapshot_index is
        touched, so whatever's already sitting there (from the last real
        disk read) is reused as-is. Only safe for a call that's purely
        reacting to a *display* setting -- the file filter text, Invert,
        List Directories/Files, Sort Directories First -- none of which
        change what's actually on disk, just what should be shown of it
        (see _listing_filter_state/_build_entries, which read the filter
        live off these same widgets on every call regardless). Every
        other call site -- a folder actually being opened/navigated,
        Refresh, or a transfer's post-op refresh -- leaves this at its
        default True, since those genuinely can have changed what's on
        disk and need a real re-scan to see it. Left/right's own
        left_valid/right_valid checks below still do a cheap os.path.isdir
        stat either way -- that's just confirming the path itself still
        resolves to a directory at all, not a re-read of its contents.

        `rescan_panes`, when given (with rescan_disk True), limits the disk
        re-read to those panes -- a pane's own Refresh: the other pane keeps
        its snapshot and is only re-tagged against the new one.

        `initiating_pane`, when given, is purely cosmetic -- which of the
        two status-line bars shows the "Loading..." busy indicator for
        this call (see _start_transfer_progress(pane=)). Every call site
        that's really a *single pane's* own action (typing/dropping a
        path into just that pane's entry field -- see
        SyncMixin._navigate_pane) passes its own pane through so the
        right side actually lights up when you load a folder there,
        instead of always defaulting to the left bar. Everything else
        (a transfer's post-op refresh, Recursive Folder Selections
        being toggled, a manual Refresh) genuinely affects both panes
        equally and leaves this as None, same as before.

        Wrapped in a busy progress/Cancel cycle (see TransferMixin) -- see
        update_listing for what actually happens in it. If a cycle is
        already active (e.g. this is running as part of a bigger
        operation -- a transfer's post-op self.update_listings() call,
        or _on_recursive_folder_contents_changed's own listing-then-
        recount sequence), reuses that one instead of starting a
        second, nested cycle that would stomp on the first.

        Bails out immediately if a call is already in progress
        (self._update_listings_pending already True on entry), same
        "safe to just skip" reasoning as update_listing's own
        pane._tree_rebuild_pending guard (see there) -- but covering
        this whole method, not just the tree-touching part of it, since
        _refresh_pane_snapshot below (a real disk walk, when rescan_disk
        is True) ticks/pumps QApplication.processEvents() same as
        update_listing's own _build_entries does, and that pump can let
        an already-queued second trigger -- another checkbox toggle,
        a held key's auto-repeat, Invert Filter's stateChanged firing
        again before the first pass even reaches update_listing -- run
        reentrant through this same method while the first call's own
        snapshot rebuild is still underway. That second call used to
        reach its own _refresh_pane_snapshot while the first was
        mid-rebuild of the very same snapshot_root/snapshot_index dicts,
        which could leave them in a half-built state neither call's own
        _pane_content_unchanged/update_listing logic downstream was
        ever written to expect -- one concrete way a segfault could
        surface later at tree.clear() despite update_listing's own
        deep-recursion guard (_detach_all_children) already being in
        place, since that guard has nothing to do with the snapshot
        dicts themselves being self-consistent. The outer, already-
        in-flight call reads every widget/snapshot live as it runs, so
        it ends up correct on its own regardless of what triggered the
        now-skipped reentrant one.

        Snapshots for both sides are refreshed in their own pass, before
        either side's tree gets diff-tagged against the other -- doing
        it in one combined left-then-right pass (rebuild+diff-tag left,
        then rebuild+diff-tag right) would diff-tag left against
        whatever was sitting in right's snapshot *before* this call --
        stale, or simply empty the first time the other side is ever
        opened -- since right's own snapshot wouldn't be rebuilt until
        the second half of that pass. That's what made a freshly-opened
        second pane leave the first pane showing everything as Missing
        until a manual Refresh (whose *second* call then found a
        still-valid snapshot left over from the first) happened to
        paper over it."""
        if getattr(self, "_update_listings_pending", False):
            return
        self._update_listings_pending = True
        try:
            self._update_listings_impl(initiating_pane, rescan_disk, rescan_panes)
        finally:
            self._update_listings_pending = False

    def _update_listings_impl(self, initiating_pane: "DirectoryPane | None", rescan_disk: bool, rescan_panes=None):
        self._update_free_space_label(self.left)
        self._update_free_space_label(self.right)

        left_dir = self.left.entry.text()
        right_dir = self.right.entry.text()
        left_valid = bool(left_dir) and self._isdir(left_dir)
        right_valid = bool(right_dir) and self._isdir(right_dir)

        progress = getattr(self, "active_transfer_progress", None)
        owns_progress = progress is None
        if owns_progress:
            progress = self._start_transfer_progress(total=None, label="Loading...", pane=initiating_pane)
        try:
            # Each side gets its own phase (see _CancellableProgress.
            # start_phase) so the live count/duration label (see
            # TransferMixin._update_live_count_label) reflects *that*
            # side's own item count while it's loading, not a running
            # total combined across both panes -- without this, opening
            # a second folder while the first was already loaded made
            # the displayed number jump straight to (first pane's
            # total) before the second pane's own count had even
            # started climbing, reading as more items than what's
            # actually in the folder just opened. Only done when this
            # call owns the progress cycle itself (a plain folder open
            # or Refresh) -- a nested call (e.g. as part of a transfer's
            # post-op refresh) shares whatever phase the outer operation
            # already established and shouldn't have that reset out
            # from under it.
            #
            # Captured *before* either refresh below overwrites
            # snapshot_root with a fresh one -- see _pane_content_
            # unchanged/retag_only just below, and update_listing's own
            # retag_only branch for why this matters: a pane whose own
            # directory and contents genuinely haven't changed since
            # the last time it was listed doesn't need its tree torn
            # down and rebuilt at all, just re-diff-tagged against
            # whatever the *other* side now looks like.
            prev_left_dir = getattr(self.left, "_last_listed_dir", None)
            prev_left_snapshot = self.left.snapshot_root
            prev_right_dir = getattr(self.right, "_last_listed_dir", None)
            prev_right_snapshot = self.right.snapshot_root

            if rescan_disk:
                if owns_progress:
                    progress.start_phase(None)
                if rescan_panes is None or self.left in rescan_panes:
                    self._refresh_pane_snapshot(self.left, left_dir if left_valid else None, progress)
                if not progress.cancelled and (rescan_panes is None or self.right in rescan_panes):
                    if owns_progress:
                        progress.start_phase(None)
                    self._refresh_pane_snapshot(self.right, right_dir if right_valid else None, progress)
            # rescan_disk=False: prev_left_snapshot/prev_right_snapshot
            # above are literally the same objects as self.left.
            # snapshot_root/self.right.snapshot_root right now (neither
            # got reassigned), so _pane_content_unchanged's field-by-field
            # walk below trivially comes back True for both -- correct,
            # since nothing about either pane's actual on-disk contents
            # was touched. left_unchanged/right_unchanged still end up
            # False on account of the filter-state check that follows
            # (that's the whole point of this call), which sends
            # update_listing down its ordinary rebuild-the-tree path --
            # just with refresh_snapshot=False (as it always passes
            # below) and therefore no snapshot rebuild, and _build_entries
            # itself only ever reads the snapshot already in memory, not
            # the disk -- so the visible tree still gets rebuilt against
            # the new filter/list-option settings without a single
            # filesystem access happening anywhere in this call.

            # See update_listing's retag_only parameter: skips tearing
            # the tree down and rebuilding it from scratch for a pane
            # whose own directory and contents came back identical to
            # what they were the last time this pane was actually
            # listed -- only its diff-tag colors (against the other
            # side, which may well have changed) need updating. Kept
            # False whenever this whole method is only running because
            # one side's Recursive Folder Contents/filter/etc. changed
            # -- initiating_pane being None doesn't by itself mean
            # skip; the actual _pane_content_unchanged check below is
            # what decides, per side, independent of *why* this got
            # called at all.
            # See update_listing's retag_only parameter: skips tearing
            # the tree down and rebuilding it from scratch for a pane
            # whose own directory and contents came back identical to
            # what they were the last time this pane was actually
            # listed -- only its diff-tag colors (against the other
            # side, which may well have changed) need updating. Also
            # requires the filter-affecting settings (file filter text,
            # Invert, List Directories/Files, Sort Directories First)
            # to be unchanged since that last listing -- see
            # _listing_filter_state -- since none of those change a
            # directory's own on-disk contents, but do change what
            # should actually be shown of them; without this check a
            # filter edit against an otherwise-untouched directory took
            # the retag_only path and silently had no visible effect.
            current_filter_state = self._listing_filter_state()
            left_unchanged = (left_valid and left_dir == prev_left_dir
                               and getattr(self.left, "_last_listed_filter_state", None) == current_filter_state
                               and self._pane_content_unchanged(prev_left_snapshot, self.left.snapshot_root))
            right_unchanged = (right_valid and right_dir == prev_right_dir
                                and getattr(self.right, "_last_listed_filter_state", None) == current_filter_state
                                and self._pane_content_unchanged(prev_right_snapshot, self.right.snapshot_root))

            if left_valid and not progress.cancelled:
                # Fresh phase for this side's own tree-population pass
                # (see update_listing's own progress.tick() calls, via
                # _build_entries) -- without this, its ticks would just
                # keep piling onto whatever count the snapshot-refresh
                # phase(s) above already left the token sitting at,
                # inflating the live "N items" figure well past this
                # side's actual entry count instead of reflecting just
                # this pass's own progress, the same reasoning
                # start_phase's own docstring gives for resetting
                # between panes' snapshot refreshes just above -- this
                # was the one phase boundary that reasoning didn't
                # actually get applied to.
                if owns_progress:
                    progress.start_phase(None)
                self.update_listing(left_dir, self.left, right_dir if right_valid else None,
                                     progress=progress, refresh_snapshot=False, retag_only=left_unchanged)
            if right_valid and not progress.cancelled:
                if owns_progress:
                    progress.start_phase(None)
                self.update_listing(right_dir, self.right, left_dir if left_valid else None,
                                     progress=progress, refresh_snapshot=False, retag_only=right_unchanged)

            # Keep pane.item_count -- the denominator the Select group's
            # progress bar fills against (see
            # SelectionMixin._start_select_progress) -- in sync with
            # whatever's actually loaded right now. This used to only get
            # recomputed on an explicit Refresh or toggling Recursive
            # Folder Selections (see _recount_all_panes), so the very
            # first Select-group search run against a newly-opened folder
            # was measured against whatever total happened to be left
            # over from before -- 0, or a previous folder's count.
            #
            # Deliberately NOT given `progress` here: _count_filtered_
            # recursive is a second full walk of the same tree the
            # snapshot build above just walked (and ticked through) --
            # sharing the same progress counter would count every item
            # twice, showing a "loaded" count roughly double the real
            # total. It's snapshot-backed now (no disk I/O), so it
            # doesn't need pumping/cancel-checking of its own the way a
            # real recursive scan would.
            if left_valid and not progress.cancelled:
                self._recount_pane_items(self.left)
            if right_valid and not progress.cancelled:
                self._recount_pane_items(self.right)
        finally:
            if owns_progress:
                # How many items this call actually reports as "loaded":
                # when `initiating_pane` was given, this was really just
                # that one pane's own action (typing/dropping a path,
                # Select Directory, Up-folder -- see this method's own
                # docstring) even though both panes' snapshots get
                # refreshed/re-diff-tagged either way, so the count
                # shown is that pane's own total, not the two panes
                # added together -- summing them used to mean opening a
                # single folder reported a number that included whatever
                # the *other*, untouched pane already had loaded, which
                # read as if the just-opened folder had far more items
                # in it than it actually does. `initiating_pane=None`
                # (a manual Refresh, a transfer's post-op refresh, "List
                # Directories"/"Sort Directories First" changing) really
                # does affect both panes equally, so that case still
                # reports the combined total -- same "how much did this
                # actually do" reporting every other operation gets (see
                # TransferMixin._show_result_count), rather than
                # refreshing being the one action that leaves the label
                # (and the timing it carries) blank.
                # snapshot_index (DiffEngineMixin._build_snapshot) is
                # keyed by every node's own normalized path -- including
                # the root directory's own node, since _snapshot_node
                # indexes itself on every call, the top-level one
                # included. len(snapshot_index) was therefore always one
                # higher than the folder's actual item count: a folder
                # with 12 items in it reported 13 here. _snapshot_item_count
                # below backs that root entry back out.
                if initiating_pane is not None:
                    total_items = self._snapshot_item_count(initiating_pane)
                else:
                    total_items = (self._snapshot_item_count(self.left)
                                    + self._snapshot_item_count(self.right))
                self._finish_transfer_progress(result_count=total_items)

    def _relist_from_existing_snapshots(self):
        """Repopulate both panes' trees straight from whatever's already
        sitting in pane.snapshot_root/snapshot_index, instead of going
        through the normal update_listings() -- which always rebuilds
        both panes' snapshots from disk via _refresh_pane_snapshot/
        DiffEngineMixin._build_snapshot, regardless of whether anything
        on disk could plausibly have changed.

        Used only by UiBuilderMixin._restore_ui_state, right after a
        purely cosmetic full rebuild (Symmetrical GUI, Dual Pane, a
        color/theme change, ...): _build_directory_widgets hands each
        pane a brand new, empty tree every rebuild (see there, and
        pane._last_listed_dir), but it never touches that pane's own
        snapshot -- the DirectoryPane objects, and whatever they last
        successfully listed, survive the rebuild completely untouched.
        So there's nothing that could actually need re-scanning: the
        only thing that's genuinely stale is the freshly emptied *tree
        widget*, not the data behind it. Re-walking the filesystem
        anyway, on every purely cosmetic toggle, was pure waste -- and
        for a large tree, or one on a slow/network drive, a very
        noticeable one right at the moment a purely cosmetic toggle was
        all that was actually asked for.

        retag_only is always False here (unlike update_listings' own
        per-pane choice, driven by _pane_content_unchanged) -- these
        trees are unconditionally empty right now, so there's nothing
        for a retag-only pass to retag; a full (but snapshot-only,
        disk-free) populate is the only option that actually works.
        pane.item_count is left untouched for the same underlying
        reason update_listings' own snapshot reuse would leave it alone
        -- nothing invalidated it, so _restore_ui_state's own
        _recount_all_panes() call right after this (cheap regardless --
        see _count_filtered_recursive's own docstring for why that one
        already reads from the snapshot rather than disk) is really
        just being thorough, not correcting anything this skipped."""
        left_dir = self.left.entry.text()
        right_dir = self.right.entry.text()
        left_valid = bool(left_dir) and self._isdir(left_dir)
        right_valid = bool(right_dir) and self._isdir(right_dir)
        if left_valid:
            self.update_listing(left_dir, self.left, right_dir if right_valid else None,
                                 refresh_snapshot=False, retag_only=False)
        if right_valid:
            self.update_listing(right_dir, self.right, left_dir if left_valid else None,
                                 refresh_snapshot=False, retag_only=False)

    def _snapshot_item_count(self, pane: DirectoryPane) -> int:
        """How many items pane's own snapshot actually contains,
        *excluding* the root directory's own node -- unlike a plain
        len(pane.snapshot_index), which counts that too:
        DiffEngineMixin._snapshot_node indexes every node it visits by
        its own normalized path, the very first (root) call included,
        so the flat index always has exactly one more entry than the
        folder actually contains. Used wherever "how many items does
        this folder have" needs the real count -- see update_listings'
        own result_count reporting, which used to be off by exactly
        this much."""
        count = len(pane.snapshot_index or ())
        return count - 1 if pane.snapshot_root is not None else count

    def _listing_filter_state(self) -> tuple:
        """Snapshot of every setting that changes what _build_entries
        actually includes/excludes for a directory -- the file filter
        text, Invert, List Directories, List Files, Sort Directories
        First -- compared (see update_listings) against whatever was
        in effect the last time a pane was actually fully (re)listed
        (pane._last_listed_filter_state, set alongside pane.
        _last_listed_dir at the end of this method's own full-rebuild
        path below -- deliberately NOT touched by the retag_only
        early-return just above, since retagging doesn't re-filter
        anything, so whatever's actually on screen still reflects
        whatever this was set to last).

        This is what update_listings' retag_only shortcut was missing:
        a directory's raw on-disk contents not having changed since
        last time doesn't mean what should be *shown* of them hasn't
        -- retag_only skips calling _build_entries entirely and just
        re-colors whatever tree items already exist, so editing the
        file filter (or flipping Invert/List Directories/List Files/
        Sort Directories First) while sitting on an otherwise-
        unchanged directory used to silently have no visible effect on
        the tree at all, even though it did immediately affect
        anything reading straight from the snapshot instead (the
        Statistics view's own file-mode bins, in particular)."""
        return (
            self.file_filter_entry.text(),
            self.invert_filter_cb.isChecked(),
            self.list_directories_cb.isChecked(),
            self.list_files_cb.isChecked(),
            self.sort_directories_first_cb.isChecked(),
        )

    def _refresh_pane_snapshot(self, pane: DirectoryPane, directory, progress=None):
        """(Re)build pane.snapshot_root/pane.snapshot_index from
        `directory` (or clear them if `directory` is None/invalid) --
        factored out of update_listing so update_listings can refresh
        both panes' snapshots up front, before either one's tree gets
        diff-tagged against the other -- see its docstring."""
        # A rebuilt snapshot invalidates every cached _dir_content_rank
        # result computed against this pane's old contents -- see
        # DiffEngineMixin._invalidate_dir_rank_cache. Cleared up front,
        # before either branch below, so both "directory changed" and
        # "directory cleared" leave no stale cached ranks behind.
        self._invalidate_dir_rank_cache()
        if directory is None:
            pane.snapshot_root, pane.snapshot_index = None, {}
            self._set_upper_status(pane, "")
        elif self._in_pane_snapshot(pane, directory):
            # A loaded snapshot: nothing to scan, just show the part of it
            # that `directory` names.
            pane.snapshot_root, pane.snapshot_index = self._select_snapshot_subtree(pane, directory)
        else:
            # Persistent hash cache: read the rows for this folder into RAM
            # first, so _build_snapshot re-attaches them (a read only; opening
            # a folder never writes). No-op unless "Cache hashed folders" is
            # on and a hash type is selected.
            self._load_hash_cache_for(directory)
            pane.snapshot_root, pane.snapshot_index = self._build_snapshot(directory, progress=progress)
            if pane.snapshot_root is None:
                pane.snapshot_index = {}
            self._report_cache_read(pane)
        # Feed this pane's embedded timeline from the same snapshot --
        # see TimelineOpsMixin._refresh_pane_timeline. Cheap either way
        # (a flatten of an already-built in-memory tree, no disk I/O),
        # so it's not worth threading `progress` through for its own
        # cancel/tick handling the way the snapshot build above needs.
        # Unconditional -- including the `directory is None` branch above
        # -- so a pane whose directory just went invalid (or was
        # explicitly cleared, see UiBuilderMixin.clear_pane_view) actually
        # goes blank here too, instead of leaving the timeline showing
        # whatever it had loaded from the last valid directory.
        #
        # Also tells the timeline whether this pane has a folder open at
        # all, so its empty-state message follows *this pane's* own state
        # ("Drag a folder ... to begin" until a folder's open here,
        # "No files in view" only once one is, and back again after
        # "Clear view") rather than whether set_records() has ever been
        # called -- see TimelineWidget.set_folder_open. Set before
        # _refresh_pane_timeline so its own repaint already sees it.
        timeline = getattr(pane, "timeline", None)
        if timeline is not None:
            timeline.set_folder_open(directory is not None and pane.snapshot_root is not None)
        self._refresh_pane_timeline(pane)

    def _pane_content_unchanged(self, old_node, new_node):
        """True if `old_node` and `new_node` -- the same pane's
        snapshot_root taken at two different times -- are identical in
        every respect that could actually change how its tree should
        look or how any entry in it gets diff-tagged: name, file-vs-
        directory, size, mtime, and (recursively) everything
        underneath a directory. Used by update_listings to decide,
        per side, whether that pane's tree can skip a full clear()+
        rebuild and just get re-diff-tagged in place -- see
        update_listing's retag_only parameter.

        Deliberately ignores atime/ctime: both drift on their own --
        atime on a plain read, ctime on anything from a chmod to some
        unrelated metadata touch -- independent of anything the app
        itself ever treats as meaningful. _diff_tag/_dir_content_rank
        only ever compare size and mtime (see DiffEngineMixin.
        _file_diff_rank), so folding atime/ctime into this comparison
        would report "changed" on runs where nothing the user would
        notice, or that this app's own diff logic would ever surface,
        actually is -- defeating the entire point of skipping the
        rebuild."""
        if old_node is None or new_node is None:
            return old_node is new_node
        if (old_node["name"] != new_node["name"]
                or old_node["is_dir"] != new_node["is_dir"]
                or old_node["size"] != new_node["size"]
                or int(old_node["mtime"]) != int(new_node["mtime"])):
            return False
        if not old_node["is_dir"]:
            return True
        old_children = old_node["children"] or {}
        new_children = new_node["children"] or {}
        if old_children.keys() != new_children.keys():
            return False
        return all(self._pane_content_unchanged(old_children[name], new_children[name])
                   for name in old_children)

    def _retag_listing_tree(self, tree, other_dir):
        """Recompute and reapply the diff tag/color for every item
        already in `tree`, at any depth -- populated or not, expanded
        or collapsed -- against `other_dir`. Used by update_listing's
        retag_only branch: everything _diff_tag needs (name, IS_DIR_ROLE,
        the size/date columns' own displayed text, PATH_ROLE) is already
        sitting on the existing item, so this is exactly the same
        per-entry computation _make_child_items/_ensure_item_populated
        already do, just reapplied in place instead of as part of
        building a brand new item -- nothing here creates, removes, or
        reparents a single item, so none of tree.clear()'s hazards
        apply. A directory item's OTHER_DIR_ROLE is refreshed too, even
        though nothing about *this* item's own display changes from
        that alone -- it's what a not-yet-populated child folder's
        eventual first expand (see _ensure_item_populated) or an
        already-populated one's own recursive retag (this same walk,
        one level down) will diff against, and it needs to reflect
        the new other_dir just as much as the tag does.

        Each item's own comparison directory is threaded down the stack
        alongside it rather than reusing the single top-level `other_dir`
        for every depth -- same rule _ensure_item_populated already
        follows by reading a parent's OTHER_DIR_ROLE (see its own
        other_dir = item.data(0, OTHER_DIR_ROLE) line) instead of the
        tree's root other_dir. A used-to-be bug here (comparing every
        item, regardless of depth, straight against
        os.path.join(other_dir, item's own bare name)) diff-tagged any
        already-populated subfolder's contents against the wrong
        counterpart path -- one that essentially never exists past the
        top level -- so retagging after a transfer could leave an
        already-expanded subtree showing everything as Missing even
        though the files themselves were sitting right there on both
        sides.

        date_field (mtime/ctime/atime, from this tree's own pane's
        Statistics-view Metric -- see TimelineOpsMixin._pane_diff_
        date_field) is resolved once up front rather than per item: it
        depends only on the pane, not on any individual item, and a
        mid-walk Metric change re-triggers this same retag pass anyway
        (see _on_timeline_metric_changed) so there's nothing to lose by
        not re-checking it at every node."""
        pane = self._pane_for_tree(tree)
        date_field = self._pane_diff_date_field(pane)
        date_col = _DATE_FIELD_TO_COLUMN[date_field]
        stack = [(tree.topLevelItem(i), other_dir) for i in range(tree.topLevelItemCount())]
        while stack:
            item, item_other_dir = stack.pop()
            is_dir = bool(item.data(0, IS_DIR_ROLE))
            name = item.text(0)
            full_path = item.data(0, PATH_ROLE)
            size_str = item.text(COL_SIZE)
            date_str = item.text(date_col)
            tag = self._diff_tag(name, is_dir, size_str, date_str, item_other_dir, full_path,
                                  date_field=date_field)
            self._apply_tag(item, tag)
            child_other_dir = None
            if is_dir:
                child_other_dir = os.path.join(item_other_dir, name) if item_other_dir else None
                item.setData(0, OTHER_DIR_ROLE, child_other_dir)
            if item.childCount():
                stack.extend((item.child(i), child_other_dir) for i in range(item.childCount()))

    def _refresh_all(self):
        """The single Refresh button: re-read both panes' listings from
        disk, and re-enumerate both panes' item counts too (content may
        well have changed since the last count) -- via _recount_all_panes
        rather than calling _recount_pane_items directly, so this gets
        the same busy/cancelable bar the checkbox toggle already does
        (see _on_recursive_folder_contents_changed) instead of running
        silently with no feedback."""
        if self._duplicate_mode_active():
            self._dup_reset_colors(self.left)  # Refresh starts the duplicate colors over
        self.update_listings()
        self._recount_all_panes()

    def _update_free_space_label(self, pane: DirectoryPane):
        if getattr(pane, "free_space_label", None) is None or getattr(pane, "entry", None) is None:
            return  # called mid-construction, before this pane's toolbar exists
        path = pane.entry.text()
        if path and not self._is_snapshot_path(path) and os.path.isdir(path):
            try:
                free = shutil.disk_usage(path).free
                size_text = self.format_size(free).replace(" ", "\u00a0")
                pane._free_atoms = [("Free:", "Free:"), (size_text, self._numeric_span(size_text))]
            except OSError:
                pane._free_atoms = []
        else:
            pane._free_atoms = []
        self._render_toolbar_labels()

    @staticmethod
    def _detach_all_children(tree):
        """Iteratively strip every item in `tree` down to zero children
        of its own, without deleting anything itself -- see the call
        site in update_listing (right before tree.clear()) for why this
        has to happen first.

        Two passes, deliberately in this order:

        1. Collect every item via a plain, read-only traversal
           (childCount()/child(i) -- no mutation), in an order that
           always lists a parent before any of its descendants. This
           part is safe at any depth: these are simple O(1) accessors,
           nothing here can trigger Qt's own recursive internal
           bookkeeping the way takeChildren() below can.
        2. Call item.takeChildren() on every collected item in REVERSE
           of that order -- bottom-up, so every item's own descendants
           have already been stripped down to nothing by the time its
           takeChildren() call actually runs.

        That ordering matters: an item.takeChildren() call, when the
        subtree beneath it is still fully intact, makes Qt recursively
        walk that whole remaining subtree internally (to invalidate/
        update persistent model indexes for every descendant, not just
        the direct children being removed) -- which is exactly the deep
        native recursion this function exists to avoid. Processing
        top-down (the first version of this fix) just moved the deep
        recursion one level in, onto the very first takeChildren() call
        on whichever item still had its whole subtree beneath it --
        still one big native recursive sweep, same crash. Bottom-up,
        every item has already had its own children taken by the time
        its parent gets processed, so no takeChildren() call ever has
        more than an already-empty subtree to deal with.

        A previous version of this method went a step further and
        explicitly force-deleted every detached item via shiboken6.
        delete(), on the theory that leaving them for Python/PySide's
        own (seemingly cyclic-gc-dependent) reclaiming was itself
        unsafe. That theory was real -- reproduced independently with a
        deep synthetic chain -- but the "fix" for it turned out to
        introduce a worse, non-deterministic crash of its own in actual
        use (same takeChildren() crash site, but only after several
        filter-change cycles, sometimes taking two or three tries to
        surface): explicitly destroying a QTreeWidgetItem's C++ object
        via a low-level API that bypasses Qt/PySide's own ownership
        bookkeeping is exactly the kind of operation where the C++
        object being genuinely gone, rather than just unreferenced,
        turns a later-cycle's stale/dangling access into an actual
        use-after-free instead of a harmless no-op -- and something
        elsewhere in this app (not yet identified) apparently does
        retain a reference across a rebuild often enough for that to
        matter. Given the choice between a rare crash at extreme,
        never-realistically-occurring chain depths (the original,
        theoretical issue) and a confirmed, if intermittent, crash at
        entirely ordinary folder sizes (the shiboken6.delete() fix's own
        side effect), this method now only does the safe, non-
        destructive ordering trick -- leaving detached items to Python's
        normal reference counting/gc, same as before either fix
        existed."""
        items = []
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            items.append(item)
            stack.extend(item.child(i) for i in range(item.childCount()))
        for item in reversed(items):
            item.takeChildren()

    def update_listing(self, directory, pane: DirectoryPane, other_dir=None, progress=None,
                       refresh_snapshot=True, retag_only=False):
        """(Re)populate the top level of `pane`'s tree from `directory`. If
        `other_dir` is given, every entry is diff-colored against its
        counterpart there; the same happens lazily, at any depth, whenever a
        subfolder gets expanded (see _on_item_expanded). pane.count_label is
        refreshed at the end either way -- see update_type_counts.

        `progress`, if given, is ticked once per entry while building the
        listing (see _build_entries/_make_child_items) -- see
        update_listings for why.

        `refresh_snapshot` controls whether this also (re)builds
        pane.snapshot_root/pane.snapshot_index (see
        DiffEngineMixin._build_snapshot) before building the displayed
        tree -- the default for a standalone call (e.g. a single pane
        opening a folder with the other side untouched). update_listings
        passes False, since it refreshes *both* panes' snapshots itself,
        in their own pass, before either side's tree gets diff-tagged --
        see its docstring for why that order matters.

        `retag_only`, set only by update_listings once it's confirmed
        (see _pane_content_unchanged) that this pane's own directory and
        contents haven't actually changed since it was last listed,
        skips the whole clear()+rebuild below entirely: only this
        tree's existing items' diff tags/colors get recomputed against
        the (possibly different) `other_dir`, in place, via
        _retag_listing_tree. Nothing about this pane's own structure,
        expand state, selection, or scroll position is touched, so none
        of it needs capturing or restoring -- and since nothing is ever
        torn down, this sidesteps the tree.clear()/takeChildren() native
        recursion hazard entirely for a call that never actually needed
        to rebuild anything in the first place, regardless of exactly
        which part of that hazard turns out to be the real one.

        Bails out immediately if `pane` is already mid-rebuild
        (pane._tree_rebuild_pending already True on entry) instead of
        proceeding to this call's own tree.clear() -- this is the guard
        every *other* consumer of that flag has already had
        (_schedule_selection_markers_update, _sync_selection_from,
        _on_scroll_sync, _sync_timeline_selection, ...), but this method
        itself never checked it before calling tree.clear(), so nothing
        stopped a second, reentrant call to update_listing for the same
        pane from doing exactly that while an outer call for that same
        pane was itself still mid-flight -- tree.clear() blocking
        `tree`'s and its scrollbar's own signals (see below) only
        guards against *those two objects'* signals firing back in
        reentrant, it does nothing about a wholly separate call landing
        on the same tree from somewhere else in the stack. That second
        call's tree.clear()/_build_entries would then be creating and
        destroying QTreeWidgetItems concurrently with the outer call's
        own still-in-progress population of that exact tree -- exactly
        the kind of live-object collision that segfaults instead of
        raising a catchable exception (see the comment on the blockSignals
        region below). One concrete way this happened in practice: Clear
        Filter (ui_builder.clear_filter) unchecking an already-checked
        Invert Filter checkbox fires apply_filter once via
        invert_filter_cb's own stateChanged signal, then calls
        apply_filter a second time itself -- two full update_listings()
        passes back to back, each one's own snapshot/entry-building
        ticking `progress` and periodically pumping
        QApplication.processEvents() (to keep Cancel responsive), which
        is exactly the kind of window a second, unrelated queued event
        (another click, a held key's auto-repeat, ...) can be delivered
        through and reach back into this same method reentrant. Safe to
        just skip here rather than queue a retry the way
        _schedule_selection_markers_update does: the outer, already-
        in-flight call reads directory/filter state live as it builds,
        so it ends up correct on its own regardless of what triggered
        the now-skipped reentrant one."""
        if getattr(pane, "_tree_rebuild_pending", False):
            return
        if not self._dual_pane_enabled:
            # With Dual Pane off there's no visible counterpart to
            # compare against, even if the right pane still has a
            # directory loaded under the hood from before it was
            # switched off (see UiBuilderMixin._on_dual_pane_changed --
            # toggling it off only hides that pane's widgets, it doesn't
            # clear its snapshot). Forcing other_dir to None here --
            # rather than trusting whatever the caller passed in -- is
            # what _diff_tag already treats as "nothing to diff
            # against" (see there), and it's the single choke point
            # every path through this method goes through, including
            # OTHER_DIR_ROLE's own lazy-expand chain below, which
            # otherwise would have kept threading a real other_dir
            # through every subfolder opened while single-pane.
            other_dir = None
        tree = pane.tree

        # Duplicate Finder mode: work out which files are copies of each
        # other before any row is tagged (see DuplicateFinderMixin).
        self._dup_prepare(pane)

        if retag_only:
            # See the retag_only paragraph above -- nothing here
            # creates, removes, or reparents a single item, so the
            # reentrancy flag below is just ordinary caution (in case
            # some other tree-touching callback fires mid-retag), not
            # protection against a teardown the way it is in the full
            # rebuild path.
            pane._tree_rebuild_pending = True
            try:
                self._retag_listing_tree(tree, other_dir)
            finally:
                pane._tree_rebuild_pending = False
            pane._last_listed_dir = directory
            self.update_type_counts(pane)
            return

        # Captured before anything below overwrites it -- decides at the end
        # whether this rebuild is a *different* folder being loaded (Select
        # toggle buttons reset) or the same one re-listed (left alone).
        prev_listed_dir = getattr(pane, "_last_listed_dir", None)
        expanded_paths = self._collect_expanded_paths(tree)
        selected_paths = self._collect_selected_paths(tree)
        # tree.clear() zeroes the scrollbar's range (and so its value)
        # the moment every item is removed, and nothing about the
        # rebuild that follows -- repopulating, re-expanding whatever
        # was expanded before -- puts it back on its own; if anything,
        # re-expanding a deep chain (see _restore_expanded_paths) can
        # actively pull it somewhere else, since Qt's own view code
        # scrolls to keep a just-expanded item visible if it would
        # otherwise land off-screen. Captured here and restored at the
        # very end of this method (once the tree's back to its full,
        # final state) so a rebuild -- especially one that's only
        # happening to re-diff this pane's colors against a change on
        # the *other* side, with nothing about this pane's own
        # directory or expand state actually different -- never moves
        # the viewport as a side effect of tearing the tree down and
        # putting it back together.
        scroll_value = tree.verticalScrollBar().value()

        # Hiding the tree for the whole rebuild -- not just
        # setUpdatesEnabled(False) -- matters a lot here, for the same
        # reason it did for Select All/Matching/Invert (see
        # SelectionMixin._select_paths_in_tree): QTreeWidgetItem.setData()
        # (used throughout _make_child_items) and tree.clear() itself
        # both do real per-row bookkeeping while the tree is actually
        # visible that setUpdatesEnabled(False) alone doesn't suppress.
        #
        # It matters *more* here than it first looks, because of
        # expanded_paths/_restore_expanded_paths below: this method
        # always throws the whole tree away and rebuilds it from
        # scratch (tree.clear()), then re-expands whatever was expanded
        # before -- which re-populates every one of those folders from
        # scratch too, synchronously, via setExpanded(True). If the tree
        # was heavily or fully expanded (e.g. right after a Select
        # All/Matching/Invert run, which expands as it goes), a routine
        # refresh like the one every New Folder/Copy/Move/Paste/Delete
        # already triggers (see TransferMixin) ends up doing real,
        # non-trivial re-populating work while visible.
        #
        # Deliberately NOT also disabling the whole window's updates the
        # way _select_paths_in_tree does: New Folder and Rename both
        # call this right after a modal QInputDialog closes, and
        # suspending the main window's repaint/focus handling in that
        # same window immediately afterward is what caused the
        # multi-minute apparent freezes -- pending focus/repaint
        # bookkeeping left over from the dialog's own closure got stuck
        # behind the disabled window, and only a fresh, unrelated input
        # event (e.g. clicking Clear) forced it to flush. Refresh/Clear
        # never show a dialog first, which is why only New Folder/Rename
        # ever hit this. Hiding just the tree itself still delivers the
        # real, measured win (this is what actually avoids the per-row
        # Qt bookkeeping cost) without touching the rest of the window.
        tree_was_visible = tree.isVisible()
        # Only actually touch visibility if the tree was genuinely on
        # screen to begin with. tree.isVisible() reflects the *whole*
        # ancestor chain, not just this widget -- so it reads False any
        # time this runs while the Main tab isn't the current tab (e.g.
        # toggling List Directories/List Files/Sort Directories First,
        # whose only visible control lives on the Settings tab). Calling
        # tree.setVisible(False) here in that case doesn't just no-op:
        # a QTabWidget's non-current page is only *implicitly* hidden
        # (via its QStackedWidget), which Qt automatically undoes the
        # moment that tab becomes current again -- but explicitly
        # calling setVisible(False)/(True) on a *descendant* of that
        # page marks the descendant itself as explicitly hidden/shown,
        # which Qt does NOT automatically reconcile with the ancestor's
        # own state. Switching back to the Main tab then left the tree
        # permanently invisible, since nothing else ever called
        # setVisible(True) on it again. Skipping this entirely when the
        # tree wasn't visible in the first place avoids ever putting it
        # in that state -- there's no performance win to chase on a
        # rebuild nobody can see anyway.
        if tree_was_visible:
            tree.setVisible(False)
        tree.setUpdatesEnabled(False)
        # Flagged for the whole rebuild, not just the tree.clear() call
        # below -- see UiBuilderMixin._schedule_selection_markers_update,
        # which checks this before touching the tree. _build_entries/
        # _make_child_items below tick `progress`, which periodically
        # pumps QApplication.processEvents() to keep Cancel responsive on
        # a large folder -- and that pump can let an *already scheduled*
        # deferred marker update (left over from an earlier, unrelated
        # expand/collapse/selection change, possibly several toggles ago)
        # run reentrant partway through this same rebuild, walking a tree
        # that's either already been cleared or is still only
        # half-repopulated. blockSignals around tree.clear() alone (see
        # below) doesn't catch this -- it's not a signal firing in
        # response to the clear, it's an unrelated, already-pending timer
        # callback getting a chance to run during the pump.
        pane._tree_rebuild_pending = True
        try:
            # Block signals for just this one call: tree.clear() can
            # synchronously emit itemSelectionChanged for whatever's
            # being selected as it tears down every item, and several
            # things are connected straight to that signal
            # (update_selection_count, the Synchronize Views mirror,
            # _on_manual_tree_selection, _sync_timeline_selection, the
            # scrollbar-marker scheduler -- see _build_directory_widgets)
            # that read selectedItems()/item data back off this very
            # tree while it's happening. QTreeWidgetItem isn't a
            # QObject, so touching one mid-teardown (as opposed to after
            # it's actually gone) doesn't raise a catchable Python
            # exception the way touching a deleted QObject would -- it
            # segfaults instead.
            #
            # This used to be rare in practice, since a tree usually had
            # nothing selected by the time a refresh got around to
            # clearing it. Restoring the selection across rebuilds (see
            # selected_paths below) means that's no longer true -- a
            # routine Refresh/Recursive-Folder-Selections-toggle/
            # transfer now regularly clears a tree that DOES still have
            # a real selection on it, so this stopped being a rare,
            # mostly-empty no-op and started actually crashing.
            #
            # Nothing here relies on a signal firing during clear()
            # itself -- unlike _restore_expanded_paths/the selection
            # restore just below, which both need itemExpanded to fire
            # for lazy population, so signals stay unblocked for those --
            # so blocking just this one call is sufficient, and the
            # explicit update_selection_count/_sync_timeline_selection
            # calls at the end of this method (replacing whatever
            # in-the-moment update the blocked signal would have done)
            # make sure the Select group's count and the timeline's
            # highlighting still end up correct once the dust settles,
            # even for the case where nothing ends up restored below.
            # tree.blockSignals() only covers signals emitted by `tree`
            # itself -- its vertical scrollbar is a *separate* QObject
            # (see UiBuilderMixin._build_directory_widgets, which wires
            # tree.verticalScrollBar().valueChanged straight to
            # SyncMixin._on_scroll_sync). tree.clear() removing every
            # item shrinks the scrollbar's range, which can clamp its
            # current value and fire valueChanged -- unblocked, since
            # it's not `tree`'s signal -- reentrant, into _on_scroll_sync,
            # while items are still mid-destruction on the C++ side.
            # QTreeWidgetItem isn't a QObject, so touching one then (as
            # opposed to after it's actually gone) doesn't raise a
            # catchable Python exception -- it segfaults instead, same
            # failure mode blocking `tree`'s own signals was already
            # meant to prevent (see the comment above), just via a
            # different QObject. Only actually observed when clear()
            # changes the scroll position at all, which is why this was
            # intermittent rather than a crash on every single refresh.
            scrollbar = tree.verticalScrollBar()
            tree.blockSignals(True)
            scrollbar.blockSignals(True)
            try:
                # QTreeWidgetItem's C++ destructor deletes its children
                # recursively, in native code, on the real OS thread
                # stack -- entirely separate from Python's own call
                # stack/recursion limit, and untouched by the
                # blockSignals guard above (that's about signal
                # reentrancy, not destructor depth). A tree that's had a
                # deep chain of ancestors auto-expanded in one shot --
                # e.g. "Expand To Selection" opening every folder above
                # a match picked straight out of the Statistics view,
                # which can reveal something dozens of levels down
                # without the user ever manually stepping through each
                # level -- builds exactly that kind of deep, fully
                # populated live chain. tree.clear() then has to tear it
                # down in one native recursive sweep, which can overflow
                # the C stack and segfault with no Python exception
                # possible, however carefully signals are blocked.
                #
                # _detach_all_children first breaks every parent/child
                # link, iteratively, before anything is actually
                # deleted -- so by the time tree.clear() (or Python's
                # own refcounting on the now-orphaned former children)
                # actually destroys anything, every single item already
                # has zero children of its own. One deep recursive
                # destructor chain becomes N independent, O(1) ones.
                #
                # clearSelection() first, separately: both segfaults
                # actually seen in practice landed on the pane that
                # still had a *live selection* on it at the moment of
                # teardown, regardless of which pane was navigated or
                # which direction _detach_all_children processed items
                # in -- pointing at Qt's selection-model bookkeeping for
                # removed-while-selected rows as the real hazard, not
                # subtree depth per se. tree.blockSignals(True) above
                # only suppresses signals *tree itself* emits; the
                # QItemSelectionModel Qt maintains underneath it is a
                # separate object with its own internal bookkeeping that
                # isn't touched by that call at all. selected_paths was
                # already captured further up (for restoration once the
                # rebuild is done), so the live selection has nothing
                # left to do here except be a hazard on the way out.
                #
                # setCurrentItem(None) too, separately from
                # clearSelection(): "current" (tree.currentItem()) is
                # tracked independently of "selected" in Qt, and
                # clearSelection() doesn't touch it. If the current item
                # (or an ancestor of it) is one of these deep,
                # freshly-auto-expanded items, removing it likely forces
                # an internal search for a new valid current item --
                # exactly the kind of implicit deep/expensive tree walk
                # clearSelection() alone wouldn't have prevented.
                tree.setCurrentItem(None)
                tree.clearSelection()
                self._detach_all_children(tree)
                tree.clear()
            finally:
                scrollbar.blockSignals(False)
                tree.blockSignals(False)

            if refresh_snapshot:
                self._refresh_pane_snapshot(pane, directory, progress)
            if pane.snapshot_root is None:
                self._add_tree_item(tree, "Error reading directory", "", "", "", "error")
                pane._last_listed_dir = None
                self.update_type_counts(pane)
                return

            entries = self._build_entries(directory, progress=progress)
            if entries is None:
                self._add_tree_item(tree, "Error reading directory", "", "", "", "error")
                pane._last_listed_dir = None
                self.update_type_counts(pane)
                return

            items, file_count, dir_count, total_size = self._make_child_items(
                entries, other_dir, progress=progress, date_field=self._pane_diff_date_field(pane))
            for item in items:
                tree.addTopLevelItem(item)

            self._resort_tree(tree)
            self._restore_expanded_paths(tree, expanded_paths)
            # Restore whatever was selected before this rebuild threw the
            # old items away -- a full repopulate (this always does one)
            # would otherwise silently drop the selection on every
            # Refresh/Clear view/transfer/new-folder-open, even for a
            # pane whose own directory didn't change and was only
            # rebuilt to re-diff against the other side. select_subtree
            # off restores exactly the same set that was selected before
            # (a selected folder doesn't also pull its newly-populated
            # children in); auto_expand off since expand state is
            # already restored above -- anything reachable now was
            # already made reachable by that. A path that's gone (e.g.
            # it just got moved out of this pane) is simply skipped, the
            # same way it would be for a post-transfer reselect. The
            # freshly rebuilt items already carry this rebuild's own
            # diff colors (see _make_child_items/_apply_tag above), so a
            # restored selection picks up whatever color it should now
            # have automatically, not whatever it had before.
            if selected_paths:
                self._select_paths_in_tree(tree, selected_paths, additive=False,
                                            auto_expand=False, select_subtree=False)
        finally:
            pane._tree_rebuild_pending = False
            if tree_was_visible:
                tree.setVisible(True)
            tree.setUpdatesEnabled(True)

        # Put the viewport back where it was before this rebuild ever
        # started -- see scroll_value above. Deliberately clamped by
        # Qt's own setValue() (a scrollbar's maximum may now be smaller,
        # e.g. this pane lost items) rather than done any earlier: the
        # tree's final scrollable range isn't settled until every item
        # is back in place and expand state is restored, so setting
        # this any sooner (e.g. right after tree.clear()) would just
        # get clamped to 0 and lost the moment the rebuild's own
        # addTopLevelItem/setExpanded calls grew the range back out
        # from under it.
        tree.verticalScrollBar().setValue(scroll_value)

        pane._last_listed_dir = directory
        pane._last_listed_filter_state = self._listing_filter_state()
        self.update_type_counts(pane)
        # _select_paths_in_tree's own update_selection_count/
        # _sync_timeline_selection calls only fire when it actually
        # toggled something (see its own tail) -- which covers the
        # normal case fine, but not a selection that doesn't survive
        # the rebuild at all (every previously-selected path is gone,
        # e.g. it was just deleted/moved out). tree.clear()'s own signal
        # was blocked above specifically so it couldn't announce that
        # transition either, so nothing else will -- called
        # unconditionally here so the Select group's count and the
        # timeline's highlighting always end up matching the tree's
        # actual final selection, restored or not. Same for the
        # scrollbar markers -- any deferred update that came in mid-
        # rebuild re-deferred itself rather than run (see
        # _tree_rebuild_pending above), so this is what actually
        # refreshes them now that it's safe to.
        # A new folder was loaded (as opposed to the same one refreshed/
        # re-diffed): pop every Select-group toggle back up, same as
        # Clear does, since whatever they described no longer applies.
        # Signals blocked so this doesn't itself trigger a search.
        if prev_listed_dir != directory:
            for btn in getattr(pane, "select_toggle_buttons", {}).values():
                if btn.isChecked():
                    btn.blockSignals(True)
                    btn.setChecked(False)
                    btn.blockSignals(False)
        self.update_selection_count(tree, pane)
        self._sync_timeline_selection(pane)
        self._update_selection_markers(pane)
        # _refresh_pane_timeline already ran once earlier (via
        # _refresh_pane_snapshot, called separately before this method --
        # see update_listings), but that was before _restore_expanded_paths
        # above ever ran -- in non-recursive mode, the timeline's contents
        # depend on expand state (see _pane_timeline_records), and
        # _on_tree_expand_collapse_for_timeline (which would normally catch
        # this up as each folder re-expands) is deliberately skipped for
        # the whole duration of this rebuild -- see
        # pane._tree_rebuild_pending -- so nothing else will. Called
        # unconditionally rather than gated on Recursive Folder Selections
        # being off, same as the other calls just above: cheap either way,
        # and one consistent "always resettle everything at the end"
        # rule beats one more special case to keep track of.
        self._refresh_pane_timeline(pane)

    def _build_entries(self, directory, progress=None):
        """List `directory`'s immediate contents as plain dicts, honoring
        the file filter and the List Directories/Files/Sort Directories
        First checkboxes. Returns None if the directory can't be read
        (i.e. isn't in either pane's current snapshot at all -- see
        _snapshot_lookup).

        Reads from the snapshot (DiffEngineMixin._build_snapshot)
        instead of os.listdir/os.stat -- the snapshot already holds
        every entry's stat info and, for directories, their recursive
        size (summed once, bottom-up, when the snapshot was built), so
        this comes down to filtering + formatting a dict that's already
        in memory. No filesystem access happens here anymore; `progress`
        is still ticked once per entry purely to keep Cancel responsive
        on a very large folder, not because there's disk I/O to pump
        the event loop around."""
        node = self._snapshot_lookup(directory)
        if node is None or node["children"] is None:
            return None

        filter_extensions = [
            ext.strip().lower()
            for ext in self.file_filter_entry.text().split(",")
            if ext.strip()
        ]

        names = sorted(node["children"].keys(), key=lambda x: x.lower())
        if self.sort_directories_first_cb.isChecked():
            dirs = [n for n in names if node["children"][n]["is_dir"]]
            files = [n for n in names if not node["children"][n]["is_dir"]]
            names = dirs + files

        entries = []
        for name in names:
            if progress is not None and not progress.tick():
                break
            child = node["children"][name]
            full_path = child["full_path"]
            date_str = datetime.datetime.fromtimestamp(child["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
            # st_ctime is "Created" on Windows, "metadata changed" on
            # POSIX -- see constants.CHANGED_FIELD_LABEL. st_atime
            # (last accessed) is portable as-is.
            changed_str = datetime.datetime.fromtimestamp(child["ctime"]).strftime("%Y-%m-%d %H:%M:%S")
            accessed_str = datetime.datetime.fromtimestamp(child["atime"]).strftime("%Y-%m-%d %H:%M:%S")

            if child["is_dir"]:
                if not self.list_directories_cb.isChecked():
                    continue
                item_count = len(child["children"] or {})
                entries.append(dict(
                    name=name, full_path=full_path, is_dir=True,
                    size_str=f"{item_count} items", type_str="Folder", date_str=date_str,
                    changed_str=changed_str, accessed_str=accessed_str,
                    size_raw=child["size"], date_raw=child["mtime"],
                    changed_raw=child["ctime"], accessed_raw=child["atime"],
                ))
            else:
                file_ext = os.path.splitext(name)[1][1:].lower()
                passes_filter = (not filter_extensions) or (
                    (file_ext in filter_extensions) ^ self.invert_filter_cb.isChecked()
                )
                if not (self.list_files_cb.isChecked() and passes_filter):
                    continue
                entries.append(dict(
                    name=name, full_path=full_path, is_dir=False,
                    size_str=self.format_size(child["size"]), type_str=file_ext or "File", date_str=date_str,
                    changed_str=changed_str, accessed_str=accessed_str,
                    size_raw=child["size"], date_raw=child["mtime"],
                    changed_raw=child["ctime"], accessed_raw=child["atime"],
                ))
        return entries

    def _recursive_folder_contents_enabled(self) -> bool:
        cb = getattr(self, "recursive_folder_contents_cb", None)
        return cb.isChecked() if cb else True

    def _on_recursive_folder_contents_changed(self, state):
        """"Expand To Selection" only means anything while
        this is on, so gray it out (without losing its checked state)
        whenever this is off. Also re-enumerates both panes' item counts
        under the new rule (root-only vs. full recursive walk), since
        that's what the Select group's progress bars report against."""
        checked = bool(state)
        self.expand_matching_cb.setEnabled(checked)
        self.update_listings()
        self._recount_all_panes()

    def _expand_folders_matching_selection_enabled(self) -> bool:
        if not self._recursive_folder_contents_enabled():
            return False
        return self.expand_matching_cb.isChecked()

    def _count_filtered_recursive(self, directory, progress=None):
        """Like the old raw os.walk-based count, but walks the same
        filtered listing (_build_entries -- respecting the file filter
        and the List Directories/Files checkboxes) that a Select-group
        search itself recurses through, rather than every entry on disk
        regardless of filter. Used as pane.item_count, the denominator
        the Select group's progress bar fills against (see
        _start_select_progress in selection_ops.py) -- counting
        unfiltered entries there meant the bar could never reach 100%
        while any filter was active (or List Files/Directories was
        off): the search's own tick() calls only ever visit the
        filtered subset, so the bar would stall partway through and
        then jump straight to "done" once the (smaller) real total was
        actually reached."""
        entries = self._build_entries(directory)
        if entries is None:
            return 0
        total = 0
        for e in entries:
            if progress is not None and not progress.tick():
                return total
            total += 1
            if e["is_dir"]:
                total += self._count_filtered_recursive(e["full_path"], progress)
        return total

    def _recount_pane_items(self, pane: DirectoryPane, progress=None):
        """(Re)compute pane.item_count -- the total the Select group's
        progress bar reports its search progress against. With Recursive
        Folder Contents off this is cheap (just the root listing's size).
        With it on, it's a full recursive walk -- `progress`, if given
        (see _recount_pane_items_with_indicator / _recount_all_panes),
        lets the caller show a shared busy indicator and cancel early."""
        directory = pane.entry.text()
        if not directory or not self._isdir(directory):
            pane.item_count = 0
            return
        if not self._recursive_folder_contents_enabled():
            entries = self._build_entries(directory)
            pane.item_count = len(entries) if entries is not None else 0
            return
        pane.item_count = self._count_filtered_recursive(directory, progress)

    def _recount_pane_items_with_indicator(self, pane: DirectoryPane):
        """_recount_pane_items for a single pane, showing the central
        progress bar as a busy indicator while it runs (only actually
        needed when Recursive Folder Selections is on, since that's the
        only case where this is a real recursive walk rather than a
        cheap, instant root listing). Silent -- see
        TransferMixin._start_transfer_progress -- since this is pure
        internal bookkeeping (pane.item_count catching up after a folder
        open/Refresh), not something the person actually asked for; the
        status line is left reading exactly as it did before this ran."""
        if not self._recursive_folder_contents_enabled():
            self._recount_pane_items(pane)
            return
        progress = self._start_transfer_progress(total=None, label="Recounting...", pane=pane, silent=True)
        try:
            self._recount_pane_items(pane, progress)
        finally:
            self._finish_transfer_progress()

    def _recount_all_panes(self):
        """_recount_pane_items for both panes at once, sharing a single
        central busy indicator (and Cancel) across both -- used when
        Recursive Folder Selections is toggled, and after a manual
        Refresh. Silent -- see _recount_pane_items_with_indicator above
        -- for the same reason."""
        if not self._recursive_folder_contents_enabled():
            self._recount_pane_items(self.left)
            self._recount_pane_items(self.right)
            return
        progress = self._start_transfer_progress(total=None, label="Recounting...", silent=True)
        try:
            self._recount_pane_items(self.left, progress)
            if not progress.cancelled:
                self._recount_pane_items(self.right, progress)
        finally:
            self._finish_transfer_progress()

    # --------------------------------------- Central move/paste progress

    def _make_child_items(self, entries, other_dir, progress=None, date_field="mtime"):
        """Build QTreeWidgetItems for `entries` (not yet attached to a
        tree/parent), diff-colored against `other_dir`. Directory items get
        an expand indicator and are wired up for lazy population, along
        with the path of their own counterpart directory so a later expand
        can keep comparing at that depth.

        `progress`, if given, is only checked for cancellation here, not
        ticked -- `entries` was just built by a _build_entries(...,
        progress=progress) call using this same token (see
        _ensure_item_populated/update_listing), which already ticked
        once per entry while listing them. Ticking again here, over the
        same entries, was counting every item twice -- the live count
        label would climb to ~2x the real total (and, for a big
        selection, the bar would visually reach 100% while roughly half
        the actual work -- the tree-item construction happening in this
        very loop -- was still left to do, which is also why Cancel
        seemed to do so little: whatever synchronous work was already
        underway when it was clicked just kept running).

        `date_field` (mtime/ctime/atime -- see _diff_tag) is passed
        through by the caller, resolved once from the destination
        pane's own Statistics-view Metric rather than re-derived here
        per entry; used to pick which of e["date_str"]/["changed_str"]/
        ["accessed_str"] (already-formatted, computed unconditionally
        by _build_entries regardless of what's actually displayed)
        matches what _diff_tag is being asked to compare."""
        date_entry_key = _DATE_FIELD_TO_ENTRY_KEY[date_field]
        items = []
        file_count = dir_count = 0
        total_size = 0
        for e in entries:
            if progress is not None and progress.cancelled:
                break
            tag = self._diff_tag(e["name"], e["is_dir"], e["size_str"], e[date_entry_key], other_dir,
                                  e["full_path"], date_field=date_field)
            item = QTreeWidgetItem([
                e["name"], e["size_str"], e["type_str"], e["date_str"],
                e["changed_str"], e["accessed_str"],
            ])
            item.setData(COL_SIZE, Qt.UserRole, e["size_raw"])
            item.setData(COL_MODIFIED, Qt.UserRole, e["date_raw"])
            item.setData(COL_CHANGED, Qt.UserRole, e["changed_raw"])
            item.setData(COL_ACCESSED, Qt.UserRole, e["accessed_raw"])
            item.setData(0, PATH_ROLE, e["full_path"])
            item.setData(0, IS_DIR_ROLE, e["is_dir"])
            self._apply_tag(item, tag)
            if e["is_dir"]:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
                item.setData(0, POPULATED_ROLE, False)
                item.setData(0, OTHER_DIR_ROLE,
                             os.path.join(other_dir, e["name"]) if other_dir else None)
                dir_count += 1
            else:
                file_count += 1
            items.append(item)
            total_size += e["size_raw"]
        return items, file_count, dir_count, total_size

    def _ensure_item_populated(self, item: QTreeWidgetItem, tree, progress=None):
        """Make sure `item`'s children actually exist in the tree's data
        model, populating them (diff-colored against their counterpart)
        if this is the first time. Used by _on_item_expanded for a normal
        user expand, and by _select_paths_in_tree when Expand To
        Selection is off -- populating a folder enough to select
        something found inside it, without also visually expanding that
        folder open.

        `progress`, if given, is forwarded into _build_entries/
        _make_child_items -- whoever's already driving a progress cycle
        (a single expand click, an Expand All walk, a Select-group
        search revealing its matches) shares it rather than this
        running silently and unprotected underneath.

        _build_entries/_make_child_items above periodically pump the
        event loop (via progress.tick()) to stay responsive/cancelable
        during a slow scan, which -- despite _set_operation_controls_
        enabled now blocking the obvious ways to trigger a conflicting
        tree.clear() while that's happening -- can't be guaranteed
        airtight against every possible source of one (a resize, some
        other queued event). The attach-back-to-tree step below is
        wrapped defensively: if `item`/`tree` turn out to have been torn
        down and rebuilt out from under this populate in the meantime,
        that RuntimeError is caught and this just gives up quietly on
        attaching stale children to a stale parent, rather than crashing
        -- the rebuilt tree already reflects whatever's actually current
        regardless."""
        if item.data(0, POPULATED_ROLE):
            return
        item.setData(0, POPULATED_ROLE, True)

        directory = item.data(0, PATH_ROLE)
        other_dir = item.data(0, OTHER_DIR_ROLE)
        entries = self._build_entries(directory, progress=progress)
        if entries is None:
            try:
                self._add_tree_item(tree, "(error reading folder)", "", "", "", "error", parent_item=item)
            except RuntimeError:
                pass
            return

        pane = self._pane_for_tree(tree)
        date_field = self._pane_diff_date_field(pane)
        child_items, _file_count, _dir_count, _total_size = self._make_child_items(
            entries, other_dir, progress=progress, date_field=date_field)
        try:
            for child in child_items:
                item.addChild(child)
            self._resort_children(tree, item)
        except RuntimeError:
            pass

    # A folder this size or smaller populates from the snapshot (see
    # _build_entries' own docstring: no filesystem access at all by this
    # point, just filtering/formatting already-in-memory dicts) fast
    # enough that a progress cycle for it is pure visual noise rather
    # than useful feedback -- _on_item_expanded skips starting one
    # below the threshold entirely. Above it, the snapshot already
    # knows the exact child count up front, so that cycle can be a
    # normal determinate bar (known total) instead of a busy/
    # indeterminate one -- there's nothing actually unknown left to
    # justify busy mode's "no total available" excuse.
    _LAZY_POPULATE_PROGRESS_THRESHOLD = 2000

    def _on_item_expanded(self, item: QTreeWidgetItem, tree, pane: DirectoryPane):
        """Nemo-style lazy expansion: populate a folder's children (see
        _ensure_item_populated) the first time it's expanded. Refreshes
        pane.count_label's breakdown either way (not just on first
        population) -- a folder that was expanded before, then collapsed,
        and is now being re-expanded didn't need repopulating, but the
        on-screen visible set still just grew back, which matters
        whenever Recursive Folder Selections is off (see
        update_type_counts / _iter_visible_items).

        Wrapped in its own busy progress/Cancel cycle only for a folder
        large enough to actually need one (see
        _LAZY_POPULATE_PROGRESS_THRESHOLD) -- unless one's already
        running (Expand All's own walk -- see _sync_all_folders_state --
        triggers this same handler once per folder via Qt's
        itemExpanded signal, and should keep sharing one continuous
        cycle across the whole walk rather than each folder starting
        and finishing its own), in which case this always ticks it
        regardless of this folder's own size -- Expand All's total
        already accounts for every folder it's about to visit. Silent
        -- see TransferMixin._start_transfer_progress -- since a single
        folder lazily populating as it's expanded isn't something the
        person asked to see reported on the status line; it's left
        reading exactly as it did before this ran.

        The update_type_counts call below is itself skipped while
        pane._bulk_expand_pending is set (see _set_all_folders_state) --
        Expand All fires this handler once per folder, and redoing that
        count's own O(visible-items) walk on every single one of those
        would make the whole operation O(n^2); that method's own single
        post-walk call already catches the count up once it's done."""
        progress = getattr(self, "active_transfer_progress", None)
        owns_progress = progress is None
        started_progress = False
        if owns_progress:
            directory = item.data(0, PATH_ROLE)
            node = self._snapshot_lookup(directory)
            child_count = len(node["children"]) if node and node["children"] else 0
            if child_count > self._LAZY_POPULATE_PROGRESS_THRESHOLD:
                progress = self._start_transfer_progress(total=child_count, label="Loading...", pane=pane, silent=True)
                started_progress = True
        try:
            self._ensure_item_populated(item, tree, progress=progress)
        finally:
            if started_progress:
                self._finish_transfer_progress()
        # Skipped mid-bulk-walk (see _set_all_folders_state) for the
        # same O(n^2) reason its Statistics-view counterpart is --
        # that method's own single post-walk update_type_counts call
        # already catches this up once, unthrottled, when it's done.
        if not self._recursive_folder_contents_enabled() and not getattr(pane, "_bulk_expand_pending", False):
            self.update_type_counts(pane)

    def _on_item_collapsed(self, item: QTreeWidgetItem, pane: DirectoryPane):
        """Mirror of the count refresh in _on_item_expanded -- a single
        manual collapse (not via Collapse All, which does its own refresh
        after the fact -- see the pane._bulk_expand_pending check below,
        same guard _on_item_expanded uses) also changes what's on screen
        whenever Recursive Folder Selections is off."""
        if not self._recursive_folder_contents_enabled() and not getattr(pane, "_bulk_expand_pending", False):
            self.update_type_counts(pane)

    def _collect_expanded_paths(self, tree) -> set:
        """Full paths of all currently-expanded items, so a refresh (e.g.
        after a copy/move/delete) can restore the user's expansion state
        instead of collapsing everything."""
        expanded = set()

        def walk(item):
            if item.isExpanded():
                path = item.data(0, PATH_ROLE)
                if path:
                    expanded.add(path)
                for i in range(item.childCount()):
                    walk(item.child(i))

        for i in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(i))
        return expanded

    def _restore_expanded_paths(self, tree, expanded_paths: set):
        if not expanded_paths:
            return

        def walk(item):
            if item.data(0, PATH_ROLE) in expanded_paths:
                item.setExpanded(True)  # synchronously triggers lazy population
                for i in range(item.childCount()):
                    walk(item.child(i))

        for i in range(tree.topLevelItemCount()):
            walk(tree.topLevelItem(i))

    def _collect_selected_paths(self, tree) -> set:
        """Full paths of every currently-selected item, so a full rebuild
        (update_listing always throws the tree away and repopulates it
        from scratch -- see tree.clear() below) can restore the
        selection afterward instead of silently losing it. Same idea,
        same _iter_items-not-_iter_visible_items reasoning, as
        _collect_expanded_paths: an item hidden under a collapsed
        ancestor can still be selected, so this doesn't care about
        expand state either -- it just can't find anything under a
        folder that was never populated in the first place, which is
        fine since nothing selected could be sitting there anyway."""
        selected = set()
        for item in self._iter_items(tree):
            if item.isSelected():
                path = item.data(0, PATH_ROLE)
                if path:
                    selected.add(path)
        return selected

    def _add_tree_item(self, tree, name, size, file_type, date, tag, size_raw=0, date_raw=0,
                        parent_item=None, changed="", accessed="", changed_raw=0, accessed_raw=0):
        item = QTreeWidgetItem([name, size, file_type, date, changed, accessed])
        item.setData(COL_SIZE, Qt.UserRole, size_raw)
        item.setData(COL_MODIFIED, Qt.UserRole, date_raw)
        item.setData(COL_CHANGED, Qt.UserRole, changed_raw)
        item.setData(COL_ACCESSED, Qt.UserRole, accessed_raw)
        self._apply_tag(item, tag)
        if parent_item is None:
            tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        return item

    # Folder tags that mean "differs from its counterpart" (as opposed to
    # "doesn't exist there at all", which stays "missing_dir"). The stored
    # tag keeps this fine-grained distinction so Smaller/Larger/Newer/Older
    # can still match on it, but under recursive comparison the row itself
    # is shown with one simplified "folder differs" color instead -- see
    # _apply_tag.
    _DIR_DIFF_TAGS = {"diff_count_smaller_dir", "diff_count_larger_dir",
                       "diff_date_newer_dir", "diff_date_older_dir"}

    def _apply_tag(self, item: QTreeWidgetItem, tag: str):
        item.setData(0, Qt.UserRole, tag)
        display_tag = tag
        if (item.data(0, IS_DIR_ROLE) and tag in self._DIR_DIFF_TAGS
                and self._recursive_folder_contents_enabled()):
            display_tag = "diff_content_dir"
        if display_tag.startswith("dup_"):
            # Duplicate Finder mode: one color per set of identical files,
            # "dup_<index>" (see DuplicateFinderMixin).
            bg, fg = self._duplicate_group_colors(int(display_tag[4:]))
        else:
            bg, fg = TAG_COLORS.get(display_tag, (None, None))
        for col in range(COLUMN_COUNT):
            item.setBackground(col, QBrush(QColor(bg)) if bg else QBrush())
            item.setForeground(col, QBrush(QColor(fg)) if fg else QBrush())

    # ------------------------------------------------------------ Sorting

    def sort_tree_by_column(self, tree, column):
        ascending = True
        if getattr(tree, "_sort_column", None) == column:
            ascending = not getattr(tree, "_sort_ascending", True)
        tree._sort_column = column
        tree._sort_ascending = ascending
        self._apply_sort(tree, column, ascending)
        tree.header().setSortIndicator(column, Qt.AscendingOrder if ascending else Qt.DescendingOrder)

        # With Synchronize Views on, keep both panes sorted the same way
        # -- same column, same direction -- rather than letting them
        # silently drift apart while "synchronized" in every other
        # respect (scroll/selection/expansion already mirror; sorting now
        # does too). See SyncMixin._sync_tree_sort_to.
        if self._synchronize_views_enabled():
            pane = self._pane_for_tree(tree)
            other_pane = self.right if pane is self.left else self.left
            self._sync_tree_sort_to(tree, other_pane.tree)

    def _resort_tree(self, tree):
        """Re-apply the tree's active column sort (if the user has clicked a
        header), recursively through every already-populated nested level."""
        column = getattr(tree, "_sort_column", None)
        if column is None:
            return
        ascending = getattr(tree, "_sort_ascending", True)
        self._apply_sort(tree, column, ascending)

    def _resort_children(self, tree, parent_item):
        """Same as _resort_tree, but scoped to one parent item's children --
        used right after lazily populating a folder that was just expanded."""
        column = getattr(tree, "_sort_column", None)
        if column is None:
            return
        ascending = getattr(tree, "_sort_ascending", True)
        self._sort_item_list(tree, parent_item, column, ascending)

    def _apply_sort(self, tree, column, ascending):
        self._sort_item_list(tree, None, column, ascending)

    def _sort_item_list(self, tree, parent_item, column, ascending):
        if parent_item is None:
            count = tree.topLevelItemCount()
            get_child = tree.topLevelItem
            take = tree.takeTopLevelItem
            add = tree.addTopLevelItem
        else:
            count = parent_item.childCount()
            get_child = parent_item.child
            take = parent_item.takeChild
            add = parent_item.addChild

        items = [get_child(i) for i in range(count)]

        def sort_key(item):
            if column in (COL_NAME, COL_TYPE):
                return item.text(column).lower()
            # Size, Modified, Changed/Created, and Accessed all sort on
            # their raw numeric value (bytes/item-count, or epoch
            # seconds) rather than the formatted display string.
            return item.data(column, Qt.UserRole) or 0

        items.sort(key=sort_key, reverse=not ascending)

        if self.sort_directories_first_cb.isChecked():
            items.sort(key=lambda it: not it.data(0, IS_DIR_ROLE))

        for i in reversed(range(count)):
            take(i)
        for item in items:
            add(item)

        # Recurse into any level that's already been populated (a lazily
        # loaded folder sorts itself once its children exist; unpopulated
        # ones get sorted at population time instead).
        for item in items:
            if item.childCount() > 0:
                self._sort_item_list(tree, item, column, ascending)

    # ------------------------------------------------------------ Select *

    def _iter_items(self, tree):
        """Yield every item in the tree, top-level and nested alike."""
        def walk(item):
            yield item
            for i in range(item.childCount()):
                yield from walk(item.child(i))

        for i in range(tree.topLevelItemCount()):
            yield from walk(tree.topLevelItem(i))

    def _iter_top_level_items(self, tree):
        """Yield only the tree's top-level items -- kept for anything that
        specifically wants a root-only listing; the Select group's search
        buttons themselves now use _iter_visible_items instead (see
        selection_ops.py)."""
        for i in range(tree.topLevelItemCount()):
            yield tree.topLevelItem(i)

    def _iter_visible_items(self, tree):
        """Yield every item currently on screen: every top-level item, plus
        (recursively) the children of whichever ones are actually expanded
        right now. A folder that was expanded earlier and has since been
        collapsed again stops contributing its children here, even though
        they're still sitting in the tree's data model -- unlike
        _iter_items, which doesn't care about collapse state at all. This
        is what "visible (as in expanded in the file list view)" means for
        both the Select group's search buttons and the "Total items"
        breakdown whenever a full recursive filesystem search isn't
        happening (see _select_recursive_matches and update_type_counts)."""
        def walk(item):
            yield item
            if item.isExpanded():
                for i in range(item.childCount()):
                    yield from walk(item.child(i))

        for i in range(tree.topLevelItemCount()):
            yield from walk(tree.topLevelItem(i))


