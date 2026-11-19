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

from constants import CMP_NAME_SIZE_DATE, CMP_HASH, CMP_FINGERPRINT

# Sentinel results of DiffEngineMixin._diff_axes for a file the Smaller/
# Larger/Newer/Older axes can't describe: hash comparison with no hash
# yet, or same-name files whose content differs while size and date agree.
# Neither is a (size, date) direction pair, so SelectionMixin._attrs_from_axes
# drops them; the count code checks for them by identity.
AXES_UNHASHED = ("unhashed", "unhashed")
AXES_CONTENT_DIFFERS = ("content", "content")
# A file that "Ignore empty files" / "Ignore hard links" leaves out of
# every comparison: neither missing nor matching nor different.
AXES_IGNORED = ("ignored", "ignored")


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
            # Link count: "Ignore hard links" (see _ignored_kind).
            node["nlink"] = getattr(st, "st_nlink", 1)
            self._attach_cached_hash(node, norm, st)
            return node

        try:
            with os.scandir(path) as it:
                raw_entries = list(it)
        except OSError:
            # Unreadable folder: its (unknown) contents must not be taken
            # for "deleted" when the persistent hash cache is pruned.
            node["children"] = {}
            node["scan_failed"] = True
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

    # ------------------------------------------------- Comparison settings

    def _ignore_extensions_active(self) -> bool:
        """True when files are matched by name without extension (Settings
        tab -> Global Options -> "Ignore file extensions"). Only the
        name-based comparison type has this option; the hash types ignore
        the checkbox even if it was left checked."""
        return self._stem_mode() != 0

    def _stem_mode(self) -> int:
        """How file names are matched in the name-based comparison type:
        0 = full name (plus size and date), 1 = name without extension
        ("Ignore file extensions"), 2 = only the part before the first dot
        ("Only first part of file name"; handy for names with several
        extensions). Modes 1 and 2 ignore size and dates, and exist only
        for the name-based type."""
        if getattr(self, "_cmp_type", CMP_NAME_SIZE_DATE) != CMP_NAME_SIZE_DATE:
            return 0
        if getattr(self, "_cmp_first_name", False):
            return 2
        return 1 if getattr(self, "_cmp_ignore_ext", False) else 0

    def _name_stem(self, name: str) -> str:
        """`name` as compared in the current stem mode (see _stem_mode).
        A leading dot is part of the name, not a separator (".bashrc")."""
        if self._stem_mode() == 2:
            dot = name.find(".", 1)
            return name if dot < 0 else name[:dot]
        return os.path.splitext(name)[0]

    def _comparison_key(self) -> tuple:
        """Everything about the comparison settings that can change a
        diff result, as one hashable value: part of _dir_content_rank's
        cache key, and what the settings handler compares to decide
        whether a changed control needs the panes re-tagged at all.

        Hash value and Fingerprint have no hashes to compare yet, so for
        now they reduce to the same key (and the same behavior) as the
        plain name/size/date type; once hashing exists they get their
        own entries here, with the algorithm and chunk sizes."""
        ctype = getattr(self, "_cmp_type", CMP_NAME_SIZE_DATE)
        if ctype == CMP_HASH:
            return (CMP_HASH, getattr(self, "_cmp_algorithm", 2))
        if ctype == CMP_FINGERPRINT:
            return (CMP_FINGERPRINT, getattr(self, "_cmp_algorithm", 2),
                    getattr(self, "_cmp_chunk_start_kb", 64), getattr(self, "_cmp_chunk_end_kb", 64))
        return (CMP_NAME_SIZE_DATE, self._stem_mode())

    def _hash_type_selected(self) -> bool:
        """The Settings tab's comparison type is Hash value / Fingerprint."""
        return getattr(self, "_cmp_type", CMP_NAME_SIZE_DATE) in (CMP_HASH, CMP_FINGERPRINT)

    # ------------------------------------------------ Ignored files

    def _ignore_flags(self) -> tuple:
        """(Ignore empty files, Ignore hard links) as set in the Settings
        tab -- part of every comparison cache key that must not outlive a
        change of either (they are NOT part of _comparison_key: that one
        also keys the hashes, which these options do not affect)."""
        return (bool(getattr(self, "_ignore_empty", False)),
                bool(getattr(self, "_ignore_hardlinks", False)))

    def _ignored_kind(self, node):
        """Why a snapshot node takes no part in any comparison: "hardlink"
        (Ignore hard links on, and the file has more than one link),
        "empty" (Ignore empty files on, and it is 0 bytes) or None. Only
        files are ever ignored; a node without a link count (an old
        snapshot) counts as having one link."""
        if node is None or node["is_dir"]:
            return None
        if getattr(self, "_ignore_hardlinks", False) and node.get("nlink", 1) > 1:
            return "hardlink"
        if getattr(self, "_ignore_empty", False) and node["size"] == 0:
            return "empty"
        return None

    def _ignored_file_tag(self, full_path):
        """The tag an ignored file shows -- "hardlink_file" for a hard
        link, the plain "file" for an empty one -- or None if the file at
        `full_path` is not ignored."""
        if not (getattr(self, "_ignore_empty", False) or getattr(self, "_ignore_hardlinks", False)):
            return None
        kind = self._ignored_kind(self._snapshot_lookup(full_path))
        if kind == "hardlink":
            return "hardlink_file"
        return "file" if kind else None

    def _compared_children(self, node):
        """A folder node's children without the ignored files."""
        children = node["children"] or {}
        if not any(self._ignore_flags()):
            return children
        return {n: c for n, c in children.items() if not self._ignored_kind(c)}

    def _hash_comparison_blocked(self) -> bool:
        """A hash type is selected but a loaded snapshot's hashes don't fit
        the current settings: nothing is compared; every item shows the
        "Hashed, not compared" look instead."""
        return self._hash_type_selected() and not self._snapshot_hashes_ok()

    def _hash_mode_active(self) -> bool:
        """Files are actually compared by hash: a hash type is selected and
        every loaded snapshot has hashes made under the current settings.
        Otherwise the comparison falls back to name, size and date."""
        return self._hash_type_selected() and self._snapshot_hashes_ok()

    def _node_hash(self, node):
        """The digest stored on a file node, if it was computed under the
        comparison settings in effect now (type, algorithm, chunk sizes);
        None = unhashed."""
        stored = node.get("hash")
        if stored and stored[0] == self._comparison_key():
            return stored[1]
        return None

    def _attach_cached_hash(self, node, norm_path, st=None):
        """Called while a snapshot is built: give a file node back its
        hash for the comparison key in effect now from the app-wide cache
        (self._hash_cache {normpath: (size, int mtime, {key: digest})},
        see HashOpsMixin), if the file's size and mtime are still what
        they were when it was hashed. Looked up by path first; failing
        that by (device, inode, size, mtime), so a file that was only
        renamed or moved within its filesystem keeps its hash. Switching
        the comparison key later re-attaches without a rescan:
        HashOpsMixin._reattach_cached_hashes."""
        cache = getattr(self, "_hash_cache", None)
        if not cache:
            return
        key = self._comparison_key()
        entry = cache.get(norm_path)
        if entry and entry[0] == node["size"] and entry[1] == int(node["mtime"]):
            digest = entry[2].get(key)
            if digest is not None:
                node["hash"] = (key, digest)
                return
        elif entry:
            # Cached, but the file has changed since: "Changed since cached"
            # (unless the inode lookup below recognizes it as a moved file).
            node["changed"] = True
        if st is not None:
            keys = getattr(self, "_hash_cache_by_inode", {}).get(
                (st.st_dev, st.st_ino, st.st_size, int(st.st_mtime)))
            digest = keys.get(key) if keys else None
            if digest is not None:
                node["hash"] = (key, digest)
                node.pop("changed", None)

    def _pair_dir_files(self, node_a, node_b):
        """Hash comparison: pair the FILES of two counterpart folders by
        content, ignoring names. Returns (states_a, states_b), each
        {file name: (state, other_node)} with state one of:
        "matched" (same hash, however many duplicates; identical names preferred on
        ties), "differs" (no content partner, but a still-unpaired file
        with the same name exists: other_node), "unhashed" (this file
        has no hash yet, or it has no partner while the other folder
        still has unhashed files that could be it), "changed" (its cached
        hash is out of date: the file changed since it was hashed, see
        _attach_cached_hash), "missing".
        Memoized; cleared with the rank cache."""
        cache = getattr(self, "_pair_cache", None)
        if cache is None:
            cache = self._pair_cache = {}
        key = (os.path.normpath(node_a["full_path"]), os.path.normpath(node_b["full_path"]),
               self._comparison_key(), self._ignore_flags())
        if key in cache:
            return cache[key]
        files_a = {n: c for n, c in self._compared_children(node_a).items() if not c["is_dir"]}
        files_b = {n: c for n, c in self._compared_children(node_b).items() if not c["is_dir"]}
        states_a, states_b = {}, {}
        by_hash_a, by_hash_b = {}, {}
        unhashed_a = unhashed_b = False
        for files, by_hash, states in ((files_a, by_hash_a, states_a), (files_b, by_hash_b, states_b)):
            for name, child in files.items():
                digest = self._node_hash(child)
                if digest is None:
                    states[name] = ("changed" if child.get("changed") else "unhashed", None)
                else:
                    by_hash.setdefault(digest, []).append(name)
        unhashed_a = bool(states_a)
        unhashed_b = bool(states_b)
        # Pass 1: equal hashes. Not one-to-one: every file whose content
        # exists on the other side is matched, however many duplicates
        # either side holds (a same-named partner is preferred as the
        # representative one).
        for digest, names_a in by_hash_a.items():
            names_b = by_hash_b.get(digest)
            if not names_b:
                continue
            set_a, set_b = set(names_a), set(names_b)
            for n in names_a:
                partner = n if n in set_b else sorted(names_b)[0]
                states_a[n] = ("matched", files_b[partner])
            for n in names_b:
                partner = n if n in set_a else sorted(names_a)[0]
                states_b[n] = ("matched", files_a[partner])
        # Pass 2: leftovers.
        for files, states, other_files, other_states, other_unhashed in (
                (files_a, states_a, files_b, states_b, unhashed_b),
                (files_b, states_b, files_a, states_a, unhashed_a)):
            for name in files:
                if name in states:
                    continue
                if other_unhashed:
                    states[name] = ("unhashed", None)
                    continue
                other = other_files.get(name)
                if other is not None and other_states.get(name, (None,))[0] != "matched":
                    states[name] = ("differs", other)
                else:
                    states[name] = ("missing", None)
        result = (states_a, states_b)
        cache[key] = result
        return result

    def _hash_file_state(self, own_full, other_full):
        """(state, other_node) of the file at `own_full` against the
        folder holding `other_full`, per _pair_dir_files; None if the
        files' folders aren't both in the snapshots (caller falls back to
        the name-based comparison)."""
        own = self._snapshot_lookup(own_full)
        if own is None or own["is_dir"]:
            return None
        own_parent = self._snapshot_lookup(os.path.dirname(own_full))
        other_parent = self._snapshot_lookup(os.path.dirname(other_full))
        if own_parent is None:
            return None
        if other_parent is None or not other_parent["is_dir"]:
            return ("missing", None)
        states_a, _states_b = self._pair_dir_files(own_parent, other_parent)
        return states_a.get(own["name"], ("missing", None))

    def _stem_index(self, dir_node):
        """{name without extension: file node} for a directory node's
        files (folders never take part -- a folder called "v1.2" has no
        extension to ignore). Built once per node and kept on it; a
        rebuilt snapshot brings new nodes, so it never goes stale."""
        mode = (self._stem_mode(), self._ignore_flags())
        by_mode = dir_node.setdefault("_stems", {})
        index = by_mode.get(mode)
        if index is None:
            index = by_mode[mode] = {}
            for name, child in (dir_node["children"] or {}).items():
                if not child["is_dir"] and not self._ignored_kind(child):
                    index.setdefault(self._name_stem(name), child)
        return index

    def _counterpart_node(self, other_full, is_dir):
        """The snapshot node `other_full` refers to, if it is the same
        kind of thing (file vs folder); None otherwise. With "Ignore file
        extensions" on, a file with no exact-name counterpart falls back
        to any file in that folder with the same name minus extension."""
        node = self._snapshot_lookup(other_full)
        if node is not None and node["is_dir"] == is_dir and not self._ignored_kind(node):
            return node
        if is_dir or not self._ignore_extensions_active():
            return None
        parent = self._snapshot_lookup(os.path.dirname(other_full))
        if parent is None or not parent["is_dir"]:
            return None
        return self._stem_index(parent).get(self._name_stem(os.path.basename(other_full)))

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
        if not is_dir and full_path:
            ignored_tag = self._ignored_file_tag(full_path)
            if ignored_tag:
                return ignored_tag
        if other_dir is None or self._hash_comparison_blocked():
            return self._lone_tag(full_path, is_dir)

        other_full = os.path.join(other_dir, name)
        if not is_dir and full_path and self._hash_mode_active():
            hashed = self._hash_file_state(full_path, other_full)
            if hashed is not None:
                state, other_node = hashed
                if state == "missing":
                    return "missing_file"
                if state == "unhashed":
                    return "unhashed_file"
                if state == "changed":
                    return "changed_file"
                if state == "matched":
                    return "common_file"
                # Same name, different content: always "Content differs",
                # whatever the sizes and dates say.
                return "diff_content_file"
        other_node = self._counterpart_node(other_full, is_dir)
        if other_node is None:
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

    def _lone_tag(self, full_path, is_dir):
        """Tag for an item with nothing to compare against: plain, except
        under a hash comparison type, where a file with an out-of-date
        cached hash is "changed", one without a hash "unhashed", and a
        hashed one "hashed, but not compared". A folder takes "changed" if
        any file below it is changed, else "unhashed" if any is unhashed,
        else "hashed" (a folder without files stays plain)."""
        plain = "directory" if is_dir else "file"
        if not is_dir and full_path:
            ignored_tag = self._ignored_file_tag(full_path)
            if ignored_tag:
                return ignored_tag
        if not full_path or not self._hash_type_selected():
            return plain
        node = self._snapshot_lookup(full_path)
        if node is None:
            return plain
        # Blocked: a snapshot's hashes made under other settings still
        # count as "hashed".
        blocked = self._hash_comparison_blocked()
        has_hash = (lambda n: n.get("hash") is not None) if blocked else (lambda n: self._node_hash(n) is not None)
        if not is_dir:
            if has_hash(node):
                return "hashed_file"
            return "changed_file" if node.get("changed") else "unhashed_file"
        cache = getattr(self, "_dir_rank_cache", None)
        if cache is None:
            cache = self._dir_rank_cache = {}
        key = ("hashed", os.path.normpath(full_path), self._comparison_key(), blocked,
               self._ignore_flags())
        state = cache.get(key)
        if state is None:
            files = 0
            state = "hashed"
            stack = [node]
            while stack and state != "changed":
                for child in (stack.pop()["children"] or {}).values():
                    if child["is_dir"]:
                        stack.append(child)
                    elif self._ignored_kind(child):
                        continue
                    else:
                        files += 1
                        if not has_hash(child):
                            state = "changed" if child.get("changed") else "unhashed"
                            if state == "changed":
                                break
            if not files:
                state = "plain"
            cache[key] = state
        return {"hashed": "hashed_dir", "unhashed": "unhashed_dir",
                "changed": "changed_dir"}.get(state, plain)

    def _diff_axes(self, is_dir, size_str, date_str, other_full, date_field="mtime", own_full=None):
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
        if not is_dir and own_full and any(self._ignore_flags()) \
                and self._ignored_kind(self._snapshot_lookup(own_full)):
            return AXES_IGNORED
        if not is_dir and own_full and other_full and self._hash_mode_active():
            hashed = self._hash_file_state(own_full, other_full)
            if hashed is not None:
                state, other_node = hashed
                if state == "missing":
                    return None
                if state in ("unhashed", "changed"):
                    return AXES_UNHASHED
                if state == "matched":
                    return (None, None)
                return AXES_CONTENT_DIFFERS
        other_node = self._counterpart_node(other_full, is_dir) if other_full else None
        if other_node is None:
            return None
        return self._diff_axes_vs_node(other_node, is_dir, size_str, date_str, date_field)

    def _diff_axes_vs_node(self, other_node, is_dir, size_str, date_str, date_field="mtime"):
        """The actual comparison behind _diff_axes/_diff_tag, against an
        already-looked-up counterpart node. Both sides are compared as
        the same formatted strings the listing shows -- dates to the
        whole second, sizes as format_size's text for a file or "N items"
        for a folder -- so what counts as "different" matches what's on
        screen.

        With "Ignore file extensions" on, a file that has a counterpart
        at all matches: size and dates are not compared."""
        if not is_dir and self._ignore_extensions_active():
            return None, None
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
        if rank < 0:
            return f"{'changed' if direction == 'changed' else 'unhashed'}_{suffix}"
        if rank == 0:
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
        if self._ignore_extensions_active():
            return 0, None
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
        for _pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if _pane is not None:
                _pane._hash_groups = None
        self._pair_cache = {}

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
               date_field, self._comparison_key(), self._ignore_flags())
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

        # Ignored files (empty / hard links, see _ignored_kind) take no
        # part: neither side's copy counts as present, matching or different.
        children_a = self._compared_children(node_a)
        children_b = self._compared_children(node_b) if node_b is not None else {}

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
        # "Ignore file extensions": a file also pairs with any file of
        # the same name minus extension (see _counterpart_node).
        ignore_ext = self._ignore_extensions_active()
        stems_a = self._stem_index(node_a) if ignore_ext else None
        stems_b = self._stem_index(node_b) if ignore_ext and node_b is not None else {}
        # Hash comparison: files pair by content (see _pair_dir_files);
        # folders still pair by name. saw_unhashed: something here could
        # not be judged yet because it has no hash -- see the end.
        hash_pairs = self._hash_mode_active() and node_b is not None
        states_a, states_b = self._pair_dir_files(node_a, node_b) if hash_pairs else ({}, {})
        saw_unhashed = False
        saw_changed = False  # like saw_unhashed, but its cached hash is out of date
        for name, child_a in children_a.items():
            if hash_pairs and not child_a["is_dir"]:
                state, other = states_a.get(name, ("missing", None))
                if state == "matched":
                    continue
                if state == "changed":
                    saw_changed = True
                    continue
                if state == "unhashed":
                    saw_unhashed = True
                    continue
                if state == "missing":
                    saw_missing = True
                    continue
                # Same name, different content: a difference whatever the
                # sizes and dates say (the direction is only a placeholder;
                # with Recursive Folder Contents on, every differing folder
                # shows the Content differs color).
                rank, direction = 1, "larger"
                if rank > best_rank:
                    best_rank = rank
                    best_directions = {direction: 1}
                elif rank == best_rank and direction:
                    best_directions[direction] = best_directions.get(direction, 0) + 1
                continue
            child_b = children_b.get(name)
            if ignore_ext and not child_a["is_dir"] and (child_b is None or child_b["is_dir"]):
                child_b = stems_b.get(self._name_stem(name))
            if child_b is None or child_b["is_dir"] != child_a["is_dir"]:
                saw_missing = True
                continue
            if child_a["is_dir"]:
                rank, direction = self._dir_content_rank(child_a["full_path"], child_b["full_path"], date_field)
                if rank < 0:
                    if direction == "changed":
                        saw_changed = True
                    else:
                        saw_unhashed = True
                    continue
            else:
                rank, direction = self._file_diff_rank(child_a, child_b, date_field)
            if rank > best_rank:
                best_rank = rank
                best_directions = {direction: 1} if direction else {}
            elif rank == best_rank and rank > 0 and direction:
                best_directions[direction] = best_directions.get(direction, 0) + 1

        # Symmetric check: dir_b may hold items dir_a simply doesn't have.
        if hash_pairs:
            for name, child_b in children_b.items():
                if child_b["is_dir"]:
                    child_a = children_a.get(name)
                    if child_a is None or not child_a["is_dir"]:
                        saw_missing = True
                    continue
                state = states_b.get(name, ("missing", None))[0]
                if state == "missing":
                    saw_missing = True
                elif state == "changed":
                    saw_changed = True
                elif state == "unhashed":
                    saw_unhashed = True
        elif ignore_ext:
            for name, child_b in children_b.items():
                child_a = children_a.get(name)
                if child_a is not None and child_a["is_dir"] == child_b["is_dir"]:
                    continue
                if not child_b["is_dir"] and self._name_stem(name) in stems_a:
                    continue
                saw_missing = True
                break
        elif set(children_b) - set(children_a):
            saw_missing = True

        if hash_pairs and best_rank < 1:
            # Same content held a different number of times on each side
            # (extra duplicates): the folders are not the same.
            counts_a, counts_b = {}, {}
            for counts, kids in ((counts_a, children_a), (counts_b, children_b)):
                for kid in kids.values():
                    digest = None if kid["is_dir"] else self._node_hash(kid)
                    if digest is not None:
                        counts[digest] = counts.get(digest, 0) + 1
            if any(n != counts_b[h] for h, n in counts_a.items() if h in counts_b):
                best_rank, best_directions = 1, {"larger": 1}

        if saw_missing and best_rank < 3:
            best_rank = 3
            best_directions = {}

        if best_rank == 0:
            # Nothing definitely differs; if anything was left unjudged for
            # want of a hash, say so (rank -1) rather than "matching".
            # Changed (an out-of-date cached hash) outranks plain unhashed.
            if saw_changed:
                return -1, "changed"
            return (-1, "unhashed") if saw_unhashed else (0, None)
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
