"""主窗口：菜单栏 + 顶部配置工具栏 + 左接收/右发送 + 状态栏。

布局：
- 自定义标题栏（带 macOS 风格三色按钮）
- 菜单栏（主题、关于）
- 顶部：串口配置横向工具栏
- 中间：左侧大接收区 + 右侧发送区（左右并排，QHBoxLayout）
- 底部：状态栏（连接状态 | port@baud | DTR/RTS | RX/TX）
"""
from __future__ import annotations

import sys
import ctypes
import time
import logging
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QEvent, QProcess, QPropertyAnimation, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config.app_config import config, rx_color_hilight, get_config_dir, get_runtime_root_dir
from config.runtime import (
    ANIMATION_ENABLED,
    AUTO_RECONNECT_INTERVAL_MS,
    LOG_HEALTH_CHECK_INTERVAL_MS,
    PANEL_ANIMATION_DURATION_MS,
    RX_DRAIN_BUDGET_MS,
    RX_DRAIN_INTERVAL_MS,
    RX_DRAIN_MAX_BYTES,
    RX_BACKLOG_WARNING_BYTES,
    SNAP_HOVER_INTERVAL_MS,
    SPLITTER_SAVE_DEBOUNCE_MS,
)
from config.layout import (
    ABOUT_DIALOG_MIN_WIDTH,
    BUFFER_DIALOG_MIN_WIDTH,
    FIRST_USE_DIALOG_MIN_WIDTH,
    RESET_DIALOG_MIN_WIDTH,
    SERIAL_ERROR_DIALOG_MIN_WIDTH,
    WINDOW_DEFAULT_HEIGHT,
    WINDOW_DEFAULT_WIDTH,
    WINDOW_MIN_WIDTH,
    WINDOW_RESIZE_BORDER_PX,
    WINDOW_SNAP_EDGE_PX,
    WINDOW_SNAP_SIDE_EDGE_PX,
    SNAP_PREVIEW_INSET_PX,
    SNAP_PREVIEW_RADIUS_PX,
)
from config.theme import FIXED_COLORS, THEME_COLORS
from config.version import APP_NAME, APP_VERSION
from core.serial_worker import SerialWorker
from gui.receive_panel import ReceivePanel
from gui.send_panel import SendPanel
from gui.serial_config_panel import SerialConfigPanel
from gui.status_bar import StatusBar
from gui.title_bar import TitleBar
from utils.font_manager import DISPLAY_FAMILY, choose_font_size, get_display_font
from utils.theme import apply_theme
from gui.color_picker import ColorPickerDialog
from gui.ext_cmd_panel import ExtCmdPanel
from gui.auto_save_dialog import AutoSaveDialog
from gui.themed_dialog import ThemedDialog
from gui.keyword_config_dialog import KeywordConfigDialog
from utils.file_logger import ensure_dir, open_auto_log_file
from utils import diagnostics
import shutil

from gui.terminal_receive_panel import TerminalReceivePanel

class _SnapPreview(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._color = QColor(FIXED_COLORS["snap_preview"])
        self.hide()

    def set_theme(self, mode: str) -> None:
        self._color = QColor(THEME_COLORS[mode]["highlight"])
        self._color.setAlpha(210)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            self.rect().adjusted(
                SNAP_PREVIEW_INSET_PX,
                SNAP_PREVIEW_INSET_PX,
                -SNAP_PREVIEW_INSET_PX,
                -SNAP_PREVIEW_INSET_PX,
            ),
            SNAP_PREVIEW_RADIUS_PX,
            SNAP_PREVIEW_RADIUS_PX,
        )


class MainWindow(QMainWindow):
    DEFAULT_WINDOW_WIDTH = WINDOW_DEFAULT_WIDTH
    DEFAULT_WINDOW_HEIGHT = WINDOW_DEFAULT_HEIGHT
    MIN_WINDOW_WIDTH = WINDOW_MIN_WIDTH
    WINDOW_DEBUG = False

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} V{APP_VERSION}")
        w, h = self._resolve_startup_normal_size()
        self.resize(w, h)
        self._window_maximized = False
        self._vertical_maximized = False
        self._normal_geometry = QRect(0, 0, w, h)
        self._window_debug(
            f"startup config: saved={config.window_width}x{config.window_height}, "
            f"maximized={config.window_maximized}, apply={w}x{h}"
        )
        self._resize_border_px = WINDOW_RESIZE_BORDER_PX
        self._snap_edge_px = WINDOW_SNAP_EDGE_PX
        self._snap_side_edge_px = WINDOW_SNAP_SIDE_EDGE_PX
        self._snap_mode: str | None = None
        self._snap_preview_mode: str | None = None
        self._snap_pending_mode: str | None = None
        self._snap_pending_target: QRect | None = None
        self._snap_hover_timer = QTimer(self)
        self._snap_hover_timer.setSingleShot(True)
        self._snap_hover_timer.setInterval(SNAP_HOVER_INTERVAL_MS)
        self._snap_hover_timer.timeout.connect(self._activate_snap_preview)
        self._snap_preview = _SnapPreview()
        self._snap_preview.set_theme(config.theme)
        self._rx_drain_timer = QTimer(self)
        self._rx_drain_timer.setSingleShot(True)
        self._rx_drain_timer.timeout.connect(self._drain_received)
        # 无边框窗口，使用自定义 TitleBar 完全跟随主题
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self._enable_native_resize_frame()
        self._reset_defaults = False  # ponytail: 恢复默认时跳过关闭保存，避免覆写已删除的 gen/
        self._restart_after_reset = False
        self._set_keep_awake(config.keep_awake)

        # 核心组件
        self.worker = SerialWorker(self)
        self._closing = False
        self._update_dialog = None
        self._pending_reconfigure = None
        self._lifecycle_timer = QTimer(self)
        self._lifecycle_timer.setInterval(100)
        self._lifecycle_timer.timeout.connect(self._finish_lifecycle_transition)
        self._port_info = ""
        self._auto_session_open = False
        self._auto_session_path = ""
        self._last_auto_session_path = ""
        self._auto_log_writer = None
        self._auto_log_health_timer = QTimer(self)
        self._auto_log_health_timer.setInterval(LOG_HEALTH_CHECK_INTERVAL_MS)
        self._auto_log_health_timer.timeout.connect(self._check_auto_log_writer)
        self._reconfig_in_progress = False
        self._last_connected = False

        # 自动重连
        self._auto_reconnect_timer = QTimer(self)
        self._auto_reconnect_timer.setInterval(AUTO_RECONNECT_INTERVAL_MS)
        self._auto_reconnect_timer.timeout.connect(self._on_auto_reconnect_tick)
        self._auto_reconnect_attempts = 0
        self._auto_reconnect_scanning = True
        self._auto_reconnect_scan_ticks = 0

        # 面板
        self.config_panel = SerialConfigPanel()
        self.receive_panel = ReceivePanel()
        self.send_panel = SendPanel()
        self.ext_cmd_panel = ExtCmdPanel()
        self.terminal_receive_panel: TerminalReceivePanel | None = None
        self._ext_cmd_range_ratio = max(
            0.0, min(1.0, float(config.get("ext_cmd_splitter_range_ratio", 1.0)))
        )
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.setInterval(SPLITTER_SAVE_DEBOUNCE_MS)
        self._splitter_save_timer.timeout.connect(self._save_splitter_ratio)
        self.status_bar = StatusBar()
        self.status_bar.set_buffer_mb(config.buffer_size_mb)
        self.status_bar.path_clicked.connect(self._open_local_path_in_explorer)

        # 中央容器：自定义标题栏（含菜单按钮）+ 内容
        central = QWidget()
        central.setObjectName("mainWindowRoot")
        self._main_root_layout = QVBoxLayout(central)
        self._main_root_layout.setContentsMargins(1, 1, 1, 1)
        self._main_root_layout.setSpacing(0)

        self._main_surface = QFrame()
        self._main_surface.setObjectName("mainWindowSurface")
        v = QVBoxLayout(self._main_surface)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # 自定义标题栏 + 内嵌菜单
        self.title_bar = TitleBar(self, self.windowTitle())
        self._build_menus()
        v.addWidget(self.title_bar)

        # 内容区
        content = QWidget()
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.config_panel)

        # 接收区在上、发送区在下，检索栏居中
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        # 接收区 QStackedWidget：普通/终端模式仅替换接收区
        self._body_stack = QStackedWidget()
        self._body_stack.addWidget(self.receive_panel)                # index 0: 普通模式
        self._body_stack.setCurrentIndex(0)

        body.addWidget(self._body_stack, stretch=1)
        body.addWidget(self.send_panel)

        # 扩展命令面板并排右侧，QSplitter 可拖拽调整宽度
        body_container = QWidget()
        body_container.setLayout(body)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(0)           # 默认隐藏拖动条
        self._splitter.addWidget(body_container)
        self._splitter.addWidget(self.ext_cmd_panel)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)     # 禁止折叠到0，显隐由代码控制
        self._splitter.setSizes([1000, 0])          # 初始隐藏面板

        cv.addWidget(self._splitter, stretch=1)
        v.addWidget(content, stretch=1)
        v.addWidget(self.status_bar)

        self._main_root_layout.addWidget(self._main_surface)
        self.setCentralWidget(central)
        self._update_main_window_surface()
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self._splitter.installEventFilter(self)
        self._connect_signals()
        self._sync_auto_save_status_text(False)
        # 恢复上次扩展命令启用状态
        self._restore_ext_cmd_enabled = bool(config.get("ext_cmd_enabled", False))
        self.receive_panel.ext_cmd_btn.blockSignals(True)
        self.receive_panel.ext_cmd_btn.setChecked(self._restore_ext_cmd_enabled)
        self.receive_panel.ext_cmd_btn.blockSignals(False)
        if self._restore_ext_cmd_enabled:
            self.receive_panel.ext_cmd_btn.setToolTip("扩展命令: 启用")
            self.receive_panel.ext_cmd_btn._refresh_style()
        self.ext_cmd_panel.hide()
        self._apply_min_height()
        if not config.window_maximized:
            self._center_on_startup()
        QTimer.singleShot(0, self._show_first_use_paths_notice_if_needed)
        # 恢复上次串口模式（延迟到 show 之后，确保布局就绪）
        if config.serial_mode == "terminal":
            QTimer.singleShot(0, lambda: self._switch_serial_mode("terminal"))

    def _ensure_terminal_panel(self) -> TerminalReceivePanel:
        panel = self.terminal_receive_panel
        if panel is not None:
            return panel
        panel = TerminalReceivePanel()
        panel.set_connected(self.worker.is_open())
        panel.hide()
        self.terminal_receive_panel = panel
        self._body_stack.addWidget(panel)
        panel.ensurePolished()
        panel.ext_cmd_toggled.connect(self._on_ext_cmd_toggled)
        panel.serial_mode_toggled.connect(self._on_serial_mode_toggled)
        panel.send_requested.connect(self._on_send)
        panel.cleared.connect(self.status_bar.reset_counters)
        return panel

    def _screen_available_geometry(self) -> QRect:
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, max(320, self.width()), max(240, self.height()))
        return screen.availableGeometry()

    def is_window_maximized(self) -> bool:
        return self._window_maximized

    def is_window_fitted(self) -> bool:
        return self._window_maximized or self._vertical_maximized or self._snap_mode is not None

    def _update_main_window_surface(self) -> None:
        """贴边时去掉外侧空隙，保留主窗口的 1 像素边框。"""
        self._main_root_layout.setContentsMargins(*(0 if self.is_window_fitted() else 1,) * 4)

    def restore_window_for_drag(self, global_pos, drag_pos):
        if not self.is_window_fitted():
            return drag_pos
        target = QRect(self._normal_geometry)
        if target.width() <= 0 or target.height() <= 0:
            target = QRect(self.x(), self.y(), self.width(), self.height())
        current = self.geometry()
        current_w = max(1, current.width())
        current_h = max(1, current.height())
        anchor_x = max(0.0, min(1.0, drag_pos.x() / current_w))
        anchor_y = max(0.0, min(1.0, drag_pos.y() / current_h))
        target_x = global_pos.x() - round(target.width() * anchor_x)
        target_y = global_pos.y() - round(target.height() * anchor_y)
        target.moveTo(target_x, target_y)
        self.setGeometry(target)
        self._window_maximized = False
        self._vertical_maximized = False
        self._snap_mode = None
        self.title_bar.max_btn.setToolTip("最大化")
        self._update_main_window_surface()
        self._cancel_snap_preview()
        return global_pos - self.pos()

    def set_window_maximized(self, maximized: bool) -> None:
        if maximized == self._window_maximized:
            return
        if maximized:
            g = self.geometry()
            if self._snap_mode is None and g.width() > 0 and g.height() > 0:
                self._normal_geometry = QRect(g)
            target = self._screen_available_geometry()
            self.setGeometry(target)
            self._window_maximized = True
            self._vertical_maximized = False
            self._snap_mode = "max"
            self.title_bar.max_btn.setToolTip("还原")
            self._update_main_window_surface()
            self._window_debug(
                f"manual-max on: normal=({self._rect_text(self._normal_geometry)}), "
                f"target=({self._rect_text(target)})"
            )
            return

        target = QRect(self._normal_geometry)
        screen_rect = self._screen_available_geometry()
        if target.width() <= 0 or target.height() <= 0:
            target = QRect(
                screen_rect.x(),
                screen_rect.y(),
                min(self.DEFAULT_WINDOW_WIDTH, screen_rect.width()),
                min(self.DEFAULT_WINDOW_HEIGHT, screen_rect.height()),
            )
        w = min(max(320, target.width()), screen_rect.width())
        h = min(max(240, target.height()), screen_rect.height())
        max_x = screen_rect.x() + screen_rect.width() - w
        max_y = screen_rect.y() + screen_rect.height() - h
        x = min(max(screen_rect.x(), target.x()), max_x)
        y = min(max(screen_rect.y(), target.y()), max_y)
        self.setGeometry(x, y, w, h)
        self._window_maximized = False
        self._vertical_maximized = False
        self._snap_mode = None
        self.title_bar.max_btn.setToolTip("最大化")
        self._update_main_window_surface()
        self._window_debug(f"manual-max off: restore=({x}, {y}, {w}, {h})")

    def toggle_window_maximized(self) -> None:
        self.set_window_maximized(not self._window_maximized)

    def toggle_vertical_maximized(self) -> None:
        if self._vertical_maximized:
            target = QRect(self._normal_geometry)
            screen_rect = self._screen_available_geometry()
            if target.width() <= 0 or target.height() <= 0:
                target = QRect(
                    screen_rect.x(),
                    screen_rect.y(),
                    min(self.DEFAULT_WINDOW_WIDTH, screen_rect.width()),
                    min(self.DEFAULT_WINDOW_HEIGHT, screen_rect.height()),
                )
            w = min(max(320, target.width()), screen_rect.width())
            h = min(max(240, target.height()), screen_rect.height())
            max_x = screen_rect.x() + screen_rect.width() - w
            max_y = screen_rect.y() + screen_rect.height() - h
            x = min(max(screen_rect.x(), target.x()), max_x)
            y = min(max(screen_rect.y(), target.y()), max_y)
            self.setGeometry(x, y, w, h)
            self._vertical_maximized = False
            self._snap_mode = None
            self.title_bar.max_btn.setToolTip("最大化")
            self._update_main_window_surface()
            return

        if not self.is_window_fitted():
            g = self.geometry()
            if g.width() > 0 and g.height() > 0:
                self._normal_geometry = QRect(g)
        area = self._screen_available_geometry()
        w = min(max(320, self.width()), area.width())
        x = min(max(area.x(), self.x()), area.x() + area.width() - w)
        self.setGeometry(x, area.y(), w, area.height())
        self._window_maximized = False
        self._vertical_maximized = True
        self._snap_mode = "vertical"
        self.title_bar.max_btn.setToolTip("还原")
        self._update_main_window_surface()

    def _snap_target(self, global_pos) -> tuple[str | None, QRect | None]:
        screen = QGuiApplication.screenAt(global_pos)
        if screen is None:
            return None, None
        area = screen.availableGeometry()
        x, y = global_pos.x(), global_pos.y()
        edge = self._snap_edge_px
        side_edge = self._snap_side_edge_px

        left_edge = x <= area.left() + side_edge
        right_edge = x >= area.right() - side_edge
        top_edge = y <= area.top() + edge
        half_w = area.width() // 2
        half_h = area.height() // 2
        segment_h = area.height() // 3
        top_segment = y < area.top() + segment_h
        bottom_segment = y >= area.top() + segment_h * 2

        if left_edge and top_segment:
            return "top_left", QRect(area.x(), area.y(), half_w, half_h)
        if right_edge and top_segment:
            return "top_right", QRect(area.x() + half_w, area.y(), area.width() - half_w, half_h)
        if left_edge and bottom_segment:
            return "bottom_left", QRect(area.x(), area.y() + half_h, half_w, area.height() - half_h)
        if right_edge and bottom_segment:
            return "bottom_right", QRect(
                area.x() + half_w,
                area.y() + half_h,
                area.width() - half_w,
                area.height() - half_h,
            )
        if y <= area.top() + edge:
            return "max", QRect(area)
        if x <= area.left() + side_edge:
            return "left", QRect(area.x(), area.y(), half_w, area.height())
        if x >= area.right() - side_edge:
            return "right", QRect(area.x() + half_w, area.y(), area.width() - half_w, area.height())
        return None, None

    def update_snap_preview(self, global_pos) -> None:
        mode, target = self._snap_target(global_pos)
        if mode is None or target is None:
            self._cancel_snap_preview()
            return

        if mode == self._snap_preview_mode:
            return
        if mode == self._snap_pending_mode:
            return

        self._snap_hover_timer.stop()
        self._snap_preview.hide()
        self._snap_preview_mode = None
        self._snap_pending_mode = mode
        self._snap_pending_target = QRect(target)
        self._snap_hover_timer.start()

    def _activate_snap_preview(self) -> None:
        mode, target = self._snap_target(QCursor.pos())
        if (
            mode is None
            or target is None
            or mode != self._snap_pending_mode
            or self._snap_pending_target is None
        ):
            self._cancel_snap_preview()
            return
        self._snap_preview_mode = mode
        self._snap_preview.setGeometry(target.adjusted(4, 4, -4, -4))
        self._snap_preview.show()
        self._snap_preview.raise_()

    def _cancel_snap_preview(self) -> None:
        self._snap_hover_timer.stop()
        self._snap_pending_mode = None
        self._snap_pending_target = None
        self._snap_preview_mode = None
        self._snap_preview.hide()

    def finish_window_drag(self) -> None:
        mode, target = self._snap_target(QCursor.pos())
        can_snap = mode is not None and mode == self._snap_preview_mode and target is not None
        self._cancel_snap_preview()
        if not can_snap:
            return
        if self._snap_mode is None and not self._window_maximized:
            g = self.geometry()
            if g.width() > 0 and g.height() > 0:
                self._normal_geometry = QRect(g)
        self.setGeometry(target)
        self._snap_mode = mode
        self._window_maximized = mode == "max"
        self._vertical_maximized = False
        self.title_bar.max_btn.setToolTip("还原")
        self._update_main_window_surface()
        self._window_debug(f"snap {mode}: target=({self._rect_text(target)})")

    def _window_debug(self, msg: str) -> None:
        if self.WINDOW_DEBUG:
            sys.stderr.write(f"[WINDOW] {msg}\n")

    @staticmethod
    def _rect_text(rect) -> str:
        return f"x={rect.x()}, y={rect.y()}, w={rect.width()}, h={rect.height()}"

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self._window_debug(
                "state-changed: "
                f"custom_max={self._window_maximized}, qt_max={self.isMaximized()}, min={self.isMinimized()}, "
                f"geom=({self._rect_text(self.geometry())}), "
                f"normal=({self._rect_text(self.normalGeometry())})"
            )
        super().changeEvent(event)

    def _resolve_startup_normal_size(self) -> tuple[int, int]:
        """计算启动时的还原态窗口尺寸，未配置时按屏幕 80%×70%。"""
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return self.DEFAULT_WINDOW_WIDTH, self.DEFAULT_WINDOW_HEIGHT
        rect = screen.availableGeometry()

        w = config.window_width
        h = config.window_height
        if w <= 0 or h <= 0:
            w = max(320, int(rect.width() * 0.6))
            h = max(240, int(rect.height() * 0.7))
        else:
            w = max(320, w)
            h = max(240, h)

        max_w = max(320, rect.width())
        max_h = max(240, rect.height())
        self._window_debug(
            f"resolve-size input={w}x{h}, screen={max_w}x{max_h}, maximized={config.window_maximized}"
        )

        # 历史版本可能在"最大化关闭"时把还原态尺寸误存为接近全屏，
        # 导致下次从最大化还原时看起来没有变化。
        if config.window_maximized and w >= max_w - 12 and h >= max_h - 12:
            w = min(self.DEFAULT_WINDOW_WIDTH, max_w)
            h = min(self.DEFAULT_WINDOW_HEIGHT, max_h)
            self._window_debug(f"resolve-size fallback to default={w}x{h}")
        else:
            w = min(w, max_w)
            h = min(h, max_h)
            self._window_debug(f"resolve-size clamped={w}x{h}")

        return w, h

    def _apply_min_height(self) -> None:
        """计算包含检索栏时的最小窗口高度，避免切换搜索框时窗口跳动。"""
        self.setMinimumWidth(max(self.MIN_WINDOW_WIDTH, self.config_panel.sizeHint().width()))
        self.receive_panel.search_wrap.setVisible(True)
        min_h = (
            self.title_bar.height()
            + 6 + 4  # content margins
            + self.config_panel.sizeHint().height()
            + self.receive_panel.search_wrap.sizeHint().height()
            + self.send_panel.sizeHint().height()
            + self.status_bar.height()
            + 350
        )
        self.receive_panel.search_wrap.setVisible(False)
        self.setMinimumHeight(min_h)

    def _center_on_startup(self) -> None:
        """启动时将窗口置于当前屏幕可用区域中心。"""
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        rect = screen.availableGeometry()
        x = rect.x() + (rect.width() - self.width()) // 2
        y = rect.y() + (rect.height() - self.height()) // 2
        self.move(max(rect.x(), x), max(rect.y(), y))
        self._window_debug(
            f"center-on-startup move=({self.x()}, {self.y()}), size={self.width()}x{self.height()}, "
            f"screen=({self._rect_text(rect)})"
        )

    def _enable_native_resize_frame(self) -> None:
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
        ctypes.windll.user32.SetWindowLongW(hwnd, -16, style | 0x00040000)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)

    def nativeEvent(self, eventType, message):
        """Windows 下为无边框窗口提供边缘拖拽缩放。"""
        if sys.platform == "win32" and eventType in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            import ctypes

            msg = ctypes.wintypes.MSG.from_address(int(message))
            WM_NCHITTEST = 0x0084
            WM_NCLBUTTONDBLCLK = 0x00A3
            WM_SYSCOMMAND = 0x0112
            SC_MINIMIZE = 0xF020
            SC_RESTORE = 0xF120
            HTLEFT = 10
            HTRIGHT = 11
            HTTOP = 12
            HTTOPLEFT = 13
            HTTOPRIGHT = 14
            HTBOTTOM = 15
            HTBOTTOMLEFT = 16
            HTBOTTOMRIGHT = 17

            if msg.message == WM_SYSCOMMAND:
                command = int(msg.wParam) & 0xFFF0
                if command == SC_MINIMIZE:
                    self.showMinimized()
                    return True, 0
                if command == SC_RESTORE and self.isMinimized():
                    self.showNormal()
                    return True, 0

            if msg.message == WM_NCLBUTTONDBLCLK and int(msg.wParam) == HTTOP:
                self.toggle_vertical_maximized()
                return True, 0

            if msg.message == WM_NCHITTEST and not self._window_maximized:
                # 使用当前鼠标全局坐标，避免 lParam 在大坐标场景下 16-bit 截断带来的误判。
                pos = self.mapFromGlobal(QCursor.pos())
                w = self.width()
                h = self.height()
                b = self._resize_border_px

                # 鼠标位于可交互控件上时，优先保证点击而非缩放命中。
                hit = self.childAt(pos)
                if self._is_interactive_widget(hit):
                    return super().nativeEvent(eventType, message)

                on_left = pos.x() < b
                on_right = pos.x() >= w - b
                on_top = pos.y() < b
                on_bottom = pos.y() >= h - b

                if on_top and on_left:
                    return True, HTTOPLEFT
                if on_top and on_right:
                    return True, HTTOPRIGHT
                if on_bottom and on_left:
                    return True, HTBOTTOMLEFT
                if on_bottom and on_right:
                    return True, HTBOTTOMRIGHT
                if on_left:
                    return True, HTLEFT
                if on_right:
                    return True, HTRIGHT
                if on_top:
                    return True, HTTOP
                if on_bottom:
                    return True, HTBOTTOM

        return super().nativeEvent(eventType, message)

    def _is_interactive_widget(self, widget: QWidget | None) -> bool:
        """判断命中点是否位于需要优先响应点击的控件。"""
        w = widget
        while w is not None and w is not self:
            if (
                w.inherits("QAbstractButton")
                or w.inherits("QAbstractSpinBox")
                or w.inherits("QComboBox")
                or w.inherits("QLineEdit")
                or w.inherits("QTextEdit")
                or w.inherits("QPlainTextEdit")
                or w.inherits("QAbstractItemView")
                or w.inherits("QScrollBar")
                or w.inherits("QSlider")
            ):
                return True
            w = w.parentWidget()
        return False

    def _build_menus(self) -> None:
        """创建菜单并注入标题栏。"""
        # 主题菜单
        theme_menu = QMenu(self)
        self._dark_action = theme_menu.addAction("深色")
        self._light_action = theme_menu.addAction("浅色")
        self._dark_action.triggered.connect(lambda: self._on_menu_theme_changed("dark"))
        self._light_action.triggered.connect(lambda: self._on_menu_theme_changed("light"))
        self._sync_theme_menu_marker(config.theme)
        self.title_bar.add_menu_button("主题", theme_menu)

        # 设置菜单
        settings_menu = QMenu(self)
        auto_save_action = settings_menu.addAction("自动保存日志…")
        auto_save_action.triggered.connect(self._on_auto_save_settings)
        font_action = settings_menu.addAction("字体大小…")
        font_action.triggered.connect(self._on_font_size)
        buf_action = settings_menu.addAction("缓冲区大小…")
        buf_action.triggered.connect(self._on_buffer_size_settings)
        ts_cfg_action = settings_menu.addAction("时间戳配置…")
        ts_cfg_action.triggered.connect(self._on_ts_color_settings)
        rx_color_action = settings_menu.addAction("关键字配置…")
        rx_color_action.triggered.connect(self._on_rx_color_hilight)
        # 接收编码子菜单
        enc_menu = settings_menu.addMenu("接收编码")
        self._utf8_action = enc_menu.addAction("UTF-8")
        self._gbk_action = enc_menu.addAction("GBK")
        self._utf8_action.triggered.connect(lambda: self._on_encoding_menu_changed("UTF-8"))
        self._gbk_action.triggered.connect(lambda: self._on_encoding_menu_changed("GBK"))
        self._sync_encoding_menu_marker(config.encoding)

        # 发送编码子菜单
        tx_enc_menu = settings_menu.addMenu("发送编码")
        self._tx_utf8_action = tx_enc_menu.addAction("UTF-8")
        self._tx_gbk_action = tx_enc_menu.addAction("GBK")
        self._tx_utf8_action.triggered.connect(lambda: self._on_tx_encoding_menu_changed("UTF-8"))
        self._tx_gbk_action.triggered.connect(lambda: self._on_tx_encoding_menu_changed("GBK"))
        self._sync_tx_encoding_menu_marker(config.tx_encoding)

        # 发送换行符子菜单
        le_menu = settings_menu.addMenu("发送换行符")
        self._le_crlf_action = le_menu.addAction("\\r\\n")
        self._le_cr_action = le_menu.addAction("\\r")
        self._le_lf_action = le_menu.addAction("\\n")
        self._le_crlf_action.triggered.connect(lambda: self._on_line_ending_changed("\r\n"))
        self._le_cr_action.triggered.connect(lambda: self._on_line_ending_changed("\r"))
        self._le_lf_action.triggered.connect(lambda: self._on_line_ending_changed("\n"))
        self._sync_line_ending_menu_marker(config.tx_line_ending)

        # 接收区滚动模式子菜单
        scroll_menu = settings_menu.addMenu("接收区滚动")
        self._scroll_manual_action = scroll_menu.addAction("手动模式")
        self._scroll_semi_action = scroll_menu.addAction("半自动模式")
        self._scroll_manual_action.triggered.connect(lambda: self._on_scroll_mode_changed("manual"))
        self._scroll_semi_action.triggered.connect(lambda: self._on_scroll_mode_changed("semi_auto"))
        self._sync_scroll_mode_menu_marker(config.scroll_mode)

        # 屏幕常亮子菜单
        keep_awake_menu = settings_menu.addMenu("屏幕常亮")
        self._keep_awake_off_action = keep_awake_menu.addAction("关闭")
        self._keep_awake_on_action = keep_awake_menu.addAction("开启")
        self._keep_awake_off_action.triggered.connect(lambda: self._on_keep_awake_changed(False))
        self._keep_awake_on_action.triggered.connect(lambda: self._on_keep_awake_changed(True))
        self._sync_keep_awake_menu_marker(config.keep_awake)

        settings_menu.addSeparator()
        reset_action = settings_menu.addAction("恢复默认…")
        reset_action.triggered.connect(self._on_reset_defaults)
        self.title_bar.add_menu_button("设置", settings_menu)

        # 关于菜单
        about_menu = QMenu(self)
        about_action = about_menu.addAction("关于")
        about_action.triggered.connect(self._on_about)
        about_menu.addAction("检查更新…").triggered.connect(self._on_check_update)
        self.title_bar.add_menu_button("关于", about_menu)

    def _on_menu_theme_changed(self, mode: str) -> None:
        self._apply_theme(mode)

    def _sync_theme_menu_marker(self, mode: str) -> None:
        """用清晰文本符号标记当前主题，替代默认勾选指示器。"""
        self._light_action.setText(f"{'●' if mode == 'light' else '○'} 浅色")
        self._dark_action.setText(f"{'●' if mode == 'dark' else '○'} 深色")

    def _sync_encoding_menu_marker(self, encoding: str) -> None:
        """用清晰文本符号标记当前编码，替代默认勾选指示器。"""
        self._utf8_action.setText(f"{'●' if encoding == 'UTF-8' else '○'} UTF-8")
        self._gbk_action.setText(f"{'●' if encoding == 'GBK' else '○'} GBK")

    def _sync_tx_encoding_menu_marker(self, encoding: str) -> None:
        """用清晰文本符号标记当前发送编码。"""
        self._tx_utf8_action.setText(f"{'●' if encoding == 'UTF-8' else '○'} UTF-8")
        self._tx_gbk_action.setText(f"{'●' if encoding == 'GBK' else '○'} GBK")

    def _sync_line_ending_menu_marker(self, mode: str) -> None:
        """用清晰文本符号标记当前换行符。"""
        crlf = "\r\n"
        cr = "\r"
        lf = "\n"
        self._le_crlf_action.setText(f"{'●' if mode == crlf else '○'} \\r\\n")
        self._le_cr_action.setText(f"{'●' if mode == cr else '○'} \\r")
        self._le_lf_action.setText(f"{'●' if mode == lf else '○'} \\n")

    def _sync_scroll_mode_menu_marker(self, mode: str) -> None:
        """用清晰文本符号标记当前滚动模式。"""
        self._scroll_semi_action.setText(f"{'●' if mode == 'semi_auto' else '○'} 半自动模式")
        self._scroll_manual_action.setText(f"{'●' if mode == 'manual' else '○'} 手动模式")

    def _sync_keep_awake_menu_marker(self, enabled: bool) -> None:
        self._keep_awake_on_action.setText(f"{'●' if enabled else '○'} 开启")
        self._keep_awake_off_action.setText(f"{'○' if enabled else '●'} 关闭")

    def _set_keep_awake(self, enabled: bool) -> None:
        """Windows 下阻止系统空闲睡眠和显示器关闭。"""
        if sys.platform != "win32":
            return
        try:
            state = 0x80000000  # ES_CONTINUOUS
            if enabled:
                state |= 0x00000001 | 0x00000002  # ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            ctypes.windll.kernel32.SetThreadExecutionState(state)
        except Exception:
            pass

    def _on_keep_awake_changed(self, enabled: bool) -> None:
        config.keep_awake = enabled
        self._set_keep_awake(enabled)
        self._sync_keep_awake_menu_marker(enabled)

    def _on_scroll_mode_changed(self, mode: str) -> None:
        """接收区滚动模式菜单项被选中。"""
        self.receive_panel.set_scroll_mode(mode)
        self._sync_scroll_mode_menu_marker(mode)

    def _on_line_ending_changed(self, mode: str) -> None:
        """发送换行符菜单项被选中。"""
        config.tx_line_ending = mode
        self._sync_line_ending_menu_marker(mode)
        # 刷新幽灵文本和 🔚 按钮 tooltip
        self.send_panel._update_crlf_ghost()
        self.send_panel._update_crlf_tooltip()
        self.ext_cmd_panel.refresh_line_ending_ui()
        self.send_panel._update_tx_byte_count()

    def _apply_theme(self, mode: str) -> None:
        """统一主题切换入口，应用 QPalette、同步菜单与下拉、保存配置。"""
        if config.theme == mode:
            return
        config.theme = mode
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode, self)
        self.status_bar.refresh_theme_icon()
        self._snap_preview.set_theme(mode)
        # 同步主题菜单标记
        self._sync_theme_menu_marker(mode)
        # 主题切换时更新时间戳有效颜色
        self.receive_panel.set_ts_color(config.ts_color_dark if mode == "dark" else config.ts_color_light)
        # 主题切换时更新检索高亮有效颜色
        self.receive_panel.refresh_search_color()
        if self.receive_panel.ts_chk.isChecked():
            self._rebuild_if_needed()

    def _on_serial_mode_toggled(self, enabled: bool) -> None:
        target_mode = "terminal" if enabled else "normal"
        if config.serial_mode == target_mode:
            return
        self._switch_serial_mode(target_mode)

    def _switch_serial_mode(self, mode: str) -> None:
        src_mode = config.serial_mode
        terminal_panel = self._ensure_terminal_panel() if src_mode == "terminal" or mode == "terminal" else None
        inherit_search_enabled = (
            terminal_panel.is_search_enabled()
            if src_mode == "terminal"
            else self.receive_panel.is_search_enabled()
        )
        inherit_auto_scroll = (
            terminal_panel._auto_scroll_chk.isChecked()
            if src_mode == "terminal"
            else self.receive_panel.auto_scroll_chk.isChecked()
        )
        inherit_ext_cmd = (
            terminal_panel._ext_cmd_btn.isChecked()
            if src_mode == "terminal"
            else self.receive_panel.ext_cmd_btn.isChecked()
        )
        config.serial_mode = mode
        if self._auto_log_writer is not None:
            self.receive_panel.stop_auto_save(close_writer=False)
            if terminal_panel is not None:
                terminal_panel.stop_auto_save(close_writer=False)
            if mode == "terminal":
                self._ensure_terminal_panel().start_auto_save(self._auto_session_path, self._auto_log_writer)
            else:
                self.receive_panel.start_auto_save(self._auto_session_path, self._auto_log_writer)
        terminal_on = mode == "terminal"
        self.receive_panel.set_find_shortcut_enabled(not terminal_on)
        self.receive_panel.set_terminal_mode_checked(terminal_on)
        if terminal_panel is not None:
            terminal_panel.set_find_shortcut_enabled(terminal_on)
            terminal_panel.set_terminal_mode_checked(terminal_on)
        # ponytail: 保存 splitter 尺寸，_body_stack.setCurrentIndex 会触发布局重算导致尺寸丢失
        _saved_sizes = self._splitter.sizes()
        if mode == "terminal":
            self.receive_panel.ext_cmd_btn.blockSignals(True)
            self.receive_panel.ext_cmd_btn.setChecked(False)
            self.receive_panel.ext_cmd_btn.blockSignals(False)
            self.receive_panel.ext_cmd_btn._refresh_style()
            if not inherit_ext_cmd:
                self.ext_cmd_panel.hide()
            terminal_panel.reset()
            self.status_bar.reset_counters()
            terminal_panel.set_display_font(DISPLAY_FAMILY, config.font_size)
            self._body_stack.setCurrentIndex(1)
            terminal_panel.set_search_enabled(inherit_search_enabled)
            terminal_panel._auto_scroll_chk.setChecked(inherit_auto_scroll)
            terminal_panel._ext_cmd_btn.setChecked(inherit_ext_cmd)
            QTimer.singleShot(0, terminal_panel.focus_edit)
        else:
            self.receive_panel._on_clear()
            self._body_stack.setCurrentIndex(0)
            self.receive_panel.set_search_enabled(inherit_search_enabled)
            self.receive_panel.auto_scroll_chk.setChecked(inherit_auto_scroll)
            if inherit_ext_cmd:
                self.receive_panel.ext_cmd_btn.setChecked(True)
        if _saved_sizes and len(_saved_sizes) >= 2:
            self._splitter.setSizes(_saved_sizes)

    def _on_check_update(self) -> None:
        if self._update_dialog is None:
            from gui.update_dialog import UpdateDialog
            self._update_dialog = UpdateDialog(self)
        self._update_dialog.open_check()

    def update_blocked(self) -> bool:
        return (self._closing or self.worker.is_open() or self.worker.is_busy()
                or self.send_panel.auto_chk.isChecked() or self._auto_reconnect_timer.isActive()
                or self._reconfig_in_progress or self._pending_reconfigure is not None)

    def _on_about(self) -> None:
        cfg_file = (get_config_dir() / "app.json").resolve()
        auto_save_dir = self._resolve_default_auto_save_dir()
        dlg = ThemedDialog(self)
        dlg.set_dialog_title(f"关于 {APP_NAME}")
        dlg.setMinimumWidth(ABOUT_DIALOG_MIN_WIDTH)
        cfg_url = QUrl.fromLocalFile(str(cfg_file)).toString()
        auto_save_url = QUrl.fromLocalFile(str(auto_save_dir)).toString()
        link_color = THEME_COLORS[config.theme]["link"]
        lbl = QLabel(
            f"{self.windowTitle()}<br><br>"
            f"配置文件路径：<a href=\"{cfg_url}\" style=\"text-decoration:none; color:{link_color};\">{cfg_file}</a><br><br>"
            f"自动保存日志路径：<a href=\"{auto_save_url}\" style=\"text-decoration:none; color:{link_color};\">{auto_save_dir}</a>"
        )
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        lbl.setOpenExternalLinks(False)
        lbl.linkActivated.connect(self._open_path_in_explorer)
        dlg.content_layout.addWidget(lbl)
        dlg.content_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        dlg.content_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _open_path_in_explorer(self, href: str) -> None:
        """点击路径链接后打开资源管理器：文件高亮，目录直接打开。"""
        url = QUrl(href)
        path_text = url.toLocalFile() if url.isValid() else href
        if not path_text:
            return
        p = Path(path_text)
        target = p if p.exists() else p.parent
        try:
            if sys.platform.startswith("win"):
                if target.is_file():
                    QProcess.startDetached("explorer.exe", ["/select,", str(target)])
                else:
                    QProcess.startDetached("explorer.exe", [str(target)])
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        except Exception:
            # 无法打开时静默失败，避免打断主流程。
            return

    def _open_local_path_in_explorer(self, path_text: str) -> None:
        if path_text:
            self._open_path_in_explorer(QUrl.fromLocalFile(path_text).toString())

    def _on_reset_defaults(self) -> None:
        """恢复默认设置：删除运行时配置目录并重启程序。"""
        gen_dir = get_config_dir()
        dlg = ThemedDialog(self)
        dlg.set_dialog_title("恢复默认设置")
        dlg.setMinimumWidth(RESET_DIALOG_MIN_WIDTH)
        lbl = QLabel(
            "将清除以下全部数据并以默认参数重启程序：\n\n"
            "• 窗口大小 / 位置记忆\n"
            "• 主题 / 字体设置\n"
            "• 串口配置历史（备注、参数）\n"
            "• 检索历史 / 发送历史\n"
            "• 缓冲区大小 / 时间戳配置\n"
            "• 扩展命令面板配置\n\n"
            f"将删除目录：{gen_dir}"
        )
        lbl.setWordWrap(True)
        dlg.content_layout.addWidget(lbl)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(reset_btn)
        dlg.content_layout.addLayout(btn_row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # 先结束串口与文件写入，再删除目录并重启。
        self._reset_defaults = True
        self._restart_after_reset = True
        self.close()

    def _connect_signals(self) -> None:
        cfg = self.config_panel
        cfg.open_requested.connect(self._on_open)
        cfg.close_requested.connect(self._on_close)
        cfg.reconfigure_requested.connect(self._on_reconfigure)

        self.worker.received_ready.connect(self._schedule_rx_drain)
        self.worker.sent.connect(self.status_bar.add_tx)
        self.worker.state_changed.connect(self._on_state_changed)

        self.send_panel.send_clicked.connect(self._on_send_clicked)
        self.send_panel.send_requested.connect(self._on_send)

        self.config_panel.cts_toggled.connect(self.worker.set_dtr)
        self.config_panel.rts_toggled.connect(self.worker.set_rts)

        self.receive_panel.encoding_changed.connect(self._on_encoding_menu_sync)
        self.receive_panel.cleared.connect(self.status_bar.reset_counters)
        self.receive_panel.ext_cmd_toggled.connect(self._on_ext_cmd_toggled)
        self.receive_panel.serial_mode_toggled.connect(self._on_serial_mode_toggled)
        self.ext_cmd_panel.send_requested.connect(self._on_send)

    # ---------- 槽 ----------
    def _schedule_rx_drain(self) -> None:
        if not self._rx_drain_timer.isActive():
            self._rx_drain_timer.start(0)

    def _drain_received(self, force: bool = False) -> None:
        pending = self.worker.pending_received_bytes()
        if pending >= RX_BACKLOG_WARNING_BYTES:
            diagnostics.event(logging.WARNING, f"接收队列积压 bytes={pending}; mode={config.serial_mode}", key="rx.backlog")
        else:
            diagnostics.recover("rx.backlog")
        deadline = float("inf") if force else time.perf_counter() + RX_DRAIN_BUDGET_MS / 1000.0
        has_more = False
        while time.perf_counter() < deadline:
            max_bytes = 1024 * 1024 if force else RX_DRAIN_MAX_BYTES
            chunks, has_more = self.worker.take_received(max_bytes)
            if not chunks:
                break
            if config.serial_mode == "terminal":
                self._ensure_terminal_panel().append_chunks(chunks)
            else:
                self.receive_panel.append_chunks(chunks)
            self.status_bar.add_rx(sum(len(data) for data, _ts, _spb in chunks))
            if not has_more:
                break
        if has_more and not force:
            self._rx_drain_timer.start(RX_DRAIN_INTERVAL_MS)

    def _on_open(self, cfg: dict) -> None:
        if self._closing:
            return
        self._pending_reconfigure = None
        self._stop_auto_reconnect()
        self._reconfig_in_progress = False
        self._stop_auto_save_session()
        self._port_info = f"{cfg['port']}@{cfg['baudrate']}bps"
        ok = self.worker.open(**cfg)
        if not ok:
            self.config_panel.set_connected(False)

    def _on_close(self) -> None:
        self._pending_reconfigure = None
        self._reconfig_in_progress = False
        self.config_panel.set_reconnecting(False)
        self._stop_auto_reconnect()
        self.worker.close()

    def _on_send(self, data: bytes, force_hex: bool = False) -> None:
        """发送数据：检查串口是否打开，未打开时闪烁红色提示。"""
        if not self.worker.is_open():
            self.config_panel.flash_port_not_open()
            return
        self.worker.send(data)
        if config.serial_mode == "terminal":
            return
        if config.tx_echo_enabled:
            self.receive_panel.echo_send(data, force_hex=force_hex)

    def _on_send_clicked(self) -> None:
        """点击发送按钮时，未连接则闪烁打开串口按钮。"""
        if not self.worker.is_open():
            self.config_panel.flash_port_not_open()

    def _on_state_changed(self, connected: bool, message: str) -> None:
        if self._closing:
            return
        was_connected = self._last_connected
        self.config_panel.set_connected(connected)
        if self.terminal_receive_panel is not None:
            self.terminal_receive_panel.set_connected(connected)
        port_info = self._port_info if connected else ""
        self.status_bar.set_connected(connected, message, port_info)
        if not connected and message.startswith("打开失败"):
            if not self._auto_reconnect_timer.isActive():
                self._show_serial_error("串口打开失败", message)
            else:
                self.config_panel.set_reconnecting(True)
        elif was_connected and not connected and message and message != "已断开":
            if config.auto_reconnect and not self._reconfig_in_progress:
                diagnostics.event(logging.WARNING, f"自动重连开始: {message}")
                # 自动重连模式：不弹窗，输出断开原因并进入扫描阶段
                self._append_system_message(f"[自动重连] 串口断开: {message}")
                self.config_panel.refresh_port_status()
                self._auto_reconnect_attempts = 0
                self._auto_reconnect_scanning = True
                self._auto_reconnect_scan_ticks = 0
                self.config_panel.set_reconnecting(True)
                self._auto_reconnect_timer.start()
            else:
                self._show_serial_error("串口连接丢失", message)
        if connected:
            # 重连成功时停止定时器
            self._auto_reconnect_timer.stop()
            self._auto_reconnect_scanning = True
            self.config_panel.set_reconnecting(False)
            if self._auto_reconnect_attempts > 0:
                diagnostics.event(logging.INFO, f"自动重连恢复，共尝试 {self._auto_reconnect_attempts} 次")
                self._append_system_message(
                    f"[自动重连] 重连成功 (共尝试 {self._auto_reconnect_attempts} 次)"
                )
                self._auto_reconnect_attempts = 0
            # pyserial 打开串口时会带默认控制线状态；连接后立刻按 UI 状态强制下发，避免误复位目标设备。
            self.worker.set_dtr(self.config_panel.cts_btn.isChecked())
            self.worker.set_rts(self.config_panel.rts_btn.isChecked())
            self._ensure_auto_save_session()
            self._reconfig_in_progress = False
            if config.serial_mode == "terminal":
                self._ensure_terminal_panel().focus_edit()
        else:
            if not self._reconfig_in_progress:
                self._stop_auto_save_session()
        if not connected:
            self.send_panel.stop_timer()
            if not self._reconfig_in_progress:
                self._port_info = ""
        self._sync_auto_save_status_text(connected)
        self._last_connected = connected

    def _show_serial_error(self, title: str, message: str) -> None:
        """ponytail: 串口错误弹窗统一使用 ThemedDialog 样式，并附带时间戳。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dlg = ThemedDialog(self)
        dlg.set_dialog_title(title)
        dlg.setMinimumWidth(SERIAL_ERROR_DIALOG_MIN_WIDTH)
        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet(f"color: {THEME_COLORS[config.theme]['status_error_text']}; font-weight: bold;")
        dlg.content_layout.addWidget(msg_lbl)
        ts_lbl = QLabel(f"异常时间：{now}")
        dlg.content_layout.addWidget(ts_lbl)
        dlg.content_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        dlg.content_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()

    def _stop_auto_reconnect(self) -> None:
        if self._auto_reconnect_timer.isActive():
            diagnostics.event(logging.INFO, f"自动重连取消，已尝试 {self._auto_reconnect_attempts} 次")
        diagnostics.recover("serial.open")
        self._auto_reconnect_timer.stop()
        self._auto_reconnect_attempts = 0
        self._auto_reconnect_scanning = True
        self._auto_reconnect_scan_ticks = 0

    def _on_auto_reconnect_tick(self) -> None:
        if self._closing or self.worker.is_busy():
            return
        cfg = self.config_panel.get_config()
        port = cfg["port"]
        if self._auto_reconnect_scanning:
            # 扫描阶段：静默检测设备是否恢复，每 10s 输出一次状态
            available = {p[1] for p in SerialWorker.list_ports()}
            if port not in available:
                self._auto_reconnect_scan_ticks += 1
                scan_interval_s = AUTO_RECONNECT_INTERVAL_MS / 1000
                if self._auto_reconnect_scan_ticks % max(1, int(10 / scan_interval_s)) == 0:
                    self._append_system_message(
                        f"[自动重连] 未扫描到 {port}，已等待 {int(self._auto_reconnect_scan_ticks * scan_interval_s)}s..."
                    )
                return
            # 检测到设备，等待 200ms 后再开始连接
            self._auto_reconnect_scanning = False
            self._auto_reconnect_attempts = 0
            return
        else:
            self._auto_reconnect_attempts += 1
        if self._auto_reconnect_attempts == 1 or self._auto_reconnect_attempts % 30 == 0:
            self._append_system_message(
                f"[自动重连] 第 {self._auto_reconnect_attempts} 次尝试 {port}@{cfg['baudrate']}bps..."
            )
        self._port_info = f"{port}@{cfg['baudrate']}bps"
        self.worker.open_async(**cfg)

    def _append_system_message(self, text: str) -> None:
        """向当前接收区注入系统消息。"""
        if config.serial_mode == "terminal":
            self._ensure_terminal_panel().append_system_message(text)
        else:
            self.receive_panel.append_system_message(text)

    def _on_reconfigure(self, cfg: dict) -> None:
        """连接状态下参数变更 → 关闭重开。"""
        # ponytail: 热重配属于同一会话，保留当前自动保存文件，不重新建文件。
        if self._closing:
            return
        self._stop_auto_reconnect()
        self._reconfig_in_progress = True
        if not self.worker.close():
            self._pending_reconfigure = cfg
            self._lifecycle_timer.start()
            return
        self._port_info = f"{cfg['port']}@{cfg['baudrate']}bps"
        ok = self.worker.open(**cfg)
        if not ok:
            self._reconfig_in_progress = False
            self.config_panel.set_connected(False)

    def _finish_lifecycle_transition(self) -> None:
        if self.worker.is_busy():
            return
        self._lifecycle_timer.stop()
        if self._closing:
            self.close()
        elif self._pending_reconfigure is not None:
            cfg = self._pending_reconfigure
            self._pending_reconfigure = None
            self._on_reconfigure(cfg)

    # ---------- 窗口事件 ----------
    def eventFilter(self, watched, event) -> bool:
        if watched is self._splitter and event.type() == QEvent.Type.Resize and self.ext_cmd_panel.isVisible():
            # 左侧自行伸缩；右侧目标宽度变化时才重新分配，避免每像素重复绘制。
            min_w, max_w = self._ext_cmd_width_bounds()
            panel_w = round(min_w + (max_w - min_w) * self._ext_cmd_range_ratio)
            if self.ext_cmd_panel.width() != panel_w:
                self._apply_splitter_sizes(panel_w)
        return super().eventFilter(watched, event)

    # ---------- 关闭清理 ----------
    def closeEvent(self, event) -> None:
        self._closing = True
        self._pending_reconfigure = None
        self._stop_auto_reconnect()
        self.send_panel.stop_timer()
        if not self.worker.close():
            self.status_bar.set_connected(False, "正在等待串口线程结束…", "")
            self._lifecycle_timer.start()
            event.ignore()
            return
        self._lifecycle_timer.stop()
        try:
            self._set_keep_awake(False)
            self.send_panel.stop_timer()
            self.receive_panel._flush_history_save()
            self._rx_drain_timer.stop()
            self._drain_received(force=True)
            self._stop_auto_save_session()
        except Exception as e:
            diagnostics.event(logging.ERROR, "关闭清理异常", exc_info=True)
            QMessageBox.warning(self, "关闭异常", str(e))
            if self._update_dialog and self._update_dialog.installing:
                self._update_dialog.installing = False
                self._closing = False
                self._rx_drain_timer.start()
                event.ignore()
                return
        if self._reset_defaults:
            if not self._restart_after_reset:
                super().closeEvent(event)
                return
            diagnostics.shutdown()
            try:
                gen_dir = get_config_dir()
                if gen_dir.exists():
                    shutil.rmtree(gen_dir)
            except OSError as exc:
                diagnostics.initialize(get_config_dir() / "diagnostics")
                diagnostics.event(logging.ERROR, "恢复默认失败", exc_info=True)
                self._reset_defaults = False
                self._closing = False
                QMessageBox.warning(self, "恢复默认失败", str(exc))
                event.ignore()
                return
            QProcess.startDetached(sys.executable, sys.argv)
            super().closeEvent(event)
            return
        self._save_splitter_ratio()
        self._save_window_state()
        if self._update_dialog:
            if self._update_dialog.installing:
                try:
                    self._update_dialog.handoff()
                except (OSError, ValueError) as exc:
                    self._update_dialog.installing = False
                    self._closing = False
                    self._rx_drain_timer.start()
                    QMessageBox.warning(self, "无法启动更新", str(exc))
                    event.ignore()
                    return
        super().closeEvent(event)

    def _save_window_state(self) -> None:
        """保存当前窗口尺寸与最大化状态。"""
        if self._window_maximized:
            g = self._normal_geometry
            w = g.width() if g.width() > 0 else config.window_width
            h = g.height() if g.height() > 0 else config.window_height
            self._window_debug(
                "save-window-state maximized "
                f"normal=({self._rect_text(g)}) -> save={w}x{h}, max=True"
            )
            config.save_window_state(w, h, True)
            return
        if self._vertical_maximized or self._snap_mode is not None:
            g = self._normal_geometry
            w = g.width() if g.width() > 0 else config.window_width
            h = g.height() if g.height() > 0 else config.window_height
            config.save_window_state(w, h, False)
            return
        g = self.geometry()
        self._normal_geometry = QRect(g)
        self._window_debug(
            f"save-window-state normal geom=({self._rect_text(g)}) -> save={g.width()}x{g.height()}, max=False"
        )
        config.save_window_state(g.width(), g.height(), False)

    def _on_font_size(self) -> None:
        """弹出字体大小选择对话框并应用。"""
        result = choose_font_size(config.font_size, self)
        if result is None:
            return
        config.font_size = result
        self._apply_display_fonts(result)

    def _apply_display_fonts(self, size: int) -> None:
        """仅调整接收文本框和发送输入框的字号。"""
        self.receive_panel.set_display_font(DISPLAY_FAMILY, size)
        self.send_panel.set_display_font(DISPLAY_FAMILY, size)
        if self.terminal_receive_panel is not None:
            self.terminal_receive_panel.set_display_font(DISPLAY_FAMILY, size)

    def _on_ts_color_settings(self) -> None:
        """弹出时间戳配置：样式下拉 + 深/浅主题颜色。"""
        options = [
            ("[YYYY-MM-DD hh:mm:ss.xxx]", "yyyy-MM-dd hh:mm:ss.xxx"),
            ("[YY-MM-DD hh:mm:ss.xxx]", "yy-MM-dd hh:mm:ss.xxx"),
            ("[MM-DD hh:mm:ss.xxx]", "MM-dd hh:mm:ss.xxx"),
            ("[hh:mm:ss.xxx]", "hh:mm:ss.xxx"),
            ("[hh:mm:ss]", "hh:mm:ss"),
        ]
        dlg = ColorPickerDialog(
            self,
            "时间戳配置",
            config.ts_color_dark,
            config.ts_color_light,
            format_options=options,
            current_format=config.ts_format,
            format_label="时间戳样式:",
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        config.ts_format = dlg.selected_format() or "yyyy-MM-dd hh:mm:ss.xxx"
        config.ts_color_dark = dlg.selected_dark()
        config.ts_color_light = dlg.selected_light()
        # 按当前主题应用对应颜色
        self.receive_panel.set_ts_color(
            config.ts_color_dark if config.theme == "dark" else config.ts_color_light
        )
        self._rebuild_if_needed()

    def _rebuild_if_needed(self) -> None:
        """如果接收区处于需重建状态（时间戳/HEX 开启），触发重建。"""
        if self.receive_panel.ts_chk.isChecked() or self.receive_panel.hex_chk.isChecked():
            self.receive_panel._rebuild_view()

    def _on_buffer_size_settings(self) -> None:
        """弹出缓冲区大小设置对话框(范围 1~128 MB)"""
        current = config.buffer_size_mb
        dlg = ThemedDialog(self, "缓冲区大小")
        dlg.setMinimumWidth(BUFFER_DIALOG_MIN_WIDTH)
        row = QHBoxLayout()
        row.addWidget(QLabel("接收缓冲区大小(MB):"))
        spin = QSpinBox()
        spin.setRange(ReceivePanel.BUFFER_MIN_MB, ReceivePanel.BUFFER_MAX_MB)
        spin.setValue(current)
        row.addWidget(spin)
        dlg.content_layout.addLayout(row)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        apply_btn = QPushButton("应用")
        apply_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(apply_btn)
        dlg.content_layout.addLayout(btn_row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        size = spin.value()
        config.buffer_size_mb = size
        self.receive_panel.set_buffer_size_mb(size)
        self.status_bar.set_buffer_mb(size)

    def _on_encoding_menu_changed(self, encoding: str) -> None:
        """编码菜单项被选中，更新接收区编码。"""
        self._sync_encoding_menu_marker(encoding)
        self.receive_panel.set_encoding(encoding)

    def _on_tx_encoding_menu_changed(self, encoding: str) -> None:
        """发送编码菜单项被选中，更新发送编码配置。"""
        config.tx_encoding = encoding
        self._sync_tx_encoding_menu_marker(encoding)

    def _on_rx_color_hilight(self) -> None:
        """编辑关键词分组，确认后才保存并刷新接收区。"""
        dlg = KeywordConfigDialog(self, rx_color_hilight.items)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            rx_color_hilight.save(dlg.groups())
            self.receive_panel.set_rx_color_hilights(rx_color_hilight.items)
        dlg.deleteLater()

    def _on_encoding_menu_sync(self, encoding: str) -> None:
        """接收区编码变化后同步菜单勾选。"""
        self._sync_encoding_menu_marker(encoding)

    def _on_auto_save_settings(self) -> None:
        dlg = AutoSaveDialog(self, enabled=config.auto_save_enabled, save_dir=config.auto_save_dir)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        config.auto_save_enabled = dlg.enabled()
        try:
            abs_dir = ensure_dir(dlg.save_dir())
            config.auto_save_dir = str(abs_dir)
        except Exception as e:
            QMessageBox.warning(self, "自动保存日志", f"保存目录不可用: {e}")
            config.auto_save_enabled = False

        if not config.auto_save_enabled:
            self._stop_auto_save_session()
            self._last_auto_session_path = ""
        elif self.worker.is_open() and not self._auto_session_open:
            self._ensure_auto_save_session()
        self._sync_auto_save_status_text(self.worker.is_open())

    def _resolve_default_auto_save_dir(self) -> Path:
        save_dir = str(config.auto_save_dir).strip() or "auto-gen/uart-log"
        p = Path(save_dir).expanduser()
        if not p.is_absolute():
            p = get_runtime_root_dir() / p
        return p.resolve()

    def _show_first_use_paths_notice_if_needed(self) -> None:
        if bool(config.get("first_use_notice_shown", False)):
            return
        cfg_file = (get_config_dir() / "app.json").resolve()
        auto_save_dir = self._resolve_default_auto_save_dir()
        dlg = ThemedDialog(self)
        dlg.set_dialog_title("首次使用提示")
        dlg.setMinimumWidth(FIRST_USE_DIALOG_MIN_WIDTH)
        lbl = QLabel(
            "检测到首次使用，本工具将默认在以下位置生成数据：\n\n"
            f"配置文件路径：{cfg_file}\n"
            f"自动保存日志目录：{auto_save_dir}\n\n"
            "可在关于中查看路径信息，在设置中修改自动保存日志目录。\n\n"
            "提示：移动 exe 到新目录后，会在新目录重新生成一套配置与数据。"
        )
        lbl.setWordWrap(True)
        dlg.content_layout.addWidget(lbl)
        dlg.content_layout.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(dlg.accept)
        dlg.content_layout.addWidget(ok_btn, alignment=Qt.AlignmentFlag.AlignRight)
        dlg.exec()
        config.set("first_use_notice_shown", True)

    def _sync_auto_save_status_text(self, connected: bool) -> None:
        if not config.auto_save_enabled:
            self.status_bar.set_auto_save_status("")
            return
        if connected and self._auto_session_path:
            self.status_bar.set_auto_save_status(
                f"✅正在保存到 {Path(self._auto_session_path).name}",
                f"✅正在保存到 {self._auto_session_path}",
                self._auto_session_path,
            )
            return
        if self._last_auto_session_path:
            self.status_bar.set_auto_save_status(
                f"✅已保存日志: {Path(self._last_auto_session_path).name}",
                f"✅已保存日志: {self._last_auto_session_path}",
                self._last_auto_session_path,
            )
            return
        self.status_bar.set_auto_save_status("✅自动保存日志")

    def _ensure_auto_save_session(self) -> None:
        if self._auto_session_open or not config.auto_save_enabled:
            return
        port = self.config_panel.port_combo.currentText().strip() or "COM"
        note = self.config_panel.note_edit.text().strip()
        try:
            path, writer = open_auto_log_file(config.auto_save_dir, port, note)
        except Exception as e:
            diagnostics.event(logging.ERROR, "创建接收自动保存文件失败", key="auto_save.open", exc_info=True)
            QMessageBox.warning(self, "自动保存日志", f"创建日志文件失败: {e}")
            config.auto_save_enabled = False
            self._auto_session_open = False
            self._auto_session_path = ""
            self.receive_panel.stop_auto_save()
            if self.terminal_receive_panel is not None:
                self.terminal_receive_panel.stop_auto_save()
            return
        if config.serial_mode == 'terminal':
            self._ensure_terminal_panel().start_auto_save(str(path), writer)
        else:
            self.receive_panel.start_auto_save(str(path), writer)
        self._auto_log_writer = writer
        self._auto_log_health_timer.start()
        self._auto_session_open = True
        self._auto_session_path = str(path)
        self._last_auto_session_path = str(path)

    def _stop_auto_save_session(self) -> None:
        self._auto_log_health_timer.stop()
        self.receive_panel.stop_auto_save(close_writer=False)
        if self.terminal_receive_panel is not None:
            self.terminal_receive_panel.stop_auto_save(close_writer=False)
        writer = self._auto_log_writer
        self._auto_log_writer = None
        if writer is not None:
            if not writer.close():
                diagnostics.event(logging.ERROR, f"接收自动保存未完成: {writer.error_message() or '停止超时'}", key="auto_save.close")
        self._auto_session_open = False
        self._auto_session_path = ""

    def _check_auto_log_writer(self) -> None:
        writer = self._auto_log_writer
        if writer is None:
            self._auto_log_health_timer.stop()
            return
        error = writer.error_message()
        if not error:
            return
        diagnostics.event(logging.ERROR, f"接收自动保存失败: {error}", key="auto_save.write")
        self._stop_auto_save_session()
        self.status_bar.set_auto_save_status("⚠自动保存失败", f"⚠自动保存失败: {error}")

    def restore_ext_cmd_state(self) -> None:
        """主窗口首帧显示后再恢复扩展命令内容。"""
        if not self._restore_ext_cmd_enabled:
            return
        self._restore_ext_cmd_enabled = False
        self._on_ext_cmd_toggled(True, persist=False)

    def _on_ext_cmd_toggled(self, visible: bool, *, persist: bool = True) -> None:
        """📋 动画展开/折叠扩展命令面板。"""
        if persist:
            self._restore_ext_cmd_enabled = False
            config.set("ext_cmd_enabled", visible)
        old_anim = getattr(self, "_ext_cmd_anim", None)
        if old_anim is not None:
            old_anim.stop()
            self._ext_cmd_anim = None

        panel = self.ext_cmd_panel
        min_w, max_w = self._ext_cmd_width_bounds()
        if visible:
            panel.ensure_loaded()
            panel.show()
            self._splitter.setHandleWidth(4)
            target_w = self._restore_target_width(min_w, max_w)
            panel.setMaximumWidth(0)
        else:
            panel.setMaximumWidth(panel.width())
            target_w = 0

        if ANIMATION_ENABLED:
            anim = QPropertyAnimation(panel, b"maximumWidth")
            anim.setDuration(PANEL_ANIMATION_DURATION_MS)
            anim.setStartValue(panel.maximumWidth())
            anim.setEndValue(target_w)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._ext_cmd_anim = anim

            def _on_finished() -> None:
                self._ext_cmd_anim = None
                if visible:
                    panel.setMaximumWidth(16777215)
                    self._apply_splitter_sizes(target_w)
                else:
                    self._save_splitter_ratio()
                    self._splitter.setHandleWidth(0)
                    panel.hide()

            anim.finished.connect(_on_finished)
            anim.start()
        else:
            if visible:
                panel.setMaximumWidth(16777215)
                self._apply_splitter_sizes(target_w)
            else:
                panel.setMaximumWidth(0)
                self._save_splitter_ratio()
                self._splitter.setHandleWidth(0)
                panel.hide()

    def _restore_target_width(self, min_w: int, max_w: int) -> int:
        """从最小/最大宽度区间中的保存位置恢复面板目标宽度。"""
        return round(min_w + (max_w - min_w) * self._ext_cmd_range_ratio)

    def _apply_splitter_sizes(self, panel_w: int) -> None:
        """显式设置 splitter 尺寸，确保面板宽度与动画终态一致。"""
        hw = self._splitter.handleWidth()
        left_w = max(0, self._splitter.width() - panel_w - hw)
        self._splitter.setSizes([left_w, panel_w])

    def _ext_cmd_width_bounds(self) -> tuple[int, int]:
        """动态计算扩展面板的最小/最大宽度。

        最小宽度分数：窗口从 320px 到屏幕宽度，从 1/3 线性过渡到 1/7。
        最大宽度分数：窗口从 320px 到屏幕宽度，从 4/10 线性过渡到 3/10。
        """
        win_w = self.width()
        screen = self._screen_available_geometry()
        max_win_w = max(win_w, screen.width())
        min_win_w = 320
        t = max(0.0, min(1.0, (win_w - min_win_w) / max(1, max_win_w - min_win_w)))
        min_frac = 1 / 3 - t * (1 / 3 - 1 / 7)  # 1/3 → 1/7
        max_frac = 4 / 10 - t * (4 / 10 - 3 / 10)  # 4/10 → 3/10
        min_w = int(win_w * min_frac)
        max_w = int(win_w * max_frac)
        return min_w, max_w

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """splitter 拖拽事件：强制动态最小/最大宽度约束。"""
        if not self.ext_cmd_panel.isVisible():
            return
        sizes = self._splitter.sizes()
        if len(sizes) < 2:
            return
        min_w, max_w = self._ext_cmd_width_bounds()
        hw = self._splitter.handleWidth()
        if sizes[1] < min_w:
            self._splitter.blockSignals(True)
            self._splitter.setSizes([self.width() - min_w - hw, min_w])
            self._splitter.blockSignals(False)
        elif sizes[1] > max_w:
            self._splitter.blockSignals(True)
            self._splitter.setSizes([self.width() - max_w - hw, max_w])
            self._splitter.blockSignals(False)
        # 立即更新区间比例：用户拖动后立刻调整窗口时，不能等待防抖保存。
        sizes = self._splitter.sizes()
        if len(sizes) >= 2:
            span = max(1, max_w - min_w)
            self._ext_cmd_range_ratio = max(0.0, min(1.0, (sizes[1] - min_w) / span))
        self._splitter_save_timer.start()

    def _save_splitter_ratio(self) -> None:
        """保存扩展面板在动态最小/最大宽度区间中的位置。"""
        if not self.ext_cmd_panel.isVisible():
            return
        sizes = self._splitter.sizes()
        if len(sizes) >= 2:
            min_w, max_w = self._ext_cmd_width_bounds()
            span = max(1, max_w - min_w)
            ratio = max(0.0, min(1.0, (sizes[1] - min_w) / span))
            self._ext_cmd_range_ratio = ratio
            config.set("ext_cmd_splitter_range_ratio", round(ratio, 4))
