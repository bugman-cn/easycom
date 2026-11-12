"""自定义颜色选择对话框：深色/浅色主题独立色块，可选透明度滑块。

用于时间戳颜色（前景色，无透明度）和检索高亮颜色（背景色，带透明度）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QColorDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gui.themed_dialog import ThemedDialog
from gui.rounded_scroll_bar import configure_combo_popup
from config.layout import (
    COLOR_DIALOG_MIN_WIDTH,
    COLOR_OPACITY_LABEL_WIDTH,
    COLOR_OPACITY_MAX,
    COLOR_OPACITY_TICK_INTERVAL,
    COLOR_PREVIEW_FONT_SIZE_PX,
    COLOR_PREVIEW_GRID_SPACING,
    COLOR_PREVIEW_HEIGHT,
    COLOR_PREVIEW_RADIUS_PX,
    COLOR_PREVIEW_WIDTH,
    COLOR_SWATCH_BORDER_PX,
    COLOR_SWATCH_HOVER_BORDER_PX,
    COLOR_SWATCH_RADIUS_PX,
    COLOR_SWATCH_SIZE,
    CONTROL_HEIGHT,
)
from config.theme import THEME_COLORS
from utils.font_manager import get_display_font_css_stack


def choose_color(current: QColor, parent: QWidget | None, title: str) -> QColor:
    """在统一的设置弹窗内显示 Qt 色块选择器。"""
    dialog = ThemedDialog(parent, title)
    picker = QColorDialog(current, dialog)
    picker.setOptions(
        QColorDialog.ColorDialogOption.DontUseNativeDialog
        | QColorDialog.ColorDialogOption.NoButtons
    )
    picker.setWindowFlags(Qt.WindowType.Widget)
    dialog.content_layout.addWidget(picker)

    buttons = QHBoxLayout()
    buttons.addStretch()
    cancel = QPushButton("取消")
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(cancel)
    apply = QPushButton("应用")
    apply.clicked.connect(dialog.accept)
    buttons.addWidget(apply)
    dialog.content_layout.addLayout(buttons)

    return picker.currentColor() if dialog.exec() == ThemedDialog.DialogCode.Accepted else QColor()


class ColorPickerDialog(ThemedDialog):
    """颜色选择对话框，深色/浅色主题各自独立色块 + 预览。

    Args:
        parent: 父窗口
        title: 对话框标题
        current_dark: 当前深色主题颜色 HEX
        current_light: 当前浅色主题颜色 HEX
        show_opacity: 是否显示透明度滑块（检索高亮模式）
        opacity: 当前透明度 0-255
    """

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        current_dark: str,
        current_light: str,
        show_opacity: bool = False,
        opacity: int = 255,
        format_options: list[tuple[str, str]] | None = None,
        current_format: str | None = None,
        format_label: str = "时间戳样式:",
    ) -> None:
        super().__init__(parent, title)
        self.setMinimumWidth(COLOR_DIALOG_MIN_WIDTH)
        self._show_opacity = show_opacity
        self._dark = current_dark
        self._light = current_light
        self._opacity = max(0, min(COLOR_OPACITY_MAX, opacity))
        self._selected_dark = current_dark
        self._selected_light = current_light
        self._selected_opacity = self._opacity
        self._format_options = list(format_options or [])
        self._selected_format = current_format
        self._format_label = format_label

        self._build_ui()
        self._refresh_preview()

    def _build_ui(self) -> None:
        root = self.content_layout

        # -- 深色主题颜色行 --
        dark_row = QHBoxLayout()
        dark_row.addWidget(QLabel("深色主题:"))
        self._dark_swatch = QLabel()
        self._dark_swatch.setFixedSize(COLOR_SWATCH_SIZE, COLOR_SWATCH_SIZE)
        self._dark_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dark_swatch.setToolTip("点击选择深色主题颜色")
        self._dark_swatch.setStyleSheet(
            f"background-color:{self._dark}; border:{COLOR_SWATCH_BORDER_PX}px solid {THEME_COLORS['dark']['color_picker_border']}; border-radius:{COLOR_SWATCH_RADIUS_PX}px;"
        )
        self._dark_swatch.mousePressEvent = lambda e: self._on_pick_dark()
        dark_row.addWidget(self._dark_swatch)
        self._dark_label = QLabel(self._dark)
        dark_row.addWidget(self._dark_label)
        dark_row.addStretch()
        root.addLayout(dark_row)

        # -- 浅色主题颜色行 --
        light_row = QHBoxLayout()
        light_row.addWidget(QLabel("浅色主题:"))
        self._light_swatch = QLabel()
        self._light_swatch.setFixedSize(COLOR_SWATCH_SIZE, COLOR_SWATCH_SIZE)
        self._light_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._light_swatch.setToolTip("点击选择浅色主题颜色")
        self._light_swatch.setStyleSheet(
            f"background-color:{self._light}; border:{COLOR_SWATCH_BORDER_PX}px solid {THEME_COLORS['light']['color_picker_border']}; border-radius:{COLOR_SWATCH_RADIUS_PX}px;"
        )
        self._light_swatch.mousePressEvent = lambda e: self._on_pick_light()
        light_row.addWidget(self._light_swatch)
        self._light_label = QLabel(self._light)
        light_row.addWidget(self._light_label)
        light_row.addStretch()
        root.addLayout(light_row)

        # -- 透明度滑块（仅检索高亮模式） --
        if self._show_opacity:
            opacity_row = QHBoxLayout()
            opacity_row.addWidget(QLabel("透明度:"))
            self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
            self._opacity_slider.setRange(0, COLOR_OPACITY_MAX)
            self._opacity_slider.setValue(self._opacity)
            self._opacity_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            self._opacity_slider.setTickInterval(COLOR_OPACITY_TICK_INTERVAL)
            self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
            opacity_row.addWidget(self._opacity_slider, stretch=1)
            self._opacity_label = QLabel(f"{self._pct(self._opacity)}%")
            self._opacity_label.setFixedWidth(COLOR_OPACITY_LABEL_WIDTH)
            self._opacity_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            opacity_row.addWidget(self._opacity_label)
            root.addLayout(opacity_row)

        # -- 预览区 --
        preview_grp = QGroupBox("预览效果")
        grid = QGridLayout(preview_grp)
        grid.setSpacing(COLOR_PREVIEW_GRID_SPACING)

        grid.addWidget(QLabel("深色主题"), 0, 0)
        self._dark_preview = self._make_preview_label()
        grid.addWidget(self._dark_preview, 0, 1)

        grid.addWidget(QLabel("浅色主题"), 1, 0)
        self._light_preview = self._make_preview_label()
        grid.addWidget(self._light_preview, 1, 1)

        root.addWidget(preview_grp)

        # -- 可选下拉（例如时间戳样式） --
        if self._format_options:
            fmt_row = QHBoxLayout()
            fmt_row.addWidget(QLabel(self._format_label))
            self._format_combo = QComboBox()
            # 对齐串口号选择框：使用可编辑只读模式以复用同一外观与箭头样式。
            self._format_combo.setEditable(True)
            self._format_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            if self._format_combo.lineEdit() is not None:
                self._format_combo.lineEdit().setReadOnly(True)
            self._format_combo.setFixedHeight(CONTROL_HEIGHT)
            self._format_combo.setStyleSheet(
                f"QComboBox {{ font-family: {get_display_font_css_stack()}; }}"
            )
            for label, value in self._format_options:
                self._format_combo.addItem(label, value)
            idx = self._format_combo.findData(self._selected_format)
            self._format_combo.setCurrentIndex(idx if idx >= 0 else 0)
            configure_combo_popup(self._format_combo)
            self._selected_format = str(self._format_combo.currentData() or "")
            self._format_combo.currentIndexChanged.connect(self._on_format_changed)
            fmt_row.addWidget(self._format_combo, stretch=1)
            root.addLayout(fmt_row)

        # -- 按钮 --
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        ok = QPushButton("应用")
        ok.clicked.connect(self.accept)
        btn_row.addWidget(ok)
        root.addLayout(btn_row)

    def _make_preview_label(self) -> QLabel:
        lbl = QLabel("Hello")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedHeight(COLOR_PREVIEW_HEIGHT)
        lbl.setFixedWidth(COLOR_PREVIEW_WIDTH)
        lbl.setStyleSheet(
            f"font-size:{COLOR_PREVIEW_FONT_SIZE_PX}px; font-weight:bold; border-radius:{COLOR_PREVIEW_RADIUS_PX}px;"
        )
        return lbl

    @staticmethod
    def _pct(v: int) -> int:
        return round(v / 255 * 100)

    def _on_pick_dark(self) -> None:
        color = choose_color(QColor(self._dark), self, "选择深色主题颜色")
        if color.isValid():
            self._dark = color.name()
            self._selected_dark = color.name()
            self._dark_swatch.setStyleSheet(
                f"background-color:{self._dark}; border:{COLOR_SWATCH_BORDER_PX}px solid {THEME_COLORS['dark']['color_picker_border']}; border-radius:{COLOR_SWATCH_RADIUS_PX}px;"
            )
            self._dark_label.setText(self._dark)
            self._refresh_preview()

    def _on_pick_light(self) -> None:
        color = choose_color(QColor(self._light), self, "选择浅色主题颜色")
        if color.isValid():
            self._light = color.name()
            self._selected_light = color.name()
            self._light_swatch.setStyleSheet(
                f"background-color:{self._light}; border:{COLOR_SWATCH_BORDER_PX}px solid {THEME_COLORS['light']['color_picker_border']}; border-radius:{COLOR_SWATCH_RADIUS_PX}px;"
            )
            self._light_label.setText(self._light)
            self._refresh_preview()

    def _on_opacity_changed(self, value: int) -> None:
        self._opacity = value
        self._selected_opacity = value
        self._opacity_label.setText(f"{self._pct(value)}%")
        self._refresh_preview()

    def _on_format_changed(self, _index: int) -> None:
        if hasattr(self, "_format_combo"):
            self._selected_format = str(self._format_combo.currentData() or "")

    def _refresh_preview(self) -> None:
        if self._show_opacity:
            # 检索高亮：颜色背景 + alpha，白色/浅灰文本
            dark_bg = QColor(self._dark)
            dark_bg.setAlpha(self._opacity)
            dark_rgba = f"rgba({dark_bg.red()},{dark_bg.green()},{dark_bg.blue()},{dark_bg.alpha()})"
            light_bg = QColor(self._light)
            light_bg.setAlpha(self._opacity)
            light_rgba = f"rgba({light_bg.red()},{light_bg.green()},{light_bg.blue()},{light_bg.alpha()})"
            self._dark_preview.setStyleSheet(
                f"color:{THEME_COLORS['dark']['text']}; background-color:{dark_rgba}; font-size:{COLOR_PREVIEW_FONT_SIZE_PX}px; font-weight:bold; border-radius:{COLOR_PREVIEW_RADIUS_PX}px;"
            )
            self._light_preview.setStyleSheet(
                f"color:{THEME_COLORS['light']['text']}; background-color:{light_rgba}; font-size:{COLOR_PREVIEW_FONT_SIZE_PX}px; font-weight:bold; border-radius:{COLOR_PREVIEW_RADIUS_PX}px;"
            )
        else:
            # 时间戳：颜色文本 + 主题背景
            self._dark_preview.setStyleSheet(
                f"color:{self._dark}; background-color:{THEME_COLORS['dark']['color_preview_bg']}; font-size:{COLOR_PREVIEW_FONT_SIZE_PX}px; font-weight:bold; border-radius:{COLOR_PREVIEW_RADIUS_PX}px;"
            )
            self._light_preview.setStyleSheet(
                f"color:{self._light}; background-color:{THEME_COLORS['light']['color_preview_bg']}; font-size:{COLOR_PREVIEW_FONT_SIZE_PX}px; font-weight:bold; border-radius:{COLOR_PREVIEW_RADIUS_PX}px;"
            )

    def selected_dark(self) -> str:
        """返回用户选择的深色主题颜色 HEX。"""
        return self._selected_dark

    def selected_light(self) -> str:
        """返回用户选择的浅色主题颜色 HEX。"""
        return self._selected_light

    def opacity(self) -> int:
        """返回用户选择的透明度 0-255。"""
        return self._selected_opacity

    def selected_format(self) -> str:
        """返回用户选择的下拉值（未启用下拉时返回空字符串）。"""
        return str(self._selected_format or "")
