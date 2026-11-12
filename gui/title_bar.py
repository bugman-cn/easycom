"""自定义标题栏：用于无边框窗口，完全跟随 QPalette 主题。

提供标题文本、macOS 风格红/黄/绿三色圆形按钮(关闭/最小化/最大化)。
按钮位于右上角，hover 时用 QPainter 绘制清晰符号。
支持鼠标拖动移动窗口、双击最大化。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)
from gui.emoji_button import EmojiButton
from gui.hover_popup import HoverPopup
from config.layout import (
    PIN_BUTTON_SIZE,
    TITLE_BAR_HEIGHT,
    TITLE_BAR_LEFT_MARGIN,
    TITLE_BAR_RIGHT_MARGIN,
    TITLE_BAR_SPACING,
    TITLE_BUTTON_SIZE,
    TITLE_MENU_SPACING,
    TITLE_SECTION_SPACING,
)
from config.theme import FIXED_COLORS

# 符号类型
_SYM_CLOSE = "close"
_SYM_MIN = "min"
_SYM_MAX = "max"


class _DotButton(QPushButton):
    """macOS 风格圆形按钮：默认彩色，hover 时用 QPainter 绘制精致符号。"""

    def __init__(self, color: str, symbol: str, tooltip: str, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self._symbol = symbol
        self._hovered = False
        self.setFixedSize(TITLE_BUTTON_SIZE, TITLE_BUTTON_SIZE)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # rect 居中：drawEllipse 以 rect 为边界，但 rect 的 center 可能因整数截断偏移
        # 用浮点 rect 确保圆和符号都精确居中
        w, h = self.width(), self.height()
        r = min(w, h) / 2.0
        cx, cy = w / 2.0, h / 2.0
        circle_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        # 绘制圆形背景
        painter.setBrush(QColor(self._color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(circle_rect)
        # hover 时绘制符号（以 (cx, cy) 为中心）
        if self._hovered:
            pen = QPen(QColor(FIXED_COLORS["icon_outline"]))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setWidthF(1.4)
            painter.setPen(pen)
            if self._symbol == _SYM_CLOSE:
                diff = r * 0.4  # 叉号半径
                painter.drawLine(QPointF(cx - diff, cy - diff), QPointF(cx + diff, cy + diff))
                painter.drawLine(QPointF(cx + diff, cy - diff), QPointF(cx - diff, cy + diff))
            elif self._symbol == _SYM_MIN:
                line_r = r * 0.45
                painter.drawLine(QPointF(cx - line_r, cy), QPointF(cx + line_r, cy))
            elif self._symbol == _SYM_MAX:
                painter.setBrush(QColor(FIXED_COLORS["icon_outline"]))
                painter.setPen(Qt.PenStyle.NoPen)
                gap = r * 0.05
                spread = r * 0.5
                height = r * 0.35
                top = QPolygonF([
                    QPointF(cx - spread, cy - gap),
                    QPointF(cx + spread, cy - gap),
                    QPointF(cx, cy - gap - height),
                ])
                bottom = QPolygonF([
                    QPointF(cx - spread, cy + gap),
                    QPointF(cx + spread, cy + gap),
                    QPointF(cx, cy + gap + height),
                ])
                painter.drawPolygon(top)
                painter.drawPolygon(bottom)
        painter.end()


class TitleBar(QWidget):
    def __init__(self, parent: QWidget, title: str = "") -> None:
        super().__init__(parent)
        self._parent = parent
        self._pressing = False
        self._dragging = False
        self._press_global_pos = None
        self._drag_pos = None
        self._build_ui(title)

    def _build_ui(self, title: str) -> None:
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.setAutoFillBackground(True)
        self.setObjectName("titleBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(TITLE_BAR_LEFT_MARGIN, 0, TITLE_BAR_RIGHT_MARGIN, 0)
        lay.setSpacing(TITLE_BAR_SPACING)

        # 标题
        self.title_label = QLabel(title)
        self.title_label.setObjectName("titleBarTitle")
        lay.addWidget(self.title_label)
        lay.addStretch()

        # 菜单按钮容器
        self._menu_holder = QWidget(self)
        self._menu_holder.setObjectName("menuBtnRow")
        mh = QHBoxLayout(self._menu_holder)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.setSpacing(TITLE_MENU_SPACING)
        lay.addWidget(self._menu_holder)

        lay.addSpacing(TITLE_SECTION_SPACING)

        self.pin_btn = EmojiButton(
            "📌",
            checkable=True,
            size=PIN_BUTTON_SIZE,
            parent=self,
            circular=True,
            checked_background=FIXED_COLORS["active_button"],
        )
        self.pin_btn.setToolTip("置顶")
        self.pin_btn.toggled.connect(self._on_toggle_topmost)
        lay.addWidget(self.pin_btn)
        lay.addSpacing(TITLE_MENU_SPACING)

        # macOS 风格三色圆形按钮
        self.min_btn = _DotButton(FIXED_COLORS["title_minimize"], _SYM_MIN, "", self)
        self.min_btn.clicked.connect(self._on_minimize)
        HoverPopup(self.min_btn, "最小化", side="left")
        lay.addWidget(self.min_btn)

        self.max_btn = _DotButton(FIXED_COLORS["title_maximize"], _SYM_MAX, "", self)
        self.max_btn.clicked.connect(self._on_toggle_max)
        HoverPopup(self.max_btn, "最大化", side="left")
        lay.addWidget(self.max_btn)

        self.close_btn = _DotButton(FIXED_COLORS["title_close"], _SYM_CLOSE, "", self)
        self.close_btn.clicked.connect(self._parent.close)
        HoverPopup(self.close_btn, "关闭", side="left")
        lay.addWidget(self.close_btn)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def add_menu_button(self, text: str, menu: QMenu) -> None:
        """在标题栏 stretch 区域添加下拉菜单按钮。"""
        btn = QToolButton(self)
        btn.setText(f"{text} ▾")
        btn.setObjectName("menuBarBtn")
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setMenu(menu)
        for popup in (menu, *menu.findChildren(QMenu)):
            popup.setObjectName("titleBarMenu")
            popup.setWindowFlags(
                popup.windowFlags()
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.NoDropShadowWindowHint
            )
            popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._menu_holder.layout().addWidget(btn)

    # ---------- 窗口控制 ----------
    def _on_minimize(self) -> None:
        self._parent.showMinimized()

    def _on_toggle_topmost(self, enabled: bool) -> None:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            hwnd = wintypes.HWND(int(self._parent.winId()))
            insert_after = wintypes.HWND(-1 if enabled else -2)
            flags = 0x0001 | 0x0002 | 0x0010  # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            ctypes.windll.user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags)
        else:
            self._parent.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
            self._parent.show()
        self.pin_btn.setToolTip("取消置顶" if enabled else "置顶")

    def _on_toggle_max(self) -> None:
        if hasattr(self._parent, "toggle_window_maximized"):
            self._parent.toggle_window_maximized()
        elif self._parent.isMaximized():
            self._parent.showNormal()
            self.max_btn.setToolTip("最大化")
        else:
            self._parent.showMaximized()
            self.max_btn.setToolTip("还原")

    # ---------- 鼠标拖动 ----------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressing = True
            self._dragging = False
            self._press_global_pos = event.globalPosition().toPoint()
            self._drag_pos = event.globalPosition().toPoint() - self._parent.pos()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pressing and self._drag_pos is not None:
            global_pos = event.globalPosition().toPoint()
            if not self._dragging:
                if self._press_global_pos is None:
                    return
                distance = (global_pos - self._press_global_pos).manhattanLength()
                if distance < QApplication.startDragDistance():
                    event.accept()
                    return
                self._dragging = True
            if hasattr(self._parent, "restore_window_for_drag"):
                self._drag_pos = self._parent.restore_window_for_drag(global_pos, self._drag_pos)
            if hasattr(self._parent, "is_window_maximized") and self._parent.is_window_maximized():
                self._parent.set_window_maximized(False)
                self._drag_pos = event.globalPosition().toPoint() - self._parent.pos()
            elif self._parent.isMaximized():
                self._parent.showNormal()
            self._parent.move(global_pos - self._drag_pos)
            if hasattr(self._parent, "update_snap_preview"):
                self._parent.update_snap_preview(global_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging and hasattr(self._parent, "finish_window_drag"):
            self._parent.finish_window_drag()
        self._pressing = False
        self._dragging = False
        self._press_global_pos = None
        self._drag_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._on_toggle_max()
            event.accept()
