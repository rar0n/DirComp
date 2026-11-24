"""
ContextMenuMixin: the File List View's right-click menu.

    Copy / Copy Metadata  >  name, hash, fingerprint, list data (+ hash) / Cut / Paste
    Paste special  >  Paste Folders
    ------------------
    Open
    Open with...            (Linux: sub-menu of installed apps; Windows: the shell's dialog)
    ------------------
    Properties

Copy/Cut reuse TransferMixin's Clipboard-group handlers on the tree's
current selection. Paste / Paste Folders target where the right-click
happened: a folder pastes into itself, a file into the folder it lives
in, empty space into the pane's root folder. With several items selected
that can mean several locations -- Paste Folders then asks for
confirmation and pastes into each; plain Paste is disabled in that case
(a Cut can only land in one place).

Right-clicking an unselected item selects just that item first; right-
clicking an already-selected item keeps the whole selection.
"""

import configparser
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import QMimeDatabase, QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox

from constants import SIZE_STYLE, format_bytes, CMP_FINGERPRINT, CMP_HASH, GNOME_COPIED_FILES_MIME, HASH_ALGORITHMS, IS_DIR_ROLE, IS_WINDOWS, PATH_ROLE
from panes import DirectoryPane

# The roadmap asks for a confirmation before launching files. Flip to
# False to open straight away.
CONFIRM_BEFORE_OPEN = True


def _human_size(n: int) -> str:
    return f"{n} B" if n < (1024 if SIZE_STYLE["binary"] else 1000) else format_bytes(n)


# ------------------------------------------------------------------
# Linux "Open with": which installed apps handle a file's MIME type.
# Pure Python (mimeinfo.cache + .desktop files), no PyGObject needed.
# ------------------------------------------------------------------
_APP_INDEX = None  # (mime -> [desktop ids], desktop id -> (name, exec)); built lazily


def _application_dirs() -> list:
    return QStandardPaths.standardLocations(QStandardPaths.ApplicationsLocation)


def _read_ini(path: str, section_names=None):
    cp = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",))
    cp.optionxform = str  # MIME types / keys are case-sensitive
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    return cp


def _build_app_index():
    mime_to_ids = {}

    def add(mime, ids_str):
        lst = mime_to_ids.setdefault(mime, [])
        for i in ids_str.split(";"):
            i = i.strip()
            if i and i not in lst:
                lst.append(i)

    # User associations first (they should sort ahead of the system list).
    config_dir = QStandardPaths.writableLocation(QStandardPaths.GenericConfigLocation)
    cp = _read_ini(os.path.join(config_dir, "mimeapps.list"))
    if cp is not None:
        for section in ("Default Applications", "Added Associations"):
            if cp.has_section(section):
                for mime, ids in cp.items(section):
                    add(mime, ids)
    for d in _application_dirs():
        cp = _read_ini(os.path.join(d, "mimeinfo.cache"))
        if cp is not None and cp.has_section("MIME Cache"):
            for mime, ids in cp.items("MIME Cache"):
                add(mime, ids)

    apps = {}
    for ids in mime_to_ids.values():
        for desktop_id in ids:
            if desktop_id in apps:
                continue
            apps[desktop_id] = None
            for d in _application_dirs():
                f = os.path.join(d, desktop_id)
                if not os.path.isfile(f):
                    continue
                cp = _read_ini(f)
                if cp is None or not cp.has_section("Desktop Entry"):
                    continue
                entry = cp["Desktop Entry"]
                if entry.get("Type", "Application") != "Application":
                    continue
                if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
                    continue
                name, exec_line = entry.get("Name"), entry.get("Exec")
                if name and exec_line:
                    apps[desktop_id] = (name, exec_line)
                break
    return mime_to_ids, apps


def _apps_for_paths(paths):
    """[(name, exec_line)] of apps that can open *every* path (exact MIME
    type first, then its parent types), in the system's own order."""
    global _APP_INDEX
    if _APP_INDEX is None:
        _APP_INDEX = _build_app_index()
    mime_to_ids, apps = _APP_INDEX
    db = QMimeDatabase()
    per_path = []
    for p in paths:
        mt = db.mimeTypeForFile(p)
        ids = []
        for name in [mt.name()] + list(mt.allAncestors()):
            for i in mime_to_ids.get(name, []):
                if i not in ids:
                    ids.append(i)
        per_path.append(ids)
    if not per_path:
        return []
    common = [i for i in per_path[0] if all(i in other for other in per_path[1:])]
    return [apps[i] for i in common if apps.get(i)]


def _expand_exec(exec_line: str, paths):
    """Turn a .desktop Exec= line into a list of argv lists (one launch if
    the app takes %F/%U, else one launch per path). Unknown field codes
    (%i, %c, %k...) are dropped."""
    base = shlex.split(exec_line)
    multi = any(t in ("%F", "%U") for t in base)
    wants_url = any(t in ("%u", "%U") for t in base)

    def one(args):
        argv = []
        for tok in base:
            if tok in ("%f", "%u"):
                argv.append(args[0])
            elif tok in ("%F", "%U"):
                argv.extend(args)
            elif len(tok) == 2 and tok[0] == "%" and tok != "%%":
                continue
            else:
                argv.append(re.sub(
                    r"%(.)",
                    lambda m: "%" if m.group(1) == "%" else (args[0] if m.group(1) in "fu" else ""),
                    tok,
                ))
        return argv

    conv = (lambda p: QUrl.fromLocalFile(p).toString(QUrl.FullyEncoded)) if wants_url else (lambda p: p)
    if multi:
        return [one([conv(p) for p in paths])]
    return [one([conv(p)]) for p in paths]


def _clean_env() -> dict:
    """The environment for programs started from here, without the Python
    virtual environment DirComp itself runs in: an editor or viewer that has
    its own Python plugins (xed, for one) would otherwise pick up the venv's
    interpreter, which lacks system modules such as `gi`, and warn that its
    plugins failed to load."""
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None)
    env.pop("VIRTUAL_ENV_PROMPT", None)
    env.pop("PYTHONHOME", None)
    if venv:
        prefix = os.path.normpath(venv)
        parts = [p for p in env.get("PATH", "").split(os.pathsep)
                 if p and not os.path.normpath(p).startswith(prefix)]
        env["PATH"] = os.pathsep.join(parts)
    return env


def _spawn(argv):
    subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, env=_clean_env(),
    )


def _open_default(path: str) -> bool:
    """Open `path` with its default program. On Linux through xdg-open in a
    clean environment (see _clean_env); elsewhere, or if xdg-open is missing,
    through Qt."""
    if sys.platform.startswith("linux"):
        opener = shutil.which("xdg-open", path=_clean_env().get("PATH"))
        if opener:
            try:
                _spawn([opener, path])
                return True
            except OSError:
                pass
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class ContextMenuMixin:
    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
    def _clipboard_has_files(self) -> bool:
        mime = QApplication.clipboard().mimeData()
        return bool(mime and (mime.hasUrls() or mime.hasFormat(GNOME_COPIED_FILES_MIME)))

    def _paste_targets_for_selection(self, pane: DirectoryPane, clicked_item, selected) -> list:
        """Folders a paste should go into: each selected folder itself, each
        selected file's parent folder (deduplicated, in selection order);
        the pane's root when the click landed on empty space."""
        root = pane.entry.text()
        if clicked_item is None:
            return [root] if root else []
        targets = []
        for it in selected:
            p = it.data(0, PATH_ROLE)
            if not p:
                continue
            d = p if it.data(0, IS_DIR_ROLE) else os.path.dirname(p)
            if d and d not in targets:
                targets.append(d)
        return targets or ([root] if root else [])

    def _show_flv_context_menu(self, pane: DirectoryPane, pos):
        # Same rule as the other list-clearing controls: nothing new may
        # start while a transfer/search/recount is in flight.
        if getattr(self, "active_transfer_progress", None) is not None:
            return
        tree = pane.tree
        clicked = tree.itemAt(pos)
        if clicked is not None and not clicked.isSelected():
            tree.clearSelection()
            clicked.setSelected(True)
        selected = tree.selectedItems()
        # Plain strings from here on: a paste/refresh rebuilds the tree
        # and would leave any QTreeWidgetItem reference dangling.
        paths = [p for p in (it.data(0, PATH_ROLE) for it in selected) if p]
        targets = self._paste_targets_for_selection(pane, clicked, selected)
        # A snapshot's items don't exist on disk: no clipboard, open or
        # properties actions for them.
        has_sel = bool(paths) and not getattr(pane, "snapshot_file", None)
        can_paste = self._clipboard_has_files() and not getattr(pane, "snapshot_file", None)

        handlers = {}
        menu = QMenu(tree)

        def add(target_menu, text, handler, enabled=True):
            act = target_menu.addAction(text)
            act.setEnabled(enabled)
            handlers[act] = handler
            return act

        add(menu, "Open", lambda: self._open_paths(paths), has_sel)
        if IS_WINDOWS or not has_sel:
            add(menu, "Open with...", lambda: self._open_with_windows(paths), has_sel)
        else:
            open_with = menu.addMenu("Open with...")
            self._fill_open_with_menu(open_with, paths, add)
        menu.addSeparator()

        add(menu, "Copy", lambda: self.copy_to_clipboard(pane), has_sel)
        # Copy Metadata: the right-clicked item's name, size and dates (as
        # listed in the file list), and a file's stored hash / fingerprint,
        # as text. Each entry is enabled only if that value exists (the size
        # and the digests only for files; the digests only if already known).
        clicked_path = clicked.data(0, PATH_ROLE) if clicked is not None else None
        node = self._snapshot_lookup(clicked_path, pane) if clicked_path else None
        is_file = node is not None and not node["is_dir"]
        file_digests = {}
        if is_file:
            file_digests = {kind: self._stored_digest_info(clicked_path, kind) for kind in (CMP_HASH, CMP_FINGERPRINT)}

        copy_metadata = menu.addMenu("Copy Metadata")
        # All selected rows' names, one per line -- the same rows (Recursive
        # Folder Selections, File Filter) as the list-data entries below.
        add(copy_metadata, "Copy File Name",
            lambda: self._copy_flv_data(pane, False, names_only=True), bool(selected))
        selected_kind = getattr(self, "_cmp_type", None)
        for kind, label in ((CMP_HASH, "Copy Hash"), (CMP_FINGERPRINT, "Copy Fingerprint")):
            info = file_digests.get(kind)
            add(copy_metadata, label, lambda k=kind, i=info: self._copy_digest(pane, clicked_path, k, i),
                info is not None)
        copy_metadata.addSeparator()
        add(copy_metadata, "Copy File List View Data",
            lambda: self._copy_flv_data(pane, False), bool(selected))
        add(copy_metadata, "Copy File List View Data + Hash / Fingerprint",
            lambda: self._copy_flv_data(pane, True),
            bool(selected) and selected_kind in (CMP_HASH, CMP_FINGERPRINT))
        add(menu, "Cut", lambda: self.cut_to_clipboard(pane), has_sel)
        add(menu, "Paste", lambda: self.paste_from_clipboard(pane, targets[0]),
            can_paste and len(targets) == 1)
        special = menu.addMenu("Paste special")
        add(special, "Paste Folders",
            lambda: self.paste_folders_from_clipboard(pane, list(targets)),
            can_paste and bool(targets))

        menu.addSeparator()
        add(menu, "Properties", lambda: self._show_properties(paths), has_sel)

        chosen = menu.exec(tree.viewport().mapToGlobal(pos))
        handler = handlers.get(chosen)
        if handler is not None:
            handler()

    # ------------------------------------------------------------------
    # Copy File List View Data
    # ------------------------------------------------------------------
    def _copy_flv_data(self, pane, with_digests: bool, names_only: bool = False):
        """(names_only: just the names, one per line, no indentation.)
        The selected rows as plain text, one per line, in list order:
        Name, Size, Type and the date column the pane's Statistics-view
        Metric picks (tab separated). Each level of nesting below the
        shallowest selected row indents the name by 2 spaces. With
        `with_digests`, a file's already-known hash / fingerprint is
        appended as a last column -- only the
        type selected as the comparison type, "-- Not calculated --" where
        the file has no value yet (nothing is computed here). For a
        fingerprint, an info line at the top gives algorithm (and chunk
        sizes). Follows Recursive Folder Selections (on: a selected
        folder brings its whole contents, de-duplicated against other
        selected rows; off: only the selected rows) and the File Filter /
        List Files / List Directories settings."""
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        from listing_ops import COL_COUNT, COL_NAME, COL_SIZE, COL_TYPE, _DATE_FIELD_TO_COLUMN, _DATE_FIELD_TO_ENTRY_KEY
        tree = pane.tree
        date_field = self._pane_diff_date_field(pane)
        date_col = _DATE_FIELD_TO_COLUMN.get(date_field, 3)
        entry_key = _DATE_FIELD_TO_ENTRY_KEY.get(date_field, "date_str")
        recursive = self._recursive_folder_contents_enabled()
        rows = []  # (depth, item) in visual (tree) order
        it = QTreeWidgetItemIterator(tree, QTreeWidgetItemIterator.Selected)
        while it.value():
            item = it.value()
            depth, parent = 0, item.parent()
            while parent is not None:
                depth += 1
                parent = parent.parent()
            rows.append((depth, item))
            it += 1
        if not rows:
            return
        base = min(d for d, _ in rows)
        # The Duplicate Finder's "Unique files only" list shows a Count
        # column (right after Size); include it where it is visible.
        show_count = not tree.isColumnHidden(COL_COUNT)
        right_cols = {1, 2} if show_count else {1}
        info_line = None
        records = []  # (depth, [columns]) -- laid out at the end, per Settings
        digest_kind = getattr(self, "_cmp_type", None) if with_digests else None
        if digest_kind not in (CMP_HASH, CMP_FINGERPRINT):
            digest_kind = None
        if digest_kind == CMP_FINGERPRINT:
            key = self._hash_key_for(CMP_FINGERPRINT)
            info_line = (f"Fingerprint: {HASH_ALGORITHMS[key[1]]}, file size + first {key[2]} kB + last {key[3]} kB")
        elif digest_kind == CMP_HASH:
            info_line = f"Hash: {HASH_ALGORITHMS[self._hash_key_for(CMP_HASH)[1]]}"

        names = []
        dup_hashable = None
        if digest_kind is not None and pane is self.left and self._duplicate_mode_active():
            dup_hashable = {n["full_path"] for n in self._dup_hash_nodes(pane)}

        def add_line(depth, name, size, type_, date, path, is_dir, count=""):
            if names_only:
                names.append(name)
                return
            cols = [name, size, count, type_, date] if show_count else [name, size, type_, date]
            if digest_kind is not None and path and not is_dir:
                key = self._hash_key_for(digest_kind)
                info = self._stored_digest_info(path, digest_kind)
                if info is not None and tuple(info[0]) == key:
                    cols.append(info[1])
                else:
                    # Ignored files (Ignore empty files / hard links) are never
                    # hashed, so say that rather than "not calculated".
                    ignored = self._ignored_kind(self._snapshot_lookup(path, pane))
                    text = {"empty": "-- Ignored (empty file) --",
                            "hardlink": "-- Ignored (hard link) --"}.get(ignored, "-- Not calculated --")
                    # Duplicate Finder mode only hashes files whose size another
                    # file in scope shares (Hash only same-size files).
                    if text == "-- Not calculated --" and dup_hashable is not None and path not in dup_hashable:
                        text = "-- Not hashed (unique size) --"
                    cols.append(text)
            records.append((depth, cols))

        # With Recursive Folder Selections on, a selected folder stands for
        # everything inside it (as for Copy and the Select group), so its
        # whole contents are listed from the snapshot -- through the same
        # List Files/Directories + File Filter rules the list itself uses --
        # even where the folder is collapsed. Anything already listed that
        # way is skipped when it turns up again as a selected row.
        emitted = set()
        for depth, item in rows:
            path = item.data(0, PATH_ROLE)
            if path in emitted:
                continue
            is_dir = bool(item.data(0, IS_DIR_ROLE))
            add_line(depth - base, item.text(COL_NAME), item.text(COL_SIZE), item.text(COL_TYPE),
                     item.text(date_col), path, is_dir, item.text(COL_COUNT))
            if not (recursive and is_dir and path):
                continue
            # explicit stack (not recursion): very deep trees; entries are
            # pushed reversed so they pop in list order, depth-first
            stack = [(depth - base + 1, e) for e in reversed(self._build_entries(path, pane=pane) or [])]
            while stack:
                d, e = stack.pop()
                emitted.add(e["full_path"])
                count = e.get("dup_count")
                add_line(d, e["name"], e["size_str"], e["type_str"], e[entry_key],
                         e["full_path"], e["is_dir"], "" if count is None else str(count))
                if e["is_dir"]:
                    stack.extend((d + 1, c) for c in reversed(self._build_entries(e["full_path"], pane=pane) or []))
        if names_only:
            QApplication.clipboard().setText("\n".join(names))
            n = len(names)
            self._set_upper_status(pane, f"Copied {n} file name{'s' if n != 1 else ''}")
            return
        nice = bool(getattr(self, "_ctx_sep_spaces", False) and getattr(self, "_ctx_nice_format", False))
        header = None
        if nice:
            head = tree.headerItem()
            header = [head.text(COL_NAME), head.text(COL_SIZE)]
            if show_count:
                header.append("Count")
            header += [head.text(COL_TYPE), head.text(date_col)]
            if digest_kind is not None:
                word = "Fingerprint" if digest_kind == CMP_FINGERPRINT else "Hash"
                header.append(word)
        lines = self._format_flv_records(records, right_cols, header)
        if info_line:
            lines[0:0] = [info_line, ""] if nice else [info_line]
        QApplication.clipboard().setText("\n".join(lines))
        n = len(records)
        self._set_upper_status(pane, f"Copied list data of {n} item{'s' if n != 1 else ''}")

    def _format_flv_records(self, records, right_cols=frozenset({1}), header=None) -> list:
        """Lay out (depth, columns) records as text lines per Settings ->
        Context Menu -> Copy File List formatting: tab-separated with no
        indentation, or spaces (folder indentation per depth, column
        separation spaces, optionally padded so the columns line up, the Size column right-aligned)."""
        if not getattr(self, "_ctx_sep_spaces", False):
            return ["\t".join(cols) for _, cols in records]
        gap = " " * max(1, int(getattr(self, "_ctx_col_spaces", 2)))
        indent = max(0, int(getattr(self, "_ctx_indent_spaces", 2)))
        rows = [[" " * (indent * depth) + cols[0], *cols[1:]] for depth, cols in records]
        if not getattr(self, "_ctx_nice_format", False):
            return [gap.join(r) for r in rows]
        if header is not None and 2 in right_cols:
            # no room for "Count" over narrow numbers: shorten it
            if max((len(r[2]) for r in rows if len(r) > 2), default=0) < len("Count"):
                header[2] = "Cnt"
        if header is not None:
            rows.insert(0, list(header))
        widths = []
        for r in rows:
            for i, cell in enumerate(r):
                if i == len(widths):
                    widths.append(0)
                widths[i] = max(widths[i], len(cell))
        lines = [
            (gap.join((cell.rjust if i in right_cols else cell.ljust)(widths[i]) for i, cell in enumerate(r[:-1])) + gap + r[-1]
             if len(r) > 1 else r[0])
            for r in rows
        ]
        if header is not None:
            lines.insert(1, "-" * max(len(x) for x in lines))
        return lines

    # ------------------------------------------------------------------
    # Open / Open with
    # ------------------------------------------------------------------
    def _open_paths(self, paths):
        if not paths:
            return
        if CONFIRM_BEFORE_OPEN:
            what = f"'{os.path.basename(paths[0])}'" if len(paths) == 1 else f"{len(paths)} items"
            answer = QMessageBox.question(
                self, "Open", f"Open {what} with the default program?",
                QMessageBox.Open | QMessageBox.Cancel, QMessageBox.Open,
            )
            if answer != QMessageBox.Open:
                return
        failed = [p for p in paths if not _open_default(p)]
        if failed:
            QMessageBox.warning(self, "Open", "Could not open:\n" + "\n".join(failed))

    def _fill_open_with_menu(self, menu, paths, add):
        try:
            apps = _apps_for_paths(paths)
        except Exception:  # a malformed .desktop file must never break the menu
            apps = []
        for name, exec_line in apps:
            add(menu, name.replace("&", "&&"), lambda e=exec_line: self._launch_with_exec(e, paths))
        if apps:
            menu.addSeparator()
        add(menu, "Choose another application...", lambda: self._choose_application(paths))

    def _launch_with_exec(self, exec_line, paths):
        try:
            for argv in _expand_exec(exec_line, paths):
                _spawn(argv)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Open with", f"Could not start the application:\n{e}")

    def _choose_application(self, paths):
        exe, _ = QFileDialog.getOpenFileName(self, "Choose an application", "/usr/bin")
        if not exe:
            return
        try:
            _spawn([exe, *paths])
        except OSError as e:
            QMessageBox.warning(self, "Open with", f"Could not start the application:\n{e}")

    def _open_with_windows(self, paths):
        # The shell's own "How do you want to open this file?" dialog; it
        # takes one file at a time.
        try:
            for p in paths:
                subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", os.path.normpath(p)])
        except OSError as e:
            QMessageBox.warning(self, "Open with", f"Could not show the dialog:\n{e}")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    def _show_properties(self, paths):
        if not paths:
            return
        shown = False
        if IS_WINDOWS:
            shown = len(paths) == 1 and self._windows_properties(paths[0])
        elif sys.platform != "darwin":
            shown = self._freedesktop_properties(paths)
        if not shown:
            self._builtin_properties(paths)

    def _freedesktop_properties(self, paths) -> bool:
        """org.freedesktop.FileManager1.ShowItemProperties -- implemented by
        Nemo, Nautilus and Dolphin. Called through `gdbus` so the array-of-
        strings argument is typed correctly."""
        def q(s):
            return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
        uris = "[" + ", ".join(q(QUrl.fromLocalFile(p).toString(QUrl.FullyEncoded)) for p in paths) + "]"
        try:
            result = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.freedesktop.FileManager1",
                 "--object-path", "/org/freedesktop/FileManager1",
                 "--method", "org.freedesktop.FileManager1.ShowItemProperties",
                 uris, ""],
                capture_output=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _windows_properties(self, path) -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            class SHELLEXECUTEINFOW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE),
                ]

            SEE_MASK_INVOKEIDLIST = 0x0000000C
            info = SHELLEXECUTEINFOW()
            info.cbSize = ctypes.sizeof(info)
            info.fMask = SEE_MASK_INVOKEIDLIST
            info.lpVerb = "properties"
            info.lpFile = os.path.normpath(path)
            info.nShow = 1
            return bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)))
        except Exception:
            return False

    def _builtin_properties(self, paths):
        """Fallback when no OS dialog is available (no file manager on the
        session bus, multi-selection on Windows...)."""
        db = QMimeDatabase()

        def fmt(ts):
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

        if len(paths) == 1:
            p = paths[0]
            try:
                st = os.stat(p)
            except OSError as e:
                QMessageBox.warning(self, "Properties", f"{p}\n\n{e}")
                return
            is_dir = os.path.isdir(p)
            lines = [
                f"Name: {os.path.basename(p)}",
                f"Location: {os.path.dirname(p)}",
                f"Type: {'Folder' if is_dir else db.mimeTypeForFile(p).comment()}",
                f"Size: {'-' if is_dir else _human_size(st.st_size)}",
                f"Modified: {fmt(st.st_mtime)}",
                f"Accessed: {fmt(st.st_atime)}",
                f"{'Created' if IS_WINDOWS else 'Changed (ctime)'}: {fmt(st.st_ctime)}",
            ]
            QMessageBox.information(self, "Properties", "\n".join(lines))
            return
        folders = sum(1 for p in paths if os.path.isdir(p))
        files = len(paths) - folders
        total = 0
        for p in paths:
            if not os.path.isdir(p):
                try:
                    total += os.stat(p).st_size
                except OSError:
                    pass
        QMessageBox.information(
            self, "Properties",
            f"{len(paths)} items selected\n{files} file(s), {folders} folder(s)\n"
            f"Total size of the files: {_human_size(total)}",
        )
