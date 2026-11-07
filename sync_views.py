"""
SyncMixin: "Synchronize Views" -- mirroring double-click navigation,
"up one folder", selection, expand/collapse, and scrolling onto the other
pane whenever a counterpart exists, while leaving the two panes' own root
folders alone. Falls back gracefully (leaves the other pane where it is)
whenever no counterpart can be found, and tracks a per-pane "sync anchor"
so that still works correctly after a navigation had to leave the other
pane behind.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from constants import PATH_ROLE
from panes import DirectoryPane


from snapshot_ops import is_snapshot_file_name


class SyncMixin:
    def _navigate_pane(self, pane: DirectoryPane, path, reset_sync=True, refresh=True, record_history=True):
        """Navigate `pane` to `path`. `reset_sync` (the default) marks this
        as a deliberate, independent choice of root -- e.g. the user
        picking a folder via Select Directory, or dropping one onto the
        entry field -- which re-establishes both panes' *current* roots as
        the new sync baseline, per "Synchronize Views" never touching the
        root folders themselves. The synchronized double-click/"up one
        folder" handlers pass reset_sync=False since they're deliberately
        preserving (or intentionally offsetting) that baseline instead.

        `refresh` (the default) does the actual listing/recount work right
        away. Callers that are about to make a *second* _navigate_pane
        call immediately after -- both synchronized handlers below, and
        UiBuilderMixin._restore_ui_state after a layout rebuild -- pass
        refresh=False on every call but the last, then do exactly one
        combined self.update_listings()/recount themselves afterward.
        update_listings() always refreshes *both* panes against each
        other's current root regardless of which single pane asked for
        it, so calling it once per _navigate_pane call when two are about
        to happen back to back used to mean doing that full two-pane
        pass twice (or, in _restore_ui_state's case, refreshing against a
        not-yet-set counterpart the first time only to redo it a moment
        later once the second path actually lands) -- exactly the kind of
        "why is a folder click doing so much work" stall this app doesn't
        need.

        `record_history` (the default) pushes `path` onto `pane`'s
        folder-open history and saves it to disk right away -- see
        SettingsMixin._record_folder_history -- before any of the actual
        scanning below. It's explicitly False for double-click drill-down/
        "up one folder" (browsing within an already-open comparison, not
        "opening" a new one) and for _restore_ui_state's replay of a prior
        session's roots across a cosmetic rebuild (already recorded when
        each was originally opened)."""
        if (is_snapshot_file_name(path) and os.path.isfile(path)
                and getattr(pane, "snapshot_file", None) != os.path.normpath(os.path.abspath(path))):
            if not self._load_snapshot_into_pane(pane, path):
                return
            path = pane.snapshot_file
        elif is_snapshot_file_name(path) and getattr(pane, "snapshot_file", None):
            path = pane.snapshot_file  # that snapshot is already loaded here
        elif getattr(pane, "snapshot_file", None) and not self._in_pane_snapshot(pane, path):
            self._unload_snapshot(pane)
        pane.entry.setText(path)
        if record_history:
            self._record_folder_history(pane, path)
        if reset_sync:
            pane.sync_anchor = None
            other_pane = self.right if pane is self.left else self.left
            other_pane.sync_anchor = None
        if refresh:
            self.update_listings(initiating_pane=pane)
            self._recount_pane_items_with_indicator(pane)
            warning = getattr(self, "_pending_snapshot_warning", None)
            if warning:
                self._pending_snapshot_warning = None
                self._show_warning_status(*warning)

    def _on_history_entry_activated(self, pane: DirectoryPane, path: str):
        """A folder picked from entry's own dropdown history (see
        DirDropComboBox.historyEntryActivated) -- navigate there like any
        other deliberate folder open, unless it's since gone missing
        (deleted/renamed/unmounted), in which case leave history alone,
        roll the combo's displayed text back to the pane's actual current
        folder (Qt already overwrote it with the missing entry's text as
        part of choosing it), and say so instead."""
        if self._isdir(path) or (is_snapshot_file_name(path) and os.path.isfile(path)):
            self._navigate_pane(pane, path)
            return
        pane.entry.setText(pane.entry._current_valid_text)
        QMessageBox.warning(
            self, "Folder Not Found",
            f"This folder no longer exists:\n\n{path}",
        )

    def _sync_reference_roots(self, pane: DirectoryPane):
        """The (pane_root, other_root) pair to compute view-sync
        counterparts against: each pane's sync_anchor if it has one (i.e.
        the two views have desynced since), otherwise its live current
        root."""
        other_pane = self.right if pane is self.left else self.left
        pane_root = pane.sync_anchor if pane.sync_anchor is not None else pane.entry.text()
        other_root = other_pane.sync_anchor if other_pane.sync_anchor is not None else other_pane.entry.text()
        return pane_root, other_root

    def _on_item_double_clicked(self, item, pane: DirectoryPane):
        # Double-click navigation clears and rebuilds both trees (see
        # update_listings), same as Refresh/the checkboxes/the toolbar
        # buttons -- but a tree has to stay clickable for selection to
        # keep working during a long scan, so unlike those it can't just
        # be disabled outright; this explicit check covers it instead.
        # See TransferMixin._set_operation_controls_enabled.
        full_path = item.data(0, PATH_ROLE)
        # A file opens exactly like the right-click menu's Open (default
        # program, same confirmation) -- see ContextMenuMixin._open_paths.
        # Checked ahead of the guard below: opening a file doesn't clear
        # or rebuild either tree, so it's harmless mid-operation.
        if full_path and not self._isdir(full_path):
            if not self._is_snapshot_path(full_path):  # a snapshot's files don't exist on disk
                self._open_paths([full_path])
            return
        if getattr(self, "active_transfer_progress", None) is not None:
            return
        if not full_path:
            return
        if not self._synchronize_views_enabled():
            self._navigate_pane(pane, full_path, record_history=False)
            return

        other_pane = self.right if pane is self.left else self.left
        old_root = pane.entry.text()
        pane_root, other_root = self._sync_reference_roots(pane)
        cp = self._counterpart_path(pane.tree, full_path, root=pane_root, other_root=other_root)
        moved_other = bool(cp and self._isdir(cp))

        self._navigate_pane(pane, full_path, reset_sync=False, refresh=False, record_history=False)
        if moved_other:
            self._navigate_pane(other_pane, cp, reset_sync=False, refresh=False, record_history=False)
        else:
            # No counterpart -- this pane moves on its own and the other
            # is left where it is. Remember the pair's roots as of just
            # before this move (if not already desynced from an earlier
            # move) so a later navigation keeps computing counterparts
            # against that fixed point rather than the live roots, which
            # no longer actually correspond to each other.
            if pane.sync_anchor is None:
                pane.sync_anchor = old_root
            if other_pane.sync_anchor is None:
                other_pane.sync_anchor = other_pane.entry.text()

        # One combined listing refresh covers both panes either way --
        # update_listing always diff-colors each pane against the
        # other's *current* root, so even the pane that stayed put needs
        # a fresh pass now that its counterpart's contents just changed
        # underneath it. See _navigate_pane's refresh param for why this
        # is one pass here instead of the two full both-pane passes the
        # individual _navigate_pane calls above would otherwise trigger.
        self.update_listings(initiating_pane=pane)
        if moved_other:
            self._recount_all_panes()
        else:
            self._recount_pane_items_with_indicator(pane)

    def go_up_one_folder(self, pane: DirectoryPane):
        # See _on_item_double_clicked -- same reasoning. Redundant with
        # up_button itself being disabled during an active cycle (see
        # _set_operation_controls_enabled), kept here too in case this
        # ever gets wired to something else (a shortcut, etc) that
        # bypasses the button's own enabled state.
        if getattr(self, "active_transfer_progress", None) is not None:
            return
        current = pane.entry.text()
        if not current:
            return
        if getattr(pane, "snapshot_file", None) and os.path.normpath(current) == pane.snapshot_file:
            return  # a snapshot's root has no parent folder to go up to
        parent = os.path.dirname(os.path.normpath(current))
        if not parent or parent == os.path.normpath(current):
            return

        if not self._synchronize_views_enabled():
            self._navigate_pane(pane, parent, record_history=False)
            return

        other_pane = self.right if pane is self.left else self.left
        pane_root, other_root = self._sync_reference_roots(pane)
        # Same idea as double-click, just in the other direction: the
        # parent we're headed to is above pane_root, so this needs
        # `allow_ancestor` to resolve on the other side at all.
        cp = self._counterpart_path(pane.tree, parent, allow_ancestor=True, root=pane_root, other_root=other_root)
        moved_other = bool(cp and self._isdir(cp))

        self._navigate_pane(pane, parent, reset_sync=False, refresh=False, record_history=False)
        if moved_other:
            self._navigate_pane(other_pane, cp, reset_sync=False, refresh=False, record_history=False)
            # Landed back exactly on the spot where a desync previously
            # happened -- both sides agree again, so go back to trusting
            # the live roots instead of the fixed anchor.
            if pane.sync_anchor is not None and os.path.normpath(parent) == os.path.normpath(pane.sync_anchor):
                pane.sync_anchor = None
            if other_pane.sync_anchor is not None and os.path.normpath(cp) == os.path.normpath(other_pane.sync_anchor):
                other_pane.sync_anchor = None
        else:
            if pane.sync_anchor is None:
                pane.sync_anchor = current
            if other_pane.sync_anchor is None:
                other_pane.sync_anchor = other_pane.entry.text()

        # See _on_item_double_clicked -- same reasoning for one combined
        # refresh/recount here instead of one per _navigate_pane call.
        self.update_listings(initiating_pane=pane)
        if moved_other:
            self._recount_all_panes()
        else:
            self._recount_pane_items_with_indicator(pane)

    def _synchronize_views_enabled(self) -> bool:
        cb = getattr(self, "synchronize_views_cb", None)
        return cb.isChecked() if cb else False

    # ------------------------------------------------------ Column sorting

    def _on_synchronize_views_changed(self, state):
        """Turning Synchronize Views on: if the two panes' column sort
        currently differs, adopt the left pane's (column + direction) as
        the shared baseline rather than leaving them to silently drift
        apart while everything else about the two views is "synchronized".
        Turning it off does nothing -- sorting goes back to being
        independent per pane, same as scrolling/selection/expansion do."""
        if not state:
            return
        self._sync_tree_sort_to(self.left.tree, self.right.tree)

    def _sync_tree_sort_to(self, src_tree, dst_tree):
        """Copy src_tree's active column sort (column + direction) onto
        dst_tree, if src_tree has one and dst_tree doesn't already match
        it. Used both when Synchronize Views is first turned on (see
        _on_synchronize_views_changed) and, while it's already on, every
        time either pane's header gets clicked (see
        ListingMixin.sort_tree_by_column) -- keeping the two in step
        instead of only synchronizing scroll/selection/expansion."""
        column = getattr(src_tree, "_sort_column", None)
        if column is None:
            return
        ascending = getattr(src_tree, "_sort_ascending", True)
        if (getattr(dst_tree, "_sort_column", None) == column
                and getattr(dst_tree, "_sort_ascending", True) == ascending):
            return
        dst_tree._sort_column = column
        dst_tree._sort_ascending = ascending
        self._apply_sort(dst_tree, column, ascending)
        dst_tree.header().setSortIndicator(column, Qt.AscendingOrder if ascending else Qt.DescendingOrder)

    # --------------------------------------- Select-group search progress

    def _counterpart_paths(self, tree, path) -> list:
        """Every item in the other pane that `path` (an item of `tree`'s
        pane) corresponds to, for selection mirroring. Folders map by
        relative path. A file is matched the way the comparison matches
        it: by content (hash / fingerprint, names ignored; with no
        content match, the same-name file if any), or by name without
        extension / first part of the name -- every file in the
        counterpart folder that matches, so an ambiguous match selects
        them all. Plain name, size and date comparison: same relative path."""
        cp = self._counterpart_path(tree, path)
        if not cp:
            return []
        exact = [cp] if self._exists(cp) else []
        node = self._snapshot_lookup(path)
        hash_mode = self._hash_mode_active()
        stem_mode = self._stem_mode() != 0
        if node is None or node["is_dir"] or not (hash_mode or stem_mode):
            return exact
        parent = self._snapshot_lookup(os.path.dirname(cp))
        if parent is None or not parent["is_dir"]:
            return []
        files = [c for c in (parent["children"] or {}).values() if not c["is_dir"]]
        if hash_mode:
            digest = self._node_hash(node)
            if digest is not None:
                same = [c["full_path"] for c in files if self._node_hash(c) == digest]
                if same:
                    return same
            return exact
        stem = self._name_stem(node["name"])
        return [c["full_path"] for c in files if self._name_stem(c["name"]) == stem]

    def _counterpart_path(self, tree, path, allow_ancestor=False, root=None, other_root=None):
        """Map `path` (which belongs to `tree`'s pane) to where the
        equivalent item would be in the *other* pane, for view-
        synchronization purposes -- e.g. left=/a/Foo/Bar, right root=/b
        -> /b/Foo/Bar. Returns None if either pane's root isn't set, or if
        `path` isn't actually under `root`. `allow_ancestor` additionally
        permits `path` to be an *ancestor* of `root` (used for "go up one
        folder", where the target is above the current root rather than
        below it) -- selection/expand-in-tree callers leave this off since
        an ancestor can't be a tree item there. `root`/`other_root`
        default to the pane's/other pane's live current roots, but callers
        that need to account for a prior desync (see
        _sync_reference_roots) can pass the sync anchor instead."""
        pane = self._pane_for_tree(tree)
        other_pane = self.right if pane is self.left else self.left
        if root is None:
            root = pane.entry.text()
        if other_root is None:
            other_root = other_pane.entry.text()
        if not root or not other_root or not path:
            return None
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            return None
        if rel.startswith(os.pardir) and not allow_ancestor:
            return None
        if rel == os.curdir:
            return other_root
        return os.path.normpath(os.path.join(other_root, rel))

    def _find_item_by_path(self, tree, path):
        """Find the QTreeWidgetItem for `path` among tree's currently
        materialized items (won't reach into folders never expanded, since
        those simply have no child items yet)."""
        norm = os.path.normpath(path)

        def walk(item):
            p = item.data(0, PATH_ROLE)
            if p and os.path.normpath(p) == norm:
                return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found is not None:
                    return found
            return None

        for i in range(tree.topLevelItemCount()):
            found = walk(tree.topLevelItem(i))
            if found is not None:
                return found
        return None

    def _sync_selection_from(self, tree):
        """With Synchronize Views on, mirror `tree`'s current selection
        onto the other pane's tree -- items with no counterpart there are
        simply left out (per-item, not an all-or-nothing skip).

        select_subtree=False is deliberate here: it maps and selects
        exactly the items already in `tree.selectedItems()`, one-for-one,
        rather than re-expanding any selected directory's counterpart
        into a whole selected subtree of its own. That's not a loss of
        anything -- if a directory's contents were themselves meant to be
        selected too (e.g. Missing/Matching's whole-subtree selections),
        Qt already puts every one of those descendant items individually
        into tree.selectedItems() before this fires, so they're already
        each in `targets` on their own. Recursing into the counterpart's
        subtree on top of that would be redundant when the two panes'
        layouts match, and actively wrong when they don't -- e.g. a
        folder that's only a *partial* match (Folder Parent's ancestor
        folders, or a Diff Folders result) is deliberately selected
        without its contents on this side, and a counterpart subtree
        can also contain files this side never had a reason to select at
        all (they may not even exist here).

        auto_expand is likewise tied to "Expand To Selection" rather than
        left at its own default (always-True) -- otherwise a plain manual
        click, with that setting off, would still force the other pane's
        matching folders open just to reveal the mirrored selection,
        which is exactly the reveal that setting is meant to opt out of.

        expand_matched_dirs=False for the same reason _on_timeline_selection
        passes it (see _select_paths_in_tree's own docstring): mirroring a
        plain click-to-select on a folder should select that folder's
        counterpart, not spring it open to reveal its contents -- that's
        what a double-click (_on_item_double_clicked) is for. Left at the
        select_paths_in_tree default of True, every folder click in one
        pane was expanding its counterpart in the other.

        progress=getattr(self, "active_transfer_progress", None): shares
        whichever progress cycle (if any) is already driving the call
        that triggered this -- a Statistics-view bin selection (see
        statistics_ops._on_timeline_selection for why: a bin covering
        many scattered, not-yet-expanded files can mean real, synchronous
        ancestor-populate work on this side too, with nothing else to
        pump the event loop during it), or a Select-group search/Select
        All/Invert (see selection_ops). Falls back to None (untracked, as
        always) for the overwhelmingly common case -- a plain manual
        click in the tree, with nothing else running.

        Called two ways now: reactively, wired to every tree's own
        itemSelectionChanged for a plain manual click (Qt fires that
        directly off item.setSelected(), nothing else involved); and
        explicitly, once, from the tail of selection_ops.
        _select_paths_in_tree itself, for anything routed through that
        method (Select-group searches, Statistics-view bin selection,
        post-transfer highlighting, ...) -- see the _bulk_select_guard
        check below for why the reactive path alone isn't enough there.

        Also bypassed while _bulk_select_guard is set: _select_paths_in_
        tree's own selection changes go through one batched
        QItemSelectionModel.select() call (_apply_bulk_selection) rather
        than one setSelected() per item, but that single batched call
        still fires itemSelectionChanged once, synchronously, from
        *inside* that method's own try block -- before its own finally
        restores tree visibility and clears its guards. Reaching this
        reentrantly at that point used to mean the mirror walk on the
        other pane ran its own complete hide/populate/show cycle nested
        inside the *middle* of the source walk's cleanup, rather than
        after it -- which is why the two panes' tree-hiding used to look
        asymmetric (destination briefly hidden then shown again while
        still mid-operation; source hidden for the whole remaining
        duration, since restoring it is the very last thing the outer
        call does). _select_paths_in_tree's own tail now makes the
        explicit call described above once *all* of that cleanup is
        done instead, so this early return just means "don't also do it
        early, and messily, off the signal" -- the explicit call still
        happens, correctly, right after.

        This is the one synchronization path for every selection change,
        Select-group buttons included (the type-driven searches, Select
        All, Invert, File/Folder Parents): each runs against its own pane
        only, and the other pane follows through here -- the same as a
        manual click or a Statistics-view bin selection. Invert holds
        _sync_guard across its own intermediate steps and calls this once
        itself at the end -- see SelectionMixin._invert_selection_for_pane.
        Clear is the one exception: it deselects only its own former
        selection's counterparts over there instead of replacing that
        pane's selection -- see _sync_deselection_from.

        Also bypassed while either side's own tree is mid-rebuild (see
        listing_ops.update_listing's pane._tree_rebuild_pending) --
        tree.selectedItems() below reads live items straight off
        whichever pane triggered this, and _select_paths_in_tree walks
        the *other* pane's tree to apply the mirror, so either one being
        mid-teardown/still-repopulating (a leftover, already-scheduled Qt
        event/timer getting a chance to run during a rebuild's own
        progress-driven processEvents() pumping) is unsafe. Nothing needs
        to catch this back up afterward the way update_listing's own tail
        does for the tree that was actually rebuilt: this mirrors a
        *manual* selection change on the source side, and update_listing
        already restores + re-mirrors whatever was selected on each side
        independently once its own rebuild finishes."""
        source_pane = self._pane_for_tree(tree)
        other_pane = self.right if tree is self.left.tree else self.left
        if (not self._synchronize_views_enabled() or self._sync_guard
                or getattr(self, "_bulk_select_guard", False)
                or getattr(source_pane, "_tree_rebuild_pending", False)
                or getattr(other_pane, "_tree_rebuild_pending", False)):
            return
        other_tree = other_pane.tree
        targets = []
        for item in tree.selectedItems():
            path = item.data(0, PATH_ROLE)
            targets.extend(self._counterpart_paths(tree, path))
        self._sync_guard = True
        try:
            self._select_paths_in_tree(
                other_tree, targets, additive=False, select_subtree=False,
                auto_expand=self._expand_folders_matching_selection_enabled(),
                expand_matched_dirs=False,
                progress=getattr(self, "active_transfer_progress", None),
            )
        finally:
            self._sync_guard = False

    def _sync_deselection_from(self, tree, paths) -> int:
        """Subtractive counterpart of _sync_selection_from, for Clear:
        with Synchronize Views on, deselect just the counterparts of
        `paths` (what `tree`'s pane had selected right before it was
        cleared) in the other pane, leaving anything else selected there
        alone. _sync_selection_from can't do this -- it *replaces* the
        other pane's selection with the counterparts of the source's
        current one, which after a Clear is nothing, wiping the other
        pane entirely.

        Only counterparts actually selected over there are passed on:
        nothing else needs touching, and an empty result skips the walk
        (and its tail's recounts) outright. The walk runs under
        _sync_guard, so its tail's own _sync_selection_from call doesn't
        mirror the other pane's remaining selection straight back.

        Same bypass conditions as _sync_selection_from. Returns how many
        items were deselected on the other side."""
        source_pane = self._pane_for_tree(tree)
        other_pane = self.right if tree is self.left.tree else self.left
        if (not self._synchronize_views_enabled() or self._sync_guard
                or getattr(source_pane, "_tree_rebuild_pending", False)
                or getattr(other_pane, "_tree_rebuild_pending", False)):
            return 0
        other_tree = other_pane.tree
        other_selected = {
            os.path.normpath(p) for p in (item.data(0, PATH_ROLE) for item in other_tree.selectedItems()) if p
        }
        targets = []
        for path in paths:
            for cp in self._counterpart_paths(tree, path):
                if os.path.normpath(cp) in other_selected:
                    targets.append(cp)
        if not targets:
            return 0
        self._sync_guard = True
        try:
            self._select_paths_in_tree(
                other_tree, targets, additive=True, select_subtree=False, select=False,
            )
        finally:
            self._sync_guard = False
        return len(targets)

    def _on_item_expanded_sync(self, item, tree):
        """Mirrors an expand from `tree` onto its counterpart item in
        the other pane. Bypassed while either side's tree is mid-rebuild
        -- see _sync_selection_from's docstring for why (same reasoning,
        same risk: item.data() below reads a live item off `tree`, and
        setExpanded touches the other pane's tree).

        Also bypassed while _bulk_select_guard is set -- a bulk walk
        (selection_ops._select_paths_in_tree) that auto-expands a lot of
        newly-populated ancestors fires this signal once per ancestor,
        and other_item.setExpanded(True) below doesn't just flip a
        visual flag: it fires the *other* tree's own itemExpanded in
        turn, triggering listing_ops._on_item_expanded's real populate
        work there -- ticking whatever progress cycle is active once per
        *child item created* in that folder, not once per real target
        match. Left unguarded, that's an entirely separate, uncontrolled
        source of ticks on top of the walk's own -- proportional to
        branching factor, not to the target count -- which is what was
        actually driving the count up toward ~4-6x rather than the
        intended 2x, even after _sync_selection_from itself was fixed to
        only run once, deferred. The walk's own deferred
        _sync_selection_from(tree) call already populates whatever the
        other pane genuinely needs, correctly ticked, once cleanup is
        done -- reaching over here too, mid-walk, was always redundant
        with that, just usually too cheap on a small selection to
        notice."""
        source_pane = self._pane_for_tree(tree)
        other_pane = self.right if tree is self.left.tree else self.left
        if (not self._synchronize_views_enabled() or self._sync_guard
                or getattr(self, "_bulk_select_guard", False)
                or getattr(source_pane, "_tree_rebuild_pending", False)
                or getattr(other_pane, "_tree_rebuild_pending", False)):
            return
        cp = self._counterpart_path(tree, item.data(0, PATH_ROLE))
        if not cp or not self._isdir(cp):
            return
        other_item = self._find_item_by_path(other_pane.tree, cp)
        if other_item is None:
            return
        self._sync_guard = True
        try:
            other_item.setExpanded(True)  # synchronously populates children if needed
        finally:
            self._sync_guard = False

    def _on_item_collapsed_sync(self, item, tree):
        """Collapse counterpart of _on_item_expanded_sync -- see there,
        including for the mid-rebuild and _bulk_select_guard bypasses."""
        source_pane = self._pane_for_tree(tree)
        other_pane = self.right if tree is self.left.tree else self.left
        if (not self._synchronize_views_enabled() or self._sync_guard
                or getattr(self, "_bulk_select_guard", False)
                or getattr(source_pane, "_tree_rebuild_pending", False)
                or getattr(other_pane, "_tree_rebuild_pending", False)):
            return
        cp = self._counterpart_path(tree, item.data(0, PATH_ROLE))
        if not cp:
            return
        other_item = self._find_item_by_path(other_pane.tree, cp)
        if other_item is not None:
            self._sync_guard = True
            try:
                other_item.setExpanded(False)
            finally:
                self._sync_guard = False

    def _on_scroll_sync(self, value, tree):
        """Best-effort scroll sync: mirrors scroll position by proportion
        of the scrollable range rather than trying to line up matching
        rows, since the two panes can hold different numbers of items.

        Bypassed while either side's tree is mid-rebuild -- same
        reasoning as _sync_selection_from/_on_item_expanded_sync (see
        there): `tree`'s own scrollbar can fire valueChanged reentrant
        while `tree` is mid-teardown/still-repopulating (its range
        shrinks to 0 the moment tree.clear() removes every item, then
        grows back as the rebuild repopulates it), and this used to be
        the only one of the four sync handlers in this file that didn't
        check for that -- ListingMixin.update_listing now also blocks
        the scrollbar's own signals for the span of tree.clear() itself
        (belt and braces against the segfault that caused), but the
        rest of a rebuild -- repopulating, restoring expand/selection
        state -- can still legitimately move the scrollbar and doesn't
        block signals for any of that, so the guard here still matters
        for the remainder of the rebuild, not just the clear() moment."""
        source_pane = self._pane_for_tree(tree)
        other_pane = self.right if tree is self.left.tree else self.left
        if (not self._synchronize_views_enabled() or self._sync_guard
                or getattr(source_pane, "_tree_rebuild_pending", False)
                or getattr(other_pane, "_tree_rebuild_pending", False)):
            return
        bar = tree.verticalScrollBar()
        other_bar = other_pane.tree.verticalScrollBar()
        max_v = bar.maximum()
        ratio = (value / max_v) if max_v else 0
        self._sync_guard = True
        try:
            other_bar.setValue(round(ratio * other_bar.maximum()))
        finally:
            self._sync_guard = False

    # --------------------------------------------- Statistics view sync

    def _sync_timeline_selection_to_other_pane(self, pane: DirectoryPane, paths: set):
        """With Synchronize Views on, catch other_pane's own timeline
        highlighting up to its tree selection, after a Statistics-view
        click on `pane` -- called from
        TimelineOpsMixin._on_timeline_selection right after it applies
        the selection to `pane`'s own tree.

        Does this the exact same way a plain manual tree selection on
        other_pane's own side always has: by calling
        _sync_timeline_selection(other_pane), which reads
        other_pane.tree.selectedItems() live and keeps only the ones
        matching other_pane.tl_folder_mode. No path-mapping or mode
        matching happens *here* -- there's nothing to map, because
        other_pane's tree selection is already correct by the time this
        runs. pane.tree's own itemSelectionChanged (fired by
        _on_timeline_selection's tree update, above) already reached
        _sync_selection_from, which mirrors selected paths onto
        other_pane's tree via counterpart-path lookup same as every
        other tree-selection sync in this app -- entirely mode-agnostic,
        the same as selecting those files directly in other_pane's own
        File List View would be. That mirror's own tail would normally
        call _sync_timeline_selection(other_pane) itself once it's done,
        but it runs *reentrantly* while this call's own outer
        _select_paths_in_tree(pane.tree, ...) walk is still mid-flight,
        so _bulk_select_guard is still True there and that tail's
        _sync_timeline_selection call silently no-ops (see
        _select_paths_in_tree). This method's whole job is just to make
        that catch-up call from here instead, once that guard's no
        longer in the way -- and since it reads other_pane's own tree
        filtered by other_pane's own mode, whatever just got mirrored
        only ever shows up in other_pane's timeline when it's actually
        plotting that same kind of thing (files vs. folders) -- so two
        panes left showing different modes doesn't need any special
        case at all: the mirrored selection is just as real on that
        side's tree either way, and simply isn't visible as a bin
        highlight until that side's own mode matches what was selected,
        same as it would be for anything selected directly there.

        update_selection_count(other_pane) is called explicitly too --
        not redundant, even though _sync_selection_from's own
        _select_paths_in_tree(other_pane.tree, ...) call already does
        this on its own tail: that tail's own toggled_count/
        cleared_existing check isn't gated on _bulk_select_guard the way
        its _sync_timeline_selection call is, so it should already be
        correct by the time this runs -- but cheap enough to repeat here
        too rather than depend on that.

        Guarded by _tree_rebuild_pending on both sides since
        _sync_timeline_selection reads pane.tree live and expects a
        stable tree, same as the rest of this mirror machinery."""
        if not self._synchronize_views_enabled() or self._sync_guard:
            return
        # `paths` (pane's own just-clicked selection) is intentionally
        # unused below -- there's nothing left to map from it, per the
        # docstring above. Kept in the signature since
        # _on_timeline_selection already has it sitting right there.
        other_pane = self.right if pane is self.left else self.left
        if (getattr(pane, "_tree_rebuild_pending", False)
                or getattr(other_pane, "_tree_rebuild_pending", False)):
            return
        self.update_selection_count(other_pane.tree, other_pane)
        self._sync_timeline_selection(other_pane)

        # The tree-selection mirror this triggers on the way here
        # (pane.tree's own itemSelectionChanged -> _sync_selection_from
        # -> _select_paths_in_tree(other_pane.tree, ...)) happens
        # *reentrantly*, nested inside _select_paths_in_tree(pane.tree,
        # ...)'s own per-item walk up in _on_timeline_selection --
        # _bulk_select_guard is still True at that point (inherited from
        # the still-in-progress outer walk), so that inner call's own
        # "toggled_count > 0 -> update_selection_count/
        # _sync_timeline_selection" tail (see _select_paths_in_tree)
        # silently skips both for other_pane, even though other_pane's
        # tree selection itself did get mirrored correctly. By here,
        # though, the outer walk has fully finished and
        # _bulk_select_guard is back to False, so this one isn't
        # skipped -- explicit rather than relying on that inner call's
        # own tail, which is exactly what got missed.
        self.update_selection_count(other_pane.tree, other_pane)
