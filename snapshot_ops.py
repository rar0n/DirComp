"""
SnapshotOpsMixin: folder snapshot files (.fss).

A snapshot is the in-memory metadata tree of a pane (names, sizes, dates and,
if present, hashes -- see DiffEngineMixin._build_snapshot / HashOpsMixin)
saved to a JSON file, and loaded back into a pane later to be compared
against a live folder or another snapshot.

A pane showing a snapshot has no folder on disk. Its "folder" is a virtual
path: the .fss file's own path, with the snapshot's folders below it
(/snaps/x.fss/sub/file). Such a path can never exist on disk or collide with
a live folder's paths, so the existing snapshot index / counterpart / diff
code works on it unchanged; only the few places that asked the filesystem
whether a pane path is a folder go through _isdir/_exists below instead.

File format (version 1), one JSON object:
  format, version, created (ISO time), root_path + root_name (of the
  original folder), recursive (was Recursive Folder Selections on),
  comparison {type, algorithm, chunk_start_kb, chunk_end_kb, hash_key}
  (hash_key = the key the stored hashes belong to, or null: no hashes), and
  tree = one node: {"n": name, "d": 1 if folder, "s": size, "m"/"c"/"a":
  mtime/ctime/atime, "h": hash digest, "k": [children], "ns": 1 +
  "ni": item count for a folder whose contents were not scanned}.
"""

from __future__ import annotations

import datetime
import json
import os

from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QMessageBox, QPushButton,
)

from constants import CMP_FINGERPRINT, CMP_HASH, IS_DIR_ROLE, PATH_ROLE

SNAPSHOT_EXT = ".fss"
_FORMAT = "dircomp-snapshot"
_VERSION = 1
_NOT_SCANNED = "Content not scanned"


def is_snapshot_file_name(path: str) -> bool:
    return bool(path) and str(path).lower().endswith(SNAPSHOT_EXT)


class SnapshotOpsMixin:
    # ------------------------------------------------ Virtual path helpers

    def _pane_snapshot_root_path(self, pane):
        return getattr(pane, "snapshot_file", None)

    def _in_pane_snapshot(self, pane, path) -> bool:
        """True if `path` is the virtual root of `pane`'s loaded snapshot
        or anything below it."""
        root = self._pane_snapshot_root_path(pane)
        if not root or not path:
            return False
        norm = os.path.normpath(path)
        return norm == root or norm.startswith(root + os.sep)

    def _is_snapshot_path(self, path) -> bool:
        return any(self._in_pane_snapshot(p, path)
                   for p in (getattr(self, "left", None), getattr(self, "right", None)) if p is not None)

    def _full_snapshot_node(self, path):
        """The node for `path` in whichever pane's loaded snapshot holds it
        (None if absent there), or False if `path` isn't a snapshot path."""
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if pane is not None and self._in_pane_snapshot(pane, path):
                index = getattr(pane, "snapshot_full_index", None) or {}
                return index.get(os.path.normpath(path))
        return False

    def _isdir(self, path) -> bool:
        """os.path.isdir that also understands a loaded snapshot's virtual
        folders."""
        if not path:
            return False
        node = self._full_snapshot_node(path)
        if node is not False:
            return bool(node and node["is_dir"])
        return os.path.isdir(path)

    def _exists(self, path) -> bool:
        if not path:
            return False
        node = self._full_snapshot_node(path)
        if node is not False:
            return node is not None
        return os.path.exists(path)

    def _snapshot_blocks(self, *panes, what="This") -> bool:
        """True (after telling the user) if any of `panes` shows a
        snapshot: snapshots are read-only, so file operations on or into
        them are refused."""
        for pane in panes:
            if pane is not None and getattr(pane, "snapshot_file", None):
                self._show_warning_status(pane, f"{what} is not available for a snapshot (it is read-only).")
                return True
        return False

    def _snapshot_hashes_ok(self) -> bool:
        """False if a loaded snapshot cannot take part in a hash
        comparison under the current settings (saved without hashes, or
        with other settings): the whole comparison then falls back to name,
        size and date -- see DiffEngineMixin._hash_mode_active."""
        key = tuple(self._comparison_key())
        for pane in (getattr(self, "left", None), getattr(self, "right", None)):
            if pane is not None and getattr(pane, "snapshot_file", None):
                if pane.snapshot_hash_key != key:
                    return False
        return True

    @staticmethod
    def _collect_subindex(node) -> dict:
        index = {}
        stack = [node]
        while stack:
            n = stack.pop()
            index[os.path.normpath(n["full_path"])] = n
            if n["children"]:
                stack.extend(n["children"].values())
        return index

    def _select_snapshot_subtree(self, pane, directory):
        """The (root node, index) for `directory` inside `pane`'s loaded
        snapshot -- what _refresh_pane_snapshot installs for a snapshot
        pane instead of scanning a disk folder."""
        node = pane.snapshot_full_index.get(os.path.normpath(directory))
        if node is None or not node["is_dir"]:
            return None, {}
        return node, self._collect_subindex(node)

    # --------------------------------------------------------- Status line

    def _show_warning_status(self, pane, message: str):
        """A message in the pane's status line in the warning color (Misc
        Colors); the next status message replaces it."""
        label = self._selection_label_for_pane(pane)
        if label is not None:
            label.setStyleSheet(f"color: {self._status_warning_color}; font-weight: bold;")
            label.setText(message)

    def _update_snapshot_label(self, pane):
        """The pane's permanent upper status line (above the entry combo):
        where a loaded snapshot came from, else the last hash cache message
        (see _set_upper_status). Empty text when there is nothing to say."""
        label = getattr(pane, "snapshot_info_label", None)
        if label is None:
            return
        meta = getattr(pane, "snapshot_meta", None)
        if getattr(pane, "snapshot_file", None) and meta:
            label.setText(f"Snapshot of {meta.get('root_path', '?')} "
                          f"(root: {meta.get('root_name', '?')}, saved {meta.get('created', '?')})")
            label.setToolTip(pane.snapshot_file)
        else:
            text, tip = getattr(pane, "upper_status", ("", ""))
            label.setText(text)
            label.setToolTip(tip or text)

    def _set_upper_status(self, pane, text, tip=None):
        """Show `text` on the pane's upper status line (kept in the pane so
        it survives a UI rebuild; a loaded snapshot's own line wins while
        one is shown)."""
        pane.upper_status = (text, tip or text)
        self._update_snapshot_label(pane)

    # ------------------------------------------------------------- Saving

    def _serialize_node(self, node, expanded, recursive, is_root, counts):
        d = {"n": node["name"], "s": node["size"], "m": node["mtime"],
             "c": node["ctime"], "a": node["atime"]}
        if not node["is_dir"]:
            digest = self._node_hash(node)
            if digest is not None:
                d["h"] = digest
                counts["hashed"] += 1
            else:
                counts["unhashed"] += 1
            counts["items"] += 1
            return d
        d["d"] = 1
        counts["items"] += 0 if is_root else 1
        children = node["children"] or {}
        if node.get("not_scanned"):
            d["ns"] = 1
            d["ni"] = node.get("not_scanned_items", 0)
        elif recursive or is_root or os.path.normpath(node["full_path"]) in expanded:
            d["k"] = [self._serialize_node(c, expanded, recursive, False, counts)
                      for c in children.values()]
        else:
            d["ns"] = 1
            d["ni"] = self._count_all_snap(node)
        return d

    def _build_snapshot_document(self, pane):
        """(document dict, counts) for what `pane` currently shows."""
        root = pane.snapshot_root
        expanded = {os.path.normpath(p) for p in self._collect_expanded_paths(pane.tree)}
        recursive = self._recursive_folder_contents_enabled()
        counts = {"hashed": 0, "unhashed": 0, "items": 0}
        tree = self._serialize_node(root, expanded, recursive, True, counts)
        meta = getattr(pane, "snapshot_meta", None)
        if getattr(pane, "snapshot_file", None) and meta:
            rel = os.path.relpath(root["full_path"], pane.snapshot_file)
            root_path = meta["root_path"] if rel == "." else os.path.join(meta["root_path"], rel)
            recursive = meta.get("recursive", recursive) if rel == "." else recursive
        else:
            root_path = root["full_path"]
        key = self._comparison_key() if self._hash_type_selected() and counts["hashed"] else None
        document = {
            "format": _FORMAT, "version": _VERSION,
            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "root_path": root_path, "root_name": os.path.basename(root_path.rstrip(os.sep)) or root_path,
            "recursive": recursive,
            "comparison": {
                "type": self._cmp_type, "algorithm": self._cmp_algorithm,
                "chunk_start_kb": self._cmp_chunk_start_kb, "chunk_end_kb": self._cmp_chunk_end_kb,
                "hash_key": list(key) if key else None,
            },
            "tree": tree,
        }
        return document, counts

    def _on_save_snapshot(self, pane):
        """The pane's "Save Snapshot" button."""
        if pane.snapshot_root is None:
            QMessageBox.information(self, "Save Snapshot", "Open a folder in this pane first.")
            return
        if getattr(self, "active_transfer_progress", None) is not None:
            return
        document, counts = self._build_snapshot_document(pane)
        if (self._hash_type_selected() and self._hash_mode_active() and counts["unhashed"]
                and not getattr(pane, "snapshot_file", None)):
            box = QMessageBox(self)
            box.setWindowTitle("Save Snapshot")
            box.setText(f"{counts['unhashed']} file(s) in this pane have not been hashed yet.")
            hash_btn = box.addButton("Hash now", QMessageBox.AcceptRole)
            box.addButton("Save without hashes", QMessageBox.DestructiveRole)
            cancel_btn = box.addButton(QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn or clicked is None:
                return
            if clicked is hash_btn:
                if not self._hash_pane(pane):
                    return
                document, counts = self._build_snapshot_document(pane)
        start_dir = getattr(self, "_last_snapshot_dir", "") or os.path.expanduser("~")
        default_name = os.path.join(start_dir, document["root_name"] + SNAPSHOT_EXT)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Snapshot", default_name, f"Folder snapshot (*{SNAPSHOT_EXT})")
        if not path:
            return
        if not is_snapshot_file_name(path):
            path += SNAPSHOT_EXT
        self._last_snapshot_dir = os.path.dirname(path)
        if self._write_snapshot_file(path, document):
            self._set_transfer_label(
                f"Snapshot saved: {os.path.basename(path)} ({counts['items']} items)", pane)

    def _write_snapshot_file(self, path, document) -> bool:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(document, f, separators=(",", ":"), ensure_ascii=False)
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Save Snapshot", f"Could not save the snapshot:\n\n{exc}")
            return False
        return True

    # ------------------------------------------------------------ Loading

    def _node_from_dict(self, d, parent_path, index, hash_key, is_root, root_path):
        name = d["n"]
        full_path = root_path if is_root else os.path.join(parent_path, name)
        is_dir = bool(d.get("d"))
        node = {
            "name": name, "full_path": full_path, "is_dir": is_dir,
            "mtime": float(d.get("m", 0)), "ctime": float(d.get("c", 0)), "atime": float(d.get("a", 0)),
            "size": int(d.get("s", 0)), "children": None,
        }
        index[os.path.normpath(full_path)] = node
        if not is_dir:
            if "h" in d and hash_key:
                node["hash"] = (hash_key, d["h"])
            return node
        children = {}
        if d.get("ns"):
            items = int(d.get("ni", 0))
            node["not_scanned"] = True
            node["not_scanned_items"] = items
            dummy_name = f"{_NOT_SCANNED} ({items} items)"
            dummy_path = os.path.join(full_path, dummy_name)
            dummy = {"name": dummy_name, "full_path": dummy_path, "is_dir": False,
                     "mtime": node["mtime"], "ctime": node["ctime"], "atime": node["atime"],
                     "size": 0, "children": None, "placeholder": True}
            index[os.path.normpath(dummy_path)] = dummy
            children[dummy_name] = dummy
        else:
            for child in d.get("k", []):
                c = self._node_from_dict(child, full_path, index, hash_key, False, root_path)
                children[c["name"]] = c
        node["children"] = children
        return node

    def _load_snapshot_into_pane(self, pane, fss_path) -> bool:
        """Read `fss_path` and make `pane` show it (without listing yet --
        the caller's update_listings does that). False, after telling the
        user, if the file can't be used."""
        try:
            with open(fss_path, "r", encoding="utf-8") as f:
                document = json.load(f)
            if document.get("format") != _FORMAT:
                raise ValueError("this is not a DirComp snapshot file")
            if int(document.get("version", 0)) > _VERSION:
                raise ValueError("it was saved by a newer version of DirComp")
            comparison = document.get("comparison", {})
            hash_key = tuple(comparison["hash_key"]) if comparison.get("hash_key") else None
            root_path = os.path.normpath(os.path.abspath(fss_path))
            index = {}
            root = self._node_from_dict(document["tree"], root_path, index, hash_key, True, root_path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Load Snapshot", f"Could not load this snapshot:\n\n{fss_path}\n\n{exc}")
            return False
        pane.snapshot_file = root_path
        pane.snapshot_meta = {
            "root_path": document.get("root_path", "?"), "root_name": document.get("root_name", "?"),
            "created": document.get("created", "?"), "recursive": bool(document.get("recursive", True)),
        }
        pane.snapshot_full_root, pane.snapshot_full_index = root, index
        pane.snapshot_hash_key = hash_key
        self._update_snapshot_label(pane)
        self._update_hash_buttons_enabled()
        self._check_snapshot_comparison(pane, comparison, hash_key)
        return True

    def _check_snapshot_comparison(self, pane, comparison, hash_key):
        """A hash comparison needs the snapshot's hashes to have been made
        with the settings in effect now. If they weren't: offer to adopt
        the snapshot's settings; if declined (or the snapshot has no
        hashes), nothing is compared (see DiffEngineMixin._hash_comparison_blocked)
        and the status line says so in the warning color."""
        if not self._hash_type_selected():
            return
        if hash_key == tuple(self._comparison_key()):
            return
        if hash_key:
            reply = QMessageBox.question(
                self, "Snapshot comparison settings",
                "This snapshot's hashes were made with different comparison settings than the ones "
                "currently selected.\n\nUse the snapshot's settings?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                self._adopt_comparison_settings(comparison)
                return
        self._pending_snapshot_warning = (pane, self._snapshot_mismatch_message(pane))

    def _snapshot_mismatch_message(self, pane) -> str:
        if getattr(pane, "snapshot_hash_key", None):
            return ("Snapshot hashes were made with other comparison settings: "
                    "nothing is compared (shown as hashed, not compared).")
        return "Snapshot has no hashes: nothing is compared."

    def _warn_if_comparison_blocked(self):
        """After a comparison setting changed: say so if a loaded snapshot's
        hashes no longer fit them."""
        if not self._hash_comparison_blocked():
            return
        for pane in (self.left, self.right):
            if getattr(pane, "snapshot_file", None) and pane.snapshot_hash_key != tuple(self._comparison_key()):
                self._show_warning_status(pane, self._snapshot_mismatch_message(pane))
                return

    def _adopt_comparison_settings(self, comparison):
        """Put the snapshot's comparison settings into the Settings-tab
        controls (which re-tags through the usual change handler)."""
        self.comparison_type_combo.setCurrentIndex(int(comparison.get("type", self._cmp_type)))
        self.hash_algorithm_combo.setCurrentIndex(int(comparison.get("algorithm", self._cmp_algorithm)))
        self.fingerprint_start_spin.setValue(int(comparison.get("chunk_start_kb", self._cmp_chunk_start_kb)))
        self.fingerprint_end_spin.setValue(int(comparison.get("chunk_end_kb", self._cmp_chunk_end_kb)))

    def _unload_snapshot(self, pane):
        """The pane stops showing a snapshot (it is about to show a live
        folder, or nothing)."""
        had = getattr(pane, "snapshot_file", None)
        pane.snapshot_file = None
        pane.snapshot_meta = None
        pane.snapshot_full_root = pane.snapshot_full_index = None
        pane.snapshot_hash_key = None
        self._update_snapshot_label(pane)
        if had:
            self._invalidate_dir_rank_cache()
            self._update_hash_buttons_enabled()

    # ---------------------------------------------------- Select Directory

    def _choose_directory_or_snapshot(self, start_dir):
        """File dialog for Select Directory: pick a snapshot file, or
        press "Select this folder" for a folder (the folder shown, or the
        one highlighted). Returns a path or None."""
        dialog = QFileDialog(self, "Select Directory or Snapshot", start_dir)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setFileMode(QFileDialog.ExistingFile)
        dialog.setNameFilters([f"Snapshots (*{SNAPSHOT_EXT})", "All files (*)"])
        chosen = {}
        button = QPushButton("Select this folder")
        button.setToolTip("Choose the folder shown (or highlighted) instead of a file.")

        def pick_folder():
            selected = dialog.selectedFiles()
            target = selected[0] if selected and os.path.isdir(selected[0]) else dialog.directory().absolutePath()
            chosen["path"] = target
            dialog.reject()

        button.clicked.connect(pick_folder)
        layout = dialog.layout()
        if layout is not None:
            row = layout.rowCount() if hasattr(layout, "rowCount") else 0
            try:
                layout.addWidget(button, row, 0, 1, layout.columnCount())
            except TypeError:
                layout.addWidget(button)
        if dialog.exec() == QFileDialog.Accepted:
            files = dialog.selectedFiles()
            return files[0] if files else None
        return chosen.get("path")
