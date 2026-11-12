"""文件日志：把接收区文本保存到本地文件。"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Condition, Thread
import time
from typing import TextIO

from PySide6.QtWidgets import QFileDialog, QMessageBox
from config.app_config import get_runtime_root_dir
from config.runtime import FILE_LOG_FLUSH_INTERVAL_S, FILE_LOG_MAX_PENDING_CHARS


class AsyncLogWriter:
    FLUSH_INTERVAL_S = FILE_LOG_FLUSH_INTERVAL_S
    MAX_PENDING_CHARS = FILE_LOG_MAX_PENDING_CHARS

    def __init__(self, fp: TextIO) -> None:
        self._fp = fp
        self._condition = Condition()
        self._chunks: deque[str] = deque()
        self._pending_chars = 0
        self._closing = False
        self._error = ""
        self._thread = Thread(target=self._run, name="uart-log-writer", daemon=True)
        self._thread.start()

    def write(self, text: str) -> bool:
        if not text:
            return True
        with self._condition:
            if self._closing or self._error:
                return False
            if self._pending_chars + len(text) > self.MAX_PENDING_CHARS:
                self._error = "自动保存写入队列已满"
                self._condition.notify_all()
                return False
            self._chunks.append(text)
            self._pending_chars += len(text)
            self._condition.notify()
        return True

    def pending_chars(self) -> int:
        with self._condition:
            return self._pending_chars

    def error_message(self) -> str:
        with self._condition:
            return self._error

    def close(self, timeout: float = 5.0) -> bool:
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._thread.join(timeout)
        return not self._thread.is_alive() and not self.error_message()

    def _run(self) -> None:
        last_flush = time.monotonic()
        try:
            while True:
                with self._condition:
                    if not self._chunks and not self._closing:
                        self._condition.wait(self.FLUSH_INTERVAL_S)
                    chunks = list(self._chunks)
                    self._chunks.clear()
                    chars = self._pending_chars
                    self._pending_chars = 0
                    closing = self._closing
                if chunks:
                    self._fp.write("".join(chunks))
                now = time.monotonic()
                if chunks and (chars >= 64 * 1024 or now - last_flush >= self.FLUSH_INTERVAL_S):
                    self._fp.flush()
                    last_flush = now
                if closing:
                    self._fp.flush()
                    self._fp.close()
                    return
        except Exception as exc:
            with self._condition:
                self._error = str(exc)
            try:
                self._fp.close()
            except Exception:
                pass


def ensure_dir(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = get_runtime_root_dir() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename_part(text: str) -> str:
    bad = '<>:"/\\|?*'
    s = (text or "").strip()
    for ch in bad:
        s = s.replace(ch, "_")
    return s


def open_auto_log_file(save_dir: str, port: str, note: str) -> tuple[Path, AsyncLogWriter]:
    base = ensure_dir(save_dir)
    port_part = sanitize_filename_part(port) or "COM"
    note_part = sanitize_filename_part(note)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    if note_part:
        path = base / f"{port_part}_{note_part}_{ts}.txt"
    else:
        path = base / f"{port_part}_{ts}.txt"
    fp = path.open("a", encoding="utf-8", newline="")
    return path, AsyncLogWriter(fp)


def save_text(parent, text: str, port: str = "", note: str = "") -> Path | None:
    """弹出文件对话框保存 text，成功返回路径，取消返回 None。

    默认文件名与自动保存命名规则一致：
    - 有备注：{port}_{note}_{timestamp}.txt
    - 无备注：{port}_{timestamp}.txt
    """
    port_part = sanitize_filename_part(port) or "COM"
    note_part = sanitize_filename_part(note)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    if note_part:
        default_name = f"{port_part}_{note_part}_{ts}.txt"
    else:
        default_name = f"{port_part}_{ts}.txt"
    path, _ = QFileDialog.getSaveFileName(
        parent, "保存接收数据", default_name, "Text Files (*.txt);;Log Files (*.log);;All Files (*)"
    )
    if not path:
        return None
    try:
        Path(path).write_text(text, encoding="utf-8")
        return Path(path)
    except Exception as e:
        QMessageBox.warning(parent, "保存失败", str(e))
        return None
