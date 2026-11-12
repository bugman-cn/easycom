"""SerialWorker：后台线程处理串口收发，通过 Qt Signal 上报主线程。

串口读取在 QThread.run() 中完成，数据进入队列，由 UI 按预算消费。
异步打开线程只提交结果，UI 验证请求编号后启动读取。
"""
from __future__ import annotations

from collections import deque
from threading import Lock, RLock, Thread
import time
from typing import Optional

import serial
import serial.tools.list_ports
from PySide6.QtCore import QThread, Signal, Slot, Qt
import logging
from config.runtime import SERIAL_READ_TIMEOUT_MS
from utils.diagnostics import event, recover


class SerialWorker(QThread):
    _open_completed = Signal(int)
    _state_report = Signal(int, bool, str)
    received_ready = Signal()
    sent = Signal(int)
    state_changed = Signal(bool, str)  # (is_open, message)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._serial: Optional[serial.Serial] = None
        self._running = False
        self._lifecycle_lock = RLock()
        self._io_lock = RLock()
        self._generation = 0
        self._opener = None
        self._pending_open = None
        self._open_completed.connect(self._finish_open, Qt.ConnectionType.QueuedConnection)
        self._state_report.connect(self._deliver_state, Qt.ConnectionType.QueuedConnection)
        self._seconds_per_byte = 0.0
        self._rx_lock = Lock()
        self._rx_chunks: deque[tuple[bytes, float, float]] = deque()
        self._rx_pending_bytes = 0
        self._rx_total_bytes = 0
        self._rx_notify_pending = False

    def _enqueue_received(self, data: bytes, ts_read: float, sec_per_byte: float) -> None:
        notify = False
        with self._rx_lock:
            self._rx_chunks.append((data, ts_read, sec_per_byte))
            self._rx_pending_bytes += len(data)
            self._rx_total_bytes += len(data)
            if not self._rx_notify_pending:
                self._rx_notify_pending = True
                notify = True
        if notify:
            self.received_ready.emit()

    def take_received(self, max_bytes: int) -> tuple[list[tuple[bytes, float, float]], bool]:
        chunks: list[tuple[bytes, float, float]] = []
        taken = 0
        max_bytes = max(1, max_bytes)
        with self._rx_lock:
            while self._rx_chunks and taken < max_bytes:
                data, ts_read, sec_per_byte = self._rx_chunks[0]
                remaining = max_bytes - taken
                if len(data) <= remaining:
                    self._rx_chunks.popleft()
                    chunks.append((data, ts_read, sec_per_byte))
                    taken += len(data)
                    continue
                prefix = data[:remaining]
                suffix = data[remaining:]
                prefix_ts = ts_read - len(suffix) * sec_per_byte
                self._rx_chunks[0] = (suffix, ts_read, sec_per_byte)
                chunks.append((prefix, prefix_ts, sec_per_byte))
                taken += len(prefix)
            self._rx_pending_bytes -= taken
            has_more = bool(self._rx_chunks)
            if not has_more:
                self._rx_notify_pending = False
        return chunks, has_more

    def pending_received_bytes(self) -> int:
        with self._rx_lock:
            return self._rx_pending_bytes

    def total_received_bytes(self) -> int:
        with self._rx_lock:
            return self._rx_total_bytes

    # ---------- 公共接口 ----------
    @staticmethod
    def list_ports() -> list[tuple[str, str]]:
        """返回可用串口列表 [(display_text, device_name), ...]，排除 COM1。"""
        ports = []
        for p in serial.tools.list_ports.comports():
            if p.device == "COM1":
                continue
            desc = p.description or ""
            display = f"{p.device} - {desc}" if desc else p.device
            ports.append((display, p.device))
        return ports

    def is_open(self) -> bool:
        with self._io_lock:
            return self._serial is not None and self._serial.is_open and self._running

    def is_busy(self) -> bool:
        with self._lifecycle_lock:
            return (self.isRunning() or self._pending_open is not None
                    or (self._opener is not None and self._opener.is_alive()))

    @Slot(int, bool, str)
    def _deliver_state(self, generation: int, connected: bool, message: str) -> None:
        if generation == self._generation:
            self.state_changed.emit(connected, message)

    @staticmethod
    def _close_handle(ser) -> None:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                event(logging.ERROR, "关闭串口句柄失败", key="serial.close", exc_info=True)

    def _stop_current_connection(self, timeout_ms: int = 2000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        with self._lifecycle_lock:
            self._generation += 1
            self._running = False
            pending = self._pending_open
            self._pending_open = None
            opener = self._opener
        if pending is not None:
            self._close_handle(pending[1])
        if opener is not None:
            opener.join(max(0.0, deadline - time.monotonic()))
        stopped = not self.isRunning() or self.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        stopped = stopped and (opener is None or not opener.is_alive())
        if not stopped:
            event(logging.ERROR, "等待串口打开/读取线程停止超时", key="serial.stop_timeout")
        else:
            recover("serial.stop_timeout")
        return stopped

    def close(self, timeout_ms: int = 2000) -> bool:
        was_active = self.is_busy() or self.is_open()
        stopped = self._stop_current_connection(timeout_ms)
        self.state_changed.emit(False, "已断开")
        if was_active:
            event(logging.INFO, f"串口关闭，线程已停止={stopped}")
        return stopped

    def _prepare_open(self) -> int | None:
        # 所有连接入口在 UI 线程调用；未结束的请求不能被后一次重连覆盖。
        if self.is_busy():
            return None
        self._generation += 1
        return self._generation

    def _create_connection(self, cfg: dict):
        return serial.Serial(**cfg, timeout=SERIAL_READ_TIMEOUT_MS / 1000)

    def _activate(self, generation: int, ser, cfg: dict) -> bool:
        if generation != self._generation:
            self._close_handle(ser)
            return False
        parity_bits = 0 if cfg["parity"] in ("N", "n") else 1
        bits = 1.0 + cfg["bytesize"] + cfg["stopbits"] + parity_bits
        with self._io_lock:
            self._serial = ser
            self._seconds_per_byte = bits / cfg["baudrate"] if cfg["baudrate"] > 0 else 0.0
            self._running = True
        self.start()
        recover("serial.open")
        recover("serial.read")
        recover("serial.send")
        event(logging.INFO, f"串口连接成功 {cfg}")
        self.state_changed.emit(True, f"{cfg['port']} @ {cfg['baudrate']} 已连接")
        return True

    def open(self, port: str, baudrate: int, bytesize: int = 8,
             stopbits: float = 1, parity: str = "N") -> bool:
        if self.is_open():
            return True
        generation = self._prepare_open()
        if generation is None:
            return False
        cfg = dict(port=port, baudrate=baudrate, bytesize=bytesize, stopbits=stopbits, parity=parity)
        try:
            ser = self._create_connection(cfg)
        except Exception as exc:
            event(logging.ERROR, f"打开串口失败 {cfg}", key="serial.open", exc_info=True)
            self.state_changed.emit(False, f"打开失败: {exc}")
            return False
        return self._activate(generation, ser, cfg)

    def open_async(self, port: str, baudrate: int, bytesize: int = 8,
                   stopbits: float = 1, parity: str = "N") -> bool:
        if self.is_open():
            return False
        generation = self._prepare_open()
        if generation is None:
            return False
        cfg = dict(port=port, baudrate=baudrate, bytesize=bytesize, stopbits=stopbits, parity=parity)

        def do_open() -> None:
            ser, error = None, ""
            try:
                ser = self._create_connection(cfg)
            except Exception as exc:
                error = str(exc)
                event(logging.ERROR, f"打开串口失败 {cfg}", key="serial.open", exc_info=True)
            with self._lifecycle_lock:
                cancelled = generation != self._generation
                if not cancelled:
                    self._pending_open = (generation, ser, cfg, error)
            if cancelled:
                self._close_handle(ser)
                return
            self._open_completed.emit(generation)

        self._opener = Thread(target=do_open, name="uart-serial-open", daemon=True)
        self._opener.start()
        return True

    @Slot(int)
    def _finish_open(self, generation: int) -> None:
        with self._lifecycle_lock:
            pending = self._pending_open
            if pending is None or generation != pending[0] or generation != self._generation:
                return
            self._pending_open = None
        _, ser, cfg, error = pending
        if error:
            self.state_changed.emit(False, f"打开失败: {error}")
        else:
            self._activate(generation, ser, cfg)

    def send(self, data: bytes) -> int:
        with self._io_lock:
            if not self.is_open():
                return 0
            try:
                n = self._serial.write(data)
            except Exception as exc:
                self._running = False
                event(logging.ERROR, "串口发送失败", key="serial.send", exc_info=True)
                self._state_report.emit(self._generation, False, f"发送失败: {exc}")
                return 0
        self.sent.emit(n)
        return n

    def _set_line(self, name: str, level: bool) -> None:
        with self._io_lock:
            if self.is_open():
                try:
                    setattr(self._serial, name, level)
                except Exception:
                    event(logging.WARNING, f"设置 {name} 失败", key=f"serial.{name}", exc_info=True)

    def set_dtr(self, level: bool) -> None:
        self._set_line("dtr", level)

    def set_rts(self, level: bool) -> None:
        self._set_line("rts", level)

    def run(self) -> None:
        ser = self._serial
        generation = self._generation
        try:
            while self._running:
                # 活动句柄由本线程在 finally 中关闭；等首字节时让发送线程继续使用串口。
                data = ser.read(ser.in_waiting or 1)
                if data:
                    self._enqueue_received(data, time.time(), self._seconds_per_byte)
        except Exception as exc:
            if self._running:
                event(logging.ERROR, "串口读取异常", key="serial.read", exc_info=True)
                self._state_report.emit(generation, False, f"读取异常: {exc}")
        finally:
            with self._io_lock:
                self._running = False
                self._close_handle(ser)
                if self._serial is ser:
                    self._serial = None
                self._seconds_per_byte = 0.0
