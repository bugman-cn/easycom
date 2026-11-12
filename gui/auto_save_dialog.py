from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)
from config.app_config import get_runtime_root_dir
from config.layout import AUTO_SAVE_DIALOG_MIN_WIDTH, EMOJI_BUTTON_SMALL_SIZE
from config.ui import EMOJI_BUTTON_TEXTS
from gui.emoji_button import EmojiButton
from gui.themed_dialog import ThemedDialog


class AutoSaveDialog(ThemedDialog):
    def __init__(self, parent=None, enabled: bool = False, save_dir: str = "auto-gen/uart-log") -> None:
        super().__init__(parent, "自动保存日志")
        self.setMinimumWidth(AUTO_SAVE_DIALOG_MIN_WIDTH)

        root = self.content_layout

        enabled_row = QHBoxLayout()
        self.enabled_btn = EmojiButton(EMOJI_BUTTON_TEXTS["highlight_enabled"], checkable=True, size=EMOJI_BUTTON_SMALL_SIZE)
        self.enabled_btn.setChecked(enabled)
        self.enabled_btn.setToolTip("启用自动保存日志")
        enabled_row.addWidget(self.enabled_btn)
        enabled_row.addWidget(QLabel("启用自动保存日志"))
        enabled_row.addStretch()
        root.addLayout(enabled_row)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("保存目录:"))
        self.path_edit = QLineEdit(self._to_abs_path(save_dir or "auto-gen/uart-log"))
        path_row.addWidget(self.path_edit, stretch=1)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(browse_btn)
        root.addLayout(path_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("应用")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

    def _on_browse(self) -> None:
        current = self.path_edit.text().strip() or "auto-gen/uart-log"
        start_dir = self._to_abs_path(current)
        selected = QFileDialog.getExistingDirectory(self, "选择自动保存日志目录", start_dir)
        if selected:
            self.path_edit.setText(self._to_abs_path(selected))

    @staticmethod
    def _to_abs_path(path_text: str) -> str:
        p = Path(path_text).expanduser()
        if not p.is_absolute():
            p = get_runtime_root_dir() / p
        return str(p.resolve())

    def enabled(self) -> bool:
        return self.enabled_btn.isChecked()

    def save_dir(self) -> str:
        return self.path_edit.text().strip() or "auto-gen/uart-log"
