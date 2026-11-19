"""
DuplicateFinderMixin: Duplicate Finder mode (Compare folders off) -- finds the
sets of identical files in the left pane's folder and tells the rest of the
app which color each one gets. No Qt widgets are created here; it reads the
pane's snapshot (see DiffEngineMixin._build_snapshot) and the tree's expansion
state, and leaves painting to ListingMixin._apply_tag.

What is "identical" follows the comparison type (Settings -> Options):
 - Name, size and date: same name, size and whole-second modified date; with
   "Ignore file extensions" / "Only first part of file name" the (shortened)
   name alone.
 - Hash value / Fingerprint: same digest. Only files whose size is shared with
   another file in scope can be duplicates, so only those are hashed (see
   _dup_hash_nodes) -- unique-size files are never touched, and show the plain
   file color.

Scope: Recursive Folder Selections on = every file under the pane's root;
off = the root's files plus the files of the folders expanded at the time of
the scan. The Global File Filter / "List Files" limit the files considered,
and ignored files (empty files / hard links, see DiffEngineMixin._ignored_kind)
never take part.

The result, pane._dup_info, is rebuilt by _dup_prepare at the start of every
listing/re-tag (ListingMixin.update_listing) -- so after a Hash, a setting
change or a Refresh -- and is what DiffEngineMixin._lone_tag reads:
    "group_of"  {normalized path: color index} of every file that has a copy
    "groups"    {group key: [file nodes]} (2 or more each), first appearance first
    "pending"   hash types: files that need a hash and have none ("unhashed")
    "changed"   hash types: files whose cached hash is out of date
    "pending_dirs"/"changed_dirs"  the folders holding such files
    "wasted"    bytes held by the extra copies
A group's color index is remembered per group key in pane._dup_color_map until
the folder changes or the pane's Refresh is pressed, so colors stay put while
files are deleted or moved; new groups continue the numbering.
"""

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from constants import PATH_ROLE, TAG_COLORS


class DuplicateFinderMixin:
    def _duplicate_mode_active(self) -> bool:
        """Compare folders is off: the left pane is searched for duplicates."""
        return not getattr(self, "_dual_pane_enabled", True)

    # ------------------------------------------------------------- Scope

    def _dup_file_passes_filter(self, name: str) -> bool:
        """The file filter / List Files rules ListingMixin._build_entries
        applies to a file row."""
        list_files = getattr(self, "list_files_cb", None)
        if list_files is not None and not list_files.isChecked():
            return False
        entry = getattr(self, "file_filter_entry", None)
        extensions = [e.strip().lower() for e in entry.text().split(",") if e.strip()] if entry else []
        if not extensions:
            return True
        invert = getattr(self, "invert_filter_cb", None)
        in_filter = os.path.splitext(name)[1][1:].lower() in extensions
        return in_filter ^ (invert.isChecked() if invert else False)

    def _dup_freeze_scope(self, pane, refresh=False):
        """The expanded folders (normalized paths) a non-recursive scan
        covers. With "Scan expanded folders" on this is always the tree's
        current state; with it off the set is kept from the last Refresh /
        Hash / folder load (refresh=True, or pane._dup_frozen_expanded
        cleared by _dup_reset_colors, re-reads it)."""
        live = getattr(self, "_scan_expanded", True)
        frozen = getattr(pane, "_dup_frozen_expanded", None)
        if live or refresh or frozen is None:
            tree = getattr(pane, "tree", None)
            frozen = ({os.path.normpath(p) for p in self._collect_expanded_paths(tree)}
                      if tree is not None else set())
            pane._dup_frozen_expanded = frozen
        return frozen

    def _dup_schedule_regroup(self, pane):
        """A folder was expanded/collapsed in the left pane: with Scan
        expanded folders on, regroup (once, after the burst of signals) --
        only when the set of expanded folders really differs from the one
        the last grouping used, which also keeps the listing's own
        re-expansion from retriggering this."""
        if (pane is not getattr(self, "left", None) or not self._duplicate_mode_active()
                or not getattr(self, "_scan_expanded", True) or getattr(pane, "_dup_regroup_pending", False)
                or getattr(pane, "tree", None) is None):
            return
        now = {os.path.normpath(p) for p in self._collect_expanded_paths(pane.tree)}
        if now == getattr(pane, "_dup_frozen_expanded", None):
            return
        pane._dup_regroup_pending = True

        def run():
            pane._dup_regroup_pending = False
            self.update_listings(rescan_disk=False)
        QTimer.singleShot(0, run)

    def _on_dup_scope_option_changed(self, *_args):
        """Scan expanded folders / Hash only same-size files toggled."""
        self._scan_expanded = self.scan_expanded_cb.isChecked()
        self._hash_same_size = self.hash_same_size_cb.isChecked()
        if self._duplicate_mode_active() and getattr(self.left, "snapshot_root", None) is not None:
            self.left._dup_frozen_expanded = None
            QTimer.singleShot(0, lambda: self.update_listings(rescan_disk=False))

    def _dup_scope_files(self, pane) -> list:
        """File nodes the duplicate search covers, in display order (folders
        first when "Sort Directories First" is on, names case-insensitive)."""
        root = pane.snapshot_root
        if root is None:
            return []
        recursive = self._recursive_folder_contents_enabled()
        expanded = None
        if not recursive:
            expanded = self._dup_freeze_scope(pane)
        dirs_first = getattr(self, "sort_directories_first_cb", None)
        dirs_first = dirs_first.isChecked() if dirs_first else True
        def display_kids(node):
            kids = sorted((node["children"] or {}).values(), key=lambda c: c["name"].lower())
            if dirs_first:
                kids = [c for c in kids if c["is_dir"]] + [c for c in kids if not c["is_dir"]]
            return kids

        # Depth-first in the order the tree shows the rows (a folder's
        # contents come right after the folder), so the first group met is
        # the first one on screen.
        files, stack = [], list(reversed(display_kids(root)))
        while stack:
            child = stack.pop()
            if child["is_dir"]:
                if recursive or (expanded is not None and os.path.normpath(child["full_path"]) in expanded):
                    stack.extend(reversed(display_kids(child)))
            elif (not child.get("placeholder") and not self._ignored_kind(child)
                    and self._dup_file_passes_filter(child["name"])):
                files.append(child)
        return files

    def _dup_hash_nodes(self, pane) -> list:
        """The files the Hash button should hash in Duplicate Finder mode:
        those in scope whose size another file in scope shares."""
        files = self._dup_scope_files(pane)
        if not getattr(self, "_hash_same_size", True):
            return files
        counts = {}
        for node in files:
            counts[node["size"]] = counts.get(node["size"], 0) + 1
        return [n for n in files if counts[n["size"]] > 1]

    # ------------------------------------------------------------- Groups

    def _dup_reset_colors(self, pane):
        """Forget the group -> color assignment (the pane's Refresh)."""
        pane._dup_color_map = {}
        pane._dup_next = 0
        pane._dup_frozen_expanded = None  # re-read which folders are expanded

    def _dup_prepare(self, pane):
        """(Re)build pane._dup_info from the pane's current snapshot -- or
        clear it (None) when Duplicate Finder mode does not apply."""
        if pane is not getattr(self, "left", None):
            return
        if (not self._duplicate_mode_active() or pane.snapshot_root is None
                or self._hash_comparison_blocked()):
            pane._dup_info = None
            return
        root_key = os.path.normpath(pane.snapshot_root["full_path"])
        if getattr(pane, "_dup_root", None) != root_key:
            pane._dup_root = root_key
            self._dup_reset_colors(pane)
        color_map = getattr(pane, "_dup_color_map", None)
        if color_map is None:
            self._dup_reset_colors(pane)
            color_map = pane._dup_color_map

        files = self._dup_scope_files(pane)
        hashing = self._hash_type_selected()
        # A different way of grouping (comparison type, name options) starts
        # the numbering over; within one way the colors stay put.
        grouping = (hashing, self._comparison_key() if hashing else self._stem_mode())
        if getattr(pane, "_dup_grouping", None) != grouping:
            pane._dup_grouping = grouping
            self._dup_reset_colors(pane)
            color_map = pane._dup_color_map
        pending, changed = set(), set()
        groups = {}
        if hashing:
            same_size_only = getattr(self, "_hash_same_size", True)
            size_counts = {}
            for node in files:
                size_counts[node["size"]] = size_counts.get(node["size"], 0) + 1
            for node in files:
                if same_size_only and size_counts[node["size"]] < 2:
                    continue  # a unique size cannot have a duplicate
                digest = self._node_hash(node)
                if digest is None:
                    (changed if node.get("changed") else pending).add(os.path.normpath(node["full_path"]))
                else:
                    groups.setdefault(digest, []).append(node)
        else:
            stem_mode = self._stem_mode()
            for node in files:
                if stem_mode:
                    key = self._name_stem(node["name"])
                else:
                    key = (node["name"], node["size"], int(node["mtime"]))
                groups.setdefault(key, []).append(node)
        groups = {k: v for k, v in groups.items() if len(v) > 1}

        group_of, wasted = {}, 0
        for key, nodes in groups.items():
            index = color_map.get(key)
            if index is None:
                index = color_map[key] = pane._dup_next
                pane._dup_next += 1
            for node in nodes:
                group_of[os.path.normpath(node["full_path"])] = index
            sizes = [n["size"] for n in nodes]
            wasted += sum(sizes) - max(sizes)

        def with_ancestors(paths):
            dirs = set()
            for path in paths:
                parent = os.path.dirname(path)
                while parent and parent not in dirs:
                    dirs.add(parent)
                    if parent == root_key or os.path.dirname(parent) == parent:
                        break
                    parent = os.path.dirname(parent)
            return dirs

        pane._dup_info = {
            "group_of": group_of, "groups": groups, "wasted": wasted,
            "pending": pending, "changed": changed,
            "pending_dirs": with_ancestors(pending), "changed_dirs": with_ancestors(changed),
        }
        self._dup_update_buttons(pane)

    # ----------------------------------------------------- Select buttons

    def _on_dup_survivor_changed(self, *_args):
        """"Extra copies keep:" changed."""
        self._dup_survivor = self.extra_copies_keep_combo.currentIndex()

    def _dup_survivor_node(self, nodes):
        """The copy in a group "Extra copies" leaves alone (nodes are in tree
        order, so ties go to the first)."""
        mode = getattr(self, "_dup_survivor", 0)
        if mode == 1:
            return min(nodes, key=lambda n: n["mtime"])
        if mode == 2:
            return max(nodes, key=lambda n: n["mtime"])
        if mode == 3:
            return nodes[0]
        return min(nodes, key=lambda n: len(n["full_path"]))

    def _dup_select_set(self, pane, extra_only: bool) -> set:
        """Normalized paths the Duplicates (every copy) / Extra copies (all
        but one per group) button selects; empty without a duplicate scan.
        Only the left pane has one."""
        info = getattr(pane, "_dup_info", None) if pane is getattr(self, "left", None) else None
        if not info:
            return set()
        paths = set()
        for nodes in info["groups"].values():
            keep = self._dup_survivor_node(nodes) if extra_only else None
            for node in nodes:
                if node is not keep:
                    paths.add(os.path.normpath(node["full_path"]))
        return paths

    def _dup_select_paths(self, pane, extra_only: bool) -> list:
        return sorted(self._dup_select_set(pane, extra_only))

    def _dup_buttons_state(self, pane):
        """(duplicates enabled, extra copies enabled) for the pane's two
        Duplicate Finder buttons. Hash value / Fingerprint need the scanned
        files hashed first (nothing left unhashed or changed); "Extra copies"
        is off for the two name-only options, where a shared name does not
        mean identical content."""
        info = getattr(pane, "_dup_info", None) if pane is getattr(self, "left", None) else None
        if not info:
            return False, False
        hashing = self._hash_type_selected()
        ready = not (hashing and (info["pending"] or info["changed"]))
        name_only = (not hashing) and bool(self._stem_mode())
        return ready, ready and not name_only

    def _dup_update_buttons(self, pane):
        """Enable/disable (and un-press, if now disabled) the two buttons."""
        buttons = getattr(pane, "select_toggle_buttons", None) or {}
        enabled = self._dup_buttons_state(pane) if self._duplicate_mode_active() else (False, False)
        for key, on in zip(("dup_all", "dup_extra"), enabled):
            btn = buttons.get(key)
            if btn is None:
                continue
            if not on and btn.isChecked():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
            btn.setEnabled(on)

    # ---------------------------------------------------------- Right pane

    def _dup_build_right_snapshot(self):
        """The right pane's snapshot in Duplicate Finder mode: the left
        pane's duplicate files and every folder above them, as copies of the
        left pane's nodes under the very same paths (so colors, the statistics
        and revealing a row in the left tree all go by path). Folder sizes
        are the sum of the files kept in them. (root, index), or (None, {})
        without a duplicate scan."""
        left = self.left
        info = getattr(left, "_dup_info", None)
        real_root = left.snapshot_root
        if not info or real_root is None:
            return None, {}
        real_index = left.snapshot_index or {}
        root_path = os.path.normpath(real_root["full_path"])

        def copy_dir(real):
            node = {k: v for k, v in real.items() if k not in ("children", "_empty", "_stems", "size")}
            node["children"] = {}
            node["size"] = 0
            return node

        root = copy_dir(real_root)
        index = {root_path: root}

        def ensure_dir(path):
            norm = os.path.normpath(path)
            node = index.get(norm)
            if node is not None:
                return node
            real = real_index.get(norm)
            if real is None:
                return root
            node = copy_dir(real)
            index[norm] = node
            ensure_dir(os.path.dirname(norm))["children"][node["name"]] = node
            return node

        for nodes in info["groups"].values():
            for real in nodes:
                node = dict(real)
                node.pop("_stems", None)
                parent = ensure_dir(os.path.dirname(os.path.normpath(real["full_path"])))
                parent["children"][node["name"]] = node
                index[os.path.normpath(real["full_path"])] = node
                size = real["size"] or 0
                ancestor = os.path.normpath(parent["full_path"])
                while True:
                    index[ancestor]["size"] += size
                    if ancestor == root_path or os.path.dirname(ancestor) == ancestor:
                        break
                    ancestor = os.path.dirname(ancestor)
        return root, index

    def _dup_sync_right(self, progress=None):
        """Duplicate Finder mode: rebuild the right pane's snapshot from the
        left pane's duplicate scan and list it (same colors, the left pane's
        sort order), feed its Statistics View, and report the wasted space on
        its upper status line. Called after every left-pane listing."""
        left, right = self.left, self.right
        left_dir = left.entry.text()
        root, index = self._dup_build_right_snapshot()
        right.snapshot_root, right.snapshot_index = root, index
        if root is None:
            right.snapshot_root, right.snapshot_index = None, {}
        # The right pane is a read-only view of the left one's duplicates:
        # its folder entry and toolbar buttons are hidden (their space kept,
        # so both panes stay aligned).
        from PySide6.QtWidgets import QSizePolicy
        for name in ("up_button", "select_button", "save_snapshot_button", "clear_view_button",
                     "pane_refresh_button"):
            widget = getattr(right, name, None)
            if widget is not None:
                policy = widget.sizePolicy()
                policy.setRetainSizeWhenHidden(True)
                widget.setSizePolicy(policy)
                widget.setVisible(False)
        cluster = getattr(right, "cluster_container", None)
        if cluster is not None:
            cluster.setVisible(False)
        if root is None:
            self._dup_clear_right()
            return
        self.update_listing(left_dir, right, None, progress=progress,
                            refresh_snapshot=False, retag_only=False)
        sort_column = getattr(left.tree, "_sort_column", None)
        if sort_column is not None:
            asc = getattr(left.tree, "_sort_ascending", True)
            right.tree._sort_column, right.tree._sort_ascending = sort_column, asc
            self._apply_sort(right.tree, sort_column, asc)
        timeline = getattr(right, "timeline", None)
        if timeline is not None:
            timeline.set_folder_open(True)
        self._refresh_pane_timeline(right)
        info = left._dup_info
        groups = len(info["groups"])
        wasted = self.format_size(info["wasted"])
        title = getattr(right, "dup_title_label", None)
        if title is not None:
            title.setToolTip(f"Duplicates in {left_dir}: {groups} group{'s' if groups != 1 else ''}, "
                             f"wasted space {wasted}")
        right._dup_sel_prev = self._dup_files_of(right, right.tree.selectedItems())

    def _dup_clear_right(self):
        """No duplicate scan (nothing loaded, or a Hash type not hashed yet
        is simply an empty list): blank the right pane."""
        right = self.right
        right.tree.clear()
        right.snapshot_root, right.snapshot_index = None, {}
        right._last_listed_dir = None
        timeline = getattr(right, "timeline", None)
        if timeline is not None:
            timeline.set_folder_open(False)
        self._refresh_pane_timeline(right)
        self.update_type_counts(right)
        self._set_upper_status(right, "")

    def _dup_files_of(self, pane, items) -> set:
        """Normalized paths of the duplicate files among `items` of the right
        pane's tree; a folder stands for every file below it."""
        index = pane.snapshot_index or {}
        files = set()
        for item in items:
            path = item.data(0, PATH_ROLE)
            node = index.get(os.path.normpath(path)) if path else None
            stack = [node] if node is not None else []
            while stack:
                current = stack.pop()
                if current["is_dir"]:
                    stack.extend((current["children"] or {}).values())
                else:
                    files.add(os.path.normpath(current["full_path"]))
        return files

    def _on_dup_right_selection(self, pane):
        """Duplicate Finder mode: selecting files in the Duplicate File View
        (the right pane) selects every copy of them -- the whole group -- in
        both panes, and reveals them in the left one. Click, Shift and Ctrl
        work as in any list: Ctrl toggles whole groups on and off, the others
        replace the selection."""
        if (pane is not getattr(self, "right", None) or not self._duplicate_mode_active()
                or getattr(self, "_dup_reveal_guard", False) or getattr(pane, "_tree_rebuild_pending", False)
                or getattr(pane, "_dup_reveal_pending", False)):
            return
        pane._dup_reveal_pending = True
        ctrl = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)

        def run():
            pane._dup_reveal_pending = False
            info = getattr(self.left, "_dup_info", None) or {}
            members = {}
            for nodes in (info.get("groups") or {}).values():
                paths = [os.path.normpath(n["full_path"]) for n in nodes]
                for path in paths:
                    members[path] = paths

            def with_groups(files):
                result = set()
                for path in files:
                    result.update(members.get(path, [path]))
                return result

            native = self._dup_files_of(pane, pane.tree.selectedItems())
            previous = getattr(pane, "_dup_sel_prev", None) or set()
            if ctrl:
                result = (previous - with_groups(previous - native)) | with_groups(native - previous)
            else:
                result = with_groups(native)
            pane._dup_sel_prev = result
            full = {os.path.normpath(n["full_path"]): n["full_path"]
                    for nodes in (info.get("groups") or {}).values() for n in nodes}
            targets = [full[p] for p in result if p in full]
            self._dup_reveal_guard = True
            try:
                if result != native:
                    if targets:
                        self._select_paths_in_tree(pane.tree, targets, additive=False,
                                                    auto_expand=True, select_subtree=False)
                    else:
                        pane.tree.clearSelection()
                if targets:
                    self._select_paths_in_tree(self.left.tree, targets, additive=False,
                                                auto_expand=True, select_subtree=False)
                    self._scroll_tree_to_first_selected(self.left.tree)
                else:
                    self.left.tree.clearSelection()
            finally:
                self._dup_reveal_guard = False
        QTimer.singleShot(0, run)

    # --------------------------------------------------------------- Tags

    def _dup_file_tag(self, full_path):
        """Tag of a file in Duplicate Finder mode: "dup_<color index>" for a
        file with copies, "unhashed_file" / "changed_file" for one that still
        needs a (fresh) hash, else the plain "file". None when there is no
        duplicate scan to go by (the caller falls back to the usual tags)."""
        info = getattr(getattr(self, "left", None), "_dup_info", None)
        if not info or not self._duplicate_mode_active():
            return None
        norm = os.path.normpath(full_path)
        index = info["group_of"].get(norm)
        if index is not None:
            return f"dup_{index}"
        if norm in info["changed"]:
            return "changed_file"
        if norm in info["pending"]:
            return "unhashed_file"
        return "file"

    def _dup_dir_tag(self, full_path):
        """Tag of a folder in Duplicate Finder mode: marked "unhashed" /
        "changed" while a file below it still needs a hash, else plain. None
        without a duplicate scan."""
        info = getattr(getattr(self, "left", None), "_dup_info", None)
        if not info or not self._duplicate_mode_active():
            return None
        norm = os.path.normpath(full_path)
        if norm in info["changed_dirs"]:
            return "changed_dir"
        if norm in info["pending_dirs"]:
            return "unhashed_dir"
        return "dupfolder"

    def _dup_summary_parts(self, pane) -> list:
        """[(plain text, html)] of the summary under the file list in
        Duplicate Finder mode: groups of identical files, the files in
        them, the space the extra copies waste (and, while hashing is
        still to do, how many files are unhashed). Colors follow the
        Duplicates / Extra copies button colors."""
        info = getattr(getattr(self, "left", None), "_dup_info", None)
        if not info:
            return []
        nbsp = "\u00a0"
        groups = info["groups"]
        copies = sum(len(nodes) for nodes in groups.values())
        dup_color = self._select_colors["dup_all"][1]
        waste_color = self._select_colors["dup_extra"][1]
        items = [
            (f"Groups:{nbsp}{len(groups)}", dup_color),
            (f"Duplicate{nbsp}files:{nbsp}{copies}", dup_color),
            (f"Wasted:{nbsp}{self.format_size(info['wasted']).replace(' ', nbsp)}", waste_color),
        ]
        unhashed = len(info["pending"]) + len(info["changed"])
        if unhashed:
            items.append((f"Unhashed:{nbsp}{unhashed}", TAG_COLORS["unhashed_file"][0] or "#C8C8C8"))
        return [(text, f'<span style="color:{color};">{text}</span>') for text, color in items]
