"""ANSI SGR 转义序列解析器 — 纯函数，无 Qt 依赖。

解析嵌入式设备通过 printf("\\033[32mOK\\033[0m\\n") 输出的带内颜色码，
输出洁净文本 + 着色分段列表，供 receive_panel 渲染层使用。

支持的 SGR 码:
  - 0(重置) 1(粗体)
  - 30-37(8色前景) 40-47(8色背景)
  - 90-97(亮色前景) 100-107(亮色背景)
  - 38;5;N(256色前景) 48;5;N(256色背景)
  - 38;2;R;G;B(真彩前景) 48;2;R;G;B(真彩背景)
"""
from __future__ import annotations

import re

# ---- ANSI 8色/亮色标准调色板 (Windows Terminal 配色) ----
_ANSI_FG: dict[int, str] = {
    30: "#0C0C0C", 31: "#C50F1F", 32: "#13A10E", 33: "#C19C00",
    34: "#0037DA", 35: "#881798", 36: "#3A96DD", 37: "#CCCCCC",
    90: "#767676", 91: "#E74856", 92: "#16C60C", 93: "#F9F1A5",
    94: "#3B78FF", 95: "#B4009E", 96: "#61D6D6", 97: "#F2F2F2",
}

_ANSI_BG: dict[int, str] = {
    40: "#0C0C0C", 41: "#C50F1F", 42: "#13A10E", 43: "#C19C00",
    44: "#0037DA", 45: "#881798", 46: "#3A96DD", 47: "#CCCCCC",
    100: "#767676", 101: "#E74856", 102: "#16C60C", 103: "#F9F1A5",
    104: "#3B78FF", 105: "#B4009E", 106: "#61D6D6", 107: "#F2F2F2",
}

_CSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
# ponytail: CSI nC (光标右移) → 展开为 n 个空格，保证普通模式缩进正确
_CSI_C_RE = re.compile(r"\x1b\[([0-9]*)C")
# 其余非 SGR 的 CSI 序列（光标移动等），统一剥离
_NON_SGR_CSI_RE = re.compile(r"\x1b\[[0-9;]*[A-BD-LN-Za-ln-z]")


def _xterm256_to_hex(code: int) -> str:
    """Convert xterm 256-color index (0-255) to hex string."""
    if 0 <= code <= 15:
        return _ANSI_FG.get(code + 30, "#000000") if code < 8 else _ANSI_FG.get(code + 82, "#000000")
    if 16 <= code <= 231:
        code -= 16
        r = code // 36
        g = (code % 36) // 6
        b = code % 6
        return "#{:02X}{:02X}{:02X}".format(
            int(r * 255 / 5),
            int(g * 255 / 5),
            int(b * 255 / 5),
        )
    if 232 <= code <= 255:
        level = int((code - 232) * 255 / 23)
        return "#{:02X}{:02X}{:02X}".format(level, level, level)
    return "#CCCCCC"


def _resolve_fg(fg: str | None, bg: str | None) -> str | None:
    """有背景但无显式前景时，返回 #FFFFFF 确保可读。"""
    if fg:
        return fg
    return "#FFFFFF" if bg else None


def parse_ansi_segments(text: str) -> tuple[str, list[tuple[int, int, dict]]]:
    """解析文本中的 ANSI SGR 转义序列，返回洁净文本与着色分段。

    Args:
        text: 含 \\x1b[...m 序列的原始文本

    Returns:
        (clean_text, segments) —
        clean_text: 去除所有 escape 序列后的纯文本
        segments: [(start, end, fmt_dict), ...]，位置基于 clean_text
        fmt_dict: {"fg": hex_str|None, "bg": hex_str|None, "bold": bool}
        无 ANSI 码时返回 (text, [])
    """
    if "\x1b" not in text:
        return text, []

    # ponytail: 将 CSI nC (光标右移) 展开为空格，保证普通模式缩进对齐
    text = _CSI_C_RE.sub(lambda m: " " * max(1, int(m.group(1) or 1)), text)
    # 剥离其余非 SGR 的 CSI 序列（光标移动等），避免在接收区显示为乱码
    text = _NON_SGR_CSI_RE.sub("", text)
    if "\x1b" not in text:
        return text, []

    # 状态机状态
    fg: str | None = None
    bg: str | None = None
    bold = False

    segments: list[tuple[int, int, dict]] = []
    clean_pos = 0
    last_end = 0

    for m in _CSI_RE.finditer(text):
        # 转义序列前的纯文本段
        text_before = text[last_end:m.start()]
        if text_before:
            if fg or bg or bold:
                segments.append((clean_pos, clean_pos + len(text_before), {
                    "fg": _resolve_fg(fg, bg), "bg": bg, "bold": bold,
                }))
            clean_pos += len(text_before)

        # 解析 SGR 参数
        params_str = m.group(1)
        if params_str:
            params = [int(p) if p else 0 for p in params_str.split(";")]
        else:
            params = [0]

        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                fg = bg = None
                bold = False
            elif p == 1:
                bold = True
            elif p == 38 and i + 2 < len(params) and params[i + 1] == 5:
                # 256色前景: 38;5;N
                fg = _xterm256_to_hex(params[i + 2])
                i += 2
            elif p == 48 and i + 2 < len(params) and params[i + 1] == 5:
                # 256色背景: 48;5;N
                bg = _xterm256_to_hex(params[i + 2])
                i += 2
            elif p == 38 and i + 4 < len(params) and params[i + 1] == 2:
                # 真彩前景: 38;2;R;G;B
                fg = "#{:02X}{:02X}{:02X}".format(
                    params[i + 2], params[i + 3], params[i + 4])
                i += 4
            elif p == 48 and i + 4 < len(params) and params[i + 1] == 2:
                # 真彩背景: 48;2;R;G;B
                bg = "#{:02X}{:02X}{:02X}".format(
                    params[i + 2], params[i + 3], params[i + 4])
                i += 4
            elif 30 <= p <= 37 or 90 <= p <= 97:
                fg = _ANSI_FG.get(p)
            elif 40 <= p <= 47 or 100 <= p <= 107:
                bg = _ANSI_BG.get(p)
            # 不支持的码静默忽略 (闪烁/反转/下划线等)
            i += 1

        last_end = m.end()

    # 末尾残余文本
    text_after = text[last_end:]
    if text_after:
        if fg or bg or bold:
            segments.append((clean_pos, clean_pos + len(text_after), {
                "fg": _resolve_fg(fg, bg), "bg": bg, "bold": bold,
            }))

    clean_text = _CSI_RE.sub("", text)
    return clean_text, segments
