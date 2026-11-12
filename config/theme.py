"""深浅主题颜色定义。"""
from __future__ import annotations

THEME_COLORS: dict[str, dict[str, str]] = {
    "dark": {
        "window": "#2b2b2b", "window_text": "#d0d0d0", "base": "#161616", "alternate_base": "#1c1c1c", "text": "#d0d0d0", "button": "#353535", "button_text": "#d0d0d0", "tooltip_base": "#555555", "tooltip_text": "#d0d0d0", "highlight": "#2a82da", "highlighted_text": "#ffffff", "selection_text": "#ffffff", "placeholder_text": "#828282", "ghost_text": "rgba(170, 170, 170, 0.70)", "title_bar": "#2d2d2d", "title_text": "#d0d0d0", "menu_btn_hover": "#3a3a3a", "text_area_bg": "#161616", "gutter_text": "#8f8f8f", "gutter_border": "rgba(255,255,255,0.08)", "scrollbar_handle": "#666666", "scrollbar_handle_hover": "#7a7a7a", "send_btn": "#4a4a4a", "send_btn_hover": "#555555", "menu_bg": "#2a2a2a", "menu_border": "#555555", "dialog_title_bar": "#2d2d2d", "dialog_frame_border": "#555555", "splitter_handle": "#888888", "link": "#5599dd", "hover_border": "#8A8A8A", "history_hover": "rgba(128,128,128,0.15)", "status_success_text": "#000000", "status_success_bg": "rgba(76, 175, 80, 0.50)", "status_success_bg_hover": "rgba(76, 175, 80, 0.60)", "status_warning_text": "#000000", "status_warning_bg": "rgba(255, 235, 59, 0.40)", "status_warning_bg_hover": "rgba(255, 235, 59, 0.55)", "status_error_text": "#E81123", "status_error_text_on_bg": "#FFFFFF", "status_error_bg": "rgba(244, 67, 54, 0.85)", "status_error_bg_hover": "rgba(244, 67, 54, 0.95)", "status_error_soft_bg": "rgba(244, 67, 54, 0.30)", "color_picker_border": "#888888", "color_picker_border_hover": "#AAAAAA", "color_preview_bg": "#2d2d2d", "selection_overlay_bg": "rgba(0,0,0,0.55)", "selection_overlay_text": "#FFFFFF", "cursor_line_selection": "rgba(255,255,255,25)",
    },
    "light": {
        "window": "#e0e0e0", "window_text": "#000000", "base": "#f5f5f5", "alternate_base": "#eeeeee", "text": "#000000", "button": "#e0e0e0", "button_text": "#000000", "tooltip_base": "#C5C5C5", "tooltip_text": "#000000", "highlight": "#2a82da", "highlighted_text": "#ffffff", "selection_text": "#000000", "placeholder_text": "#969696", "ghost_text": "#888888", "title_bar": "#e0e0e0", "title_text": "#000000", "menu_btn_hover": "#d4d4d4", "text_area_bg": "#efefef", "gutter_text": "#8a8a8a", "gutter_border": "rgba(0,0,0,0.10)", "scrollbar_handle": "#939393", "scrollbar_handle_hover": "#7a7a7a", "send_btn": "#b5b5b5", "send_btn_hover": "#c5c5c5", "menu_bg": "#e8e8e8", "menu_border": "#cccccc", "dialog_title_bar": "#e0e0e0", "dialog_frame_border": "#cccccc", "splitter_handle": "#333333", "link": "#1a5c9e", "hover_border": "#7A7A7A", "history_hover": "rgba(128,128,128,0.15)", "status_success_text": "#FFFFFF", "status_success_bg": "rgba(76, 175, 80, 0.50)", "status_success_bg_hover": "rgba(76, 175, 80, 0.60)", "status_warning_text": "#000000", "status_warning_bg": "rgba(255, 235, 59, 0.40)", "status_warning_bg_hover": "rgba(255, 235, 59, 0.55)", "status_error_text": "#E81123", "status_error_text_on_bg": "#FFFFFF", "status_error_bg": "rgba(244, 67, 54, 0.85)", "status_error_bg_hover": "rgba(244, 67, 54, 0.95)", "status_error_soft_bg": "rgba(244, 67, 54, 0.30)", "color_picker_border": "#888888", "color_picker_border_hover": "#AAAAAA", "color_preview_bg": "#f0f0f0", "selection_overlay_bg": "rgba(0,0,0,0.55)", "selection_overlay_text": "#FFFFFF", "cursor_line_selection": "rgba(255,255,255,25)",
    },
}

# 与主题无关的固定语义色。
FIXED_COLORS: dict[str, str] = {
    "title_minimize": "#F5A623",
    "title_maximize": "#34C759",
    "title_close": "#E81123",
    "echo": "#FF5100",
    "history_newline": "#F00000",
    "active_button": "rgba(42, 130, 218, 0.50)",
    "icon_outline": "rgba(0,0,0,160)",
    "snap_preview": "rgba(42,130,218,72)",
}
