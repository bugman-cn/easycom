"""界面字体、尺寸和图标文案。"""
from __future__ import annotations

DEFAULT_DISPLAY_FONT_SIZE_PT = 11
DEFAULT_UI_FONT_SIZE_PT = 12
DEFAULT_CONTROL_EMOJI_FONT_SIZE_PT = 16
DEFAULT_APP_FONT_SIZE_PT = DEFAULT_DISPLAY_FONT_SIZE_PT
SEND_HISTORY_MENU_FONT_SIZE_PT = 11
FONT_SIZE_MIN_PT = 8
FONT_SIZE_MAX_PT = 48

EMOJI_BUTTON_TEXTS: dict[str, str] = {
    "cfg_add": "➕", "cfg_del": "✖", "row_add": "➕", "hex_mode": "📄", "crlf_mode": "🔚",
    "row_delete": "✖", "search_case": "Aa", "search_word": "W", "search_regex": ".*",
    "search_prev": "🔼", "search_next": "🔽", "search_toggle": "🔍", "clear": "🗑",
    "clear_rx": "🧹", "timestamp": "⏱", "auto_scroll": "🔃", "ext_cmd": "📋", "save": "💾",
    "terminal_mode": "💻", "config_expand": "▼", "send_history": "🔺", "auto_send": "🅰",
    "highlight_enabled": "✅",
}

FONT_FALLBACK_DEFAULT_FAMILIES: list[str] = ["Hack", "SimSun", "WenYuan Serif SC"]
FONT_FALLBACK_FAMILIES: list[str] = list(FONT_FALLBACK_DEFAULT_FAMILIES)
DISPLAY_FONT_FILE = "HACK-REGULAR.TTF"
CJK_PRIMARY_FONT_FILE = "WENYUANSERIFSC-REGULAR.TTF"
