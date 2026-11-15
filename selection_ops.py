"""
SelectionMixin: the Select group's buttons -- Invert/Clear, Missing/
Matching, Smaller/Larger, Newer/Older, Diff Folders, File Parents, and
Folder Parents -- and the recursive filesystem search behind most of
them (used whenever "Recursive Folder Selections" is on; "Expand To
Selection" additionally auto-expands folders to visually reveal what
was found). Leans on DiffEngineMixin for the actual per-item comparison
and on TransferMixin's central Transfer-group bar/Cancel button (shared
with actual transfers -- see _start_select_progress) for the search's
progress display.

Every type-driven button is run by one shared engine: _run_select/
_select_by_type do the "visible-only, or full search" logic once,
generically, instead of each button repeating it. Missing/Matching
classify by an item's diff tag (_tag_select_type); Smaller/Larger/
Newer/Older by every way it differs (_diff_axes/_item_select_attrs),
since the tag only keeps one -- see _RANK_SELECT_TYPES. Diff Folders shares the same _run_select plumbing
(progress/cancel/apply-selection) but supplies its own directory-based
collector and visible-fallback predicate, since a differing folder isn't
something a single tag captures.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from PySide6.QtCore import QItemSelection, QItemSelectionModel

from constants import _PANE_GAP
from constants import PATH_ROLE, IS_DIR_ROLE, POPULATED_ROLE, _DIFF_RANK, GNOME_COPIED_FILES_MIME
from listing_ops import _DATE_FIELD_TO_ENTRY_KEY, _DATE_FIELD_TO_COLUMN, COL_SIZE
from panes import DirectoryPane
from progress import _CancellableProgress
from diff_engine import AXES_UNHASHED, AXES_CONTENT_DIFFERS


class _NoTargets(Exception):
    """Internal control-flow sentinel for _select_paths_in_tree: raised
    when `paths` is empty so the empty-targets case can still fall
    through to that method's shared finally/notify tail below instead of
    returning early from inside the try and skipping it -- see there."""


# The four attribute types a _diff_axes result can name; anything else in
# it (the AXES_UNHASHED / AXES_CONTENT_DIFFERS sentinels) is not one.
_AXIS_TYPES = ("smaller", "larger", "newer", "older")


class SelectionMixin:
    # Display name + color for every Select-group type, used for the
    # per-type breakdown in the "Total items" label. Order here is also
    # display order in that label.
    _SELECT_TYPE_COLORS = {
        "missing": ("Missing", "#FFA0A0"),
        "matching": ("Matching", "#90E0C0"),
        "smaller": ("Smaller", "#D9BCF0"),
        "larger": ("Larger", "#B98CE0"),
        "newer": ("Newer", "#DECFA3"),
        "older": ("Older", "#C9A96E"),
        "diff_folders": ("Diff Items", "#7777DD"),
    }
    _SELECT_TYPE_ORDER = ["missing", "matching", "smaller", "larger", "newer", "older", "diff_folders"]

    # (want_rank, want_direction) for every Select-group type that's
    # driven by a plain per-item diff tag via _DIFF_RANK -- i.e. every
    # type except Matching (common_file/common_dir aren't part of the
    # rank/direction system, so it's checked by tag membership instead --
    # see _tag_select_type) and Diff Folders (which reasons about
    # directory aggregates via _dir_content_rank instead of a per-item
    # tag -- see _collect_diff_folders/select_diff_folders). The single
    # source of truth for "what does this tag mean".
    #
    # Only Missing is still actually *found* by its tag, though (plus
    # Matching, via _tag_select_type). Smaller/Larger/Newer/Older are
    # classified by _item_select_attrs/_diff_axes instead: an item's tag
    # can only name one difference -- a date difference wins over a size
    # one (see diff_engine._diff_tag) -- so a file that's both newer and
    # larger is tagged Newer, and going by the tag alone Larger could
    # never find it. The tag still decides the row's color.
    _RANK_SELECT_TYPES = {
        "missing": (3, None),
        "smaller": (1, "smaller"),
        "larger": (1, "larger"),
        "newer": (2, "newer"),
        "older": (2, "older"),
    }

    def _tag_select_type(self, tag: str):
        """Classify a single diff tag into a Select-group type key, or
        None if it's not one of the types Select buttons search for
        (e.g. an ordinary file/folder with nothing notable about it).
        Doesn't cover Diff Folders -- that's a directory-aggregate
        classification, not a per-item tag; see select_diff_folders.
        For Smaller/Larger/Newer/Older this only reports the one
        difference the tag kept -- use _item_select_attrs to find
        every difference (see _RANK_SELECT_TYPES)."""
        if tag in ("common_file", "common_dir"):
            return "matching"
        rank_direction = _DIFF_RANK.get(tag)
        for key, want in self._RANK_SELECT_TYPES.items():
            if rank_direction == want:
                return key
        return None

    @staticmethod
    def _attrs_from_axes(axes) -> tuple:
        """Smaller/Larger/Newer/Older type keys from a
        DiffEngineMixin._diff_axes result -- its directions *are* those
        type keys, so this just drops the attributes that agree. () for
        no counterpart (None) or no difference."""
        return tuple(d for d in axes if d in _AXIS_TYPES) if axes else ()

    def _item_select_attrs(self, pane: DirectoryPane, item, date_field) -> tuple:
        """Every attribute type (Smaller/Larger/Newer/Older) a tree row
        differs from its counterpart by -- possibly two at once, unlike
        its single tag (see _RANK_SELECT_TYPES). Compares the row's own
        displayed size and `date_field` date text, the same strings
        _diff_tag compared when tagging it. Used by the visible-only
        (Recursive Folder Selections off) predicate and count, where a
        folder row is compared by immediate item count and date -- the
        same comparison its own non-recursive tag came from."""
        return self._attrs_from_axes(self._item_diff_axes(pane, item, date_field))

    def _item_diff_axes(self, pane: DirectoryPane, item, date_field):
        """DiffEngineMixin._diff_axes for one tree row -- see
        _item_select_attrs."""
        other_full = self._counterpart_path(pane.tree, item.data(0, PATH_ROLE))
        return self._diff_axes(
            bool(item.data(0, IS_DIR_ROLE)), item.text(COL_SIZE),
            item.text(_DATE_FIELD_TO_COLUMN[date_field]), other_full, date_field,
            own_full=item.data(0, PATH_ROLE),
        )

    # ------------------------------------------- Last-used diff coloring
    #
    # A row's tag -- and so its color -- names only one difference: date
    # wins over size (see diff_engine._diff_tag). Pressing Smaller/Larger/
    # Newer/Older recolors the *selected* rows that differ that way to that
    # button's color instead, so a row that's both newer and larger reads
    # as Larger right after Larger was pressed. Undone per row when a
    # release (select=False) deselects it, and wholesale by Clear. Load
    # and Refresh rebuild every row through _diff_tag, so they come back
    # at the default ranking by themselves; so does a retag (a Metric or
    # filter change) -- nothing here needs hooking into those.
    #
    # Goes through _apply_tag, so the stored tag changes along with the
    # color -- the scrollbar's selection markers (see ui_builder.
    # _update_selection_markers) read that tag, and keep matching the
    # row. Nothing classifies by these four tags anymore (see
    # _RANK_SELECT_TYPES), and Missing/Matching/Diff Folders can't be
    # affected: only a row that genuinely differs both ways is ever
    # re-tagged, and only between diff tags.
    #
    # pane._diff_override_paths holds the recolored rows' paths (paths,
    # never QTreeWidgetItems -- those don't survive a rebuild safely).
    # It can go stale across a rebuild; that's harmless, since a reset
    # always recomputes each row's *default* tag rather than restoring
    # anything remembered.

    _ATTR_OPPOSITE = {"smaller": "larger", "larger": "smaller", "newer": "older", "older": "newer"}

    @staticmethod
    def _attr_tag(attr: str, is_dir: bool) -> str:
        """The diff tag naming one attribute difference -- the same
        names diff_engine._tag_from_rank builds."""
        if attr in ("newer", "older"):
            return f"diff_date_{attr}_{'dir' if is_dir else 'file'}"
        return f"diff_count_{attr}_dir" if is_dir else f"diff_size_{attr}_file"

    def _recolor_rows(self, tree, changes):
        """_apply_tag every (item, tag) in `changes`, with the tree's
        repaints suspended -- and, for a large batch, the tree hidden, per
        the same measured per-row setData() cost _select_paths_in_tree
        hides it for. Signals blocked: setBackground() fires itemChanged,
        and nothing listening to that should see a pure recolor."""
        if not changes:
            return
        do_hide = len(changes) > 50 and tree.isVisible()
        if do_hide:
            tree.setVisible(False)
        tree.setUpdatesEnabled(False)
        tree.blockSignals(True)
        try:
            for item, tag in changes:
                self._apply_tag(item, tag)
        finally:
            tree.blockSignals(False)
            tree.setUpdatesEnabled(True)
            if do_hide:
                tree.setVisible(True)

    def _apply_attr_color_override(self, pane: DirectoryPane, attr: str):
        """Recolor `pane`'s selected rows that differ by `attr` to
        `attr`'s color -- see the section comment above. Folder rows are
        left alone with Recursive Folder Contents on: they all paint as
        the one "Diff folder" color then (see _apply_tag), and their tag
        is an aggregate, not this per-row comparison."""
        tree = pane.tree
        date_field = self._pane_diff_date_field(pane)
        recursive = self._recursive_folder_contents_enabled()
        overrides = getattr(pane, "_diff_override_paths", None)
        if overrides is None:
            overrides = pane._diff_override_paths = set()
        changes = []
        for item in tree.selectedItems():
            is_dir = bool(item.data(0, IS_DIR_ROLE))
            if is_dir and recursive:
                continue
            if attr not in self._item_select_attrs(pane, item, date_field):
                continue
            tag = self._attr_tag(attr, is_dir)
            if item.data(0, Qt.UserRole) != tag:
                changes.append((item, tag))
                overrides.add(os.path.normpath(item.data(0, PATH_ROLE)))
        self._recolor_rows(tree, changes)

    def _reset_diff_color_overrides(self, pane: DirectoryPane, only_unselected: bool = False):
        """Put `pane`'s recolored rows back to their default-ranked tag
        (date over size, recomputed fresh -- see the section comment
        above). only_unselected keeps the ones still selected as they
        are: a button release, or the other pane's side of a Clear with
        Synchronize Views on."""
        overrides = getattr(pane, "_diff_override_paths", None)
        if not overrides:
            return
        tree = pane.tree
        date_field = self._pane_diff_date_field(pane)
        recursive = self._recursive_folder_contents_enabled()
        remaining = set()
        changes = []
        # Explicit stack rather than _iter_items' recursive generator --
        # see qt-pitfalls on deep `yield from` chains.
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack.extend(item.child(i) for i in range(item.childCount()))
            path = item.data(0, PATH_ROLE)
            norm = os.path.normpath(path) if path else None
            if norm not in overrides:
                continue
            if only_unselected and item.isSelected():
                remaining.add(norm)
                continue
            is_dir = bool(item.data(0, IS_DIR_ROLE))
            if is_dir and recursive:
                continue
            axes = self._item_diff_axes(pane, item, date_field)
            if not axes:
                continue
            size_direction, date_direction = axes
            default_attr = date_direction or size_direction
            if default_attr is None:
                continue
            default_tag = self._attr_tag(default_attr, is_dir)
            if item.data(0, Qt.UserRole) != default_tag:
                changes.append((item, default_tag))
        pane._diff_override_paths = remaining
        self._recolor_rows(tree, changes)

    def _start_select_progress(self, pane: DirectoryPane, initiator_pane: DirectoryPane = None,
                                total=None) -> "_CancellableProgress":
        """Arm the shared central Transfer-group bar + Cancel button
        (see TransferMixin) for a Select-group search and hand back a
        token the search loop checks periodically. `total`, if given,
        overrides the default of `pane.item_count`.

        `initiator_pane` -- defaulting to `pane` itself -- is who the
        progress bar's direction should be attributed to (see
        TransferMixin._start_transfer_progress), for a caller that
        searches one pane on behalf of a button clicked on the other."""
        if initiator_pane is None:
            initiator_pane = pane
        if total is None:
            total = pane.item_count or None
        return self._start_transfer_progress(total=total, label="Searching...", pane=initiator_pane)

    def _finish_select_progress(self, pane: DirectoryPane, result_count: "int | None" = None):
        """Wraps _finish_transfer_progress for a Select-group search --
        deliberately never passes result_count through to it (which
        would let _show_result_count overwrite the label with a
        generic "N items (duration)" summary). By the time this runs,
        update_selection_count has *usually* already written this exact
        selection's own "N selected, X files, Y folders, <size>"
        breakdown to the same label (see _select_paths_in_tree's tail,
        or the non-recursive path in _run_select) -- a search's own
        result count *is* that same selection, so all that's still
        needed is the timing, appended onto that existing text rather
        than replacing it (see _append_operation_duration).

        "Usually" above is the catch this method exists to close:
        _select_paths_in_tree's own tail skips update_selection_count
        entirely when nothing was toggled and nothing pre-existing got
        cleared (an intentional O(n^2)-avoiding fast path for its many
        other, higher-volume callers -- see its own docstring). A
        search that matched nothing (Missing/Newer/etc with zero hits)
        is exactly that "nothing toggled" case, so the label was just
        left showing whatever it happened to have before this search
        ran -- a stale leftover result (e.g. a previous Paste's own "1
        item") with this search's own duration tacked onto the end of
        it, reading as if *that* was this search's result. Forcing one
        more recompute here, unconditionally, right before the timing
        gets appended, means this specific call site -- reporting a
        finished operation's result to the user -- always reflects
        what's actually selected right now, while every higher-volume
        caller in the middle of a bulk change still gets to skip it."""
        self.update_selection_count(pane.tree, pane)
        self._finish_transfer_progress()
        self._append_operation_duration()

    def _cancel_select_progress(self, pane: DirectoryPane):
        """Cancel button: stops the in-progress search (or item recount --
        see _recount_pane_items). A search's results are only ever applied
        to the tree's selection once, in one shot, after the whole search
        finishes -- so cancelling simply discards whatever had been found
        so far rather than needing to undo a partial selection. A
        cancelled recount just keeps whatever partial count it reached."""
        self._cancel_transfer_progress()

    def _on_manual_tree_selection(self, pane: DirectoryPane):
        """Any selection change not driven by the Select group's own
        machinery -- a plain click, ctrl/shift-click, drag-select, or
        keyboard navigation directly in the tree -- means whatever's
        selected now no longer necessarily matches any of the checkable
        Select-group buttons' tracked sets (Missing/Matching/Smaller/
        Larger/Newer/Older/Diff Folders), so pop them all back up, same
        as Clear/Invert already do, without touching the selection
        itself.

        Suppressed via _select_group_guard while a Select-group
        operation (a search, Invert, Clear, File Parent/All Parents, a
        post-transfer/paste selection) is itself actively driving the
        change -- see _select_paths_in_tree/invert_selection/
        clear_selection/_run_select's non-recursive branch -- otherwise
        pressing one of these toggle buttons would see its own selection
        change come through here and immediately un-toggle itself.
        Also suppressed via _sync_guard while Synchronize Views is
        mirroring a change from the other pane, for the same reason.
        Also suppressed while pane._tree_rebuild_pending is set (see
        listing_ops.update_listing) -- tree.clear()'s own signal is
        blocked during the call itself, but this can still be reached
        reentrant if a leftover, already-scheduled Qt event/timer from
        before the rebuild gets a chance to run during the rebuild's own
        progress-driven processEvents() pumping; unchecking buttons
        based on a tree that's mid-teardown/still-repopulating isn't
        meaningful anyway, and update_listing settles this same state
        correctly itself once it's actually done."""
        if (getattr(self, "_select_group_guard", False) or getattr(self, "_sync_guard", False)
                or getattr(pane, "_tree_rebuild_pending", False)):
            pane._multi_prev = None
            return
        for btn in pane.select_toggle_buttons.values():
            if btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
        self._select_duplicates_of_new(pane)

    def _selected_path_set(self, pane: DirectoryPane) -> set:
        return {os.path.normpath(p) for p in
                (i.data(0, PATH_ROLE) for i in pane.tree.selectedItems()) if p}

    def _shown_paths(self, pane: DirectoryPane) -> set:
        """Normalized paths of the rows actually listed (populated) in the pane."""
        shown = set()
        stack = [pane.tree.topLevelItem(i) for i in range(pane.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            shown.add(os.path.normpath(it.data(0, PATH_ROLE) or ""))
            if it.data(0, POPULATED_ROLE):
                stack.extend(it.child(i) for i in range(it.childCount()))
        return shown

    def _hash_groups_index(self, pane: DirectoryPane):
        """{digest: [file nodes]} over the pane's whole snapshot, built
        once and kept until the next snapshot rebuild / settings change
        (see DiffEngineMixin._invalidate_dir_rank_cache)."""
        cached = getattr(pane, "_hash_groups", None)
        if cached is not None:
            return cached
        groups = {}
        for node in (pane.snapshot_index or {}).values():
            if not node["is_dir"]:
                digest = self._node_hash(node)
                if digest is not None:
                    groups.setdefault(digest, []).append(node)
        pane._hash_groups = groups
        return groups

    def _select_duplicates_of_new(self, pane: DirectoryPane):
        """Hash / Fingerprint comparison with "Multi Select matching files
        in same pane" on: files newly selected by hand (click, Ctrl-click,
        Shift-click range, drag) pull in the files with identical content --
        in the same folder only, or (that option off) anywhere in the pane.
        Without a previous selection to diff against (pane._multi_prev is
        None after a Select-group operation) only the item just clicked
        counts. Deselecting never pulls anything in or out."""
        previous = getattr(pane, "_multi_prev", None)
        if (getattr(self, "_dup_select_guard", False)
                or not getattr(self, "_multi_select_matching", True)
                or not self._hash_mode_active()):
            pane._multi_prev = None
            return
        current = self._selected_path_set(pane)
        if previous is None:
            item = pane.tree.currentItem()
            new = {os.path.normpath(item.data(0, PATH_ROLE))} if (
                item is not None and item.isSelected() and item.data(0, PATH_ROLE)) else set()
        else:
            new = current - previous
        same_folder = getattr(self, "_multi_select_same_folder", True)
        recursive = self._recursive_folder_contents_enabled()
        extra = []
        for path in new:
            node = self._snapshot_lookup(path)
            if node is None or node["is_dir"]:
                continue
            digest = self._node_hash(node)
            if digest is None:
                continue
            if same_folder:
                parent = self._snapshot_lookup(os.path.dirname(path))
                pool = [c for c in (parent["children"] or {}).values()] if parent else []
            else:
                pool = self._hash_groups_index(pane).get(digest, [])
            for c in pool:
                cp = os.path.normpath(c["full_path"])
                if (not c["is_dir"] and cp not in current and self._node_hash(c) == digest):
                    extra.append(c["full_path"])
                    current.add(cp)
        if not same_folder and not recursive and extra:
            # Pane-wide, but only what is actually listed (populated).
            shown = self._shown_paths(pane)
            keep = [p for p in extra if os.path.normpath(p) in shown]
            current -= {os.path.normpath(p) for p in extra if p not in keep}
            extra = keep
        if extra:
            self._dup_select_guard = True
            try:
                self._select_paths_in_tree(
                    pane.tree, extra, additive=True, select_subtree=False,
                    auto_expand=self._expand_folders_matching_selection_enabled(),
                    expand_matched_dirs=False)
            finally:
                self._dup_select_guard = False
        pane._multi_prev = self._selected_path_set(pane)

    def update_selection_count(self, tree, pane: DirectoryPane):
        if getattr(self, "_bulk_select_guard", False) or getattr(pane, "_tree_rebuild_pending", False):
            # A bulk selection change (a Select-group search's apply
            # step, the non-recursive select-visible loop below) is
            # mid-flight, driving many individual setSelected() calls in
            # a row -- see _select_paths_in_tree. Recomputing this (an
            # O(selected items) scan) on every single one of those calls
            # turned an O(n) selection into an O(n^2) one; the caller
            # recomputes this itself exactly once, right after the whole
            # batch finishes, instead.
            #
            # pane._tree_rebuild_pending (set for the whole rebuild, not
            # just around tree.clear() -- see listing_ops.update_listing)
            # guards something more serious than a wasted recompute:
            # tree.selectedItems()/item.data() below touch live
            # QTreeWidgetItems, and reaching this reentrant -- via a
            # leftover, already-scheduled Qt event/timer getting a
            # chance to run during the rebuild's own progress-driven
            # processEvents() pumping -- would do that while the tree is
            # either already cleared or still only half-repopulated.
            # update_listing calls this itself, unconditionally, once
            # it's actually finished (see its tail), so this still ends
            # up correct either way.
            return
        selected_items = tree.selectedItems()
        total_size = 0
        file_count = 0
        dir_count = 0
        for item in selected_items:
            if item.data(0, IS_DIR_ROLE):
                dir_count += 1
            else:
                file_count += 1
            path = item.data(0, PATH_ROLE)
            if path:
                # _path_size(path) is a folder's own recursive total for
                # a directory -- if one of this item's ancestors is ALSO
                # currently selected (select_subtree=True cascades a
                # folder selection onto everything under it, e.g. Select
                # All; Statistics View's Folder mode can also just as
                # easily leave a parent and child folder selected
                # independently, with no cascade involved at all), that
                # ancestor's own _path_size already includes these same
                # on-disk bytes. Adding this item's size too counted them
                # a second (or, several folders deep, a third, fourth...)
                # time -- the deeper/more broadly something was
                # selected, the further the reported total drifted above
                # the real one.
                ancestor = item.parent()
                shadowed = False
                while ancestor is not None:
                    if ancestor.isSelected():
                        shadowed = True
                        break
                    ancestor = ancestor.parent()
                if not shadowed:
                    total_size += self._path_size(path)
        selected_count = file_count + dir_count
        # Also feeds the Statistics view's own upper-corner "N selected"
        # readout (see TimelineWidget.set_selected_total_size) -- that
        # widget's own internal calculation only ever sums whichever
        # single type (files, or folders) this pane's Files/Folders
        # toggle currently has loaded, so a selection spanning both
        # types needs this already-correct, de-duplicated combined
        # figure pushed in from here instead.
        timeline = getattr(pane, "timeline", None)
        if timeline is not None:
            timeline.set_selected_total_size(total_size)
        # Moved from the Select group's own title (setTitle/setStyleSheet
        # there) to the status line's left/right selection labels -- see
        # ui_builder._build_status_line -- so the Select group's title
        # can go back to being a plain, droppable "Select" (see
        # ui_builder._update_responsive_layout's narrow-width title
        # blanking) instead of carrying this count. is_left picks which
        # of the two status-line labels this pane's count belongs on;
        # the right one is hidden outright with Dual Pane off (see
        # _rebuild_status_bars_row), so there's nothing to route to on
        # that side in that case.
        is_left = pane is self.left
        label = self.left_selection_label if is_left else getattr(self, "right_selection_label", None)
        if label is not None:
            label.setText(
                f"{selected_count} selected, {file_count} files, {dir_count} folders, "
                f"{self.format_size(total_size)}"
            )
            # Yellow while something's actually selected -- otherwise no
            # color override at all, so it follows the theme's normal
            # text color instead of a hardcoded #F5F5F5 that only ever
            # looked right on Dark (see ui_builder.py's own title-color
            # cleanup for the same fix applied to this label's initial
            # stylesheet).
            label.setStyleSheet(self._status_line_style(bool(selected_count)))

    def _count_visible_types(self, pane: DirectoryPane):
        """Tally *currently visible/expanded on screen* items -- collapsed
        branches don't contribute, even if they were populated earlier
        (see _iter_visible_items) -- into each Select-group type, via the
        same classification (_tag_select_type) the tag-based Select
        buttons themselves fall back to when a full recursive filesystem
        search isn't running. Used for the "Total items" breakdown while
        Recursive Folder Selections is off; see _count_recursive_types
        for the on case.

        Returns (counts, total_count, total_size): counts is the
        per-type dict; total_count/total_size cover every visible item
        once, for the plain "Total items" figure."""
        counts = {key: 0 for key in self._SELECT_TYPE_COLORS}
        total_count = 0
        total_size = 0
        date_field = self._pane_diff_date_field(pane)
        for item in self._iter_visible_items(pane.tree):
            total_count += 1
            is_dir = bool(item.data(0, IS_DIR_ROLE))
            # A directory's own stored size (COL_SIZE/Qt.UserRole -- see
            # listing_ops._make_child_items) is its recursive total,
            # children included. Adding that AND separately walking into
            # an expanded folder's own now-visible children counted
            # every one of those children's bytes twice -- once folded
            # into the parent folder's own figure, once again for each
            # child itself. A collapsed folder still needs its rollup
            # size counted here, since nothing else in this loop will
            # ever visit its (not currently visible) children to count
            # them individually.
            if not is_dir or not item.isExpanded():
                total_size += item.data(1, Qt.UserRole) or 0
            tag = item.data(0, Qt.UserRole) or ""
            # Missing/Matching by tag; Smaller/Larger/Newer/Older by
            # every difference, not just the one the tag kept -- see
            # _RANK_SELECT_TYPES. An item that's both newer and larger
            # counts toward both, same as both buttons would find it.
            type_key = self._tag_select_type(tag)
            if type_key in ("missing", "matching"):
                counts[type_key] += 1
            else:
                for attr in self._item_select_attrs(pane, item, date_field):
                    counts[attr] += 1
            if (is_dir and tag in self._DIR_DIFF_TAGS) or tag == "diff_content_file":
                counts["diff_folders"] += 1
        return counts, total_count, total_size

    def _count_recursive_types(self, pane: DirectoryPane):
        """Full recursive filesystem walk tallying every Select-group
        type at once -- missing/matching/smaller/larger/newer/older/
        diff_folders -- against pane's counterpart directory, at any
        depth, regardless of what's actually been expanded on screen.
        Used for the "Total items" breakdown while Recursive Folder
        Selections is on (see _count_visible_types for the off case,
        which only looks at what's already expanded instead).

        Files are classified the same way _tag_select_type does (one
        combined walk instead of a separate one per type, the way
        clicking each Select button individually would); directories are
        classified by their own aggregate rank (_dir_content_rank), same
        as Diff Folders' own search, since a folder's rank isn't a
        per-item tag. Unlike select_diff_folders's own search, this keeps
        descending into a differing folder instead of stopping there, so
        the individual files causing the difference are still tallied
        too, not just the folder itself. Deliberately not
        cancellable/progress-shown (unlike _recount_pane_items) -- worth
        revisiting if this turns out to be slow on very large trees,
        since it re-walks the whole thing on every listing refresh while
        this setting is on."""
        counts = {key: 0 for key in self._SELECT_TYPE_COLORS}
        total_count = 0
        total_size = 0

        directory = pane.entry.text()
        if not directory or not self._isdir(directory):
            return counts, 0, 0
        other_pane = self.right if pane is self.left else self.left
        other_root = other_pane.entry.text()
        if not other_root or not self._isdir(other_root):
            other_root = None

        # This pane's own Statistics-view Metric decides which date
        # field (mtime/ctime/atime) "Newer"/"Older" means here too -- see
        # TimelineOpsMixin._pane_diff_date_field -- resolved once for the
        # whole walk rather than per entry, since it depends only on
        # `pane`, not on anything the walk itself discovers.
        date_field = self._pane_diff_date_field(pane)
        date_entry_key = _DATE_FIELD_TO_ENTRY_KEY[date_field]

        def walk(d, other_d):
            nonlocal total_count, total_size
            entries = self._build_entries(d)
            if entries is None:
                return
            for e in entries:
                total_count += 1
                if not e["is_dir"]:
                    total_size += e["size_raw"]
                # A directory's own e["size_raw"] is already its full
                # recursive total (see settings_ops.py's module
                # docstring) -- this walk always fully descends into
                # every folder regardless of on-screen expansion state,
                # so every file under it gets its size added exactly
                # once anyway, right here, at its own entry. Adding the
                # folder's rollup on top of that (as this used to) added
                # the same bytes again for every ancestor folder above
                # it -- a file three folders deep ended up counted four
                # times over.
                if e["is_dir"]:
                    other_full = os.path.join(other_d, e["name"]) if other_d else None
                    has_other = bool(other_full and self._isdir(other_full))
                    if not has_other:
                        # No counterpart -- either directly (other_d is
                        # set but nothing named e["name"] exists there),
                        # or because other_d is already None here, i.e.
                        # this call is itself recursing inside a folder
                        # that was just counted missing one level up.
                        # Either way there's nothing to compare against,
                        # so this folder (and, via the walk() call below,
                        # everything inside it) counts as missing too --
                        # previously only the top folder of a missing
                        # subtree ever got tallied; every file and
                        # subfolder underneath it fell through both this
                        # branch and the file branch below (neither
                        # matched with other_d as None) and simply never
                        # got counted as anything, undercounting Missing
                        # by however much content that subtree actually had.
                        counts["missing"] += 1
                    else:
                        rank, _direction = self._dir_content_rank(e["full_path"], other_full, date_field)
                        if rank > 0:
                            counts["diff_folders"] += 1
                        elif rank == 0:
                            counts["matching"] += 1
                        # rank < 0: not hashed yet -- counted as neither.
                    walk(e["full_path"], other_full if has_other else None)
                elif other_d:
                    # _diff_axes rather than the single _diff_tag -- see
                    # _RANK_SELECT_TYPES: None means no comparable
                    # counterpart (what the tag calls missing), no
                    # differences means matching, and otherwise every
                    # difference counts, so a newer-and-larger file is
                    # tallied under both Newer and Larger.
                    axes = self._diff_axes(False, e["size_str"], e[date_entry_key],
                                           os.path.join(other_d, e["name"]), date_field,
                                           own_full=e["full_path"])
                    if axes is None:
                        counts["missing"] += 1
                    elif axes is AXES_UNHASHED:
                        pass  # not hashed yet: counted as neither
                    elif axes is AXES_CONTENT_DIFFERS:
                        counts["diff_folders"] += 1
                    else:
                        attrs = self._attrs_from_axes(axes)
                        if not attrs:
                            counts["matching"] += 1
                        for attr in attrs:
                            counts[attr] += 1
                else:
                    # Same reasoning as the directory branch above: a
                    # file with no counterpart directory to even look in
                    # (other_d is None) is missing, not uncategorized.
                    counts["missing"] += 1

        walk(directory, other_root)
        return counts, total_count, total_size

    def update_type_counts(self, pane: DirectoryPane):
        """Refresh pane.total_items_label (the bold "Total: N items
        (size)" figure on the toolbar row above the tree) and
        pane.count_label (the colored per-type breakdown for all seven
        Select-group types below the tree, always shown even at zero so
        its width stays steady). With Recursive Folder Selections on,
        this is a full recursive filesystem tally
        (_count_recursive_types) -- "everything", regardless of what's
        expanded on screen. With it off, it's scoped to whatever's
        actually visible/expanded right now instead
        (_count_visible_types) -- collapsing a folder removes its
        contents from the count again. This mirrors the same on/off
        split the Select buttons themselves now use (see _run_select).

        Call this whenever the tree's contents or expansion state
        changes -- it does not run on selection changes; that's
        update_selection_count, which drives the Select group's own
        title instead."""
        # The toolbar's "Free:" figure shares this line and this color (Title
        # Numeric) with the "Total:" label below, so it's re-rendered
        # whenever that one is -- which is also what picks up a changed
        # Title Numeric color.
        self._update_free_space_label(pane)
        total_items_label = getattr(pane, "total_items_label", None)
        count_label = getattr(pane, "count_label", None)
        if not total_items_label and not count_label:
            return
        if self._recursive_folder_contents_enabled():
            counts, total_count, total_size = self._count_recursive_types(pane)
        else:
            counts, total_count, total_size = self._count_visible_types(pane)
        # Also feeds the Statistics view's own upper-corner readout's
        # "total" figure (see TimelineWidget.set_total_size_override) --
        # this total_size already covers everything in the pane's own
        # listing (files and folders together, per whichever of
        # _count_recursive_types/_count_visible_types ran above), unlike
        # that widget's own internal self._total_size, which is only
        # ever whichever single type (files, or folders) the Files/
        # Folders toggle currently has loaded.
        timeline = getattr(pane, "timeline", None)
        if timeline is not None:
            timeline.set_total_size_override(total_size)
        if total_items_label:
            # "Total: X items (Y size)" -- only the two figures (the count,
            # and the size with its unit) get the Settings tab's "Title
            # Numeric Colors" swatch (see UiBuilderMixin._numeric_span);
            # "Total:", "items" and the parentheses are left uncolored so
            # they follow the theme's normal text color.
            noun = "item" if total_count == 1 else "items"
            nbsp = "\u00a0"
            size_text = self.format_size(total_size).replace(" ", nbsp)
            # Words that belong together stay together when the label wraps
            # (see _render_toolbar_labels).
            pane._total_atoms = [
                ("Total:", "Total:"),
                (f"{total_count}{nbsp}{noun}", f"{self._numeric_span(total_count)}{nbsp}{noun}"),
                (f"({size_text})", f"({self._numeric_span(size_text)})"),
            ]
            self._render_toolbar_labels()
        if count_label:
            pane._last_type_counts = counts
            self._render_type_counts(pane)

    def _render_type_counts(self, pane: DirectoryPane = None):
        """Re-render the colored per-type summary below the trees (both
        panes' -- see _render_all_type_counts) from the last counts
        update_type_counts computed, using the Settings tab's Comparison
        Type Colors -> Summary Text Color. No recount, so a color change
        is instant even with Recursive Folder Selections on."""
        self._render_all_type_counts()

    _TYPE_COUNT_SEPARATOR = "\u00a0\u00a0\u00b7\u00a0\u00a0"

    def _type_count_parts(self, pane: DirectoryPane) -> list:
        """[(plain text, html)] for each per-type count of `pane`."""
        counts = getattr(pane, "_last_type_counts", None) or {}
        summary_colors = getattr(self, "_summary_colors", None) or {}
        parts = []
        for key in self._SELECT_TYPE_ORDER:
            label, default_color = self._SELECT_TYPE_COLORS[key]
            color = summary_colors.get(key, default_color)
            plain = f"{label}: {counts.get(key, 0)}".replace(" ", "\u00a0")
            parts.append((plain, f'<span style="color:{color};">{plain}</span>'))
        return parts

    def _type_count_split(self, widths: list, lines: int, sep: int = None) -> list:
        """The consecutive split of the per-type counts into `lines` lines
        with the narrowest widest line: ([[part indexes] per line],
        widest line px). `widths` are the parts' own widths; `sep` the
        width of what goes between two parts on a line."""
        from itertools import combinations
        count = len(widths)
        if sep is None:
            sep = self._type_count_separator_width
        best = None
        for breaks in combinations(range(1, count), min(lines, count) - 1):
            bounds = (0, *breaks, count)
            groups = [list(range(bounds[i], bounds[i + 1])) for i in range(len(bounds) - 1)]
            widest = max(sum(widths[i] for i in g) + sep * (len(g) - 1) for g in groups)
            if best is None or widest < best[1]:
                best = (groups, widest)
        return best

    def _render_all_type_counts(self):
        """Lays out the per-type summary under each file list: one line if
        it fits, otherwise two, three... lines. Both panes always get the
        same number of lines (the larger of what each needs), so their
        layouts stay in step even though their counts differ. Called when
        counts or colors change and whenever a pane's file list widget is
        resized."""
        left = getattr(self, "left", None)
        if left is None or getattr(left, "count_label", None) is None:
            return
        panes = [left]
        right = getattr(self, "right", None)
        if self._dual_pane_enabled and right is not None and getattr(right, "count_label", None) is not None:
            panes.append(right)
        try:
            plans = []
            for pane in panes:
                label = pane.count_label
                metrics = label.fontMetrics()
                parts = self._type_count_parts(pane)
                widths = [metrics.horizontalAdvance(plain) for plain, _html in parts]
                self._type_count_separator_width = metrics.horizontalAdvance(self._TYPE_COUNT_SEPARATOR)
                # Room beside the Expand/Collapse buttons in the same row.
                spacing = max(0, label.parentWidget().layout().spacing()) if label.parentWidget() and label.parentWidget().layout() else 6
                avail = self._shared_width_for(pane, pane.tree) - 2 * 32 - 4 * spacing - 4
                needed = len(parts)
                for lines in range(1, len(parts) + 1):
                    _groups, widest = self._type_count_split(widths, lines)
                    if widest <= avail:
                        needed = lines
                        break
                plans.append((pane, parts, widths, needed, metrics.height()))
            lines = max(plan[3] for plan in plans)
            for pane, parts, widths, _needed, line_height in plans:
                groups, _widest = self._type_count_split(widths, lines)
                html_lines = [
                    "&nbsp;&nbsp;\u00b7&nbsp;&nbsp;".join(parts[i][1] for i in group) for group in groups
                ]
                text = "<br>".join(html_lines) if getattr(pane, "_last_type_counts", None) is not None else ""
                if pane.count_label.text() != text:
                    pane.count_label.setText(text)
                pane.count_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                height = lines * line_height
                if pane.count_label.minimumHeight() != height:
                    pane.count_label.setMinimumHeight(height)
        except RuntimeError:
            pass  # widgets from before a UI rebuild, already deleted

    # ---------------------------------------------- Toolbar total/free labels

    def _render_toolbar_labels(self):
        """Lays out the "Total: N items (size)" and "Free: size" labels on
        the toolbar row above each file list: on one line while they fit
        between the buttons, otherwise on two or three lines. Words that
        belong together stay together ("120 items", "(56.7 MB)"). Like the
        per-type counts (_render_all_type_counts), both panes always get
        the same number of lines so their layouts stay in step."""
        left = getattr(self, "left", None)
        if left is None or getattr(left, "total_items_label", None) is None:
            return
        panes = [left]
        right = getattr(self, "right", None)
        if self._dual_pane_enabled and right is not None and getattr(right, "total_items_label", None) is not None:
            panes.append(right)
        try:
            from PySide6.QtGui import QFont, QFontMetrics
            plans = []
            for pane in panes:
                total_atoms = getattr(pane, "_total_atoms", None) or []
                free_atoms = getattr(pane, "_free_atoms", None) or []
                font = QFont(pane.total_items_label.font())
                font.setBold(True)
                metrics = QFontMetrics(font)
                space = metrics.horizontalAdvance(" ")
                total_w = [metrics.horizontalAdvance(plain) for plain, _html in total_atoms]
                free_w = [metrics.horizontalAdvance(plain) for plain, _html in free_atoms]
                buttons = (
                    pane.up_button, pane.select_button, pane.save_snapshot_button,
                    pane.hash_button, pane.clear_view_button,
                )
                fixed = sum(b.sizeHint().width() for b in buttons) + 3 * 6 + 8
                spacing = pane._toolbar_layout.spacing() if getattr(pane, "_toolbar_layout", None) else 6
                fixed += 3 * max(0, spacing - 6)
                avail = self._shared_width_for(pane, pane.tree)
                longest = max(len(total_atoms), len(free_atoms), 1)
                needed = longest
                for lines in range(1, longest + 1):
                    widest = 0
                    for widths in (total_w, free_w):
                        if widths:
                            widest += self._type_count_split(widths, min(lines, len(widths)), space)[1]
                    if fixed + widest <= avail:
                        needed = lines
                        break
                plans.append((pane, total_atoms, free_atoms, total_w, free_w, space, needed, metrics.height()))
            lines = max(plan[6] for plan in plans)
            for pane, total_atoms, free_atoms, total_w, free_w, space, _needed, line_height in plans:
                for label, atoms, widths in (
                    (pane.total_items_label, total_atoms, total_w),
                    (pane.free_space_label, free_atoms, free_w),
                ):
                    if widths:
                        groups, _widest = self._type_count_split(widths, min(lines, len(widths)), space)
                        text = "<br>".join(" ".join(atoms[i][1] for i in group) for group in groups)
                    else:
                        text = ""
                    if label.text() != text:
                        label.setText(text)
                    label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                    height = lines * line_height
                    if label.minimumHeight() != height:
                        label.setMinimumHeight(height)
        except (RuntimeError, AttributeError):
            pass  # widgets from before a UI rebuild, or not built yet

    def _relayout_pane_labels(self):
        """Re-wraps every label that wraps with its pane's width -- called
        when a pane's file list widget is resized."""
        self._render_all_type_counts()
        self._render_toolbar_labels()
        self._render_stats_totals()

    def _shared_width_for(self, pane: DirectoryPane, widget) -> int:
        """The width `widget` (inside `pane`) will have once both panes
        have settled: half of the shared panes row minus this widget's own
        offset inside its pane. Used instead of widget.width() so both
        panes get identical results in the same pass, even while one of
        them has not received its own resize event yet."""
        row = getattr(self, "_panes_row", None)
        root = widget
        while root is not None and root.parentWidget() is not row:
            root = root.parentWidget()
        if row is None or root is None or row.width() < 300:
            return widget.width()
        half = (row.width() - _PANE_GAP) // 2 if self._dual_pane_enabled else row.width()
        return half - max(0, root.width() - widget.width())

    def _render_stats_totals(self):
        """Lays out the "Files: X - Folders: Y" count above each
        Statistics View: one line if it fits beside the title and buttons,
        otherwise "Files: X" and "Folders: Y" on two lines (never split
        inside either). Both panes always get the same number of lines."""
        left = getattr(self, "left", None)
        if left is None or getattr(left, "tl_totals_label", None) is None:
            return
        panes = [left]
        right = getattr(self, "right", None)
        if self._dual_pane_enabled and right is not None and getattr(right, "tl_totals_label", None) is not None:
            panes.append(right)
        try:
            plans = []
            for pane in panes:
                label = pane.tl_totals_label
                atoms = getattr(pane, "_stats_atoms", None) or []
                metrics = label.fontMetrics()
                sep = metrics.horizontalAdvance(" - ")
                widths = [metrics.horizontalAdvance(plain) for plain, _html in atoms]
                row = pane._stats_title_row
                others = 0
                count = row.count()
                for i in range(count):
                    item = row.itemAt(i)
                    if item.widget() is label:
                        continue
                    others += item.sizeHint().width()
                others += max(0, row.spacing()) * (count - 1)
                avail = self._shared_width_for(pane, pane._stats_widget)
                margins = pane._stats_widget.layout().contentsMargins()
                avail -= margins.left() + margins.right() + others
                one_line = sum(widths) + sep * max(0, len(widths) - 1)
                needed = 1 if (not widths or one_line <= avail) else len(widths)
                plans.append((pane, atoms, needed, metrics.height()))
            lines = max(plan[2] for plan in plans)
            for pane, atoms, _needed, line_height in plans:
                if not atoms:
                    text = ""
                elif lines == 1:
                    text = " - ".join(html for _plain, html in atoms)
                else:
                    text = "<br>".join(html for _plain, html in atoms)
                label = pane.tl_totals_label
                if label.text() != text:
                    label.setText(text)
                height = lines * line_height
                if label.minimumHeight() != height:
                    label.setMinimumHeight(height)
        except (RuntimeError, AttributeError):
            pass  # widgets from before a UI rebuild, or not built yet

    # ------------------------------------------------------ Search engine

    def _run_select(self, pane: DirectoryPane, collector, visible_predicate,
                     select_subtree=True, require_other_root=False, select=True,
                     initiator_pane: DirectoryPane = None, progress: "_CancellableProgress" = None):
        """Shared engine behind every Select-group search button.

        With Recursive Folder Selections off, just selects among items
        already visible/expanded on screen right now (see
        _iter_visible_items) -- `visible_predicate(item)` decides which
        ones, no filesystem walk.

        With it on, always runs a real recursive filesystem search --
        `collector(root_dir, other_root, progress)`, returning a list of
        absolute paths -- regardless of Expand To Selection; that setting
        only controls whether the matches found get visually revealed
        (auto-expanded) or just selected in place (see
        _select_paths_in_tree). require_other_root controls what happens
        if the other pane's root isn't a valid directory: True aborts
        the search entirely (there's nothing to compare against, e.g.
        Matching or Diff Folders); False still runs the search with
        other_root=None (meaningful for Missing -- everything is missing
        if there's nowhere to compare against -- and a harmless no-op for
        the rest, since nothing can be Smaller/Larger/Newer/Older than a
        counterpart that doesn't exist).

        select controls whether the matches get selected (True, the
        default -- a toggle button being pressed on) or deselected
        (False -- the same button released again). Deselect re-runs the
        exact same search/predicate rather than remembering what got
        selected on the way in, so it stays correct even if the listing
        changed in between; it only ever touches items matching *this*
        button's own type, so a file matching two types at once (e.g.
        both Larger and Older) simply follows whichever button was just
        pressed or released, regardless of any other toggle button's
        current state -- see _select_paths_in_tree.

        `progress`, if given, is an already-armed token this call
        contributes ticks to rather than starting/finishing its own --
        for a caller running this as one step of a larger progress
        cycle. Without one, this starts and finishes its own.
        Either way, the same token also drives the apply-selection step
        afterward (_select_paths_in_tree) rather than that running
        silently and uncancellably once the search itself is done.

        initiator_pane is forwarded to _start_select_progress -- see its
        docstring for why it can differ from `pane`.

        With Synchronize Views on, the resulting selection change is
        mirrored onto the other pane through the same generic path a
        manual selection (or a Statistics-view bin selection) takes --
        SyncMixin._sync_selection_from, run once after this pane's own
        selection is final: _select_paths_in_tree's own tail on the
        recursive path, an explicit call on the visible-only path below.
        Skipped if a caller holds _sync_guard (it owns synchronization
        itself then -- e.g. _sync_selection_from's own mirror walk).

        Returns the number of matches found/toggled -- used to report
        the resulting count above the bar (see _show_result_count)."""
        tree = pane.tree
        if not self._recursive_folder_contents_enabled():
            # Still a programmatic, Select-group-driven selection change
            # (not a manual click in the tree), so guarded the same way
            # as the recursive-search path below -- see
            # _on_manual_tree_selection -- otherwise a toggle button
            # pressed with Recursive Folder Selections off would
            # immediately see its own selection change and un-toggle
            # itself.
            guard_was_set = getattr(self, "_select_group_guard", False)
            self._select_group_guard = True
            bulk_guard_was_set = getattr(self, "_bulk_select_guard", False)
            self._bulk_select_guard = True
            # Instant path -- never touches the progress bar/Cancel
            # button, but still needs _begin_timed_operation for two
            # things _start_transfer_progress would otherwise have set
            # up: an accurate timer (so the duration appended below
            # isn't measured from whatever operation happened to run
            # last) and the correct side's status label (see
            # _append_operation_duration/update_selection_count below).
            self._begin_timed_operation(initiator_pane or pane)
            count = 0
            try:
                for item in self._iter_visible_items(tree):
                    if visible_predicate(item):
                        item.setSelected(select)
                        count += 1
            finally:
                self._select_group_guard = guard_was_set
                self._bulk_select_guard = bulk_guard_was_set
            if count:
                self.update_selection_count(tree, pane)
                self._sync_timeline_selection(pane)
            # Appends the duration to update_selection_count's own
            # breakdown (see _append_operation_duration) rather than
            # replacing it outright with _show_result_count's generic
            # "N items (duration)" -- when count is 0 nothing about the
            # pane's actual selection changed, so whatever the label
            # already showed from the last real update is still
            # accurate, and is exactly what should get the timing
            # appended to it.
            self._append_operation_duration()
            if count:
                # The per-item setSelected() calls above each fired
                # itemSelectionChanged, but _sync_selection_from bails
                # out while _bulk_select_guard is held -- so nothing has
                # reached the other pane yet. Mirror once, now that this
                # pane's selection is final and the guards are restored
                # (same explicit, after-the-fact call _select_paths_in_
                # tree's own tail makes on the recursive path). A no-op
                # with Synchronize Views off, or under a caller's
                # _sync_guard.
                self._sync_selection_from(tree)
            return count

        root_dir = pane.entry.text()
        if not root_dir or not self._isdir(root_dir):
            return 0
        other_pane = self.right if pane is self.left else self.left
        other_root = other_pane.entry.text()
        if not other_root or not self._isdir(other_root):
            if require_other_root:
                return 0
            other_root = None

        owns_progress = progress is None
        if owns_progress:
            progress = self._start_select_progress(pane, initiator_pane=initiator_pane)
        matches = []
        toggled = 0
        try:
            matches = collector(root_dir, other_root, progress)
            if not progress.cancelled:
                # Applying the matches (_select_paths_in_tree) shares this
                # same progress/Cancel token -- see its own docstring --
                # so it has to happen *before* this finishes/resets the
                # bar below, not after. This used to be split into two
                # separate steps with the finish sitting in between (dead
                # code in practice, since every caller until Select All
                # always pre-armed and owned its own progress via
                # _select_by_type -- see owns_progress) -- Select All was
                # the first caller to actually own its progress here, and
                # promptly exposed it: the bar got reset to idle (and
                # Cancel disabled) right after the filesystem search, then
                # kept getting ticked against that already-"finished" bar
                # while the matches were applied, leaving it looking stuck
                # at 100% with nothing left to stop it.
                toggled = self._select_paths_in_tree(
                    tree, matches, additive=True,
                    auto_expand=self._expand_folders_matching_selection_enabled(),
                    select_subtree=select_subtree,
                    select=select,
                    progress=progress,
                )
        finally:
            if owns_progress:
                # toggled, not len(matches): a folder match with
                # select_subtree=True can turn a handful of top-level
                # matches into thousands of actually-selected items (see
                # _select_paths_in_tree/_set_whole_subtree_selected), and
                # the count shown should reflect what actually ended up
                # selected -- the same number the Select group's own
                # title (update_selection_count) would report -- not the
                # raw number of matches the search itself found. Reported
                # either way (not just when select=True) so un-toggling a
                # button shows what it just deselected too.
                self._finish_select_progress(pane, result_count=toggled)
        return toggled

    def _pop_toggle_buttons(self, pane: DirectoryPane):
        """Pop every checkable Select-group button on `pane` back up
        without firing its toggled handler (which would re-run that
        type's deselect search) -- for when whatever's selected no
        longer describes any of the Missing/Matching/etc. sets those
        buttons track."""
        for btn in pane.select_toggle_buttons.values():
            if btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)

    def _reset_mirrored_pane_toggle_buttons(self, pane: DirectoryPane):
        """After a Select-group operation on `pane` has been mirrored onto
        the other pane (Synchronize Views on -- see _sync_selection_from),
        the other pane's selection is now "the counterparts of `pane`'s",
        not any of its own tracked sets -- so pop its toggle buttons up.
        _on_manual_tree_selection can't do this itself: the mirror runs
        under _sync_guard, which it deliberately stands down for."""
        if self._synchronize_views_enabled():
            self._pop_toggle_buttons(self.right if pane is self.left else self.left)

    def _select_type_plan(self, type_key: str, pane: DirectoryPane):
        """(collector, predicate, require_other_root, select_subtree) for
        `type_key` -- the per-type half of _select_by_type.

        `pane` -- the pane this plan's collector will search -- decides
        date_field (mtime/ctime/atime, see _pane_diff_date_field), bound
        into the collector lambda so "Newer"/"Older" (and any recursive
        folder-rank comparison along the way) follows that pane's own
        Statistics-view Metric."""
        date_field = self._pane_diff_date_field(pane)
        if type_key == "matching":
            collector = lambda root, other, progress, df=date_field: (
                self._collect_common_paths(root, other, progress, df)
            )
            predicate = lambda item: self._tag_select_type(item.data(0, Qt.UserRole) or "") == "matching"
            return collector, predicate, True, True
        if type_key == "diff_folders":
            collector = lambda root, other, progress, df=date_field: (
                self._collect_diff_folders(root, other, progress, df)
            )
            predicate = lambda item: (
                (item.data(0, IS_DIR_ROLE) and item.data(0, Qt.UserRole) in self._DIR_DIFF_TAGS)
                or item.data(0, Qt.UserRole) == "diff_content_file"
            )
            return collector, predicate, True, False
        want_rank, want_direction = self._RANK_SELECT_TYPES[type_key]
        collector = lambda root, other, progress, wr=want_rank, wd=want_direction, df=date_field: (
            self._collect_matching_paths(root, other, wr, wd, progress, df)
        )
        if type_key == "missing":
            predicate = lambda item: self._tag_select_type(item.data(0, Qt.UserRole) or "") == "missing"
        else:
            # Every difference, not just the one the row's tag kept --
            # see _RANK_SELECT_TYPES.
            predicate = lambda item, df=date_field: type_key in self._item_select_attrs(pane, item, df)
        return collector, predicate, False, True

    def _select_by_type(self, pane: DirectoryPane, type_key: str, select: bool = True,
                         initiator_pane: DirectoryPane = None):
        """Generic entry point behind every Select-group search button.
        Looks up `type_key`'s collector/predicate/root-requirement (see
        _select_type_plan) and runs it through _run_select.

        Diff Folders differs from the other six only in the two ways
        _run_select/_select_paths_in_tree already have parameters for:
        its collector/predicate work off a directory's own aggregate
        rank (_dir_content_rank) rather than a single item's diff tag
        (_tag_select_type), and select_subtree=False since a differing
        folder can easily contain files, or whole subfolders, that don't
        themselves differ -- only the folder itself should end up
        selected, unlike Matching/Missing/etc. where the whole subtree
        really is a match once you already know the folder is.

        With Synchronize Views on, the search only ever runs against
        *this* pane; the other pane then gets the counterparts of
        whatever ended up selected here, via the same generic mirror a
        manual click or a Statistics-view bin selection goes through
        (SyncMixin._sync_selection_from -- see _run_select). So the
        other side selects exactly the same relative paths, whatever
        the type: pressing Larger on the left selects those same files
        on the right (which are, from the right's own point of view, its
        Smaller ones), and Missing selects nothing there at all, since a
        missing item by definition has no counterpart. This replaces an
        earlier scheme that ran a separate "complementary" search
        (Larger <-> Smaller, Newer <-> Older, the rest mapped to
        themselves) independently against the other pane -- which, for
        Missing/Matching/Diff Folders, selected that pane's own,
        genuinely different result set rather than mirroring this one."""
        collector, predicate, require_other_root, select_subtree = self._select_type_plan(type_key, pane)
        count = self._run_select(
            pane, collector, predicate,
            select_subtree=select_subtree,
            require_other_root=require_other_root,
            select=select,
            initiator_pane=initiator_pane,
        )

        # A press that turned up nothing has nothing to actually show as
        # selected, so the button popping back up on its own reads as
        # more honest than leaving it checked next to an otherwise-empty
        # result -- checked is supposed to mean "this pane's Missing/
        # Matching/etc. set is what's currently selected", which isn't
        # true of an empty one. Only for select=True (pressing in) --
        # release (select=False, deselecting) already always ends with
        # the button unchecked regardless, that's just Qt's own toggle
        # state doing its job.
        if select and count == 0:
            btn = pane.select_toggle_buttons.get(type_key)
            if btn is not None and btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)

        # See _reset_mirrored_pane_toggle_buttons.
        if count:
            self._reset_mirrored_pane_toggle_buttons(pane)

        # Last-used diff coloring -- see _apply_attr_color_override. With
        # Synchronize Views on, the other pane's mirrored rows take the
        # opposite attribute's color: its counterparts of this side's
        # Larger rows are, from its own side, Smaller (sizes are
        # symmetric; dates too, given the same Metric on both panes).
        if count and type_key in self._ATTR_OPPOSITE:
            other_pane = self.right if pane is self.left else self.left
            sync = self._synchronize_views_enabled()
            if select:
                self._apply_attr_color_override(pane, type_key)
                if sync:
                    self._apply_attr_color_override(other_pane, self._ATTR_OPPOSITE[type_key])
            else:
                self._reset_diff_color_overrides(pane, only_unselected=True)
                if sync:
                    self._reset_diff_color_overrides(other_pane, only_unselected=True)

        # Bring the first match into view, same "go look at this"
        # finishing touch a timeline selection already gets (see
        # _scroll_tree_to_first_selected) -- only for a press that
        # actually found something, and only in the pane whose own
        # button was clicked.
        if select and count:
            self._scroll_tree_to_first_selected(pane.tree)

    def select_matching(self, pane: DirectoryPane, select: bool = True):
        self._select_by_type(pane, "matching", select=select)

    def select_missing(self, pane: DirectoryPane, select: bool = True):
        self._select_by_type(pane, "missing", select=select)

    def select_newer(self, pane: DirectoryPane, select: bool = True):
        self._select_by_type(pane, "newer", select=select)

    def select_older(self, pane: DirectoryPane, select: bool = True):
        self._select_by_type(pane, "older", select=select)

    def select_smaller(self, pane: DirectoryPane, select: bool = True):
        self._select_by_type(pane, "smaller", select=select)

    def select_larger(self, pane: DirectoryPane, select: bool = True):
        self._select_by_type(pane, "larger", select=select)

    def select_diff_folders(self, pane: DirectoryPane, select: bool = True):
        self._select_by_type(pane, "diff_folders", select=select)

    def _collect_all_paths(self, directory, other_dir=None, progress=None):
        """Collector for Select All -- every immediate entry under
        `directory`, files and folders alike; `other_dir` is unused
        (Select All doesn't care about a counterpart, unlike every other
        Select-group search) but kept in the signature so this fits
        _run_select's generic collector(root, other_root, progress)
        shape. A matched folder is selected as a whole -- select_subtree
        stays True in select_all below -- so there's no need to descend
        into it here.

        `progress` is forwarded into _build_entries, which already ticks
        once per entry itself -- this just takes its output, no need to
        tick a second time for the same entries here."""

        entries = self._build_entries(directory, progress=progress)
        if entries is None:
            return []
        return [e["full_path"] for e in entries]

    def select_all(self, pane: DirectoryPane):
        """Select All: selects everything in `pane`, respecting Recursive
        Folder Selections the same way the type-driven buttons do -- on,
        a real recursive filesystem search of the whole tree under this
        pane's root (auto-expanding to reveal it, same as the others, if
        Expand To Selection is on); off, just whatever's already
        visible/expanded on screen right now. Goes through the same
        _run_select engine as every other Select-group button, so it's
        additive, cancellable, progress-shown, and reported the same
        way.

        With Synchronize Views on, only `pane` itself is searched; the
        other pane then gets the counterparts of the result through the
        same generic mirror as everything else (_sync_selection_from --
        see _run_select). Items the other pane holds with no counterpart
        here are therefore left unselected, the same as if everything
        here had been selected by hand."""
        count = self._run_select(
            pane, self._collect_all_paths, lambda item: True,
            select_subtree=True, require_other_root=False, select=True,
        )
        if count:
            self._reset_mirrored_pane_toggle_buttons(pane)
            # Same "go look at what just got selected" touch as the
            # type-driven buttons -- see _select_by_type.
            self._scroll_tree_to_first_selected(pane.tree)

    def invert_selection(self, tree, progress=None):
        """Flip every already-materialized item's selection state in
        `tree`. Runs with the tree's own signals blocked and repainting
        suspended for the duration -- QTreeWidget.setSelected() fires
        itemSelectionChanged (and, through it, Synchronize Views' own
        per-item mirror, SyncMixin._sync_selection_from) once per call
        otherwise, which is what made inverting a large tree visibly
        stall the UI for a moment: one repaint/signal cycle per item
        instead of one for the whole operation. A single
        itemSelectionChanged fired once at the end (see
        _invert_selection_for_pane, which is what actually needs to
        hear about it) does the same job once instead of once per item.

        progress, if given, is ticked once per item -- purely for
        cancelability and to keep the event loop pumped on a very large
        tree, since the total here (already-materialized tree items)
        isn't the same "how much filesystem work is left" progress
        elsewhere in this app reports against."""
        tree.setUpdatesEnabled(False)
        tree.blockSignals(True)
        try:
            for item in self._iter_items(tree):
                if progress is not None and not progress.tick():
                    break
                item.setSelected(not item.isSelected())
        finally:
            tree.blockSignals(False)
            tree.setUpdatesEnabled(True)
        # Suppress the manual-selection-reset handler (see
        # _on_manual_tree_selection) for this one programmatic emit --
        # Invert already pops the toggle buttons back up itself, right
        # below in _invert_selection_for_pane, with its own reasoning
        # for why; the generic handler doing the same thing a second
        # time is harmless but redundant.
        guard_was_set = getattr(self, "_select_group_guard", False)
        self._select_group_guard = True
        try:
            tree.itemSelectionChanged.emit()
        finally:
            self._select_group_guard = guard_was_set

    def _walk_all_paths(self, directory, progress=None):
        """Recursively yield every real file/folder path under
        `directory`, at every depth. Unlike _collect_all_paths (Select
        All's collector, which can stop at the top level and let
        select_subtree cascade since it's selecting everything
        uniformly), a recursive Invert needs each individual item's
        *current* selection state to know which way it flips, so
        there's no whole-subtree shortcut available here -- every real
        path has to actually be visited."""
        entries = self._build_entries(directory, progress=progress)
        if entries is None:
            return
        for e in entries:
            if progress is not None and progress.cancelled:
                return
            yield e["full_path"]
            if e["is_dir"]:
                yield from self._walk_all_paths(e["full_path"], progress=progress)

    def _invert_selection_recursive(self, pane: DirectoryPane, progress):
        """The Recursive-Folder-Selections-on version of Invert: a real
        walk of the whole filesystem tree under `pane`'s root, rather
        than only flipping whatever's already materialized/expanded on
        screen (see invert_selection) -- the same "respect the setting"
        contract every other Select-group button follows (see
        _run_select's two branches); otherwise, with a big collapsed
        subtree, it could look done while having silently left most of
        the tree's actual contents untouched.

        Three phases (Scan, Deselect, Select), each its own determinate
        total via progress.start_phase.

        Splits into an explicit deselect pass (everything currently
        selected) and select pass (every real path not currently
        selected, auto-expanding to reveal it -- same as Select All)
        rather than trying to flip in place, since a folder can easily
        have some of its contents selected and others not; there's no
        single "select_subtree" shortcut that's correct for an inverted
        selection the way there is for a uniform one."""
        root_dir = pane.entry.text()
        if not root_dir or not self._isdir(root_dir):
            return

        self._set_transfer_label("Scanning...")
        progress.start_phase(pane.item_count or None)
        currently_selected = {item.data(0, PATH_ROLE) for item in pane.tree.selectedItems()}
        all_paths = []
        for path in self._walk_all_paths(root_dir, progress):
            all_paths.append(path)
            if progress.cancelled:
                return

        self._set_transfer_label("Deselecting...")
        progress.start_phase(len(currently_selected) or None)
        self._select_paths_in_tree(
            pane.tree, list(currently_selected), additive=True,
            select_subtree=False, select=False, progress=progress,
        )
        if progress.cancelled:
            return

        to_select = [p for p in all_paths if p not in currently_selected]
        self._set_transfer_label("Selecting...")
        progress.start_phase(len(to_select) or None)
        self._select_paths_in_tree(
            pane.tree, to_select, additive=True, select_subtree=False, select=True,
            auto_expand=self._expand_folders_matching_selection_enabled(), progress=progress,
        )

    def _invert_selection_for_pane(self, pane: DirectoryPane):
        """Invert button. With Recursive Folder Selections off, flips
        every already-materialized item's selection state in `pane`'s
        tree (see invert_selection); with it on, does a real recursive
        filesystem-wide invert instead (see _invert_selection_recursive)
        -- respecting the setting the same way every other Select-group
        button does.

        Only ever inverts `pane` itself. With Synchronize Views on, the
        other pane then gets the counterparts of the inverted result --
        selected or deselected to match -- through the same generic
        mirror as a manual selection (_sync_selection_from), run exactly
        once, as its own "Synchronizing..." phase of the same progress
        cycle, after the invert is final. Invert's own work is held
        under _sync_guard until then: the recursive path makes two
        _select_paths_in_tree calls (deselect, then select), each of
        whose tails would otherwise mirror an intermediate state onto
        the other pane first; the visible-only path's single
        itemSelectionChanged emit would mirror too, just without its own
        progress phase. Skipped on Cancel, which leaves the other pane
        untouched rather than half-mirrored.

        Progress-shown and cancellable: a large tree's worth of
        individual setSelected() calls (or, recursively, a real
        filesystem walk) used to visibly stall the UI with no indication
        why. Whatever's already been flipped when Cancel is hit stays
        flipped, same as every other cancellable operation here.

        Afterward, any Select-group toggle button left checked (on
        either pane, if mirrored) no longer describes what's actually
        selected, so they're popped back up -- the same way Clear does
        -- without touching the selection itself."""
        recursive = self._recursive_folder_contents_enabled()
        if recursive:
            # Busy only for the instant before _invert_selection_recursive's
            # first start_phase gives it a real total.
            progress = self._start_transfer_progress(total=None, label="Inverting...", pane=pane)
        else:
            total = sum(1 for _ in self._iter_items(pane.tree))
            progress = self._start_transfer_progress(total=total or None, label="Inverting...", pane=pane)
        guard_was_set = self._sync_guard
        mirrored = False
        try:
            self._sync_guard = True
            try:
                if recursive:
                    self._invert_selection_recursive(pane, progress)
                else:
                    self.invert_selection(pane.tree, progress=progress)
            finally:
                self._sync_guard = guard_was_set
            # guard_was_set: an outer caller already mirroring (or
            # otherwise holding the guard) owns synchronization -- and
            # _sync_selection_from would bail on it anyway.
            if self._synchronize_views_enabled() and not guard_was_set and not progress.cancelled:
                self._set_transfer_label("Synchronizing...")
                # The mirror ticks once per counterpart it finds, which
                # is at most this many.
                progress.start_phase(len(pane.tree.selectedItems()) or None)
                self._sync_selection_from(pane.tree)
                mirrored = True
        finally:
            self._finish_transfer_progress()
            # Keep update_selection_count's "N selected, X files, Y
            # folders, <size>" breakdown and append the timing behind it
            # (see _finish_select_progress/_append_operation_duration).
            # self._active_status_label still points at pane's own label
            # -- set by _start_transfer_progress(pane=pane) above.
            # Forced rather than relied on: _select_paths_in_tree's tail
            # skips update_selection_count when nothing was toggled.
            self.update_selection_count(pane.tree, pane)
            self._append_operation_duration()

        self._pop_toggle_buttons(pane)
        if mirrored:
            self._reset_mirrored_pane_toggle_buttons(pane)

    def clear_selection(self, pane: DirectoryPane):
        """Clear button: deselects everything in `pane`'s tree, pops
        every checkable Select-group button (Missing/Matching/Smaller/
        Larger/Newer/Older/Diff Folders) back up so their visual state
        can't drift from the (now-empty) actual selection, and also
        resets any pending cut/copy state -- _clear_cut_marks undoes the
        italic "cut" styling (see TransferMixin), and the system
        clipboard itself is emptied too, but only if it's actually
        holding DirComp's own cut/copy data (GNOME_COPIED_FILES_MIME) --
        so Clear can't stomp on something unrelated the user copied from
        another application. blockSignals around the toggle-button reset
        -- otherwise each setChecked(False) would fire toggled(False)
        and re-run that type's deselect search, which is both wasted
        work and redundant with the clearSelection() above. The
        clearSelection() call itself is wrapped in _select_group_guard
        (see _on_manual_tree_selection) purely for consistency with
        every other Select-group-driven selection change -- redundant
        here since the toggle buttons are popped up explicitly right
        below either way, but keeps this method following the same
        pattern as the rest.

        Also explicitly wipes the Statistics view for this pane -- both
        the *currently shown* mode's drag-range overlay (which
        tree.clearSelection()'s itemSelectionChanged -> _sync_timeline_
        selection chain reaches for the discrete bin highlighting, but
        never for selection_range -- set_external_selection doesn't
        touch that, only set_external_selection_range does) and the
        *other, currently-hidden* mode's own stashed selection (see
        pane.tl_mode_selections/_on_timeline_mode_changed). Without
        this, Clear only ever reset whichever mode's chart happened to
        be on screen at the time: switching Files/Folders afterward
        would restore the other mode's untouched stash exactly as it
        was before Clear was clicked, looking like the selection had
        "lingered" through a Clear that was supposed to wipe it.

        With Synchronize Views on, only the other pane's counterparts of
        what was selected here get deselected; anything else selected
        over there stays (see SyncMixin._sync_deselection_from). The
        clearSelection() below runs under _sync_guard, since its
        itemSelectionChanged would otherwise reach the ordinary
        replace-style mirror and clear the other pane outright."""
        self._begin_timed_operation(pane)
        previously_selected = [
            p for p in (item.data(0, PATH_ROLE) for item in pane.tree.selectedItems()) if p
        ]
        guard_was_set = getattr(self, "_select_group_guard", False)
        sync_guard_was_set = self._sync_guard
        self._select_group_guard = True
        self._sync_guard = True
        try:
            pane.tree.clearSelection()
        finally:
            self._select_group_guard = guard_was_set
            self._sync_guard = sync_guard_was_set
        pane.tl_mode_selections = {"file": None, "folder": None}
        timeline = getattr(pane, "timeline", None)
        if timeline is not None:
            timeline.set_external_selection(set())
            timeline.set_external_selection_range(None)
        for btn in pane.select_toggle_buttons.values():
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)

        if self._sync_deselection_from(pane.tree, previously_selected):
            self._reset_mirrored_pane_toggle_buttons(pane)

        # Back to the default diff ranking -- see _reset_diff_color_
        # overrides. Everything on this side; on the other side (with
        # Synchronize Views on) only the rows Clear just deselected there,
        # matching how Clear leaves that side's other selections alone.
        self._reset_diff_color_overrides(pane)
        if self._synchronize_views_enabled():
            self._reset_diff_color_overrides(self.right if pane is self.left else self.left, only_unselected=True)

        self._clear_cut_marks()
        clipboard = QApplication.clipboard()
        if clipboard.mimeData().hasFormat(GNOME_COPIED_FILES_MIME):
            clipboard.clear()
        # clearSelection() above already ran update_selection_count via
        # itemSelectionChanged (see _on_manual_tree_selection's
        # connected handler, unsuppressed here since only
        # _select_group_guard -- not _bulk_select_guard -- is held),
        # leaving an accurate "0 selected, 0 files, 0 folders, 0 B" in
        # this pane's own status label -- append the duration to that
        # rather than replacing it with _show_result_count's generic
        # "0 items (duration)" (see _append_operation_duration).
        self._append_operation_duration()

    def _select_ancestor_folders(self, pane: DirectoryPane, full_walk: bool, include_dirs: bool = False):
        """Shared engine behind File Parents and Folder Parents.

        With full_walk off, only selected FILES contribute, and only
        their immediate parent gets added -- a selected folder's own
        parent is deliberately not included, since "parent of a folder"
        isn't what File Parents asks for in that mode.

        With full_walk on, every selected item -- files AND folders
        alike -- has its entire ancestor chain added instead of just one
        level, walking up via item.parent() until reaching a top-level
        item (parent() is None there -- that's as far up as the tree
        goes; the pane's own root isn't itself a tree item, so the walk
        naturally stops right at it without needing a special case).

        Either way this only ever touches what's already materialized in
        the tree -- an item's ancestors necessarily already exist as
        tree items, since the item itself is visible/selected right
        now -- so unlike the tag-based Select buttons this never needs a
        filesystem search or progress bar.

        With Synchronize Views on, the resulting selection change is
        mirrored onto the other pane the same as any other selection
        change -- see SyncMixin._sync_selection_from -- so an ancestor
        folder ends up selected there too whenever it genuinely has a
        counterpart, and simply doesn't when it doesn't (per-item, same
        as everything else that handler mirrors). No special-casing
        needed here: unlike the type-driven Select buttons, this isn't
        classifying anything that needs a "complementary" reinterpretation
        on the other side -- it's just "select this folder", which is
        exactly what that handler already does correctly."""
        self._begin_timed_operation(pane)
        parent_paths = set()
        for item in pane.tree.selectedItems():
            if not full_walk and not include_dirs and item.data(0, IS_DIR_ROLE):
                continue
            parent = item.parent()
            while parent is not None:
                path = parent.data(0, PATH_ROLE)
                if path:
                    parent_paths.add(path)
                if not full_walk:
                    break
                parent = parent.parent()
        if parent_paths:
            # Its tail mirrors onto the other pane (Synchronize Views) --
            # see _select_paths_in_tree/_sync_selection_from.
            if self._select_paths_in_tree(pane.tree, parent_paths, additive=True, select_subtree=False):
                self._reset_mirrored_pane_toggle_buttons(pane)
            # Same "go look at what just got selected" touch as the
            # type-driven buttons -- see _select_by_type.
            self._scroll_tree_to_first_selected(pane.tree)
            # _select_paths_in_tree's own tail already ran
            # update_selection_count, leaving this pane's status label
            # with an accurate breakdown -- append the duration to that
            # (see _append_operation_duration) instead of replacing it.
            self._append_operation_duration()
        else:
            # Nothing actually got selected, so there's no fresh
            # update_selection_count breakdown to append to -- same
            # "did anything happen" generic message every other empty
            # result gets.
            self._show_result_count(0)

    def select_file_parents(self, pane: DirectoryPane):
        """Item Parent (formerly File Parent): adds the parent folder of
        every selected item -- files and folders alike -- one level up,
        additively (the selection stays), regardless of Recursive Folder
        Selections. Each press climbs one more level, since the parents
        just added are selected items too."""
        self._select_ancestor_folders(pane, full_walk=False, include_dirs=True)

    def select_folder_parents(self, pane: DirectoryPane):
        """Folder Parents: one-click version of what File Parents does
        when Recursive Folder Selections is on -- always walks every
        selected item's entire ancestor chain (files and folders alike)
        up to the pane's root, regardless of that checkbox's state."""
        self._select_ancestor_folders(pane, full_walk=True)

    # -------------------------------------------------- Filesystem search

    def _collect_common_paths(self, directory, other_dir, progress=None, date_field="mtime"):
        """Recursively find every item under `directory` that matches its
        counterpart in `other_dir` exactly (the same "common_file"/
        "common_dir" tag "Matching" uses when just looking at what's
        already visible) -- files by their own status, directories by
        their aggregate tag. A directory whose own aggregate already
        matches is selected as a whole rather than also descending into
        it, same as _collect_matching_paths.

        `date_field` (mtime/ctime/atime) -- see _diff_tag -- is resolved
        once by the top-level caller (_select_by_type, from the
        searching pane's own Statistics-view Metric) and threaded
        through every recursive call unchanged, rather than re-derived
        at each depth."""
        entries = self._build_entries(directory)
        if entries is None:
            return []
        date_entry_key = _DATE_FIELD_TO_ENTRY_KEY[date_field]
        matches = []
        for e in entries:
            if progress is not None and not progress.tick():
                return matches
            tag = self._diff_tag(e["name"], e["is_dir"], e["size_str"], e[date_entry_key], other_dir,
                                  e["full_path"], date_field=date_field)
            if tag in ("common_file", "common_dir"):
                matches.append(e["full_path"])
                continue
            if e["is_dir"] and tag != "missing_dir":
                child_other = os.path.join(other_dir, e["name"])
                matches.extend(self._collect_common_paths(e["full_path"], child_other, progress, date_field))
        return matches

    # Missing/Smaller/Larger/Newer/Older search the real filesystem
    # recursively rather than just the currently-materialized tree items
    # -- so they find (and, with Expand To Selection on, auto-expand to
    # reveal) matches inside folders you've never opened. Each item's
    # status is always computed against its real counterpart by relative
    # path, so this is unambiguous at any depth. Driven by
    # _select_by_type/_RANK_SELECT_TYPES -- see the class docstring.
    def _collect_matching_paths(self, directory, other_dir, want_rank, want_direction, progress=None,
                                 date_field="mtime"):
        """Recursively find every FILE under `directory` whose own diff
        status is (want_rank, want_direction) -- by tag for Missing, by
        _diff_axes for the other four, so a file differing in both size
        and date is found by either of its buttons -- Missing/Smaller/Larger/
        Newer/Older are all about individual file-level differences, so
        this always descends into folders that exist on both sides to find
        (and mark) the actual file causing the difference, however deep it
        is, rather than stopping early and selecting a containing folder
        as a stand-in for it. The one exception is a folder that's missing
        entirely on the other side -- there's nothing to compare its
        contents against, so for the Missing search (want_rank == 3) the
        whole folder is selected as-is, same as a missing file would be.

        `date_field` -- see _collect_common_paths -- ties "Newer"/
        "Older" (want_rank == 2) to whichever date type the searching
        pane's Metric combo currently has selected."""
        entries = self._build_entries(directory)
        if entries is None:
            return []
        date_entry_key = _DATE_FIELD_TO_ENTRY_KEY[date_field]
        matches = []
        for e in entries:
            if progress is not None and not progress.tick():
                return matches
            if e["is_dir"]:
                other_full = os.path.join(other_dir, e["name"]) if other_dir else None
                if not other_full or not self._isdir(other_full):
                    if (want_rank, want_direction) == (3, None):
                        matches.append(e["full_path"])
                    continue  # nothing on the other side to recurse into
                matches.extend(self._collect_matching_paths(
                    e["full_path"], other_full, want_rank, want_direction, progress, date_field))
            elif want_rank == 3:
                tag = self._diff_tag(e["name"], e["is_dir"], e["size_str"], e[date_entry_key], other_dir,
                                      e["full_path"], date_field=date_field)
                if _DIFF_RANK.get(tag, (0, None)) == (want_rank, want_direction):
                    matches.append(e["full_path"])
            elif other_dir:
                # Smaller/Larger/Newer/Older: test the one attribute this
                # search is about via _diff_axes, not the single tag --
                # which lets a date difference hide a size one (see
                # _RANK_SELECT_TYPES), so e.g. a file both newer and
                # larger used to be invisible to Larger.
                axes = self._diff_axes(False, e["size_str"], e[date_entry_key],
                                       os.path.join(other_dir, e["name"]), date_field,
                                       own_full=e["full_path"])
                if want_direction in self._attrs_from_axes(axes):
                    matches.append(e["full_path"])
        return matches

    def _collect_diff_folders(self, directory, other_dir, progress=None, date_field="mtime"):
        """Recursively find every folder under `directory` that exists in
        `other_dir` too but differs somewhere inside (see
        _dir_content_rank), at any depth -- including a differing folder
        nested inside another differing folder: an outer folder's
        aggregate rank goes non-zero precisely because something inside
        it (possibly several levels down) differs, so stopping at the
        first differing folder found would hide exactly which nested
        folder is the actual source of the difference. Always keeps
        descending regardless of whether the current folder itself was
        just added, to find all of them.

        `date_field` -- see _collect_common_paths -- same threading."""
        entries = self._build_entries(directory)
        if entries is None:
            return []
        date_entry_key = _DATE_FIELD_TO_ENTRY_KEY[date_field]
        found = []
        for e in entries:
            if progress is not None and not progress.tick():
                return found
            if not e["is_dir"]:
                # Hash comparison: same-name files whose content differs
                # while size and date agree ("Content differs") count too.
                if other_dir and self._hash_mode_active():
                    tag = self._diff_tag(e["name"], False, e["size_str"], e[date_entry_key], other_dir,
                                          e["full_path"], date_field=date_field)
                    if tag == "diff_content_file":
                        found.append(e["full_path"])
                continue
            other_full = os.path.join(other_dir, e["name"]) if other_dir else None
            if not other_full or not self._isdir(other_full):
                continue  # doesn't exist on the other side at all -- that's Missing, not a difference
            rank, _direction = self._dir_content_rank(e["full_path"], other_full, date_field)
            if rank > 0:
                found.append(e["full_path"])
            found.extend(self._collect_diff_folders(e["full_path"], other_full, progress, date_field))
        return found

    # --------------------------------------------------------- Clipboard

    def _select_paths_in_tree(self, tree, paths, additive=False, auto_expand=True, select_subtree=True,
                               select=True, progress=None, expand_matched_dirs=True):
        """Select (or, with select=False, deselect) the items at `paths`
        (absolute paths) in `tree`, populating whatever ancestor folders
        are needed to reach them. Used both to highlight items just
        copied/moved/pasted into a pane (additive=False, the default --
        that selection should replace whatever was selected before) and
        by the Select-group buttons (additive=True -- those are meant to
        add to the current selection, like Missing/Matching).

        select_subtree controls what happens when a directory is itself
        one of `paths`. With it on (the default), the whole subtree gets
        selected too, not just the folder -- see _set_whole_subtree_selected.
        That's right for a folder that's wholly Matching, or entirely
        Missing on the other side: the search never bothered classifying
        its contents individually (see _collect_matching_paths/
        _collect_common_paths), but every file inside genuinely is a
        Matching/Missing match too, so the Select group's own "N
        selected, X files, Y folders" total (and what an actual
        copy/move sees) should reflect that. Diff Folders passes
        select_subtree=False instead: a *differing* folder can easily
        contain files that don't themselves differ (or aren't even
        files, un-differing subfolders), so only the folder itself
        should end up selected -- it's still expanded/populated the same
        as any other match, just not recursed into for selection. Either
        way, a matched directory's own children still get walked
        afterwards (rather than stopping there) -- with select_subtree
        off, `paths` can itself contain a nested match inside another
        match (a differing folder nested inside another differing
        folder, from Diff Folders), so a match isn't necessarily a dead
        end.

        auto_expand controls whether reaching a target -- whether a
        nested one, or a folder-level match itself -- also visually
        expands its ancestor folders open (the default) or just
        populates them enough to select while leaving them collapsed --
        Qt tracks selection independently of expand state, so a
        collapsed folder can still hold a selected descendant. The
        Select-group buttons pass auto_expand=False when Expand To
        Selection is off, so a search still finds and selects everything,
        it just doesn't reshape the tree to show it. Ignored when
        select=False -- deselecting never needs to reveal or populate
        anything: an item that was never populated can't be selected in
        the first place, so there's nothing underneath it to deselect.

        expand_matched_dirs controls a different thing: whether a
        directory that's itself one of `paths` (with select_subtree=
        False -- see above) gets expanded/populated *itself*, revealing
        its own contents, once auto_expand has found and selected it.
        With it on (the default -- Diff Folders' own behavior, unchanged),
        a matched directory opens up the same as any other match, so its
        contents are right there to look at. With it off, a matched
        directory is still selected and still brought into view (its
        *ancestors* still expand under auto_expand, same as ever, so you
        can see the row itself) -- it just doesn't also open itself up.
        The Statistics view's Folder-mode selection (see
        TimelineOpsMixin._on_timeline_selection) needs exactly this: a
        folder that's itself a plotted point should highlight in the
        tree, not spring open. A directory that's an *ancestor* of some
        other, deeper target is unaffected either way -- it always gets
        populated/expanded (however auto_expand says), since the walk
        has to go through it regardless of whether it's also a target in
        its own right.

        select toggles the whole walk between selecting matches (True,
        the default) and deselecting them again (False) -- used by the
        Select-group's toggle buttons on release. A path that's a target
        for both an on-toggle and an off-toggle button (e.g. a file
        that's both Larger and Older) just ends up wherever the most
        recent call left it -- each call only ever touches the paths it
        was given, never consulting what any other button's state is.

        progress, if given, is ticked once per tree item visited during
        the walk -- shares whatever progress bar/Cancel is already
        active (a Select-group search's own filesystem collector, or a
        transfer's enumerate/copy phases -- see TransferMixin) rather
        than applying the matches in one silent, uncancellable pass
        after those finish. A large match set, or simply a large
        destination tree to walk while looking for just-pasted paths,
        used to mean the bar reset to 0% and went dark right when this
        started, with no way to stop it and no sign anything was still
        happening -- ticking the same token through keeps it moving and
        responsive instead.

        The whole walk runs under _select_group_guard (see
        _on_manual_tree_selection) -- every setSelected() call below is
        a programmatic, Select-group-driven (or post-transfer/paste)
        selection change, never a manual click in the tree, so the
        generic "manual selection resets the toggle buttons" handler
        needs to stay out of its way for the duration.

        Also runs under _bulk_select_guard, a second, narrower flag
        checked only by update_selection_count: setSelected() fires
        itemSelectionChanged on every single call, and that handler
        recomputes the Select group's title from scratch (an O(selected
        items) scan) -- with thousands of items toggled in one search,
        that turned an O(n) selection into an O(n^2) one. It's a
        separate flag from _select_group_guard on purpose: unlike that
        one, it must stay False for the deliberate final emit some
        callers (Invert) make afterward, which *does* need the title
        recomputed -- reusing _select_group_guard for both would
        suppress that too. Signals themselves aren't blocked here (the
        way invert_selection blocks them) because auto_expand's
        setExpanded(True) calls below rely on itemExpanded firing
        synchronously to populate children -- blocking every signal
        would silently break that.

        Also suspends the tree's own repainting (setUpdatesEnabled(False))
        for the duration -- unlike blockSignals, this only stops Qt from
        recalculating layout/repainting after every individual call, it
        doesn't stop itemExpanded from firing, so population still works
        exactly as before; it just doesn't force a redraw after each of
        what can be thousands of calls in a row. This matters most for
        something like Select All, where most/all matches are top-level
        folders and select_subtree cascades into populating and
        expanding the *entire* tree -- as opposed to Matching, where
        most matches tend to be individual scattered files that only
        need their own ancestors populated, not a full recursive
        cascade, which is why the two can otherwise take wildly
        different amounts of real Qt widget-construction work for a
        similar total item count.

        Returns the number of tree items actually toggled -- the
        targets found plus every descendant a whole-folder match
        cascaded into (see _set_whole_subtree_selected) -- which is
        what a caller reporting a result count should show instead of
        len(paths): a folder match can turn a handful of top-level
        matches into thousands of actually-selected items."""
        guard_was_set = getattr(self, "_select_group_guard", False)
        self._select_group_guard = True
        bulk_guard_was_set = getattr(self, "_bulk_select_guard", False)
        self._bulk_select_guard = True

        # Actually hiding the tree here (not just setUpdatesEnabled(False)
        # on it) turns out to matter far more than that alone: profiling a
        # ~5000-item Select All showed QTreeWidgetItem.setData() -- called
        # repeatedly while each new row is built, see _make_child_items --
        # costing over 10x more per call while the tree is visible than
        # while it's hidden, even with repaints already suppressed.
        # QAbstractItemView does real per-row bookkeeping in response to
        # every data change on a visible view; setUpdatesEnabled(False)
        # only ever suppressed the resulting repaint, not that underlying
        # work. Genuinely hiding the tree (isVisible() False) skips it
        # outright.
        #
        # But that's only worth doing for a genuinely large batch --
        # Select All/Matching/Invert, where select_subtree can cascade
        # into populating/toggling thousands of items. Synchronize
        # Views' own per-click mirror (_sync_selection_from) calls this
        # same method for *every* manual selection change, including
        # each intermediate step of a drag-select -- often just a
        # handful of paths, sometimes several times a second. Hiding the
        # tree on every one of those, back to back, is what made
        # Synchronize Views feel broken/stuttery: each rapid-fire mirror
        # paid a real, synchronous hide/show cost, and a fast-moving
        # drag could trigger another one before the previous had
        # visually settled. A plain setUpdatesEnabled(False) (below) is
        # already near-instant for a small target set, so the more
        # invasive hide-the-tree path is now reserved for when there's
        # actually enough work to make it worth the visible toggle.
        #
        # Deliberately not also suspending the whole window's updates
        # here (an earlier version of this did): see update_listing's
        # own version of this comment -- disabling the whole window's
        # repaint/focus handling right after a modal dialog closes
        # elsewhere in the app caused multi-minute apparent freezes.
        # Nothing here currently follows a dialog, but hiding just the
        # tree already delivers the measured win on its own, so there's
        # no reason to keep the riskier, window-wide version around too.
        large_operation = select_subtree or len(paths) > 50
        tree_was_visible = tree.isVisible()
        # Same reasoning as ListingMixin.update_listing: only actually
        # hide/restore if the tree was genuinely visible to begin with,
        # so a tree that's only implicitly hidden (its tab isn't
        # current) never gets marked explicitly hidden -- see that
        # method's comment for the full mechanism.
        do_hide = large_operation and tree_was_visible
        if do_hide:
            tree.setVisible(False)
        tree.setUpdatesEnabled(False)
        toggled_count = 0
        # Items to actually select/deselect are collected here instead of
        # calling item.setSelected() as each one is found -- see
        # _apply_bulk_selection below for why. Every item appended shares
        # this same call's `select` value, so one flag covers the whole
        # batch.
        pending: list = []
        # Set below when clearSelection() actually had something to
        # clear -- tracked separately from toggled_count (which only
        # counts items *matched by paths*) so the empty-targets early
        # return just below still reports a real change happened. Before
        # this, calling this method with an empty paths (a Clear
        # Selection, or a mirrored clear from the other pane -- see
        # SyncMixin._sync_selection_from) cleared the tree's actual
        # selection but returned 0, which the caller reads as "nothing
        # changed": the tail below that refreshes update_selection_count
        # and _sync_timeline_selection never ran, leaving the Select
        # group's count and the timeline's bin highlighting stale even
        # though the tree itself had genuinely just been cleared.
        cleared_existing = False
        try:
            targets = {os.path.normpath(p) for p in paths}
            if select and not additive:
                cleared_existing = bool(tree.selectedItems())
                tree.clearSelection()
            if not targets:
                raise _NoTargets

            # Every directory that has a target somewhere inside it, at
            # any depth -- lets walk() below prune straight past a
            # branch that can't possibly contain a match, instead of
            # visiting every materialized item in the tree on every
            # single search/apply. Applying a search that only found a
            # handful of matches out of a huge tree used to still walk
            # the *entire* tree to find them; this makes the walk's cost
            # proportional to the matches (and their ancestors) instead.
            # A target can itself be a file or a folder -- doesn't
            # matter here, since this only ever walks *up* from a
            # target's own path to build the ancestor set.
            ancestor_dirs = set()
            for t in targets:
                d = os.path.dirname(t)
                while d and d not in ancestor_dirs:
                    ancestor_dirs.add(d)
                    parent = os.path.dirname(d)
                    if parent == d:
                        break
                    d = parent

            def walk(item):
                nonlocal toggled_count
                path = item.data(0, PATH_ROLE)
                norm = os.path.normpath(path) if path else None
                is_target = norm in targets
                is_ancestor = bool(norm) and norm in ancestor_dirs
                if not (is_target or is_ancestor):
                    return  # nothing under this branch is a target -- skip it entirely

                if progress is not None:
                    # Only a genuine target match counts against the
                    # caller's total -- statistics_ops._on_timeline_
                    # selection's own total already assumes this split
                    # (see its docstring), but it was never actually
                    # implemented here before: this used to tick() on
                    # every ancestor visited too, so a target set
                    # scattered across a big tree (lots of distinct
                    # ancestor directories) could tick multiple times
                    # the size of `targets` itself, running the bar to
                    # 100% long before the real matches were even half
                    # done. An ancestor visited purely on the way to a
                    # match now just pumps the event loop instead,
                    # exactly like a plain background traversal would.
                    alive = progress.tick() if is_target else progress.pump()
                    if not alive:
                        return

                if is_target:
                    pending.append(item)
                    toggled_count += 1
                    if item.data(0, IS_DIR_ROLE) and select_subtree:
                        # Fully handled: every descendant is selected (and
                        # expanded/populated) already, so there's nothing
                        # left underneath worth walking into individually.
                        toggled_count += self._set_whole_subtree_selected(
                            item, tree, select, auto_expand, progress, pending)
                        return

                if select and (is_target or is_ancestor):
                    if item.data(0, IS_DIR_ROLE) and (is_ancestor or expand_matched_dirs):
                        # Populate directly, untracked -- this walk's own
                        # recursion below already visits (and ticks)
                        # every child of this node in a moment, so
                        # ticking during population too would double-
                        # count every newly-populated child (same
                        # reasoning as _set_all_folders_state's walk()).
                        # Mattered most for something like Select All,
                        # where matches are top-level folders and
                        # select_subtree cascades into populating the
                        # *entire* tree -- Matching's matches tend to be
                        # individual scattered files, so far fewer nodes
                        # go through this ancestor-populate path and the
                        # doubling was smaller, easier to miss.
                        self._ensure_item_populated(item, tree, progress=None)
                        if auto_expand and not item.isExpanded():
                            item.setExpanded(True)

                for i in range(item.childCount()):
                    if progress is not None and progress.cancelled:
                        return
                    walk(item.child(i))

            for i in range(tree.topLevelItemCount()):
                if progress is not None and progress.cancelled:
                    break
                walk(tree.topLevelItem(i))

            # One QItemSelectionModel.select() call covering every item
            # found, instead of one item.setSelected() call per item --
            # see _apply_bulk_selection. This runs inside the same
            # setUpdatesEnabled(False)/guarded region the per-item calls
            # used to run in, so it's exercising the same
            # not-yet-repainted, signal-suppressed conditions, just with
            # the actual selection-model work batched.
            self._apply_bulk_selection(tree, pending, select)
        except _NoTargets:
            pass
        finally:
            if do_hide:
                tree.setVisible(True)
            self._select_group_guard = guard_was_set
            self._bulk_select_guard = bulk_guard_was_set
            tree.setUpdatesEnabled(True)

        if toggled_count or cleared_existing:
            pane = self._pane_for_tree(tree)
            self.update_selection_count(tree, pane)
            self._sync_timeline_selection(pane)
            # Explicit, single, deferred mirror to the other pane, now
            # that _bulk_select_guard/tree visibility are both back to
            # normal (see the finally block above) -- replaces relying
            # solely on the implicit itemSelectionChanged ->
            # sync_views._sync_selection_from cascade that setSelected()
            # firing during the walk used to trigger. That cascade is
            # now suppressed by _bulk_select_guard for the walk's own
            # duration (see _sync_selection_from's own early-return), so
            # without this explicit call here Synchronize Views would
            # simply never mirror a selection made through this method
            # at all. Being explicit rather than signal-driven also
            # means the mirror runs *after* this walk's own tree is
            # fully back to its normal (visible, guards-restored) state,
            # instead of nested inside it mid-cleanup -- which is what
            # made the other pane's tree only ever look briefly hidden
            # while this one stayed hidden for the whole operation.
            # _sync_selection_from's own _sync_guard check prevents this
            # from calling back into itself when `tree` here *is* the
            # mirror's own destination tree (i.e. this call is itself
            # the reentrant half, already running under _sync_guard).
            self._sync_selection_from(tree)
        return toggled_count

    def _apply_bulk_selection(self, tree, items, select):
        """Select/deselect every item in `items` in one shot, instead of
        calling item.setSelected() once per item.

        item.setSelected() looked cheap but wasn't: each call round-trips
        through QItemSelectionModel -- updating its internal selection
        ranges and firing selectionChanged -- on its own. That's fine for
        a handful of clicks, but for the thousands of items a Select
        All/Matching can touch it dominates the whole operation (measured
        at roughly 60% of Select All's total time on a ~5000-item tree,
        dwarfing the actual tree-building work). Building one QItemSelection
        covering every item and handing it to the selection model once
        does the equivalent update in a single pass instead of thousands
        of separate ones.

        `items` all share the same `select` value (True to select, False
        to deselect) -- every caller here only ever collects one or the
        other per call, never a mix, so a single flag for the whole batch
        is enough."""
        if not items:
            return
        model = tree.selectionModel()
        if model is None:
            # Fall back to the per-item path if there's somehow no
            # selection model yet (e.g. a tree not fully constructed) --
            # better than silently selecting nothing.
            for item in items:
                item.setSelected(select)
            return
        selection = QItemSelection()
        for item in items:
            index = tree.indexFromItem(item, 0)
            if index.isValid():
                selection.select(index, index)
        command = QItemSelectionModel.Select if select else QItemSelectionModel.Deselect
        model.select(selection, command | QItemSelectionModel.Rows)

    def _set_whole_subtree_selected(self, item, tree, select, auto_expand, progress=None, pending=None):
        """Recursively select (or deselect) every descendant of `item`.
        Called from _select_paths_in_tree when select_subtree is on and
        a whole folder was itself the match found (fully Matching, or
        entirely Missing on the other side), so the individual files
        inside end up genuinely selected too, not just the containing
        folder. While selecting, populates (and -- when auto_expand is
        on -- expands) as needed to reach every descendant; while
        deselecting, skips that entirely, since an unpopulated
        descendant was never selected in the first place.

        progress -- see _select_paths_in_tree -- is ticked once per
        descendant, but only where that tick wouldn't double-count work
        already ticked earlier in this same call: if `item` needed
        populating just now (see _ensure_item_populated/setExpanded
        below), building its new children already ticked once per child
        (_build_entries/_make_child_items), so the loop below only
        checks for cancellation on those without ticking again; if
        `item` was already populated from before (nothing new just
        happened), nothing else has ticked for its children yet, so the
        loop ticks them here instead. Getting this wrong (ticking both
        times) is what used to make a determinate bar reach 100% at
        roughly the halfway point of the real work still left to do.

        Returns the number of descendants actually toggled -- used by
        _select_paths_in_tree to report how many items a whole-folder
        match really turned into, rather than just counting the match
        itself (see its own docstring).

        `pending`, if given, collects touched items for a single bulk
        selection-model update at the very end of the whole operation
        (see _select_paths_in_tree/_apply_bulk_selection) instead of each
        one calling item.setSelected() here and now -- omitted only by
        the standalone-call fallback below, which still needs to leave
        the item actually (de)selected on its own."""
        freshly_populated = False
        if select:
            was_populated = bool(item.data(0, POPULATED_ROLE))
            if auto_expand:
                if not item.isExpanded():
                    item.setExpanded(True)  # synchronously populates children
            else:
                self._ensure_item_populated(item, tree, progress=progress)
            freshly_populated = not was_populated
        toggled_count = 0
        for i in range(item.childCount()):
            if progress is not None:
                if freshly_populated:
                    if progress.cancelled:
                        return toggled_count
                elif not progress.tick():
                    return toggled_count
            child = item.child(i)
            if pending is not None:
                pending.append(child)
            else:
                child.setSelected(select)
            toggled_count += 1
            if child.data(0, IS_DIR_ROLE):
                toggled_count += self._set_whole_subtree_selected(
                    child, tree, select, auto_expand, progress, pending)
        return toggled_count

    # --------------------------------------------------- Synchronize Views
