"""主题管理：浅色/深色主题切换，基于 QPalette + Fusion 样式 + 自定义标题栏。

使用无边框窗口 + 自定义 TitleBar，完全由 QPalette 与样式表控制，
避免 Windows DWM 原生标题栏无法跟随主题的问题。

所有 HEX 颜色集中在 config.theme.THEME_COLORS，修改颜色只需改配置文件。
"""
from __future__ import annotations

from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication, QWidget
from config.layout import INPUT_CORNER_RADIUS, SCROLL_BAR_WIDTH
from config.theme import THEME_COLORS
from config.ui import DEFAULT_CONTROL_EMOJI_FONT_SIZE_PT, DEFAULT_UI_FONT_SIZE_PT

# 主题键
LIGHT = "light"
DARK = "dark"


def _c(mode: str, key: str) -> str:
    """快捷取色：THEME_COLORS[mode][key]。"""
    return THEME_COLORS[mode][key]


def active_green_style(mode: str, extra: str = "") -> str:
    """Enabled/checked green bg stylesheet snippet, theme-aware text color.

    深色模式：绿底黑字；浅色模式：绿底白字。
    ``extra`` 可拼接额外 CSS（如 "padding: 0px;"）。
    """
    colors = THEME_COLORS[mode]
    return f"color: {colors['status_success_text']}; background-color: {colors['status_success_bg']}; {extra}"


def _build_palette(mode: str) -> QPalette:
    pal = QPalette()
    c = lambda k: _c(mode, k)

    pal.setColor(QPalette.ColorRole.Window, QColor(c("window")))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c("window_text")))
    pal.setColor(QPalette.ColorRole.Base, QColor(c("base")))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c("alternate_base")))
    pal.setColor(QPalette.ColorRole.Text, QColor(c("text")))
    pal.setColor(QPalette.ColorRole.Button, QColor(c("button")))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c("button_text")))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c("tooltip_base")))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c("tooltip_text")))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c("highlight")))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c("highlighted_text")))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c("placeholder_text")))

    return pal


def apply_theme(app: QApplication, mode: str, window: QWidget | None = None) -> None:
    """根据 mode ('light'/'dark') 应用 QPalette 主题 + 自定义标题栏样式表。"""
    from utils.font_manager import get_display_font_css_stack, get_emoji_family_name

    # Fusion 样式完整尊重 QPalette，避免 Windows 原生样式漏色
    if app.style().objectName().lower() != "fusion":
        app.setStyle("Fusion")
    app.setPalette(_build_palette(mode))

    # 窗口字体：普通文本优先主显示字体；中文/emoji 走回退链。
    display_font_css = get_display_font_css_stack()
    emoji_family = get_emoji_family_name()
    base = f"* {{ font-family: {display_font_css}; font-size: {DEFAULT_UI_FONT_SIZE_PT}pt; }}"
    emoji_btn = (
        f"QPushButton[emojiBtn=\"true\"] "
        f"{{ font-family: \"{emoji_family}\"; font-size: {DEFAULT_CONTROL_EMOJI_FONT_SIZE_PT}pt; }}"
    )

    # 自定义标题栏配色 + 标题栏内菜单按钮
    c = lambda k: _c(mode, k)
    (window if window is not None else app).setStyleSheet(
        base
        + emoji_btn
        + f"QLabel#crlfGhost {{ color: {c('ghost_text')}; }}"
        + f"QWidget#mainWindowRoot {{ background: {c('window')}; }}"
        + f"QFrame#mainWindowSurface {{ background: {c('window')}; border: 1px solid {c('dialog_frame_border')}; }}"
        + f"QWidget#titleBar {{ background: {c('title_bar')}; }}"
        f"QLabel#titleBarTitle {{ color: {c('title_text')}; font-weight: bold; padding-left: 4px; font-family: {display_font_css}; }}"
        "QWidget#menuBtnRow { background: transparent; }"
        f"QToolButton#menuBarBtn {{ background: transparent; border: none; color: {c('title_text')}; padding: 2px 5px; }}"
        f"QToolButton#menuBarBtn:hover {{ background: {c('menu_btn_hover')}; border-radius: 4px; }}"
        "QToolButton#menuBarBtn::menu-indicator { image: none; }"
        f"QWidget#receiveViewWrap {{ border: none; border-radius: 4px; background: {c('text_area_bg')}; }}"
        "QPlainTextEdit#receiveView { border: none; background: transparent; border-top-right-radius: 4px; border-bottom-right-radius: 4px; }"
        f"QWidget#terminalWrap {{ border: none; border-radius: 4px; background: {c('text_area_bg')}; }}"
        "QTextEdit#terminalEdit { border: none; background: transparent; border-top-right-radius: 4px; border-bottom-right-radius: 4px; }"
        f"QWidget#lineNumberGutter {{ background: transparent; color: {c('gutter_text')}; border-top-left-radius: 4px; border-bottom-left-radius: 4px; border-right: 1px solid {c('gutter_border')}; }}"
        f"QTextEdit#terminalEdit QScrollBar:vertical {{ border: none; background: transparent; width: {SCROLL_BAR_WIDTH}px; margin: 2px 2px 2px 0px; }}"
        f"QTextEdit#terminalEdit QScrollBar::handle:vertical {{ background: {c('scrollbar_handle')}; min-height: 22px; border-radius: 5px; }}"
        f"QTextEdit#terminalEdit QScrollBar::handle:vertical:hover {{ background: {c('scrollbar_handle_hover')}; }}"
        "QTextEdit#terminalEdit QScrollBar::add-line:vertical, QTextEdit#terminalEdit QScrollBar::sub-line:vertical { height: 0px; border: none; background: transparent; }"
        "QTextEdit#terminalEdit QScrollBar::add-page:vertical, QTextEdit#terminalEdit QScrollBar::sub-page:vertical { background: transparent; }"
        f"QTextEdit#terminalEdit QScrollBar:horizontal {{ border: none; background: transparent; height: 10px; margin: 0px 2px 2px 2px; }}"
        f"QTextEdit#terminalEdit QScrollBar::handle:horizontal {{ background: {c('scrollbar_handle')}; min-width: 22px; border-radius: 5px; }}"
        f"QTextEdit#terminalEdit QScrollBar::handle:horizontal:hover {{ background: {c('scrollbar_handle_hover')}; }}"
        "QTextEdit#terminalEdit QScrollBar::add-line:horizontal, QTextEdit#terminalEdit QScrollBar::sub-line:horizontal { width: 0px; border: none; background: transparent; }"
        "QTextEdit#terminalEdit QScrollBar::add-page:horizontal, QTextEdit#terminalEdit QScrollBar::sub-page:horizontal { background: transparent; }"
        f"QPlainTextEdit#receiveView QScrollBar:vertical {{ border: none; background: transparent; width: {SCROLL_BAR_WIDTH}px; margin: 2px 1px 2px 0px; }}"
        f"QPlainTextEdit#receiveView QScrollBar::handle:vertical {{ background: {c('scrollbar_handle')}; border: none; min-height: 24px; margin: 0px 2px; border-radius: 4px; }}"
        f"QPlainTextEdit#receiveView QScrollBar::handle:vertical:hover {{ background: {c('scrollbar_handle_hover')}; border: none; }}"
        "QPlainTextEdit#receiveView QScrollBar::add-line:vertical, QPlainTextEdit#receiveView QScrollBar::sub-line:vertical { height: 0px; border: none; background: transparent; }"
        "QPlainTextEdit#receiveView QScrollBar::add-page:vertical, QPlainTextEdit#receiveView QScrollBar::sub-page:vertical { background: transparent; }"
        f"QPlainTextEdit#receiveView QScrollBar:horizontal {{ border: none; background: transparent; height: 10px; margin: 0px 2px 2px 2px; }}"
        f"QPlainTextEdit#receiveView QScrollBar::handle:horizontal {{ background: {c('scrollbar_handle')}; min-width: 22px; border-radius: 5px; }}"
        f"QPlainTextEdit#receiveView QScrollBar::handle:horizontal:hover {{ background: {c('scrollbar_handle_hover')}; }}"
        "QPlainTextEdit#receiveView QScrollBar::add-line:horizontal, QPlainTextEdit#receiveView QScrollBar::sub-line:horizontal { width: 0px; border: none; background: transparent; }"
        "QPlainTextEdit#receiveView QScrollBar::add-page:horizontal, QPlainTextEdit#receiveView QScrollBar::sub-page:horizontal { background: transparent; }"
        f"QWidget#sendInputWrap {{ border: none; border-radius: {INPUT_CORNER_RADIUS}px; background: {c('text_area_bg')}; }}"
        f"QTextEdit#sendInput {{ border: none; background: transparent; border-top-right-radius: {INPUT_CORNER_RADIUS}px; border-bottom-right-radius: {INPUT_CORNER_RADIUS}px; }}"
        f"QWidget#keywordInputWrap {{ border: none; border-radius: {INPUT_CORNER_RADIUS}px; background: {c('text_area_bg')}; }}"
        "QTextEdit#keywordInput { border: none; background: transparent; }"
        f"QPushButton#sendBtn {{ border: none; background: {c('send_btn')}; border-radius: 4px; font-family: {display_font_css}; }}"
        f"QPushButton#sendBtn:hover {{ background: {c('send_btn_hover')}; }}"
        f"QLineEdit#msInput {{ border: none; border-radius: {INPUT_CORNER_RADIUS}px; }}"
        f"QLineEdit#extCmdInput {{ border: none; border-radius: {INPUT_CORNER_RADIUS}px; background: {c('base')}; padding: 0 4px; }}"
        f"QTextEdit#sendInput QScrollBar:vertical {{ border: none; background: transparent; width: {SCROLL_BAR_WIDTH}px; margin: 2px 2px 2px 0px; }}"
        f"QTextEdit#sendInput QScrollBar::handle:vertical {{ background: {c('scrollbar_handle')}; min-height: 22px; border-radius: 5px; }}"
        f"QTextEdit#sendInput QScrollBar::handle:vertical:hover {{ background: {c('scrollbar_handle_hover')}; }}"
        "QTextEdit#sendInput QScrollBar::add-line:vertical, QTextEdit#sendInput QScrollBar::sub-line:vertical { height: 0px; border: none; background: transparent; }"
        "QTextEdit#sendInput QScrollBar::add-page:vertical, QTextEdit#sendInput QScrollBar::sub-page:vertical { background: transparent; }"
        f"QTextEdit#sendInput QScrollBar:horizontal {{ border: none; background: transparent; height: 10px; margin: 0px 2px 2px 2px; }}"
        f"QTextEdit#sendInput QScrollBar::handle:horizontal {{ background: {c('scrollbar_handle')}; min-width: 22px; border-radius: 5px; }}"
        f"QTextEdit#sendInput QScrollBar::handle:horizontal:hover {{ background: {c('scrollbar_handle_hover')}; }}"
        "QTextEdit#sendInput QScrollBar::add-line:horizontal, QTextEdit#sendInput QScrollBar::sub-line:horizontal { width: 0px; border: none; background: transparent; }"
        "QTextEdit#sendInput QScrollBar::add-page:horizontal, QTextEdit#sendInput QScrollBar::sub-page:horizontal { background: transparent; }"
        f"QComboBox QAbstractItemView {{ background: {c('text_area_bg')}; color: {c('text')}; border: 1px solid {c('menu_border')}; border-radius: 6px; padding: 1px; selection-background-color: {c('highlight')}; selection-color: {c('highlighted_text')}; }}"
        f"QToolTip {{ background: {c('tooltip_base')}; color: {c('tooltip_text')}; border: 1px solid {c('menu_border')}; border-radius: 6px; padding: 5px; }}"
        f"QMenu {{ background: {c('menu_bg')}; color: {c('text')}; border: 1px solid {c('menu_border')}; padding: 2px; }}"
        "QMenu::item { padding: 4px 20px; }"
        f"QMenu::item:selected {{ background: {c('highlight')}; }}"
        "QMenu#titleBarMenu { border-radius: 6px; padding: 3px; }"
        "QMenu#titleBarMenu::item { padding: 4px 20px; margin: 2px 3px; border-radius: 4px; }"
        "QMenu#sendHistoryMenu { border-radius: 6px; padding: 3px; }"
        "QMenu#sendHistoryMenu::item, QMenu#sendHistoryMenu::item:selected { background: transparent; padding: 0; margin: 0; }"
        f"QScrollArea#sendHistoryScroll, QWidget#sendHistoryRows {{ border: none; background: {c('menu_bg')}; }}"
        f"QWidget#themedDialogTitleBar {{ background: {c('dialog_title_bar')}; border-top-left-radius: 6px; border-top-right-radius: 6px; }}"
        f"QLabel#themedDialogTitle {{ color: {c('title_text')}; font-weight: bold; }}"
        f"QWidget#themedDialogContent {{ background: {c('window')}; border-bottom-left-radius: 6px; border-bottom-right-radius: 6px; }}"
        f"QFrame#themedDialogFrame {{ background: {c('window')}; border: 2px solid {c('dialog_frame_border')}; border-radius: 8px; }}"
        f"QSplitter::handle {{ background: {c('splitter_handle')}; border-radius: 2px; margin: 12px 0; }}"
        f"QScrollBar:vertical {{ width: {SCROLL_BAR_WIDTH}px; margin: 2px 0; }}"
        f"QScrollBar::handle:vertical {{ border-radius: 5px; min-height: 22px; margin: 0 2px; }}"
        "QScrollBar:horizontal { margin: 0 2px; }"
        f"QScrollBar::handle:horizontal {{ border-radius: 5px; min-width: 22px; margin: 2px 0; }}"
        "QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; border: none; background: transparent; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
    )
