"""
HashOpsMixin: computing file hashes for the Hash value / Fingerprint
comparison types, and the per-pane "Hash" button that does it.

Hashes live on the in-memory snapshot's file nodes as node["hash"] =
(comparison_key, hex digest) -- see DiffEngineMixin._node_hash, which only
honors one made under the comparison settings in effect now. Because a
snapshot is rebuilt from scratch on every Refresh, every digest is also
kept in self._hash_cache {normalized path: (size, int mtime, {key: digest})}
(several comparison keys per file), and DiffEngineMixin._snapshot_node
re-attaches it while rebuilding, as long as the file's size and mtime still
match. Pairing files by hash (the actual comparison) is in diff_engine.py.

With Settings > Options > "Cache hashed folders" on (and a hash type
selected) that RAM cache is backed by hash_cache_store.HashCacheStore, a
SQLite file: read once per folder root when a folder loads (or when the
setting / comparison type is switched on), written when hashes are made
(about every _FLUSH_SECONDS while hashing and at the end). Opening a folder
writes nothing. See "Plan: persistent hash cache" in docs/Roadmap.txt.
"""

from __future__ import annotations

import hashlib
import os
import time

from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox

from constants import CMP_FINGERPRINT, CMP_HASH, HASH_ALGORITHMS, PATH_ROLE
from hash_cache_store import HashCacheStore

_CHUNK = 1024 * 1024
_UNIT = 4096  # progress bar unit (bytes): keeps the bar's int range safe for huge totals
_FLUSH_SECONDS = 30  # how often hashes made so far are written to the persistent cache

_ALGORITHM_FACTORIES = {
    "MD5": hashlib.md5, "SHA-1": hashlib.sha1, "SHA-256": hashlib.sha256,
    "BLAKE2b": hashlib.blake2b,
}


class _HashLive:
    """File List View rows as hash progress bars (Settings -> File List View
    -> "File List names as hash progress bars"). Owns, for one _hash_pane run,
    the tree's `_hash_fill` dict (normalized path -> 0..1, read by the row
    painting in custom_widgets/dnd_widgets): a file's own fraction of the
    bytes read, and each ancestor folder's share of all bytes its contents
    need. Also follows the hashing with the scrollbar (until the user scrolls
    by hand), and with Expand To Selection on opens the folders being hashed,
    closing them again in finish()."""

    _REPAINT_SECONDS = 0.04

    def __init__(self, owner, pane, todo, cost):
        self.owner, self.pane, self.tree = owner, pane, pane.tree
        self.expand = owner.expand_matching_cb.isChecked()
        root = pane.snapshot_root.get("full_path") or ""
        self.root = os.path.normpath(root) if root else None
        self.fill = {}
        self.total = {}   # normalized path -> bytes its contents need (files: own cost)
        self.done = {}
        self.ancestors = {}  # file path -> [outermost ... innermost folder paths]
        for node in todo:
            path = os.path.normpath(node["full_path"])
            chain, parent = [], os.path.dirname(path)
            while self.root and parent != self.root and len(parent) > len(self.root) and parent != os.path.dirname(parent):
                chain.append(parent)
                parent = os.path.dirname(parent)
            chain.reverse()
            self.ancestors[path] = chain
            c = max(0, cost(node["size"]))
            for key in [path] + chain:
                self.total[key] = self.total.get(key, 0) + c
                self.done.setdefault(key, 0)
        self.scrollbar = self.tree.verticalScrollBar()
        self.orig_scroll = self.scrollbar.value()
        self.follow = True
        self._set_value = None
        self.expanded = []
        self.items = {}
        for item in owner._iter_items(self.tree):
            p = item.data(0, PATH_ROLE)
            if p:
                self.items[os.path.normpath(p)] = item
        self.pre_selected = bool(self.tree.selectedItems())
        self.current = None
        self._last_paint = 0.0
        self.tree._hash_fill = self.fill

    def begin_file(self, node):
        self.current = os.path.normpath(node["full_path"])
        self._publish(self.current)
        self._reveal(self.current)
        self.tree.viewport().update()

    def add_bytes(self, n):
        if self.current is None:
            return
        for key in [self.current] + self.ancestors.get(self.current, []):
            self.done[key] += n
            self._publish(key)
        now = time.monotonic()
        if now - self._last_paint >= self._REPAINT_SECONDS:
            self._last_paint = now
            self.tree.viewport().update()

    def end_file(self):
        if self.current is not None:
            path = self.current
            for key in [path] + self.ancestors.get(path, []):
                self.done[key] = max(self.done[key], 0)
            self.fill[path] = 1.0
            self.current = None

    def _publish(self, key):
        total = self.total.get(key, 0)
        self.fill[key] = (self.done[key] / total) if total else 1.0

    def _reveal(self, path):
        """Expand (Expand To Selection on) and/or scroll to the file being
        hashed, or to the nearest visible folder row above it."""
        tree = self.tree
        target = None
        reachable = True
        for folder in self.ancestors.get(path, []):
            item = self.items.get(folder)
            if item is None:
                reachable = False
                break
            if not item.isExpanded():
                if self.expand:
                    item.setExpanded(True)
                    self.expanded.append(item)
                    self._register(item)
                else:
                    target = item
                    reachable = False
                    break
        if reachable:
            target = self.items.get(path)
        if target is None or not self.follow:
            return
        if self._set_value is not None and self.scrollbar.value() != self._set_value:
            self.follow = False  # the user scrolled: stop following
            return
        tree.scrollToItem(target, QAbstractItemView.PositionAtCenter)
        self._set_value = self.scrollbar.value()

    def _register(self, item):
        def walk(it):
            for i in range(it.childCount()):
                child = it.child(i)
                p = child.data(0, PATH_ROLE)
                if p:
                    self.items[os.path.normpath(p)] = child
                walk(child)
        walk(item)

    def finish(self):
        """Stop painting; close what was opened. Call before the listing is
        rebuilt (which keeps the expand state it finds)."""
        self.tree._hash_fill = None
        for item in reversed(self.expanded):
            try:
                item.setExpanded(False)
            except RuntimeError:
                pass
        self.tree.viewport().update()

    def restore_scroll(self):
        """After the rebuild: to the first selection made before hashing, or
        back where the list was -- unless the user took over scrolling."""
        if not self.follow:
            return
        if self.pre_selected:
            self.owner._scroll_tree_to_first_selected(self.tree)
        else:
            self.scrollbar.setValue(self.orig_scroll)


class HashOpsMixin:
    def _hash_cost(self, size: int) -> int:
        """Bytes that actually get read from a file of `size` bytes under
        the current comparison type (Fingerprint reads only two chunks)."""
        if getattr(self, "_cmp_type", 0) == CMP_FINGERPRINT:
            return min(size, (self._cmp_chunk_start_kb + self._cmp_chunk_end_kb) * 1024)
        return size

    def _compute_file_hash(self, path: str, size: int, on_bytes=None):
        """Hex digest of the file at `path` under the current comparison
        settings, or None if it can't be read or `on_bytes` (called with
        each chunk's size) returned False, i.e. the operation was
        cancelled. Hash value: the whole file. Fingerprint: the file size,
        then the first and last chunk -- or the whole file if it is not
        larger than the two chunks together."""
        digest = _ALGORITHM_FACTORIES[HASH_ALGORITHMS[self._cmp_algorithm]]()

        def feed(f, count):
            while count > 0:
                data = f.read(min(_CHUNK, count))
                if not data:
                    return True
                digest.update(data)
                count -= len(data)
                if on_bytes is not None and on_bytes(len(data)) is False:
                    return False
            return True

        try:
            with open(path, "rb") as f:
                if self._cmp_type == CMP_FINGERPRINT:
                    start, end = self._cmp_chunk_start_kb * 1024, self._cmp_chunk_end_kb * 1024
                    digest.update(size.to_bytes(8, "little"))
                    if size <= start + end:
                        ok = feed(f, size)
                    else:
                        ok = feed(f, start)
                        if ok:
                            f.seek(size - end)
                            ok = feed(f, end)
                else:
                    ok = feed(f, size)
        except OSError:
            return None
        return digest.hexdigest() if ok else None

    def _display_order_key(self, pane):
        """Sort key putting file nodes in (approximately) the order the File
        List View shows them -- by name, folders first when that option is on
        -- so the list can follow the hashing top to bottom."""
        root = os.path.normpath(pane.snapshot_root.get("full_path") or "")
        dirs_first = self.sort_directories_first_cb.isChecked()

        def key(node):
            parts = os.path.relpath(os.path.normpath(node["full_path"]), root).split(os.sep)
            last = len(parts) - 1
            return [(1 if (dirs_first and i == last) else 0, name.lower()) for i, name in enumerate(parts)]
        return key

    def _files_to_hash(self, pane, progress):
        """Snapshot file nodes the Hash button should look at: every file
        in the pane with Recursive Folder Selections on; otherwise only
        those currently visible (top level plus expanded folders)."""
        nodes = []
        if self._duplicate_mode_active() and pane is self.left:
            # Duplicate Finder mode: only files whose size is shared (the
            # scope -- Recursive Folder Selections, expanded folders, the
            # file filter -- is DuplicateFinderMixin's).
            self._dup_freeze_scope(pane, refresh=True)  # Hash re-reads which folders are expanded
            for node in self._dup_hash_nodes(pane):
                if not progress.tick():
                    break
                nodes.append(node)
            return nodes
        if self._recursive_folder_contents_enabled():
            stack = [pane.snapshot_root]
            while stack:
                node = stack.pop()
                if not progress.tick():
                    return nodes
                if node["is_dir"]:
                    stack.extend((node["children"] or {}).values())
                elif not self._ignored_kind(node):
                    nodes.append(node)
        else:
            from constants import IS_DIR_ROLE, PATH_ROLE
            for item in self._iter_visible_items(pane.tree):
                if not progress.tick():
                    return nodes
                if item.data(0, IS_DIR_ROLE):
                    continue
                node = self._snapshot_lookup(item.data(0, PATH_ROLE))
                if node is not None and not node["is_dir"] and not self._ignored_kind(node):
                    nodes.append(node)
        return nodes

    def _hash_pane(self, pane, rehash: bool = False):
        """The pane's "Hash" button: hash its not-yet-hashed files under
        the current comparison type. Enumerates first (busy bar), then
        shows progress by bytes; the pane's Cancel button stops it and
        keeps what was done. Afterwards both panes are re-tagged.
        `rehash` (the Rehash button): hash every file in scope, not just the
        unhashed ones, and replace ALL stored digests of each file (every
        key) with the new one; counts files whose digest changed although
        size and date did not."""
        if (not self._hash_type_selected() or pane.snapshot_root is None
                or getattr(pane, "snapshot_file", None)):
            return False
        key = self._comparison_key()
        progress = self._start_transfer_progress(total=None, label="Enumerating...", pane=pane)
        hashed = failed = 0
        pending = []  # rows not yet written to the persistent cache
        cache_failed = False
        saved = compared = differed = 0
        live = None

        def flush():
            nonlocal saved, cache_failed
            count = len(pending)
            if not count:
                return
            if self._flush_hash_rows(pending, exclusive=rehash):
                if self._hash_cache_enabled():
                    saved += count
            else:
                cache_failed = True
        try:
            nodes = self._files_to_hash(pane, progress)
            todo = list(nodes) if rehash else [n for n in nodes if self._node_hash(n) is None]
            if not progress.cancelled and todo:
                if getattr(self, "hash_progress_in_list_cb", None) is not None \
                        and self.hash_progress_in_list_cb.isChecked():
                    todo.sort(key=self._display_order_key(pane))
                    live = _HashLive(self, pane, todo, self._hash_cost)
                total_units = sum(self._hash_cost(n["size"]) for n in todo) // _UNIT + 1
                progress.start_phase(total_units)
                progress._on_tick = None
                label = getattr(self, "_active_status_label", None)
                carry = 0
                last_label = 0.0

                def on_bytes(n):
                    nonlocal carry
                    if live is not None:
                        live.add_bytes(n)
                    carry += n
                    units, carry = divmod(carry, _UNIT)
                    return progress.advance(units) if units else progress.pump()

                last_flush = time.monotonic()
                for i, node in enumerate(todo, 1):
                    if label is not None and time.monotonic() - last_label > 0.25:
                        last_label = time.monotonic()
                        label.setText(f"{'Rehashing' if rehash else 'Hashing'} {i}/{len(todo)}")
                        QApplication.processEvents()
                    previous = self._node_hash(node)
                    if live is not None:
                        live.begin_file(node)
                    digest = self._compute_file_hash(node["full_path"], node["size"], on_bytes)
                    if live is not None:
                        live.end_file()
                    if progress.cancelled:
                        break
                    if digest is None:
                        failed += 1
                        continue
                    if rehash and previous is not None:
                        compared += 1
                        differed += previous != digest
                    self._remember_hash(node, key, digest, replace=rehash)
                    pending.append((os.path.normpath(node["full_path"]), key,
                                    node["size"], int(node["mtime"]), digest))
                    hashed += 1
                    if time.monotonic() - last_flush >= _FLUSH_SECONDS:
                        last_flush = time.monotonic()
                        flush()
        finally:
            if live is not None:
                live.finish()
            flush()
            removed = self._prune_hash_cache(pane) if hashed else 0
            self._finish_transfer_progress(result_count=hashed)
        # The other pane may show the same folder (or files with the same
        # content-hash source): hand it what is in the RAM cache now.
        self._reattach_cached_hashes()
        self._invalidate_dir_rank_cache()
        self.update_listings(rescan_disk=False)
        if live is not None:
            live.restore_scroll()
        # Said after the re-tag (which overwrites the status text): files
        # only compare once BOTH panes' files are hashed, so when the other
        # pane still has unhashed files nothing changes color yet -- say so.
        message = f"{hashed} {'rehashed' if rehash else 'hashed'}"
        if rehash and compared:
            message += (f", {differed} hashed differently although size and date were unchanged"
                        if differed else ", all matched their earlier hashes")
        if failed:
            message += f", {failed} could not be read"
        if cache_failed:
            message += " (could not write the hash cache)"
        other = self.right if pane is self.left else self.left
        waiting = 0
        if other.snapshot_index:
            waiting = sum(1 for n in other.snapshot_index.values()
                          if not n["is_dir"] and self._node_hash(n) is None)
        if waiting and not progress.cancelled:
            message += f". The other pane still has {waiting} unhashed file{'s' if waiting != 1 else ''}; " \
                       "hash it too to compare."
        self._set_transfer_label(message, pane)
        self._report_cache_written(pane, saved, removed, cache_failed)
        return not progress.cancelled

    # ------------------------------------------------- Persistent hash cache

    def _remember_hash(self, node, key, digest, replace: bool = False):
        """Put a freshly computed digest on its node and into the RAM
        caches (by path, and by inode so a renamed file keeps it)."""
        node["hash"] = (key, digest)
        node.pop("changed", None)
        cache = self.__dict__.setdefault("_hash_cache", {})
        by_inode = self.__dict__.setdefault("_hash_cache_by_inode", {})
        norm = os.path.normpath(node["full_path"])
        size, mtime = node["size"], int(node["mtime"])
        entry = cache.get(norm)
        if entry is None or (entry[0], entry[1]) != (size, mtime):
            entry = cache[norm] = (size, mtime, {})
        if replace:
            entry[2].clear()
        entry[2][key] = digest
        try:
            st = os.stat(node["full_path"])
        except OSError:
            return
        slot = by_inode.setdefault((st.st_dev, st.st_ino, st.st_size, int(st.st_mtime)), {})
        if replace:
            slot.clear()
        slot[key] = digest

    def _hash_key_for(self, kind):
        """The comparison key a digest of type `kind` (CMP_HASH or
        CMP_FINGERPRINT) gets under the algorithm / chunk sizes set now."""
        if kind == CMP_FINGERPRINT:
            return (CMP_FINGERPRINT, self._cmp_algorithm, self._cmp_chunk_start_kb, self._cmp_chunk_end_kb)
        return (CMP_HASH, self._cmp_algorithm)

    def _stored_digest_info(self, path, kind):
        """(comparison key, hex digest) of type `kind` already known for the
        file at `path` (on its snapshot node, or in the RAM hash cache while
        the file's size and mtime still match), whatever comparison type is
        selected now; None if there is none. Nothing is computed."""
        node = self._snapshot_lookup(path)
        if node is None or node["is_dir"]:
            return None
        stored = node.get("hash")
        if stored and stored[0] and stored[0][0] == kind:
            return stored
        entry = self.__dict__.get("_hash_cache", {}).get(os.path.normpath(path))
        if entry and entry[0] == node["size"] and entry[1] == int(node["mtime"]):
            key = self._hash_key_for(kind)
            digest = entry[2].get(key)
            if digest is not None:
                return key, digest
        return None

    def _copy_text(self, pane, path, text, what):
        """Context menu "Copy Metadata" entry: `text` to the clipboard, and
        a note on the pane's upper status line."""
        QApplication.clipboard().setText(text)
        self._set_upper_status(pane, f"Copied {what} of {os.path.basename(path)}")

    def _copy_digest(self, pane, path, kind, info):
        """Context menu "Copy Hash" / "Copy Fingerprint": put the digest on
        the clipboard as text and say on the pane's upper status line which
        one it was (algorithm, and the chunk sizes for a fingerprint)."""
        key, digest = info
        QApplication.clipboard().setText(digest)
        algorithm = HASH_ALGORITHMS[key[1]]
        if kind == CMP_FINGERPRINT:
            what = f"{algorithm} fingerprint (size + first {key[2]} kB + last {key[3]} kB)"
        else:
            what = f"{algorithm} hash"
        self._set_upper_status(pane, f"Copied {what} of {os.path.basename(path)}")

    def _hash_store(self) -> HashCacheStore:
        store = self.__dict__.get("_hash_store_obj")
        if store is None:
            store = self._hash_store_obj = HashCacheStore()
        return store

    def _hash_cache_enabled(self) -> bool:
        """The persistent cache is read and written: "Cache hashed folders"
        is on and a hash type is selected (Name, size and date never
        touches it)."""
        return bool(getattr(self, "_cache_hashed_folders", False)) and self._hash_type_selected()

    def _flush_hash_rows(self, pending, exclusive: bool = False) -> bool:
        """Write `pending` rows (and empty the list) to the persistent
        cache. True if nothing needed writing or the write worked."""
        if not pending:
            return True
        rows = list(pending)
        pending.clear()
        if not self._hash_cache_enabled():
            return True
        return self._hash_store().put_many(rows, exclusive=exclusive)

    def _prune_hash_cache(self, pane) -> int:
        """After hashing: drop cache rows below this pane's root whose
        files are gone. Only for a scan that really saw the folder: files
        below an unreadable folder are kept, and a path missing from the
        snapshot is only dropped if it is also missing on disk now.
        Returns how many files' rows were removed."""
        if not self._hash_cache_enabled():
            return 0
        root_node, index = pane.snapshot_root, pane.snapshot_index
        if root_node is None or not index:
            return 0
        store = self._hash_store()
        root = os.path.normpath(root_node["full_path"])
        failed = [p for p, n in index.items() if n.get("scan_failed")]
        sep = os.sep
        gone = []
        for path in store.paths_under(root):
            if path in index:
                continue
            if any(path.startswith(f.rstrip(sep) + sep) for f in failed):
                continue
            if os.path.lexists(path):
                continue
            gone.append(path)
        if gone and store.delete_paths(gone):
            cache = self.__dict__.get("_hash_cache", {})
            for path in gone:
                cache.pop(path, None)
            return len(gone)
        return 0

    def _load_hash_cache_for(self, directory) -> int:
        """Read the persistent cache rows below `directory` into the RAM
        cache, once per folder root (a folder inside an already loaded
        root is covered). Returns how many files were added. Reads only;
        nothing is created or written."""
        if not directory or not self._hash_cache_enabled():
            return 0
        root = os.path.normpath(directory)
        loaded = self.__dict__.setdefault("_hash_cache_roots", set())
        for done in loaded:
            if root == done or root.startswith(done.rstrip(os.sep) + os.sep):
                return 0
        rows = self._hash_store().load_prefix(root)
        loaded.add(root)
        cache = self.__dict__.setdefault("_hash_cache", {})
        added = 0
        for path, (size, mtime, keys) in rows.items():
            mem = cache.get(path)
            if mem is None:
                cache[path] = (size, mtime, dict(keys))
                added += 1
            elif (mem[0], mem[1]) == (size, mtime):
                for k, d in keys.items():
                    mem[2].setdefault(k, d)
        return added

    def _ensure_hash_cache_loaded(self):
        """Load the cache for both panes' folders (e.g. right after the
        setting or the comparison type was switched on)."""
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if pane is None or getattr(pane, "snapshot_file", None) or pane.snapshot_root is None:
                continue
            self._load_hash_cache_for(pane.snapshot_root["full_path"])

    def _reattach_cached_hashes(self) -> int:
        """Give the already built snapshots' files the cached digest for
        the comparison key in effect now, and flag files whose cached entry
        is out of date ("Changed since cached"): RAM only, no disk access.
        For after the key changed, or after the cache was loaded for them.
        Returns how many files were attached or had their flag changed."""
        cache = self.__dict__.get("_hash_cache")
        if not cache:
            return 0
        key = self._comparison_key()
        touched = 0
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if pane is None or getattr(pane, "snapshot_file", None):
                continue
            for norm, node in (pane.snapshot_index or {}).items():
                if node["is_dir"] or self._node_hash(node) is not None:
                    continue
                entry = cache.get(norm)
                if not entry:
                    continue
                if entry[0] == node["size"] and entry[1] == int(node["mtime"]):
                    digest = entry[2].get(key)
                    if digest is not None:
                        node["hash"] = (key, digest)
                        touched += 1
                    if node.pop("changed", None):
                        touched += 1
                elif not node.get("changed"):
                    node["changed"] = True
                    touched += 1
        return touched

    def _on_cache_hashed_folders_toggled(self, checked):
        """Settings > Options > "Cache hashed folders". Switching it on
        (with a hash type selected) reads the cache for the folders
        already open and re-tags from it; switching it off just stops
        reading and writing (the file is left alone)."""
        self._cache_hashed_folders = bool(checked)
        if not checked or not self._hash_type_selected():
            for pane in (self.left, self.right):
                self._set_upper_status(pane, "")
            return
        self._ensure_hash_cache_loaded()
        if self._reattach_cached_hashes() and any(
                getattr(p, "snapshot_root", None) is not None for p in (self.left, self.right)):
            self._invalidate_dir_rank_cache()
            self.update_listings(rescan_disk=False)
        for pane in (self.left, self.right):
            self._report_cache_read(pane)

    def _on_clear_hash_cache(self):
        """Settings > Options > "Clear cache": wipe the persistent file and
        every hash held in RAM, so open folders show as unhashed."""
        answer = QMessageBox.question(
            self, "Clear hash cache",
            "Delete all cached hashes, on disk and in memory?\n\n"
            "Open folders will show as unhashed and must be hashed again.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        removed = self._hash_store().clear()
        self.__dict__["_hash_cache"] = {}
        self.__dict__["_hash_cache_by_inode"] = {}
        self.__dict__["_hash_cache_roots"] = set()
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if pane is None or getattr(pane, "snapshot_file", None):
                continue
            for node in (pane.snapshot_index or {}).values():
                node.pop("hash", None)
                node.pop("changed", None)
        self._invalidate_dir_rank_cache()
        if any(getattr(p, "snapshot_root", None) is not None for p in (self.left, self.right)):
            self.update_listings(rescan_disk=False)
        for pane in (self.left, self.right):
            self._set_upper_status(pane, "Hash cache cleared" if pane.snapshot_root is not None else "")
        self._set_settings_status(f"Hash cache cleared ({removed} file{'s' if removed != 1 else ''} had entries).")

    # ---------- Keeping the cache right when DirComp itself changes files

    def _note_cache_change(self, text):
        """Queue "Hash cache updated ...: <text>" for both panes' upper status
        lines: shown by the next _report_cache_read of each pane (the
        listings are rescanned right after a file operation, which would
        otherwise overwrite it at once)."""
        self.__dict__["_cache_note_pending"] = {
            "text": f"Hash cache updated {time.strftime('%H:%M:%S')}: {text}",
            "t": time.monotonic(),
            "panes": {id(getattr(self, "left", None)), id(getattr(self, "right", None))},
        }

    @staticmethod
    def _plural(n, one, many):
        return f"{n} {one if n == 1 else many}"

    def _cache_files_moved(self, pairs):
        """Files moved by DirComp (src, dest): their hashes follow them, so
        they need not be hashed again. RAM always; the cache file only while
        the cache is in use."""
        norm = os.path.normpath
        pairs = [(norm(s), norm(d)) for s, d in pairs]
        cache = self.__dict__.get("_hash_cache") or {}
        hits = 0
        for src, dst in pairs:
            cache.pop(dst, None)
            entry = cache.pop(src, None)
            if entry is not None:
                cache[dst] = entry
                hits += 1
        if self._hash_cache_enabled():
            self._hash_store().move_paths(pairs)
            if hits:
                self._note_cache_change(self._plural(hits, "hash moved", "hashes moved"))

    def _cache_files_copied(self, pairs):
        """Files copied by DirComp (src, dest): a copy inherits the source's
        hashes if its size and date equal the source's (copy2 keeps the
        date), else it starts unhashed."""
        norm = os.path.normpath
        cache = self.__dict__.get("_hash_cache") or {}
        items = []
        hits = 0
        for src, dst in pairs:
            src, dst = norm(src), norm(dst)
            cache.pop(dst, None)
            try:
                st = os.stat(dst)
            except OSError:
                continue
            items.append((src, dst, st.st_size, int(st.st_mtime)))
            entry = cache.get(src)
            if entry and (entry[0], entry[1]) == (st.st_size, int(st.st_mtime)):
                cache[dst] = (entry[0], entry[1], dict(entry[2]))
                hits += 1
        if self._hash_cache_enabled():
            self._hash_store().copy_rows(items)
            if hits:
                self._note_cache_change(self._plural(hits, "hash copied", "hashes copied"))

    def _cache_path_renamed(self, old, new):
        """A file or folder renamed by DirComp: everything below it re-keys."""
        norm = os.path.normpath
        old, new = norm(old), norm(new)
        cache = self.__dict__.get("_hash_cache") or {}
        old_pre, new_pre = old.rstrip(os.sep) + os.sep, new.rstrip(os.sep) + os.sep
        for key in [k for k in cache if k == new or k.startswith(new_pre)]:
            del cache[key]
        hits = 0
        for key in [k for k in cache if k == old or k.startswith(old_pre)]:
            cache[new + key[len(old):]] = cache.pop(key)
            hits += 1
        if self._hash_cache_enabled():
            self._hash_store().move_tree(old, new)
            if hits:
                self._note_cache_change(self._plural(hits, "hash moved", "hashes moved"))

    def _cache_path_deleted(self, path):
        """A file or folder deleted (sent to the trash) by DirComp."""
        path = os.path.normpath(path)
        cache = self.__dict__.get("_hash_cache") or {}
        pre = path.rstrip(os.sep) + os.sep
        gone = [k for k in cache if k == path or k.startswith(pre)]
        for key in gone:
            del cache[key]
        if self._hash_cache_enabled():
            self._hash_store().delete_tree(path)
            if gone:
                self._note_cache_change(self._plural(len(gone), "hash removed", "hashes removed"))

    def _refresh_pane(self, pane):
        """The pane's "Refresh" button: re-read this pane's folder from disk
        (the other pane is not rescanned, only re-tagged against it). With a
        hash comparison type it also rehashes: after a confirmation, every
        file in the Hash button's scope is hashed again and ALL its stored
        hashes are discarded."""
        if getattr(pane, "snapshot_file", None) or not pane.entry.text():
            return False
        dup_mode = self._duplicate_mode_active() and pane is self.left
        rehash = self._hash_type_selected()
        if rehash:
            scope = ("the files being scanned for duplicates" if dup_mode
                     else "every file in this pane" if self._recursive_folder_contents_enabled()
                     else "the files currently visible in this pane")
            answer = QMessageBox.question(
                self, "Refresh and rehash",
                f"Re-read this pane's folder, discard the stored hashes (every algorithm) of "
                f"{scope} and hash them again?\n\n"
                "Files that now hash differently although their size and date are unchanged "
                "are reported afterwards.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return False
        if dup_mode:
            # Duplicate Finder mode: Refresh starts the duplicate colors over.
            self._dup_reset_colors(pane)
        self.update_listings(initiating_pane=pane, rescan_panes=(pane,))
        self._recount_pane_items_with_indicator(pane)
        if rehash and pane.snapshot_root is not None:
            return self._hash_pane(pane, rehash=True)
        return True

    def _on_main_comparison_type_activated(self, index):
        """The Main tab's comparison type drop-down was used: hand the choice
        to the Settings tab's combo, whose change handler does everything."""
        type_id = self.main_comparison_type_combo.itemData(index)
        target = self.comparison_type_combo.findData(type_id)
        if target >= 0 and self.comparison_type_combo.currentIndex() != target:
            self.comparison_type_combo.setCurrentIndex(target)

    def _sync_main_comparison_combo(self):
        """Settings tab's comparison type changed (or was loaded): show it in
        the Main tab's copy, without triggering it."""
        combo = getattr(self, "main_comparison_type_combo", None)
        index = combo.findData(self.comparison_type_combo.currentData()) if combo is not None else -1
        if combo is not None and index >= 0 and combo.currentIndex() != index:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def _report_cache_read(self, pane):
        """Upper status line after a folder was loaded (or the cache was
        switched on / the key changed): what the hash cache supplied.
        Nothing is written by this. Blank when the cache is not in use."""
        if not self._hash_cache_enabled() or pane.snapshot_root is None or getattr(pane, "snapshot_file", None):
            self._set_upper_status(pane, "")
            return
        pending = self.__dict__.get("_cache_note_pending")
        if pending and id(pane) in pending["panes"]:
            pending["panes"].discard(id(pane))
            if time.monotonic() - pending["t"] < 5:
                self._set_upper_status(pane, pending["text"])
                return
        files = hashed = 0
        for node in (pane.snapshot_index or {}).values():
            if not node["is_dir"]:
                files += 1
                hashed += self._node_hash(node) is not None
        self._set_upper_status(
            pane, f"Hash cache read, nothing written: {hashed} of {files} file{'s' if files != 1 else ''} already hashed")

    def _report_cache_written(self, pane, saved, removed, failed):
        """Upper status line after hashing wrote to the cache."""
        if failed:
            self._set_upper_status(pane, "Could not write the hash cache")
        elif self._hash_cache_enabled() and (saved or removed):
            text = f"Hash cache updated {time.strftime('%H:%M:%S')}: {saved} hash{'es' if saved != 1 else ''} saved"
            if removed:
                text += f", {removed} stale entr{'ies' if removed != 1 else 'y'} removed"
            self._set_upper_status(pane, text)

    def _update_hash_buttons_enabled(self, operation_enabled: bool = True):
        """Hash buttons only work for the hash comparison types (and not
        while another operation runs)."""
        enabled = operation_enabled and self._hash_type_selected()
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            button = getattr(pane, "hash_button", None) if pane is not None else None
            if button is not None:
                button.setEnabled(enabled and not getattr(pane, "snapshot_file", None))
            # Refresh works in every comparison mode (not on a snapshot).
            refresh = getattr(pane, "pane_refresh_button", None) if pane is not None else None
            if refresh is not None:
                refresh.setEnabled(operation_enabled and not getattr(pane, "snapshot_file", None))

            # File Op (Rename / New Folder / Delete) can't act on a snapshot.
            if pane is None:
                continue
            other = self.right if pane is self.left else self.left
            snap = bool(getattr(pane, "snapshot_file", None))
            pane.tree.items_not_draggable = snap
            # Larger/Smaller/Newer/Older mean nothing when files are
            # compared by content.
            blocked = self._hash_comparison_blocked()
            keys = (("missing", "matching", "larger", "smaller", "newer", "older", "diff_folders")
                    if blocked else ("larger", "smaller", "newer", "older"))
            self._dup_update_buttons(pane)
            for key in ("missing", "matching", "larger", "smaller", "newer", "older", "diff_folders"):
                btn = (getattr(pane, "select_toggle_buttons", None) or {}).get(key)
                if btn is not None:
                    off = key in keys and (blocked or self._hash_mode_active())
                    # Larger/Smaller need the size compared, Newer/Older the date.
                    use_size, use_date = self._compare_axes()
                    off = off or (key in ("larger", "smaller") and not use_size) \
                        or (key in ("newer", "older") and not use_date)
                    if off and btn.isChecked():
                        btn.blockSignals(True)
                        btn.setChecked(False)
                        btn.blockSignals(False)
                    btn.setEnabled(not off)
            # Clipboard (incl. its Copy/Move across buttons) too.
            for attr in ("file_op_group", "clipboard_group"):
                group = getattr(pane, attr, None)
                if group is not None:
                    group.setEnabled(not snap)
            # A live pane can't copy/move INTO a snapshot pane either.
            into_snap = bool(getattr(other, "snapshot_file", None)) or self._duplicate_mode_active()
            for b in (getattr(pane, "_across_buttons", None) or {}).values():
                b.setEnabled(not into_snap)
            # The buttons themselves too: in the narrow layout they live in
            # the Controls group instead, so the group-level disable above
            # doesn't reach them.
            own = [getattr(pane, a, None) for a in ("rename_button", "new_folder_button", "delete_button")]
            own += list((getattr(pane, "_clipboard_buttons", None) or {}).values())
            for b in own:
                if b is not None:
                    b.setEnabled(not snap)
            if snap:
                for b in (getattr(pane, "_across_buttons", None) or {}).values():
                    b.setEnabled(False)
        self._update_sync_button_enabled(operation_enabled)
