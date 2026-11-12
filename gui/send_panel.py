"""发送区面板：ASCII/HEX 切换、回车换行、定时发送。紧凑布局适配右侧。"""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication, QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from config.app_config import config, tx_history
from config.layout import (
    SIDEBAR_ICON_SIZE,
    EMOJI_BUTTON_DEFAULT_SIZE,
    INPUT_CORNER_RADIUS,
    SEND_COLUMN_SPACING,
    SEND_HISTORY_BUTTON_SIZE,
    SEND_HISTORY_DELETE_BUTTON_SIZE,
    SEND_HISTORY_MENU_MAX_WIDTH,
    SEND_HISTORY_MENU_SCROLL_INSET,
    SEND_HISTORY_ROW_MIN_EXTRA_HEIGHT,
    SEND_HISTORY_ROW_PADDING_X,
    SEND_HISTORY_ROW_PADDING_Y,
    SEND_HISTORY_ROW_SPACING,
    SEND_HISTORY_SCREEN_MARGIN,
    SEND_HISTORY_TAG_MIN_SIZE,
    SEND_HISTORY_TAG_SPACING,
    SEND_HISTORY_VISIBLE_ROWS,
    SEND_INPUT_HEIGHT,
    SEND_INPUT_ROW_SPACING,
    SEND_INTERVAL_HEIGHT,
    SEND_PANEL_MARGINS,
    SELECTION_OVERLAY_FONT_SIZE_PX,
    SELECTION_OVERLAY_PADDING_X_PX,
    SELECTION_OVERLAY_PADDING_Y_PX,
    SELECTION_OVERLAY_RADIUS_PX,
)
from config.runtime import FEEDBACK_RECOVERY_DELAY_MS
from config.theme import FIXED_COLORS, THEME_COLORS
from config.ui import EMOJI_BUTTON_TEXTS, SEND_HISTORY_MENU_FONT_SIZE_PT
from core.data_converter import append_crlf, bytes_to_hex, hex_string_to_bytes
from gui.emoji_button import EmojiButton, get_emoji_icon_path
from gui.line_number_gutter import LineNumberGutter
from gui.receive_panel import ReceivePanel
from gui.rounded_scroll_bar import RoundedScrollBar
from utils.font_manager import apply_display_fallbacks, get_display_font_css_stack


class _PlainTextPasteEdit(QTextEdit):
    """粘贴时仅保留纯文本，去掉来源样式。"""

    def insertFromMimeData(self, source) -> None:
        self.insertPlainText(source.text())


class SendPanel(QWidget):
    send_requested = Signal(bytes, bool)
    send_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._do_send)
        self._history_items: list[dict] = []       # 历史条目标
        self._history_index: int = -1              # 当前导航索引，-1=未导航
        self._history_draft: str = ""              # 导航前保存的草稿文本
        self._history_menu: QMenu | None = None
        self._history_menu_items: list[dict] = []
        self._history_menu_theme: str | None = None
        self._history_menu_geometry: tuple[int, int] | None = None
        self._history_rows: dict[tuple[str, bool, bool], QPushButton] = {}
        self._history_scroll: QScrollArea | None = None
        self._history_rows_layout: QVBoxLayout | None = None
        self._syncing_input_text = False
        self._suppress_hex_transform = False
        self._cursor_visible_timer = QTimer(self)
        self._cursor_visible_timer.setSingleShot(True)
        self._cursor_visible_timer.timeout.connect(self._ensure_cursor_visible)
        self._build_ui()

    def _build_ui(self) -> None:
        grp = QWidget()
        v = QVBoxLayout(grp)
        v.setContentsMargins(*SEND_PANEL_MARGINS)
        display_font_css = get_display_font_css_stack()
        colors = THEME_COLORS[config.theme]

        self.input = _PlainTextPasteEdit()
        self.input.setObjectName("sendInput")
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setVerticalScrollBar(RoundedScrollBar(self.input))
        # 高度自适应右侧选项按钮总高: 38×3 + 4×2 = 122
        self.input.setFixedHeight(SEND_INPUT_HEIGHT)

        self._input_wrap = QWidget()
        self._input_wrap.setObjectName("sendInputWrap")
        iw = QHBoxLayout(self._input_wrap)
        iw.setContentsMargins(0, 0, 0, 0)
        iw.setSpacing(0)
        self._line_gutter = LineNumberGutter(self.input, max_digits=6, parent=self._input_wrap)
        iw.addWidget(self._line_gutter)
        iw.addWidget(self.input, 1)

        # 历史下拉按钮，叠加在发送输入框右下角
        self._history_btn = EmojiButton(EMOJI_BUTTON_TEXTS["send_history"], size=SEND_HISTORY_BUTTON_SIZE, parent=self._input_wrap)
        self._history_btn.setToolTip("发送历史")
        self._history_btn.clicked.connect(self._show_history_menu)
        self._history_btn.raise_()

        # 清除历史按钮，叠加在发送输入框右下角，▼ 左侧
        self._clear_history_btn = EmojiButton(EMOJI_BUTTON_TEXTS["clear"], size=SEND_HISTORY_BUTTON_SIZE, parent=self._input_wrap)
        self._clear_history_btn.setToolTip("清除发送历史")
        self._clear_history_btn.clicked.connect(self._on_clear_history)
        self._clear_history_btn.raise_()

        # 总发送字节数浮层：固定显示在右下角清空历史按钮左侧
        self._tx_bytes_label = QLabel(self._input_wrap)
        self._tx_bytes_label.setStyleSheet(
            f"QLabel {{ background: {colors['selection_overlay_bg']}; color: {colors['selection_overlay_text']}; border-radius: {SELECTION_OVERLAY_RADIUS_PX}px; "
            f"padding: {SELECTION_OVERLAY_PADDING_Y_PX}px {SELECTION_OVERLAY_PADDING_X_PX}px; font-size: {SELECTION_OVERLAY_FONT_SIZE_PX}px; font-family: {display_font_css}; }}"
        )

        # 选中字节数浮层：仅有选中时显示，风格与接收区一致
        self._sel_bytes_label = QLabel(self._input_wrap)
        self._sel_bytes_label.setStyleSheet(
            f"QLabel {{ background: {colors['selection_overlay_bg']}; color: {colors['selection_overlay_text']}; border-radius: {SELECTION_OVERLAY_RADIUS_PX}px; "
            f"padding: {SELECTION_OVERLAY_PADDING_Y_PX}px {SELECTION_OVERLAY_PADDING_X_PX}px; font-size: {SELECTION_OVERLAY_FONT_SIZE_PX}px; }}"
        )
        self._sel_bytes_label.hide()

        # CRLF 幽灵文本：开启 CRLF 时显示；文本模式为 \r\n，HEX 模式为 0D 0A
        self._crlf_ghost_label = QLabel("\\r\\n", self.input.viewport())
        self._crlf_ghost_label.setObjectName("crlfGhost")
        self._crlf_ghost_label.setContentsMargins(0, 0, 0, 0)
        self._crlf_ghost_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._crlf_ghost_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._crlf_ghost_label.hide()
        self._sync_crlf_ghost_font()

        self._input_wrap.installEventFilter(self)
        self.input.installEventFilter(self)
        self.input.viewport().installEventFilter(self)
        self.input.textChanged.connect(self._on_send_text_changed)
        self.input.cursorPositionChanged.connect(self._schedule_cursor_visible)
        self.input.selectionChanged.connect(self._on_selection_changed)
        self.input.verticalScrollBar().valueChanged.connect(lambda _value: self._update_crlf_ghost())
        self.input.verticalScrollBar().rangeChanged.connect(lambda _min, _max: self._update_crlf_ghost())

        # 输入区 + 右侧控件
        input_row = QHBoxLayout()
        input_row.setSpacing(SEND_INPUT_ROW_SPACING)
        input_row.addWidget(self._input_wrap)

        self.hex_chk = EmojiButton(EMOJI_BUTTON_TEXTS["hex_mode"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.hex_chk.toggled.connect(lambda v: setattr(config, "send_hex", v))
        self.hex_chk.toggled.connect(self._on_hex_mode_toggled)
        self.hex_chk.toggled.connect(lambda v: self.hex_chk.setToolTip("HEX发送: 启用" if v else "HEX发送: 禁用"))
        self.hex_chk.setToolTip("HEX发送: 禁用")

        self.crlf_chk = EmojiButton(EMOJI_BUTTON_TEXTS["crlf_mode"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.crlf_chk.toggled.connect(lambda v: setattr(config, "send_crlf", v))
        self.crlf_chk.toggled.connect(lambda _v: self._update_tx_byte_count())
        self.crlf_chk.toggled.connect(lambda _v: self._update_crlf_ghost())
        self.crlf_chk.toggled.connect(lambda _v: self._update_crlf_tooltip())
        self._update_crlf_tooltip()

        self.auto_chk = EmojiButton(EMOJI_BUTTON_TEXTS["auto_send"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.auto_chk.toggled.connect(self._on_auto_toggled)
        self.auto_chk.toggled.connect(lambda v: self.auto_chk.setToolTip("定时发送: 启用" if v else "定时发送: 禁用"))
        self.auto_chk.setToolTip("定时发送: 禁用")

        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.clicked.connect(self._on_send_clicked)

        self.interval_edit = QLineEdit()
        self.interval_edit.setObjectName("msInput")
        self.interval_edit.setValidator(QIntValidator(10, 600000))
        self.interval_edit.setStyleSheet(f"QLineEdit {{ font-family: {display_font_css}; }}")
        self.interval_edit.setPlaceholderText(" ms ")
        self.interval_edit.textChanged.connect(self._on_interval_changed)

        # 发送列：发送按钮 + 时间输入
        send_col = QVBoxLayout()
        send_col.setSpacing(SEND_COLUMN_SPACING)
        send_col.addWidget(self.send_btn)
        send_col.addWidget(self.interval_edit)
        input_row.addLayout(send_col)

        # 选项列：HEX / \r\n / 自动 纵向对齐
        opts_col = QVBoxLayout()
        opts_col.setSpacing(SEND_COLUMN_SPACING)
        opts_col.addWidget(self.hex_chk)
        opts_col.addWidget(self.crlf_chk)
        opts_col.addWidget(self.auto_chk)
        input_row.addLayout(opts_col)

        self._sync_right_col_sizes()
        v.addLayout(input_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(grp)

        self._load_history()
        QTimer.singleShot(0, self._warm_history_menu)
        self._update_tx_byte_count()

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            ms = self._interval_ms()
            if ms is None:
                self._flash_interval()
                self.auto_chk.setChecked(False)
                return
            self._timer.start(ms)
        else:
            self._timer.stop()

    def _interval_ms(self) -> int | None:
        try:
            ms = int(self.interval_edit.text().strip())
        except ValueError:
            return None
        return ms if 10 <= ms <= 600000 else None

    def _on_interval_changed(self, _text: str) -> None:
        if not self.auto_chk.isChecked():
            return
        ms = self._interval_ms()
        if ms is None:
            self._timer.stop()
            return
        self._timer.start(ms)

    def _flash_interval(self) -> None:
        self.interval_edit.setStyleSheet(
            f"QLineEdit {{ border: 2px solid {THEME_COLORS[config.theme]['status_error_bg_hover']}; border-radius: {INPUT_CORNER_RADIUS}px; }}"
        )
        QTimer.singleShot(FEEDBACK_RECOVERY_DELAY_MS, lambda: self.interval_edit.setStyleSheet(""))

    @Slot()
    def _on_send_clicked(self) -> None:
        self.send_clicked.emit()
        self._do_send(flash_on_empty=True)

    @Slot()
    def _do_send(self, flash_on_empty: bool = False) -> None:
        text = self.input.toPlainText()
        le = config.tx_line_ending
        if self.hex_chk.isChecked():
            if not text.strip():
                if self.crlf_chk.isChecked():
                    data = le.encode("ascii")
                else:
                    if flash_on_empty:
                        self._flash_empty_input()
                    return
            else:
                normalized, data = self._hex_normalize_for_send(text)
                if normalized and normalized != text:
                    self._replace_input_text(normalized)
                    self._update_crlf_ghost()
                    QTimer.singleShot(0, self._update_crlf_ghost)
                if self.crlf_chk.isChecked():
                    data += le.encode("ascii")
        else:
            if not text:
                if self.crlf_chk.isChecked():
                    data = le.encode("ascii")
                else:
                    if flash_on_empty:
                        self._flash_empty_input()
                    return
            else:
                normalized = self._normalize_line_endings_for_send(text)
                normalized = append_crlf(normalized, le) if self.crlf_chk.isChecked() else normalized
                data = normalized.encode(config.tx_encoding, errors="replace")
        if data:
            self.send_requested.emit(data, self.hex_chk.isChecked())
            tx_history.add(self.input.toPlainText(), self.hex_chk.isChecked(), self.crlf_chk.isChecked())
            self._history_items = tx_history.items
            self._history_index = -1
            self._history_draft = ""
            self._update_tx_byte_count()

    def _flash_empty_input(self) -> None:
        self._input_wrap.setStyleSheet(
            f"QWidget#sendInputWrap {{ border: 2px solid {THEME_COLORS[config.theme]['status_error_bg_hover']}; border-radius: {INPUT_CORNER_RADIUS}px; }}"
        )
        QTimer.singleShot(FEEDBACK_RECOVERY_DELAY_MS, self._restore_input_style)

    def _restore_input_style(self) -> None:
        self._input_wrap.setStyleSheet("")

    def stop_timer(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
        self.auto_chk.setChecked(False)

    def _sync_right_col_sizes(self) -> None:
        """右侧两列：发送按钮正方形，option 按钮与接收区一致 38×38。"""
        send_h = EMOJI_BUTTON_DEFAULT_SIZE + SEND_COLUMN_SPACING + EMOJI_BUTTON_DEFAULT_SIZE
        self.send_btn.setFixedSize(send_h, send_h)
        self.interval_edit.setFixedSize(send_h, SEND_INTERVAL_HEIGHT)

    # ---------- HEX 规范化与计数 ----------
    @staticmethod
    def _hex_clean(text: str) -> str:
        return "".join(ch for ch in text.upper() if ch in "0123456789ABCDEF")

    @classmethod
    def _hex_format_pairs(cls, text: str) -> str:
        cleaned = cls._hex_clean(text)
        return " ".join(cleaned[i:i + 2] for i in range(0, len(cleaned), 2))

    @classmethod
    def _hex_to_bytes_lenient(cls, text: str) -> bytes:
        cleaned = cls._hex_clean(text)
        if not cleaned:
            return b""
        if len(cleaned) % 2:
            cleaned = "0" + cleaned
        return bytes.fromhex(cleaned)

    @classmethod
    def _hex_normalize_for_send(cls, text: str) -> tuple[str, bytes]:
        cleaned = cls._hex_clean(text)
        if not cleaned:
            return "", b""
        if len(cleaned) % 2:
            cleaned = cleaned[:-1] + "0" + cleaned[-1]
        normalized = cls._hex_format_pairs(cleaned)
        return normalized, bytes.fromhex(cleaned)

    @classmethod
    def _map_hex_cursor_pos(cls, raw_text: str, normalized_text: str, old_pos: int) -> int:
        old_pos = max(0, min(old_pos, len(raw_text)))
        nibble_count = len(cls._hex_clean(raw_text[:old_pos]))
        total_nibbles = len(cls._hex_clean(normalized_text))
        nibble_count = min(nibble_count, total_nibbles)
        if nibble_count <= 0:
            return 0
        return nibble_count + (nibble_count - 1) // 2

    def _replace_input_text(self, text: str, cursor_pos: int | None = None) -> None:
        self._syncing_input_text = True
        self.input.setPlainText(text)
        cursor = self.input.textCursor()
        if cursor_pos is None:
            cursor.movePosition(cursor.MoveOperation.End)
        else:
            cursor.setPosition(max(0, min(cursor_pos, len(text))))
        self.input.setTextCursor(cursor)
        self._syncing_input_text = False

    def _on_hex_mode_toggled(self, checked: bool) -> None:
        if self._suppress_hex_transform:
            self._update_tx_byte_count()
            self._update_selection_count()
            self._update_crlf_ghost()
            return

        text = self.input.toPlainText()
        if checked:
            if text:
                normalized = self._normalize_line_endings_for_send(text)
                encoded = normalized.encode(config.tx_encoding, errors="replace")
                self._replace_input_text(bytes_to_hex(encoded))
        else:
            if text:
                raw = self._hex_to_bytes_lenient(text)
                self._replace_input_text(raw.decode(config.tx_encoding, errors="replace"))

        self._update_tx_byte_count()
        self._update_selection_count()
        self._update_crlf_ghost()

    def _on_send_text_changed(self) -> None:
        if self._syncing_input_text:
            return
        if self.hex_chk.isChecked():
            raw = self.input.toPlainText()
            old_pos = self.input.textCursor().position()
            normalized = self._hex_format_pairs(raw)

            if normalized != raw:
                new_pos = self._map_hex_cursor_pos(raw, normalized, old_pos)
                self._replace_input_text(normalized, cursor_pos=new_pos)
        self._update_tx_byte_count()
        self._update_selection_count()
        self._update_crlf_ghost()
        self._schedule_cursor_visible()

    def _schedule_cursor_visible(self) -> None:
        """布局完成后滚动到光标行，覆盖多行输入、粘贴与历史命令切换。"""
        self._cursor_visible_timer.start(0)

    def _ensure_cursor_visible(self) -> None:
        self.input.ensureCursorVisible()

    def _on_selection_changed(self) -> None:
        self._update_selection_count()

    def _normalize_line_endings_for_send(self, text: str) -> str:
        """QTextEdit 内部会把换行统一为 \n；发送前按配置换行符还原。"""
        if not text:
            return text
        return text.replace("\n", config.tx_line_ending)

    def _current_tx_bytes(self) -> int:
        text = self.input.toPlainText()
        if self.hex_chk.isChecked():
            if not text.strip():
                return len(config.tx_line_ending.encode("ascii")) if self.crlf_chk.isChecked() else 0
            try:
                size = len(hex_string_to_bytes(text))
            except ValueError:
                size = len(self._hex_to_bytes_lenient(text))
            if self.crlf_chk.isChecked():
                size += len(config.tx_line_ending.encode("ascii"))
            return size

        normalized = self._normalize_line_endings_for_send(text)
        size = len(normalized.encode(config.tx_encoding, errors="replace"))
        if self.crlf_chk.isChecked():
            size += len(config.tx_line_ending.encode(config.tx_encoding, errors="replace"))
        return size

    def _update_tx_byte_count(self) -> None:
        n = self._current_tx_bytes()
        if n == 0:
            self._tx_bytes_label.hide()
        else:
            self._tx_bytes_label.setText(f"发送: {n} Byte")
            self._tx_bytes_label.adjustSize()
            self._tx_bytes_label.show()
        self._position_overlay_btns()

    def _update_selection_count(self) -> None:
        text = self.input.textCursor().selectedText()
        if not text:
            self._sel_bytes_label.hide()
            return

        if self.hex_chk.isChecked():
            text = text.replace("\u2029", "")
            n = len(self._hex_to_bytes_lenient(text))
        else:
            text = text.replace("\u2029", "\r\n")
            n = len(text.encode(config.tx_encoding, errors="replace"))

        self._sel_bytes_label.setText(f"选中: {n} Byte")
        self._sel_bytes_label.adjustSize()
        self._sel_bytes_label.show()
        self._position_overlay_btns()
        QTimer.singleShot(0, self._position_overlay_btns)

    # ---------- 发送历史 ----------
    def _load_history(self) -> None:
        self._history_items = tx_history.items
        self._history_index = -1
        self._history_draft = ""

    def _position_overlay_btns(self) -> None:
        """定位 sendInput 右下角 overlay，并为滚动条预留空间。"""
        w = self._input_wrap.width()
        h = self._input_wrap.height()
        if w > 0 and h > 0:
            scrollbar_w = self.input.verticalScrollBar().width()
            right_edge = w - scrollbar_w - 6
            history_x = right_edge - self._history_btn.width()
            clear_x = history_x - self._clear_history_btn.width() - 2
            self._history_btn.move(max(0, history_x), h - self._history_btn.height() - 2)
            self._clear_history_btn.move(max(0, clear_x), h - self._clear_history_btn.height() - 2)

            self._tx_bytes_label.adjustSize()
            tx_w = self._tx_bytes_label.sizeHint().width()
            tx_h = self._tx_bytes_label.sizeHint().height()
            tx_x = self._clear_history_btn.x() - tx_w - 8
            tx_y = h - tx_h - 8

            if not self._sel_bytes_label.isHidden():
                self._sel_bytes_label.adjustSize()
                sel_w = self._sel_bytes_label.sizeHint().width()
                sel_h = self._sel_bytes_label.sizeHint().height()
                # 横向排布：左侧选中，右侧发送
                sel_x = tx_x - sel_w - 6
                sel_y = h - sel_h - 8
                self._sel_bytes_label.setGeometry(sel_x, sel_y, sel_w, sel_h)

            self._tx_bytes_label.setGeometry(tx_x, tx_y, tx_w, tx_h)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._position_overlay_btns()
        QTimer.singleShot(0, self._position_overlay_btns)
        QTimer.singleShot(0, self._update_crlf_ghost)

    def eventFilter(self, watched, event) -> bool:
        # 定位历史按钮
        if watched is self._input_wrap and event.type() == QEvent.Type.Resize:
            self._position_overlay_btns()
            return False

        if watched in (self.input, self.input.viewport()) and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._update_crlf_ghost)
            return False

        # 发送输入框 ↑↓ 历史切换 / Ctrl+Enter 发送 / Enter 插入换行符
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up:
                return self._cycle_send_history(up=True)
            if key == Qt.Key.Key_Down:
                return self._cycle_send_history(up=False)
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._on_send_clicked()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                if self.hex_chk.isChecked():
                    self.input.insertPlainText(self._line_ending_hex())
                else:
                    self.input.insertPlainText(config.tx_line_ending)
                self._update_tx_byte_count()
                self._update_selection_count()
                self._update_crlf_ghost()
                return True
        return super().eventFilter(watched, event)

    def _line_ending_text(self) -> str:
        """返回当前换行符的可读表示。"""
        le = config.tx_line_ending
        return le.replace("\r", "\\r").replace("\n", "\\n")

    def _update_crlf_tooltip(self) -> None:
        le_display = self._line_ending_text()
        self.crlf_chk.setToolTip(f"追加{le_display}: 启用" if self.crlf_chk.isChecked() else f"追加{le_display}: 禁用")

    def _line_ending_hex(self) -> str:
        """返回当前换行符的 HEX 表示。"""
        return " ".join(f"{b:02X}" for b in config.tx_line_ending.encode("ascii"))

    def _update_crlf_ghost(self) -> None:
        visible = self.crlf_chk.isChecked()
        if not visible:
            self._crlf_ghost_label.hide()
            return

        self._sync_crlf_ghost_font()
        self._crlf_ghost_label.setText(" " + self._line_ending_hex() if self.hex_chk.isChecked() else self._line_ending_text())

        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        end_rect = self.input.cursorRect(cursor)

        self._crlf_ghost_label.adjustSize()
        lw = self._crlf_ghost_label.sizeHint().width()
        lh = self._crlf_ghost_label.sizeHint().height()
        vw = self.input.viewport().width()
        vh = self.input.viewport().height()

        x = end_rect.x() + 2
        y = end_rect.y() - 1

        if x + lw > vw - 4:
            x = max(2, round(self.input.document().documentMargin()))
            y = end_rect.y() + end_rect.height()

        if x + lw > vw or y < 0 or y + lh > vh:
            self._crlf_ghost_label.hide()
            return

        self._crlf_ghost_label.setGeometry(x, y, lw, lh)
        self._crlf_ghost_label.show()
        self._crlf_ghost_label.raise_()

    def _sync_crlf_ghost_font(self) -> None:
        self._crlf_ghost_label.setFont(self.input.font())

    def _cycle_send_history(self, up: bool) -> bool:
        """↑↓ 切历史：多行光标移动优先，单行/边界切历史。
        ↑ → 向旧遍历(direction=+1)，↓ → 向新回退(direction=-1)。"""
        self._history_items = tx_history.items
        if not self._history_items:
            return False
        block_count = self.input.document().blockCount()
        cursor = self.input.textCursor()
        current_line = cursor.blockNumber()

        if up:
            # 非首行：光标上移；首行/单行：向旧遍历
            if block_count > 1 and current_line > 0:
                return False
            self._navigate_history(direction=1)
        else:
            # 非末行：光标下移；末行/单行：向新回退
            if block_count > 1 and current_line < block_count - 1:
                return False
            self._navigate_history(direction=-1)
        return True

    def _navigate_history(self, direction: int) -> None:
        """direction: +1=向旧(↑), -1=向新(↓)。"""
        items = self._history_items
        if not items:
            return

        # 首次进入导航，保存当前草稿
        if self._history_index == -1:
            self._history_draft = self.input.toPlainText()
            # ponytail: 初始态 ↑(向旧) 跳到最近(0)，↓(向新) 停在 -1
            if direction == 1:
                new_idx = 0
            else:
                new_idx = -1
        else:
            new_idx = self._history_index + direction

        if new_idx < -1:
            new_idx = -1
        if new_idx >= len(items):
            new_idx = len(items) - 1
        if new_idx == self._history_index:
            return
        self._history_index = new_idx

        if new_idx == -1:
            # 恢复草稿
            self._suppress_hex_transform = True
            self.input.setPlainText(self._history_draft)
            self.hex_chk.setChecked(False)
            self.crlf_chk.setChecked(False)
            self._suppress_hex_transform = False
            cursor = self.input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.input.setTextCursor(cursor)
            self._update_tx_byte_count()
            self._update_selection_count()
            return

        entry = items[new_idx]
        self._apply_history_entry(entry)

    def _apply_history_entry(self, entry: dict) -> None:
        """将历史条目应用到输入框和 HEX/CRLF 按钮。"""
        self._suppress_hex_transform = True
        self.input.setPlainText(entry.get("text", ""))
        self.hex_chk.setChecked(entry.get("hex", False))
        self.crlf_chk.setChecked(entry.get("crlf", False))
        self._suppress_hex_transform = False
        cursor = self.input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.input.setTextCursor(cursor)
        self._update_tx_byte_count()
        self._update_selection_count()

    def _on_menu_history_selected(self, entry: dict) -> None:
        """▼ 菜单选中条目：应用并重置导航状态。"""
        self._history_index = -1
        self._history_draft = ""
        self._apply_history_entry(entry)

    def _on_clear_history(self) -> None:
        """🗑 清除全部发送历史并清空输入框。"""
        tx_history.clear()
        self._invalidate_history_menu()
        self.input.clear()
        self._history_items = []
        self._history_index = -1
        self._history_draft = ""
        self._update_tx_byte_count()
        self._update_selection_count()

    def _invalidate_history_menu(self) -> None:
        menu = self._history_menu
        self._history_menu = None
        self._history_menu_items = []
        self._history_menu_theme = None
        self._history_menu_geometry = None
        self._history_rows.clear()
        self._history_scroll = None
        self._history_rows_layout = None
        if menu is not None:
            menu.close()
            menu.deleteLater()

    def _popup_history_menu(self, menu: QMenu) -> None:
        anchor = self._history_btn
        menu.popup(QPoint(anchor.mapToGlobal(anchor.rect().center()).x() - menu.width() // 2, anchor.mapToGlobal(anchor.rect().topLeft()).y()))

    def _warm_history_menu(self) -> None:
        if self._history_items:
            self._show_history_menu(show=False)
            if self._history_menu is not None:
                self._history_menu.adjustSize()

    def _show_history_menu(self, _checked: bool = False, *, show: bool = True) -> None:
        """历史内容变化时只同步行，主题或可用尺寸变化时重建菜单。"""
        self._history_items = tx_history.items
        items = self._history_items
        if not items:
            self._invalidate_history_menu()
            return

        anchor = self._history_btn
        screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center())) or QGuiApplication.primaryScreen()
        menu_width = min(SEND_HISTORY_MENU_MAX_WIDTH, screen.availableGeometry().width() - SEND_HISTORY_SCREEN_MARGIN) if screen else SEND_HISTORY_MENU_MAX_WIDTH
        max_height = screen.availableGeometry().height() - 32 if screen else 600
        geometry = (menu_width, max_height)
        if self._history_menu_theme != config.theme or self._history_menu_geometry != geometry:
            self._invalidate_history_menu()
        if self._history_menu is None:
            self._create_history_menu(menu_width)
            self._history_menu_theme = config.theme
            self._history_menu_geometry = geometry
        if self._history_menu_items != items:
            self._sync_history_menu(items, menu_width, max_height)
            if show:
                self._history_scroll.verticalScrollBar().setValue(0)
        if show:
            self._popup_history_menu(self._history_menu)

    def _create_history_menu(self, menu_width: int) -> None:
        anchor = self._history_btn

        # 内联子类：在 showEvent 中精确对齐+屏幕边界 clamp
        class _AnchorMenu(QMenu):
            def showEvent(menu_self, event) -> None:
                super().showEvent(event)
                # 菜单水平居中于历史按钮，并限制在屏幕可用区域。
                screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center()))
                if screen is None:
                    screen = QGuiApplication.primaryScreen()
                sr = screen.availableGeometry() if screen else None
                center = anchor.mapToGlobal(anchor.rect().center())
                pos = QPoint(center.x() - menu_self.width() // 2, anchor.mapToGlobal(anchor.rect().topLeft()).y() - menu_self.height())
                if sr:
                    if pos.x() < sr.x():
                        pos.setX(sr.x())
                    if pos.y() < sr.y():
                        pos.setY(sr.y())
                    right_edge = pos.x() + menu_self.width()
                    if right_edge > sr.right():
                        pos.setX(sr.right() - menu_self.width())
                menu_self.move(pos)
                if menu_self.minimumWidth() > 0 and menu_self.width() < menu_self.minimumWidth():
                    menu_self.resize(menu_self.minimumWidth(), menu_self.height())

        menu = _AnchorMenu(self)
        menu.setObjectName("sendHistoryMenu")
        menu.setWindowFlags(
            menu.windowFlags()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        scroll = QScrollArea(menu)
        scroll.setObjectName("sendHistoryScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBar(RoundedScrollBar(scroll))
        rows_widget = QWidget()
        rows_widget.setObjectName("sendHistoryRows")
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(0)
        scroll.setWidget(rows_widget)
        scroll.setFixedWidth(menu_width - SEND_HISTORY_MENU_SCROLL_INSET)
        act = QWidgetAction(menu)
        act.setDefaultWidget(scroll)
        menu.addAction(act)
        menu.setFixedWidth(menu_width)
        self._history_menu = menu
        self._history_scroll = scroll
        self._history_rows_layout = rows_layout

    @staticmethod
    def _history_row_key(entry: dict) -> tuple[str, bool, bool]:
        return entry.get("text", ""), bool(entry.get("hex", False)), bool(entry.get("crlf", False))

    def _sync_history_menu(self, items: list[dict], menu_width: int, max_height: int) -> None:
        keys = [self._history_row_key(item) for item in items]
        for key in self._history_rows.keys() - set(keys):
            row = self._history_rows.pop(key)
            self._history_rows_layout.removeWidget(row)
            row.hide()
            row.deleteLater()

        hist_font = QFont()
        apply_display_fallbacks(hist_font)
        hist_font.setPointSize(SEND_HISTORY_MENU_FONT_SIZE_PT)
        fm = QFontMetrics(hist_font)
        display_font_css = get_display_font_css_stack()
        for index, (key, item) in enumerate(zip(keys, items)):
            row = self._history_rows.get(key)
            if row is None:
                row = self._create_history_row(item, menu_width, fm, display_font_css)
                self._history_rows[key] = row
            current = self._history_rows_layout.itemAt(index)
            if current is None or current.widget() is not row:
                self._history_rows_layout.insertWidget(index, row)
            row.show()

        visible_rows = [self._history_rows[key] for key in keys[:SEND_HISTORY_VISIBLE_ROWS]]
        height = min(max_height, sum(max(row.minimumHeight(), row.sizeHint().height()) for row in visible_rows))
        extra_height = self._history_menu.height() - self._history_scroll.height()
        self._history_scroll.setFixedHeight(height)
        if self._history_menu_items:
            # 固定高度，避免 QMenu 首次弹出时用缓存尺寸覆盖新增行所需的高度。
            self._history_menu.setFixedHeight(height + extra_height)
        else:
            self._history_menu.adjustSize()
        self._history_menu_items = list(items)

    def _remove_history_entry(self, entry: dict) -> None:
        if tx_history.remove(entry):
            self._history_index = -1
            self._history_draft = ""
        # 同时刷新缓存快照，避免删除后再次展开时重建；也处理已被淘汰的条目。
        self._show_history_menu(show=False)
        menu = self._history_menu
        if menu is not None and menu.isVisible():
            anchor = self._history_btn
            screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center())) or QGuiApplication.primaryScreen()
            top = anchor.mapToGlobal(anchor.rect().topLeft()).y() - menu.height()
            menu.move(menu.x(), max(screen.availableGeometry().top(), top) if screen else top)

    def _create_history_row(self, item: dict, menu_width: int, fm: QFontMetrics, display_font_css: str) -> QPushButton:
        delete_size = SEND_HISTORY_DELETE_BUTTON_SIZE
        text = item.get("text", "")
        hex_on = item.get("hex", False)
        crlf_on = item.get("crlf", False)
        display = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", ReceivePanel.ECHO_NEWLINE_ICON)
        tags_w = 0
        tags: list[str] = []
        if hex_on or crlf_on:
            if hex_on:
                tags.append(EMOJI_BUTTON_TEXTS["hex_mode"])
            if crlf_on:
                tags.append(EMOJI_BUTTON_TEXTS["crlf_mode"])
            tag_icon_side = max(SEND_HISTORY_TAG_MIN_SIZE, fm.height())
            tags_w = len(tags) * tag_icon_side + max(0, len(tags) - 1) * SEND_HISTORY_TAG_SPACING + 6

        # 用 QPushButton 作行容器：原生 :hover 伪态，稳定可靠
        row = QPushButton(self._history_scroll.widget())
        row.setFlat(True)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setMinimumHeight(fm.height() + SEND_HISTORY_ROW_MIN_EXTRA_HEIGHT)
        row.setStyleSheet(
            "QPushButton { border: none; border-radius: 4px; margin: 2px 3px; text-align: left; padding: 0px; background: transparent; }"
            f"QPushButton:hover {{ background: {THEME_COLORS[config.theme]['history_hover']}; }}"
        )
        row.clicked.connect(lambda checked=False, e=item: self._on_menu_history_selected(e))
        row.clicked.connect(self._history_menu.close)

        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(
            SEND_HISTORY_ROW_PADDING_X,
            SEND_HISTORY_ROW_PADDING_Y,
            SEND_HISTORY_ROW_PADDING_X,
            SEND_HISTORY_ROW_PADDING_Y,
        )
        row_layout.setSpacing(SEND_HISTORY_ROW_SPACING)

        label_width = menu_width - 8 - 14 - 16 - 6 - delete_size - tags_w
        shown = display
        if fm.horizontalAdvance(display) > label_width:
            shown = fm.elidedText(display, Qt.TextElideMode.ElideRight, label_width - fm.horizontalAdvance("...")).removesuffix("…") + "..."
        text_lbl = QLabel(escape(shown).replace(ReceivePanel.ECHO_NEWLINE_ICON, f'<span style="color: {FIXED_COLORS["history_newline"]};">{ReceivePanel.ECHO_NEWLINE_ICON}</span>'))
        text_lbl.setTextFormat(Qt.TextFormat.RichText)
        text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_lbl.setWordWrap(False)
        text_lbl.setFixedWidth(label_width)
        text_lbl.setStyleSheet(f"font-size: {SEND_HISTORY_MENU_FONT_SIZE_PT}pt; font-family: {display_font_css};")
        row_layout.addWidget(text_lbl)

        if hex_on or crlf_on:
            tags_widget = QWidget(row)
            tags_widget.setFixedWidth(tags_w - 6)
            tags_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            tags_layout = QHBoxLayout(tags_widget)
            tags_layout.setContentsMargins(0, 0, 0, 0)
            tags_layout.setSpacing(SEND_HISTORY_TAG_SPACING)
            for tag in tags:
                tag_lbl = QLabel(tags_widget)
                tag_lbl.setFixedSize(tag_icon_side, tag_icon_side)
                tag_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                icon_path = get_emoji_icon_path(tag)
                if icon_path:
                    pixmap = QPixmap(str(icon_path)).scaled(
                        tag_icon_side,
                        tag_icon_side,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    tag_lbl.setPixmap(pixmap)
                tags_layout.addWidget(tag_lbl)
            row_layout.addWidget(tags_widget)

        del_btn = EmojiButton(EMOJI_BUTTON_TEXTS["row_delete"], size=delete_size, parent=row)
        del_btn.setToolTip("删除此条发送历史")
        del_btn.clicked.connect(lambda checked=False, e=item: self._remove_history_entry(e))
        row_layout.addWidget(del_btn)

        return row

    def set_display_font(self, family: str, size: int) -> None:
        """仅设置发送输入框的显示字体。"""
        font = QFont()
        apply_display_fallbacks(font, family if family else None)
        font.setPointSize(size)
        self.input.setFont(font)
        self.input.setStyleSheet(f"font-size: {size}pt;")
        self._line_gutter.sync_metrics()
        self._update_crlf_ghost()
