"""
TransferMixin: the Clipboard group (Cut/Copy/Paste/Delete), the cross-pane
Move/Copy buttons, "Make New Folder", and the low-level file-transfer
machinery (overwrite confirmation, free-space checks, nested-selection
dedup, and the central progress bar/Cancel/operation-label) they all
share. That central bar is also reused by SelectionMixin for the Select
group's recursive searches -- see _start_transfer_progress -- since only
one such operation can ever be in flight at once.
"""

import atexit
import os
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import Qt, QUrl, QMimeData
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from constants import PATH_ROLE, GNOME_COPIED_FILES_MIME
from listing_ops import COLUMN_COUNT
from panes import DirectoryPane
from progress import _CancellableProgress
from trash_compat import send2trash, SEND2TRASH_AVAILABLE


class TransferMixin:
    def _start_transfer_progress(self, total=None, label="", pane=None, silent=False) -> "_CancellableProgress":
        """Arm the status line's progress bar/Cancel for a move/paste, a
        Recursive Folder Contents item recount, or a Select-group search
        (see SelectionMixin._start_select_progress -- it's the same
        token either way, since only one of these can ever be running
        at once). With total=None it runs as a busy indicator instead,
        for whenever the real amount of work isn't known ahead of time
        -- see _transfer_paths' enumeration phase, which later calls
        progress.start_phase() once it knows the real count. `label` is
        shown in the status line's operation text (e.g. "Copying...",
        "Enumerating...", "Searching...") so it's clear which of these
        is actually running.

        `silent`: this cycle is purely internal bookkeeping (an item
        recount after a folder open/Refresh, a lazy large-folder
        populate) rather than something the person actually asked for --
        see _finish_transfer_progress, which restores the status line to
        whatever it showed right before this cycle started rather than
        leaving it on this cycle's own in-progress text or a live-tick
        reading that never got a real final value. Leave it False (the
        default) for anything the person would recognize as "an
        operation" -- a transfer, a Select-group search, Clear -- even
        one that reports its result purely via the live tick rather than
        an explicit result_count; those should keep whatever that tick
        last showed as their own final answer, not get reverted past it.

        `pane` is the pane whose own button/action kicked this off (a
        transfer's source_pane, a paste's own pane, a search's pane,
        an expand/recount's own pane) -- picks which of the two status-
        line bars actually shows this operation: the right pane's own
        bar when `pane` is the right pane, the left one otherwise
        (including `pane=None`, for operations -- a full Refresh, both-
        panes recount -- that aren't really "started by" one side).
        Each bar's fill direction is fixed at construction (left bar
        left-to-right, right bar right-to-left -- see
        ui_builder._build_status_line's setInvertedAppearance), so
        which one lights up is what shows which side is doing the work,
        regardless of Symmetrical Buttons -- unlike the old single
        shared bar, this no longer depends on that setting.

        The status *text* and Cancel *button* follow the same side --
        self._active_status_label (left_selection_label/
        right_selection_label -- see ui_builder._build_status_line,
        which doubles these up as each side's own operation status now
        that there's no more single shared center label) and self.
        _active_cancel_button (left_cancel_button/right_cancel_button)
        are picked here alongside the bar, and every other method below
        that used to touch a single shared transfer_label/
        transfer_cancel_button now goes through these instead."""
        self._set_operation_controls_enabled(False)
        self._operation_start_time = time.monotonic()
        is_right_initiated = pane is not None and pane is getattr(self, "right", None)
        self._active_progress_bar = self.right_progress_bar if is_right_initiated else self.left_progress_bar
        self._active_status_label = self.right_selection_label if is_right_initiated else self.left_selection_label
        self._active_cancel_button = self.right_cancel_button if is_right_initiated else self.left_cancel_button
        progress = _CancellableProgress(self._active_progress_bar, total=total, on_tick=self._update_live_count_label)
        self.active_transfer_progress = progress
        self._active_cancel_button.setEnabled(True)
        self._silent_operation = silent
        # Snapshot of whatever this side's status label was showing
        # right before this cycle's own label/live-tick counter start
        # overwriting it (see _update_live_count_label) -- only actually
        # restored by _finish_transfer_progress for a `silent` cycle
        # (see above), but cheap enough to always take regardless.
        if self._active_status_label is not None:
            self._pre_operation_label_text = self._active_status_label.text()
            self._pre_operation_label_style = self._active_status_label.styleSheet()
        self._set_transfer_label(label)
        QApplication.processEvents()
        return progress

    def _update_live_count_label(self, count: int):
        """Live "N items (duration)" readout while a long operation is
        still running -- same shape _show_result_count uses once it's
        actually finished, so the label reads as one continuous count
        rather than only appearing at the end. Called by
        _CancellableProgress.tick(), already throttled to roughly once a
        second there -- updating this on every single tick would mean
        redrawing the label thousands of times for no visible benefit."""
        label = getattr(self, "_active_status_label", None)
        if label is None:
            return
        duration = self._format_last_operation_duration()
        suffix = f" ({duration})" if duration else ""
        label.setText(f"{count} item{'' if count == 1 else 's'}{suffix}")

    def _set_operation_controls_enabled(self, enabled: bool):
        """Enable/disable the controls that could kick off a *second*,
        independent listing/navigation operation while one's already
        running against the shared progress bar/Cancel button --
        the whole Global Options group (Dual Pane, Synchronize Views,
        Symmetrical GUI, Recursive Folder Selections, Expand To
        Selection, Refresh), and each pane's Select Directory/Up-folder/
        Make New Folder buttons and path entry field.

        All of those end up calling update_listings() (which clears and
        rebuilds both trees from scratch) or, for Dual Pane/Symmetrical
        Buttons, a full widget rebuild. If one of those fires while a *different*
        operation is still mid-scan and periodically pumping the event
        loop to stay responsive/cancelable (see _CancellableProgress.tick
        and _start_transfer_progress's own initial pump), that first
        operation is left holding Python references to QTreeWidgetItems
        that just got deleted out from under it in C++ -- which is what
        caused a hard crash (RuntimeError: ... already deleted) rather
        than just wrong results.

        Selecting/expanding/collapsing/scrolling in the tree deliberately
        stay enabled throughout -- those don't clear anything, and
        disabling them would defeat the entire point of keeping the UI
        responsive during a long scan. Double-click-to-navigate and Up
        One Folder *do* clear things, though, and can't be blocked by
        simply disabling a widget the same way (a tree has to stay
        interactive for selection to keep working) -- see
        SyncMixin._on_item_double_clicked/go_up_one_folder for the
        narrower, in-code guards that cover those instead."""
        # The whole group at once, rather than its widgets one by one:
        # disabling a parent leaves each child's *own* enabled flag
        # alone, so the dependencies between them (Symmetrical GUI/
        # Synchronize Views off while Dual Pane is off, Expand To
        # Selection off while Recursive Folder Selections is) come back
        # exactly as they were once the group's re-enabled. The old
        # per-widget setEnabled(True) calls at the end of an operation
        # would re-enable e.g. Symmetrical GUI even with Dual Pane off.
        # Also covers Dual Pane and Synchronize Views, which weren't
        # locked out before despite Dual Pane triggering a full rebuild.
        group = getattr(self, "global_options_group", None)
        if group is not None:
            group.setEnabled(enabled)
        # The Main tab's mode switch and Swap pane button: a mode change
        # rebuilds the UI, a swap relists both panes.
        for attr in ("mode_duplicate_button", "mode_compare_button", "swap_pane_button"):
            button = getattr(self, attr, None)
            if button is not None:
                button.setEnabled(enabled)
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if pane is None:
                continue
            for attr in ("select_button", "up_button", "new_folder_button", "entry"):
                widget = getattr(pane, attr, None)
                if widget is not None:
                    widget.setEnabled(enabled)
        self._update_hash_buttons_enabled(enabled)

    def _begin_timed_operation(self, pane: "DirectoryPane | None" = None):
        """Record the start of an operation that reports a result count
        but doesn't go through _start_transfer_progress (Clear, File/All
        Parents, delete -- none of those show the progress bar at all,
        being effectively instant), so _show_result_count still has
        something to time. _start_transfer_progress does this itself for
        every progress-bar-driven operation; this covers the rest.

        `pane`, like _start_transfer_progress's own `pane` param, picks
        which side's status label (self._active_status_label) the
        eventual _show_result_count call lands on -- the right pane's
        own label when `pane` is the right pane, the left one otherwise
        (including `pane=None`). Matters here specifically because these
        instant operations never call _start_transfer_progress at all,
        so without this _active_status_label would just be left over
        from whatever the last progress-bar-driven cycle happened to
        set it to -- possibly the wrong side, or stale from a much
        earlier operation."""
        self._operation_start_time = time.monotonic()
        is_right = pane is not None and pane is getattr(self, "right", None)
        self._active_status_label = self.right_selection_label if is_right else self.left_selection_label

    def _format_last_operation_duration(self) -> str:
        """How long since the current operation began (see
        _begin_timed_operation/_start_transfer_progress) -- h:mm:ss with
        every leading zero *segment* dropped rather than just the hours:
        under a minute shows as "N.NNs" (two decimal places, no "0:"
        minutes prefix either), under an hour as "M:SS", only reaching
        "H:MM:SS" once there's actually an hour to show. Most operations
        here take seconds, not hours or even whole minutes -- decimals
        matter at that scale (plenty of these finish in well under a
        second now), but would just be noise once minutes/hours are
        already in the picture, so those stay whole-second."""
        start = getattr(self, "_operation_start_time", None)
        if start is None:
            return ""
        elapsed = max(0.0, time.monotonic() - start)
        total = int(elapsed)
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        if minutes:
            return f"{minutes}:{seconds:02d}"
        return f"{elapsed:.2f}s"

    def _append_operation_duration(self):
        """Append this operation's elapsed time to whatever's already
        showing in the active status label, instead of replacing it
        outright the way _show_result_count does. Used by the Select
        group's own search buttons (Missing/Matching/Newer/Older/
        Smaller/Larger/Diff Folders -- see SelectionMixin._run_select/
        _finish_select_progress): by the time one of those finishes,
        update_selection_count has already written a genuinely more
        useful "N selected, X files, Y folders, <size>" breakdown to
        this exact same label (see ui_builder._build_status_line), and
        a search's own result *is* that same selection -- so replacing
        it with _show_result_count's generic "N items (duration)"
        summary right afterward, as used to happen, only threw away the
        breakdown the user just got and left a bare count and a
        timestamp in its place. Appending the timing onto the existing
        text instead keeps the breakdown intact.

        A second call before anything else touched the label (e.g. an
        immediately repeated search) replaces rather than doubles up
        the previous "(duration)" suffix."""
        target = getattr(self, "_active_status_label", None)
        if target is None:
            return
        duration = self._format_last_operation_duration()
        if not duration:
            return
        current = target.text()
        if current.endswith(")") and " (" in current:
            current = current.rsplit(" (", 1)[0]
        target.setText(f"{current} ({duration})")

    def _set_transfer_label(self, label: str, pane: "DirectoryPane | None" = None):
        """Set the label shown above this cycle's own bar while an
        operation is actively running ("Enumerating...", "Copying...",
        etc) -- self._active_status_label, picked by _start_transfer_
        progress to match whichever side's bar/Cancel button this cycle
        is using. `pane`, if given, overrides that for this call only
        (updating self._active_status_label to match) -- for callers
        that need to target a specific side without going through a
        full _start_transfer_progress/_begin_timed_operation cycle (see
        SelectionMixin.select_all, whose combined-total message must
        stay attached to whichever pane's own button was actually
        pressed, not whichever side an inner per-pane pass happened to
        touch last). Always resets to the plain white/non-bold look --
        see _show_result_count for the styled look a *finished*
        operation's result count gets -- so starting a new operation
        right after a result was shown doesn't carry that styling
        over."""
        if pane is not None:
            self._active_status_label = self._selection_label_for_pane(pane)
        target = getattr(self, "_active_status_label", None)
        if target is not None:
            target.setStyleSheet("color: #F5F5F5;")
            target.setText(label)

    def _selection_label_for_pane(self, pane: "DirectoryPane | None"):
        """The left/right status label instance for `pane` -- same left/
        right resolution _set_transfer_label/_show_result_count already
        do inline, factored out for call sites (like
        transfer_between_panes) that need to retarget self.
        _active_status_label at a specific pane themselves, without
        going through either of those (see _append_operation_duration,
        which only ever touches whatever's already targeted)."""
        is_right = pane is getattr(self, "right", None)
        return self.right_selection_label if is_right else self.left_selection_label

    def _show_result_count(self, count: int, pane: "DirectoryPane | None" = None):
        """Once an operation (a transfer, a Select-group search, Invert,
        Clear, File Parent/All Parents, etc) finishes: how many items it
        worked on/found/selected, and how long that took (see
        _format_last_operation_duration) -- shown in this cycle's own
        side's status label (self._active_status_label), the same one
        _set_transfer_label used while it was running. `pane`, if given,
        overrides that (see _set_transfer_label's own `pane` param for
        why a caller would need to). Timing starts once per outer
        operation (nested/reused progress cycles -- e.g. a folder auto-
        populating mid-selection -- don't restart the clock; see
        _start_transfer_progress/_begin_timed_operation), so this
        reflects the whole thing the user actually asked for, not just
        its last phase. Yellow if the count is more than zero, plain
        white if it came up empty -- a quick "did anything actually
        happen" signal at a glance, same spirit as the Select group's
        own title color (see SelectionMixin.update_selection_count)."""
        if pane is not None:
            self._active_status_label = self._selection_label_for_pane(pane)
        target = getattr(self, "_active_status_label", None)
        if target is None:
            return
        color = "#F5D400" if count else "#F5F5F5"
        target.setStyleSheet(f"color: {color}; font-weight: bold;")
        duration = self._format_last_operation_duration()
        suffix = f" ({duration})" if duration else ""
        target.setText(f"{count} item{'' if count == 1 else 's'}{suffix}")

    def _finish_transfer_progress(self, result_count: "int | None" = None):
        """End the current operation's bar/Cancel cycle. `result_count`,
        if given, leaves the label showing what the operation actually
        did (see _show_result_count); every call site that has a
        meaningful count to report should pass one.

        Left out (the default), what happens depends on whether
        _start_transfer_progress's own `silent` flag was set for this
        cycle: a `silent` cycle gets the label restored to whatever it
        showed right before the cycle started (its own in-progress text/
        live-tick reading is discarded -- it was purely internal
        bookkeeping with nothing user-facing to report). A non-silent
        cycle with no result_count is left exactly as the live tick left
        it -- that's by design for operations that report their result
        purely through _update_live_count_label's own running tally
        rather than an explicit count here, and reverting past that
        would just throw the real result away. Either way, an untouched
        label naturally stays as-is until the next thing overwrites it
        (a selection change on that side, another operation, Clear) --
        these labels do double duty as each side's live selection count
        now (see ui_builder._build_status_line), so that's exactly the
        "count stays visible until something else happens" behavior
        this is meant to have.

        Resets whichever bar/Cancel button _start_transfer_progress
        picked for this cycle (self._active_progress_bar/
        self._active_cancel_button) -- not both sides unconditionally --
        so the *other* side's bar/Cancel (idle the whole time this
        operation ran) is never touched.

        Calls active_transfer_progress.flush_to_max() first, before any
        of the actual collapsing below -- cosmetic only, for a cycle
        whose `total` was an estimate (see statistics_ops.
        _on_timeline_selection) and so may finish with real ticks short
        of the bar's own maximum; a no-op for busy mode, a cancelled
        cycle, or a cycle whose ticks already reached `total` exactly,
        so this is safe to do unconditionally rather than threading an
        opt-in through every call site."""
        progress = getattr(self, "active_transfer_progress", None)
        if progress is not None:
            progress.flush_to_max()
        bar = getattr(self, "_active_progress_bar", None) or self.left_progress_bar
        bar.setProperty("busy", False)
        style = bar.style()
        style.unpolish(bar)
        style.polish(bar)
        bar.setRange(0, 1)
        bar.setValue(0)
        cancel_button = getattr(self, "_active_cancel_button", None) or self.left_cancel_button
        cancel_button.setEnabled(False)
        self.active_transfer_progress = None
        self._set_operation_controls_enabled(True)
        silent = getattr(self, "_silent_operation", False)
        self._silent_operation = False
        if result_count is not None:
            self._show_result_count(result_count)
        elif silent:
            target = getattr(self, "_active_status_label", None)
            if target is not None:
                target.setText(getattr(self, "_pre_operation_label_text", ""))
                target.setStyleSheet(
                    getattr(self, "_pre_operation_label_style", "color: #F5F5F5;")
                )

    def _cancel_transfer_progress(self):
        """Shared Cancel handler for both left_cancel_button and right_
        cancel_button: stops the in-progress move/paste, enumeration,
        item recount, or Select-group search -- whichever button was
        clicked, since only one such operation is ever running at once
        (see _start_transfer_progress), so both buttons cancel the same
        self.active_transfer_progress regardless of which side's own
        button happened to be the enabled one. Whatever's already been
        moved/copied when this is clicked stays that way -- only items
        not yet reached are skipped; see _transfer_paths."""
        if self.active_transfer_progress is not None:
            self.active_transfer_progress.cancelled = True

    def _update_listings_after_transfer(self, progress, **update_kwargs):
        """self.update_listings(), but not short-circuited by `progress`
        already being marked cancelled from the transfer that just ran --
        see transfer_between_panes/paste_from_clipboard, which call this
        while `progress` is still active_transfer_progress (so
        update_listings sees owns_progress=False and would otherwise
        honor an already-cancelled flag as "stop this refresh early too").

        That's the right behavior for a nested phase *within* the same
        logical operation (see _CancellableProgress.start_phase's own
        "cancellation carries over" note) but wrong here: reflecting
        what's actually on disk after a transfer is its own step that
        should run in full regardless of whether the transfer itself got
        cut short -- otherwise the very first pane refreshed (always
        `self.left`, see update_listings) rebuilds its snapshot from a
        progress that's already reporting cancelled, which aborts
        _build_snapshot on the first entry and leaves that pane's
        snapshot (and, through it, its embedded timeline -- see
        _refresh_pane_timeline) looking at an empty tree, even though the
        on-screen file list itself was left showing the old, merely
        stale listing (its own update_listing call gets skipped the same
        way) rather than anything actually wrong -- which is what made
        the bug read as "the timeline went blank" specifically.

        The original cancelled state is preserved across the call
        (restored after, unless the refresh triggers a fresh cancel of
        its own -- Cancel stays live throughout) so a caller checking
        progress.cancelled right after this to decide whether to still
        select what was just transferred still sees the transfer's own
        cancellation correctly."""
        was_cancelled = progress.cancelled
        progress.cancelled = False
        try:
            self.update_listings(**update_kwargs)
        finally:
            progress.cancelled = was_cancelled or progress.cancelled

    def _pane_for_tree(self, tree):
        return self.left if tree is self.left.tree else self.right

    def _clear_cut_marks(self):
        for item in self._cut_items:
            font = item.font(0)
            font.setItalic(False)
            for col in range(COLUMN_COUNT):
                item.setFont(col, font)
        self._cut_items = []
        self._cut_source_tree = None

    def copy_to_clipboard(self, pane: DirectoryPane):
        self._do_copy_or_cut(pane, cut=False)

    def cut_to_clipboard(self, pane: DirectoryPane):
        self._do_copy_or_cut(pane, cut=True)

    def copy_flat_to_clipboard(self, pane: DirectoryPane):
        """Copy without the folder structure: every selected item ends up
        directly in whatever folder it is pasted into, here or in another
        file explorer. A selected folder still brings its own contents
        (and an item selected inside a selected folder is just part of
        that folder's copy -- see _dedupe_nested_paths). Items that share
        a name get " (1)", " (2)", ... on all but the first (before the
        extension for files). Only when some name actually had to change
        are the items staged under their new names in a temporary folder
        (hard links where the file system allows, copies otherwise) so
        that other programs see a conflict-free set; otherwise the plain
        paths go on the clipboard, with no source base folder so that
        Paste drops each one directly into the target."""
        if self._snapshot_blocks(pane, what="Copy flat"):
            return
        selected_items = pane.tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "No items selected.")
            return
        paths = self._dedupe_nested_paths([item.data(0, PATH_ROLE) for item in selected_items])
        names = self._unique_flat_names(paths)
        self._cleanup_flat_stage()
        staged_dir = None
        if any(names[i] != os.path.basename(p) for i, p in enumerate(paths)):
            staged_dir = tempfile.mkdtemp(prefix="dircomp_flat_")
            self._flat_stage_dir = staged_dir
            atexit.register(shutil.rmtree, staged_dir, True)
            error = None
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                paths = [self._stage_flat_item(p, os.path.join(staged_dir, names[i])) for i, p in enumerate(paths)]
            except OSError as e:
                error = e
            finally:
                QApplication.restoreOverrideCursor()
            if error is not None:
                self._cleanup_flat_stage()
                QMessageBox.warning(self, "Copy flat", f"Could not prepare the items: {error}")
                return
        self._set_clipboard_paths(paths, cut=False, base_dir=staged_dir)
        self._clear_cut_marks()

    @staticmethod
    def _unique_flat_names(paths) -> list:
        """One name per path, same order: the basename, with " (n)" added
        (before the extension, for files) to later ones sharing it."""
        used = {}
        result = []
        for p in paths:
            name = os.path.basename(os.path.normpath(p))
            candidate = name
            if candidate.lower() in used:
                stem, ext = (name, "") if os.path.isdir(p) else os.path.splitext(name)
                n = used[name.lower()]
                while True:
                    n += 1
                    candidate = f"{stem} ({n}){ext}"
                    if candidate.lower() not in used:
                        break
                used[name.lower()] = n
            used.setdefault(candidate.lower(), 0)
            result.append(candidate)
        return result

    @staticmethod
    def _stage_flat_item(src, dest):
        """Hard-link (or copy) `src` to `dest`; folders are rebuilt the
        same way. Returns dest."""
        def link_or_copy(s, d, **_kw):
            try:
                os.link(s, d)
            except OSError:
                shutil.copy2(s, d)
            return d
        if os.path.isdir(src):
            shutil.copytree(src, dest, copy_function=link_or_copy, symlinks=True)
        else:
            link_or_copy(src, dest)
        return dest

    def _cleanup_flat_stage(self):
        stage = getattr(self, "_flat_stage_dir", None)
        if stage:
            shutil.rmtree(stage, ignore_errors=True)
            self._flat_stage_dir = None

    def _set_clipboard_paths(self, paths, cut: bool, base_dir):
        self._clipboard_source_base_dir = base_dir
        urls = [QUrl.fromLocalFile(p) for p in paths]
        mime = QMimeData()
        mime.setUrls(urls)
        op = b"cut" if cut else b"copy"
        uri_lines = "\n".join(u.toString() for u in urls)
        mime.setData(GNOME_COPIED_FILES_MIME, op + b"\n" + uri_lines.encode("utf-8"))
        QApplication.clipboard().setMimeData(mime)

    def _do_copy_or_cut(self, pane: DirectoryPane, cut: bool):
        if self._snapshot_blocks(pane, what="Cut/Copy"):
            return
        self._cleanup_flat_stage()
        selected_items = pane.tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "No items selected.")
            return

        base_dir = pane.entry.text()
        file_paths = [item.data(0, PATH_ROLE) for item in selected_items]
        self._clipboard_source_base_dir = base_dir
        urls = [QUrl.fromLocalFile(p) for p in file_paths]

        mime = QMimeData()
        mime.setUrls(urls)
        op = b"cut" if cut else b"copy"
        uri_lines = "\n".join(u.toString() for u in urls)
        mime.setData(GNOME_COPIED_FILES_MIME, op + b"\n" + uri_lines.encode("utf-8"))
        QApplication.clipboard().setMimeData(mime)

        # Clear any previous cut marks before applying a new state.
        self._clear_cut_marks()
        if cut:
            self._cut_source_tree = pane.tree
            self._cut_items = list(selected_items)
            for item in selected_items:
                font = item.font(0)
                font.setItalic(True)
                for col in range(COLUMN_COUNT):
                    item.setFont(col, font)

    def delete_selected(self, pane: DirectoryPane):
        if self._snapshot_blocks(pane, what="Delete"):
            return
        if not SEND2TRASH_AVAILABLE:
            QMessageBox.warning(
                self, "Delete",
                "The 'send2trash' package is not installed.\n"
                "Install it with:  pip install send2trash",
            )
            return

        selected_items = pane.tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "No items selected.")
            return

        paths = [item.data(0, PATH_ROLE) for item in selected_items]
        # Selecting a whole matched folder (Matching/Missing/Select All
        # with select_subtree=True) leaves every item inside it
        # individually selected too -- see
        # SelectionMixin._set_whole_subtree_selected -- so the raw
        # selection can contain both a folder and its own descendants.
        # Deleting each one independently would send the folder to
        # trash, then immediately fail trying to trash the now-gone
        # children underneath it: same end result, spurious "errors"
        # reported for a delete that actually fully succeeded. Only the
        # outermost selected item along each branch needs to actually be
        # deleted; _transfer_paths already relies on the same dedup for
        # copy/move.
        paths = self._dedupe_nested_paths(paths)

        choice = QMessageBox.question(
            self, "Move to Trash",
            f"Move {len(paths)} item{'' if len(paths) == 1 else 's'} to trash?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return

        self._begin_timed_operation(pane)
        errors = []
        deleted_count = 0
        for path in paths:
            try:
                send2trash(path)
                deleted_count += 1
                self._cache_path_deleted(path)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")

        if pane.tree is self._cut_source_tree:
            self._clear_cut_marks()

        self.update_listings(initiating_pane=pane)
        self._show_result_count(deleted_count)

        if errors:
            QMessageBox.warning(self, "Delete completed with errors", "\n".join(errors))

    def _check_space_for_transfer(self, paths, dest_dir, move: bool) -> bool:
        """Warn if the destination drive doesn't have enough free space.
        Returns True if the transfer should proceed.

        A same-drive move is just a relocation (no extra space needed), so
        only cross-drive moves and all copies count toward the total."""
        try:
            dest_dev = os.stat(dest_dir).st_dev
        except OSError:
            return True

        needed = 0
        for src in paths:
            try:
                src_dev = os.stat(src).st_dev
            except OSError:
                continue
            if move and src_dev == dest_dev:
                continue
            needed += self._path_size(src)

        if needed == 0:
            return True

        try:
            free = shutil.disk_usage(dest_dir).free
        except OSError:
            return True

        if needed <= free:
            return True

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Insufficient Space")
        msg.setText(
            f"This operation needs about {self.format_size(needed)}, but only "
            f"{self.format_size(free)} is free on the destination drive."
        )
        msg.setInformativeText("Continue anyway, or cancel the operation?")
        continue_btn = msg.addButton("Continue", QMessageBox.AcceptRole)
        msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.exec()
        return msg.clickedButton() is continue_btn

    def _safe_rel_root(self, paths, rel_root):
        """Return rel_root if every path in `paths` actually lives under it
        (so relative sub-paths make sense there), otherwise None."""
        if not rel_root or not os.path.isdir(rel_root):
            return None
        rel_root_abs = os.path.abspath(rel_root)
        for p in paths:
            try:
                common = os.path.commonpath([os.path.abspath(p), rel_root_abs])
            except ValueError:
                return None
            if common != rel_root_abs:
                return None
        return rel_root

    def _dedupe_nested_paths(self, paths):
        """Drop any path that's already contained inside another path in
        the same list -- e.g. a folder and one of its own descendants both
        selected at once. Copying/moving the ancestor already carries the
        descendant along, so treating the descendant as a second, separate
        item is redundant and is what caused a spurious "already exists"
        warning: the ancestor's copy would create it first, then the
        descendant's own turn would find it already there."""
        entries = [(p, os.path.normpath(os.path.abspath(p))) for p in paths]
        abs_set = {ap for _, ap in entries}
        result = []
        for p, ap in entries:
            parent = os.path.dirname(ap)
            nested = False
            while parent and parent != os.path.dirname(parent):
                if parent in abs_set:
                    nested = True
                    break
                parent = os.path.dirname(parent)
            if not nested:
                result.append(p)
        return result

    def _enumerate_folders_only(self, paths, recursive, progress=None):
        """Like _enumerate_transfer_items, but folders only, and never
        looks at files at all. With `recursive` on, walks each folder in
        `paths` all the way down, same as a real transfer's own
        enumeration; off, just takes the top-level folders in `paths`
        as-is, without descending into their own subfolders. Returns
        whatever had been found so far if cancelled partway through --
        same convention as _enumerate_transfer_items."""
        dirs = []
        for p in paths:
            if progress is not None and not progress.tick():
                return dirs
            dirs.append(p)
            if not recursive:
                continue
            for root, subdirs, _files in os.walk(p):
                for d in subdirs:
                    if progress is not None and not progress.tick():
                        return dirs
                    dirs.append(os.path.join(root, d))
        return dirs

    def _enumerate_transfer_items(self, paths, progress=None):
        """Recursively walk every path in `paths` (the top-level
        files/folders selected for a transfer) and return (dirs, files):
        every subdirectory found (so empty folders get recreated at the
        destination too, not just the ones that happen to contain a
        file) and every actual file, at any depth. This is what makes
        the progress bar afterward track real work instead of top-level
        item count -- one giant folder used to count as a single tick
        that then sat at the same spot until the whole thing finished;
        now it's counted (and progress reported) file by file.

        `progress`, if given, is ticked once per filesystem entry visited
        -- runs in whatever mode the bar is currently in (typically busy,
        since the total isn't known until this finishes) purely so
        Cancel works and the UI stays responsive during a large scan, not
        to represent a fraction complete. Returns whatever had been
        found so far if cancelled partway through."""
        dirs = []
        files = []
        for p in paths:
            if progress is not None and not progress.tick():
                return dirs, files
            if os.path.isdir(p):
                dirs.append(p)
                for root, subdirs, filenames in os.walk(p):
                    for d in subdirs:
                        if progress is not None and not progress.tick():
                            return dirs, files
                        dirs.append(os.path.join(root, d))
                    for fname in filenames:
                        if progress is not None and not progress.tick():
                            return dirs, files
                        files.append(os.path.join(root, fname))
            else:
                files.append(p)
        return dirs, files

    def _transfer_paths(self, paths, dest_dir, move: bool, rel_root=None, progress=None):
        """Copy or move each path into dest_dir, confirming before overwrite.
        When rel_root is given, each item's path relative to rel_root is
        preserved under dest_dir -- recreating any subfolders as needed --
        so a nested selection lands in the same relative spot on the other
        side. Without it, items are placed directly in dest_dir by basename.

        Runs in two phases against `progress` (if given): first a full
        recursive enumeration (see _enumerate_transfer_items, busy-mode,
        labelled "Enumerating..."), then the actual copy/move, retargeted
        (see _CancellableProgress.start_phase) to a determinate total and
        ticked once per *file* -- so the bar advances roughly linearly
        with real work even when the selection is a few huge folders,
        instead of stalling at "1 of 3" for the duration of the biggest
        one. Cancelling during either phase stops the transfer; whatever
        was already written stays written.

        An overwrite conflict asks once, with Yes/Yes to All/No/No to
        All/Cancel -- picking a "to All" option then applies silently to
        every later conflict in this same transfer (see
        overwrite_policy), so a huge tree landing on top of itself
        doesn't mean a dialog per file; Cancel stops the whole transfer
        the same as clicking the progress bar's own Cancel button would.

        Returns (errors, dest_paths): a list of error strings, and the
        *top-level* destination paths that were actually reached (used to
        auto-select the transferred items afterward)."""
        paths = self._dedupe_nested_paths(paths)
        if not self._check_space_for_transfer(paths, dest_dir, move):
            return [], []

        if progress is not None:
            self._set_transfer_label("Enumerating...")
        dirs, files = self._enumerate_transfer_items(paths, progress=progress)
        if progress is not None and progress.cancelled:
            return [], []

        if progress is not None:
            progress.start_phase(len(dirs) + len(files))
            self._set_transfer_label("Moving..." if move else "Copying...")

        tops = [os.path.normpath(os.path.abspath(p)) for p in paths]

        def dest_for(src):
            if rel_root:
                rel = os.path.relpath(src, rel_root)
            else:
                # No common root: each top-level item lands directly in
                # dest_dir under its own name, carrying whatever it holds
                # (a folder's contents keep their structure inside it).
                asrc = os.path.normpath(os.path.abspath(src))
                rel = os.path.basename(asrc)
                for top in tops:
                    if asrc == top or asrc.startswith(top + os.sep):
                        rel = os.path.join(os.path.basename(top), os.path.relpath(asrc, top)) if asrc != top else os.path.basename(top)
                        break
            return os.path.join(dest_dir, rel)

        errors = []

        # Recreate the directory structure first -- including empty leaf
        # folders, which a plain file-by-file copy would otherwise drop.
        for d in dirs:
            if progress is not None and not progress.tick():
                break
            dest = dest_for(d)
            try:
                os.makedirs(dest, exist_ok=True)
            except OSError as e:
                errors.append(f"{d}: {e}")

        # None = ask on the next conflict; True/False = "to All" was
        # already picked once this transfer, so every later conflict is
        # resolved silently the same way instead of asking again.
        overwrite_policy = None

        moved_files = []
        transferred = []  # (src, dest) pairs, for the hash cache
        for src in files:
            if progress is not None and not progress.tick():
                break
            if not os.path.exists(src):
                errors.append(f"{src} (not found)")
                continue

            dest = dest_for(src)
            if os.path.abspath(src) == os.path.abspath(dest):
                continue

            dest_parent = os.path.dirname(dest)
            if dest_parent and not os.path.isdir(dest_parent):
                try:
                    os.makedirs(dest_parent, exist_ok=True)
                except OSError as e:
                    errors.append(f"{dest_parent}: {e}")
                    continue

            if os.path.exists(dest):
                if overwrite_policy is None:
                    choice = QMessageBox.question(
                        self, "Overwrite?",
                        f"'{os.path.basename(dest)}' already exists in the destination. Overwrite it?",
                        QMessageBox.Yes | QMessageBox.YesToAll | QMessageBox.No
                        | QMessageBox.NoToAll | QMessageBox.Cancel,
                    )
                    if choice == QMessageBox.Cancel:
                        if progress is not None:
                            progress.cancelled = True
                        break
                    if choice == QMessageBox.YesToAll:
                        overwrite_policy = True
                    elif choice == QMessageBox.NoToAll:
                        overwrite_policy = False
                    overwrite = choice in (QMessageBox.Yes, QMessageBox.YesToAll)
                else:
                    overwrite = overwrite_policy
                if not overwrite:
                    continue
                try:
                    if os.path.isdir(dest):
                        shutil.rmtree(dest)
                    else:
                        os.remove(dest)
                except OSError as e:
                    errors.append(f"{dest}: {e}")
                    continue

            try:
                if move:
                    shutil.move(src, dest)
                else:
                    shutil.copy2(src, dest)
            except OSError as e:
                errors.append(f"{src}: {e}")
                continue
            moved_files.append(src)
            transferred.append((src, dest))

        # Keep the hash cache right: moved files take their hashes along,
        # copies inherit them when size and date are equal.
        if transferred:
            if move:
                self._cache_files_moved(transferred)
            else:
                self._cache_files_copied(transferred)

        if move:
            # The directories themselves were recreated at the
            # destination up front, but the *source* copies are only
            # emptied out as their files get moved above -- clean up
            # whatever's now-empty on the source side, deepest first, so
            # a moved folder doesn't leave a hollow shell behind. Left
            # alone (silently) if something inside failed to move, or
            # wasn't part of this transfer to begin with.
            for d in sorted(dirs, key=len, reverse=True):
                try:
                    os.rmdir(d)
                except OSError:
                    pass

        moved_set = set(moved_files)
        dest_paths = []
        for p in paths:
            if os.path.isdir(p):
                dest_paths.append(dest_for(p))
            elif p in moved_set:
                dest_paths.append(dest_for(p))
        return errors, dest_paths

    def rename_selected(self, pane: DirectoryPane):
        if self._snapshot_blocks(pane, what="Rename"):
            return
        """Rename the single selected item. With Synchronize Views on,
        also renames its counterpart on the other pane -- found by the
        same relative path under that pane's own root, the same
        correspondence Make New Folder and the Select-group searches
        use -- if one exists there."""
        selected_items = pane.tree.selectedItems()
        if len(selected_items) != 1:
            QMessageBox.warning(self, "Rename", "Select exactly one item to rename.")
            return

        old_path = selected_items[0].data(0, PATH_ROLE)
        if not old_path or not os.path.exists(old_path):
            QMessageBox.warning(self, "Rename", "That item no longer exists.")
            return
        old_name = os.path.basename(old_path)

        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=old_name)
        if not ok:
            return
        name = name.strip()
        if not name or name == old_name:
            return
        if os.sep in name or (os.altsep and os.altsep in name):
            QMessageBox.warning(self, "Rename", "Name can't contain a path separator.")
            return

        new_path = os.path.join(os.path.dirname(old_path), name)
        if os.path.exists(new_path):
            QMessageBox.warning(self, "Rename", f"'{name}' already exists here.")
            return

        targets = [(pane, old_path, new_path)]
        if self._synchronize_views_enabled():
            base_dir = pane.entry.text()
            other_pane = self.right if pane is self.left else self.left
            other_base = other_pane.entry.text()
            if other_base and os.path.isdir(other_base) and self._safe_rel_root([old_path], base_dir):
                other_old_path = os.path.join(other_base, os.path.relpath(old_path, base_dir))
                if os.path.exists(other_old_path):
                    other_new_path = os.path.join(os.path.dirname(other_old_path), name)
                    if not os.path.exists(other_new_path):
                        targets.append((other_pane, other_old_path, other_new_path))

        errors = []
        renamed = []
        for p, src, dest in targets:
            try:
                os.rename(src, dest)
                renamed.append((p, dest))
                self._cache_path_renamed(src, dest)
            except OSError as e:
                errors.append(f"{src}: {e}")

        if pane.tree is self._cut_source_tree:
            self._clear_cut_marks()

        self.update_listings(initiating_pane=pane)
        for p, dest in renamed:
            self._select_paths_in_tree(p.tree, [dest])

        if errors:
            QMessageBox.warning(self, "Rename completed with errors", "\n".join(errors))

    def create_new_folder(self, pane: DirectoryPane):
        if self._snapshot_blocks(pane, what="New Folder"):
            return
        """Create a new folder in `pane`'s current directory. With
        Synchronize Views on, also creates one of the same name in the
        other pane's current directory (if it has a valid one)."""
        current_dir = pane.entry.text()
        if not current_dir or not os.path.isdir(current_dir):
            QMessageBox.warning(self, "New Folder", "Select a valid directory first.")
            return

        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if os.sep in name or (os.altsep and os.altsep in name):
            QMessageBox.warning(self, "New Folder", "Folder name can't contain a path separator.")
            return

        targets = [(pane, current_dir)]
        if self._synchronize_views_enabled():
            other_pane = self.right if pane is self.left else self.left
            other_dir = other_pane.entry.text()
            if other_dir and os.path.isdir(other_dir):
                targets.append((other_pane, other_dir))

        errors = []
        created = []
        for p, d in targets:
            new_path = os.path.join(d, name)
            if os.path.exists(new_path):
                errors.append(f"'{name}' already exists in {d}.")
                continue
            try:
                os.makedirs(new_path)
                created.append((p, new_path))
            except OSError as e:
                errors.append(f"{new_path}: {e}")

        self.update_listings(initiating_pane=pane)
        for p, path in created:
            self._select_paths_in_tree(p.tree, [path])

        if errors:
            QMessageBox.warning(self, "New Folder", "\n".join(errors))

    def transfer_between_panes(self, source_pane: DirectoryPane, dest_pane: DirectoryPane, move: bool):
        if self._snapshot_blocks(source_pane, dest_pane, what="Copy/Move"):
            return
        selected_items = source_pane.tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Selection Error", "No items selected.")
            return

        dest_dir = dest_pane.entry.text()
        if not dest_dir or not os.path.isdir(dest_dir):
            QMessageBox.warning(self, "Transfer", "Select a valid destination directory first.")
            return

        src_dir = source_pane.entry.text()
        paths = [item.data(0, PATH_ROLE) for item in selected_items]
        rel_root = self._safe_rel_root(paths, src_dir)

        progress = self._start_transfer_progress(label="Enumerating...", pane=source_pane)
        errors, dest_paths = [], []
        try:
            errors, dest_paths = self._transfer_paths(paths, dest_dir, move=move, rel_root=rel_root, progress=progress)

            if move and source_pane.tree is self._cut_source_tree:
                self._clear_cut_marks()

            self._update_listings_after_transfer(progress)

            # Keep the same bar/Cancel running through the "select what
            # was just transferred" step too, rather than resetting to
            # 0% right after the copy/move and then applying the
            # selection in one silent, uncancellable pass -- see
            # _select_paths_in_tree's progress parameter. Sized to the
            # destination tree's own item count (what the walk actually
            # has to visit), not dest_paths' length (what it's looking
            # for).
            if not progress.cancelled and dest_paths:
                self._set_transfer_label("Selecting...")
                progress.start_phase(sum(1 for _ in self._iter_items(dest_pane.tree)) or None)
                # auto_expand matches the Select group's own searches
                # (see SelectionMixin._run_select) instead of always
                # expanding regardless of that setting, and the same
                # "go look at what just got selected" scroll those get
                # too -- a paste landing a bunch of newly-selected items
                # somewhere off-screen in the destination pane used to
                # leave the user with no visual confirmation anything
                # actually arrived without scrolling to find it by hand.
                self._select_paths_in_tree(
                    dest_pane.tree, dest_paths, progress=progress,
                    auto_expand=self._expand_folders_matching_selection_enabled(),
                )
                self._scroll_tree_to_first_selected(dest_pane.tree)

            # A copy (not a move) leaves the source items themselves
            # untouched -- update_listings() above just rebuilt the
            # source tree from scratch (fresh QTreeWidgetItems, nothing
            # selected), so without this the source selection would
            # simply vanish even though every one of those items is
            # still sitting right there. Restore it to what it was
            # before the copy, same paths (`paths`, captured above --
            # still valid, since nothing was removed). Skipped for a
            # move (those paths are gone from the source now, there's
            # nothing to reselect) and while Synchronize Views is on
            # (its own selection-mirroring already governs what the
            # source pane ends up showing as selected; layering this on
            # top would fight it rather than complement it).
            if not move and not self._synchronize_views_enabled() and not progress.cancelled and paths:
                self._set_transfer_label("Reselecting source...")
                progress.start_phase(sum(1 for _ in self._iter_items(source_pane.tree)) or None)
                self._select_paths_in_tree(source_pane.tree, paths, progress=progress)
        finally:
            # Append the duration to whichever side(s) actually ended up
            # with a fresh update_selection_count breakdown above,
            # rather than the old _finish_transfer_progress(result_
            # count=len(selected_items)) call this replaces: that
            # overwrote source_pane's own label (self._active_status_
            # label stays pointed there the whole way through, from
            # _start_transfer_progress(..., pane=source_pane) at the
            # top -- nothing above retargets it) with a generic "N
            # items (duration)" -- discarding whatever "Reselecting
            # source..." had just written there -- while dest_pane's own
            # "N selected, X files, Y folders, <size>" breakdown (from
            # the "Selecting..." step above) was left with no timing
            # attached to it at all.
            self._finish_transfer_progress()
            appended = False
            if not progress.cancelled and dest_paths:
                self._active_status_label = self._selection_label_for_pane(dest_pane)
                self._append_operation_duration()
                appended = True
            if not move and not self._synchronize_views_enabled() and not progress.cancelled and paths:
                self._active_status_label = self._selection_label_for_pane(source_pane)
                self._append_operation_duration()
                appended = True
            if not appended:
                # Cancelled, or nothing actually ended up transferred --
                # neither branch above had a fresh breakdown to attach
                # the timing to, so fall back to the old generic
                # count/duration message rather than leaving the label
                # stuck on stale "Enumerating.../Copying..." text.
                self._show_result_count(len(dest_paths), pane=source_pane)

        if errors:
            QMessageBox.warning(self, "Transfer completed with errors", "\n".join(errors))

    def paste_from_clipboard(self, pane: DirectoryPane, target_dir: "str | None" = None):
        if self._snapshot_blocks(pane, what="Paste"):
            return
        # target_dir: where to paste (the right-click menu passes the
        # clicked folder / the clicked file's parent); None = the pane's
        # own root folder, as the Clipboard group's Paste button does.
        base_dir = target_dir or pane.entry.text()
        if not base_dir or not os.path.isdir(base_dir):
            QMessageBox.warning(self, "Paste", "Select a valid directory first.")
            return

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        cut = False
        urls = []
        if mime.hasFormat(GNOME_COPIED_FILES_MIME):
            raw = bytes(mime.data(GNOME_COPIED_FILES_MIME)).decode("utf-8", "replace")
            lines = raw.splitlines()
            if lines:
                cut = lines[0].strip() == "cut"
                urls = [QUrl(u) for u in lines[1:] if u.strip()]
        elif mime.hasUrls():
            urls = mime.urls()

        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if not paths:
            QMessageBox.warning(self, "Paste", "Clipboard has no files to paste.")
            return

        rel_root = self._safe_rel_root(paths, self._clipboard_source_base_dir)

        progress = self._start_transfer_progress(label="Enumerating...", pane=pane)
        errors, dest_paths = [], []
        try:
            errors, dest_paths = self._transfer_paths(paths, base_dir, move=cut, rel_root=rel_root, progress=progress)

            if cut:
                QApplication.clipboard().clear()
                self._clear_cut_marks()

            self._update_listings_after_transfer(progress)

            # See transfer_between_panes -- same reason for keeping this
            # bar/Cancel alive through the post-paste selection step
            # instead of resetting between the copy and this.
            if not progress.cancelled and dest_paths:
                self._set_transfer_label("Selecting...")
                progress.start_phase(sum(1 for _ in self._iter_items(pane.tree)) or None)
                # See transfer_between_panes -- same auto_expand/scroll
                # treatment for a plain Paste's own newly-selected items.
                self._select_paths_in_tree(
                    pane.tree, dest_paths, progress=progress,
                    auto_expand=self._expand_folders_matching_selection_enabled(),
                )
                self._scroll_tree_to_first_selected(pane.tree)
        finally:
            # Same "keep the breakdown, add the timing" treatment as
            # transfer_between_panes above, rather than the old
            # _finish_transfer_progress(result_count=len(dest_paths))
            # call this replaces (which overwrote pane's own freshly-
            # selected breakdown from the "Selecting..." step with a
            # generic "N items (duration)").
            self._finish_transfer_progress()
            if not progress.cancelled and dest_paths:
                self._active_status_label = self._selection_label_for_pane(pane)
                self._append_operation_duration()
            else:
                # Cancelled, or nothing actually got pasted -- no fresh
                # breakdown to attach the timing to, so fall back to
                # the generic count/duration message.
                self._show_result_count(len(dest_paths), pane=pane)

        if errors:
            QMessageBox.warning(self, "Paste completed with errors", "\n".join(errors))

    def paste_folders_from_clipboard(self, pane: DirectoryPane, target_dirs: "list[str] | None" = None):
        if self._snapshot_blocks(pane, what="Paste Folders"):
            return
        """Paste Folders: like Paste, but recreates only the *directory*
        structure from whatever's cut/copied -- no files. With Recursive
        Folder Selections on, every subfolder at any depth under a
        copied folder is recreated too (still empty); with it off, only
        the top-level folders actually on the clipboard are created,
        exactly as selected -- their own subfolders are left alone.
        Files on the clipboard (and, when not recursing, a folder's
        nested subfolders) are silently skipped rather than erroring.

        `target_dirs`: the folders to paste into (the right-click menu
        passes one per distinct clicked/selected location); None = just
        the pane's own root folder, as the Clipboard group's button does.
        With more than one, the person is asked to confirm first.

        The source folder list is enumerated in full, once, *before*
        anything is created, and then reused for every target -- so
        pasting a folder into itself (or into one of its own subfolders,
        or into a target that's inside another target's copy) can't
        pick up the folders this very operation just made. Never treated
        as a "cut": the originals are left where they are."""
        targets = list(dict.fromkeys(target_dirs or [pane.entry.text()]))
        for t in targets:
            if not t or not os.path.isdir(t):
                QMessageBox.warning(self, "Paste Folders", f"Not a valid directory:\n{t}" if t else "Select a valid directory first.")
                return

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        urls = []
        if mime.hasFormat(GNOME_COPIED_FILES_MIME):
            raw = bytes(mime.data(GNOME_COPIED_FILES_MIME)).decode("utf-8", "replace")
            lines = raw.splitlines()
            urls = [QUrl(u) for u in lines[1:] if u.strip()]
        elif mime.hasUrls():
            urls = mime.urls()

        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        folder_paths = [p for p in paths if os.path.isdir(p)]
        if not folder_paths:
            QMessageBox.warning(self, "Paste Folders", "Clipboard has no folders to paste.")
            return

        if len(targets) > 1:
            answer = QMessageBox.question(
                self, "Paste Folders",
                f"{len(targets)} locations are selected.\n\nPaste the folders into all of them?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        rel_root = self._safe_rel_root(folder_paths, self._clipboard_source_base_dir)
        recursive = self._recursive_folder_contents_enabled()
        top_set = set(folder_paths)

        def dest_for(src, base_dir):
            rel = os.path.relpath(src, rel_root) if rel_root else os.path.basename(src)
            return os.path.join(base_dir, rel)

        errors = []
        dest_paths = []
        progress = self._start_transfer_progress(label="Enumerating...", pane=pane)
        try:
            dirs = self._enumerate_folders_only(folder_paths, recursive, progress=progress)
            if not progress.cancelled:
                progress.start_phase(len(dirs) * len(targets))
                self._set_transfer_label("Creating folders...")
                stop = False
                for base_dir in targets:
                    for d in dirs:
                        if not progress.tick():
                            stop = True
                            break
                        dest = dest_for(d, base_dir)
                        try:
                            os.makedirs(dest, exist_ok=True)
                            if d in top_set and dest not in dest_paths:
                                dest_paths.append(dest)
                        except OSError as e:
                            errors.append(f"{d} -> {base_dir}: {e}")
                    if stop:
                        break

            # Not self.update_listings(): a cancelled `progress` would
            # make that refresh abort halfway -- see
            # _update_listings_after_transfer.
            self._update_listings_after_transfer(progress, initiating_pane=pane)
            if dest_paths:
                # Whatever was created is selected -- also after a Cancel,
                # so a half-finished or mistaken paste can be deleted
                # straight away. The previous selection is dropped first
                # so the new folders are the only thing selected. Expands
                # to them only when Recursive Folder Selections + Expand
                # To Selection are both on. The cancelled flag is lifted
                # for the duration (and restored) so the selection step
                # itself can't be cut short by the very Cancel click that
                # stopped the paste.
                was_cancelled = progress.cancelled
                progress.cancelled = False
                try:
                    pane.tree.clearSelection()
                    self._select_paths_in_tree(
                        pane.tree, dest_paths,
                        auto_expand=self._expand_folders_matching_selection_enabled(),
                    )
                    self._scroll_tree_to_first_selected(pane.tree)
                finally:
                    progress.cancelled = was_cancelled
        finally:
            self._finish_transfer_progress()
            if dest_paths:
                self._active_status_label = self._selection_label_for_pane(pane)
                self._append_operation_duration()
            else:
                self._show_result_count(len(dest_paths), pane=pane)

        if errors:
            QMessageBox.warning(self, "Paste Folders completed with errors", "\n".join(errors))


def main():
    app = QApplication(sys.argv)
    window = DirectoryComparisonApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
