"""状态栏：右对齐 RX/TX 计数 + 复位按钮。格式: RX: xxx Byte (0.0x%)  TX: xxx Byte"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget
from config.app_config import config
from config.layout import (
    STATUS_BAR_HEIGHT,
    STATUS_BAR_LEFT_MARGIN,
    STATUS_BAR_RIGHT_MARGIN,
    STATUS_ICON_SIZE,
    STATUS_LEFT_MIN_WIDTH,
    STATUS_LAYOUT_SPACING,
    STATUS_MARQUEE_GAP_PX,
    STATUS_MARQUEE_SPEED_PX,
    STATUS_MAX_TEXT_WIDTH,
    STATUS_MIN_WIDTH,
    STATUS_RESET_BUTTON_HEIGHT,
    STATUS_TEXT_HEIGHT_EXTRA,
    STATUS_TEXT_WIDTH_EXTRA,
)
from config.runtime import STATUS_MARQUEE_INTERVAL_MS
from gui.emoji_button import get_emoji_icon_path
from gui.hover_popup import HoverPopup

_MB = 1024 * 1024


class _SmoothMarqueeLabel(QWidget):
    """像素级平移跑马灯，避免按字符切换带来的卡顿感。"""

    path_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._text = ""
        self._fixed_prefix = ""
        self._path = ""
        self._offset_px = 0.0
        self._gap_px = STATUS_MARQUEE_GAP_PX
        self._speed_px = STATUS_MARQUEE_SPEED_PX
        self._timer = QTimer(self)
        self._timer.setInterval(STATUS_MARQUEE_INTERVAL_MS)  # ~60fps
        self._timer.timeout.connect(self._on_tick)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_text(self, text: str) -> None:
        self._text = text
        self._offset_px = 0.0
        self._sync_scroll_state()
        self.updateGeometry()
        self.update()

    def set_fixed_prefix(self, prefix: str) -> None:
        self._fixed_prefix = prefix
        self._offset_px = 0.0
        self._sync_scroll_state()
        self.update()

    def text(self) -> str:
        return self._text

    def set_path(self, path: str) -> None:
        self._path = path
        if path:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._path:
            self.path_clicked.emit(self._path)
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_scroll_state()

    def setFont(self, font) -> None:  # noqa: N802
        super().setFont(font)
        self._offset_px = 0.0
        self._sync_scroll_state()
        self.update()

    def _split_text(self) -> tuple[str, str]:
        if self._fixed_prefix and self._text.startswith(self._fixed_prefix):
            return self._fixed_prefix, self._text[len(self._fixed_prefix):]
        return "", self._text

    def _scroll_text_width(self) -> int:
        _prefix, scroll_text = self._split_text()
        return self.fontMetrics().horizontalAdvance(scroll_text)

    def _available_scroll_width(self) -> int:
        prefix, _scroll_text = self._split_text()
        return max(0, self.width() - self.fontMetrics().horizontalAdvance(prefix))

    def _needs_scroll(self) -> bool:
        _prefix, scroll_text = self._split_text()
        return bool(scroll_text) and self._scroll_text_width() > self._available_scroll_width()

    def _sync_scroll_state(self) -> None:
        if self._needs_scroll():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._offset_px = 0.0

    def _on_tick(self) -> None:
        if not self._needs_scroll():
            self._sync_scroll_state()
            return
        cycle = self._scroll_text_width() + self._gap_px
        if cycle <= 0:
            return
        self._offset_px = (self._offset_px + self._speed_px) % cycle
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        h = fm.height() + 2
        if not self._text:
            return QSize(STATUS_MIN_WIDTH, h)
        w = min(STATUS_MAX_TEXT_WIDTH, fm.horizontalAdvance(self._text) + STATUS_TEXT_WIDTH_EXTRA)
        return QSize(max(STATUS_MIN_WIDTH, w), h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        return QSize(STATUS_MIN_WIDTH, fm.height() + STATUS_TEXT_HEIGHT_EXTRA)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(self.palette().color(self.foregroundRole()))

        if not self._text:
            return

        fm = self.fontMetrics()
        baseline_y = int((self.height() + fm.ascent() - fm.descent()) / 2)
        prefix, scroll_text = self._split_text()
        prefix_w = fm.horizontalAdvance(prefix)

        if prefix:
            painter.drawText(0, baseline_y, prefix)

        if not scroll_text:
            return

        x0 = prefix_w
        avail_w = max(0, self.width() - prefix_w)
        if avail_w <= 0:
            return

        # 仅允许后缀文本绘制在前缀右侧区域，避免覆盖固定前缀。
        painter.save()
        painter.setClipRect(x0, 0, avail_w, self.height())

        if not self._needs_scroll():
            painter.drawText(x0, baseline_y, scroll_text)
            painter.restore()
            return

        text_w = self._scroll_text_width()
        step = text_w + self._gap_px
        x = x0 - self._offset_px
        while x < x0 + avail_w:
            painter.drawText(int(x), baseline_y, scroll_text)
            x += step
        painter.restore()


class StatusBar(QWidget):
    path_clicked = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rx_total = 0
        self._tx_total = 0
        self._buffer_limit = config.buffer_size_mb * _MB
        self._left_full_text = ""
        self._left_display_text = ""
        self._left_icon_symbol = ""
        self._left_path = ""

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._left_icon = QLabel()
        self._left_icon.setFixedSize(STATUS_ICON_SIZE, STATUS_ICON_SIZE)
        self._left_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._left_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._left_icon.hide()
        self._left_label = _SmoothMarqueeLabel()
        self._left_label.setMinimumWidth(0)

        self._reset_btn = QPushButton(" 清除 ")
        self._reset_btn.setObjectName("sendBtn")
        self._reset_btn.setFixedHeight(STATUS_RESET_BUTTON_HEIGHT)
        self._reset_btn.clicked.connect(self.reset_counters)

        self._label_popup = HoverPopup(self._label, "", align="right")
        self._left_popup = HoverPopup(self._left_label, "", align="left")
        self._left_label.path_clicked.connect(self.path_clicked)
        self._reset_popup = HoverPopup(self._reset_btn, "清零收发计数", align="right")

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(STATUS_LAYOUT_SPACING)
        bar.addWidget(self._left_icon)
        bar.addWidget(self._left_label, 1)
        bar.addStretch(1)
        bar.addWidget(self._label)
        bar.addWidget(self._reset_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(STATUS_BAR_LEFT_MARGIN, 0, STATUS_BAR_RIGHT_MARGIN, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        self.setFixedHeight(STATUS_BAR_HEIGHT)
        QTimer.singleShot(0, self._refresh_left_status_display)
        self._refresh()

    def set_buffer_mb(self, mb: int) -> None:
        self._buffer_limit = mb * _MB
        self._refresh()

    def set_connected(self, connected: bool, message: str = "", port_info: str = "") -> None:
        _ = connected
        _ = port_info
        # 失败类状态只显示固定短文案，避免底部出现过长异常细节。
        if message.startswith("打开失败"):
            self._set_left_status_text("⚠串口打开失败")
        elif not connected and message and message != "已断开":
            self._set_left_status_text("❌串口丢失")
        self._refresh()

    def add_rx(self, n: int) -> None:
        if n > 0:
            self._rx_total += n
            self._refresh()

    def add_tx(self, n: int) -> None:
        if n > 0:
            self._tx_total += n
            self._refresh()

    def reset_counters(self) -> None:
        self._rx_total = 0
        self._tx_total = 0
        self._refresh()

    def set_auto_save_status(self, text: str, popup_text: str = "", path: str = "") -> None:
        self._set_left_status_text(text, popup_text, path)

    def set_auto_save_path(self, path: str) -> None:
        self._left_path = path
        self._left_label.set_path(path)

    def refresh_theme_icon(self) -> None:
        if not self._left_icon_symbol:
            return
        icon_path = get_emoji_icon_path(self._left_icon_symbol)
        if icon_path is None:
            self._left_icon.clear()
            return
        pixmap = QPixmap(str(icon_path)).scaled(
            self._left_icon.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._left_icon.setPixmap(pixmap)

    def _set_left_status_text(self, text: str, popup_text: str = "", path: str = "") -> None:
        """左侧状态文案使用全局字体；emoji 由字体回退到 Segoe UI Emoji。"""
        self._left_full_text = text
        self._left_icon_symbol = next((symbol for symbol in ("✅", "⚠", "❌") if text.startswith(symbol)), "")
        self._left_display_text = text[len(self._left_icon_symbol):] if self._left_icon_symbol else text
        self._left_icon.setVisible(bool(self._left_icon_symbol))
        if self._left_icon_symbol:
            self.refresh_theme_icon()
        self.set_auto_save_path(path)
        self._left_label.set_fixed_prefix("正在保存到 " if self._left_display_text.startswith("正在保存到") else "")
        self._left_popup._text = popup_text or text
        self._refresh_left_status_display(reset=True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_left_status_display()

    def _left_max_width(self) -> int:
        # 限制左侧状态最多占状态栏宽度的一半，避免长备注把窗口撑大。
        return max(80, self.width() // 2)

    def _refresh_left_status_display(self, reset: bool = False) -> None:
        _ = reset
        max_w = self._left_max_width()
        # 仅限制最大宽度，不固定宽度，避免放大后无法缩回小窗口。
        self._left_label.setMaximumWidth(max_w)
        # 有文本时保留可读宽度下限；仍受最大半宽限制。
        self._left_label.setMinimumWidth(min(STATUS_LEFT_MIN_WIDTH, max_w) if self._left_display_text else 0)
        self._left_label.set_text(self._left_display_text)

    # ---------- internal ----------
    def _refresh(self) -> None:
        pct = (self._rx_total / self._buffer_limit * 100) if self._buffer_limit > 0 else 0.0
        buf_mb = self._buffer_limit / _MB
        text = f"RX:{self._rx_total} B({pct:.2f}%) TX:{self._tx_total} B"
        self._label.setText(text)
        self._label_popup._text = (
            f"接收:{self._rx_total} Byte({pct:.2f}%)-{buf_mb:.0f} MB\n"
            f"发送:{self._tx_total} Byte"
        )
