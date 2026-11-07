"""
DiffEngineMixin: the pure comparison logic that decides how any two items
(a file, or a whole folder's recursive contents) differ -- tags, ranks,
sizes, dates. No Qt widgets are touched here.

Everything in this file now reads from each pane's in-memory snapshot
(see _build_snapshot / ListingMixin.update_listing) instead of hitting
the filesystem directly. The snapshot is a plain nested-dict mirror of a
pane's directory tree, taken once per load/Refresh/post-transfer
update_listings() call -- the same point that already rebuilds both
trees from scratch -- so every comparison/search that used to re-stat or
re-walk the disk on every single call (once per Select-group button
press, once per folder expand, redundantly at every level of a
recursive folder comparison) now just reads a dict. A directory's total
size is summed bottom-up once while the snapshot is built, rather than
being recomputed with a fresh os.walk every time it's needed.

A future change to how dates are compared/typed, or to what counts as
"the same" between two entries, still belongs in this file.
"""

import os
import datetime


class DiffEngineMixin:
    # ------------------------------------------------------------ Snapshot

    def _build_snapshot(self, directory, progress=None):
        """Take a full recursive metadata snapshot of `directory`: one
        os.scandir pass per folder, at every depth, done once up front
        instead of being repeated by every comparison/search that
        touches it afterward. Returns (root_node, index):

        - root_node is `directory`'s own node (None if it can't be read).
        - index is a flat {normalized_path: node} dict covering the root
          and everything under it, so anything elsewhere in the app that
          has a path string (not a node reference) can look one up in
          O(1) via _snapshot_lookup, without needing to walk down from
          the root by hand.

        Each node: {"name", "full_path", "is_dir", "mtime", "ctime",
        "atime", "size", "children"}. For a file, "size" is its own
        byte count and "children" is None. For a directory, "children"
        is a {name: node} dict of its immediate entries and "size" is
        the recursive total of everything underneath -- summed bottom-up
        as the recursion unwinds, so it costs one pass over the whole
        subtree total, not one pass per directory the way the old
        per-call _path_size (a fresh os.walk every time) did.

        `progress`, if given, is ticked once per entry visited, same
        reasoning as the old per-listing ticking -- a folder with many/
        large subfolders is exactly the case that needs the event loop
        pumped and Cancel checked regularly."""
        index = {}
        root_node = self._snapshot_node(directory, index, progress)
        return root_node, index

    def _snapshot_node(self, path, index, progress=None):
        norm = os.path.normpath(path)
        try:
            st = os.stat(path)
        except OSError:
            return None
        is_dir = os.path.isdir(path)
        node = {
            "name": os.path.basename(path.rstrip(os.sep)) or path,
            "full_path": path, "is_dir": is_dir,
            "mtime": st.st_mtime, "ctime": st.st_ctime, "atime": st.st_atime,
            "size": 0, "children": None,
        }
        index[norm] = node
        if not is_dir:
            node["size"] = st.st_size
            return node

        try:
            with os.scandir(path) as it:
                raw_entries = list(it)
        except OSError:
            node["children"] = {}
            return node

        children = {}
        total_size = 0
        for entry in raw_entries:
            if progress is not None and not progress.tick():
                break
            child = self._snapshot_node(entry.path, index, progress)
            if child is None:
                continue
            children[entry.name] = child
            total_size += child["size"]
        node["children"] = children
        node["size"] = total_size
        return node

    def _snapshot_lookup(self, path):
        """O(1) lookup of a path's snapshot node, checking both panes'
        indexes (a caller doesn't generally know -- or need to know --
        which side `path` belongs to; it's always one or the other, or
        genuinely absent from both, which correctly reads as "missing"
        the same way a failed os.stat used to)."""
        if not path:
            return None
        norm = os.path.normpath(path)
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            index = getattr(pane, "snapshot_index", None) if pane else None
            if index and norm in index:
                return index[norm]
        return None

    # ------------------------------------------------------------- Compare

    def _diff_tag(self, name, is_dir, size_str, date_str, other_dir, full_path=None, date_field="mtime"):
        """Work out the color tag for one entry by comparing it against its
        counterpart (same relative path) in `other_dir`, if any, via the
        snapshot -- see module docstring. Works at any nesting depth
        regardless of whether the other side is currently expanded on
        screen, same as before; `other_dir` being a path that doesn't
        exist just means its snapshot lookup comes back empty, which
        still correctly cascades "missing" to everything underneath.

        `date_field` -- one of "mtime"/"ctime"/"atime" -- is which
        snapshot date this entry's Newer/Older comparison actually
        uses, tied to the entry's own pane's Statistics-view Metric
        (see TimelineOpsMixin._pane_diff_date_field, the only real
        caller of this parameter's non-default value). `date_str` must
        already be formatted from that same field -- callers are
        responsible for picking the matching formatted string (see
        listing_ops._make_child_items/_retag_listing_tree) so the two
        stay in sync; this method just compares whichever field it's
        told to.

        For a directory that *does* exist on both sides: if "Recursive
        Folder Contents" is on, `full_path` is used to recursively compare
        its actual contents (see _dir_content_rank), even if it's never
        been expanded in the UI, and the result is still one of the usual
        common/diff_count/diff_date tags -- never "missing_dir", which stays
        reserved for a folder that doesn't exist on the other side at all.
        If Recursive Folder Contents is off, only the folder's own
        immediate item count and date are compared -- the original,
        cheaper behavior."""
        if other_dir is None:
            return "directory" if is_dir else "file"

        other_full = os.path.join(other_dir, name)
        other_node = self._snapshot_lookup(other_full)
        if other_node is None:
            return "missing_dir" if is_dir else "missing_file"

        if other_node["is_dir"] != is_dir:
            return "missing_dir" if is_dir else "missing_file"

        if is_dir and full_path and self._recursive_folder_contents_enabled():
            rank, direction = self._dir_content_rank(full_path, other_full, date_field)
            return self._tag_from_rank(rank, direction, is_dir=True)

        size_direction, date_direction = self._diff_axes_vs_node(
            other_node, is_dir, size_str, date_str, date_field)

        if size_direction is None and date_direction is None:
            return "common_dir" if is_dir else "common_file"

        # Priority when an item differs in more than one way at once (e.g.
        # both larger AND older than its counterpart): Newer/Older wins
        # over Smaller/Larger -- see _DIFF_RANK/_file_diff_rank, which
        # apply the same priority to the recursive folder-content
        # comparison below. This only decides the single *tag* (i.e. the
        # row color); the Select group's Smaller/Larger/Newer/Older
        # buttons ask _diff_axes for both differences instead, so an item
        # that's newer AND larger is found by either button.
        if date_direction is not None:
            return f"diff_date_{date_direction}_dir" if is_dir else f"diff_date_{date_direction}_file"
        return f"diff_count_{size_direction}_dir" if is_dir else f"diff_size_{size_direction}_file"

    def _diff_axes(self, is_dir, size_str, date_str, other_full, date_field="mtime"):
        """Both ways an entry differs from its counterpart at `other_full`
        -- (size_direction, date_direction), each "larger"/"smaller" and
        "newer"/"older" respectively, or None where that attribute
        agrees. None overall if there's no comparable counterpart
        (nothing there, or a file on one side and a folder on the
        other -- i.e. what _diff_tag calls "missing").

        Unlike _diff_tag, which has to boil an entry down to one tag and
        lets the date difference win, this keeps both -- it's what the
        Select group's Smaller/Larger/Newer/Older buttons (and the "Total
        items" breakdown) classify by, so a file that's both newer and
        larger counts for both. Same inputs and same comparison as
        _diff_tag's own non-recursive branch (see _diff_axes_vs_node),
        so the two can never disagree about whether something differs.

        For a directory this is its *immediate* item count and date --
        the non-recursive comparison -- regardless of Recursive Folder
        Contents; aggregate folder differences are Diff Folders' job."""
        other_node = self._snapshot_lookup(other_full) if other_full else None
        if other_node is None or other_node["is_dir"] != is_dir:
            return None
        return self._diff_axes_vs_node(other_node, is_dir, size_str, date_str, date_field)

    def _diff_axes_vs_node(self, other_node, is_dir, size_str, date_str, date_field="mtime"):
        """The actual comparison behind _diff_axes/_diff_tag, against an
        already-looked-up counterpart node. Both sides are compared as
        the same formatted strings the listing shows -- dates to the
        whole second, sizes as format_size's text for a file or "N items"
        for a folder -- so what counts as "different" matches what's on
        screen."""
        other_date_str = datetime.datetime.fromtimestamp(other_node[date_field]).strftime("%Y-%m-%d %H:%M:%S")
        if is_dir:
            other_size_str = f"{len(other_node['children'] or {})} items"
        else:
            other_size_str = self.format_size(other_node["size"])

        date_direction = None
        if date_str != other_date_str:
            date_direction = "newer" if date_str > other_date_str else "older"
        size_direction = None
        if size_str != other_size_str:
            size_direction = "larger" if self.compare_size(size_str, other_size_str) > 0 else "smaller"
        return size_direction, date_direction

    def _tag_from_rank(self, rank, direction, is_dir):
        suffix = "dir" if is_dir else "file"
        if rank <= 0:
            return f"common_{suffix}"
        if rank == 2:
            return f"diff_date_{direction}_{suffix}"
        return f"diff_count_{direction}_dir" if is_dir else f"diff_size_{direction}_file"

    def _file_diff_rank(self, node_a, node_b, date_field="mtime"):
        """Rank/direction (see _DIFF_RANK) for one file against its
        counterpart, used while walking a folder's contents recursively
        (see _dir_content_rank). Both arguments are snapshot nodes now,
        not (entry dict, path) -- the whole recursive comparison stays
        in snapshot-node terms throughout, rather than dropping back to
        a path string (and a fresh disk lookup) at the leaves. Sizes
        compare exactly (raw bytes); dates compare truncated to whole
        seconds, matching the granularity _diff_tag's formatted
        date strings already used.

        `date_field` -- see _diff_tag -- is which of mtime/ctime/atime
        this comparison uses; defaults to mtime for any caller that
        hasn't been threaded through with a pane-specific one yet.

        Date takes priority over size when a file differs in both at
        once (rank 2, same as a lone date difference at the top of
        _diff_tag) -- only falls back to the size comparison (rank 1)
        once the (truncated) dates actually agree. See _DIFF_RANK for
        the same priority applied to _diff_tag's own top-level compare."""
        same_size = node_a["size"] == node_b["size"]
        same_date = int(node_a[date_field]) == int(node_b[date_field])
        if same_size and same_date:
            return 0, None
        if not same_date:
            return 2, ("newer" if node_a[date_field] > node_b[date_field] else "older")
        return 1, ("larger" if node_a["size"] > node_b["size"] else "smaller")

    def _count_all(self, directory):
        node = self._snapshot_lookup(directory)
        if node is not None:
            return self._count_all_snap(node)
        # Fallback for a path that isn't in either pane's current
        # snapshot (e.g. queried before a listing's ever been loaded) --
        # same real-disk logic as before.
        total = 0
        for _root, dirs, files in os.walk(directory):
            total += len(dirs) + len(files)
        return total

    def _count_all_snap(self, node):
        children = node["children"] or {}
        total = len(children)
        for child in children.values():
            if child["is_dir"]:
                total += self._count_all_snap(child)
        return total

    def _count_direction(self, dir_a, dir_b):
        """Fallback direction for an aggregate difference that's caused by
        content missing entirely on one side (rather than a same-named file
        differing), or by worst-rank children that disagree on direction:
        compares total recursive item counts, mirroring what "diff_count"
        already means for a folder's own immediate listing. Falls back
        further to total recursive size on an exact item-count tie, and
        only then to a fixed default -- otherwise a tie evaluated from
        each side would inconsistently report both as "larger" than the
        other (since a naive ">=" is true from both directions at once)."""
        count_a, count_b = self._count_all(dir_a), self._count_all(dir_b)
        if count_a != count_b:
            return "larger" if count_a > count_b else "smaller"
        size_a, size_b = self._path_size(dir_a), self._path_size(dir_b)
        if size_a != size_b:
            return "larger" if size_a > size_b else "smaller"
        return "larger"

    def _invalidate_dir_rank_cache(self):
        """Clear the memoized _dir_content_rank results -- call whenever
        either pane's snapshot is rebuilt (see
        ListingMixin._refresh_pane_snapshot). A cached rank/direction from
        before a Refresh, or from a folder that was just opened over a
        previous one, no longer reflects what's actually on disk/in the
        new snapshot, so it has to be thrown away wholesale rather than
        picked through entry by entry."""
        self._dir_rank_cache = {}

    def _dir_content_rank(self, dir_a, dir_b, date_field="mtime"):
        """Memoized wrapper around _dir_content_rank_uncached.

        Without this, _diff_tag calls _dir_content_rank once per
        directory row as it's built -- and _dir_content_rank_uncached
        recurses into every subfolder to work out that row's aggregate
        color. That's one full walk of the row's subtree, which is fine
        happening once. The problem is every one of those subfolders
        then gets its *own* row built too (as it's populated, lazily or
        via a whole-subtree selection like Select All/Matching), and
        that row's _diff_tag call repeats the exact same walk of the
        exact same subtree from scratch -- once per level of nesting,
        for every folder, all the way down. On a tree with real depth
        that's the dominant cost (this is what made Select All take
        over a minute on ~5000 items), not the selecting itself.

        Caching by (dir_a, dir_b, date_field) fixes this for free:
        computing a parent's rank already recurses through
        self._dir_content_rank for every child subfolder (see below),
        which populates this cache for each of them along the way -- so
        by the time a child folder's own row actually gets built and
        asks for its rank, the answer is already sitting here from when
        its parent computed it, O(1) instead of another full subtree
        walk. date_field is part of the key (not just dir_a/dir_b) so a
        pane whose Statistics-view Metric changes doesn't serve a stale
        rank computed under the old date field -- without it, this
        would need its own separate invalidation on every Metric
        change instead of just aging out of relevance on the next
        snapshot rebuild the same way everything else here does. Cleared
        wholesale whenever a snapshot is rebuilt (see
        _invalidate_dir_rank_cache) since a stale cached rank would
        otherwise silently outlive the disk state it was computed
        from."""
        cache = getattr(self, "_dir_rank_cache", None)
        if cache is None:
            cache = self._dir_rank_cache = {}
        key = (os.path.normpath(dir_a) if dir_a else None,
               os.path.normpath(dir_b) if dir_b else None,
               date_field)
        if key in cache:
            return cache[key]
        result = self._dir_content_rank_uncached(dir_a, dir_b, date_field)
        cache[key] = result
        return result

    def _dir_content_rank_uncached(self, dir_a, dir_b, date_field="mtime"):
        """Recursively compare dir_a's (filter-respecting) contents against
        dir_b by relative path -- regardless of whether either side has
        ever been expanded in the UI. Returns (rank, direction) using the
        same 0/1/2 scale as a single file (see _DIFF_RANK): 0 everything
        matches, 1 a size/count differs, 2 a date differs -- content
        that's missing entirely on one side outranks both (see the
        internal rank-3 handling below), with the direction decided by
        total recursive item count (see _count_direction), so a folder's
        aggregate tag always stays one of the ordinary common/diff_count/
        diff_date tags. direction is whichever one goes with the
        worst-case entry found.

        Now a pure in-memory walk of the two snapshot subtrees -- no
        _build_entries/disk calls at all, so a folder with many/deep
        subfolders costs the same whether this is called once or a
        thousand times in the same session, instead of a fresh recursive
        filesystem walk every single call (which is what used to make
        Recursive Folder Contents noticeably slower to populate on a big
        tree, and made every Select-group search re-pay that same cost
        again).

        Rank scale here mirrors _file_diff_rank's own 0/1/2 (0 matches, 1
        a size/count differs, 2 a date differs, date taking priority over
        size when a single child differs in both). Content missing
        entirely on one side is treated as worse than either -- forced up
        to an internal rank 3, past both -- so it still wins out over a
        sibling that merely differs by date, and (since _tag_from_rank's
        only special case is rank==2) still surfaces as the usual
        diff_count tag rather than diff_date."""
        node_a = self._snapshot_lookup(dir_a)
        if node_a is None:
            # Nothing in the snapshot to compare against -- same situation
            # as content missing on one side, so same internal rank 3 (and
            # so the same diff_count_* tag). This used to return rank 2
            # paired with a count-based larger/smaller direction, which
            # _tag_from_rank turned into "diff_date_larger_dir" -- a tag
            # that doesn't exist (see the rank-2 direction handling at the
            # bottom of this method).
            return 3, self._count_direction(dir_a, dir_b)
        node_b = self._snapshot_lookup(dir_b)

        children_a = node_a["children"] or {}
        children_b = (node_b["children"] or {}) if node_b is not None else {}

        # Track every direction seen at the current worst rank -- and how
        # many children voted for each -- not just the first one
        # encountered: see _tag_from_rank's callers. A folder whose
        # worst-rank children disagreed on direction (some "smaller",
        # some "larger", say) needs a tie-break, not a first-wins pick.
        # date_field is threaded through every recursive call below (both
        # the folder and file branches) -- it used to be accepted here but
        # never actually passed on, so a nested comparison silently fell
        # back to mtime regardless of the pane's Statistics-view Metric.
        best_rank = 0
        best_directions = {}
        saw_missing = False
        for name, child_a in children_a.items():
            child_b = children_b.get(name)
            if child_b is None or child_b["is_dir"] != child_a["is_dir"]:
                saw_missing = True
                continue
            if child_a["is_dir"]:
                rank, direction = self._dir_content_rank(child_a["full_path"], child_b["full_path"], date_field)
            else:
                rank, direction = self._file_diff_rank(child_a, child_b, date_field)
            if rank > best_rank:
                best_rank = rank
                best_directions = {direction: 1} if direction else {}
            elif rank == best_rank and rank > 0 and direction:
                best_directions[direction] = best_directions.get(direction, 0) + 1

        # Symmetric check: dir_b may hold items dir_a simply doesn't have.
        if set(children_b) - set(children_a):
            saw_missing = True

        if saw_missing and best_rank < 3:
            best_rank = 3
            best_directions = {}

        if best_rank == 0:
            return 0, None
        if len(best_directions) == 1:
            return best_rank, next(iter(best_directions))
        if best_rank == 2:
            # A *date* difference needs a newer/older direction -- see
            # _tag_from_rank, which builds "diff_date_{direction}_dir"
            # from it. Falling through to _count_direction here (which
            # only ever answers larger/smaller) produced tags like
            # "diff_date_larger_dir" whenever a folder held both newer
            # and older files (or subfolders): no such tag exists in
            # TAG_COLORS, so the row got no background at all, and the
            # bad direction then propagated up through every ancestor
            # folder (each one saw a single, "agreeing" direction).
            return best_rank, self._date_direction(best_directions, node_a, node_b, date_field)
        return best_rank, self._count_direction(dir_a, dir_b)

    def _date_direction(self, votes, node_a, node_b, date_field="mtime"):
        """Tie-break newer/older for a folder whose worst-rank (date-
        differing) children disagree on direction -- the date-side
        counterpart of _count_direction, which only speaks larger/smaller
        and so can't be used for a rank-2 result. `votes` is the
        {direction: child count} tally from _dir_content_rank_uncached.
        Majority wins; on an exact tie, the two folders' own dates decide;
        only if those tie too does it fall back to a fixed default.
        Deterministic, and flips correctly when called from the other
        side (the votes and the folder dates both swap). With Recursive
        Folder Contents on, every differing folder paints as the same
        "Diff folder" color regardless, so this only has to be a valid,
        stable label, not a perfect one."""
        newer, older = votes.get("newer", 0), votes.get("older", 0)
        if newer != older:
            return "newer" if newer > older else "older"
        if node_a is not None and node_b is not None and node_a[date_field] != node_b[date_field]:
            return "newer" if node_a[date_field] > node_b[date_field] else "older"
        return "newer"

    def _path_size(self, full_path):
        """Size in bytes: direct for a file, recursive total for a
        directory -- an O(1) lookup into the snapshot (summed bottom-up
        once when the snapshot was built) instead of a fresh recursive
        os.walk every call."""
        node = self._snapshot_lookup(full_path)
        if node is not None:
            return node["size"]
        # Fallback for a path outside either pane's current snapshot.
        if os.path.isdir(full_path):
            total = 0
            for root, _dirs, files in os.walk(full_path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
        try:
            return os.path.getsize(full_path)
        except OSError:
            return 0

    def compare_size(self, size1, size2):
        def parse_size(size):
            if size.endswith("items"):
                return int(size.split()[0])
            value, unit = size.split()
            value = float(value)
            units = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
            return value * units[unit]

        return parse_size(size1) - parse_size(size2)

    def format_size(self, size):
        for unit in [" B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} TB"
