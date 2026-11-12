"""持久化用户设置的默认值。"""
from __future__ import annotations

from typing import Any

from config.ui import DEFAULT_APP_FONT_SIZE_PT
from config.runtime import RX_BUFFER_DEFAULT_MB


DEFAULT_TS_FORMAT = "yyyy-MM-dd hh:mm:ss.xxx"
RX_COLOR_DEFAULT_GROUP_COUNT = 3
RX_COLOR_DEFAULT_COLORS = (
    "#FF0000", "#00FF00", "#0000FF", "#FF9900", "#00CCCC",
    "#CC66FF", "#FF66AA", "#CCCC00", "#66AAFF", "#A07050",
)

DEFAULTS: dict[str, Any] = {
    "theme": "dark", "font_size": DEFAULT_APP_FONT_SIZE_PT,
    "window_width": 0, "window_height": 0, "window_maximized": False, "keep_awake": False,
    "encoding": "UTF-8", "buffer_size_mb": RX_BUFFER_DEFAULT_MB, "ts_enabled": False,
    "ts_format": DEFAULT_TS_FORMAT, "ts_color_dark": "#00CC00", "ts_color_light": "#008C00",
    "search_color_dark": "#FFEB3B", "search_color_light": "#FFEB3B", "search_opacity": 128,
    "tx_encoding": "UTF-8", "send_hex": False, "send_crlf": False, "tx_line_ending": "\r\n",
    "tx_echo_enabled": True, "auto_reconnect": False,
    "ext_cmd_enabled": False, "ext_cmd_splitter_ratio": 0.0,
    # 面板位置位于动态最小/最大宽度之间的比例；1.0 表示最大宽度。
    "ext_cmd_splitter_range_ratio": 1.0,
    "auto_save_enabled": False, "auto_save_dir": "auto-gen/uart-log",
    "first_use_notice_shown": False, "scroll_mode": "manual", "serial_mode": "normal",
}
