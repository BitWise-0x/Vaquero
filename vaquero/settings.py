"""The Settings window: where Vaquero shows up, what macOS lets it reach, where its files are, and repairs."""

from __future__ import annotations

import html
import logging
import shutil
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from vaquero import access, logs, macos, remote, theme
from vaquero.remote import JOURNAL, LOG_DIR, MODELS_DIR

if TYPE_CHECKING:
    from vaquero.app import Window

log = logging.getLogger("vaquero")

AUTO_UPDATE_KEY = "check_updates_on_launch"
NOTIFY_KEY = "notify_when_done"
SHOW_IN_DOCK_KEY = "show_in_dock"
SHOW_IN_MENU_BAR_KEY = "show_in_menu_bar"
MENU_BAR_HINT_KEY = "menu_bar_hint_shown"
KEEP_SCANS_KEY = "scan_logs_kept"
STATE_COLORS = {access.FolderState.OK: theme.MATCH, access.FolderState.NOT_SET: theme.DIM}


class _Checked(QObject):
    """Carries the folder and Full Disk Access checks back from their thread."""

    done = Signal(object, object)  # {field: FolderState}, Full Disk Access (bool | None)


class SettingsDialog(QDialog):
    def __init__(self, window: Window) -> None:
        super().__init__(window)
        self.window_ = window
        self.settings = window.settings
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        layout.addWidget(self._general())
        layout.addWidget(self._access())
        layout.addWidget(self._files())
        layout.addWidget(self._maintenance())
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.resize(560, 0)
        self._check_access()

    # General

    def _general(self) -> QGroupBox:
        box = QGroupBox("General")
        grid = QVBoxLayout(box)
        w = self.window_
        self.dock = QCheckBox("Show Vaquero in the Dock")
        self.menu_bar = QCheckBox("Show Vaquero in the menu bar")
        self.dock.setChecked(w.dock_shown())
        self.menu_bar.setChecked(w.menu_bar_shown())
        for box_ in (self.dock, self.menu_bar):
            box_.toggled.connect(self._presence_changed)
            grid.addWidget(box_)

        self.login = QCheckBox("Open Vaquero at login")
        self.login_note = QLabel()
        self.login_note.setStyleSheet(f"color: {theme.MUTED}")
        self.login_note.setWordWrap(True)
        self.login_note.setTextFormat(Qt.TextFormat.RichText)
        self.login_note.linkActivated.connect(lambda _: macos.open_login_items_settings())
        self._show_login_state()
        self.login.toggled.connect(self._login_changed)
        grid.addWidget(self.login)
        grid.addWidget(self.login_note)

        notify = QCheckBox("Notify me when a scan, copy or move finishes in the background")
        notify.setChecked(self.settings.value(NOTIFY_KEY, True, type=bool))
        notify.toggled.connect(lambda on: self.settings.setValue(NOTIFY_KEY, on))
        grid.addWidget(notify)
        updates = QCheckBox("Check for updates when Vaquero opens")
        updates.setChecked(self.settings.value(AUTO_UPDATE_KEY, True, type=bool))
        updates.toggled.connect(lambda on: self.settings.setValue(AUTO_UPDATE_KEY, on))
        grid.addWidget(updates)
        return box

    def _presence_changed(self) -> None:
        dock, menu_bar = self.dock.isChecked(), self.menu_bar.isChecked()
        if not dock and not menu_bar:  # never neither: Vaquero could not be reached
            sender = self.sender()
            other = self.menu_bar if sender is self.dock else self.dock
            other.blockSignals(True)
            other.setChecked(True)
            other.blockSignals(False)
            dock, menu_bar = self.dock.isChecked(), self.menu_bar.isChecked()
        self.window_.set_presence(dock, menu_bar)

    def _show_login_state(self) -> None:
        bundled = remote.running_bundle() is not None
        state = macos.login_item_status() if bundled else macos.LOGIN_NOT_FOUND
        self.login.blockSignals(True)
        self.login.setChecked(state in (macos.LOGIN_ON, macos.LOGIN_NEEDS_APPROVAL))
        self.login.blockSignals(False)
        self.login.setEnabled(bundled)
        if not bundled:
            self.login_note.setText("Installed app only")
        elif state == macos.LOGIN_NEEDS_APPROVAL:
            self.login_note.setText(f'Waiting for approval in <a href="login" style="color:{theme.SIGNAL}">Login Items</a>')
        else:
            self.login_note.setText("")
        self.login_note.setVisible(bool(self.login_note.text()))

    def _login_changed(self, on: bool) -> None:
        if error := macos.set_login_item(on):
            log.warning("open at login not changed: %s", error)
            QMessageBox.warning(self, "Open at login", f"macOS did not allow it: {error}")
        self._show_login_state()

    # Access

    def _access(self) -> QGroupBox:
        box = QGroupBox("Access")
        grid = QGridLayout(box)
        self.state_labels: dict[str, QLabel] = {}
        w = self.window_
        for row, (name, edit) in enumerate((("Reference faces", w.refs), ("Folder to scan", w.source), ("Destination", w.dest))):
            where = edit.text().strip() or "not chosen"
            label = QLabel(f"{name}<br><span style='color:{theme.DIM}'>{html.escape(where)}</span>")
            label.setTextFormat(Qt.TextFormat.RichText)
            state = QLabel("Checking…")
            state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.state_labels[name] = state
            grid.addWidget(label, row, 0)
            grid.addWidget(state, row, 1)
        self.fda = QLabel("Full Disk Access: checking…")
        grid.addWidget(self.fda, 3, 0, 1, 2)
        buttons = QHBoxLayout()
        for text, url in (("Files and Folders…", access.PRIVACY_URL), ("Full Disk Access…", access.FULL_DISK_ACCESS_URL)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            buttons.addWidget(b)
        recheck = QPushButton("Check Again")
        recheck.clicked.connect(self._check_access)
        buttons.addStretch()
        buttons.addWidget(recheck)
        grid.addLayout(buttons, 4, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        return box

    def _check_access(self) -> None:
        """Folder checks can stall on a dead share, so they run on a thread and report back."""
        for label in self.state_labels.values():
            label.setText("Checking…")
        texts = {"Reference faces": self.window_.refs.text(), "Folder to scan": self.window_.source.text(),
                 "Destination": self.window_.dest.text()}
        self._checked = _Checked()
        self._checked.done.connect(self._show_access, Qt.ConnectionType.QueuedConnection)
        relay = self._checked

        def work() -> None:
            states = {name: access.folder_state(text) for name, text in texts.items()}
            relay.done.emit(states, access.full_disk_access())

        threading.Thread(target=work, daemon=True).start()

    def _show_access(self, states: dict[str, access.FolderState], fda: bool | None) -> None:
        for name, state in states.items():
            text, color = state.value, STATE_COLORS.get(state, theme.REVIEW)
            if name == "Destination" and state is access.FolderState.MISSING:
                text, color = "Created on first copy or move", theme.DIM
            self.state_labels[name].setText(f"<span style='color:{color}'>{html.escape(text)}</span>")
        self.fda.setText("Full Disk Access: " + {True: "on", False: "off", None: "unknown"}[fda])

    # Files

    def _files(self) -> QGroupBox:
        box = QGroupBox("Files")
        grid = QGridLayout(box)
        for row, (name, folder) in enumerate(
            (("Logs and scan reports", LOG_DIR), ("Face model", MODELS_DIR), ("Undo journal", JOURNAL.parent))
        ):
            label = QLabel(f"{name}<br><span style='color:{theme.DIM}'>{html.escape(str(folder))}</span>")
            label.setTextFormat(Qt.TextFormat.RichText)
            show = QPushButton("Show")
            show.clicked.connect(lambda _=False, f=folder: self.window_._reveal(f))
            grid.addWidget(label, row, 0)
            grid.addWidget(show, row, 1)
        keep_row = QHBoxLayout()
        keep_row.addWidget(QLabel("Keep the newest"))
        keep = QSpinBox()
        keep.setRange(5, 1000)
        keep.setValue(self.settings.value(KEEP_SCANS_KEY, logs.SCANS_KEPT, type=int))
        keep.valueChanged.connect(lambda n: self.settings.setValue(KEEP_SCANS_KEY, n))
        keep_row.addWidget(keep)
        keep_row.addWidget(QLabel("scan logs and reports"))
        keep_row.addStretch()
        grid.addLayout(keep_row, 3, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        return box

    # Maintenance

    def _maintenance(self) -> QGroupBox:
        box = QGroupBox("Maintenance")
        row = QHBoxLayout(box)
        verify = QPushButton("Check Face Model")
        verify.setToolTip("Compare the face model files with their pinned checksums; a damaged model is downloaded again")
        verify.clicked.connect(self._verify_model)
        reset = QPushButton("Reset Settings…")
        reset.setToolTip("Forget the chosen folders, options and window size")
        reset.clicked.connect(self._reset)
        row.addWidget(verify)
        row.addWidget(reset)
        row.addStretch()
        return box

    def _verify_model(self) -> None:
        pack = MODELS_DIR / remote.DEFAULT_PACK
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            intact = pack.is_dir() and remote.pack_intact(pack, verify=True)
        finally:
            QApplication.restoreOverrideCursor()
        if intact:
            QMessageBox.information(self, "Face model", "The face model is complete and matches its checksums.")
            return
        log.warning("face model %s failed its check; downloading it again", pack, extra={"link": pack, "link_text": str(pack)})
        if pack.is_dir():
            shutil.rmtree(pack, ignore_errors=True)
        self.window_.packs = {k: v for k, v in self.window_.packs.items() if v != pack}
        self.window_.ensure_models()
        QMessageBox.information(self, "Face model", "The face model was damaged or missing and is downloading again.")

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self, "Reset Settings", "Forget the chosen folders, options and window size? Logs, the undo journal and "
            "the face model stay."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.settings.clear()
        self.settings.sync()
        log.info("settings reset")
        QMessageBox.information(self, "Reset Settings", "Settings are reset. They take effect when Vaquero opens again.")
