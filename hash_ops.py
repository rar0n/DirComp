"""
HashOpsMixin: computing file hashes for the Hash value / Fingerprint
comparison types, and the per-pane "Hash" button that does it.

Hashes live on the in-memory snapshot's file nodes as node["hash"] =
(comparison_key, hex digest) -- see DiffEngineMixin._node_hash, which only
honors one made under the comparison settings in effect now. Because a
snapshot is rebuilt from scratch on every Refresh, every digest is also
kept in self._hash_cache {normalized path: (size, int mtime, key, digest)},
and DiffEngineMixin._snapshot_node re-attaches it while rebuilding, as long
as the file's size and mtime still match. Pairing files by hash (the actual
comparison) is in diff_engine.py.
"""

from __future__ import annotations

import hashlib
import os
import time

from PySide6.QtWidgets import QApplication

from constants import CMP_FINGERPRINT, HASH_ALGORITHMS

_CHUNK = 1024 * 1024
_UNIT = 4096  # progress bar unit (bytes): keeps the bar's int range safe for huge totals

_ALGORITHM_FACTORIES = {
    "MD5": hashlib.md5, "SHA-1": hashlib.sha1, "SHA-256": hashlib.sha256,
    "BLAKE2b": hashlib.blake2b,
}


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

    def _files_to_hash(self, pane, progress):
        """Snapshot file nodes the Hash button should look at: every file
        in the pane with Recursive Folder Selections on; otherwise only
        those currently visible (top level plus expanded folders)."""
        nodes = []
        if self._recursive_folder_contents_enabled():
            stack = [pane.snapshot_root]
            while stack:
                node = stack.pop()
                if not progress.tick():
                    return nodes
                if node["is_dir"]:
                    stack.extend((node["children"] or {}).values())
                else:
                    nodes.append(node)
        else:
            from constants import IS_DIR_ROLE, PATH_ROLE
            for item in self._iter_visible_items(pane.tree):
                if not progress.tick():
                    return nodes
                if item.data(0, IS_DIR_ROLE):
                    continue
                node = self._snapshot_lookup(item.data(0, PATH_ROLE))
                if node is not None and not node["is_dir"]:
                    nodes.append(node)
        return nodes

    def _hash_pane(self, pane):
        """The pane's "Hash" button: hash its not-yet-hashed files under
        the current comparison type. Enumerates first (busy bar), then
        shows progress by bytes; the pane's Cancel button stops it and
        keeps what was done. Afterwards both panes are re-tagged."""
        if not self._hash_mode_active() or pane.snapshot_root is None:
            return
        key = self._comparison_key()
        progress = self._start_transfer_progress(total=None, label="Enumerating...", pane=pane)
        hashed = failed = 0
        try:
            nodes = self._files_to_hash(pane, progress)
            todo = [n for n in nodes if self._node_hash(n) is None]
            if not progress.cancelled and todo:
                total_units = sum(self._hash_cost(n["size"]) for n in todo) // _UNIT + 1
                progress.start_phase(total_units)
                progress._on_tick = None
                label = getattr(self, "_active_status_label", None)
                carry = 0
                last_label = 0.0

                def on_bytes(n):
                    nonlocal carry
                    carry += n
                    units, carry = divmod(carry, _UNIT)
                    return progress.advance(units) if units else progress.pump()

                cache = self.__dict__.setdefault("_hash_cache", {})
                for i, node in enumerate(todo, 1):
                    if label is not None and time.monotonic() - last_label > 0.25:
                        last_label = time.monotonic()
                        label.setText(f"Hashing {i}/{len(todo)}")
                        QApplication.processEvents()
                    digest = self._compute_file_hash(node["full_path"], node["size"], on_bytes)
                    if progress.cancelled:
                        break
                    if digest is None:
                        failed += 1
                        continue
                    node["hash"] = (key, digest)
                    cache[os.path.normpath(node["full_path"])] = (
                        node["size"], int(node["mtime"]), key, digest)
                    hashed += 1
        finally:
            self._finish_transfer_progress(result_count=hashed)
        if failed:
            self._set_transfer_label(f"{hashed} hashed, {failed} could not be read")
        self._invalidate_dir_rank_cache()
        self.update_listings(rescan_disk=False)

    def _update_hash_buttons_enabled(self, operation_enabled: bool = True):
        """Hash buttons only work for the hash comparison types (and not
        while another operation runs)."""
        enabled = operation_enabled and self._hash_mode_active()
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            button = getattr(pane, "hash_button", None) if pane is not None else None
            if button is not None:
                button.setEnabled(enabled)
