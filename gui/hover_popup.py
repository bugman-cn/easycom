"""通用 hover 弹窗：悬停 delay 后在控件上方滑出说明文本。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget
from config.layout import (
    TOOLTIP_EDGE_GAP_PX,
    TOOLTIP_FONT_SIZE_PX,
    TOOLTIP_PADDING_X_PX,
    TOOLTIP_PADDING_Y_PX,
    TOOLTIP_RADIUS_PX,
    TOOLTIP_WINDOW_MARGIN_PX,
)
from config.runtime import EMOJI_POPUP_DELAY_MS
from config.theme import THEME_COLORS


class HoverPopup(QObject):
    """轻量 hover 弹窗：悬停 250ms 后滑出说明文本。

    side: 'top' 从上方弹出（默认）, 'left' 从左侧弹出
    align: 仅 side='top' 时生效 — 'left'/'right'/'center'
    """

    DELAY_MS = EMOJI_POPUP_DELAY_MS

    def __init__(self, widget: QWidget, text: str, side: str = "top", align: str = "center") -> None:
        super().__init__(widget)
        self._widget = widget
        self._text = text
        self._side = side
        self._align = align
        self._popup: QLabel | None = None
        self._timer = QTimer(widget)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if watched is not self._widget:
            return False
        t = event.type()
        if t == QEvent.Type.Enter:
            self._timer.start(self.DELAY_MS)
        elif t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
            self._timer.stop()
            self._hide()
        elif t == QEvent.Type.Move:
            self._hide()
        return False

    def _show(self) -> None:
        if self._popup or not self._text:
            return
        w = self._widget
        popup = QLabel(self._text, w.window())
        popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        popup.raise_()

        from config.app_config import config
        colors = THEME_COLORS[config.theme]
        bg = colors["tooltip_base"]
        fg = colors["tooltip_text"]
        popup.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: {TOOLTIP_RADIUS_PX}px; "
            f"padding: {TOOLTIP_PADDING_Y_PX}px {TOOLTIP_PADDING_X_PX}px; font-size: {TOOLTIP_FONT_SIZE_PX}px; }}"
        )
        popup.setWordWrap(False)
        max_w = w.window().width() - TOOLTIP_WINDOW_MARGIN_PX
        popup.setMaximumWidth(max_w)
        popup.adjustSize()
        pw = popup.width()
        ph = popup.height()

        pos = w.mapTo(w.window(), QPoint(0, 0))
        bx, by = pos.x(), pos.y()

        if self._side == "left":
            x = bx - pw - TOOLTIP_EDGE_GAP_PX
            y = by + (w.height() - ph) // 2
        else:
            if self._align == "left":
                x = bx
            elif self._align == "right":
                x = bx + w.width() - pw
            else:
                x = bx + (w.width() - pw) // 2
            y = by - ph - 4
        # 钳位到窗口边界内，避免左侧或右侧溢出
        win_w = w.window().width()
        x = max(TOOLTIP_EDGE_GAP_PX, min(x, win_w - pw - TOOLTIP_EDGE_GAP_PX))
        target = QRect(x, y, pw, ph)
        popup.setGeometry(target)
        popup.show()
        self._popup = popup

    def _hide(self) -> None:
        if self._popup:
            self._popup.hide()
            self._popup.deleteLater()
            self._popup = None

    def close(self) -> None:
        """立即关闭提示，供替换或销毁拥有者时调用。"""
        self._timer.stop()
        self._hide()
