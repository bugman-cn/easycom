"""可复用的 Emoji 图标按钮：支持 toggle（勾选态绿色背景）与 action（点击执行）。

通过 config.runtime.ICON_BORDER_DEBUG 控制调试模式：
- False（默认）：未勾选按钮扁平无边框，只显示 emoji 图标
- True：未勾选按钮保留 Qt Fusion 默认边框，方便对齐调试

光标悬停后以 60fps 动画从侧边滑出说明信息，背景跟随主题。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QPushButton

from config.app_config import config
from config.layout import (
    EMOJI_BUTTON_BORDER_WIDTH,
    EMOJI_BUTTON_DEFAULT_SIZE,
    EMOJI_BUTTON_RADIUS,
    EMOJI_ICON_MARGIN,
    EMOJI_ICON_MIN_SIZE,
    TOOLTIP_BUTTON_HEIGHT_INSET_PX,
    TOOLTIP_EDGE_GAP_PX,
    TOOLTIP_FONT_SIZE_PX,
    TOOLTIP_PADDING_X_PX,
    TOOLTIP_PADDING_Y_PX,
    TOOLTIP_RADIUS_PX,
)
from config.runtime import (
    ANIMATION_ENABLED,
    EMOJI_POPUP_DELAY_MS,
    EMOJI_POPUP_DURATION_MS,
    ICON_BORDER_DEBUG,
)
from config.theme import THEME_COLORS
from utils.theme import active_green_style

_ICON_LIGHT_DIR = Path(__file__).resolve().parent.parent / "icon" / "emoji-light"
_ICON_DARK_DIR = Path(__file__).resolve().parent.parent / "icon" / "emoji-dark"


def get_emoji_icon_path(text: str, theme: str | None = None) -> Path | None:
    cps = [ord(ch) for ch in text if ord(ch) not in (0xFE0F, 0x200D)]
    if not cps:
        return None

    icon_dir = _ICON_DARK_DIR if (theme or config.theme) == "dark" else _ICON_LIGHT_DIR
    direct_path = icon_dir / ("u" + "_".join(f"{cp:04x}" for cp in cps) + ".png")
    if direct_path.is_file():
        return direct_path

    first_path = icon_dir / f"u{cps[0]:04x}.png"
    return first_path if first_path.is_file() else None


class EmojiButton(QPushButton):
    """Emoji 图标按钮。构造时指定 text、checkable、size 即可，样式自动管理。

    说明弹窗：通过 setToolTip() 设置文本，光标悬停 POPUP_DELAY_MS 后从左侧滑出。
    """

    POPUP_DELAY_MS = EMOJI_POPUP_DELAY_MS
    POPUP_DURATION_MS = EMOJI_POPUP_DURATION_MS
    _ICON_LIGHT_DIR = _ICON_LIGHT_DIR
    _ICON_DARK_DIR = _ICON_DARK_DIR

    def __init__(
        self,
        text: str = "",
        checkable: bool = False,
        size: int = EMOJI_BUTTON_DEFAULT_SIZE,
        parent=None,
        circular: bool = False,
        checked_background: str | None = None,
        icon_size: int | None = None,
    ) -> None:
        super().__init__("", parent)
        self._btn_text = text
        self._circular = circular
        self._checked_background = checked_background
        self.setFixedSize(size, size)
        if icon_size is None:
            icon_size = max(EMOJI_ICON_MIN_SIZE, size - EMOJI_ICON_MARGIN)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setProperty("emojiBtn", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_icon_or_text()
        self._checkable = checkable
        if checkable:
            self.setCheckable(True)
            self.toggled.connect(self._on_toggled)
        self._refresh_style()

        # 动画弹窗
        self._popup_text = ""
        self._popup_timer = QTimer(self)
        self._popup_timer.setSingleShot(True)
        self._popup_timer.timeout.connect(self._show_popup)
        self._popup: QLabel | None = None
        self._popup_anim: QPropertyAnimation | None = None

    def _codepoints_for_text(self, text: str) -> list[int]:
        return [ord(ch) for ch in text if ord(ch) not in (0xFE0F, 0x200D)]

    def _active_icon_dir(self) -> Path:
        return self._ICON_DARK_DIR if config.theme == "dark" else self._ICON_LIGHT_DIR

    def _icon_path_for_text(self, text: str) -> Path | None:
        return get_emoji_icon_path(text)

    def _apply_icon_or_text(self) -> None:
        icon_path = self._icon_path_for_text(self._btn_text)
        if icon_path is None:
            self.setText(self._btn_text)
            self.setIcon(QIcon())
            return

        self.setText("")
        self.setIcon(QIcon(str(icon_path)))

    def _on_toggled(self, _checked: bool) -> None:
        self._refresh_style()

    def _refresh_style(self) -> None:
        colors = THEME_COLORS[config.theme]
        hover_border = colors["hover_border"]
        radius = self.height() // 2 if self._circular else EMOJI_BUTTON_RADIUS
        border_width = EMOJI_BUTTON_BORDER_WIDTH
        center_css = f"padding: 0px; text-align: center; border-radius: {radius}px;"
        if self._checkable and self.isChecked() and self.isEnabled():
            checked_style = (
                f"background-color: {self._checked_background};"
                if self._checked_background
                else active_green_style(config.theme)
            )
            hover_background = self._checked_background or colors["status_success_bg_hover"]
            self.setStyleSheet(
                f"QPushButton {{ {center_css} border: {border_width}px solid transparent; {checked_style} }}"
                f"QPushButton:hover {{ border: {border_width}px solid {hover_border}; background-color: {hover_background}; }}"
            )
        elif ICON_BORDER_DEBUG:
            self.setStyleSheet(f"QPushButton {{ {center_css} }}")
        else:
            self.setStyleSheet(
                f"QPushButton {{ {center_css} background: transparent; border: {border_width}px solid transparent; }}"
                f"QPushButton:hover {{ border: {border_width}px solid {hover_border}; background: transparent; }}"
            )

    # ---------- 动画弹窗 ----------
    def setToolTip(self, text: str) -> None:
        self._popup_text = text
        super().setToolTip("")

    def enterEvent(self, event) -> None:
        if self._popup_text:
            self._popup_timer.start(self.POPUP_DELAY_MS)
        super().enterEvent(event)

    def mousePressEvent(self, event) -> None:
        self._popup_timer.stop()
        self._hide_popup()
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:
        self._popup_timer.stop()
        self._hide_popup()
        super().leaveEvent(event)

    def moveEvent(self, event) -> None:
        self._hide_popup()
        super().moveEvent(event)

    def changeEvent(self, event) -> None:
        theme_change = getattr(QEvent.Type, "ThemeChange", None)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, theme_change):
            self._apply_icon_or_text()
        super().changeEvent(event)

    def _show_popup(self) -> None:
        if self._popup or not self._popup_text:
            return
        popup = QLabel(self._popup_text, self.window())
        popup.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        popup.raise_()

        colors = THEME_COLORS[config.theme]
        bg = colors["tooltip_base"]
        fg = colors["tooltip_text"]
        popup.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: {TOOLTIP_RADIUS_PX}px; "
            f"padding: {TOOLTIP_PADDING_Y_PX}px {TOOLTIP_PADDING_X_PX}px; font-size: {TOOLTIP_FONT_SIZE_PX}px; }}"
        )
        popup.adjustSize()
        pw = popup.width()
        bh = self.height()

        pos_in_win = self.mapTo(self.window(), QPoint(0, 0))
        bx, by = pos_in_win.x(), pos_in_win.y()
        bw = self.width()

        popup.setFixedHeight(bh - TOOLTIP_BUTTON_HEIGHT_INSET_PX)
        popup.setWindowOpacity(0.0)
        popup.show()

        center = QRect(bx + bw // 2, by + 2, 0, bh - TOOLTIP_BUTTON_HEIGHT_INSET_PX)
        if bx >= pw + TOOLTIP_EDGE_GAP_PX:
            full = QRect(bx - pw - 2, by + 2, pw, bh - TOOLTIP_BUTTON_HEIGHT_INSET_PX)
        else:
            full = QRect(bx + bw + 2, by + 2, pw, bh - TOOLTIP_BUTTON_HEIGHT_INSET_PX)

        if ANIMATION_ENABLED:
            anim = QPropertyAnimation(popup, b"geometry")
            anim.setDuration(self.POPUP_DURATION_MS)
            anim.setStartValue(center)
            anim.setEndValue(full)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.start()

            fade = QPropertyAnimation(popup, b"windowOpacity")
            fade.setDuration(self.POPUP_DURATION_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(0.92)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)
            fade.start()
        else:
            popup.setGeometry(full)
            popup.setWindowOpacity(0.92)
            anim = None

        self._popup = popup
        self._popup_anim = anim
        self._popup_center = center

    def _hide_popup(self) -> None:
        popup = self._popup
        if popup is None:
            self._stop_anim()
            return
        self._popup = None
        self._stop_anim()

        center = getattr(self, "_popup_center", QRect())
        if ANIMATION_ENABLED:
            anim = QPropertyAnimation(popup, b"geometry")
            anim.setDuration(self.POPUP_DURATION_MS // 2)
            anim.setStartValue(popup.geometry())
            anim.setEndValue(center)
            anim.setEasingCurve(QEasingCurve.Type.InCubic)
            anim.finished.connect(lambda: (popup.hide(), popup.deleteLater()))
            anim.start()

            fade = QPropertyAnimation(popup, b"windowOpacity")
            fade.setDuration(self.POPUP_DURATION_MS // 2)
            fade.setStartValue(popup.windowOpacity())
            fade.setEndValue(0.0)
            fade.setEasingCurve(QEasingCurve.Type.InCubic)
            fade.start()
        else:
            popup.hide()
            popup.deleteLater()
            anim = None

        # ponytail: 保持引用防止 GC 回收导致动画中断
        self._popup_anim = anim

    def _stop_anim(self) -> None:
        if self._popup_anim:
            self._popup_anim.stop()
            self._popup_anim = None
