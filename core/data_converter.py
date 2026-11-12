"""数据转换与格式化纯函数：bytes<->hex、时间戳、换行。"""
from __future__ import annotations

from datetime import datetime


def bytes_to_hex(data: bytes) -> str:
    """bytes -> 'AA BB CC' 大写空格分隔。"""
    return " ".join(f"{b:02X}" for b in data)


def hex_string_to_bytes(s: str) -> bytes:
    """'41 42 43' 或 '414243' -> bytes。

    容错：去掉所有空白与常见分隔符，按2位分组；奇数位补0；
    非法十六进制字符触发 ValueError。
    """
    cleaned = "".join(ch for ch in s if ch not in " \t\r\n,;-_")
    if len(cleaned) % 2:
        cleaned = "0" + cleaned
    try:
        return bytes.fromhex(cleaned)
    except ValueError as e:
        raise ValueError(f"HEX 字符串非法: {e}") from e


def format_timestamp(ts_seconds: float | None = None, style: str = "yyyy-MM-dd hh:mm:ss.xxx") -> str:
    """时间戳，精度到毫秒。"""
    now = datetime.fromtimestamp(ts_seconds) if ts_seconds is not None else datetime.now()
    style_key = (style or "").strip()
    if style_key.startswith("[") and style_key.endswith("]"):
        style_key = style_key[1:-1].strip()
    key = style_key.lower()
    aliases = {
        "yyyy-mm-dd hh:mm:ss.xxx": "yyyy-MM-dd hh:mm:ss.xxx",
        "yyyy-mm-dd hh:mm:ss.sss": "yyyy-MM-dd hh:mm:ss.xxx",
        "yy-mm-dd hh:mm:ss.xxx": "yy-MM-dd hh:mm:ss.xxx",
        "yy-mm-dd hh:mm:ss.sss": "yy-MM-dd hh:mm:ss.xxx",
        "mm-dd hh:mm:ss.xxx": "MM-dd hh:mm:ss.xxx",
        "mm-dd hh:mm:ss.sss": "MM-dd hh:mm:ss.xxx",
        "hh:mm:ss.xxx": "hh:mm:ss.xxx",
        "hh:mm:ss.sss": "hh:mm:ss.xxx",
        "hh:mm:ss": "hh:mm:ss",
    }
    canonical = aliases.get(key, "yyyy-MM-dd hh:mm:ss.xxx")
    fmt_map = {
        "yyyy-MM-dd hh:mm:ss.xxx": "%Y-%m-%d %H:%M:%S.",
        "yy-MM-dd hh:mm:ss.xxx": "%y-%m-%d %H:%M:%S.",
        "MM-dd hh:mm:ss.xxx": "%m-%d %H:%M:%S.",
        "hh:mm:ss.xxx": "%H:%M:%S.",
        "hh:mm:ss": "%H:%M:%S",
    }
    fmt = fmt_map.get(canonical, fmt_map["yyyy-MM-dd hh:mm:ss.xxx"])
    return now.strftime(fmt) + (f"{now.microsecond // 1000:03d}" if canonical != "hh:mm:ss" else "")


def append_crlf(text: str, line_ending: str = "\r\n") -> str:
    """追加指定的换行符。"""
    return text + line_ending


def bytes_to_ascii(data: bytes, encoding: str = "utf-8") -> str:
    """bytes -> 文本，解码失败用替换字符避免崩溃。"""
    return data.decode(encoding, errors="replace")
