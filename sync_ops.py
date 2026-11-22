"""
FolderSyncMixin: the Both Panes group's "Sync" button -- makes the two
panes' folders equal by copying, in both directions, whatever the other
side lacks or has an older version of (newest Modified date wins).

Nothing is ever deleted. A file that is overwritten is first sent to the
trash (send2trash, as Delete does), so every change can be undone from
there. Before anything happens a full overview is shown (see
_sync_confirm) with a warning, the number and size of the copies in each
direction, the files that will go to the trash, the conflicts that will be
skipped and -- while a File Filter is active -- how many items it leaves
out; Cancel is the default answer.

Rules (build_sync_plan, a plain function on two snapshot trees, no Qt):
 - Items are paired by relative path.
 - Only the Modified date decides which of two files is newer (whole
   seconds, like the comparison itself). Equal dates: nothing is copied --
   unless the sizes differ, which is reported as a conflict.
 - A file/folder name that is a file on one side and a folder on the other
   is a conflict; so is an unreadable folder. Conflicts are skipped.
 - A folder missing on one side is created there, with its contents.
 - An active File Filter limits what is copied to the files whose names
   pass it (existence on the other side is judged without the filter, so a
   filtered-out file is never mistaken for a missing one); folders
   that would only receive filtered-out files are not created.
 - Both folders are scanned afresh when Sync is pressed, so the plan never
   rests on a stale listing.
"""

import os
import shutil

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QStyle,
                               QVBoxLayout)

from trash_compat import send2trash, SEND2TRASH_AVAILABLE

_LR, _RL = "lr", "rl"
_MAX_DETAIL_LINES = 400


def build_sync_plan(left_root, right_root, name_passes=None):
    """Plan syncing two snapshot trees (see DiffEngineMixin._build_snapshot).
    `name_passes(file_name) -> bool` is the File Filter (None = no filter).

    Returns {"ops": [...], "mkdirs": [...], "conflicts": [...], "filtered": n}:
      ops       {"src", "dest", "size", "kind": "new"|"update", "dir": "lr"|"rl"}
      mkdirs    (path, dir) -- empty folders to create (no filter only)
      conflicts (display path, reason)
      filtered  files that would have been copied but fail the File Filter"""
    plan = {"ops": [], "mkdirs": [], "conflicts": [], "filtered": 0}
    ops, conflicts = plan["ops"], plan["conflicts"]
    filter_active = name_passes is not None

    def passes(name):
        return name_passes(name) if filter_active else True

    def add_op(src_node, dest_path, kind, direction):
        if not passes(src_node["name"]):
            plan["filtered"] += 1
            return
        ops.append({"src": src_node["full_path"], "dest": dest_path, "size": src_node["size"],
                    "kind": kind, "dir": direction})

    def copy_missing(node, dest_path, direction):
        """`node` (a file or folder) has no counterpart: copy all of it."""
        stack = [(node, dest_path)]
        while stack:
            current, dest = stack.pop()
            if not current["is_dir"]:
                add_op(current, dest, "new", direction)
                continue
            if current.get("scan_failed"):
                conflicts.append((current["full_path"], "Unreadable folder"))
            if not filter_active:
                plan["mkdirs"].append((dest, direction))
            for name, child in (current["children"] or {}).items():
                stack.append((child, os.path.join(dest, name)))

    if left_root is None or right_root is None:
        return plan
    stack = [(left_root, right_root)]
    while stack:
        left, right = stack.pop()
        if left.get("scan_failed") or right.get("scan_failed"):
            bad = left if left.get("scan_failed") else right
            conflicts.append((bad["full_path"], "Unreadable folder"))
            continue
        left_children, right_children = left["children"] or {}, right["children"] or {}
        for name in set(left_children) | set(right_children):
            l_node, r_node = left_children.get(name), right_children.get(name)
            if r_node is None:
                copy_missing(l_node, os.path.join(right["full_path"], name), _LR)
            elif l_node is None:
                copy_missing(r_node, os.path.join(left["full_path"], name), _RL)
            elif l_node["is_dir"] and r_node["is_dir"]:
                stack.append((l_node, r_node))
            elif l_node["is_dir"] != r_node["is_dir"]:
                conflicts.append((l_node["full_path"], "A file on one side, a folder on the other"))
            else:
                l_time, r_time = int(l_node["mtime"]), int(r_node["mtime"])
                if l_time > r_time:
                    add_op(l_node, r_node["full_path"], "update", _LR)
                elif r_time > l_time:
                    add_op(r_node, l_node["full_path"], "update", _RL)
                elif l_node["size"] != r_node["size"]:
                    conflicts.append((l_node["full_path"], "Same date, different size"))
    return plan


def _plan_new_dirs(plan, direction) -> int:
    """How many folders executing `plan` creates on the side that receives
    `direction`'s copies (counting the missing parents of every copy)."""
    dirs = {path for path, d in plan["mkdirs"] if d == direction}
    for op in plan["ops"]:
        if op["dir"] != direction:
            continue
        parent = os.path.dirname(op["dest"])
        while parent and not os.path.isdir(parent) and parent not in dirs:
            dirs.add(parent)
            parent = os.path.dirname(parent)
    return len(dirs)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


class FolderSyncMixin:
    # --------------------------------------------------------- Enabling

    def _sync_unavailable_reason(self) -> str:
        """Why Sync can't run right now ("" = it can)."""
        if self._duplicate_mode_active():
            return "Sync needs two folders -- it is not available in Duplicate Finder mode."
        if not SEND2TRASH_AVAILABLE:
            return ("Sync sends overwritten files to the trash, which needs the 'send2trash' "
                    "package (pip install send2trash).")
        for pane in (self.left, self.right):
            if getattr(pane, "snapshot_file", None):
                return "Sync can't work on a loaded snapshot (.fss file)."
        left, right = self.left.entry.text().strip(), self.right.entry.text().strip()
        if not (left and right and os.path.isdir(left) and os.path.isdir(right)):
            return "Open a folder in each pane first."
        a, b = _norm(left), _norm(right)
        if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
            return "The two folders are the same, or one is inside the other."
        return ""

    def _update_sync_button_enabled(self, operation_enabled: bool = True):
        button = getattr(self, "sync_button", None)
        if button is None:
            return
        try:
            reason = self._sync_unavailable_reason() if operation_enabled else ""
            button.setEnabled(operation_enabled and not reason)
            button.setToolTip(self._SYNC_TOOLTIP + (f"\n\nUnavailable: {reason}" if reason else ""))
        except RuntimeError:
            pass  # replaced by a UI rebuild

    _SYNC_TOOLTIP = (
        "Make the two folders equal: copy every file or folder the other side lacks, and "
        "every file that has a newer Modified date than its counterpart, to the other side "
        "-- in both directions. Nothing is deleted; overwritten files go to the trash. "
        "An overview with a warning is shown first."
    )

    # ------------------------------------------------------------- Sync

    def _sync_panes(self):
        if getattr(self, "active_transfer_progress", None) is not None:
            return
        reason = self._sync_unavailable_reason()
        if reason:
            QMessageBox.warning(self, "Sync", reason)
            return
        left_dir, right_dir = self.left.entry.text().strip(), self.right.entry.text().strip()

        # Fresh scans of both folders (busy bar, Cancel works).
        progress = self._start_transfer_progress(label="Scanning...", pane=None)
        try:
            left_root, _ = self._build_snapshot(left_dir, progress)
            right_root, _ = self._build_snapshot(right_dir, progress) if not progress.cancelled else (None, None)
            if progress.cancelled or left_root is None or right_root is None:
                if not progress.cancelled:
                    QMessageBox.warning(self, "Sync", "A folder could not be read.")
                return
            name_filter = self._name_filter()
            plan = build_sync_plan(left_root, right_root, name_filter.name_passes if name_filter.active else None)
        finally:
            self._finish_transfer_progress()

        if not self._sync_confirm(plan, left_dir, right_dir, name_filter):
            return
        self._sync_execute(plan)

    def _sync_totals(self, plan, direction):
        new = [op for op in plan["ops"] if op["dir"] == direction and op["kind"] == "new"]
        upd = [op for op in plan["ops"] if op["dir"] == direction and op["kind"] == "update"]
        return new, upd

    def _sync_confirm(self, plan, left_dir, right_dir, name_filter) -> bool:
        """The overview: True = Continue. Shows nothing to confirm (just a
        note) when there is nothing to copy."""
        fmt = self.format_size
        esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        no_ops = not plan["ops"] and not plan["mkdirs"]
        lines = []
        problems = []
        for direction, title, src_dir, dest_dir in (
                (_LR, "Left &rarr; Right", left_dir, right_dir), (_RL, "Right &rarr; Left", right_dir, left_dir)):
            new, upd = self._sync_totals(plan, direction)
            folders = _plan_new_dirs(plan, direction)
            need = sum(op["size"] for op in new + upd)
            parts = [f"{len(new)} new file{'' if len(new) == 1 else 's'} ({fmt(sum(o['size'] for o in new))})",
                     f"{len(upd)} newer file{'' if len(upd) == 1 else 's'} replacing older ({fmt(sum(o['size'] for o in upd))})"]
            if folders:
                parts.append(f"{folders} folder{'' if folders == 1 else 's'} created")
            lines.append(f"<b>{title}</b>: " + ", ".join(parts))
            try:
                free = shutil.disk_usage(dest_dir).free
            except OSError:
                free = None
            if free is not None and need > free:
                problems.append(f"Not enough free space in {esc(dest_dir)}: needs {fmt(need)}, {fmt(free)} free.")
        trashed = sum(1 for op in plan["ops"] if op["kind"] == "update")

        html = [self._sync_banner("WARNING: this changes files in BOTH folders.<br>"
                                  "Check that these are the right two folders:"),
                f"<p><b>Left:</b> {esc(left_dir)}<br><b>Right:</b> {esc(right_dir)}</p>"]
        html.append("<p>" + "<br>".join(lines) + "</p>")
        if trashed:
            html.append(f"<p>{trashed} older file{'' if trashed == 1 else 's'} will be moved to the trash "
                        "before being replaced. Nothing is deleted otherwise.</p>")
        if plan["conflicts"]:
            html.append(f"<p>{len(plan['conflicts'])} conflict{'' if len(plan['conflicts']) == 1 else 's'} "
                        "will be skipped (see Show Details).</p>")
        if name_filter.active:
            html.append("<p style='color:#E68C14'><b>The File Filter is ACTIVE</b> (" + esc(", ".join(name_filter.terms))
                        + (", inverted" if name_filter.invert else "")
                        + f"): only matching files are synced; {plan['filtered']} otherwise-needed "
                        f"cop{'y is' if plan['filtered'] == 1 else 'ies are'} left out.</p>")
        for problem in problems:
            html.append(self._sync_banner(problem))

        detail = []
        for op in plan["ops"][:_MAX_DETAIL_LINES]:
            arrow = "Left -> Right" if op["dir"] == _LR else "Right -> Left"
            detail.append(f"[{arrow}] {'NEW   ' if op['kind'] == 'new' else 'NEWER '} {op['src']}")
        if len(plan["ops"]) > _MAX_DETAIL_LINES:
            detail.append(f"... and {len(plan['ops']) - _MAX_DETAIL_LINES} more")
        for path, why in plan["conflicts"][:_MAX_DETAIL_LINES]:
            detail.append(f"[SKIPPED] {path}: {why}")

        if no_ops:
            return self._sync_dialog(
                "<p><b>Nothing to sync</b> -- every file that exists on both sides has the same Modified "
                "date, and neither side lacks anything"
                + (" (within the File Filter)" if name_filter.active else "") + ".</p>"
                + ("".join(html[-2:]) if plan["conflicts"] else ""),
                "\n".join(detail), proceed=False, warning=False)
        return self._sync_dialog("".join(html), "\n".join(detail), proceed=not problems, warning=True)

    def _sync_dialog(self, html: str, detail: str, proceed: bool, warning: bool) -> bool:
        """The overview dialog (wide, with a roomy collapsible details list).
        True = Continue was clicked; Cancel is the default."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Sync folders")
        dialog.setMinimumWidth(820)
        outer = QVBoxLayout(dialog)
        top = QHBoxLayout()
        icon = QLabel()
        style = self.style()
        icon.setPixmap(style.standardIcon(
            QStyle.SP_MessageBoxWarning if warning else QStyle.SP_MessageBoxInformation).pixmap(40, 40))
        icon.setAlignment(Qt.AlignTop)
        top.addWidget(icon)
        label = QLabel(html)
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        label.setMinimumWidth(740)
        top.addWidget(label, 1)
        outer.addLayout(top)
        details = QPlainTextEdit(detail)
        details.setReadOnly(True)
        details.setLineWrapMode(QPlainTextEdit.NoWrap)
        details.setMinimumHeight(320)
        details.setVisible(False)
        outer.addWidget(details, 1)
        buttons = QHBoxLayout()
        toggle = None
        if detail:
            toggle = QPushButton("Show Details...")
            toggle.setCheckable(True)

            def on_toggle(checked):
                toggle.setText("Hide Details" if checked else "Show Details...")
                details.setVisible(checked)
                dialog.adjustSize()
            toggle.toggled.connect(on_toggle)
            buttons.addWidget(toggle)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel" if proceed else "Close")
        cancel.clicked.connect(dialog.reject)
        if proceed:
            go = QPushButton("Continue")
            go.clicked.connect(dialog.accept)
            buttons.addWidget(go)
        buttons.addWidget(cancel)
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        outer.addLayout(buttons)
        dialog.adjustSize()
        return dialog.exec() == QDialog.Accepted and proceed

    @staticmethod
    def _sync_banner(text: str) -> str:
        """White bold text on a dark red block -- readable on any theme."""
        return ("<table width='100%' cellpadding='8' cellspacing='0'><tr><td bgcolor='#8B1A1A'>"
                f"<font color='#FFFFFF'><b>{text}</b></font></td></tr></table>")

    def _sync_execute(self, plan):
        ops = plan["ops"]
        progress = self._start_transfer_progress(label="Syncing...", pane=None)
        errors, copied, pairs = [], 0, []
        try:
            progress.start_phase(len(ops) + len(plan["mkdirs"]))
            for path, _direction in plan["mkdirs"]:
                if not progress.tick():
                    break
                try:
                    os.makedirs(path, exist_ok=True)
                except OSError as e:
                    errors.append(f"{path}: {e}")
            for op in ops:
                if progress.cancelled or not progress.tick():
                    break
                src, dest = op["src"], op["dest"]
                try:
                    if not os.path.exists(src):
                        raise OSError("source no longer exists")
                    if op["kind"] == "new" and os.path.lexists(dest):
                        raise OSError("appeared since the scan -- skipped")
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    if op["kind"] == "update":
                        # The old version is kept in the trash.
                        send2trash(dest)
                except Exception as e:  # send2trash raises several kinds
                    errors.append(f"{dest}: {e}")
                    continue
                try:
                    shutil.copy2(src, dest)
                except OSError as e:
                    note = " (the previous version is in the trash)" if op["kind"] == "update" else ""
                    errors.append(f"{dest}: {e}{note}")
                    continue
                copied += 1
                pairs.append((src, dest))
            if pairs:
                self._cache_files_copied(pairs)
            self._update_listings_after_transfer(progress)
        finally:
            self._finish_transfer_progress()
            self._show_result_count(copied)
        if errors:
            shown = errors[:30] + ([f"... and {len(errors) - 30} more"] if len(errors) > 30 else [])
            QMessageBox.warning(self, "Sync completed with errors", "\n".join(shown))
