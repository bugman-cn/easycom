"""主题化弹窗基类：自定义标题栏 + 红色圆形关闭按钮，跟随全局主题。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from gui.hover_popup import HoverPopup
from config.layout import (
    DIALOG_CLOSE_BUTTON_SIZE,
    DIALOG_CONTENT_MARGIN_PX,
    DIALOG_CONTENT_SPACING_PX,
    DIALOG_TITLE_LEFT_MARGIN_PX,
    DIALOG_TITLE_RIGHT_MARGIN_PX,
    DIALOG_TITLE_SPACING_PX,
    TITLE_BAR_HEIGHT,
)
from config.theme import FIXED_COLORS


class _CloseDot(QPushButton):
    """红色圆形关闭按钮，hover 时绘制 ✕ 符号。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hovered = False
        self.setFixedSize(DIALOG_CLOSE_BUTTON_SIZE, DIALOG_CLOSE_BUTTON_SIZE)
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
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        r = min(w, h) / 2.0
        cx, cy = w / 2.0, h / 2.0
        p.setBrush(QColor(FIXED_COLORS["title_close"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        if self._hovered:
            pen = QPen(QColor(FIXED_COLORS["icon_outline"]))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setWidthF(1.4)
            p.setPen(pen)
            diff = r * 0.4
            p.drawLine(QPointF(cx - diff, cy - diff), QPointF(cx + diff, cy + diff))
            p.drawLine(QPointF(cx + diff, cy - diff), QPointF(cx - diff, cy + diff))
        p.end()


class ThemedDialog(QDialog):
    """主题化弹窗：无边框 + 自定义标题栏 + 红色关闭按钮。

    子类在 __init__ 中调用 super().__init__(parent, title) 后，
    通过 self.content_layout 添加自己的控件。
    """

    def __init__(self, parent=None, title: str = "") -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_pos: QPointF | None = None

        # 外层垂直布局
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 边框容器：包含标题栏 + 内容区，统一绘制边框
        self._frame = QFrame()
        self._frame.setObjectName("themedDialogFrame")
        fv = QVBoxLayout(self._frame)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(0)
        outer.addWidget(self._frame)

        # 标题栏
        self._title_bar = QWidget()
        self._title_bar.setFixedHeight(TITLE_BAR_HEIGHT)
        self._title_bar.setAutoFillBackground(True)
        self._title_bar.setObjectName("themedDialogTitleBar")
        tb = QHBoxLayout(self._title_bar)
        tb.setContentsMargins(DIALOG_TITLE_LEFT_MARGIN_PX, 0, DIALOG_TITLE_RIGHT_MARGIN_PX, 0)
        tb.setSpacing(DIALOG_TITLE_SPACING_PX)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("themedDialogTitle")
        tb.addWidget(self._title_label)
        tb.addStretch()

        self._close_btn = _CloseDot(self._title_bar)
        self._close_btn.clicked.connect(self.reject)
        HoverPopup(self._close_btn, "关闭", side="left")
        tb.addWidget(self._close_btn)

        fv.addWidget(self._title_bar)

        # 内容区
        self._content = QWidget()
        self._content.setObjectName("themedDialogContent")
        self.content_layout = QVBoxLayout(self._content)
        self.content_layout.setContentsMargins(*((DIALOG_CONTENT_MARGIN_PX,) * 4))
        self.content_layout.setSpacing(DIALOG_CONTENT_SPACING_PX)
        fv.addWidget(self._content)

        # 鼠标拖动
        self._title_bar.mousePressEvent = self._title_mouse_press
        self._title_bar.mouseMoveEvent = self._title_mouse_move
        self._title_bar.mouseReleaseEvent = self._title_mouse_release

    def _title_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def _title_mouse_move(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None:
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()

    def _title_mouse_release(self, event: QMouseEvent) -> None:
        self._drag_pos = None
        event.accept()

    def set_dialog_title(self, title: str) -> None:
        self._title_label.setText(title)
