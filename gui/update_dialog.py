"""Manual update check and download. Network and ZIP work run off the UI thread."""
from __future__ import annotations

import queue
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QProgressBar, QPushButton, QPlainTextEdit

from config.layout import UPDATE_DIALOG_HEIGHT, UPDATE_DIALOG_WIDTH
from config.version import APP_VERSION
from core import updates, update_install
from gui.rounded_scroll_bar import RoundedScrollBar
from gui.themed_dialog import ThemedDialog


class UpdateDialog(ThemedDialog):
    def __init__(self, window):
        super().__init__(window, "检查更新")
        self.window = window
        self.build = None
        self.release = None
        self.job = None
        self.busy = False
        self.thread = None
        self.installing = False
        self.last_check = float("-inf")
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(lambda: self.action.setEnabled(not self.busy))
        self.cancel = threading.Event()
        self.messages = queue.Queue()
        self.setFixedSize(UPDATE_DIALOG_WIDTH, UPDATE_DIALOG_HEIGHT)
        self.content_layout.addWidget(QLabel(f"当前版本  V{APP_VERSION}"))
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.content_layout.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setVerticalScrollBar(RoundedScrollBar(self.notes))
        self.notes.setReadOnly(True)
        self.notes.setFont(window.font())
        self.notes.setPlaceholderText("暂无发布说明")
        self.content_layout.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.content_layout.addWidget(self.progress)
        self.action = QPushButton()
        self.action.clicked.connect(self._act)
        buttons = QHBoxLayout()
        buttons.addWidget(self.action)
        self.later = QPushButton("稍后")
        self.later.clicked.connect(self.reject)
        buttons.addWidget(self.later)
        self.content_layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        self.finished.connect(self._dismiss)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.progress.hide()

    def open_check(self):
        self.show()
        self.raise_()
        self.activateWindow()
        if not self.busy and self.job is None and time.monotonic() - self.last_check >= 60:
            self._check()

    def _start(self, fn):
        self.busy = True
        self.cancel.clear()
        self.action.setEnabled(False)
        self.later.setText("取消")
        self.progress.setRange(0, 0)
        self.progress.show()
        self.timer.start()

        def work():
            try:
                result = fn()
                self.messages.put(("done", result))
            except Exception as exc:
                self.messages.put(("error", exc))

        self.thread = threading.Thread(target=work, name="easycom-update")
        self.thread.start()

    def _check(self):
        if self.retry_timer.isActive():
            return
        self.release = None
        self.status.setText("正在查询 GitHub 正式版…")
        self.action.setText("检查更新")

        def check():
            build = updates.Build.current()
            return build, updates.latest(build)

        self._start(check)

    def _act(self):
        if self.busy:
            return
        if self.job is not None:
            if self.window.update_blocked():
                self.status.setText("请先关闭串口、停止定时发送和自动重连，再更新并重启。")
                return
            self.installing = True
            self.window.close()
        elif self.release and self.release.url:
            self._download()
        else:
            self._check()

    def _download(self):
        self.status.setText("正在下载并校验更新…")
        build, release = self.build, self.release

        def check_cancel():
            if self.cancel.is_set():
                raise InterruptedError("下载已取消。")

        def download():
            job = update_install.new_job(Path(sys.executable).resolve().parent)
            last = -1

            def progress(value):
                nonlocal last
                if value != last:
                    self.messages.put(("progress", value))
                    last = value

            try:
                updates.prepare(build, release, job, progress, check_cancel)
                check_cancel()
                return job
            except Exception:
                shutil.rmtree(job, ignore_errors=True)
                raise

        self._start(download)

    def _poll(self):
        while not self.messages.empty():
            kind, result = self.messages.get_nowait()
            if kind == "progress":
                self.progress.setRange(0, 100)
                self.progress.setValue(result)
                if result == 100:
                    self.status.setText("正在校验并解压更新包…")
                continue
            self.timer.stop()
            self.busy = False
            self.later.setText("稍后")
            self.progress.hide()
            self.action.setEnabled(True)
            if isinstance(result, Path):
                self.job = result
                if self.cancel.is_set():
                    self._discard()
                    self.action.setText("下载更新")
                else:
                    self.status.setText(f"V{self.release.version} 已通过校验，可以更新并重启。")
                    self.action.setText("更新并重启")
            elif kind == "error":
                self.last_check = float("-inf")
                self.status.setText(str(result))
                if isinstance(result, updates.RateLimited):
                    self.action.setEnabled(False)
                    ms = max(1, min(2_147_483_647, int((result.until - time.time()) * 1000) + 1))
                    self.retry_timer.start(ms)
                self.action.setText("重试下载" if self.release and self.release.url else "重新检查")
            else:
                self.build, self.release = result
                self.last_check = time.monotonic()
                newer = updates.version_parts(self.release.version) > updates.version_parts(APP_VERSION)
                if newer:
                    msg = f"发现新版本 V{self.release.version}"
                    if self.release.url:
                        msg += f"（{self.release.size / (1024 * 1024):.1f} MB）"
                    else:
                        msg += "。当前从源码运行，请通过 Git 更新。"
                else:
                    msg = "当前已是最新版本。"
                self.status.setText(msg)
                self.notes.setPlainText(self.release.notes)
                self.action.setText("下载更新" if self.release.url else "检查更新")

    def _discard(self):
        if self.job is not None:
            shutil.rmtree(self.job, ignore_errors=True)
            self.job = None

    def _dismiss(self):
        self.cancel.set()

    def shutdown(self):
        self.cancel.set()
        if self.thread and self.busy:
            # The Qt event loop is closing. Finish cleanup even if the last
            # download result has been queued but not yet delivered to the dialog.
            thread = self.thread
            messages = self.messages
            def finish():
                thread.join()
                while not messages.empty():
                    _, result = messages.get_nowait()
                    if isinstance(result, Path):
                        shutil.rmtree(result, ignore_errors=True)
            self.timer.stop()
            threading.Thread(target=finish, name="easycom-update-cleanup").start()
        if not self.installing:
            self._discard()

    def handoff(self):
        update_install.handoff(Path(sys.executable).resolve().parent, self.job,
                              self.build.mode, self.release.version)
