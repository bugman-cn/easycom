"""应用配置管理：JSON 文件读写，单例模式。

开发运行默认写入项目根目录 auto-gen/；打包运行写入 exe 同级目录下的 auto-gen/。
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from config.defaults import (
    DEFAULTS, DEFAULT_TS_FORMAT, RX_COLOR_DEFAULT_COLORS, RX_COLOR_DEFAULT_GROUP_COUNT,
)
from config.runtime import RX_COLOR_MAX_GROUPS

# 配置目录与主配置文件
def get_runtime_root_dir() -> Path:
    # 打包后以 exe 所在目录作为运行根目录；移动 exe 即形成全新运行环境。
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _resolve_config_dir() -> Path:
    return get_runtime_root_dir() / "auto-gen"


_CONFIG_DIR = _resolve_config_dir()
_APP_CONFIG = _CONFIG_DIR / "app.json"


def _normalize_ts_format(value: str) -> str:
    s = (value or "").strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    key = s.lower()
    alias = {
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
    return alias.get(key, DEFAULT_TS_FORMAT)


def get_config_dir() -> Path:
    return _CONFIG_DIR

class AppConfig:
    """应用配置单例，封装 JSON 读写。"""

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._load()

    # ---------- 通用接口 ----------
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    # ---------- 便捷属性 ----------
    @property
    def theme(self) -> str:
        return str(self.get("theme", DEFAULTS["theme"]))

    @theme.setter
    def theme(self, value: str) -> None:
        self.set("theme", value)

    @property
    def font_size(self) -> int:
        return int(self.get("font_size", DEFAULTS["font_size"]))

    @font_size.setter
    def font_size(self, value: int) -> None:
        self.set("font_size", value)

    @property
    def ts_color_dark(self) -> str:
        return str(self.get("ts_color_dark", DEFAULTS["ts_color_dark"]))

    @ts_color_dark.setter
    def ts_color_dark(self, value: str) -> None:
        self.set("ts_color_dark", value)

    @property
    def ts_color_light(self) -> str:
        return str(self.get("ts_color_light", DEFAULTS["ts_color_light"]))

    @ts_color_light.setter
    def ts_color_light(self, value: str) -> None:
        self.set("ts_color_light", value)

    @property
    def search_color_dark(self) -> str:
        return str(self.get("search_color_dark", DEFAULTS["search_color_dark"]))

    @search_color_dark.setter
    def search_color_dark(self, value: str) -> None:
        self.set("search_color_dark", value)

    @property
    def search_color_light(self) -> str:
        return str(self.get("search_color_light", DEFAULTS["search_color_light"]))

    @search_color_light.setter
    def search_color_light(self, value: str) -> None:
        self.set("search_color_light", value)

    @property
    def search_opacity(self) -> int:
        return int(self.get("search_opacity", DEFAULTS["search_opacity"]))

    @search_opacity.setter
    def search_opacity(self, value: int) -> None:
        self.set("search_opacity", max(0, min(255, int(value))))

    @property
    def buffer_size_mb(self) -> int:
        return int(self.get("buffer_size_mb", DEFAULTS["buffer_size_mb"]))

    @buffer_size_mb.setter
    def buffer_size_mb(self, value: int) -> None:
        self.set("buffer_size_mb", value)

    @property
    def encoding(self) -> str:
        return str(self.get("encoding", DEFAULTS["encoding"]))

    @encoding.setter
    def encoding(self, value: str) -> None:
        self.set("encoding", value)

    @property
    def tx_encoding(self) -> str:
        return str(self.get("tx_encoding", DEFAULTS["tx_encoding"]))

    @tx_encoding.setter
    def tx_encoding(self, value: str) -> None:
        self.set("tx_encoding", value)

    @property
    def ts_enabled(self) -> bool:
        return bool(self.get("ts_enabled", DEFAULTS["ts_enabled"]))

    @ts_enabled.setter
    def ts_enabled(self, value: bool) -> None:
        self.set("ts_enabled", value)

    @property
    def ts_format(self) -> str:
        return _normalize_ts_format(str(self.get("ts_format", DEFAULT_TS_FORMAT)))

    @ts_format.setter
    def ts_format(self, value: str) -> None:
        self.set("ts_format", _normalize_ts_format(str(value)))

    @property
    def send_crlf(self) -> bool:
        return bool(self.get("send_crlf", DEFAULTS["send_crlf"]))

    @send_crlf.setter
    def send_crlf(self, value: bool) -> None:
        self.set("send_crlf", value)

    @property
    def send_hex(self) -> bool:
        return bool(self.get("send_hex", DEFAULTS["send_hex"]))

    @send_hex.setter
    def send_hex(self, value: bool) -> None:
        self.set("send_hex", value)

    @property
    def tx_echo_enabled(self) -> bool:
        return bool(self.get("tx_echo_enabled", DEFAULTS["tx_echo_enabled"]))

    @tx_echo_enabled.setter
    def tx_echo_enabled(self, value: bool) -> None:
        self.set("tx_echo_enabled", value)

    @property
    def auto_reconnect(self) -> bool:
        return bool(self.get("auto_reconnect", DEFAULTS["auto_reconnect"]))

    @auto_reconnect.setter
    def auto_reconnect(self, value: bool) -> None:
        self.set("auto_reconnect", value)

    @property
    def tx_line_ending(self) -> str:
        return str(self.get("tx_line_ending", DEFAULTS["tx_line_ending"]))

    @tx_line_ending.setter
    def tx_line_ending(self, value: str) -> None:
        self.set("tx_line_ending", value)

    @property
    def auto_save_enabled(self) -> bool:
        return bool(self.get("auto_save_enabled", DEFAULTS["auto_save_enabled"]))

    @auto_save_enabled.setter
    def auto_save_enabled(self, value: bool) -> None:
        self.set("auto_save_enabled", value)

    @property
    def auto_save_dir(self) -> str:
        return str(self.get("auto_save_dir", DEFAULTS["auto_save_dir"]))

    @auto_save_dir.setter
    def auto_save_dir(self, value: str) -> None:
        saved = str(value).strip() or str(DEFAULTS["auto_save_dir"])
        self.set("auto_save_dir", saved)

    @property
    def keep_awake(self) -> bool:
        return bool(self.get("keep_awake", DEFAULTS["keep_awake"]))

    @keep_awake.setter
    def keep_awake(self, value: bool) -> None:
        self.set("keep_awake", value)

    @property
    def window_width(self) -> int:
        return int(self.get("window_width", DEFAULTS["window_width"]))

    @property
    def window_height(self) -> int:
        return int(self.get("window_height", DEFAULTS["window_height"]))

    @property
    def window_maximized(self) -> bool:
        return bool(self.get("window_maximized", DEFAULTS["window_maximized"]))

    @property
    def scroll_mode(self) -> str:
        return str(self.get("scroll_mode", DEFAULTS["scroll_mode"]))

    @scroll_mode.setter
    def scroll_mode(self, value: str) -> None:
        self.set("scroll_mode", value)

    @property
    def serial_mode(self) -> str:
        return str(self.get("serial_mode", DEFAULTS["serial_mode"]))

    @serial_mode.setter
    def serial_mode(self, value: str) -> None:
        self.set("serial_mode", value)

    def save_window_size(self, width: int, height: int) -> None:
        """保存窗口尺寸（像素）。"""
        self._data["window_width"] = max(320, int(width))
        self._data["window_height"] = max(240, int(height))
        self._save()

    def save_window_state(self, width: int, height: int, maximized: bool) -> None:
        """保存窗口尺寸与最大化状态。"""
        self._data["window_width"] = max(320, int(width))
        self._data["window_height"] = max(240, int(height))
        self._data["window_maximized"] = bool(maximized)
        self._save()

    def save_window_maximized(self, maximized: bool) -> None:
        """仅保存最大化状态，不改动已保存的窗口尺寸。"""
        self._data["window_maximized"] = bool(maximized)
        self._save()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        loaded_data: dict[str, Any] | None = None
        try:
            if _APP_CONFIG.exists():
                with open(_APP_CONFIG, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        loaded_data = dict(loaded)
                        self._data.update(loaded_data)
            # 迁移旧版时间戳格式文本到规范写法
            self._data["ts_format"] = _normalize_ts_format(str(self._data.get("ts_format", DEFAULT_TS_FORMAT)))
        except Exception:
            pass  # 文件损坏时静默使用默认值
        # 补写版本升级新增的默认键到 JSON
        if loaded_data is None or self._data != loaded_data:
            self._save()

    def _save(self) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            data = dict(self._data)
            with open(_APP_CONFIG, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # 保存失败不抛异常


# ---------- UART 端口配置历史 ----------------
# 独立持久化：<config_dir>/uart-history/history.json

_MAX_UART_CONFIG = 64

def _com_sort_key(port: str) -> int:
    """从 'COM60' 提取数字 60 用于排序；非 COM 格式回退到 0。"""
    import re
    m = re.search(r"\d+", port)
    return int(m.group()) if m else 0


class UartHistory:
    """串口配置历史，独立 JSON 文件读写。"""

    def __init__(self) -> None:
        self._dir = _CONFIG_DIR / "uart-history"
        self._path = self._dir / "history.json"
        self._data: dict[str, dict] = {}
        self._load()

    # ---------- 公共接口 ----------
    def get(self, port: str) -> dict | None:
        """获取指定端口的已保存配置，无则返回 None。"""
        return self._data.get(port)

    def save(self, port: str, cfg: dict) -> None:
        """保存指定端口的配置参数，LRU 淘汰（最多 64 条）。"""
        self._data.pop(port, None)
        self._data[port] = cfg
        while len(self._data) > _MAX_UART_CONFIG:
            oldest = next(iter(self._data))
            del self._data[oldest]
        self._persist()

    def note(self, port: str) -> str:
        """获取端口备注，无则返回空字符串。"""
        entry = self._data.get(port)
        if not entry:
            return ""
        val = entry.get("note", "")
        # 防御旧版数据损坏：note 字段为非字符串时返回空
        return val if isinstance(val, str) else ""

    def save_note(self, port: str, note: str) -> None:
        """保存端口备注，保留已有参数。"""
        entry = self._data.pop(port, {})
        entry["note"] = note
        self._data[port] = entry
        while len(self._data) > _MAX_UART_CONFIG:
            oldest = next(iter(self._data))
            del self._data[oldest]
        self._persist()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        # 按 COM 数字排序，兼容直接从 app.json 拷过来的数据
                        self._data = dict(
                            sorted(loaded.items(), key=lambda kv: _com_sort_key(kv[0]))
                        )
        except Exception:
            pass

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            data = dict(
                sorted(self._data.items(), key=lambda kv: _com_sort_key(kv[0]))
            )
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# ---------- 检索历史 --------------------------
# 独立持久化：<config_dir>/search-history/history.json

_SEARCH_HISTORY_MAX = 32


class SearchHistory:
    """检索历史，独立 JSON 文件读写。"""

    def __init__(self) -> None:
        self._dir = _CONFIG_DIR / "search-history"
        self._path = self._dir / "history.json"
        self._items: list[str] = []
        self._load()

    @property
    def items(self) -> list[str]:
        return list(self._items)

    def load_into(self, target: list[str]) -> None:
        """加载到给定的列表（去重、去空、截断到 32）。"""
        raw = self._items
        hist: list[str] = []
        for item in raw:
            s = str(item).strip()
            if s and s not in hist:
                hist.append(s)
        target.clear()
        target.extend(hist[:_SEARCH_HISTORY_MAX])

    def save(self, items: list[str]) -> None:
        """保存列表（去重、去空、截断到 32）并落盘。"""
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in items:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                cleaned.append(s)
        self._items = cleaned[:_SEARCH_HISTORY_MAX]
        self._persist()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        self._items = loaded
        except Exception:
            pass

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._items, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# ---------- 发送历史 --------------------------
# 独立持久化：<config_dir>/tx-history/history.json

_TX_HISTORY_MAX = 99


class TxHistory:
    """发送历史，独立 JSON 文件读写。每项 {"text": str, "hex": bool, "crlf": bool}。"""

    def __init__(self) -> None:
        self._dir = _CONFIG_DIR / "tx-history"
        self._path = self._dir / "history.json"
        self._items: list[dict] = []
        self._load()

    @property
    def items(self) -> list[dict]:
        """返回历史列表副本。"""
        return list(self._items)

    @staticmethod
    def _entry_key(text: str, hex_enabled: bool, crlf_enabled: bool) -> tuple[str, bool, bool]:
        return text, bool(hex_enabled), bool(crlf_enabled)

    def add(self, text: str, hex_enabled: bool, crlf_enabled: bool) -> None:
        """记录一条发送历史。按 text+hex+crlf 联合去重，超量淘汰尾部。"""
        s = str(text)
        if s == "":
            return
        key = self._entry_key(s, hex_enabled, crlf_enabled)
        # 仅移出完全相同组合，HEX/CRLF 变体互不覆盖
        self._items = [
            it for it in self._items
            if self._entry_key(
                str(it.get("text", "")),
                bool(it.get("hex", False)),
                bool(it.get("crlf", False)),
            ) != key
        ]
        self._items.insert(0, {"text": s, "hex": bool(hex_enabled), "crlf": bool(crlf_enabled)})
        # 超量淘汰
        if len(self._items) > _TX_HISTORY_MAX:
            self._items = self._items[:_TX_HISTORY_MAX]
        self._persist()

    def clear(self) -> None:
        """ponytail: 清空全部发送历史并即时落盘。"""
        self._items.clear()
        self._persist()

    def remove(self, entry: dict) -> bool:
        """删除指定的文本和发送模式组合。"""
        key = self._entry_key(entry.get("text", ""), entry.get("hex", False), entry.get("crlf", False))
        remaining = [
            item for item in self._items
            if self._entry_key(item["text"], item["hex"], item["crlf"]) != key
        ]
        if len(remaining) == len(self._items):
            return False
        self._items = remaining
        self._persist()
        return True

    # ---------- 持久化 ----------
    def _load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        cleaned: list[dict] = []
                        seen: set[tuple[str, bool, bool]] = set()
                        for it in loaded:
                            if not isinstance(it, dict):
                                continue
                            text = str(it.get("text", ""))
                            if text == "":
                                continue
                            hex_enabled = bool(it.get("hex", False))
                            crlf_enabled = bool(it.get("crlf", False))
                            key = self._entry_key(text, hex_enabled, crlf_enabled)
                            if key in seen:
                                continue
                            seen.add(key)
                            cleaned.append({"text": text, "hex": hex_enabled, "crlf": crlf_enabled})
                        self._items = cleaned[:_TX_HISTORY_MAX]
        except Exception:
            pass

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._items, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# ---------- 接收文本颜色高亮 --------------------------
# 独立持久化：<config_dir>/color-text/color_text.json

class RxColorHighlight:
    """接收区文本颜色过滤，独立 JSON 文件读写。"""

    def __init__(self) -> None:
        self._dir = _CONFIG_DIR / "color-text"
        self._path = self._dir / "color_text.json"
        self._groups: list[dict] = []
        self._load()

    @property
    def items(self) -> list[dict]:
        """返回独立副本，编辑草稿不会修改已保存的颜色和关键词。"""
        return deepcopy(self._groups)

    @staticmethod
    def default_group(index: int) -> dict:
        """创建一组独立的默认设置，颜色按色板循环。"""
        return {
            "color": RX_COLOR_DEFAULT_COLORS[index % len(RX_COLOR_DEFAULT_COLORS)],
            "texts": [],
            "case_sensitive": False,
            "whole_word": False,
            "enabled": False,
        }

    @classmethod
    def _clean_groups(cls, groups: list[dict]) -> list[dict]:
        cleaned: list[dict] = []
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise ValueError("关键词分组必须是对象")
            color = str(group.get("color", "")).strip()
            raw_texts = group.get("texts", [])
            if isinstance(raw_texts, str):
                raw_texts = raw_texts.splitlines()
            if not isinstance(raw_texts, list):
                raise ValueError("关键词必须是列表或多行文本")
            texts = [text.strip() for text in raw_texts if isinstance(text, str) and text.strip()]
            cleaned.append({
                "color": color or cls.default_group(index)["color"],
                "texts": texts,
                "case_sensitive": bool(group.get("case_sensitive", False)),
                "whole_word": bool(group.get("whole_word", False)),
                "enabled": bool(group.get("enabled", bool(texts))),
            })
        return cleaned

    def save(self, groups: list[dict]) -> None:
        """保存实际数量的颜色组；拒绝越界输入，避免静默截断已有数据。"""
        if not isinstance(groups, list) or not 1 <= len(groups) <= RX_COLOR_MAX_GROUPS:
            raise ValueError(f"关键词分组数量必须为 1～{RX_COLOR_MAX_GROUPS} 组")
        cleaned = self._clean_groups(groups)
        self._groups = cleaned
        self._persist()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        try:
            if self._path.exists():
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, list) and loaded:
                    # 外部配置超出上限时保留全部，交由配置窗口删减后保存。
                    normalized = self._clean_groups(loaded)
                    # 仅补缺失字段，已有文本与选项在读取时保持原样。
                    self._groups = [
                        {**defaults, **group} for defaults, group in zip(normalized, loaded)
                    ]
                    return
        except (OSError, ValueError, TypeError):
            pass
        self._groups = [self.default_group(index) for index in range(RX_COLOR_DEFAULT_GROUP_COUNT)]

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._groups, f, indent=2, ensure_ascii=False)
        except Exception:
            pass


# 全局单例
config = AppConfig()
uart_history = UartHistory()
search_history = SearchHistory()
tx_history = TxHistory()
rx_color_hilight = RxColorHighlight()
