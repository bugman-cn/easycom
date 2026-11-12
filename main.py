"""easycom 串口调试工具入口。"""
from __future__ import annotations

import sys
import logging
import hashlib
from pathlib import Path

from PySide6.QtCore import QTimer, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from config.version import APP_NAME
from utils import diagnostics
import ctypes


def _message_handler(msg_type: QtMsgType, context, msg: str) -> None:
    """过滤无害的 Qt 字体引擎告警。"""
    # 屏蔽 OpenType 缺失告警
    if "OpenType support missing" in msg or "script 9" in msg or "iCCP" in msg:
        return
    levels = {QtMsgType.QtWarningMsg: logging.WARNING,
              QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    if msg_type in levels:
        location = f"{context.file or ''}:{context.line}" if context else ""
        fingerprint = hashlib.sha256(msg.encode("utf-8", errors="replace")).hexdigest()[:12]
        key = "" if msg_type == QtMsgType.QtFatalMsg else f"qt.{msg_type.value}.{fingerprint}"
        diagnostics.event(levels[msg_type], f"Qt {location}: {msg}", key=key)


def _set_windows_english_keyboard_layout_once() -> None:
    """Windows 下默认切到英文键盘布局，仅在启动时执行一次。"""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        # 00000409: English (United States)
        hkl = user32.LoadKeyboardLayoutW("00000409", 1)
        if hkl:
            user32.ActivateKeyboardLayout(hkl, 0)
    except Exception:
        pass


def _run_app() -> int:
    from config.app_config import config, rx_color_hilight
    from gui.main_window import MainWindow
    from utils.font_manager import load_bundled_fonts
    from utils.theme import apply_theme
    # Windows 任务栏图标依赖 AppUserModelID，需在 QApplication 前设置
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        except Exception:
            pass
    _set_windows_english_keyboard_layout_once()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # 设置窗口图标
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    icon_path = base / "icon" / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    # 加载内置等宽字体（QApplication 构造后、任何字体使用前）
    load_bundled_fonts()
    win = MainWindow()
    # 加载上次保存的主题（默认 light，内置字体注入到 stylesheet）
    theme = config.theme
    apply_theme(app, theme, win)
    # 加载时间戳颜色（根据主题选择对应颜色）
    win.receive_panel.set_ts_color(config.ts_color_dark if theme == "dark" else config.ts_color_light)
    # 加载编码
    win.receive_panel.set_encoding(config.encoding)
    # 加载缓冲区大小
    win.receive_panel.set_buffer_size_mb(config.buffer_size_mb)
    # 加载时间戳开关状态
    if config.ts_enabled:
        win.receive_panel.ts_chk.setChecked(True)
    # 加载追加换行开关状态
    if config.send_crlf:
        win.send_panel.crlf_chk.setChecked(True)
    # 加载 HEX 发送开关状态
    if config.send_hex:
        win.send_panel.hex_chk.setChecked(True)
    # 加载发送回显开关状态
    if config.tx_echo_enabled:
        win.config_panel.tx_echo_chk.setChecked(True)
    # 加载接收文本颜色高亮配置
    win.receive_panel.set_rx_color_hilights(rx_color_hilight.items)
    # 同步主题菜单文本标记
    win._sync_theme_menu_marker(theme)
    # 同步编码菜单文本标记
    enc = config.encoding
    win._sync_encoding_menu_marker(enc)
    # QPlainTextEdit/QTextEdit 需显式字体（stylesheet 不控制富文本引擎）
    win._apply_display_fonts(config.font_size)
    win.show()
    def check_diagnostics() -> None:
        error = diagnostics.take_failure()
        if error:
            QMessageBox.warning(win, "诊断日志不可用", f"无法写入诊断日志，串口仍可使用。\n{error}")

    diagnostic_timer = QTimer(win)
    diagnostic_timer.setInterval(1000)
    diagnostic_timer.timeout.connect(check_diagnostics)
    diagnostic_timer.start()
    QTimer.singleShot(0, win.restore_ext_cmd_state)
    if config.window_maximized:
        win.set_window_maximized(True)
    return app.exec()


def main() -> int:
    from config.version import APP_VERSION
    from PySide6 import __version__ as qt_version
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    diagnostics.initialize(root / "auto-gen" / "diagnostics")
    previous_handler = qInstallMessageHandler(_message_handler)
    diagnostics.event(logging.INFO, f"启动 V{APP_VERSION}; Python={sys.version.split()[0]}; PySide6={qt_version}; frozen={getattr(sys, 'frozen', False)}")
    try:
        result = _run_app()
        diagnostics.event(logging.INFO, f"正常退出 code={result}")
        return result
    except Exception:
        diagnostics.event(logging.CRITICAL, "启动或运行失败", exc_info=True)
        return 1
    finally:
        qInstallMessageHandler(previous_handler)
        diagnostics.shutdown()


if __name__ == "__main__":
    sys.exit(main())
