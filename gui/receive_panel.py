"""接收区面板：时间戳/HEX/自动滚动开关 + 清空/保存。

时间戳/HEX 为前向开关：仅影响切换后的新接收数据，不回刷历史。
时间戳通过 QTextCharFormat 着色，颜色可配置（默认纯绿 #00FF00）。
自动滚动默认开启，关闭时用户可自由翻阅历史数据。
"""
from __future__ import annotations

from bisect import bisect_left

from collections import deque
from itertools import islice
import re
import time
import logging

from PySide6.QtCore import QElapsedTimer, QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer, Slot, Signal
from config.runtime import (
    ANIMATION_ENABLED,
    HISTORY_SAVE_DEBOUNCE_MS,
    PANEL_ANIMATION_DURATION_MS,
    RX_DISPLAY_MAX_LINES,
    RX_BUFFER_DEFAULT_MB,
    RX_BUFFER_MAX_MB,
    RX_BUFFER_MIN_MB,
    RX_HEX_FLUSH_TIMEOUT_MS,
    RX_TEXT_FLUSH_TIMEOUT_MS,
    SEARCH_DEBOUNCE_INTERVAL_MS,
)
from config.layout import (
    SIDEBAR_ICON_SIZE,
    RECEIVE_BODY_LEFT_MARGIN,
    RECEIVE_BODY_RIGHT_MARGIN,
    RECEIVE_CONTROL_BAR_WIDTH,
    RECEIVE_WRAP_RADIUS,
    SELECTION_OVERLAY_BOTTOM_MARGIN_PX,
    SELECTION_OVERLAY_FONT_SIZE_PX,
    SELECTION_OVERLAY_PADDING_X_PX,
    SELECTION_OVERLAY_PADDING_Y_PX,
    SELECTION_OVERLAY_RADIUS_PX,
    SELECTION_OVERLAY_RIGHT_MARGIN_PX,
    SEARCH_COLOR_BUTTON_BORDER_PX,
    SEARCH_COLOR_BUTTON_RADIUS_PX,
    SEARCH_COUNT_LABEL_WIDTH,
)
from config.theme import FIXED_COLORS, THEME_COLORS
from config.ui import DEFAULT_DISPLAY_FONT_SIZE_PT, EMOJI_BUTTON_TEXTS
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainterPath, QRegion, QShortcut, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.data_converter import bytes_to_ascii, bytes_to_hex, format_timestamp
from core.ansi_parser import parse_ansi_segments
from utils.file_logger import save_text
from utils.diagnostics import event as diagnostic_event
from config.app_config import config, search_history
from gui.color_picker import ColorPickerDialog
from gui.emoji_button import EmojiButton
from gui.document_search import DocumentSearch
from gui.keyword_highlighter import KeywordHighlighter, RX_TEXT_KIND, RX_PLAIN, RX_ANSI
from gui.line_number_gutter import LineNumberGutter
from gui.rounded_scroll_bar import RoundedScrollBar, configure_combo_popup
from utils.font_manager import (
    apply_display_fallbacks,
    get_default_font,
    get_display_font_css_stack,
    get_display_font_families,
)


class ReceivePanel(QWidget):
    encoding_changed = Signal(str)
    cleared = Signal()
    ext_cmd_toggled = Signal(bool)
    serial_mode_toggled = Signal(bool)

    BUFFER_MIN_MB = RX_BUFFER_MIN_MB
    BUFFER_MAX_MB = RX_BUFFER_MAX_MB
    BUFFER_DEFAULT_MB = RX_BUFFER_DEFAULT_MB
    DISPLAY_TAB_SIZE = 8
    ECHO_NEWLINE_ICON = "⤶"
    BODY_LEFT_MARGIN = RECEIVE_BODY_LEFT_MARGIN
    BODY_RIGHT_MARGIN = RECEIVE_BODY_RIGHT_MARGIN
    CTRL_BAR_WIDTH = RECEIVE_CONTROL_BAR_WIDTH
    VIEW_WRAP_RADIUS = RECEIVE_WRAP_RADIUS
    MAX_HISTORY_LINES = RX_DISPLAY_MAX_LINES

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._suppress_scroll_signal = False  # ponytail: 程序化滚动时抑制 _on_user_scroll 误判
        self._zoom_wheel_delta = 0
        self._line_click_timer = QElapsedTimer()
        self._line_click_held = False
        self._lines: deque[tuple[str, bytes, bool, bool, bool]] = deque()
        self._total_bytes = 0
        self._buffer_limit = self.BUFFER_DEFAULT_MB * 1024 * 1024
        self._ts_color = config.ts_color_dark if config.theme == "dark" else config.ts_color_light
        self._ts_fmt = QTextCharFormat()
        self._ts_fmt.setForeground(QColor(self._ts_color))
        self._default_fmt = QTextCharFormat()
        self._default_fmt.setProperty(RX_TEXT_KIND, RX_PLAIN)
        self._echo_fmt = QTextCharFormat()
        self._echo_fmt.setForeground(QColor(FIXED_COLORS["echo"]))
        self._rx_hilights: list[dict] = []  # [{"color": "#FF0000", "texts": ["Error"], "fmt": QTextCharFormat}]
        self._rendered = 0  # 已渲染到视图的行数
        self._encoding = "GBK"
        self._line_buf = b""  # 行缓冲区：跨帧积累未完结的行数据
        self._last_rx_ts_ms = 0  # 接收日志时间戳单调性保护（毫秒）
        self._auto_save_enabled = False
        self._auto_log_fp = None
        self._auto_log_path = ""
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(RX_TEXT_FLUSH_TIMEOUT_MS)
        self._flush_timer.timeout.connect(self._flush_partial_line)
        self._search_color_dark = config.search_color_dark
        self._search_color_light = config.search_color_light
        self._search_opacity = config.search_opacity
        self._search_color = config.search_color_dark if config.theme == "dark" else config.search_color_light
        self._search_history: list[str] = []
        self._history_nav_index = -1
        self._history_draft = ""
        self._syncing_search_text = False
        self._history_dirty = False
        self._history_save_timer = QTimer(self)
        self._history_save_timer.setSingleShot(True)
        self._history_save_timer.setInterval(HISTORY_SAVE_DEBOUNCE_MS)
        self._history_save_timer.timeout.connect(self._flush_history_save)
        self._search_refresh_timer = QTimer(self)
        self._search_refresh_timer.setSingleShot(True)
        self._search_refresh_timer.setInterval(SEARCH_DEBOUNCE_INTERVAL_MS)
        self._search_refresh_timer.timeout.connect(self._highlight_search)
        self._search_matches: list[tuple[int, int]] = []
        self._search_nav_index = -1
        self._search_nav_activated = False
        self._build_ui()
        self._keywords = KeywordHighlighter(self.view)
        self._search = DocumentSearch(self.view, qt=True)
        self._search.updated.connect(self._on_search_results)
        self._search.paint_requested.connect(self._render_search_highlights)
        self._search.activated.connect(self._highlight_search)
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.activated.connect(self._on_find_shortcut)

    def set_find_shortcut_enabled(self, enabled: bool) -> None:
        self._find_shortcut.setEnabled(bool(enabled))

    def _build_ui(self) -> None:
        grp = QWidget()
        v = QVBoxLayout(grp)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self.view = QPlainTextEdit()
        self.view.setObjectName("receiveView")
        self.view.setVerticalScrollBar(RoundedScrollBar(self.view))
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.horizontalScrollBar().setRange(0, 0)
        # ponytail: 去掉文档默认 margin，避免最后一行始终与底部留固定空白。
        self.view.document().setDocumentMargin(0)
        self.view.setMaximumBlockCount(self.MAX_HISTORY_LINES)
        self.view.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.view.setCursorWidth(2)  # ponytail: 读模式下光标可见，方便定位
        self.view.viewport().setMouseTracking(True)
        self.view.installEventFilter(self)
        self.view.viewport().installEventFilter(self)
        # 当前行高亮，便于阅读定位
        self._cursor_line_sel = QTextEdit.ExtraSelection()
        self._cursor_line_sel.format.setBackground(QColor(THEME_COLORS[config.theme]["cursor_line_selection"]))
        self._cursor_line_sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        self.view.cursorPositionChanged.connect(self._update_cursor_line_highlight)

        self._view_wrap = QWidget()
        self._view_wrap.setObjectName("receiveViewWrap")
        self._view_wrap.installEventFilter(self)
        vw = QHBoxLayout(self._view_wrap)
        vw.setContentsMargins(0, 0, 0, 0)
        vw.setSpacing(0)
        self._line_gutter = LineNumberGutter(self.view, max_digits=6, parent=self._view_wrap)
        vw.addWidget(self._line_gutter)
        vw.addWidget(self.view, 1)
        display_font_css = get_display_font_css_stack()
        colors = THEME_COLORS[config.theme]

        # 选中字节数浮层：右下角圆角半透明提示
        self._sel_label = QLabel(self._view_wrap)
        self._sel_label.setStyleSheet(
            f"QLabel {{ background: {colors['selection_overlay_bg']}; color: {colors['selection_overlay_text']}; "
            f"border-radius: {SELECTION_OVERLAY_RADIUS_PX}px; "
            f"padding: {SELECTION_OVERLAY_PADDING_Y_PX}px {SELECTION_OVERLAY_PADDING_X_PX}px; "
            f"font-size: {SELECTION_OVERLAY_FONT_SIZE_PX}px; font-family: {display_font_css}; }}"
        )
        self._sel_label.hide()

        # 检索行：颜色按钮 + 输入框 + 选项按钮
        self._search_row_widget = QWidget()
        search_row = QHBoxLayout(self._search_row_widget)
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(4)

        # 检索行包装器：水平对齐接收/发送区边界，纵向 margin + io_layout.spacing 形成对称间距
        self.search_wrap = QWidget()
        sw = QVBoxLayout(self.search_wrap)
        sw.setContentsMargins(0, 5, 0, 0)
        sw.setSpacing(0)
        sw.addWidget(self._search_row_widget)

        # 统一高度：与搜索框等高的正方形按钮
        f11 = QFont()
        if hasattr(f11, "setFamilies"):
            f11.setFamilies(get_display_font_families())
        else:
            families = get_display_font_families()
            if families:
                f11.setFamily(families[0])
        f11.setPointSize(DEFAULT_DISPLAY_FONT_SIZE_PT)
        search_h = int(1.8 * QFontMetrics(f11).lineSpacing())

        # 检索色块：略小于搜索框高度，正方形
        swatch_sz = int(search_h * 0.9)

        self._search_color_btn = QPushButton()
        self._search_color_btn.setFixedSize(swatch_sz, swatch_sz)
        self._search_color_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._search_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_color_btn.setToolTip("点击选择高亮颜色")
        self._search_color_btn.clicked.connect(self._on_search_color_pick)
        self._update_search_color_btn()
        search_row.addWidget(self._search_color_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._search_combo = QComboBox()
        self._search_combo.setEditable(True)
        self._search_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        configure_combo_popup(self._search_combo)
        self._search_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._search_combo.setMinimumContentsLength(1)
        self._search_combo.setFixedHeight(search_h)
        self._search_combo.setCompleter(None)  # ponytail: 禁用自动补全，避免输入"1"自动补"12"
        self._search_combo.installEventFilter(self)
        self._search_input = self._search_combo.lineEdit()
        if self._search_input is None:
            self._search_input = QLineEdit()
            self._search_combo.setLineEdit(self._search_input)
        self._search_input.setFont(get_default_font())  # ponytail: 先设字体再设 placeholder，确保回退链渲染稳定
        self._search_input.setStyleSheet(f"QLineEdit {{ font-family: {display_font_css}; }}")
        self._search_input.setPlaceholderText("检索接收区文本…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.installEventFilter(self)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._commit_search_history)
        self._search_input.editingFinished.connect(self._commit_search_history)
        self._search_combo.activated.connect(lambda _: self._commit_search_history())
        search_row.addWidget(self._search_combo)

        self._search_prev_btn = EmojiButton(EMOJI_BUTTON_TEXTS["search_prev"], size=search_h)
        self._search_prev_btn.setToolTip("上一条匹配")
        self._search_prev_btn.clicked.connect(self._on_search_prev_match)
        search_row.addWidget(self._search_prev_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self._search_next_btn = EmojiButton(EMOJI_BUTTON_TEXTS["search_next"], size=search_h)
        self._search_next_btn.setToolTip("下一条匹配")
        self._search_next_btn.clicked.connect(self._on_search_next_match)
        search_row.addWidget(self._search_next_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        clear_hist_btn = EmojiButton(EMOJI_BUTTON_TEXTS["clear"], size=search_h)
        clear_hist_btn.setToolTip("清除检索历史")
        clear_hist_btn.clicked.connect(self._clear_search_history)
        search_row.addWidget(clear_hist_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.case_chk = EmojiButton(EMOJI_BUTTON_TEXTS["search_case"], checkable=True, size=search_h)
        self.case_chk.setToolTip("区分大小写")
        self.case_chk.toggled.connect(self._on_search_options_changed)
        search_row.addWidget(self.case_chk, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.word_chk = EmojiButton(EMOJI_BUTTON_TEXTS["search_word"], checkable=True, size=search_h)
        self.word_chk.setToolTip("全字匹配")
        self.word_chk.toggled.connect(self._on_search_options_changed)
        search_row.addWidget(self.word_chk, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.regex_chk = EmojiButton(EMOJI_BUTTON_TEXTS["search_regex"], checkable=True, size=search_h)
        self.regex_chk.setToolTip("正则表达式")
        self.regex_chk.toggled.connect(self._on_search_options_changed)
        search_row.addWidget(self.regex_chk, alignment=Qt.AlignmentFlag.AlignVCenter)
        search_row.addStretch()
        self._search_count_lbl = QLabel("[-/0]")
        self._search_count_lbl.setFixedWidth(SEARCH_COUNT_LABEL_WIDTH)
        self._search_count_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        search_row.addWidget(self._search_count_lbl)

        # 输入框获取大部分额外空间，app.* 与计数标签适度靠右
        search_row.setStretchFactor(self._search_combo, 4)
        self.search_wrap.setVisible(False)
        self._load_search_history()

        # 接收区主体：左侧文本+检索，右侧控制列
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, self.BODY_RIGHT_MARGIN, 0)
        body_row.setSpacing(0)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(self.BODY_LEFT_MARGIN, 0, 0, 0)
        left_col.setSpacing(0)
        left_col.addWidget(self._view_wrap, stretch=1)
        left_col.addWidget(self.search_wrap)
        body_row.addLayout(left_col, stretch=1)

        ctrl_bar = QWidget()
        ctrl_bar.setFixedWidth(self.CTRL_BAR_WIDTH)
        ctrl_col = QVBoxLayout(ctrl_bar)
        ctrl_col.setContentsMargins(0, 2, 0, 2)
        ctrl_col.setSpacing(6)

        self.ts_chk = EmojiButton(EMOJI_BUTTON_TEXTS["timestamp"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.ts_chk.setToolTip("时间戳: 禁用")
        self.ts_chk.toggled.connect(lambda v: setattr(config, "ts_enabled", v))
        self.ts_chk.toggled.connect(lambda v: self.ts_chk.setToolTip("时间戳: 启用" if v else "时间戳: 禁用"))

        self.hex_chk = EmojiButton(EMOJI_BUTTON_TEXTS["hex_mode"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.hex_chk.setToolTip("HEX显示: 禁用")
        self.hex_chk.toggled.connect(lambda v: self.hex_chk.setToolTip("HEX显示: 启用" if v else "HEX显示: 禁用"))

        self.auto_scroll_chk = EmojiButton(EMOJI_BUTTON_TEXTS["auto_scroll"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.auto_scroll_chk.setChecked(True)
        self.auto_scroll_chk.setToolTip("自动滚动: 启用")
        self.auto_scroll_chk.toggled.connect(self._on_auto_scroll_toggled)
        self.auto_scroll_chk.toggled.connect(lambda v: self.auto_scroll_chk.setToolTip("自动滚动: 启用" if v else "自动滚动: 禁用"))

        self.search_chk = EmojiButton(EMOJI_BUTTON_TEXTS["search_toggle"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.search_chk.setToolTip("Ctrl+F 检索: 禁用")
        self.search_chk.toggled.connect(self._toggle_search_bar)
        self.search_chk.toggled.connect(lambda v: self.search_chk.setToolTip("Ctrl+F 检索: 启用" if v else "Ctrl+F 检索: 禁用"))

        self.ext_cmd_btn = EmojiButton(EMOJI_BUTTON_TEXTS["ext_cmd"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.ext_cmd_btn.setToolTip("扩展命令: 禁用")
        self.ext_cmd_btn.toggled.connect(lambda v: self.ext_cmd_btn.setToolTip("扩展命令: 启用" if v else "扩展命令: 禁用"))
        self.ext_cmd_btn.toggled.connect(self.ext_cmd_toggled.emit)

        clear_btn = EmojiButton(EMOJI_BUTTON_TEXTS["clear_rx"], icon_size=SIDEBAR_ICON_SIZE)
        clear_btn.setToolTip("清空接收区全部数据并复位计数")
        clear_btn.clicked.connect(self._on_clear)
        save_btn = EmojiButton(EMOJI_BUTTON_TEXTS["save"], icon_size=SIDEBAR_ICON_SIZE)
        save_btn.setToolTip("保存接收区数据到文件")
        save_btn.clicked.connect(self._on_save)

        self.mode_chk = EmojiButton(EMOJI_BUTTON_TEXTS["terminal_mode"], checkable=True, icon_size=SIDEBAR_ICON_SIZE)
        self.mode_chk.setChecked(config.serial_mode == "terminal")
        self.mode_chk.setToolTip("终端模式: 启用" if self.mode_chk.isChecked() else "终端模式: 禁用")
        self.mode_chk.toggled.connect(lambda v: self.mode_chk.setToolTip("终端模式: 启用" if v else "终端模式: 禁用"))
        self.mode_chk.toggled.connect(self.serial_mode_toggled.emit)

        # 窄栏按钮按从上到下依次排列。
        ctrl_col.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addWidget(save_btn, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addWidget(self.mode_chk, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addWidget(self.ts_chk, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addWidget(self.hex_chk, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addWidget(self.auto_scroll_chk, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addWidget(self.ext_cmd_btn, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addWidget(self.search_chk, alignment=Qt.AlignmentFlag.AlignRight)
        ctrl_col.addStretch()

        body_row.addWidget(ctrl_bar)
        v.addLayout(body_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(grp)

        self.view.selectionChanged.connect(self._on_selection_changed)
        self.view.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        QTimer.singleShot(0, self._apply_view_wrap_mask)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_view_wrap_mask()
        self._reposition_sel_label()

    def _apply_view_wrap_mask(self) -> None:
        rect = self._view_wrap.rect()
        if rect.isEmpty():
            self._view_wrap.clearMask()
            return
        radius = min(float(self.VIEW_WRAP_RADIUS), rect.width() / 2.0, rect.height() / 2.0)
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), radius, radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        self._view_wrap.setMask(region)

    def _reposition_sel_label(self) -> None:
        """将选中字节浮层定位到 _view_wrap 右下角。"""
        w = self._view_wrap.width()
        h = self._view_wrap.height()
        lw = self._sel_label.sizeHint().width()
        lh = self._sel_label.sizeHint().height()
        self._sel_label.setGeometry(
            w - lw - SELECTION_OVERLAY_RIGHT_MARGIN_PX,
            h - lh - SELECTION_OVERLAY_BOTTOM_MARGIN_PX,
            lw,
            lh,
        )

    def _on_selection_changed(self) -> None:
        text = self.view.textCursor().selectedText()
        if not text:
            self._sel_label.hide()
            return
        text = text.replace(" ", "\r\n")
        n = len(text.encode(self._encoding, errors="replace"))
        self._sel_label.setText(f"选中: {n} Byte")
        self._sel_label.adjustSize()
        self._sel_label.show()
        self._reposition_sel_label()
        QTimer.singleShot(0, self._reposition_sel_label)

    # ---------- 显示控制 ----------
    def _on_auto_scroll_toggled(self, checked: bool) -> None:
        if checked:
            self._scroll_to_end()

    def _on_user_scroll(self, value: int) -> None:
        """根据滚动模式处理用户滚动行为。"""
        if self._suppress_scroll_signal:
            return
        mode = config.scroll_mode
        sb = self.view.verticalScrollBar()
        at_bottom = value >= sb.maximum()

        if mode == "semi_auto":
            # 半自动：滚离底部关，滚到底部开
            if not at_bottom and self.auto_scroll_chk.isChecked():
                self.auto_scroll_chk.setChecked(False)
            elif at_bottom and not self.auto_scroll_chk.isChecked():
                self.auto_scroll_chk.setChecked(True)
        # 手动模式：滚动不影响自动滚动状态

    def _on_clear(self) -> None:
        diagnostic_event(logging.INFO, f"清空接收区开始 mode=normal; blocks={self.view.document().blockCount()}; buffered_bytes={self._total_bytes}")
        self._reset_line_click()
        self._lines.clear()
        self._line_buf = b""
        self._flush_timer.stop()
        self._search_refresh_timer.stop()
        self._total_bytes = 0
        self._rendered = 0
        self.view.clear()
        self.cleared.emit()
        diagnostic_event(logging.INFO, "清空接收区完成 mode=normal")

    def _on_save(self) -> None:
        win = self.window()
        port = ""
        note = ""
        if hasattr(win, 'config_panel'):
            port = win.config_panel.port_combo.currentText().strip()
            note = win.config_panel.note_edit.text().strip()
        save_text(self, self.view.toPlainText(), port, note)

    # ---------- 自动保存 ----------
    def start_auto_save(self, file_path: str, fp) -> None:
        self.stop_auto_save()
        self._auto_log_path = file_path
        self._auto_log_fp = fp
        self._auto_save_enabled = True

    def stop_auto_save(self, close_writer: bool = True) -> None:
        if self._auto_log_fp is not None:
            if close_writer:
                self._auto_log_fp.close()
        self._auto_log_fp = None
        self._auto_log_path = ""
        self._auto_save_enabled = False

    def is_auto_saving(self) -> bool:
        return self._auto_save_enabled and self._auto_log_fp is not None

    def current_auto_log_filename(self) -> str:
        from pathlib import Path
        return Path(self._auto_log_path).name if self._auto_log_path else ""

    def _auto_write_line(self, ts: str, data: bytes, is_echo: bool, ts_on: bool, hex_on: bool) -> None:
        if not self.is_auto_saving():
            return
        if hex_on and not is_echo:
            body = bytes_to_hex(data)
        else:
            log_data = data if is_echo else self._strip_line_ending(data)
            body = self._decode_echo(log_data) if is_echo else self._decode(log_data)
            if not is_echo and "\x1b" in body:
                body, _segments = parse_ansi_segments(body)
        line = f"[{ts}] {body}" if ts_on else body
        if not self._auto_log_fp.write(line + "\n"):
            self.stop_auto_save(close_writer=False)

    # ---------- 检索高亮 ----------
    def _update_search_color_btn(self) -> None:
        """刷新颜色按钮样式，含透明度效果。"""
        colors = THEME_COLORS[config.theme]
        bg = self._search_bg_color()
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

    def _update_cursor_line_highlight(self) -> None:
        self._render_search_highlights()

    def _on_search_color_pick(self) -> None:
        dlg = ColorPickerDialog(
            self, "选择高亮颜色",
            self._search_color_dark, self._search_color_light,
            show_opacity=True, opacity=self._search_opacity,
        )
        if dlg.exec() != ColorPickerDialog.DialogCode.Accepted:
            return
        self._search_color_dark = dlg.selected_dark()
        self._search_color_light = dlg.selected_light()
        self._search_opacity = dlg.opacity()
        config.search_color_dark = self._search_color_dark
        config.search_color_light = self._search_color_light
        config.search_opacity = self._search_opacity
        self._refresh_search_color()
        self._highlight_search()

    def _refresh_search_color(self) -> None:
        """根据当前主题选择检索高亮颜色并刷新按钮。"""
        self._search_color = config.search_color_dark if config.theme == "dark" else config.search_color_light
        self._update_search_color_btn()

    def refresh_search_color(self) -> None:
        """外部调用：主题切换时刷新检索高亮颜色。"""
        self._refresh_search_color()
        self._highlight_search()

    def _search_bg_color(self) -> QColor:
        """返回带透明度的检索高亮背景色。"""
        c = QColor(self._search_color)
        c.setAlpha(self._search_opacity)
        return c

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
        self._search_refresh_timer.start()

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
        cursor = self.view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.view.setTextCursor(cursor)
        self.view.centerCursor()

    def _highlight_search(self) -> None:
        if not hasattr(self, "_search") or self._search_refresh_timer.isActive():
            return
        self._search.search(self._search_input.text().strip(), self.regex_chk.isChecked(), self.case_chk.isChecked(),
                            self.word_chk.isChecked(), self.search_wrap.isVisible())
        self._render_search_highlights()

    def _render_search_highlights(self) -> None:
        if not hasattr(self, "_search"):
            return
        self._search_extras = self._search.selections(self._search_bg_color())
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

    def _apply_all_highlights(self, search_selections: list[QTextEdit.ExtraSelection]) -> None:
        """合并搜索高亮 + 光标行高亮，一次 setExtraSelections 避免互相覆盖。"""
        all_sel = list(search_selections)
        # 光标行高亮：仅在 view 有焦点时显示，颜色参考行号栏高亮
        if self.view.hasFocus():
            cur = self.view.textCursor()
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
        self.view.setExtraSelections(all_sel)

    def _on_search_text_changed(self, text: str) -> None:
        if not self._syncing_search_text:
            self._history_nav_index = -1
            self._history_draft = text.strip()
            self._history_dirty = True
            self._history_save_timer.start()
        if hasattr(self, "_search"):
            self._search.clear()
        self._reset_search_nav_state()
        self._search_refresh_timer.start()

    def _load_search_history(self) -> None:
        search_history.load_into(self._search_history)
        self._refresh_search_combo_items()

    def _save_search_history(self) -> None:
        search_history.save(self._search_history)

    def _clear_search_history(self) -> None:
        self._search_history.clear()
        self._history_nav_index = -1
        self._history_draft = ""
        self._history_dirty = False
        self._history_save_timer.stop()
        self._search_matches = []
        self._reset_search_nav_state()
        self._update_search_count_label(0)
        self._refresh_search_combo_items()
        self._save_search_history()
        self._search_input.clear()  # ponytail: 同步清空检索输入框文本

    def _flush_history_save(self) -> None:
        if not self._history_dirty:
            return
        self._history_dirty = False
        text = self._history_draft
        if text:
            self._upsert_search_history(text)
            self._refresh_search_combo_items(keep_popup=self._search_combo.view().isVisible())
        self._save_search_history()

    def _refresh_search_combo_items(self, keep_popup: bool = False) -> None:
        current = self._search_input.text()
        popup_visible = keep_popup and self._search_combo.view().isVisible()
        self._syncing_search_text = True
        input_blocked = self._search_input.blockSignals(True)
        try:
            self._search_combo.blockSignals(True)
            self._search_combo.clear()
            if self._search_history:
                self._search_combo.addItems(self._search_history)
            self._search_combo.setCurrentIndex(-1)
            self._search_combo.setEditText(current)
        finally:
            self._search_combo.blockSignals(False)
            self._search_input.blockSignals(input_blocked)
            self._syncing_search_text = False
        if popup_visible:
            self._search_combo.showPopup()

    def _upsert_search_history(self, text: str) -> bool:
        s = text.strip()
        if not s:
            return False
        old = list(self._search_history)
        hist = [h for h in self._search_history if h != s]
        hist.insert(0, s)
        self._search_history = hist[:32]
        return self._search_history != old

    def _commit_search_history(self) -> None:
        text = self._search_input.text().strip()
        if not text:
            return
        self._upsert_search_history(text)
        self._history_nav_index = -1
        self._history_draft = text
        self._refresh_search_combo_items(keep_popup=self._search_combo.view().isVisible())
        self._history_dirty = False
        self._history_save_timer.stop()
        self._save_search_history()

    def _set_search_text(self, text: str) -> None:
        self._syncing_search_text = True
        try:
            self._search_input.setText(text)
            self._search_input.setCursorPosition(len(text))
        finally:
            self._syncing_search_text = False
        self._reset_search_nav_state()
        self._highlight_search()

    def _cycle_search_history(self, up: bool) -> None:
        """↑↓ 切换检索历史：direction=+1 向旧(↑)，direction=-1 向新(↓)。"""
        if not self._search_history:
            return
        direction = 1 if up else -1
        self._navigate_search_history(direction)

    def _navigate_search_history(self, direction: int) -> None:
        """direction: +1=向旧(↑), -1=向新(↓)，纯索引状态机。"""
        items = self._search_history
        if not items:
            return

        # 首次进入导航，保存当前草稿
        if self._history_nav_index == -1:
            self._history_draft = self._search_input.text()
            # ↑(向旧) 跳到最近(0)，↓(向新) 停在 -1
            if direction == 1:
                new_idx = 0
            else:
                new_idx = -1
        else:
            new_idx = self._history_nav_index + direction

        # 边界钳位
        if new_idx < -1:
            new_idx = -1
        if new_idx >= len(items):
            new_idx = len(items) - 1
        if new_idx == self._history_nav_index:
            return
        self._history_nav_index = new_idx

        if new_idx == -1:
            # 恢复草稿
            self._set_search_text(self._history_draft)
            return

        self._set_search_text(items[new_idx])

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
            cursor = self.view.cursorForPosition(pos)
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
                self.view.setFocus(Qt.FocusReason.MouseFocusReason)
                self.view.setTextCursor(cursor)
                self._update_cursor_line_highlight()
                return True
            elif triple:
                self._line_click_held = True
                cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                # 不包含段落分隔符，避免选区和当前行高亮落到下一行。
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                self.view.setFocus(Qt.FocusReason.MouseFocusReason)
                self.view.setTextCursor(cursor)
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

    def eventFilter(self, watched, event):
        if watched is self.view.viewport() and self._handle_line_click(event):
            return True
        if (
            watched is self.view.viewport()
            and event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._zoom_wheel_delta += event.angleDelta().y()
            steps = int(self._zoom_wheel_delta / 120)
            self._zoom_wheel_delta -= steps * 120
            if steps:
                size = max(8, min(48, self.view.font().pointSize() + steps))
                self.set_display_font(self.view.font().family(), size)
            return True
        if watched is self._view_wrap and event.type() == QEvent.Type.Resize:
            self._apply_view_wrap_mask()
            return False
        if watched is self.view:
            if event.type() in (QEvent.Type.KeyPress, QEvent.Type.FocusOut, QEvent.Type.Hide):
                self._reset_line_click()
            if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                if not getattr(self, '_suppress_highlight', False):
                    self._update_cursor_line_highlight()
                return False
            if event.type() == QEvent.Type.KeyPress and event.matches(QKeySequence.StandardKey.Copy):
                selected = self.view.textCursor().selectedText().replace(" ", "\n")
                if selected:
                    QApplication.clipboard().setText(selected)
                return True
        if hasattr(self, '_search_input') and watched in (self._search_input, self._search_combo) and event.type() == QEvent.Type.KeyPress:
            if not self._search_combo.view().isVisible():
                key = event.key()
                if key == Qt.Key.Key_Up:
                    self._cycle_search_history(up=True)
                    return True
                if key == Qt.Key.Key_Down:
                    self._cycle_search_history(up=False)
                    return True
        return super().eventFilter(watched, event)

    def _on_find_shortcut(self) -> None:
        """Ctrl+F：有选中文本时直接填充搜索框并打开，无选中时 toggle。"""
        text = self.view.textCursor().selectedText()
        if text:
            self._toggle_search_bar(True)
            self.search_chk.setChecked(True)
            self._search_input.setText(text)
            self._highlight_search()
            self._search_input.setFocus()
        else:
            self.search_chk.toggle()

    def _toggle_search_bar(self, checked: bool | None = None) -> None:
        if not self.search_chk.isChecked() and hasattr(self, "_search"):
            self._search.clear()
        sb = self.view.verticalScrollBar()
        old_max = sb.maximum()
        old_val = sb.value()
        dist_from_bottom = max(0, old_max - old_val)

        def _restore_scroll_position() -> None:
            new_max = sb.maximum()
            if self._should_auto_scroll() or dist_from_bottom == 0:
                self._scroll_to_end()
                return
            # ponytail: 保持与底部距离不变，避免搜索栏显隐时内容“钉住不动”。
            target = max(0, min(new_max, new_max - dist_from_bottom))
            self._suppress_scroll_signal = True
            try:
                sb.setValue(target)
            finally:
                self._suppress_scroll_signal = False

        def _restore_scroll_after_layout() -> None:
            _restore_scroll_position()
            # ponytail: 某些平台在首帧后才更新 maximum，补一次兜底。
            QTimer.singleShot(0, _restore_scroll_position)

        visible = checked if checked is not None else not self.search_wrap.isVisible()
        if visible == self.search_wrap.isVisible() and not getattr(self, '_search_anim', None):
            return
        old_anim = getattr(self, '_search_anim', None)
        if old_anim is not None:
            old_anim.stop()
            self._search_anim = None
        wrap = self.search_wrap
        if visible:
            self._reset_search_nav_state()
            wrap.setMaximumHeight(0)
            wrap.setMinimumHeight(0)
            wrap.setVisible(True)
            full_h = wrap.sizeHint().height()
            if full_h <= 0:
                full_h = 40
        else:
            self._search_matches = []
            self._reset_search_nav_state()
            self._update_search_count_label(0)
            wrap.setMinimumHeight(0)
            full_h = wrap.height()
            wrap.setMaximumHeight(full_h)
        target_h = full_h if visible else 0
        if ANIMATION_ENABLED:
            anim = QPropertyAnimation(wrap, b'maximumHeight')
            anim.setDuration(PANEL_ANIMATION_DURATION_MS)
            anim.setStartValue(wrap.maximumHeight())
            anim.setEndValue(target_h)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._search_anim = anim
            if visible:
                self._suppress_highlight = True
                try:
                    self._highlight_search()
                    self._search_input.setFocus()
                    self._search_input.selectAll()
                finally:
                    self._suppress_highlight = False
                self._highlight_search()

            def _on_finished() -> None:
                self._search_anim = None
                if not visible:
                    wrap.setVisible(False)
                    self._commit_search_history()
                    self.view.clearFocus()
                    self.view.setExtraSelections([])
                wrap.setMaximumHeight(16777215)
                QTimer.singleShot(0, _restore_scroll_after_layout)

            anim.finished.connect(_on_finished)
            anim.start()
        else:
            wrap.setMaximumHeight(16777215 if visible else 0)
            if not visible:
                wrap.setVisible(False)
                self._commit_search_history()
                self.view.clearFocus()
                self.view.setExtraSelections([])
            else:
                self._suppress_highlight = True
                try:
                    self._highlight_search()
                    self._search_input.setFocus()
                    self._search_input.selectAll()
                finally:
                    self._suppress_highlight = False
                self._highlight_search()
            QTimer.singleShot(0, _restore_scroll_after_layout)

    # ---------- 滚动 ----------
    def _scroll_to_end(self) -> None:
        self._suppress_scroll_signal = True
        try:
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
        finally:
            self._suppress_scroll_signal = False

    def _should_auto_scroll(self) -> bool:
        # 只要开启自动滚动，就始终跟随最新内容。
        return self.auto_scroll_chk.isChecked()

    # ---------- 缓冲区管理 ----------
    def set_buffer_size_mb(self, size_mb: int) -> None:
        """设置缓冲区上限（MB），立即裁剪超出的旧日志。"""
        size_mb = max(self.BUFFER_MIN_MB, min(self.BUFFER_MAX_MB, size_mb))
        self._buffer_limit = size_mb * 1024 * 1024
        self._trim_by_buffer_limit()

    def _trim_by_buffer_limit(self) -> None:
        """字节超限时从头部弹出最旧行，直到回到限制内。"""
        popped = 0
        while self._lines and (
            self._total_bytes > self._buffer_limit or len(self._lines) > self.MAX_HISTORY_LINES
        ):
            _ts, data, _is_echo, _ts_on, _hex_on = self._lines.popleft()
            popped += 1
            self._total_bytes -= len(data)
        if popped:
            self._rendered = max(0, self._rendered - popped)

    # ---------- 时间戳颜色 ----------
    def set_ts_color(self, color_hex: str) -> None:
        """设置时间戳显示颜色（HEX 格式如 '#90EE90'）。"""
        self._ts_color = color_hex
        self._ts_fmt.setForeground(QColor(color_hex))

    # ---------- RX 文本颜色高亮 ----------
    def set_rx_color_hilights(self, groups: list[dict]) -> None:
        """设置接收文本颜色过滤组，并立即重新应用到已显示内容。"""
        self._rx_hilights = []
        for g in groups:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(g["color"]))
            flags = 0 if g.get("case_sensitive", False) else re.IGNORECASE
            whole_word = bool(g.get("whole_word", False))
            patterns = []
            literals = []
            for text in g.get("texts", []):
                if not text:
                    continue
                pattern = re.escape(text)
                if whole_word:
                    pattern = r"(?<!\w)" + pattern + r"(?!\w)"
                patterns.append(re.compile(pattern, flags))
                literals.append(text)
            self._rx_hilights.append({
                "color": g["color"],
                "enabled": bool(g.get("enabled", True)),
                "fmt": fmt,
                "patterns": patterns,
                "literals": literals,
            })
        self._keywords.set_rules(self._rx_hilights)

    def _insert_ansi_segments(self, cursor, text: str, default_fmt: QTextCharFormat) -> None:
        """Insert base text and ANSI formats; keywords are a display overlay."""
        segments: list[tuple[int, int, QTextCharFormat]] = []

        # ---- ANSI SGR 解析（先注册，重叠时优先） ----
        if "\x1b" in text:
            clean_text, ansi_raw = parse_ansi_segments(text)
            for start, end, fmt_dict in ansi_raw:
                fmt = QTextCharFormat()
                fmt.setProperty(RX_TEXT_KIND, RX_ANSI)
                if fmt_dict.get("fg"):
                    fmt.setForeground(QColor(fmt_dict["fg"]))
                if fmt_dict.get("bg"):
                    fmt.setBackground(QColor(fmt_dict["bg"]))
                if fmt_dict.get("bold"):
                    fmt.setFontWeight(QFont.Weight.Bold)
                segments.append((start, end, fmt))
            text = clean_text  # 后续 render 用洁净文本

        if not text:
            return

        if not segments:
            cursor.insertText(text, default_fmt)
            return
        # 按 start 排序，同位置长匹配优先
        segments.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        merged: list[tuple[int, int, QTextCharFormat]] = []
        for s, e, f in segments:
            if merged and s < merged[-1][1]:
                continue  # 重叠，跳过
            merged.append((s, e, f))
        # 逐段插入
        pos = 0
        for s, e, f in merged:
            if s > pos:
                cursor.insertText(text[pos:s], default_fmt)
            cursor.insertText(text[s:e], f)
            pos = e
        if pos < len(text):
            cursor.insertText(text[pos:], default_fmt)

    # ---------- 数据追加 ----------
    def append_system_message(self, text: str) -> None:
        """向接收区追加系统消息（灰色文本，用于自动重连等状态提示）。"""
        sys_fmt = QTextCharFormat()
        sys_fmt.setForeground(QColor(THEME_COLORS[config.theme]["gutter_text"]))
        auto = self._should_auto_scroll()
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0:
            cursor.insertText("\n", sys_fmt)
        if config.ts_enabled:
            ts = format_timestamp(style=config.ts_format)
            cursor.insertText(f"[{ts}] ", self._ts_fmt)
        cursor.insertText(text, sys_fmt)
        if auto:
            self._scroll_to_end()

    @Slot(bytes, float, float)
    def append_data(self, data: bytes, ts_read: float, sec_per_byte: float) -> None:
        self.append_chunks([(data, ts_read, sec_per_byte)])

    def append_chunks(self, chunks: list[tuple[bytes, float, float]]) -> None:
        if not chunks:
            return
        ts_on = self.ts_chk.isChecked()
        hex_on = self.hex_chk.isChecked()
        raw_entries: list[tuple[str, bytes, bool, bool, bool]] = []

        for data, ts_read, sec_per_byte in chunks:
            previous_len = len(self._line_buf)
            self._line_buf += data
            prefix_remaining = previous_len
            current_consumed = 0

            while not hex_on:
                cr_idx = self._line_buf.find(b"\r")
                lf_idx = self._line_buf.find(b"\n")
                candidates = [idx for idx in (cr_idx, lf_idx) if idx >= 0]
                idx = min(candidates) if candidates else -1
                if idx < 0:
                    break
                if self._line_buf[idx] == 0x0D:
                    if idx + 1 >= len(self._line_buf):
                        break
                    line_ending = b"\r\n" if self._line_buf[idx + 1] == 0x0A else b"\r"
                else:
                    line_ending = b"\n"

                consumed = idx + len(line_ending)
                current_part = max(0, consumed - prefix_remaining)
                current_consumed += current_part
                prefix_remaining = max(0, prefix_remaining - consumed)
                raw_line = self._line_buf[:idx]
                self._line_buf = self._line_buf[consumed:]

                tail_gap = max(0, len(data) - current_consumed)
                est_ms = int((ts_read - tail_gap * sec_per_byte) * 1000)
                if est_ms <= self._last_rx_ts_ms:
                    est_ms = self._last_rx_ts_ms + 1
                self._last_rx_ts_ms = est_ms
                ts = format_timestamp(est_ms / 1000.0, config.ts_format)
                line_data = raw_line + line_ending
                entry = (ts, line_data, False, ts_on, hex_on)
                self._lines.append(entry)
                self._total_bytes += len(line_data)
                raw_entries.append(entry)
                self._auto_write_line(ts, line_data, False, ts_on, hex_on)

        if self._line_buf:
            self._flush_timer.start(
                RX_HEX_FLUSH_TIMEOUT_MS if hex_on else RX_TEXT_FLUSH_TIMEOUT_MS
            )
        else:
            self._flush_timer.stop()
        if not raw_entries:
            return
        self._trim_by_buffer_limit()
        self._defer_search_refresh()
        self._append_rich_lines(raw_entries)
        self._rendered += len(raw_entries)

    def _flush_partial_line(self) -> None:
        """超时将未换行的残留缓冲刷新到视图与历史。"""
        if not self._line_buf:
            return
        partial = self._line_buf
        self._line_buf = b""

        ts = format_timestamp(time.time(), config.ts_format)
        ts_on = self.ts_chk.isChecked()
        hex_on = self.hex_chk.isChecked()
        self._lines.append((ts, partial, False, ts_on, hex_on))
        self._total_bytes += len(partial)
        self._trim_by_buffer_limit()
        self._auto_write_line(ts, partial, False, ts_on, hex_on)

        self._defer_search_refresh()
        self._append_rich_lines([(ts, partial, False, ts_on, hex_on)])
        self._rendered += 1

    # ---------- 发送回显 ----------
    def _decode_echo(self, data: bytes) -> str:
        """回显内容统一按 UTF-8 解码（echo_send 也按 UTF-8 编码），避免受接收编码切换影响。"""
        return data.decode("utf-8", errors="replace")

    def _format_echo_body(self, data: bytes, force_hex: bool) -> str:
        if force_hex:
            return bytes_to_hex(data)
        text = self._decode(data)
        # 先按当前发送换行符匹配，再兜底替换所有 CR/LF 变体为可视图标。
        le = config.tx_line_ending
        if le:
            text = text.replace(le, self.ECHO_NEWLINE_ICON)
        text = text.replace("\r\n", self.ECHO_NEWLINE_ICON)
        text = text.replace("\r", self.ECHO_NEWLINE_ICON)
        text = text.replace("\n", self.ECHO_NEWLINE_ICON)
        return text

    def echo_send(self, data: bytes, force_hex: bool = False) -> None:
        """发送回显：文本使用 TX->>>，十六进制使用 HEX TX->>>，入 _lines 缓冲区。"""
        decoded = self._format_echo_body(data, force_hex).expandtabs(self.DISPLAY_TAB_SIZE)
        prefix = "HEX TX->>>" if force_hex else "TX->>>"
        echo_text = f"{prefix} {decoded}"
        echo_bytes = echo_text.encode("utf-8", errors="replace")
        ts = format_timestamp(style=config.ts_format)
        ts_on = self.ts_chk.isChecked()
        hex_on = self.hex_chk.isChecked()
        self._lines.append((ts, echo_bytes, True, ts_on, hex_on))
        self._total_bytes += len(echo_bytes)
        self._trim_by_buffer_limit()
        self._auto_write_line(ts, echo_bytes, True, ts_on, hex_on)

        auto = self._should_auto_scroll()
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0:
            cursor.insertText("\n", self._default_fmt)
        if ts_on:
            cursor.insertText(f"[{ts}] ", self._ts_fmt)
        cursor.insertText(echo_text, self._echo_fmt)
        if auto:
            self._scroll_to_end()
        self._rendered += 1
        self._highlight_search()

    def _decode(self, data: bytes) -> str:
        return bytes_to_ascii(data, encoding=self._encoding)

    def set_encoding(self, enc: str) -> None:
        self._encoding = enc
        from config.app_config import config
        config.encoding = enc
        self.encoding_changed.emit(enc)
        if self.ts_chk.isChecked() or self.hex_chk.isChecked():
            self._rebuild_view()

    # ---------- 显示重建（编码切换等场景） ----------
    def _rebuild_view(self) -> None:
        """全量重建显示。保存并恢复滚动位置。"""
        sb = self.view.verticalScrollBar()
        saved = sb.value()
        self.view.clear()
        self._rendered = 0
        if not self._lines:
            self._highlight_search()
            return
        self._render_backlog()
        self._suppress_scroll_signal = True
        try:
            sb.setValue(min(saved, sb.maximum()))
        finally:
            self._suppress_scroll_signal = False

    # ---------- 字体 ----------
    def set_display_font(self, family: str, size: int) -> None:
        font = QFont()
        apply_display_fallbacks(font, family if family else None)
        font.setPointSize(size)
        self.view.setFont(font)
        self.view.setStyleSheet(f"font-size: {size}pt;")
        self._line_gutter.sync_metrics()

    def _append_text_line(self, line: str, is_echo: bool = False) -> None:
        """快速追加单行纯文本（无时间戳/HEX 时）。"""
        line = line.expandtabs(self.DISPLAY_TAB_SIZE)
        auto = self._should_auto_scroll()
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if cursor.position() > 0:
            cursor.insertText("\n", self._default_fmt)
        if is_echo:
            cursor.insertText(line, self._echo_fmt)
        else:
            self._insert_ansi_segments(cursor, line, self._default_fmt)
        if auto:
            self._scroll_to_end()

    @staticmethod
    def _strip_line_ending(data: bytes) -> bytes:
        """文本显示时去掉结尾 CR/LF，HEX 显示保留原始字节。"""
        if data.endswith(b"\r\n"):
            return data[:-2]
        if data.endswith(b"\r") or data.endswith(b"\n"):
            return data[:-1]
        return data

    def _append_rich_lines(self, lines: list[tuple[str, bytes, bool, bool, bool]]) -> None:
        """批量追加多行（按每行快照决定时间戳/HEX），避免历史回刷。"""
        auto = self._should_auto_scroll()
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        try:
            inserted_plain_batch = False
            plain_batch = all(not is_echo and not ts_on and not hex_on for _ts, _data, is_echo, ts_on, hex_on in lines)
            if plain_batch:
                bodies = [
                    self._decode(self._strip_line_ending(data)).expandtabs(self.DISPLAY_TAB_SIZE)
                    for _ts, data, _is_echo, _ts_on, _hex_on in lines
                ]
                if not any("\x1b" in body for body in bodies):
                    text = "\n".join(bodies)
                    if cursor.position() > 0:
                        text = "\n" + text
                    elif not any(bodies):
                        text += "\n"
                    cursor.insertText(text, self._default_fmt)
                    inserted_plain_batch = True
            if not inserted_plain_batch:
                for ts, data, is_echo, ts_on, hex_on in lines:
                    if cursor.position() > 0:
                        cursor.insertText("\n", self._default_fmt)
                    start_pos = cursor.position()
                    if is_echo:
                        body = self._decode_echo(data)
                    elif hex_on:
                        body = bytes_to_hex(data)
                    else:
                        body = self._decode(self._strip_line_ending(data))
                    body = body.expandtabs(self.DISPLAY_TAB_SIZE)
                    if ts_on:
                        cursor.insertText(f"[{ts}] ", self._ts_fmt)
                    if is_echo:
                        cursor.insertText(body, self._echo_fmt)
                    else:
                        self._insert_ansi_segments(cursor, body, self._default_fmt)
                    if cursor.position() == start_pos:
                        cursor.insertBlock()
        finally:
            cursor.endEditBlock()
        if auto:
            self._scroll_to_end()

    def _render_backlog(self) -> None:
        """渲染尚未绘制的历史行（用于重建）。"""
        backlog = list(islice(self._lines, self._rendered, None))
        if not backlog:
            return
        if all((not ts_on and not hex_on) for _, _, _, ts_on, hex_on in backlog):
            for ts, data, is_echo, _ts_on, _hex_on in backlog:
                text_data = data if is_echo else self._strip_line_ending(data)
                line_text = self._decode_echo(text_data) if is_echo else self._decode(text_data)
                self._append_text_line(line_text, is_echo=is_echo)
        else:
            self._append_rich_lines(backlog)
        self._rendered = len(self._lines)
        if self._should_auto_scroll():
            self._scroll_to_end()
        self._highlight_search()

    # ---------- 滚动模式 ----------
    def set_scroll_mode(self, mode: str) -> None:
        """设置滚动模式：manual 或 semi_auto。"""
        config.scroll_mode = mode

    def is_search_enabled(self) -> bool:
        return self.search_chk.isChecked()

    def set_search_enabled(self, enabled: bool) -> None:
        self.search_chk.setChecked(bool(enabled))

    def set_terminal_mode_checked(self, enabled: bool) -> None:
        self.mode_chk.blockSignals(True)
        self.mode_chk.setChecked(enabled)
        self.mode_chk.blockSignals(False)
        self.mode_chk._refresh_style()
        self.mode_chk.setToolTip("终端模式: 启用" if enabled else "终端模式: 禁用")
