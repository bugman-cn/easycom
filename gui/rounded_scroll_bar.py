"""统一的圆角竖向滚动条与下拉列表配置。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QRegion
from PySide6.QtWidgets import (
    QComboBox,
    QScrollBar,
    QStyle,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QStyledItemDelegate,
)

from config.app_config import config
from config.layout import SCROLL_BAR_WIDTH
from config.theme import THEME_COLORS


class RoundedScrollBar(QScrollBar):
    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedWidth(SCROLL_BAR_WIDTH)

    def paintEvent(self, event) -> None:
        if self.maximum() <= self.minimum():
            return

        option = QStyleOptionSlider()
        self.initStyleOption(option)
        slider = self.style().subControlRect(
            QStyle.ComplexControl.CC_ScrollBar,
            option,
            QStyle.SubControl.SC_ScrollBarSlider,
            self,
        )
        rect = QRect(2, slider.y(), max(1, self.width() - 4), slider.height())
        if rect.isEmpty():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color_key = "scrollbar_handle_hover" if self.underMouse() else "scrollbar_handle"
        color = THEME_COLORS.get(config.theme, THEME_COLORS["dark"])[color_key]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        radius = min(rect.width(), rect.height()) / 2
        painter.drawRoundedRect(rect, radius, radius)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class RoundedSelectionDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        option = QStyleOptionViewItem(option)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(option.palette.brush(QPalette.ColorRole.Highlight))
            painter.drawRoundedRect(option.rect.adjusted(3, 1, -3, -1), 4, 4)
            painter.restore()
            option.state &= ~QStyle.StateFlag.State_Selected
            option.state &= ~QStyle.StateFlag.State_HasFocus
            option.palette.setColor(
                QPalette.ColorRole.Text,
                option.palette.color(QPalette.ColorRole.HighlightedText),
            )
        super().paint(painter, option, index)


def configure_combo_popup(combo: QComboBox) -> None:
    """将下拉列表统一限制为 8 行并应用圆角滚动条。"""
    combo.setMaxVisibleItems(8)
    view = combo.view()
    view.setVerticalScrollBar(RoundedScrollBar(view))
    view.setAutoScroll(False)
    view.window().setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    if not isinstance(view.itemDelegate(), RoundedSelectionDelegate):
        view.setItemDelegate(RoundedSelectionDelegate(view))
    sync = _ComboPopupSelectionSync(combo)
    view.window().installEventFilter(sync)
    # Python 包装对象需要显式保留引用，否则事件过滤器会被回收。
    combo._popup_selection_sync = sync


class _ComboPopupSelectionSync(QObject):
    """显示下拉列表时，同步有效的当前项高亮状态。"""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self._combo = combo

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self._sync_selection)
            QTimer.singleShot(0, self._round_popup_corners)
        elif event.type() == QEvent.Type.Resize:
            self._round_popup_corners()
        return False

    def _round_popup_corners(self) -> None:
        popup = self._combo.view().window()
        path = QPainterPath()
        path.addRoundedRect(popup.rect(), 6, 6)
        popup.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _sync_selection(self) -> None:
        view = self._combo.view()
        row = self._combo.currentIndex()
        # 搜索框会在刷新历史后保持编辑文本并清空 currentIndex；
        # 此时按文本回查历史项，命中才高亮。
        if row < 0 and self._combo.isEditable():
            row = self._combo.findText(
                self._combo.currentText(), Qt.MatchFlag.MatchExactly
            )
        selection = view.selectionModel()
        if selection is None:
            return
        if row < 0:
            selection.clearSelection()
            view.setCurrentIndex(QModelIndex())
            return
        index = self._combo.model().index(row, self._combo.modelColumn(), self._combo.rootModelIndex())
        if not index.isValid():
            selection.clearSelection()
            view.setCurrentIndex(QModelIndex())
            return
        selection.setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        view.scrollTo(index)
