from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy, QStyle, QStyleOption, QTextEdit, QWidget

from config.app_config import config
from config.layout import LINE_NUMBER_CORNER_RADIUS, LINE_NUMBER_LEFT_PADDING, LINE_NUMBER_RIGHT_PADDING
from config.theme import THEME_COLORS


class LineNumberGutter(QWidget):
    """左侧行号栏：逻辑行编号，自动换行不重复编号。"""

    LEFT_PADDING = LINE_NUMBER_LEFT_PADDING
    RIGHT_PADDING = LINE_NUMBER_RIGHT_PADDING
    CORNER_RADIUS = LINE_NUMBER_CORNER_RADIUS

    def __init__(self, editor: QPlainTextEdit | QTextEdit, max_digits: int = 6, parent=None) -> None:
        super().__init__(parent)
        self._editor = editor
        self._max_digits = max(1, int(max_digits))
        self._is_plain = isinstance(editor, QPlainTextEdit)
        self.setObjectName("lineNumberGutter")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._editor.installEventFilter(self)
        self._editor.viewport().installEventFilter(self)
        self._editor.cursorPositionChanged.connect(self._on_content_changed)
        if self._is_plain:
            self._editor.blockCountChanged.connect(self._on_content_changed)
            self._editor.updateRequest.connect(self._on_plain_update_request)
        else:
            self._editor.textChanged.connect(self._on_content_changed)
            self._editor.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

        self.sync_metrics()

    def sync_metrics(self) -> None:
        font = self._editor.font()
        # 主窗口的 * 字号样式会覆盖 setFont；行号栏需显式跟随编辑器字号。
        font_style = f"font-size: {font.pointSizeF():g}pt;"
        if self.styleSheet() != font_style:
            self.setStyleSheet(font_style)
        self.setFont(font)
        fm = QFontMetrics(self.font())
        width = self.LEFT_PADDING + fm.horizontalAdvance("9" * self._max_digits) + self.RIGHT_PADDING
        self.setFixedWidth(width)
        self.update()

    def eventFilter(self, watched, event) -> bool:
        if watched in (self._editor, self._editor.viewport()):
            if event.type() in (
                QEvent.Type.Resize,
                QEvent.Type.FontChange,
                QEvent.Type.StyleChange,
                QEvent.Type.PaletteChange,
            ):
                self.sync_metrics()
            elif event.type() in (QEvent.Type.UpdateRequest, QEvent.Type.Paint):
                self.update()
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:
        # 行号栏不可选中，点击时仅将焦点回到编辑区。
        self._editor.setFocus()
        event.accept()

    def _on_content_changed(self, *_args) -> None:
        self.update()

    def _on_scroll_changed(self, _value: int) -> None:
        self.update()

    def _on_plain_update_request(self, rect, dy: int) -> None:
        if dy:
            self.scroll(0, dy)
        else:
            self.update(0, rect.y(), self.width(), rect.height())

    def _highlighted_blocks(self) -> set[int]:
        if not self._editor.hasFocus():
            return set()
        cursor = self._editor.textCursor()
        if cursor.isNull():
            return set()
        if not cursor.hasSelection():
            return {cursor.blockNumber()}

        doc = self._editor.document()
        start = min(cursor.selectionStart(), cursor.selectionEnd())
        end = max(cursor.selectionStart(), cursor.selectionEnd())
        if end > start:
            end -= 1

        block = doc.findBlock(start)
        highlighted: set[int] = set()
        while block.isValid() and block.position() <= end:
            highlighted.add(block.blockNumber())
            block = block.next()
        return highlighted

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, option, painter, self)
        painter.setPen(self.palette().windowText().color())

        painter.save()
        self._apply_left_rounded_clip(painter)

        if self._is_plain:
            self._paint_for_plain_editor(painter, event.rect())
        else:
            self._paint_for_text_editor(painter, event.rect())
        painter.restore()

    def _apply_left_rounded_clip(self, painter: QPainter) -> None:
        rect = self.rect()
        if rect.isEmpty():
            return
        radius = min(float(self.CORNER_RADIUS), rect.width() / 2.0, rect.height() / 2.0)
        x = float(rect.x())
        y = float(rect.y())
        w = float(rect.width())
        h = float(rect.height())

        # ponytail: 仅裁剪左上/左下圆角，避免高亮方角溢出到容器外。
        path = QPainterPath()
        path.moveTo(x + radius, y)
        path.lineTo(x + w, y)
        path.lineTo(x + w, y + h)
        path.lineTo(x + radius, y + h)
        path.quadTo(x, y + h, x, y + h - radius)
        path.lineTo(x, y + radius)
        path.quadTo(x, y, x + radius, y)
        path.closeSubpath()
        painter.setClipPath(path)

    def _draw_number(self, painter: QPainter, top: int, height: int, number: int, highlighted: bool) -> None:
        if height <= 0:
            return
        # 使用编辑器首行的实际高度，避免大字号时与文本行高亮错位。
        rect = QRect(self.LEFT_PADDING, top, self.width() - self.LEFT_PADDING - self.RIGHT_PADDING, height)
        if highlighted:
            hc = self.palette().highlight().color()
            hc.setAlpha(70)
            painter.fillRect(rect.adjusted(-self.LEFT_PADDING, 0, self.RIGHT_PADDING, 0), hc)
            is_dark = config.theme == "dark"
            painter.setPen(QColor(THEME_COLORS[config.theme]["selection_text"]))
        else:
            painter.setPen(self.palette().windowText().color())
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(number))

    def _paint_for_plain_editor(self, painter: QPainter, clip_rect: QRect) -> None:
        editor: QPlainTextEdit = self._editor
        highlighted = self._highlighted_blocks()
        offset_y = editor.viewport().mapToGlobal(QPoint(0, 0)).y() - self.mapToGlobal(QPoint(0, 0)).y()
        block = editor.firstVisibleBlock()
        if not block.isValid():
            return
        # Accumulate layout heights instead of locating each block again via cursorRect.
        top = editor.blockBoundingGeometry(block).translated(editor.contentOffset()).top()
        while block.isValid() and top + offset_y <= clip_rect.bottom():
            height = editor.blockBoundingRect(block).height()
            if top + height + offset_y >= clip_rect.top():
                n = block.blockNumber()
                line = block.layout().lineAt(0)
                if line.isValid():
                    line_rect = QRectF(0, top + line.y(), 0, line.height()).toRect()
                    self._draw_number(painter, line_rect.top() + offset_y, line_rect.height(), n + 1, n in highlighted)
            top += height
            block = block.next()

    def _paint_for_text_editor(self, painter: QPainter, clip_rect: QRect) -> None:
        editor: QTextEdit = self._editor
        highlighted = self._highlighted_blocks()
        offset_y = editor.viewport().mapToGlobal(QPoint(0, 0)).y() - self.mapToGlobal(QPoint(0, 0)).y()
        first_cursor = editor.cursorForPosition(QPoint(1, 1))
        block = first_cursor.block()
        if not block.isValid():
            return

        layout = editor.document().documentLayout()
        viewport_h = editor.viewport().height()

        while block.isValid():
            block_cursor = QTextCursor(block)
            line_rect = editor.cursorRect(block_cursor)
            top = line_rect.top()
            if top > viewport_h:
                break
            height = max(int(layout.blockBoundingRect(block).height()), editor.fontMetrics().height())
            if top + height + offset_y >= clip_rect.top():
                n = block.blockNumber()
                self._draw_number(painter, top + offset_y, line_rect.height(), n + 1, n in highlighted)
            block = block.next()
