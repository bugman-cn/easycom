"""串口配置面板：两行布局，高级参数默认折叠。

第一行（始终可见）：串口号/设备名/波特率/[展开▼]/打开
第二行（默认隐藏）：数据位/停止位/校验位
自动轮询检测串口插拔，不断开当前连接。
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from config.layout import (
    CONTROL_HEIGHT,
    EMOJI_BUTTON_SMALL_SIZE,
    SERIAL_FIELD_GAP,
    SERIAL_FIELD_WIDTH_CHARS,
    SERIAL_PANEL_MARGIN_LEFT,
    SERIAL_PANEL_MARGIN_RIGHT,
    SERIAL_PANEL_MARGIN_TOP,
    SERIAL_ROW2_TOP_MARGIN,
    SERIAL_ROW_SPACING,
)
from config.runtime import (
    ANIMATION_ENABLED,
    PANEL_ANIMATION_DURATION_MS,
    SERIAL_POLL_INTERVAL_MS,
    UI_DEBOUNCE_INTERVAL_MS,
)
from config.theme import THEME_COLORS
from config.ui import EMOJI_BUTTON_TEXTS
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from config.app_config import _com_sort_key, config, uart_history
from core.serial_worker import SerialWorker
from gui.emoji_button import EmojiButton
from gui.rounded_scroll_bar import RoundedSelectionDelegate, configure_combo_popup
from utils.font_manager import apply_display_fallbacks, get_display_font_css_stack
from utils.theme import active_green_style


# 校验位下拉文本 -> pyserial parity
PARITY_MAP = {"None": "N", "Even": "E", "Odd": "O", "Mark": "M", "Space": "S"}
STOPBITS_MAP = {"1": 1, "1.5": 1.5, "2": 2}
BYTESIZE_MAP = {"5": 5, "6": 6, "7": 7, "8": 8}
BAUD_RATES = [
    "1200", "2400", "4800", "9600", "14400", "19200", "38400", "56000",
    "75000", "115200", "128000", "230400", "256000", "460800", "512000",
    "921600", "1000000", "1500000", "2000000",
]


class _PortDelegate(RoundedSelectionDelegate):
    """下拉时显示完整端口描述，禁止缩略。"""

    def __init__(self, descriptions: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self._desc = descriptions

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        device = index.data(Qt.ItemDataRole.DisplayRole)
        full = self._desc.get(device)
        if full:
            option.text = full
        # ponytail: 禁止文本省略号折叠
        option.textElideMode = Qt.TextElideMode.ElideNone


class _PortCombo(QComboBox):
    """QComboBox 子类：showPopup 时强制 popup view 宽度到预设值。"""

    popup_about_to_show = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._popup_min_w = 300

    def set_popup_width(self, w: int) -> None:
        self._popup_min_w = w

    def showPopup(self) -> None:
        self.popup_about_to_show.emit()
        super().showPopup()
        v = self.view()
        if v and self._popup_min_w > 0:
            popup_w = max(self.width(), self._popup_min_w)
            v.setFixedWidth(popup_w)
            v.setMaximumWidth(max(popup_w, 700))


class SerialConfigPanel(QWidget):
    open_requested = Signal(dict)
    close_requested = Signal()
    reconfigure_requested = Signal(dict)
    cts_toggled = Signal(bool)
    rts_toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._port_descriptions: dict[str, str] = {}  # device → full display
        self._ports_refreshed = False
        self._build_ui()
        self.port_combo.popup_about_to_show.connect(self._ensure_ports_refreshed)
        self._reconf_timer = QTimer(self)
        self._reconf_timer.setSingleShot(True)
        self._reconf_timer.setInterval(UI_DEBOUNCE_INTERVAL_MS)
        self._reconf_timer.timeout.connect(self._do_reconfigure)

        # 备注保存 debounce（200ms）
        self._note_save_timer = QTimer(self)
        self._note_save_timer.setSingleShot(True)
        self._note_save_timer.setInterval(UI_DEBOUNCE_INTERVAL_MS)
        self._note_save_timer.timeout.connect(self._do_save_note)

        # 自动轮询串口插拔（2000ms），不断开已连接端口
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(SERIAL_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_ports)
        self._poll_timer.start()
        QTimer.singleShot(0, self.refresh_ports)

    # ---------- helpers ----------
    @staticmethod
    def _make_locked_combo(items: list[str], current: str = "", width_chars: int = 8) -> QComboBox:
        """创建一个可编辑但禁止手输的下拉框，行为与串口号一致。"""
        cb = QComboBox()
        cb.addItems(items)
        if current:
            cb.setCurrentText(current)
        cb.setEditable(True)
        cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if cb.lineEdit() is not None:
            cb.lineEdit().setReadOnly(True)
        w = cb.fontMetrics().horizontalAdvance("W") * width_chars
        cb.setFixedWidth(w)
        cb.setFixedHeight(CONTROL_HEIGHT)
        cb.setStyleSheet(f"QComboBox {{ font-family: {get_display_font_css_stack()}; }}")
        configure_combo_popup(cb)
        return cb

    # ---------- UI ----------
    def _build_ui(self) -> None:
        grp = QWidget()
        vbox = QVBoxLayout(grp)
        vbox.setContentsMargins(SERIAL_PANEL_MARGIN_LEFT, SERIAL_PANEL_MARGIN_TOP, SERIAL_PANEL_MARGIN_RIGHT, SERIAL_PANEL_MARGIN_TOP)
        vbox.setSpacing(0)
        vbox.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        # ── 第一行：串口号 → 波特率 → 备注 → DTR → RTS → 展开 → 打开 ──
        row1 = QHBoxLayout()
        row1.setSpacing(SERIAL_ROW_SPACING)

        # 统一控件宽度（8 字符等宽）
        ctrl_w = self.fontMetrics().horizontalAdvance("W") * SERIAL_FIELD_WIDTH_CHARS

        row1.addWidget(QLabel("串口号:"))
        self.port_combo = _PortCombo()
        self.port_combo.setEditable(True)
        self.port_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.port_combo.lineEdit() is not None:
            self.port_combo.lineEdit().setReadOnly(True)
        self.port_combo.setFixedWidth(ctrl_w)
        self.port_combo.setFixedHeight(CONTROL_HEIGHT)
        self.port_combo.setItemDelegate(_PortDelegate(self._port_descriptions, self.port_combo))
        configure_combo_popup(self.port_combo)
        row1.addWidget(self.port_combo)

        row1.addSpacing(SERIAL_FIELD_GAP)
        row1.addWidget(QLabel("波特率:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(BAUD_RATES)
        self.baud_combo.setEditable(True)
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setStyleSheet(f"QComboBox {{ font-family: {get_display_font_css_stack()}; }}")
        if self.baud_combo.lineEdit() is not None:
            self.baud_combo.lineEdit().setValidator(QIntValidator(1, 9999999))
        self.baud_combo.setFixedWidth(ctrl_w)
        self.baud_combo.setFixedHeight(CONTROL_HEIGHT)
        configure_combo_popup(self.baud_combo)
        row1.addWidget(self.baud_combo)

        row1.addSpacing(SERIAL_FIELD_GAP)
        row1.addWidget(QLabel("设备名:"))
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("NULL")
        self.note_edit.setFixedWidth(ctrl_w)
        self.note_edit.setFixedHeight(CONTROL_HEIGHT)
        self.note_edit.setStyleSheet(f"QLineEdit {{ font-family: {get_display_font_css_stack()}; }}")
        self.note_edit.textChanged.connect(self._on_note_changed)
        row1.addWidget(self.note_edit)

        row1.addSpacing(SERIAL_FIELD_GAP)
        self.cts_btn = QPushButton("DTR")
        self.cts_btn.setCheckable(True)
        self.cts_btn.setMinimumWidth(0)
        self.cts_btn.setFixedHeight(CONTROL_HEIGHT)
        self.cts_btn.clicked.connect(self._on_cts_clicked)
        row1.addWidget(self.cts_btn)

        self.rts_btn = QPushButton("RTS")
        self.rts_btn.setCheckable(True)
        self.rts_btn.setMinimumWidth(0)
        self.rts_btn.setFixedHeight(CONTROL_HEIGHT)
        self.rts_btn.clicked.connect(self._on_rts_clicked)
        row1.addWidget(self.rts_btn)
        self._apply_line_btn_style(self.cts_btn)
        self._apply_line_btn_style(self.rts_btn)

        cts_w = self.cts_btn.fontMetrics().horizontalAdvance("DTR") + 14
        rts_w = self.rts_btn.fontMetrics().horizontalAdvance("RTS") + 14
        self.cts_btn.setFixedWidth(cts_w)
        self.rts_btn.setFixedWidth(rts_w)

        # 展开/折叠按钮（改为 EmojiButton，支持深浅主题图标变体）
        self._expand_btn = EmojiButton(EMOJI_BUTTON_TEXTS["config_expand"], checkable=True, size=EMOJI_BUTTON_SMALL_SIZE)
        self._expand_btn.setCheckable(True)
        self._expand_btn.setToolTip("展开高级参数")
        self._expand_btn.clicked.connect(self._on_toggle_expand)

        self.open_btn = QPushButton("打开串口")
        self.open_btn.setCheckable(True)
        self.open_btn.setFixedHeight(CONTROL_HEIGHT)
        self.open_btn.clicked.connect(self._on_open_clicked)
        row1.addWidget(self.open_btn)
        row1.addWidget(self._expand_btn)

        row1.addStretch()
        vbox.addLayout(row1)

        # ── 第二行：数据位 → 停止位 → 校验位 → 发送回显（默认折叠）──
        self._row2 = QWidget()
        row2 = QHBoxLayout(self._row2)
        row2.setContentsMargins(0, SERIAL_ROW2_TOP_MARGIN, 0, 0)
        row2.setSpacing(SERIAL_ROW_SPACING)

        row2.addWidget(QLabel("数据位:"))
        self.data_combo = self._make_locked_combo(list(BYTESIZE_MAP.keys()), "8", width_chars=8)
        row2.addWidget(self.data_combo)

        row2.addSpacing(8)
        row2.addWidget(QLabel("停止位:"))
        self.stop_combo = self._make_locked_combo(list(STOPBITS_MAP.keys()), "1", width_chars=8)
        row2.addWidget(self.stop_combo)

        row2.addSpacing(8)
        row2.addWidget(QLabel("校验位:"))
        self.parity_combo = self._make_locked_combo(list(PARITY_MAP.keys()), "None", width_chars=8)
        row2.addWidget(self.parity_combo)

        row2.addSpacing(8)
        self.tx_echo_chk = QPushButton("发送回显")
        self.tx_echo_chk.setCheckable(True)
        self.tx_echo_chk.setMinimumWidth(0)
        self.tx_echo_chk.setFixedHeight(CONTROL_HEIGHT)
        self.tx_echo_chk.setChecked(config.tx_echo_enabled)
        self.tx_echo_chk.clicked.connect(self._on_echo_toggled)
        echo_w = cts_w + 6 + rts_w
        self.tx_echo_chk.setFixedWidth(echo_w)
        self.open_btn.setFixedWidth(echo_w)
        row2.addWidget(self.tx_echo_chk)
        self._apply_line_btn_style(self.tx_echo_chk)

        self.auto_reconnect_chk = QPushButton("自动重连")
        self.auto_reconnect_chk.setCheckable(True)
        self.auto_reconnect_chk.setMinimumWidth(0)
        self.auto_reconnect_chk.setFixedHeight(CONTROL_HEIGHT)
        self.auto_reconnect_chk.setChecked(config.get("auto_reconnect", False))
        self.auto_reconnect_chk.clicked.connect(self._on_auto_reconnect_toggled)
        self.auto_reconnect_chk.setFixedWidth(echo_w)
        row2.addWidget(self.auto_reconnect_chk)
        self._apply_line_btn_style(self.auto_reconnect_chk)

        row2.addStretch()

        self._row2.setMaximumHeight(0)
        self._row2.setMinimumHeight(0)
        vbox.addWidget(self._row2)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(grp, alignment=Qt.AlignmentFlag.AlignLeft)

        # 参数变更信号 → 重配置
        self.port_combo.currentTextChanged.connect(self._on_port_changed)
        self.baud_combo.currentTextChanged.connect(self._on_config_changed)
        self.data_combo.currentTextChanged.connect(self._on_config_changed)
        self.stop_combo.currentTextChanged.connect(self._on_config_changed)
        self.parity_combo.currentTextChanged.connect(self._on_config_changed)

    # ---------- 行为 ----------
    def _ensure_ports_refreshed(self) -> None:
        if not self._ports_refreshed:
            self.refresh_ports()

    def refresh_ports(self) -> None:
        current_device = self.port_combo.currentText()
        self.port_combo.clear()
        self._port_descriptions.clear()
        ports = SerialWorker.list_ports()
        available = {p[1] for p in ports}
        if ports:
            ports.sort(key=lambda p: _com_sort_key(p[1]))
            for display, device in ports:
                self.port_combo.addItem(device)
                self._port_descriptions[device] = display
            # 按 device 名恢复选中；失效端口只保留在可编辑显示框中，不加入下拉列表
            if current_device and current_device != "NULL":
                if current_device in available:
                    self.port_combo.setCurrentText(current_device)
                else:
                    self.port_combo.setEditText(current_device)
        else:
            self.port_combo.addItem("NULL")
            if current_device and current_device != "NULL":
                self.port_combo.setEditText(current_device)
        self._sync_popup_width()
        self._update_port_tooltip()
        self._update_port_status_style(current_device, available)
        self._ports_refreshed = True

    def get_config(self) -> dict:
        return {
            "port": self.port_combo.currentText(),
            "baudrate": int(self.baud_combo.currentText()),
            "bytesize": BYTESIZE_MAP[self.data_combo.currentText()],
            "stopbits": STOPBITS_MAP[self.stop_combo.currentText()],
            "parity": PARITY_MAP[self.parity_combo.currentText()],
        }

    def _on_open_clicked(self) -> None:
        if self.open_btn.isChecked():
            # 波特率合法性校验
            baud_text = self.baud_combo.currentText().strip()
            try:
                baud = int(baud_text)
                if baud <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                self.open_btn.setChecked(False)
                self._flash_baud_invalid()
                return
            cfg = self.get_config()
            if not cfg["port"] or cfg["port"] == "NULL":
                self.open_btn.setChecked(False)
                return
            self._save_current_config()
            self.open_requested.emit(cfg)
        else:
            self.close_requested.emit()

    def _flash_baud_invalid(self) -> None:
        """波特率非法时红色闪烁提示。"""
        red = (
            f"QComboBox {{ color: {THEME_COLORS[config.theme]['status_error_text_on_bg']}; background-color: {THEME_COLORS[config.theme]['status_error_bg']}; }}"
        )
        self.baud_combo.setStyleSheet(red)
        QTimer.singleShot(UI_DEBOUNCE_INTERVAL_MS, lambda: self.baud_combo.setStyleSheet(""))

    def set_connected(self, connected: bool) -> None:
        """根据连接状态切换按钮图标。参数控件保持可编辑以支持热切换。"""
        self.open_btn.setText("关闭串口" if connected else "打开串口")
        self.open_btn.setChecked(connected)
        self._apply_line_btn_style(self.cts_btn)
        self._apply_line_btn_style(self.rts_btn)
        if connected:
            self.open_btn.setStyleSheet(
                f"QPushButton {{ {active_green_style(config.theme)} }}"
                f"QPushButton:hover {{ background-color: {THEME_COLORS[config.theme]['status_success_bg_hover']}; }}"
            )
        else:
            self.open_btn.setStyleSheet("")

    def set_reconnecting(self, active: bool) -> None:
        """重连中状态：按钮保持"关闭串口"文字，背景透明橙色。"""
        if active:
            self.open_btn.setText("关闭串口")
            self.open_btn.setChecked(True)
            self.open_btn.setStyleSheet(
                f"QPushButton {{ color: {THEME_COLORS[config.theme]['status_warning_text']}; background-color: {THEME_COLORS[config.theme]['status_warning_bg']}; }}"
                f"QPushButton:hover {{ color: {THEME_COLORS[config.theme]['status_warning_text']}; background-color: {THEME_COLORS[config.theme]['status_warning_bg_hover']}; }}"
            )

    def flash_port_not_open(self) -> None:
        """串口未打开时发送，打开按钮闪烁红色提示。"""
        red = (
            f"QPushButton {{ color: {THEME_COLORS[config.theme]['status_error_text_on_bg']}; background-color: {THEME_COLORS[config.theme]['status_error_bg']}; }}"
            f"QPushButton:hover {{ background-color: {THEME_COLORS[config.theme]['status_error_bg_hover']}; }}"
        )
        self.open_btn.setStyleSheet(red)
        # 200ms 后恢复原样式
        QTimer.singleShot(UI_DEBOUNCE_INTERVAL_MS, self._restore_open_btn_style)

    def _restore_open_btn_style(self) -> None:
        """恢复打开按钮的正常样式。"""
        if self.open_btn.isChecked():
            self.open_btn.setStyleSheet(
                f"QPushButton {{ {active_green_style(config.theme)} }}"
                f"QPushButton:hover {{ background-color: {THEME_COLORS[config.theme]['status_success_bg_hover']}; }}"
            )
        else:
            self.open_btn.setStyleSheet("")

    def _on_cts_clicked(self, checked: bool) -> None:
        self._apply_line_btn_style(self.cts_btn)
        self.cts_toggled.emit(checked)

    def _on_rts_clicked(self, checked: bool) -> None:
        self._apply_line_btn_style(self.rts_btn)
        self.rts_toggled.emit(checked)

    def _on_echo_toggled(self, checked: bool) -> None:
        self._apply_line_btn_style(self.tx_echo_chk)
        config.tx_echo_enabled = checked

    def _on_auto_reconnect_toggled(self, checked: bool) -> None:
        self._apply_line_btn_style(self.auto_reconnect_chk)
        config.auto_reconnect = checked

    def _apply_line_btn_style(self, btn: QPushButton) -> None:
        display_font_css = get_display_font_css_stack()
        if btn.isChecked() and btn.isEnabled():
            btn.setStyleSheet(
                f"QPushButton {{ font-family: {display_font_css}; {active_green_style(config.theme)} }}"
                f"QPushButton:hover {{ background-color: {THEME_COLORS[config.theme]['status_success_bg_hover']}; }}"
            )
        else:
            btn.setStyleSheet(f"QPushButton {{ font-family: {display_font_css}; }}")

    def _on_config_changed(self) -> None:
        """连接状态下参数变更：立刻保存，延迟触发重连（200ms debounce）。"""
        if not self.open_btn.isChecked():
            return
        try:
            cfg = self.get_config()
        except (KeyError, TypeError, ValueError):
            return
        if cfg["port"] and cfg["port"] != "NULL":
            self._save_current_config()
            self._pending_config = cfg
            self._reconf_timer.start()

    def _do_reconfigure(self) -> None:
        """debounce 到期，发出重连请求。"""
        cfg = getattr(self, "_pending_config", None)
        if cfg is None:
            return
        del self._pending_config
        if self.open_btn.isChecked():
            self.reconfigure_requested.emit(cfg)

    def _save_current_config(self) -> None:
        """保存当前端口的配置参数到 UART 历史。"""
        port = self.port_combo.currentText()
        if not port or port == "NULL":
            return
        cfg = self.get_config()
        entry = {"note": self.note_edit.text().strip()}
        entry.update({
            "baudrate": cfg["baudrate"],
            "bytesize": cfg["bytesize"],
            "stopbits": cfg["stopbits"],
            "parity": cfg["parity"],
        })
        uart_history.save(port, entry)

    def _on_note_changed(self) -> None:
        """备注文本变更，200ms debounce 后自动保存。"""
        self._note_save_timer.start()

    def _do_save_note(self) -> None:
        port = self.port_combo.currentText()
        if not port or port == "NULL":
            return
        uart_history.save_note(port, self.note_edit.text().strip())

    def _on_port_changed(self, port: str) -> None:
        """串口号切换时加载已保存的配置和备注。"""
        self._update_port_tooltip()
        # 用户主动切换端口时先恢复正常样式，后续轮询按实际状态重新判定
        self.port_combo.setStyleSheet(
            f"QComboBox {{ font-family: {get_display_font_css_stack()}; }}"
        )
        if not port or port == "NULL":
            self.note_edit.clear()
            return
        # 加载备注
        note = uart_history.note(port)
        self.note_edit.setText(note)
        # 加载配置参数
        saved = uart_history.get(port)
        if saved:
            self._load_port_config(saved)
        # 端口切换也触发重配置检查（连接状态下）
        self._on_config_changed()

    def _load_port_config(self, saved: dict) -> None:
        """将已保存的配置参数设置到各下拉框（不触发 save 循环）。"""
        baud_str = str(saved.get("baudrate", ""))
        if baud_str and baud_str in [self.baud_combo.itemText(i) for i in range(self.baud_combo.count())]:
            self.baud_combo.setCurrentText(baud_str)
        elif baud_str:
            self.baud_combo.setCurrentText(baud_str)  # 自定义波特率，editable=True 直接设

        bytesize = saved.get("bytesize")
        if bytesize is not None:
            txt = str(bytesize)
            if txt in BYTESIZE_MAP:
                self.data_combo.setCurrentText(txt)

        stopbits = saved.get("stopbits")
        if stopbits is not None:
            txt = str(stopbits)
            if txt in STOPBITS_MAP:
                self.stop_combo.setCurrentText(txt)

        parity = saved.get("parity")
        if parity is not None:
            # 反向查找 PARITY_MAP
            for label, val in PARITY_MAP.items():
                if val == parity:
                    self.parity_combo.setCurrentText(label)
                    break

    def _on_toggle_expand(self, checked: bool) -> None:
        """动画展开/折叠高级参数行。"""
        self._expand_btn.setToolTip("折叠高级参数" if checked else "展开高级参数")
        old_anim = getattr(self, "_expand_anim", None)
        if old_anim is not None:
            old_anim.stop()
            self._expand_anim = None

        row = self._row2
        if checked:
            row.setMaximumHeight(0)
            row.setMinimumHeight(0)
            full_h = row.sizeHint().height()
            if full_h <= 0:
                full_h = 40
        else:
            row.setMinimumHeight(0)
            full_h = row.height()
            row.setMaximumHeight(full_h)

        target_h = full_h if checked else 0

        if ANIMATION_ENABLED:
            anim = QPropertyAnimation(row, b"maximumHeight")
            anim.setDuration(PANEL_ANIMATION_DURATION_MS)
            anim.setStartValue(row.maximumHeight())
            anim.setEndValue(target_h)
            anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._expand_anim = anim

            def _on_finished() -> None:
                self._expand_anim = None
                row.setMaximumHeight(16777215 if checked else 0)
                row.setMinimumHeight(0)

            anim.finished.connect(_on_finished)
            anim.start()
        else:
            row.setMaximumHeight(16777215 if checked else 0)
            row.setMinimumHeight(0)

    def _poll_ports(self) -> None:
        """定时轮询串口列表，检测插拔；不断开已连接的端口。"""
        ports = SerialWorker.list_ports()
        ports.sort(key=lambda p: _com_sort_key(p[1]))
        current_device = self.port_combo.currentText()
        current_items = {self.port_combo.itemText(i) for i in range(self.port_combo.count())}
        new_items = {p[1] for p in ports}
        if current_items == new_items:
            self._update_port_status_style(current_device, new_items)
            return
        # 临时阻断信号，避免刷新列表时触发 currentTextChanged → 重连
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self._port_descriptions.clear()
        if ports:
            for display, device in ports:
                self.port_combo.addItem(device)
                self._port_descriptions[device] = display
            if current_device and current_device != "NULL":
                if current_device in new_items:
                    self.port_combo.setCurrentText(current_device)
                else:
                    self.port_combo.setEditText(current_device)
        else:
            self.port_combo.addItem("NULL")
            if current_device and current_device != "NULL":
                self.port_combo.setEditText(current_device)
        self.port_combo.blockSignals(False)
        self._sync_popup_width()
        self._update_port_status_style(current_device, new_items)

    def _sync_popup_width(self) -> None:
        """下拉 popup 宽度 = max(控件宽度, 最宽描述文本 + 余量)。"""
        if not self._port_descriptions:
            return
        fm = self.port_combo.fontMetrics()
        combo_w = self.port_combo.width()
        desc_w = max(fm.horizontalAdvance(d) for d in self._port_descriptions.values())
        popup_w = max(combo_w, desc_w + 180)
        self.port_combo.set_popup_width(popup_w)

    def _update_port_tooltip(self) -> None:
        port = self.port_combo.currentText()
        desc = self._port_descriptions.get(port, "")
        text = desc if desc else port
        if hasattr(self, "_port_popup"):
            self._port_popup.close()
            self._port_popup.deleteLater()
        from gui.hover_popup import HoverPopup
        self._port_popup = HoverPopup(self.port_combo, text)

    def _update_port_status_style(self, current_device: str, available: set) -> None:
        """当前选中串口不在可用列表中时显示透明红色背景。"""
        if current_device and current_device != "NULL" and current_device not in available:
            self.port_combo.setStyleSheet(
                f"QComboBox {{ background-color: {THEME_COLORS[config.theme]['status_error_soft_bg']}; }}"
                f"QComboBox {{ font-family: {get_display_font_css_stack()}; }}"
            )
        else:
            self.port_combo.setStyleSheet(
                f"QComboBox {{ font-family: {get_display_font_css_stack()}; }}"
            )

    def refresh_port_status(self) -> None:
        """立即刷新串口号背景状态（供外部在断开事件时调用）。"""
        current = self.port_combo.currentText()
        available = {p[1] for p in SerialWorker.list_ports()}
        self._update_port_status_style(current, available)

    # ---------- 字体 ----------
    def set_display_font(self, family: str, size: int) -> None:
        from PySide6.QtGui import QFont
        font = QFont()
        apply_display_fallbacks(font, family if family else None)
        font.setPointSize(size)
        self.note_edit.setFont(font)
        # 统一所有下拉控件字体（含弹窗 view）
        self.port_combo.setFont(font)
        if self.port_combo.lineEdit() is not None:
            self.port_combo.lineEdit().setFont(font)
        v = self.port_combo.view()
        if v is not None:
            v.setFont(font)
        for cb in (self.baud_combo, self.data_combo, self.stop_combo, self.parity_combo):
            cb.setFont(font)
            le = cb.lineEdit()
            if le is not None:
                le.setFont(font)
            v = cb.view()
            if v is not None:
                v.setFont(font)
        self._sync_popup_width()
        # 刷新按钮样式以应用新字体
        self._apply_line_btn_style(self.cts_btn)
        self._apply_line_btn_style(self.rts_btn)
        self._apply_line_btn_style(self.tx_echo_chk)
        self._apply_line_btn_style(self.auto_reconnect_chk)
