"""关键词分组配置与独立的多行编辑窗口。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextFormat
from PySide6.QtWidgets import (
    QDialog, QFrame, QGroupBox, QHBoxLayout, QLabel, QLayout, QPushButton,
    QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

from config.app_config import RxColorHighlight, config
from config.defaults import RX_COLOR_DEFAULT_GROUP_COUNT
from config.layout import (
    COLOR_SWATCH_BORDER_PX, COLOR_SWATCH_HOVER_BORDER_PX,
    COLOR_SWATCH_RADIUS_PX, COLOR_SWATCH_SIZE, EMOJI_BUTTON_DEFAULT_SIZE,
    EMOJI_BUTTON_SMALL_SIZE, HIGHLIGHT_CLEAR_BUTTON_SIZE,
    HIGHLIGHT_COLOR_ROW_SPACING, HIGHLIGHT_DELETE_BUTTON_SIZE,
    HIGHLIGHT_DIALOG_HEIGHT, HIGHLIGHT_DIALOG_WIDTH, HIGHLIGHT_GROUP_MARGINS, HIGHLIGHT_GROUP_SPACING,
    HIGHLIGHT_EDITOR_HEIGHT, HIGHLIGHT_EDITOR_WIDTH, HIGHLIGHT_TEXT_LINES,
)
from config.runtime import RX_COLOR_MAX_GROUPS
from config.theme import THEME_COLORS
from config.ui import DEFAULT_UI_FONT_SIZE_PT, EMOJI_BUTTON_TEXTS
from gui.color_picker import choose_color
from gui.emoji_button import EmojiButton
from gui.line_number_gutter import LineNumberGutter
from gui.rounded_scroll_bar import RoundedScrollBar
from gui.themed_dialog import ThemedDialog
from utils.font_manager import get_display_font


class _KeywordTextEdit(QTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.selectionChanged.connect(self._highlight_current_line)

    def _highlight_current_line(self) -> None:
        selections = []
        if self.hasFocus() and not self.textCursor().hasSelection():
            selection = QTextEdit.ExtraSelection()
            color = self.palette().highlight().color()
            color.setAlpha(40)
            selection.format.setBackground(color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            selections.append(selection)
        self.setExtraSelections(selections)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._highlight_current_line()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._highlight_current_line()

def _keyword_input(parent: QWidget, text: str) -> tuple[QWidget, QTextEdit]:
    wrap = QWidget(parent)
    wrap.setObjectName("keywordInputWrap")
    row = QHBoxLayout(wrap)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    editor = _KeywordTextEdit()
    editor.setObjectName("keywordInput")
    editor.setAcceptRichText(False)
    editor.setFont(get_display_font(DEFAULT_UI_FONT_SIZE_PT))
    editor.setPlaceholderText("每行一个关键词")
    editor.setFrameShape(QFrame.Shape.NoFrame)
    editor.document().setDocumentMargin(0)
    editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    editor.setVerticalScrollBar(RoundedScrollBar(editor))
    editor.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    editor.setPlainText(text)
    row.addWidget(LineNumberGutter(editor, max_digits=3, parent=wrap))
    row.addWidget(editor, 1)
    return wrap, editor


class KeywordEditorDialog(ThemedDialog):
    def __init__(self, parent=None, group_number: int = 1, text: str = "") -> None:
        super().__init__(parent, f"关键词编辑 - 组 {group_number}")
        self.setFixedSize(HIGHLIGHT_EDITOR_WIDTH, HIGHLIGHT_EDITOR_HEIGHT)
        wrap, self.text_edit = _keyword_input(self, text)
        self.content_layout.addWidget(wrap, 1)

        row = QHBoxLayout()
        row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.accept)
        row.addWidget(self.apply_btn)
        self.content_layout.addLayout(row)
        self.text_edit.setFocus()

    def text(self) -> str:
        return self.text_edit.toPlainText()


class _KeywordGroup(QGroupBox):
    def __init__(self, group: dict, parent: KeywordConfigDialog) -> None:
        super().__init__(parent)
        self._dialog = parent
        self._number = 1
        self._color = group["color"]
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        root = QVBoxLayout(self)
        root.setContentsMargins(*HIGHLIGHT_GROUP_MARGINS)
        root.setSpacing(HIGHLIGHT_GROUP_SPACING)

        color_row = QHBoxLayout()
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(COLOR_SWATCH_SIZE, COLOR_SWATCH_SIZE)
        self.color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_btn.setToolTip("点击选择颜色")
        self.color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self.color_btn)
        self.color_label = QLabel()
        color_row.addWidget(self.color_label)
        self._refresh_color()
        color_row.addSpacing(HIGHLIGHT_COLOR_ROW_SPACING)

        self.case_btn = EmojiButton(EMOJI_BUTTON_TEXTS["search_case"], checkable=True, size=EMOJI_BUTTON_SMALL_SIZE)
        self.case_btn.setChecked(bool(group.get("case_sensitive", False)))
        self.case_btn.setToolTip("区分大小写")
        color_row.addWidget(self.case_btn)
        self.word_btn = EmojiButton(EMOJI_BUTTON_TEXTS["search_word"], checkable=True, size=EMOJI_BUTTON_SMALL_SIZE)
        self.word_btn.setChecked(bool(group.get("whole_word", False)))
        self.word_btn.setToolTip("全字匹配")
        color_row.addWidget(self.word_btn)
        self.enabled_btn = EmojiButton(EMOJI_BUTTON_TEXTS["highlight_enabled"], checkable=True, size=EMOJI_BUTTON_SMALL_SIZE)
        self.enabled_btn.setChecked(bool(group.get("enabled", True)))
        self.enabled_btn.setToolTip("启用关键词变色")
        color_row.addWidget(self.enabled_btn)
        self.delete_btn = EmojiButton(EMOJI_BUTTON_TEXTS["row_delete"], size=HIGHLIGHT_DELETE_BUTTON_SIZE)
        self.delete_btn.clicked.connect(lambda: self._dialog.remove_group(self))
        color_row.addWidget(self.delete_btn)
        color_row.addStretch()
        root.addLayout(color_row)

        input_row = QHBoxLayout()
        texts = group["texts"]
        wrap, self.text_edit = _keyword_input(self, texts if isinstance(texts, str) else "\n".join(texts))
        input_row.addWidget(wrap, 1)
        self.expand_btn = QPushButton("展开")
        self.expand_btn.setFixedSize(HIGHLIGHT_CLEAR_BUTTON_SIZE, HIGHLIGHT_CLEAR_BUTTON_SIZE)
        self.expand_btn.clicked.connect(self._expand)
        input_row.addWidget(self.expand_btn, alignment=Qt.AlignmentFlag.AlignTop)
        self.clear_btn = QPushButton("清除")
        self.clear_btn.setFixedSize(HIGHLIGHT_CLEAR_BUTTON_SIZE, HIGHLIGHT_CLEAR_BUTTON_SIZE)
        self.clear_btn.clicked.connect(self.text_edit.clear)
        input_row.addWidget(self.clear_btn, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(input_row)
        self.text_edit.ensurePolished()
        wrap.setFixedHeight(self.text_edit.fontMetrics().lineSpacing() * HIGHLIGHT_TEXT_LINES)

        # Enter 在编辑区用于换行，其他操作必须显式点击对应按钮。
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)

    def set_number(self, number: int) -> None:
        self._number = number
        self.setTitle(f"组 {number}")
        self.text_edit.setAccessibleName(f"组 {number} 关键词")
        self.delete_btn.setAccessibleName(f"删除组 {number}")

    def _refresh_color(self) -> None:
        colors = THEME_COLORS[config.theme]
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background-color:{self._color or 'transparent'}; border:{COLOR_SWATCH_BORDER_PX}px solid {colors['color_picker_border']}; border-radius:{COLOR_SWATCH_RADIUS_PX}px; }}"
            f"QPushButton:hover {{ border: {COLOR_SWATCH_HOVER_BORDER_PX}px solid {colors['color_picker_border_hover']}; }}"
        )
        self.color_label.setText(self._color or "未选择")

    def _pick_color(self) -> None:
        color = choose_color(QColor(self._color), self._dialog, "选择颜色")
        if color.isValid():
            self._color = color.name().upper()
            self._refresh_color()

    def _expand(self) -> None:
        dialog = KeywordEditorDialog(self._dialog, self._number, self.text_edit.toPlainText())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.text_edit.setPlainText(dialog.text())
        dialog.deleteLater()

    def group(self) -> dict:
        return {
            "color": self._color,
            "texts": [text.strip() for text in self.text_edit.toPlainText().splitlines() if text.strip()],
            "case_sensitive": self.case_btn.isChecked(),
            "whole_word": self.word_btn.isChecked(),
            "enabled": self.enabled_btn.isChecked(),
        }


class KeywordConfigDialog(ThemedDialog):
    def __init__(self, parent=None, groups: list[dict] | None = None) -> None:
        super().__init__(parent, "关键字配置")
        self.setFixedSize(HIGHLIGHT_DIALOG_WIDTH, HIGHLIGHT_DIALOG_HEIGHT)
        self._groups: list[_KeywordGroup] = []
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBar(RoundedScrollBar(self._scroll))
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        container = QWidget()
        self._group_layout = QVBoxLayout(container)
        self._group_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._group_layout.setContentsMargins(0, 0, 0, 0)
        self.add_btn = EmojiButton(EMOJI_BUTTON_TEXTS["row_add"], size=EMOJI_BUTTON_DEFAULT_SIZE)
        self.add_btn.setAccessibleName("新增关键词组")
        self.add_btn.setAutoDefault(False)
        self.add_btn.clicked.connect(self.add_group)
        self._group_layout.addWidget(self.add_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._group_layout.addStretch()
        self._scroll.setWidget(container)
        self.content_layout.addWidget(self._scroll, 1)

        row = QHBoxLayout()
        row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)
        self.apply_btn = QPushButton("应用")
        self.apply_btn.clicked.connect(self.accept)
        row.addWidget(self.apply_btn)
        self.content_layout.addLayout(row)

        initial = groups or [RxColorHighlight.default_group(i) for i in range(RX_COLOR_DEFAULT_GROUP_COUNT)]
        for group in initial:
            self._append_group(group)
        self._update_buttons()

    def _append_group(self, data: dict) -> None:
        group = _KeywordGroup(data, self)
        self._groups.append(group)
        self._group_layout.insertWidget(self._group_layout.indexOf(self.add_btn), group)

    def add_group(self) -> None:
        if len(self._groups) >= RX_COLOR_MAX_GROUPS:
            return
        self._append_group(RxColorHighlight.default_group(len(self._groups)))
        self._update_buttons()
        self._groups[-1].text_edit.setFocus()
        QTimer.singleShot(0, self._ensure_add_visible)

    def remove_group(self, group: _KeywordGroup) -> None:
        if len(self._groups) <= 1 or group not in self._groups:
            return
        self._groups.remove(group)
        self._group_layout.removeWidget(group)
        group.hide()
        group.deleteLater()
        self._update_buttons()

    def _update_buttons(self) -> None:
        count = len(self._groups)
        self.add_btn.setEnabled(count < RX_COLOR_MAX_GROUPS)
        self.add_btn.setToolTip(f"新增关键词组（{count}/{RX_COLOR_MAX_GROUPS}）" if count < RX_COLOR_MAX_GROUPS else f"最多 {RX_COLOR_MAX_GROUPS} 组")
        self.apply_btn.setEnabled(count <= RX_COLOR_MAX_GROUPS)
        self.apply_btn.setToolTip("" if count <= RX_COLOR_MAX_GROUPS else f"请先删除至 {RX_COLOR_MAX_GROUPS} 组以内")
        for number, group in enumerate(self._groups, 1):
            group.set_number(number)
            group.delete_btn.setEnabled(count > 1)
            group.delete_btn.setToolTip("删除关键词组" if count > 1 else "至少保留 1 组")

    def _ensure_add_visible(self) -> None:
        self._scroll.ensureWidgetVisible(self.add_btn, 0, 6)

    def groups(self) -> list[dict]:
        return [group.group() for group in self._groups]

