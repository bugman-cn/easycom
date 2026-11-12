"""有界、低频的运行诊断。禁止在这里记录串口载荷。"""
from __future__ import annotations

from collections import deque
import logging
from pathlib import Path
import sys
from threading import Condition, Lock, Thread
import threading
import time

from config.runtime import (
    DIAGNOSTIC_FILE_BYTES, DIAGNOSTIC_QUEUE_RECORDS,
    DIAGNOSTIC_RECORD_BYTES, DIAGNOSTIC_REPEAT_INTERVAL_S,
)

logger = logging.getLogger("uart")
logger.addHandler(logging.NullHandler())
logger.propagate = False
_handler = None
_old_hooks = None


def event(level: int, message: str, *, key: str = "", exc_info=False) -> None:
    logger.log(level, message, extra={"event_key": key}, exc_info=exc_info)


class DiagnosticHandler(logging.Handler):
    def __init__(self, directory: Path, *, file_bytes=DIAGNOSTIC_FILE_BYTES,
                 queue_records=DIAGNOSTIC_QUEUE_RECORDS, repeat_interval=DIAGNOSTIC_REPEAT_INTERVAL_S):
        super().__init__()
        self.directory = Path(directory)
        self.file_bytes = file_bytes
        self.queue_records = queue_records
        self.repeat_interval = repeat_interval
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
        self._condition = Condition()
        self._disk_lock = Lock()
        self._queue = deque()
        self._repeats = {}
        self._dropped = 0
        self._closing = False
        self._writing = False
        self._failure = ""
        self._failure_reported = False
        self._thread = Thread(target=self._run, name="uart-diagnostics", daemon=True)
        self._thread.start()

    def _encode(self, text: str) -> bytes:
        data = (text.rstrip("\n") + "\n").encode("utf-8", errors="replace")
        limit = min(DIAGNOSTIC_RECORD_BYTES, self.file_bytes)
        if len(data) > limit:
            suffix = b" ... [truncated]\n"
            data = data[:limit - len(suffix)].decode("utf-8", errors="ignore").encode("utf-8") + suffix
        return data

    def _append(self, data: bytes) -> None:
        if len(self._queue) >= self.queue_records:
            self._queue.popleft()
            self._dropped += 1
        self._queue.append(data)

    def _summary(self, key: str, count: int) -> bytes:
        record = logging.LogRecord("uart", logging.WARNING, "", 0,
                                   f"重复事件 {key}: 省略 {count} 次", (), None)
        return self._encode(self.format(record))

    def emit(self, record) -> None:
        try:
            with self._condition:
                if self._closing or self._failure:
                    return
                key = getattr(record, "event_key", "")
                if key:
                    now = time.monotonic()
                    previous = self._repeats.get(key)
                    if previous:
                        last, count = previous
                        self._repeats[key] = (last, count + 1)
                        if now - last < self.repeat_interval:
                            return
                        self._append(self._summary(key, count + 1))
                        self._repeats[key] = (now, 0)
                        self._condition.notify()
                        return
                    if len(self._repeats) >= 128:
                        oldest = next(iter(self._repeats))
                        _, count = self._repeats.pop(oldest)
                        if count:
                            self._append(self._summary(oldest, count))
                    self._repeats[key] = (now, 0)
                data = self._encode(self.format(record))
                if record.levelno >= logging.CRITICAL:
                    # 等待已出队的批次，保证致命事件返回前更早的记录也已刷新。
                    while self._writing:
                        self._condition.wait()
                    if self._failure:
                        return
                    batch = list(self._queue) + [data]
                    self._queue.clear()
                    self._write(batch)
                else:
                    self._append(data)
                    self._condition.notify()
        except Exception as exc:
            self._fail(exc)

    def recover(self, key: str) -> None:
        with self._condition:
            previous = self._repeats.pop(key, None)
            if previous and previous[1] and not self._closing and not self._failure:
                self._append(self._summary(key, previous[1]))
                self._condition.notify()

    def _fail(self, exc) -> None:
        # 不再调用 logging / Qt，防止错误处理递归。
        with self._condition:
            if not self._failure:
                self._failure = str(exc)
            self._queue.clear()

    def take_failure(self) -> str:
        with self._condition:
            if self._failure_reported or not self._failure:
                return ""
            self._failure_reported = True
            return self._failure

    def _write(self, batch) -> None:
        try:
            with self._disk_lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                current = self.directory / "runtime.log"
                old = self.directory / "runtime.log.1"
                # 收紧曾由其他版本留下的文件，始终保持两个文件的总上限。
                for path in (current, old):
                    if path.exists() and path.stat().st_size > self.file_bytes:
                        with path.open("rb") as fp:
                            fp.seek(-self.file_bytes, 2)
                            tail = fp.read().split(b"\n", 1)
                        path.write_bytes(tail[1] if len(tail) == 2 else b"")
                for data in batch:
                    size = current.stat().st_size if current.exists() else 0
                    if size + len(data) > self.file_bytes:
                        old.unlink(missing_ok=True)
                        current.replace(old)
                    # 每批关闭句柄：刷新落到 OS，恢复默认不会残留文件占用。
                    with current.open("ab") as fp:
                        fp.write(data)
        except Exception as exc:
            self._fail(exc)

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._queue and not self._closing:
                    self._condition.wait(1.0)
                batch = list(self._queue)
                self._queue.clear()
                if self._dropped:
                    batch.append(self._summary("诊断队列溢出，丢弃旧记录", self._dropped))
                    self._dropped = 0
                closing = self._closing
                self._writing = bool(batch)
            if batch:
                self._write(batch)
                with self._condition:
                    self._writing = False
                    self._condition.notify_all()
            if closing:
                return

    def close(self) -> None:
        with self._condition:
            if not self._closing:
                for key, (_, count) in self._repeats.items():
                    if count and not self._failure:
                        self._append(self._summary(key, count))
                self._repeats.clear()
                self._closing = True
                self._condition.notify()
        self._thread.join()
        super().close()


def recover(key: str) -> None:
    if _handler is not None:
        _handler.recover(key)


def take_failure() -> str:
    return _handler.take_failure() if _handler is not None else ""


def initialize(directory: Path) -> None:
    global _handler, _old_hooks
    if _handler is not None:
        return
    _handler = DiagnosticHandler(directory)
    logger.setLevel(logging.INFO)
    logger.addHandler(_handler)
    _old_hooks = (sys.excepthook, threading.excepthook)

    def main_exception(kind, value, tb):
        location = f"{tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}" if tb else ""
        event(logging.CRITICAL, "未捕获异常", key=f"python.main.{kind.__name__}.{location}",
              exc_info=(kind, value, tb))

    def thread_exception(args):
        event(logging.CRITICAL, "后台线程未捕获异常",
              key=f"python.thread.{args.exc_type.__name__}",
              exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = main_exception
    threading.excepthook = thread_exception


def shutdown() -> None:
    global _handler, _old_hooks
    if _handler is not None:
        logger.removeHandler(_handler)
        _handler.close()
        _handler = None
    if _old_hooks is not None:
        sys.excepthook, threading.excepthook = _old_hooks
        _old_hooks = None
