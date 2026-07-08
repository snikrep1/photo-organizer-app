#!/usr/bin/env python3
"""Photo Organizer — GUI app for organizing photos by date into monthly folders."""

import os
import sys
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QFileDialog, QDialog, QDialogButtonBox, QMessageBox,
    QProgressBar, QStatusBar, QGroupBox, QAbstractItemView,
    QCheckBox, QFrame, QMenu, QInputDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QBrush, QIcon

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

PHOTO_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.heic', '.heif',
    '.tiff', '.tif', '.bmp', '.webp',
    '.raw', '.cr2', '.nef', '.arw', '.dng',
}

MONTH_NAMES = {
    1: 'january', 2: 'february', 3: 'march', 4: 'april',
    5: 'may', 6: 'june', 7: 'july', 8: 'august',
    9: 'september', 10: 'october', 11: 'november', 12: 'december',
}

# ── Core logic ────────────────────────────────────────────────────────────────

def get_exif_date(path: Path) -> Optional[datetime]:
    if not PIL_AVAILABLE:
        return None
    try:
        img = Image.open(path)
        exif_data = img._getexif()
        if not exif_data:
            return None
        tag_map = {TAGS.get(k, k): v for k, v in exif_data.items()}
        for field in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
            val = tag_map.get(field)
            if val:
                try:
                    return datetime.strptime(val, '%Y:%m:%d %H:%M:%S')
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def get_file_date(path: Path) -> datetime:
    s = path.stat()
    times = [s.st_mtime]
    if hasattr(s, 'st_birthtime'):
        times.append(s.st_birthtime)
    else:
        times.append(s.st_ctime)
    return datetime.fromtimestamp(min(times))


def build_plan(
    photos: list[dict],
    photo_dir: Path,
    descriptor: Optional[str],
    keep_in_place: bool = False,
    custom_prefix: Optional[str] = None,
    full_name: Optional[str] = None,
) -> list[dict]:
    """Build a rename/move plan.

    keep_in_place  — rename files inside their current folder instead of moving.
    custom_prefix  — replaces the YYYY-MM portion of the filename.
    full_name      — replaces prefix+descriptor entirely; files become
                     <full_name>-NNN.ext. Overrides custom_prefix and descriptor.
    """
    full = full_name.strip() if full_name and full_name.strip() else None
    desc_override = descriptor.strip() if descriptor and descriptor.strip() else None
    prefix_override = custom_prefix.strip() if custom_prefix and custom_prefix.strip() else None

    # Annotate each photo with its final destination folder + stem pattern.
    # Every file that shares (dest_folder, stem_template) must share ONE counter
    # or filenames will collide.
    annotated = []
    for p in photos:
        year, month = p['date'].year, p['date'].month
        dest_folder = p['path'].parent if keep_in_place else photo_dir / f"{year}-{month:02d}"

        if full:
            stem_template = full   # "<full>-NNN"
        else:
            prefix = prefix_override if prefix_override else f"{year}-{month:02d}"
            desc = desc_override if desc_override else MONTH_NAMES[month]
            stem_template = f"{prefix}-{desc}"   # "<prefix>-<desc>-NNN"

        annotated.append({**p, 'dest_folder': dest_folder, 'stem': stem_template})

    # Group by (dest_folder, stem_template) so one counter covers every file
    # that would otherwise produce the same filename.
    groups: dict[tuple, list] = defaultdict(list)
    for e in annotated:
        groups[(str(e['dest_folder']), e['stem'])].append(e)

    plan = []
    used_paths: set[str] = set()  # safety net: absolute uniqueness guarantee
    for key in sorted(groups.keys()):
        group = sorted(groups[key], key=lambda x: (x['date'], str(x['path'])))
        for idx, e in enumerate(group, start=1):
            ext = e['path'].suffix.lower()
            if ext == '.jpeg':
                ext = '.jpg'
            stem = e['stem']
            dest = e['dest_folder'] / f"{stem}-{idx:03d}{ext}"
            # Safety net — if this path is still somehow taken (shouldn't happen
            # given the grouping above, but defends against future edge cases
            # and against pre-existing files on disk), bump the counter.
            bump = idx
            while str(dest) in used_paths:
                bump += 1
                dest = e['dest_folder'] / f"{stem}-{bump:03d}{ext}"
            used_paths.add(str(dest))
            plan.append({
                'src': e['path'],
                'dest': dest,
                'dest_folder': e['dest_folder'],
                'date': e['date'],
                'source': e['source'],
            })
    return plan


# ── Background workers ────────────────────────────────────────────────────────

class ScanWorker(QThread):
    progress = pyqtSignal(int, int)      # scanned, total
    finished = pyqtSignal(list, bool)    # photos, was_cancelled
    error = pyqtSignal(str)

    def __init__(self, photo_dir: Path, excluded_folders: set[Path]):
        super().__init__()
        self.photo_dir = photo_dir
        self.excluded_folders = excluded_folders
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            # First pass: collect all candidates
            candidates = []
            for root, dirs, files in os.walk(self.photo_dir):
                if self._stop:
                    self.finished.emit([], True)
                    return
                root_path = Path(root)
                dirs[:] = [
                    d for d in dirs
                    if not d.startswith('.')
                    and root_path / d not in self.excluded_folders
                ]
                for fname in files:
                    fpath = root_path / fname
                    if fpath.suffix.lower() in PHOTO_EXTENSIONS:
                        candidates.append(fpath)

            photos = []
            for i, fpath in enumerate(candidates):
                if self._stop:
                    self.finished.emit(photos, True)
                    return
                self.progress.emit(i + 1, len(candidates))
                exif_dt = get_exif_date(fpath)
                file_dt = get_file_date(fpath)
                photos.append({
                    'path': fpath,
                    'date': exif_dt if exif_dt else file_dt,
                    'source': 'exif' if exif_dt else 'file-date',
                })

            self.finished.emit(photos, False)
        except Exception as e:
            self.error.emit(str(e))


class ExecuteWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(int, list)   # moved_count, errors

    def __init__(self, plan: list[dict]):
        super().__init__()
        self.plan = plan

    def run(self):
        errors = []
        moved = 0
        for i, entry in enumerate(self.plan):
            self.progress.emit(i + 1, len(self.plan))
            entry['dest_folder'].mkdir(parents=True, exist_ok=True)
            dest = entry['dest']
            if dest.exists():
                stem, suffix = dest.stem, dest.suffix
                n = 1
                while dest.exists():
                    dest = dest.parent / f"{stem}-dup{n:02d}{suffix}"
                    n += 1
            try:
                shutil.move(str(entry['src']), str(dest))
                moved += 1
            except Exception as e:
                errors.append((str(entry['src']), str(e)))
        self.finished.emit(moved, errors)


# ── Mount dialog ──────────────────────────────────────────────────────────────

class MountDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mount NAS Share")
        self.setMinimumWidth(420)
        self.mounted_path = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form_layout = QVBoxLayout()
        def row(label, widget):
            h = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(110)
            h.addWidget(lbl)
            h.addWidget(widget)
            form_layout.addLayout(h)

        self.host = QLineEdit("truenas.local")
        self.share = QLineEdit("family/Photos")
        self.username = QLineEdit("guy")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.mountpoint = QLineEdit("/mnt/nas-photos")
        self.sudo_password = QLineEdit()
        self.sudo_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.sudo_password.setPlaceholderText("Required to run mount.cifs")

        row("NAS Host:", self.host)
        row("Share Path:", self.share)
        row("Username:", self.username)
        row("NAS Password:", self.password)
        row("Mount Point:", self.mountpoint)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        form_layout.addWidget(sep)

        row("Sudo Password:", self.sudo_password)

        layout.addLayout(form_layout)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.mount_btn = QPushButton("Mount")
        self.mount_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        buttons.addButton(self.mount_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        self.mount_btn.clicked.connect(self._do_mount)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _do_mount(self):
        self.mount_btn.setEnabled(False)
        self.status.setText("Mounting…")
        QApplication.processEvents()

        mount_point = Path(self.mountpoint.text().strip())
        smb_path = f"//{self.host.text().strip()}/{self.share.text().strip()}"
        username = self.username.text().strip()
        password = self.password.text()
        uid = os.getuid()
        gid = os.getgid()

        try:
            mount_point.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.status.setText(f"Could not create mount point: {e}")
            self.mount_btn.setEnabled(True)
            return

        sudo_pw = self.sudo_password.text()
        cmd = [
            'sudo', '-S', 'mount.cifs', smb_path, str(mount_point),
            '-o', f'username={username},password={password},uid={uid},gid={gid},iocharset=utf8',
        ]
        # sudo -S reads its password from stdin; we append a newline as sudo expects
        result = subprocess.run(
            cmd, input=sudo_pw + '\n',
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            self.mounted_path = str(mount_point)
            self.accept()
        else:
            msg = result.stderr.strip() or "mount.cifs failed (check sudo permissions)"
            self.status.setText(f"Error: {msg}")
            self.mount_btn.setEnabled(True)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Photo Organizer")
        self.setMinimumSize(1000, 680)
        self.photo_dir: Path | None = None
        self.photos: list[dict] = []
        self.plan: list[dict] = []
        self._scan_worker: ScanWorker | None = None
        self._exec_worker: ExecuteWorker | None = None

        self._updating_checks = False  # re-entrancy guard for checkbox signals
        self._table_updating = False   # re-entrancy guard for table itemChanged
        self._custom_names: dict[str, str] = {}  # str(src_path) → custom filename
        self._mounted_path: Path | None = None  # set when we mount a share via the dialog
        self._build_ui()
        self._set_controls_enabled(False)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        # ── Source row ────────────────────────────────────────────────────────
        source_group = QGroupBox("Source Directory")
        source_layout = QHBoxLayout(source_group)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select a folder or mount the NAS…")
        self.path_edit.setReadOnly(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        source_layout.addWidget(self.path_edit)
        source_layout.addWidget(browse_btn)

        # CIFS mount is Linux-only (mount.cifs + sudo). On Win/Mac users should
        # mount the share via Finder / Explorer / SMB client and point Browse at it.
        if sys.platform.startswith("linux"):
            self.mount_btn = QPushButton("Mount NAS…")
            self.mount_btn.clicked.connect(self._mount_nas)
            self.unmount_btn = QPushButton("Unmount")
            self.unmount_btn.clicked.connect(self._unmount_nas)
            self.unmount_btn.setVisible(False)
            source_layout.addWidget(self.mount_btn)
            source_layout.addWidget(self.unmount_btn)
        else:
            # Stubs so references elsewhere don't explode
            self.mount_btn = QPushButton()
            self.mount_btn.hide()
            self.unmount_btn = QPushButton()
            self.unmount_btn.hide()

        root.addWidget(source_group)

        # ── Options rows ──────────────────────────────────────────────────────
        options_group = QGroupBox("Naming Options")
        options_vlay = QVBoxLayout(options_group)
        options_vlay.setSpacing(6)

        # Row 1: Prefix + Descriptor
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Prefix:"))
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("Leave blank to use date  (e.g. family, trip)")
        self.prefix_edit.setMaximumWidth(240)
        self.prefix_edit.textChanged.connect(self._on_options_changed)
        row1.addWidget(self.prefix_edit)
        row1.addSpacing(16)
        row1.addWidget(QLabel("Descriptor:"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Leave blank to use month name  (e.g. vacation, birthday)")
        self.desc_edit.setMaximumWidth(300)
        self.desc_edit.textChanged.connect(self._on_options_changed)
        row1.addWidget(self.desc_edit)
        row1.addStretch()
        options_vlay.addLayout(row1)

        # Row 1b: Full name override (replaces prefix + descriptor when set)
        row1b = QHBoxLayout()
        fullname_label = QLabel("Full name:")
        fullname_label.setToolTip("Overrides prefix and descriptor. Files are named <name>-NNN.ext")
        row1b.addWidget(fullname_label)
        self.fullname_edit = QLineEdit()
        self.fullname_edit.setPlaceholderText("Overrides prefix+descriptor — files become <name>-NNN.ext")
        self.fullname_edit.setMaximumWidth(556)
        self.fullname_edit.textChanged.connect(self._on_fullname_changed)
        row1b.addWidget(self.fullname_edit)
        row1b.addStretch()
        options_vlay.addLayout(row1b)

        # Row 2: Keep-in-place toggle
        row2 = QHBoxLayout()
        self.keep_in_place_chk = QCheckBox("Keep files in their current folder  (rename in place, don't move)")
        self.keep_in_place_chk.setChecked(False)
        self.keep_in_place_chk.checkStateChanged.connect(self._on_options_changed)
        row2.addWidget(self.keep_in_place_chk)
        row2.addStretch()
        options_vlay.addLayout(row2)

        root.addWidget(options_group)

        # ── Main splitter: folder tree | preview table ────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: folder exclusion panel
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        folder_header = QHBoxLayout()
        folder_header.addWidget(QLabel("Folders to include:"))
        folder_header.addStretch()
        check_all_btn = QPushButton("All")
        check_all_btn.setFixedWidth(60)
        check_all_btn.setToolTip("Select all folders")
        check_all_btn.clicked.connect(lambda: self._set_all_folders(True))
        none_btn = QPushButton("None")
        none_btn.setFixedWidth(70)
        none_btn.setToolTip("Deselect all folders")
        none_btn.clicked.connect(lambda: self._set_all_folders(False))
        folder_header.addWidget(check_all_btn)
        folder_header.addWidget(none_btn)
        left_layout.addLayout(folder_header)

        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.itemChanged.connect(self._on_folder_check_changed)
        left_layout.addWidget(self.folder_tree)

        scan_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan Photos")
        self.scan_btn.setFixedHeight(34)
        self.scan_btn.clicked.connect(self._start_scan)
        self.stop_scan_btn = QPushButton("Stop Scanning")
        self.stop_scan_btn.setFixedHeight(34)
        self.stop_scan_btn.setObjectName("stopBtn")
        self.stop_scan_btn.setVisible(False)
        self.stop_scan_btn.clicked.connect(self._stop_scan)
        scan_row.addWidget(self.scan_btn)
        scan_row.addWidget(self.stop_scan_btn)
        left_layout.addLayout(scan_row)

        left.setMinimumWidth(220)
        left.setMaximumWidth(340)
        splitter.addWidget(left)

        # Right: preview table
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("Preview:"))
        preview_header.addStretch()
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #666;")
        preview_header.addWidget(self.stats_label)
        right_layout.addLayout(preview_header)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Original File", "New Name  ✎", "Folder", "Date Source"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        # Double-click on the New Name column (col 1) triggers inline edit
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_context_menu)
        right_layout.addWidget(self.table)

        splitter.addWidget(right)
        splitter.setSizes([260, 740])
        root.addWidget(splitter, stretch=1)

        # ── Bottom bar ────────────────────────────────────────────────────────
        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedHeight(18)
        bottom.addWidget(self.progress)
        bottom.addStretch()
        self.organize_btn = QPushButton("Organize Photos")
        self.organize_btn.setFixedHeight(38)
        self.organize_btn.setFixedWidth(160)
        self.organize_btn.setObjectName("organizeBtn")
        self.organize_btn.clicked.connect(self._confirm_and_execute)
        bottom.addWidget(self.organize_btn)
        root.addLayout(bottom)

        # Status bar
        self.statusBar().showMessage("Select a folder to get started.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_controls_enabled(self, enabled: bool):
        self.scan_btn.setEnabled(enabled)
        self.organize_btn.setEnabled(enabled and bool(self.plan))

    def _get_excluded_folders(self) -> tuple[set[Path], bool]:
        """Return (excluded_subdir_paths, root_files_excluded).

        Walks the tree recursively. A fully-unchecked item adds itself to the
        excluded set (its children are implicitly excluded via os.walk pruning).
        A partially-checked item is recursed into to find the unchecked leaves.
        """
        excluded: set[Path] = set()
        root_excluded = False

        def collect(item: QTreeWidgetItem):
            nonlocal root_excluded
            path = Path(item.data(0, Qt.ItemDataRole.UserRole))
            state = item.checkState(0)
            if path == self.photo_dir:
                # Special "(root folder)" sentinel — only controls root-level files
                root_excluded = (state == Qt.CheckState.Unchecked)
                return
            if state == Qt.CheckState.Unchecked:
                excluded.add(path)
                # No need to recurse: os.walk will prune this dir entirely
            elif state == Qt.CheckState.PartiallyChecked:
                for i in range(item.childCount()):
                    collect(item.child(i))
            # Checked → nothing excluded under this subtree

        root_widget = self.folder_tree.invisibleRootItem()
        for i in range(root_widget.childCount()):
            collect(root_widget.child(i))

        return excluded, root_excluded

    def _set_all_folders(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._updating_checks = True
        try:
            def set_recursive(item: QTreeWidgetItem):
                item.setCheckState(0, state)
                for i in range(item.childCount()):
                    set_recursive(item.child(i))
            root = self.folder_tree.invisibleRootItem()
            for i in range(root.childCount()):
                set_recursive(root.child(i))
        finally:
            self._updating_checks = False
        self._rebuild_preview()

    # ── Directory / mount ─────────────────────────────────────────────────────

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Photo Folder")
        if path:
            self._set_photo_dir(Path(path))

    def _mount_nas(self):
        dlg = MountDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.mounted_path:
            self._mounted_path = Path(dlg.mounted_path)
            self._set_photo_dir(self._mounted_path)
            self.mount_btn.setVisible(False)
            self.unmount_btn.setVisible(True)
            self.statusBar().showMessage(f"Mounted NAS at {dlg.mounted_path}")

    def _unmount_nas(self):
        if not self._mounted_path:
            return
        mp = self._mounted_path

        # Running workers + mounted share don't mix — bail early with a clear message.
        if self._scan_worker and self._scan_worker.isRunning():
            QMessageBox.warning(self, "Busy", "Stop the scan before unmounting.")
            return
        if self._exec_worker and self._exec_worker.isRunning():
            QMessageBox.warning(self, "Busy", "Wait for the organize operation to finish before unmounting.")
            return

        reply = QMessageBox.question(
            self, "Unmount NAS",
            f"Unmount the NAS share at:\n\n  {mp}\n\n"
            "Any unsaved work will be flushed. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        sudo_pw, ok = QInputDialog.getText(
            self, "Sudo Password",
            f"Sudo password (to run umount on {mp}):",
            QLineEdit.EchoMode.Password,
        )
        if not ok:
            return

        result = subprocess.run(
            ['sudo', '-S', 'umount', str(mp)],
            input=sudo_pw + '\n',
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "umount failed"
            QMessageBox.critical(self, "Unmount failed", f"Could not unmount {mp}:\n\n{err}")
            return

        # Reset UI to pre-mount state
        self._mounted_path = None
        self.photo_dir = None
        self.photos = []
        self.plan = []
        self._custom_names.clear()
        self.path_edit.clear()
        self.folder_tree.clear()
        self._clear_preview()
        self._set_controls_enabled(False)
        self.unmount_btn.setVisible(False)
        self.mount_btn.setVisible(True)
        self.statusBar().showMessage(f"Unmounted {mp}")

    def _set_photo_dir(self, path: Path):
        self.photo_dir = path
        self.path_edit.setText(str(path))
        self.photos = []
        self.plan = []
        self._populate_folder_tree()
        self._clear_preview()
        self._set_controls_enabled(True)
        self.statusBar().showMessage(f"Ready — click Scan Photos to begin.")

    # ── Folder tree ───────────────────────────────────────────────────────────

    def _populate_folder_tree(self):
        self.folder_tree.clear()
        if not self.photo_dir:
            return

        self._updating_checks = True
        try:
            # Sentinel for files sitting directly in photo_dir (not in any subdir)
            root_sentinel = QTreeWidgetItem(["(root folder)"])
            root_sentinel.setData(0, Qt.ItemDataRole.UserRole, str(self.photo_dir))
            root_sentinel.setCheckState(0, Qt.CheckState.Checked)
            root_sentinel.setToolTip(0, f"Photos directly inside {self.photo_dir}")
            font = root_sentinel.font(0)
            font.setItalic(True)
            root_sentinel.setFont(0, font)
            self.folder_tree.addTopLevelItem(root_sentinel)

            self._populate_subtree(self.folder_tree.invisibleRootItem(), self.photo_dir)
        except PermissionError as e:
            self.statusBar().showMessage(f"Permission error listing folders: {e}")
        finally:
            self._updating_checks = False

        self.folder_tree.expandToDepth(0)  # expand top-level only by default

    def _populate_subtree(self, parent: QTreeWidgetItem, directory: Path, depth: int = 0):
        """Recursively add subdirectory items under parent (max 8 levels deep)."""
        if depth > 8:
            return
        try:
            subdirs = sorted(
                e for e in directory.iterdir()
                if e.is_dir() and not e.name.startswith('.')
            )
        except PermissionError:
            return

        for subdir in subdirs:
            item = QTreeWidgetItem([subdir.name])
            item.setData(0, Qt.ItemDataRole.UserRole, str(subdir))
            item.setToolTip(0, str(subdir))
            # ItemIsAutoTristate: parent reflects children's mixed state automatically
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            item.setCheckState(0, Qt.CheckState.Checked)
            if isinstance(parent, QTreeWidget):
                parent.addTopLevelItem(item)
            else:
                parent.addChild(item)
            self._populate_subtree(item, subdir, depth + 1)

    def _on_folder_check_changed(self, item: QTreeWidgetItem, column: int):
        if self._updating_checks:
            return
        self._updating_checks = True
        try:
            state = item.checkState(0)
            # PartiallyChecked is set automatically by Qt for parents — don't propagate it
            if state != Qt.CheckState.PartiallyChecked:
                def propagate(node: QTreeWidgetItem):
                    for i in range(node.childCount()):
                        child = node.child(i)
                        child.setCheckState(0, state)
                        propagate(child)
                propagate(item)
        finally:
            self._updating_checks = False
        self._rebuild_preview()

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _start_scan(self):
        if not self.photo_dir:
            return
        self.scan_btn.setEnabled(False)
        self.organize_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.statusBar().showMessage("Scanning photos…")
        self._clear_preview()
        self._custom_names.clear()

        self.scan_btn.setVisible(False)
        self.stop_scan_btn.setVisible(True)

        excluded, self._root_excluded = self._get_excluded_folders()
        self._scan_worker = ScanWorker(self.photo_dir, excluded)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _on_scan_progress(self, done: int, total: int):
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _stop_scan(self):
        if self._scan_worker:
            self._scan_worker.stop()
        self.stop_scan_btn.setEnabled(False)
        self.statusBar().showMessage("Stopping scan…")

    def _on_scan_finished(self, photos: list[dict], cancelled: bool):
        if self._root_excluded:
            photos = [p for p in photos if p['path'].parent != self.photo_dir]

        self.photos = photos
        self.progress.setVisible(False)
        self.stop_scan_btn.setVisible(False)
        self.stop_scan_btn.setEnabled(True)
        self.scan_btn.setVisible(True)
        self.scan_btn.setEnabled(True)

        if cancelled:
            self.statusBar().showMessage(
                f"Scan cancelled — {len(photos)} photos found so far. You can still organize these, or scan again."
            )
        else:
            self.statusBar().showMessage(f"Found {len(photos)} photos. Review the preview below.")

        self._rebuild_preview()

    def _on_scan_error(self, msg: str):
        self.progress.setVisible(False)
        self.stop_scan_btn.setVisible(False)
        self.stop_scan_btn.setEnabled(True)
        self.scan_btn.setVisible(True)
        self.scan_btn.setEnabled(True)
        QMessageBox.critical(self, "Scan Error", msg)

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_options_changed(self, *_):
        if self.photos:
            self._rebuild_preview()

    def _on_fullname_changed(self, text: str):
        # Visually disable prefix/descriptor when a full name is active
        active = bool(text.strip())
        self.prefix_edit.setEnabled(not active)
        self.desc_edit.setEnabled(not active)
        if self.photos:
            self._rebuild_preview()

    def _current_options(self) -> dict:
        return {
            'descriptor':    self.desc_edit.text().strip() or None,
            'keep_in_place': self.keep_in_place_chk.isChecked(),
            'custom_prefix': self.prefix_edit.text().strip() or None,
            'full_name':     self.fullname_edit.text().strip() or None,
        }

    def _rebuild_preview(self):
        if not self.photos:
            return
        opts = self._current_options()
        self.plan = build_plan(
            self.photos, self.photo_dir,
            opts['descriptor'],
            keep_in_place=opts['keep_in_place'],
            custom_prefix=opts['custom_prefix'],
            full_name=opts['full_name'],
        )
        self._populate_table()
        # _populate_table sets _has_conflicts and disables the button if needed
        if not getattr(self, '_has_conflicts', False):
            self.organize_btn.setEnabled(bool(self.plan))

    def _clear_preview(self):
        self.table.setRowCount(0)
        self.stats_label.setText("")
        self.plan = []
        self.organize_btn.setEnabled(False)

    def _populate_table(self):
        self._table_updating = True
        try:
            self.table.setRowCount(0)

            # Apply custom-name overrides on top of the auto plan.
            for entry in self.plan:
                src_key = str(entry['src'])
                if src_key in self._custom_names:
                    entry['dest'] = entry['dest_folder'] / self._custom_names[src_key]

            # Detect duplicate destination paths. Auto names are guaranteed
            # unique by build_plan; duplicates here come from custom renames
            # stepping on an auto name or on another custom name.
            dest_counts: dict[str, int] = defaultdict(int)
            for entry in self.plan:
                dest_counts[str(entry['dest'])] += 1
            conflict_count = sum(1 for c in dest_counts.values() if c > 1)

            no_exif = sum(1 for p in self.plan if p['source'] == 'file-date')
            custom_count = sum(1 for e in self.plan if str(e['src']) in self._custom_names)
            stat = f"{len(self.plan)} photos  |  {len(self.plan) - no_exif} with EXIF  |  {no_exif} from file date"
            if custom_count:
                stat += f"  |  {custom_count} manually renamed"
            if conflict_count:
                stat += f"  |  ⚠ {conflict_count} name conflict(s)"
            self.stats_label.setText(stat)

            no_exif_bg   = QColor("#2d2515")
            no_exif_fg   = QColor("#f9e2af")  # Mocha Yellow
            custom_bg    = QColor("#1e1e30")  # slightly blue surface
            custom_fg    = QColor("#b4befe")  # Mocha Lavender
            conflict_bg  = QColor("#3a1f24")  # dark Mocha Red
            conflict_fg  = QColor("#f38ba8")  # Mocha Red

            for entry in self.plan:
                row = self.table.rowCount()
                self.table.insertRow(row)

                src_key = str(entry['src'])
                is_custom = src_key in self._custom_names
                is_conflict = dest_counts[str(entry['dest'])] > 1

                try:
                    src_display = entry['src'].relative_to(self.photo_dir)
                except ValueError:
                    src_display = entry['src']

                src_item    = QTableWidgetItem(str(src_display))
                dest_item   = QTableWidgetItem(entry['dest'].name)
                folder_item = QTableWidgetItem(entry['dest_folder'].name)
                source_item = QTableWidgetItem(entry['source'])

                read_only = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

                for col, item in enumerate([src_item, dest_item, folder_item, source_item]):
                    if col == 1:
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable)
                    else:
                        item.setFlags(read_only)

                    # Conflict colouring overrides custom/no-exif colouring
                    if is_conflict:
                        item.setBackground(QBrush(conflict_bg))
                        item.setForeground(QBrush(conflict_fg))
                        item.setToolTip(f"Name conflict: {entry['dest'].name} is used by another file")
                    elif is_custom:
                        item.setBackground(QBrush(custom_bg))
                        if col == 1:
                            item.setForeground(QBrush(custom_fg))
                    elif entry['source'] == 'file-date':
                        item.setBackground(QBrush(no_exif_bg))
                        item.setForeground(QBrush(no_exif_fg))

                    self.table.setItem(row, col, item)

            # Block Organize while conflicts exist
            self._has_conflicts = conflict_count > 0
            if self._has_conflicts:
                self.organize_btn.setEnabled(False)
                self.organize_btn.setToolTip("Resolve name conflicts (rows shown in red) before organizing")
            else:
                self.organize_btn.setToolTip("")
        finally:
            self._table_updating = False

    def _on_table_item_changed(self, item: QTableWidgetItem):
        if self._table_updating or item.column() != 1:
            return
        row = item.row()
        if row < 0 or row >= len(self.plan):
            return

        entry = self.plan[row]
        src_key = str(entry['src'])
        original_ext = entry['src'].suffix.lower()
        if original_ext == '.jpeg':
            original_ext = '.jpg'

        new_name = item.text().strip()

        # Silently restore extension if the user removed it
        if new_name and not Path(new_name).suffix:
            new_name = new_name + original_ext

        auto_name = entry['dest_folder'].name  # will rebuild properly below
        # Compute what the auto name would be so we can detect "reverted to auto"
        opts = self._current_options()
        auto_dest_name = build_plan(
            [{'path': entry['src'], 'date': entry['date'], 'source': entry['source']}],
            self.photo_dir,
            opts['descriptor'],
            keep_in_place=opts['keep_in_place'],
            custom_prefix=opts['custom_prefix'],
            full_name=opts['full_name'],
        )[0]['dest'].name

        if not new_name or new_name == auto_dest_name:
            # Cleared or matches auto — remove custom override
            self._custom_names.pop(src_key, None)
        else:
            self._custom_names[src_key] = new_name

        # Rebuild to apply colours / stat update cleanly
        self._rebuild_preview()

    def _table_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or row >= len(self.plan):
            return
        src_key = str(self.plan[row]['src'])
        menu = QMenu(self)
        if src_key in self._custom_names:
            reset_act = menu.addAction("Reset to auto-name")
            action = menu.exec(self.table.viewport().mapToGlobal(pos))
            if action == reset_act:
                del self._custom_names[src_key]
                self._rebuild_preview()
        else:
            hint = menu.addAction("Double-click the New Name column to rename")
            hint.setEnabled(False)
            menu.exec(self.table.viewport().mapToGlobal(pos))

    # ── Execute ───────────────────────────────────────────────────────────────

    def _confirm_and_execute(self):
        if not self.plan:
            return
        no_exif = sum(1 for p in self.plan if p['source'] == 'file-date')
        msg = (
            f"This will move and rename {len(self.plan)} photos into monthly folders inside:\n\n"
            f"  {self.photo_dir}\n\n"
        )
        if no_exif:
            msg += f"  ⚠  {no_exif} photos have no EXIF data and will be dated by file timestamp.\n\n"
        msg += "This cannot be undone automatically. Continue?"

        reply = QMessageBox.question(
            self, "Confirm Organization", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.organize_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.statusBar().showMessage("Organizing photos…")

        self._exec_worker = ExecuteWorker(self.plan)
        self._exec_worker.progress.connect(self._on_exec_progress)
        self._exec_worker.finished.connect(self._on_exec_finished)
        self._exec_worker.start()

    def _on_exec_progress(self, done: int, total: int):
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _on_exec_finished(self, moved: int, errors: list):
        self.progress.setVisible(False)
        self.scan_btn.setEnabled(True)

        if errors:
            err_text = "\n".join(f"  {src}: {msg}" for src, msg in errors[:20])
            if len(errors) > 20:
                err_text += f"\n  … and {len(errors) - 20} more"
            QMessageBox.warning(
                self, "Done with errors",
                f"Moved {moved} photos.\n\n{len(errors)} errors:\n{err_text}",
            )
        else:
            QMessageBox.information(
                self, "Done",
                f"Successfully organized {moved} photos into monthly folders.",
            )

        # Refresh tree and clear plan
        self._populate_folder_tree()
        self._clear_preview()
        self.photos = []
        self.statusBar().showMessage(f"Done — {moved} photos organized.")


# ── Entry point ───────────────────────────────────────────────────────────────

MOCHA_STYLESHEET = """
/* ── Catppuccin Mocha ─────────────────────────────────────────────────────── */

QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-size: 13px;
}

QMainWindow, QDialog {
    background-color: #1e1e2e;
}

/* Group boxes */
QGroupBox {
    border: 1px solid #45475a;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
    color: #89b4fa;
}

/* Buttons */
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 14px;
    min-height: 22px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background-color: #585b70;
}
QPushButton:disabled {
    background-color: #181825;
    color: #45475a;
    border-color: #313244;
}

/* Organize button — Mocha Blue accent */
QPushButton#organizeBtn {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
    font-weight: bold;
}
QPushButton#organizeBtn:hover {
    background-color: #b4befe;
}
QPushButton#organizeBtn:disabled {
    background-color: #313244;
    color: #45475a;
}

/* Stop button — Mocha Red accent */
QPushButton#stopBtn {
    background-color: #f38ba8;
    color: #1e1e2e;
    border: none;
    font-weight: bold;
}
QPushButton#stopBtn:hover {
    background-color: #eba0ac;
}

/* Text inputs */
QLineEdit {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
}
QLineEdit:focus {
    border-color: #89b4fa;
}
QLineEdit:read-only {
    color: #a6adc8;
}

/* Tree widget */
QTreeWidget {
    background-color: #181825;
    alternate-background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    outline: none;
}
QTreeWidget::item {
    padding: 2px 0;
}
QTreeWidget::item:hover {
    background-color: #2a2a3e;
}
QTreeWidget::item:selected {
    background-color: #313244;
    color: #cdd6f4;
}
QTreeWidget::branch {
    background-color: #181825;
}

/* Table widget */
QTableWidget {
    background-color: #181825;
    alternate-background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #45475a;
    gridline-color: #313244;
    outline: none;
}
QTableWidget::item {
    padding: 3px 6px;
}
QTableWidget::item:selected {
    background-color: #313244;
    color: #cdd6f4;
}

/* Table / tree header */
QHeaderView {
    background-color: #11111b;
}
QHeaderView::section {
    background-color: #11111b;
    color: #89b4fa;
    border: none;
    border-right: 1px solid #313244;
    border-bottom: 1px solid #313244;
    padding: 5px 8px;
    font-weight: bold;
}
QHeaderView::section:last {
    border-right: none;
}

/* Progress bar */
QProgressBar {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    text-align: center;
    color: #cdd6f4;
    height: 16px;
}
QProgressBar::chunk {
    background-color: #89b4fa;
    border-radius: 3px;
}

/* Scroll bars */
QScrollBar:vertical {
    background-color: #181825;
    width: 10px;
    border: none;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #45475a;
    border-radius: 5px;
    min-height: 24px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover { background-color: #585b70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }

QScrollBar:horizontal {
    background-color: #181825;
    height: 10px;
    border: none;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #45475a;
    border-radius: 5px;
    min-width: 24px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover { background-color: #585b70; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

/* Splitter handle */
QSplitter::handle { background-color: #313244; }
QSplitter::handle:horizontal { width: 2px; }
QSplitter::handle:vertical   { height: 2px; }

/* Status bar */
QStatusBar {
    background-color: #11111b;
    color: #a6adc8;
    border-top: 1px solid #313244;
}

/* Labels */
QLabel {
    background: transparent;
    color: #cdd6f4;
}

/* Separator frames */
QFrame[frameShape="4"],
QFrame[frameShape="5"] {
    color: #45475a;
}

/* Message boxes */
QMessageBox { background-color: #1e1e2e; }
QMessageBox QLabel { color: #cdd6f4; }
QMessageBox QPushButton { min-width: 80px; }

/* Dialog button box */
QDialogButtonBox QPushButton { min-width: 80px; }
"""


def resource_path(rel: str) -> Path:
    """Resolve a bundled resource path.

    Works both when running from source and when frozen by PyInstaller
    (which unpacks bundled data under sys._MEIPASS).
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def load_app_icon() -> QIcon:
    """Build a multi-resolution app icon from the bundled PNGs."""
    icon = QIcon()
    png_dir = resource_path("resources/icons/png")
    added = False
    if png_dir.is_dir():
        for png in sorted(png_dir.glob("icon-*.png")):
            icon.addFile(str(png))
            added = True
    if not added:
        fallback = resource_path("resources/icons/app.png")
        if fallback.exists():
            icon.addFile(str(fallback))
    return icon


def main():
    # On Windows, give the app its own taskbar identity so the icon
    # groups correctly and doesn't inherit python.exe's default.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "com.guy.photoorganizer"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Photo Organizer")
    # On Linux, match the window to the installed .desktop entry
    # (StartupWMClass=photo-organizer) so GNOME/Wayland shows its icon
    # in the dock and app menu instead of a generic placeholder.
    if sys.platform.startswith("linux"):
        app.setDesktopFileName("photo-organizer")
    app.setWindowIcon(load_app_icon())
    app.setStyle("Fusion")
    app.setStyleSheet(MOCHA_STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
