"""终端接收面板：CSI 终端仿真接收区，可直敲输入，布局对齐普通接收区。"""
from __future__ import annotations

from bisect import bisect_left

import codecs
import re
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QKeySequence, QPainterPath, QRegion, QShortcut, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config.app_config import config, search_history
from config.layout import (
    SIDEBAR_ICON_SIZE,
    CONTROL_BAR_SPACING,
    CONTROL_BAR_VERTICAL_MARGIN,
    RECEIVE_BODY_LEFT_MARGIN,
    RECEIVE_BODY_RIGHT_MARGIN,
    RECEIVE_CONTROL_BAR_WIDTH,
    RECEIVE_WRAP_RADIUS,
    SEARCH_COUNT_LABEL_WIDTH,
    SEARCH_ROW_SPACING,
    SEARCH_WRAP_TOP_MARGIN,
    SELECTION_OVERLAY_BOTTOM_MARGIN_PX,
    SELECTION_OVERLAY_FONT_SIZE_PX,
    SELECTION_OVERLAY_PADDING_X_PX,
    SELECTION_OVERLAY_PADDING_Y_PX,
    SELECTION_OVERLAY_RADIUS_PX,
    SEARCH_COLOR_BUTTON_BORDER_PX,
    SEARCH_COLOR_BUTTON_RADIUS_PX,
    SELECTION_OVERLAY_RIGHT_MARGIN_PX,
)
from config.runtime import (
    FEEDBACK_RECOVERY_DELAY_MS,
    PANEL_ANIMATION_DURATION_MS,
    RX_DISPLAY_MAX_LINES,
    SEARCH_DEBOUNCE_INTERVAL_MS,
    TERMINAL_RX_FLUSH_TIMEOUT_MS,
)
from config.theme import THEME_COLORS
from config.ui import DEFAULT_DISPLAY_FONT_SIZE_PT, EMOJI_BUTTON_TEXTS
from gui.emoji_button import EmojiButton
from gui.document_search import DocumentSearch
from gui.line_number_gutter import LineNumberGutter
from gui.rounded_scroll_bar import RoundedScrollBar, configure_combo_popup
from utils.file_logger import save_text
from utils.font_manager import (
    get_default_font,
    get_display_font_families,
)


class TerminalReceivePanel(QWidget):
    """终端模式接收面板：按键即发，CSI 仿真，布局与普通接收区一致。"""

    _CSI_RE = re.compile(r"\x1b\[([0-9;?]*)([@-~])")
    _PARTIAL_CSI_RE = re.compile(rb"(?:\x1b|\x1b\[[0-9;?]{0,15})$")
    _WRAP_RADIUS = RECEIVE_WRAP_RADIUS

    _ANSI_FG: dict[int, str] = {
        30: "#0C0C0C", 31: "#C50F1F", 32: "#13A10E", 33: "#C19C00",
        34: "#0037DA", 35: "#881798", 36: "#3A96DD", 37: "#CCCCCC",
        90: "#767676", 91: "#E74856", 92: "#16C60C", 93: "#F9F1A5",
        94: "#3B78FF", 95: "#B4009E", 96: "#61D6D6", 97: "#F2F2F2",
    }
    _ANSI_BG: dict[int, str] = {
        40: "#0C0C0C", 41: "#C50F1F", 42: "#13A10E", 43: "#C19C00",
        44: "#0037DA", 45: "#881798", 46: "#3A96DD", 47: "#CCCCCC",
        100: "#767676", 101: "#E74856", 102: "#16C60C", 103: "#F9F1A5",
        104: "#3B78FF", 105: "#B4009E", 106: "#61D6D6", 107: "#F2F2F2",
    }

    send_requested = Signal(bytes)
    cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # SGR 状态
        self._sgr_fg: str | None = None
        self._sgr_bg: str | None = None
        self._sgr_bold: bool = False
        self._cursor_stack: list[tuple[int, int, str | None, str | None, bool]] = []
        self._serial_open = False
        self._local_echo = False
        self._term_cursor_pos = 0
        self._sel_dragging = False
        self._zoom_wheel_delta = 0
        self._line_click_timer = QElapsedTimer()
        self._line_click_held = False
        self._auto_log_fp = None
        self._auto_log_path = ""
        self._byte_buf = bytearray()
        self._decoder_encoding = config.encoding
        self._decoder = codecs.getincrementaldecoder(config.encoding)(errors="replace")
        self._hist_nav_index: int = -1
        self._hist_draft: str = ""
        self._syncing_search_text = False
        self._search_history: list[str] = []
        self._search_extras: list[QTextEdit.ExtraSelection] = []
        self._search_matches: list[tuple[int, int]] = []
        self._search_nav_index: int = -1
        self._search_nav_activated = False
        self._cursor_line_sel = QTextEdit.ExtraSelection()
        self._cursor_line_sel.format.setBackground(QColor(THEME_COLORS[config.theme]["cursor_line_selection"]))
        self._cursor_line_sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_INTERVAL_MS)
        self._search_timer.timeout.connect(self._highlight_terminal_search)

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(TERMINAL_RX_FLUSH_TIMEOUT_MS)
        self._flush_timer.timeout.connect(self._flush_buf)

        # ——— 主布局：左(终端+检索栏) + 右(控制栏) ———
        body_row = QHBoxLayout(self)
        body_row.setContentsMargins(RECEIVE_BODY_LEFT_MARGIN, 0, RECEIVE_BODY_RIGHT_MARGIN, 0)
        body_row.setSpacing(0)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(0)

        self._wrap = QWidget()
        self._wrap.setObjectName("terminalWrap")
        self._wrap.installEventFilter(self)
        self._disconnect_flash = QWidget(self._wrap)
        self._disconnect_flash.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._disconnect_flash.setStyleSheet(
            f"background: transparent; border: 2px solid {THEME_COLORS[config.theme]['status_error_bg_hover']}; border-radius: {RECEIVE_WRAP_RADIUS}px;"
        )
        self._disconnect_flash.hide()
        wv = QHBoxLayout(self._wrap)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(0)

        self._edit = QTextEdit()
        self._edit.setObjectName("terminalEdit")
        self._edit.setVerticalScrollBar(RoundedScrollBar(self._edit))
        self._edit.setReadOnly(False)
        self._edit.setCursorWidth(2)
        self._edit.setUndoRedoEnabled(False)
        self._edit.setAcceptRichText(False)
        self._edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self._edit.setFrameShape(QTextEdit.Shape.NoFrame)
        # ponytail: 与普通模式对齐，去掉文档默认 margin，避免文本边距不一致。
        self._edit.document().setDocumentMargin(0)
        self._edit.document().setMaximumBlockCount(RX_DISPLAY_MAX_LINES)
        self._line_gutter = LineNumberGutter(self._edit, max_digits=6, parent=self._wrap)
        wv.addWidget(self._line_gutter)
        wv.addWidget(self._edit, 1)
        left_col.addWidget(self._wrap, stretch=1)

        # 选中字节数浮层，复用普通接收区样式
        self._sel_label = QLabel(self._wrap)
        self._sel_label.setStyleSheet(
            f"QLabel {{ background: {THEME_COLORS[config.theme]['selection_overlay_bg']}; color: {THEME_COLORS[config.theme]['selection_overlay_text']}; "
            f"border-radius: {SELECTION_OVERLAY_RADIUS_PX}px; padding: {SELECTION_OVERLAY_PADDING_Y_PX}px {SELECTION_OVERLAY_PADDING_X_PX}px; "
            f"font-size: {SELECTION_OVERLAY_FONT_SIZE_PX}px; }}"
        )
        self._sel_label.hide()

        self._build_search_bar()
        self.search_wrap = self._search_wrap
        left_col.addWidget(self._search_wrap)
        body_row.addLayout(left_col, stretch=1)

        self._build_ctrl_bar()
        body_row.addWidget(self._ctrl_bar)

        # 滚动条用户手动滚动 → 同步自动滚动按钮状态
        self._edit.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        self._edit.textChanged.connect(self._on_terminal_text_changed)
        self._edit.cursorPositionChanged.connect(self._update_cursor_line_highlight)
        self._edit.selectionChanged.connect(self._on_selection_changed)

        self._edit.viewport().setMouseTracking(True)
        self._edit.installEventFilter(self)
        self._edit.viewport().installEventFilter(self)
        self._search = DocumentSearch(self._edit, qt=False)
        self._search.updated.connect(self._on_search_results)
        self._search.paint_requested.connect(self._render_search_highlights)
        self._search.activated.connect(self._highlight_terminal_search)
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.activated.connect(self._on_find_shortcut)
        self._find_shortcut.setEnabled(False)
        QTimer.singleShot(0, self._apply_wrap_mask)

    def set_find_shortcut_enabled(self, enabled: bool) -> None:
        self._find_shortcut.setEnabled(bool(enabled))

    def _on_find_shortcut(self) -> None:
        if not self.isVisible():
            return
        if self._search_wrap.isVisible():
            self._search_btn.toggle()
            return
        text = self._selected_terminal_text or self._edit.textCursor().selectedText()
        if text:
            self._search_btn.setChecked(True)
            self._set_search_text(text)
            self._highlight_terminal_search()
            self._search_input.setFocus()
            self._search_input.selectAll()
            return
        self._search_btn.toggle()

    # ——— 控制栏 ———

    def _build_ctrl_bar(self) -> None:
        self._ctrl_bar = QWidget()
        self._ctrl_bar.setFixedWidth(RECEIVE_CONTROL_BAR_WIDTH)
        col = QVBoxLayout(self._ctrl_bar)
        col.setContentsMargins(0, CONTROL_BAR_VERTICAL_MARGIN, 0, CONTROL_BAR_VERTICAL_MARGIN)
        col.setSpacing(CONTROL_BAR_SPACING)

        clear_btn = EmojiButton(EMOJI_BUTTON_TEXTS["clear_rx"], icon_size=SIDEBAR_ICON_SIZE)
        clear_btn.setToolTip("清空终端全部数据并复位计数")
        clear_btn.clicked.connect(self._on_clear)

        save_btn = EmojiButton(EMOJI_BUTTON_TEXTS["save"], icon_size=SIDEBAR_ICON_SIZE)
        save_btn.setToolTip("保存终端数据到文件")
        save_btn.clicked.connect(self._on_save)

        self._mode_chk = EmojiButton(EMOJI_BUTTON_TEXTS["terminal_mode"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self._mode_chk.setChecked(config.serial_mode == "terminal")
        self._mode_chk.setToolTip("终端模式: 启用" if self._mode_chk.isChecked() else "终端模式: 禁用")
        self._mode_chk.toggled.connect(lambda v: self._mode_chk.setToolTip("终端模式: 启用" if v else "终端模式: 禁用"))
        self._mode_chk.toggled.connect(self.serial_mode_toggled.emit)

        self._auto_scroll_chk = EmojiButton(EMOJI_BUTTON_TEXTS["auto_scroll"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self._auto_scroll_chk.setChecked(True)
        self._auto_scroll_chk.setToolTip("自动滚动: 启用")
        self._auto_scroll_chk.toggled.connect(self._on_auto_scroll_toggled)
        self._auto_scroll_chk.toggled.connect(lambda v: self._auto_scroll_chk.setToolTip("自动滚动: 启用" if v else "自动滚动: 禁用"))

        self._ext_cmd_btn = EmojiButton(EMOJI_BUTTON_TEXTS["ext_cmd"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self._ext_cmd_btn.setToolTip("扩展命令: 禁用")
        self._ext_cmd_btn.toggled.connect(lambda v: self._ext_cmd_btn.setToolTip("扩展命令: 启用" if v else "扩展命令: 禁用"))
        self._ext_cmd_btn.toggled.connect(self.ext_cmd_toggled.emit)

        self._search_btn = EmojiButton(EMOJI_BUTTON_TEXTS["search_toggle"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self._search_btn.setToolTip("Ctrl+F 检索: 禁用")
        self._search_btn.toggled.connect(self._toggle_search_bar)
        self._search_btn.toggled.connect(lambda v: self._search_btn.setToolTip("Ctrl+F 检索: 启用" if v else "Ctrl+F 检索: 禁用"))

        col.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignRight)
        col.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)
        col.addWidget(self._mode_chk, alignment=Qt.AlignmentFlag.AlignRight)
        col.addWidget(self._auto_scroll_chk, alignment=Qt.AlignmentFlag.AlignRight)
        col.addWidget(self._ext_cmd_btn, alignment=Qt.AlignmentFlag.AlignRight)
        col.addWidget(self._search_btn, alignment=Qt.AlignmentFlag.AlignRight)
        col.addStretch()

    def _on_auto_scroll_toggled(self, checked: bool) -> None:
        if checked:
            sb = self._edit.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_user_scroll(self, value: int) -> None:
        """根据滚动模式处理用户滚动。"""
        if getattr(self, '_suppress_scroll_signal', False):
            return
        mode = config.scroll_mode
        sb = self._edit.verticalScrollBar()
        at_bottom = value >= sb.maximum()

        if mode == "semi_auto":
            if not at_bottom and self._auto_scroll_chk.isChecked():
                self._auto_scroll_chk.setChecked(False)
            elif at_bottom and not self._auto_scroll_chk.isChecked():
                self._auto_scroll_chk.setChecked(True)

    def _on_clear(self) -> None:
        self.reset()
        self.cleared.emit()

    def _on_save(self) -> None:
        win = self.window()
        port = note = ""
        if hasattr(win, 'config_panel'):
            port = win.config_panel.port_combo.currentText().strip()
            note = win.config_panel.note_edit.text().strip()
        save_text(self, self._edit.toPlainText(), port, note)

    # ——— 检索栏 ———

    def _build_search_bar(self) -> None:
        self._search_wrap = QWidget()
        self._search_wrap.setVisible(False)
        sw = QVBoxLayout(self._search_wrap)
        sw.setContentsMargins(0, SEARCH_WRAP_TOP_MARGIN, 0, 0)
        sw.setSpacing(0)

        row = QWidget()
        sr = QHBoxLayout(row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(SEARCH_ROW_SPACING)

        f11 = QFont()
        if hasattr(f11, "setFamilies"):
            f11.setFamilies(get_display_font_families())
        else:
            families = get_display_font_families()
            if families:
                f11.setFamily(families[0])
        f11.setPointSize(DEFAULT_DISPLAY_FONT_SIZE_PT)
        search_h = int(1.8 * QFontMetrics(f11).lineSpacing())
        swatch_sz = int(search_h * 0.9)

        self._search_color_btn = QPushButton()
        self._search_color_btn.setFixedSize(swatch_sz, swatch_sz)
        self._search_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_color_btn.setToolTip("点击选择高亮颜色")
        self._search_color_btn.clicked.connect(self._on_search_color_pick)
        self._update_search_color_btn()
        sr.addWidget(self._search_color_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._search_combo = QComboBox()
        self._search_combo.setEditable(True)
        self._search_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        configure_combo_popup(self._search_combo)
        self._search_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._search_combo.setMinimumContentsLength(1)
        self._search_combo.setFixedHeight(search_h)
        self._search_combo.setCompleter(None)
        self._search_combo.installEventFilter(self)
        self._search_input = self._search_combo.lineEdit()
        if self._search_input is None:
            self._search_input = QLineEdit()
            self._search_combo.setLineEdit(self._search_input)
        self._search_input.setFont(get_default_font())
        self._search_input.setPlaceholderText("检索终端文本…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._commit_search_history)
        self._search_combo.activated.connect(lambda _: self._commit_search_history())
        sr.addWidget(self._search_combo)

        self._search_prev_btn = EmojiButton(EMOJI_BUTTON_TEXTS["search_prev"], size=search_h)
        self._search_prev_btn.setToolTip("上一条匹配")
        self._search_prev_btn.clicked.connect(self._on_search_prev_match)
        sr.addWidget(self._search_prev_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._search_next_btn = EmojiButton(EMOJI_BUTTON_TEXTS["search_next"], size=search_h)
        self._search_next_btn.setToolTip("下一条匹配")
        self._search_next_btn.clicked.connect(self._on_search_next_match)
        sr.addWidget(self._search_next_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        clear_hist_btn = EmojiButton(EMOJI_BUTTON_TEXTS["clear"], size=search_h)
        clear_hist_btn.setToolTip("清除检索历史")
        clear_hist_btn.clicked.connect(self._clear_search_history)
        sr.addWidget(clear_hist_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._case_chk = EmojiButton(EMOJI_BUTTON_TEXTS["search_case"], checkable=True, size=search_h)
        self._case_chk.setToolTip("区分大小写")
        self._case_chk.toggled.connect(self._on_search_options_changed)
        sr.addWidget(self._case_chk, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._word_chk = EmojiButton(EMOJI_BUTTON_TEXTS["search_word"], checkable=True, size=search_h)
        self._word_chk.setToolTip("全字匹配")
        self._word_chk.toggled.connect(self._on_search_options_changed)
        sr.addWidget(self._word_chk, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._regex_chk = EmojiButton(EMOJI_BUTTON_TEXTS["search_regex"], checkable=True, size=search_h)
        self._regex_chk.setToolTip("正则表达式")
        self._regex_chk.toggled.connect(self._on_search_options_changed)
        sr.addWidget(self._regex_chk, alignment=Qt.AlignmentFlag.AlignVCenter)

        sr.addStretch()
        self._search_count_lbl = QLabel("[-/0]")
        self._search_count_lbl.setFixedWidth(SEARCH_COUNT_LABEL_WIDTH)
        self._search_count_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        sr.addWidget(self._search_count_lbl)
        sr.setStretchFactor(self._search_combo, 4)
        sw.addWidget(row)
        self._load_search_history()

    def _toggle_search_bar(self, visible: bool) -> None:
        if not visible and hasattr(self, "_search"):
            self._search.clear()
        self._search_wrap.setVisible(visible)
        if visible:
            self._reset_search_nav_state()
            self._search_input.setFocus()
            self._highlight_terminal_search()
        else:
            self._search_timer.stop()
            self._search_extras = []
            self._search_matches = []
            self._reset_search_nav_state()
            self._apply_all_highlights([])
            self._update_search_count_label(0)
        self.updateGeometry()
        sb = self._edit.verticalScrollBar()
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def _on_search_text_changed(self, text: str) -> None:
        if not self._syncing_search_text:
            self._hist_nav_index = -1
            self._hist_draft = text.strip()
        if hasattr(self, "_search"):
            self._search.clear()
        self._reset_search_nav_state()
        self._search_timer.start()

    def _on_terminal_text_changed(self) -> None:
        """终端文本变化时，搜索栏开启且有关键词则防抖刷新匹配。"""
        self._defer_search_refresh()

    def _reset_search_nav_state(self) -> None:
        self._search_nav_index = -1
        self._search_nav_activated = False

    def _update_search_count_label(self, total: int) -> None:
        search = getattr(self, "_search", None)
        if search and search.error:
            self._search_count_lbl.setText("[正则错误]")
            self._search_count_lbl.setToolTip(search.error)
            return
        suffix = "+" if search and search.limited else ""
        current = str(self._search_nav_index + 1) if self._search_nav_activated and 0 <= self._search_nav_index < total else "-"
        self._search_count_lbl.setText(
            "[搜索中…]" if search and search.updating and total == 0 else f"[{current}/{total}{suffix}]"
        )
        self._search_count_lbl.setToolTip(
            "已达上限，仅保留最近的匹配" if suffix else
            ("正在更新匹配…" if search and search.updating else "")
        )

    def _defer_search_refresh(self) -> None:
        if hasattr(self, "_search"):
            self._search.schedule()

    def _on_search_options_changed(self, _checked: bool) -> None:
        if hasattr(self, "_search"):
            self._search.clear()
        self._reset_search_nav_state()
        self._search_timer.start()

    def _update_cursor_line_highlight(self) -> None:
        self._render_search_highlights()

    def _on_selection_changed(self) -> None:
        text = self._edit.textCursor().selectedText()
        if not text:
            self._sel_label.hide()
            return
        text = text.replace(" ", "\n")
        n = len(text.encode(config.encoding, errors="replace"))
        self._sel_label.setText(f"选中: {n} Byte")
        self._sel_label.adjustSize()
        self._sel_label.show()
        self._reposition_sel_label()
        QTimer.singleShot(0, self._reposition_sel_label)

    def _reposition_sel_label(self) -> None:
        w = self._wrap.width()
        h = self._wrap.height()
        lw = self._sel_label.sizeHint().width()
        lh = self._sel_label.sizeHint().height()
        self._sel_label.setGeometry(
            w - lw - SELECTION_OVERLAY_RIGHT_MARGIN_PX,
            h - lh - SELECTION_OVERLAY_BOTTOM_MARGIN_PX,
            lw,
            lh,
        )

    def _apply_all_highlights(self, search_selections: list[QTextEdit.ExtraSelection]) -> None:
        all_sel = list(search_selections)
        if self._edit.hasFocus():
            cur = self._edit.textCursor()
            if cur.block().isValid():
                hc = self.palette().highlight().color()
                hc.setAlpha(40)
                self._cursor_line_sel.format.setBackground(hc)
                line_cur = QTextCursor(cur)
                line_cur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                line_cur.movePosition(
                    QTextCursor.MoveOperation.EndOfBlock,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                self._cursor_line_sel.cursor = line_cur
                all_sel.append(self._cursor_line_sel)
        self._edit.setExtraSelections(all_sel)

    def _on_search_prev_match(self) -> None:
        self._jump_search_match(-1)

    def _on_search_next_match(self) -> None:
        self._jump_search_match(1)

    def _jump_search_match(self, direction: int) -> None:
        total = len(self._search_matches)
        if total <= 0:
            self._reset_search_nav_state()
            self._update_search_count_label(0)
            return
        if not self._search_nav_activated:
            # 首次跳转统一定位到最后一条
            self._search_nav_index = total - 1
            self._search_nav_activated = True
        else:
            self._search_nav_index = (self._search_nav_index + direction) % total
        self._focus_search_match(self._search_nav_index)
        self._update_search_count_label(total)

    def _focus_search_match(self, index: int) -> None:
        if not (0 <= index < len(self._search_matches)):
            return
        start, end = self._search_matches[index]
        cursor = self._edit.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self._edit.setTextCursor(cursor)
        self._edit.ensureCursorVisible()

    def _commit_search_history(self) -> None:
        text = self._search_input.text().strip()
        if not text:
            return
        if self._upsert_search_history(text):
            self._refresh_search_combo_items()
            self._save_search_history()

    def _load_search_history(self) -> None:
        search_history.load_into(self._search_history)
        self._refresh_search_combo_items()

    def _save_search_history(self) -> None:
        search_history.save(self._search_history)

    def _upsert_search_history(self, text: str) -> bool:
        s = text.strip()
        if not s:
            return False
        old = list(self._search_history)
        hist = [h for h in self._search_history if h != s]
        hist.insert(0, s)
        self._search_history = hist[:32]
        return self._search_history != old

    def _refresh_search_combo_items(self) -> None:
        current = self._search_input.text()
        self._syncing_search_text = True
        input_blocked = self._search_input.blockSignals(True)
        self._search_combo.blockSignals(True)
        try:
            self._search_combo.clear()
            if self._search_history:
                self._search_combo.addItems(self._search_history)
            self._search_combo.setCurrentIndex(-1)
            self._search_combo.setEditText(current)
        finally:
            self._search_combo.blockSignals(False)
            self._search_input.blockSignals(input_blocked)
            self._syncing_search_text = False

    def _set_search_text(self, text: str) -> None:
        self._syncing_search_text = True
        try:
            self._search_input.setText(text)
            self._search_input.setCursorPosition(len(text))
        finally:
            self._syncing_search_text = False
        self._reset_search_nav_state()
        self._search_timer.start()

    def _update_search_color_btn(self) -> None:
        """刷新颜色按钮样式，含透明度效果。"""
        colors = THEME_COLORS[config.theme]
        c = config.search_color_dark if config.theme == "dark" else config.search_color_light
        bg = QColor(c)
        bg.setAlpha(config.search_opacity)
        rgba = f"rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()})"
        focus_bg = QColor(bg)
        focus_bg = focus_bg.lighter(118)
        focus_bg.setAlpha(min(bg.alpha() + 35, 255))
        focus_rgba = f"rgba({focus_bg.red()},{focus_bg.green()},{focus_bg.blue()},{focus_bg.alpha()})"
        pressed_bg = QColor(bg)
        pressed_bg = pressed_bg.darker(118)
        pressed_bg.setAlpha(min(bg.alpha() + 20, 255))
        pressed_rgba = f"rgba({pressed_bg.red()},{pressed_bg.green()},{pressed_bg.blue()},{pressed_bg.alpha()})"
        self._search_color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {rgba}; border: {SEARCH_COLOR_BUTTON_BORDER_PX}px solid {colors['text']}; border-radius: {SEARCH_COLOR_BUTTON_RADIUS_PX}px; }}"
            f"QPushButton:hover {{ background-color: {focus_rgba}; border: {SEARCH_COLOR_BUTTON_BORDER_PX}px solid {colors['text']}; }}"
            f"QPushButton:focus {{ background-color: {focus_rgba}; border: {SEARCH_COLOR_BUTTON_BORDER_PX}px solid {colors['text']}; }}"
            f"QPushButton:pressed {{ background-color: {pressed_rgba}; border: {SEARCH_COLOR_BUTTON_BORDER_PX}px solid {colors['text']}; }}"
        )

    def _on_search_color_pick(self) -> None:
        """弹出颜色选择器设置检索高亮颜色。"""
        from gui.color_picker import ColorPickerDialog
        dlg = ColorPickerDialog(
            self, "选择检索高亮颜色",
            config.search_color_dark, config.search_color_light,
            show_opacity=True, opacity=config.search_opacity,
        )
        if dlg.exec() != ColorPickerDialog.DialogCode.Accepted:
            return
        config.search_color_dark = dlg.selected_dark()
        config.search_color_light = dlg.selected_light()
        config.search_opacity = dlg.opacity()
        self._update_search_color_btn()
        self._highlight_terminal_search()

    def _clear_search_history(self) -> None:
        self._search_history.clear()
        self._refresh_search_combo_items()
        self._save_search_history()
        self._search_extras = []
        self._search_matches = []
        self._reset_search_nav_state()
        self._update_search_count_label(0)
        self._search_input.clear()

    def _highlight_terminal_search(self) -> None:
        if not hasattr(self, "_search") or self._search_timer.isActive():
            return
        self._search.search(self._search_input.text(), self._regex_chk.isChecked(), self._case_chk.isChecked(),
                            self._word_chk.isChecked(), self._search_wrap.isVisible())
        self._render_search_highlights()

    def _render_search_highlights(self) -> None:
        if not hasattr(self, "_search"):
            return
        self._search_extras = self._search.selections(self._terminal_search_color())
        self._apply_all_highlights(self._search_extras)

    def _on_search_results(self) -> None:
        previous = None
        if self._search_nav_activated and 0 <= self._search_nav_index < len(self._search_matches):
            previous = self._search_matches[self._search_nav_index]
        self._search_matches = self._search.matches
        if previous is not None:
            index = bisect_left(self._search_matches, previous)
            if index < len(self._search_matches) and self._search_matches[index] == previous:
                self._search_nav_index = index
            else:
                self._reset_search_nav_state()
        elif not self._search_matches:
            self._reset_search_nav_state()
        self._update_search_count_label(len(self._search_matches))
        self._render_search_highlights()

    def _terminal_search_color(self) -> QColor:
        color = QColor(config.search_color_dark if config.theme == "dark" else config.search_color_light)
        color.setAlpha(config.search_opacity)
        return color

    def set_display_font(self, family: str, size: int) -> None:
        from utils.font_manager import apply_display_fallbacks
        f = QFont()
        apply_display_fallbacks(f, family if family else None)
        f.setPointSize(size)
        self._edit.setFont(f)
        self._edit.setStyleSheet(f"font-size: {size}pt;")
        self._line_gutter.sync_metrics()

    def set_connected(self, connected: bool) -> None:
        self._serial_open = connected

    def is_search_enabled(self) -> bool:
        return self._search_btn.isChecked()

    def set_search_enabled(self, enabled: bool) -> None:
        self._search_btn.setChecked(bool(enabled))

    def set_terminal_mode_checked(self, enabled: bool) -> None:
        self._mode_chk.blockSignals(True)
        self._mode_chk.setChecked(enabled)
        self._mode_chk.blockSignals(False)
        self._mode_chk._refresh_style()
        self._mode_chk.setToolTip("终端模式: 启用" if enabled else "终端模式: 禁用")

    def set_local_echo(self, enabled: bool) -> None:
        self._local_echo = enabled

    def focus_edit(self) -> None:
        self._edit.setFocus()

    def reset(self) -> None:
        self._reset_line_click()
        self._edit.clear()
        self._search_extras = []
        self._apply_all_highlights([])
        self._byte_buf.clear()
        self._decoder = codecs.getincrementaldecoder(config.encoding)(errors="replace")
        self._decoder_encoding = config.encoding
        self._flush_timer.stop()
        self._sgr_fg = self._sgr_bg = None
        self._sgr_bold = False
        self._cursor_stack.clear()
        self._term_cursor_pos = 0

    def append_system_message(self, text: str) -> None:
        """向终端区追加系统消息（灰色文本，无时间戳）。"""
        sys_fmt = QTextCharFormat()
        sys_fmt.setForeground(QColor(THEME_COLORS[config.theme]["gutter_text"]))
        cursor = QTextCursor(self._edit.document())
        cursor.setPosition(self._term_cursor_pos)
        if cursor.position() > 0:
            cursor.insertText("\r\n", sys_fmt)
        cursor.insertText(text, sys_fmt)
        self._term_cursor_pos = cursor.position()
        self._suppress_scroll_signal = True
        self._edit.setTextCursor(cursor)
        self._edit.ensureCursorVisible()

    def append_data(self, data: bytes, ts: float, spb: float) -> None:
        """对齐 ReceivePanel.append_data 签名。"""
        self._byte_buf.extend(data)
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def append_chunks(self, chunks: list[tuple[bytes, float, float]]) -> None:
        for data, _ts, _spb in chunks:
            self._byte_buf.extend(data)
        if chunks and not self._flush_timer.isActive():
            self._flush_timer.start()

    def echo_send(self, data: bytes, force_hex: bool = False) -> None:
        """对齐 ReceivePanel.echo_send，终端模式设备回显覆盖，不处理。"""
        pass

    # ——— 自动保存 ———

    def start_auto_save(self, file_path: str, fp) -> None:
        self.stop_auto_save()
        self._auto_log_path = file_path
        self._auto_log_fp = fp

    def stop_auto_save(self, close_writer: bool = True) -> None:
        if self._auto_log_fp is not None:
            if close_writer:
                self._auto_log_fp.close()
        self._auto_log_fp = None
        self._auto_log_path = ""

    def is_auto_saving(self) -> bool:
        return self._auto_log_fp is not None

    def current_auto_log_filename(self) -> str:
        return Path(self._auto_log_path).name if self._auto_log_path else ""

    # ——— 数据刷新 ———

    def _flush_buf(self) -> None:
        if len(self._byte_buf) > 1_000_000:
            text = self._decode_received(bytes(self._byte_buf))
            self._byte_buf.clear()
            self._write_to_terminal(text)
            return
        tail = bytes(self._byte_buf[-32:] if len(self._byte_buf) > 32 else self._byte_buf)
        m = self._PARTIAL_CSI_RE.search(tail)
        if m:
            cut = len(self._byte_buf) - len(tail) + m.start()
            safe = bytes(self._byte_buf[:cut])
            del self._byte_buf[:cut]
        else:
            safe = bytes(self._byte_buf)
            self._byte_buf.clear()
        if safe:
            text = self._decode_received(safe)
            self._write_to_terminal(text)

    def _decode_received(self, data: bytes) -> str:
        if self._decoder_encoding != config.encoding:
            self._decoder_encoding = config.encoding
            self._decoder = codecs.getincrementaldecoder(config.encoding)(errors="replace")
        return self._decoder.decode(data, final=False)

    # ——— 终端写入核心 ———

    @staticmethod
    def _sanitize_plain_text(text: str) -> str:
        """过滤不可见控制字符，避免异常 ESC 片段落屏成乱码。"""
        if not text:
            return ""
        out: list[str] = []
        for ch in text:
            o = ord(ch)
            if ch in ("\r", "\n", "\b", "\t") or (o >= 0x20 and o != 0x7F):
                out.append(ch)
        return "".join(out)

    def _write_to_terminal(self, text: str) -> None:
        self._defer_search_refresh()
        cursor = QTextCursor(self._edit.document())
        cursor.setPosition(self._term_cursor_pos)

        sb = self._edit.verticalScrollBar()
        old_val = sb.value()
        content_fits = sb.maximum() <= 0
        was_at_bottom = content_fits or old_val >= sb.maximum()

        cursor.beginEditBlock()
        try:
            pos = 0
            for m in self._CSI_RE.finditer(text):
                plain = self._sanitize_plain_text(text[pos:m.start()])
                if plain:
                    self._insert_plain(cursor, plain)
                params_str = m.group(1)
                cmd = m.group(2)
                private = params_str.startswith("?")
                if private:
                    params_str = params_str[1:]
                params = [int(p) if p else 0 for p in params_str.split(";")] if params_str else [0]
                self._handle_csi(cursor, cmd, params, private)
                pos = m.end()

            plain = self._sanitize_plain_text(text[pos:])
            if plain:
                self._insert_plain(cursor, plain)
        finally:
            cursor.endEditBlock()

        self._term_cursor_pos = cursor.position()
        self._suppress_scroll_signal = True
        self._edit.setTextCursor(cursor)

        # 手动模式：仅 🔁 按钮控制；半自动：用户位置 + 🔁 联合控制
        if config.scroll_mode == "manual":
            if self._auto_scroll_chk.isChecked():
                sb.setValue(sb.maximum())
            else:
                sb.setValue(old_val)
        else:
            if was_at_bottom and self._auto_scroll_chk.isChecked():
                sb.setValue(sb.maximum())
            else:
                sb.setValue(old_val)
        self._suppress_scroll_signal = False

    def _insert_plain(self, cursor: QTextCursor, text: str) -> None:
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\r":
                if i + 1 < len(text) and text[i + 1] == "\n":
                    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                    cursor.insertBlock()
                    self._sgr_fg = self._sgr_bg = None
                    self._sgr_bold = False
                    self._auto_write("\r\n")
                    i += 2
                else:
                    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    i += 1
            elif ch == "\n":
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                cursor.insertBlock()
                self._sgr_fg = self._sgr_bg = None
                self._sgr_bold = False
                self._auto_write("\n")
                i += 1
            elif ch == "\b":
                cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor)
                i += 1
            elif ch == "\t":
                spaces = 8 - (cursor.columnNumber() % 8)
                self._insert_styled(cursor, " " * spaces)
                i += 1
            else:
                j = i + 1
                while j < len(text) and text[j] not in ("\r", "\n", "\b", "\t"):
                    j += 1
                segment = text[i:j]
                self._insert_styled(cursor, segment)
                self._auto_write(segment)
                i = j

    def _insert_styled(self, cursor: QTextCursor, text: str) -> None:
        fmt = QTextCharFormat()
        if self._sgr_fg:
            fmt.setForeground(QColor(self._sgr_fg))
        if self._sgr_bg:
            fmt.setBackground(QColor(self._sgr_bg))
        if self._sgr_bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if not self._sgr_fg and not self._sgr_bg and not self._sgr_bold:
            fmt = QTextCharFormat()
        if not cursor.atBlockEnd():
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len(text))
        cursor.insertText(text, fmt)


    def _auto_write(self, text: str) -> None:
        if self._auto_log_fp is None:
            return
        cleaned = self._CSI_RE.sub("", text)
        if not self._auto_log_fp.write(cleaned):
            self.stop_auto_save(close_writer=False)

    def _move_to_col(self, cursor: QTextCursor, col: int) -> None:
        block_len = cursor.block().length() - 1
        if col <= block_len:
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, col)
        else:
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            padding = " " * (col - block_len)
            cursor.insertText(padding)
            self._auto_write(padding)

    def _handle_csi(self, cursor: QTextCursor, cmd: str, params: list[int], private: bool = False) -> None:
        if private:
            return
        if cmd == "m":
            self._csi_sgr(params)
        elif cmd == "A":
            n = params[0] or 1
            cursor.movePosition(QTextCursor.MoveOperation.Up, QTextCursor.MoveMode.MoveAnchor, n)
        elif cmd == "B":
            n = params[0] or 1
            cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor, n)
        elif cmd == "C":
            n = params[0] or 1
            self._move_to_col(cursor, cursor.columnNumber() + n)
        elif cmd == "D":
            n = params[0] or 1
            self._move_to_col(cursor, max(0, cursor.columnNumber() - n))
        elif cmd in ("H", "f"):
            row = max(params[0] - 1, 0) if len(params) > 0 and params[0] > 0 else 0
            col = max(params[1] - 1, 0) if len(params) > 1 and params[1] > 0 else 0
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            for _ in range(row):
                cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor)
            self._move_to_col(cursor, col)
        elif cmd == "G":
            col = max(params[0] - 1, 0) if len(params) > 0 and params[0] > 0 else 0
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            self._move_to_col(cursor, col)
        elif cmd == "J":
            n = params[0] if params else 0
            if n == 2:
                self._edit.clear()
                self._sgr_fg = self._sgr_bg = None
                self._sgr_bold = False
                cursor.movePosition(QTextCursor.MoveOperation.Start)
                self._auto_write("\r\n")
            elif n == 0:
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                if not cursor.atEnd():
                    cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
                    cursor.removeSelectedText()
            elif n == 1:
                cursor.movePosition(QTextCursor.MoveOperation.Start, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
        elif cmd == "K":
            n = params[0] if params else 0
            if n == 0:
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            elif n == 1:
                cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
            elif n == 2:
                cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
        elif cmd == "s":
            self._cursor_stack.append((
                cursor.blockNumber(), cursor.columnNumber(),
                self._sgr_fg, self._sgr_bg, self._sgr_bold,
            ))
        elif cmd == "u" and self._cursor_stack:
            blk, col, fg, bg, bold = self._cursor_stack.pop()
            self._sgr_fg, self._sgr_bg, self._sgr_bold = fg, bg, bold
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            for _ in range(blk):
                cursor.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.MoveAnchor)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            for _ in range(col):
                cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor)

    def _csi_sgr(self, params: list[int]) -> None:
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                self._sgr_fg = self._sgr_bg = None
                self._sgr_bold = False
            elif p == 1:
                self._sgr_bold = True
            elif 30 <= p <= 37 or 90 <= p <= 97:
                self._sgr_fg = self._ANSI_FG.get(p)
            elif 40 <= p <= 47 or 100 <= p <= 107:
                self._sgr_bg = self._ANSI_BG.get(p)
            elif p == 38 and i + 2 < len(params) and params[i + 1] == 5:
                self._sgr_fg = self._xterm256(params[i + 2])
                i += 2
            elif p == 48 and i + 2 < len(params) and params[i + 1] == 5:
                self._sgr_bg = self._xterm256(params[i + 2])
                i += 2
            elif p == 38 and i + 4 < len(params) and params[i + 1] == 2:
                self._sgr_fg = "#{:02X}{:02X}{:02X}".format(params[i + 2], params[i + 3], params[i + 4])
                i += 4
            elif p == 48 and i + 4 < len(params) and params[i + 1] == 2:
                self._sgr_bg = "#{:02X}{:02X}{:02X}".format(params[i + 2], params[i + 3], params[i + 4])
                i += 4
            i += 1

    @staticmethod
    def _xterm256(code: int) -> str:
        if 0 <= code <= 15:
            d = TerminalReceivePanel._ANSI_FG
            return d.get(code + 30, "#000000") if code < 8 else d.get(code + 82, "#000000")
        if 16 <= code <= 231:
            code -= 16
            r, g, b = code // 36, (code % 36) // 6, code % 6
            return "#{:02X}{:02X}{:02X}".format(int(r * 255 / 5), int(g * 255 / 5), int(b * 255 / 5))
        if 232 <= code <= 255:
            level = int((code - 232) * 255 / 23)
            return "#{:02X}{:02X}{:02X}".format(level, level, level)
        return "#CCCCCC"

    # ——— 内部 ———

    def _is_at_end(self, cursor: QTextCursor | None = None) -> bool:
        c = cursor or self._edit.textCursor()
        return c.atEnd() and not c.hasSelection()

    def _flash_terminal_wrap(self) -> None:
        self._disconnect_flash.setGeometry(self._wrap.rect())
        self._disconnect_flash.raise_()
        self._disconnect_flash.show()
        QTimer.singleShot(FEEDBACK_RECOVERY_DELAY_MS, self._disconnect_flash.hide)

    def _apply_wrap_mask(self) -> None:
        rect = self._wrap.rect()
        if rect.isEmpty():
            self._wrap.clearMask()
            return
        radius = min(float(self._WRAP_RADIUS), rect.width() / 2.0, rect.height() / 2.0)
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), radius, radius)
        self._wrap.setMask(QRegion(path.toFillPolygon().toPolygon()))

    # ——— 事件拦截 ———

    def _reset_line_click(self) -> None:
        self._line_click_timer.invalidate()
        self._line_click_held = False

    def _handle_line_click(self, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.Hide, QEvent.Type.Wheel):
            self._reset_line_click()
            return False
        if self._line_click_timer.isValid() and self._line_click_timer.hasExpired(QApplication.doubleClickInterval()):
            self._reset_line_click()
        if self._line_click_held:
            if kind == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
                return True
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._reset_line_click()
                return True

        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            pos = event.position().toPoint()
            cursor = self._edit.cursorForPosition(pos)
            plain_left = event.button() == Qt.MouseButton.LeftButton and event.modifiers() == Qt.KeyboardModifier.NoModifier
            triple = (
                kind == QEvent.Type.MouseButtonPress
                and plain_left
                and self._line_click_timer.isValid()
                and (pos - self._line_click_pos).manhattanLength() <= QApplication.styleHints().mouseDoubleClickDistance()
                and cursor.block() == self._line_click_block
            )
            self._reset_line_click()
            if kind == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                if plain_left:
                    self._line_click_pos = pos
                    self._line_click_block = cursor.block()
                    self._line_click_timer.start()
                # 使用 Qt 选词规则，避免其默认三击处理另行选入段落分隔符。
                cursor.select(QTextCursor.SelectionType.WordUnderCursor)
                self._sel_dragging = False
                if hasattr(self, "_sel_anchor"):
                    del self._sel_anchor
                self._edit.setFocus(Qt.FocusReason.MouseFocusReason)
                self._edit.setTextCursor(cursor)
                self._update_cursor_line_highlight()
                return True
            elif triple:
                self._sel_dragging = False
                if hasattr(self, "_sel_anchor"):
                    del self._sel_anchor
                self._line_click_held = True
                cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                # 不包含段落分隔符，避免选区和当前行高亮落到下一行。
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                self._edit.setFocus(Qt.FocusReason.MouseFocusReason)
                self._edit.setTextCursor(cursor)
                self._update_cursor_line_highlight()
                return True
        elif kind == QEvent.Type.MouseMove and self._line_click_timer.isValid():
            if (
                event.modifiers() != Qt.KeyboardModifier.NoModifier
                or (event.position().toPoint() - self._line_click_pos).manhattanLength() > QApplication.styleHints().mouseDoubleClickDistance()
            ):
                self._reset_line_click()
        elif kind == QEvent.Type.MouseButtonRelease:
            if event.button() != Qt.MouseButton.LeftButton or event.modifiers() != Qt.KeyboardModifier.NoModifier:
                self._reset_line_click()
        return False

    def eventFilter(self, obj, event) -> bool:
        wrap = getattr(self, "_wrap", None)
        edit = getattr(self, "_edit", None)
        search_combo = getattr(self, "_search_combo", None)
        search_input = getattr(self, "_search_input", None)

        if edit is not None and obj is edit.viewport() and self._handle_line_click(event):
            return True

        if (
            edit is not None
            and obj is edit.viewport()
            and event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._zoom_wheel_delta += event.angleDelta().y()
            steps = int(self._zoom_wheel_delta / 120)
            self._zoom_wheel_delta -= steps * 120
            if steps:
                size = max(8, min(48, edit.font().pointSize() + steps))
                self.set_display_font(edit.font().family(), size)
            return True
        if wrap is not None and obj is wrap and event.type() == QEvent.Type.Resize:
            self._disconnect_flash.setGeometry(wrap.rect())
            self._apply_wrap_mask()
            return False
        if edit is not None and obj is edit:
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.FocusOut, QEvent.Type.Hide):
                self._reset_line_click()
            if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self._update_cursor_line_highlight()
                return False
            if event.type() == QEvent.Type.KeyPress:
                return self._handle_key(event)
            if event.type() == QEvent.Type.InputMethod:
                return True
            if event.type() == QEvent.Type.MouseButtonPress:
                self._sel_anchor = self._edit.cursorForPosition(event.pos()).position()
                self._sel_dragging = True
                self._apply_extra_sel(self._sel_anchor, self._sel_anchor)
                return True
            if event.type() == QEvent.Type.MouseMove and self._sel_dragging:
                end = self._edit.cursorForPosition(event.pos()).position()
                self._apply_extra_sel(self._sel_anchor, end)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._sel_dragging = False
                if hasattr(self, '_sel_anchor'):
                    del self._sel_anchor
                return True
            if event.type() == QEvent.Type.ContextMenu:
                return True

        if obj is search_combo or obj is search_input:
            if event.type() == QEvent.Type.KeyPress:
                key_event = event
                if key_event.key() == Qt.Key.Key_Escape:
                    self._search_btn.setChecked(False)
                    return True
                if not self._search_combo.view().isVisible():
                    if key_event.key() == Qt.Key.Key_Up:
                        self._search_hist_nav(1)
                        return True
                    if key_event.key() == Qt.Key.Key_Down:
                        self._search_hist_nav(-1)
                        return True
            return False

        return super().eventFilter(obj, event)

    def _search_hist_nav(self, direction: int) -> None:
        items = self._search_history
        if not items:
            return

        if self._hist_nav_index == -1:
            self._hist_draft = self._search_input.text()
            new_idx = 0 if direction > 0 else -1
        else:
            new_idx = self._hist_nav_index + direction

        if new_idx < -1:
            new_idx = -1
        if new_idx >= len(items):
            new_idx = len(items) - 1
        if new_idx == self._hist_nav_index:
            return

        self._hist_nav_index = new_idx
        if new_idx == -1:
            self._set_search_text(self._hist_draft)
            return
        self._set_search_text(items[new_idx])

    def _apply_extra_sel(self, start: int, end: int) -> None:
        if start == end:
            cursor = self._edit.textCursor()
            cursor.setPosition(start)
            self._edit.setTextCursor(cursor)
            self._update_cursor_line_highlight()
            return
        s, e = (start, end) if start < end else (end, start)
        text_cursor = self._edit.textCursor()
        text_cursor.setPosition(s)
        text_cursor.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        self._edit.setTextCursor(text_cursor)
        self._update_cursor_line_highlight()

    @property
    def _selected_terminal_text(self) -> str:
        return self._edit.textCursor().selectedText()

    def _handle_key(self, event: QKeyEvent) -> bool:
        key = event.key()
        text = event.text()
        mods = event.modifiers()

        if key == Qt.Key.Key_A and mods == Qt.KeyboardModifier.ControlModifier:
            self._edit.selectAll()
            return True

        if key == Qt.Key.Key_C and mods == Qt.KeyboardModifier.ControlModifier:
            sel = self._selected_terminal_text.replace(" ", "\n")
            if sel:
                QApplication.clipboard().setText(sel)
            return True

        if not self._serial_open:
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                       Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                return False
            self._flash_terminal_wrap()
            win = self.window()
            if hasattr(win, 'config_panel'):
                win.config_panel.flash_port_not_open()
            return True

        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            _ARROW_CSI = {
                Qt.Key.Key_Up: b"\x1b[A", Qt.Key.Key_Down: b"\x1b[B",
                Qt.Key.Key_Left: b"\x1b[D", Qt.Key.Key_Right: b"\x1b[C",
            }
            self.send_requested.emit(_ARROW_CSI[key])
            return True

        if key in (Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            return False

        if key == Qt.Key.Key_Backspace:
            self.send_requested.emit(b"\x08")
            if self._local_echo:
                self._echo_local_backspace()
            return True

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            le = config.tx_line_ending or "\r"
            self.send_requested.emit(le.encode("ascii", errors="replace"))
            if self._local_echo:
                self._echo_local("\n")
            return True

        if text and (text.isprintable() or text == "\t"):
            data = text.encode(config.tx_encoding, errors="replace")
            self.send_requested.emit(data)
            if self._local_echo:
                self._echo_local(text)
            return True

        return True

    def _echo_local(self, text: str) -> None:
        cursor = self._edit.textCursor()
        at_end = self._is_at_end(cursor)
        cursor.insertText(text, QTextCharFormat())
        self._edit.setTextCursor(cursor)
        if at_end:
            self._edit.ensureCursorVisible()

    def _echo_local_backspace(self) -> None:
        cursor = self._edit.textCursor()
        at_end = self._is_at_end(cursor)
        cursor.deletePreviousChar()
        self._edit.setTextCursor(cursor)
        if at_end:
            self._edit.ensureCursorVisible()

    # ponytail: 兼容 receive_panel 的属性引用，避免 main_window 报错
    ext_cmd_toggled = Signal(bool)
    serial_mode_toggled = Signal(bool)
