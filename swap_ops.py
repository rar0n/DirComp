"""
SwapOpsMixin: the Main tab's "Swap pane" button (see
UiBuilderMixin._build_main_tab_top_row).

Swapping trades everything that belongs to a pane's FOLDER -- its root,
folder history, loaded snapshot (.fss) and in-memory snapshot of the
listing, item count -- plus what's expanded and selected in it, and leaves
everything that belongs to the pane's VIEW (Statistics Files/Folders mode,
zoom, splitter, ...) where it is. Nothing is rescanned from disk: the
swapped snapshots are simply listed again, the way a cosmetic UI rebuild
does (see ListingMixin._relist_from_existing_snapshots).

In Duplicate Finder mode the right pane shows the duplicates of the left
one; the folder it has in Compare Folders mode is only kept in the
background (its entry text, history, and snapshot in self._dup_stashed_right,
see UiBuilderMixin._on_dual_pane_changed). The swap works on exactly that
hidden folder, as if the right pane were still showing it: the left pane
takes the stashed folder, and the old left folder becomes the stashed one
-- so switching back to Compare Folders finds the panes swapped.
"""

# Per-pane state that goes with the pane's folder (see module docstring).
# snapshot_root / snapshot_index are swapped separately, since in
# Duplicate Finder mode the right pane's own are the duplicates view.
_FOLDER_STATE_ATTRS = (
    "snapshot_file", "snapshot_meta", "snapshot_full_root", "snapshot_full_index",
    "snapshot_hash_key", "folder_history", "saved_folder_history", "sync_anchor",
    "item_count", "upper_status",
)


class SwapOpsMixin:
    def _swap_panes(self):
        """Swap the left and right pane's folders -- see the module
        docstring. Does nothing mid-operation, or with no folder in either
        pane."""
        if getattr(self, "active_transfer_progress", None) is not None:
            return
        left, right = self.left, self.right
        if left.tree is None or right.tree is None:
            return
        left_text, right_text = left.entry.text(), right.entry.text()
        if not left_text and not right_text:
            return
        duplicate_mode = self._duplicate_mode_active()

        # What's expanded/selected goes with each folder. In Duplicate
        # Finder mode the right tree is the duplicates list, not a folder.
        left_view = (self._collect_expanded_paths(left.tree), self._collect_selected_paths(left.tree))
        right_view = (set(), set()) if duplicate_mode else (
            self._collect_expanded_paths(right.tree), self._collect_selected_paths(right.tree))

        for attr in _FOLDER_STATE_ATTRS:
            left_value, right_value = getattr(left, attr), getattr(right, attr)
            setattr(left, attr, right_value)
            setattr(right, attr, left_value)
        if duplicate_mode:
            stashed = getattr(self, "_dup_stashed_right", None)
            self._dup_stashed_right = (left.snapshot_root, left.snapshot_index)
            left.snapshot_root, left.snapshot_index = stashed if stashed else (None, None)
        else:
            left.snapshot_root, right.snapshot_root = right.snapshot_root, left.snapshot_root
            left.snapshot_index, right.snapshot_index = right.snapshot_index, left.snapshot_index
        left.entry.setText(right_text)
        right.entry.setText(left_text)
        self._invalidate_dir_rank_cache()
        for pane in (left, right):
            self._refresh_history_combo(pane)
            self._update_snapshot_label(pane)

        # Mirroring what's expanded/selected onto the other pane (Synchronize
        # Views) must not happen part-way through.
        guard_was_set = self._sync_guard
        self._sync_guard = True
        try:
            if duplicate_mode and left.snapshot_root is None and left_text != right_text and right_text:
                # Nothing stashed for the right pane's folder: scan it.
                self._navigate_pane(left, right_text, record_history=False)
            else:
                self._relist_from_existing_snapshots()
            for pane, (expanded, selected) in ((left, right_view), (right, left_view)):
                if duplicate_mode and pane is right:
                    continue
                pane.tree.collapseAll()
                self._restore_expanded_paths(pane.tree, expanded)
                if selected:
                    self._select_paths_in_tree(pane.tree, selected, additive=False,
                                               auto_expand=False, select_subtree=False)
            # A side that is left without a folder shows nothing.
            for pane in (left, right):
                if not pane.entry.text() and not (duplicate_mode and pane is right):
                    self.clear_pane_view(pane)
        finally:
            self._sync_guard = guard_was_set

        self._recount_all_panes()
        for pane in (left, right):
            self._refresh_pane_timeline(pane)
            self._update_free_space_label(pane)
            self.update_selection_count(pane.tree, pane)
        self._update_hash_buttons_enabled()
        if duplicate_mode:
            self._recolor_all_items()
        # Histories changed (and are persisted like any folder-open).
        self.save_settings(quiet=True)
