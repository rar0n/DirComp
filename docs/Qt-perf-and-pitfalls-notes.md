# DirComp: performance tweaks and Qt pitfalls (reference for later apps)

Stack: PySide6 6.11, Python 3.10, Linux Mint 21.3. Most items come from comments in `listing_ops.py`, `selection_ops.py`, `pane_builder.py` and `theme_ops.py`.

## 1. Big trees (QTreeWidget) - speed

**Hide the tree during any bulk work.** `setUpdatesEnabled(False)` is not enough. While the widget is visible, `QTreeWidgetItem.setData()`, `tree.clear()`, `setExpanded()` and selection changes do per-row bookkeeping that `setUpdatesEnabled(False)` does not suppress. The pattern:
```python
was_visible = tree.isVisible()
if was_visible: tree.setVisible(False)
tree.setUpdatesEnabled(False)
try:
    ...bulk work...
finally:
    tree.setUpdatesEnabled(True)
    if was_visible: tree.setVisible(True)
```
Used for: full listing rebuild, expand/collapse all, Select All/Matching/Invert, and restoring expanded folders after a rebuild.

**Lazy population.** Folders get a placeholder child (the expand arrow) and are populated on expand. In bulk expand, populate directly and untracked instead of relying on `setExpanded(True)` to trigger it.

**Guard flags so per-item listeners stand down during a bulk operation:**
- `pane._bulk_expand_pending`: expanded/collapsed listeners (type counts, timeline recompute) would each do an O(visible items) walk per signal, which is O(n²) over a big tree. Skip them during the bulk op, then do one refresh at the end.
- `_bulk_select_guard`: same idea for selection-changed handlers during Select All and similar.
- `_sync_guard`: prevents ping-pong between panes when "Synchronize Views" mirrors selection, expansion or scroll.

**Throttle expensive derived views.** The statistics timeline refresh is throttled during bulk work (`_maybe_refresh_pane_timeline_throttled`). One unthrottled catch-up call runs at the end.

**Don't rebuild what didn't change.**
- `retag_only`: if the directory contents are identical and only the diff status changed, re-color and re-tag the existing items in place. It skips `_build_entries` and the tree teardown. It reads the data roles already stored on the items (name, IS_DIR_ROLE, etc.). One trap: a pane whose own contents didn't change still needs its tags refreshed when the other pane changed, otherwise the change is silently invisible.
- Reuse the snapshot: filters work from the cached snapshot (`refresh_snapshot=False`) instead of rescanning the disk.
- Non-recursive load is fast because it lists a single directory and defers subfolders.

**Progress and cancel.** `_CancellableProgress` has `tick()`, which returns False on cancel, plus `pump()` (processEvents, throttled) and `flush_to_max()`. Tick from inside the loops. Use one shared active token so nested operations don't stack dialogs. Long copy/paste enumerates once, then confirms, then works.

**Avoid `QTreeWidgetItemIterator` after rebuilds.** It misbehaved on trees that were rebuilt or hidden, so a plain visible-items walker (`_iter_visible_items`) replaced it.

## 1b. Read the filesystem once: the in-memory snapshot (biggest single win)

`diff_engine.py` / `_build_snapshot`: on load, Refresh, or after a transfer, walk the folder **once** (one `os.scandir` per folder) into a nested-dict mirror. Every node holds name, full_path, is_dir, mtime, ctime, atime, size and children. Directory sizes are summed bottom-up during that same pass. A flat `{normpath: node}` index gives O(1) lookup by path (`_snapshot_lookup`).

Everything that used to touch the disk now reads the dicts instead:
- diff tagging (`_diff_tag`), folder content ranking, sizes and counts (`_path_size`, `_count_all`);
- all Select-group searches;
- the statistics/timeline data (`statistics_ops.py`);
- folder expand: children come from the snapshot, not a fresh listing.

So selections, expand/collapse, filters and metric changes never re-read the disk. Only load, Refresh and file operations rebuild the snapshot. Related rules:
- **Retag instead of rescan.** If nothing on disk changed (a metric change or a filter toggle, say), retag the existing items from the snapshot (`retag_only`).
- **Memoize recursive folder comparison.** `_dir_content_rank` is cached by `(dir_a, dir_b, date_field)`. Computing a parent already fills the cache for every child, so each child row is O(1) later. Without this, Select All took over a minute on about 5000 items, because every nested row redid the same subtree walk. Clear the cache whenever a snapshot is rebuilt (`_invalidate_dir_rank_cache`). Include every input that changes the answer in the key.
- **Use an explicit stack, not recursive generators**, for flattening deep trees (`_flatten_files`, `_flatten_folders`). A deep `yield from` chain can exhaust the C stack on top of Qt's own dispatch depth. Recursion is fine for the snapshot build itself.
- **Rank ties need a real tie-break.** Two different folder rankings that "agree" from both sides gave tags that don't exist (`diff_date_larger_dir`), and the row got no background at all. That was the "black folders" bug. Always map every rank/direction combination to a tag that exists.
- **Recursive mode reads the snapshot; non-recursive mode reads only what's visible.** Counts and the timeline follow that scope. That is why non-recursive load is fast: it shows one directory and defers the rest.

## 1c. Progress helper details (`progress.py`)
- **Pump the event loop by time (about 50 ms), not by item count.** A run of slow items otherwise gives no repaint and no chance for Cancel.
- **Refresh the text label much less often (about 1 s).** The bar itself updates every tick, but label repaints are the costly part. Always show the first tick.
- `total=None` gives busy mode (range 0,0). Flip a dynamic QSS property and do unpolish/polish so busy and determinate bars look different.
- `pump()` keeps the UI alive without counting. Use it for ancestor-only traversal so the bar hits 100% only on real matches.
- `start_phase(total)` reuses one token across phases (enumerate, then copy), carrying cancellation over but resetting the count.
- `flush_to_max()` snaps the bar to full (with one `processEvents`) before it collapses, when the total was an estimate.

## 1d. Custom-widget notes (`custom_widgets.py`)
- Scrollbar handle magnifier: the row-to-pixel mapping is a **closed-form integral** of a cosine density, not a per-row loop. An O(total_rows) scan per repaint would kill huge trees. Do this in any custom paint over large data.
- Selected-row delegate reads its tint settings on every paint, so setting changes need no rebuild.
- Spin boxes set under `blockSignals()` leave signal-driven sliders stale. Override `setValue/setRange/...` in Python and notify from there, as well as listening to `valueChanged`.
- Reversing the wheel direction: `QWheelEvent` fields are read-only. Swallow it, build a negated copy, find the widget under the cursor with `QApplication.widgetAt`, and walk up via `parentWidget()` until something accepts. `sendEvent` does not run Qt's native bubbling, and the event target can be the `QWindow`. Use a `_resending` guard against loops.

## 1e. Snapshot-era checklist for a new app
1. Scan once, store plain dicts, and give every later feature a dict lookup.
2. Rebuild the snapshot only on load, refresh or mutation.
3. Cache derived results and invalidate them with the snapshot.
4. Keep widgets thin: rows are built lazily from the snapshot.

## 2. The hard segfaults (tree teardown)

Symptom: a hard crash, with no Python exception, when navigating or refreshing. It was intermittent, and worse with deeply auto-expanded trees and with a live selection.

Causes and fixes, in the order they were found:
1. **Deep recursive destructor chain** when `tree.clear()` destroys a fully expanded, deep tree. Fix: `_detach_all_children(tree)` runs before `clear()`. It has two passes. (a) Collect every item with a read-only traversal, parents before descendants. (b) Call `takeChildren()` on each item in **reverse** order (bottom-up), so no call ever has an intact subtree beneath it. Top-down just moved the deep recursion one level in.
2. **Live selection or current item at teardown.** Both real crashes were on the pane that still had a selection. The `QItemSelectionModel` is a separate object that `tree.blockSignals(True)` does not cover. Fix: capture the selected paths first (to restore later), then `tree.setCurrentItem(None)` and `tree.clearSelection()`, then detach, then `clear()`. "Current" is tracked separately from "selected", so both calls are needed.
3. **Signals from other objects.** `tree.blockSignals(True)` covers only the tree's own signals. The vertical scrollbar's `valueChanged` re-entered `_on_scroll_sync` mid-rebuild and crashed on a half-built tree. Fix: `blockSignals` on the scrollbar too. Restore both in `finally`.
4. **Reentrancy.** A second `update_listing` call for the same tree while the first was mid-rebuild, for example from a queued timer or a slot that ran synchronously. Fix: `_tree_rebuild_pending` flag, and a skip of the reentrant call.
5. **Don't force-delete items with `shiboken6.delete()`.** It seemed logical, but it caused a worse, non-deterministic crash after several filter cycles. Something else still held references, so a dangling access became a use-after-free. Leave the detached items to normal refcounting.
6. **Orphaned Qt objects** (widgets created without a parent, then dropped) are a known PySide6/shiboken crash trap (see the comment near `pane_builder.py:907`). Always parent them.
7. **Virtual methods and paint events.** Don't do heavy work or mutate the tree inside them. Defer with `QTimer.singleShot(0, ...)`.

Also: restore the scroll position after a rebuild (`scroll_value` captured beforehand), or the viewport jumps.

## 3. Theming and layout quirks

- Force the **Fusion** style, otherwise native styles ignore palette and stylesheet colors.
- `QPalette` changes are not propagated synchronously to children. Compute a `_theme_window_color` yourself instead of reading it back from the widget.
- Per-theme color sets: `_builtin_color_set`, `_capture_color_set`, `_load_color_set_into_live`. Each color key must be added in every one of those plus the bulk-refresh call, or it leaks between themes.
- **QGridLayout spreads stretch** across all rows and columns. Box the grid in a QHBoxLayout with a trailing stretch to keep the buttons compact.
- Implicit versus explicit hide in tab pages: a widget hidden explicitly stays hidden when its parent is shown, so track your own visibility state.
- Narrow and wide layouts are built by re-gridding the same widgets (`responsive_layout.py`), not by rebuilding them. Remember the wide-mode margins so you can restore them.

## 4. Linux file-manager features

- Clipboard interop: MIME type `x-special/gnome-copied-files` (`copy\n` or `cut\n` plus `file://` URIs) plus `text/uri-list`.
- Open with: parse `mimeinfo.cache` and the `.desktop` files, and expand `%f/%F/%u/%U` in `Exec`.
- Properties: `gdbus call ... org.freedesktop.FileManager1.ShowItemProperties`, with a built-in dialog as fallback.
- Windows equivalents (shell verbs for Open with and Properties) are written but untested.

## 5. General lessons

- Fix root causes and check whether an "improvement" (force delete, top-down detach) just moves the crash.
- Any tree teardown or rebuild needs: block signals on every related object, clear selection and current item, detach bottom-up, and use a reentrancy flag.
- Any bulk operation needs: hide, disable updates, use guard flags, throttle derived views, refresh once at the end.

## Also noted from the remaining files
- `statistics_ops.py`: `_on_tree_expand_collapse_for_timeline` skips work when recursive, or when `_tree_rebuild_pending` or `_bulk_expand_pending` is set. `_maybe_refresh_pane_timeline_throttled` (0.2 s) is used inside bulk walks, with one unthrottled refresh at the end. `_sync_timeline_selection` skips under `_bulk_select_guard` and during rebuilds. `_scroll_tree_to_first_selected` uses `_iter_visible_items`. `QTreeWidgetItemIterator` crashed reliably when a tree was rebuilt right after the search. `scrollToItem` silently expands collapsed ancestors, so only scroll to rows that are already visible.
- `binning.py`: pure-float bin-axis strategies with no file or Qt knowledge, so it is reusable. Bins snap to calendar or decade grids so panning never changes bin width. It has a log mode for size.
- `panes.py`: a plain state bag per pane (no Qt parent tree). Keep per-pane state there, so a cosmetic UI rebuild doesn't lose it.
