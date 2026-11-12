"""扩展命令面板：多配置文件切换 + 多行快捷发送。

布局：
- 配置文件选择器（ComboBox 可编辑备注 + 新建/删除）
- 命令列表（QScrollArea，每行5列：文本 | HEX | CRLF | 发送(行号) | 删除）
- 底部添加行按钮

持久化：<config_dir>/cmd/index.json + cmd_{id}.json
当前选中配置ID 记忆在 index.json 的 current 字段
"""
import json

from PySide6.QtCore import QPoint, Qt, Signal, QTimer
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from config.app_config import config, get_config_dir, tx_history
from config.layout import (
    EXT_CMD_DRAG_LINE_HEIGHT,
    EXT_CMD_OUTER_LEFT_MARGIN,
    EXT_CMD_ROW_HEIGHT,
    EXT_CMD_VIEW_RIGHT_MARGIN,
)
from config.runtime import EXT_CMD_MAX_CONFIGS, EXT_CMD_MAX_ROWS, UI_DEBOUNCE_INTERVAL_MS
from config.theme import THEME_COLORS
from config.ui import EMOJI_BUTTON_TEXTS
from core.data_converter import append_crlf, bytes_to_hex
from gui.emoji_button import EmojiButton
from gui.rounded_scroll_bar import RoundedScrollBar, configure_combo_popup
from utils.font_manager import apply_display_fallbacks, get_display_font_css_stack


class DraggableSendButton(QPushButton):
    drag_started = Signal(QPoint)
    drag_moved = Signal(QPoint)
    drag_finished = Signal(QPoint)

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._press_pos: QPoint | None = None
        self._dragging = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._dragging and (
                event.position().toPoint() - self._press_pos
            ).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.drag_started.emit(self.mapToGlobal(event.position().toPoint()))
            if self._dragging:
                self.drag_moved.emit(self.mapToGlobal(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self.drag_finished.emit(self.mapToGlobal(event.position().toPoint()))
            self._press_pos = None
            self._dragging = False
            return
        self._press_pos = None
        super().mouseReleaseEvent(event)


class ExtCmdPanel(QWidget):
    MAX_CONFIGS = EXT_CMD_MAX_CONFIGS
    MAX_ROWS = EXT_CMD_MAX_ROWS
    SAVE_DEBOUNCE_MS = UI_DEBOUNCE_INTERVAL_MS

    send_requested = Signal(bytes, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cmd_dir = get_config_dir() / "cmd"
        self._cmd_dir.mkdir(parents=True, exist_ok=True)
        self._loaded = False
        self._loading = False
        self._pending_items: list[dict] = []
        self._pending_item_index = 0
        self._display_font: tuple[str, int] | None = None
        self._configs: list[dict] = []  # [{"id": 1, "note": ""}, ...]
        self._current_idx = 0
        self._rows: list[dict] = []  # [{input, hex_btn, crlf_btn, send_btn, del_btn, row_widget}]
        self._row_pool: list[dict] = []
        self._drag_source_row: dict | None = None
        self._drag_target_slot: int | None = None
        self._drag_line: QFrame | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(self.SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._do_save_config)
        self._ghost_refresh_timer = QTimer(self)
        self._ghost_refresh_timer.setSingleShot(True)
        self._ghost_refresh_timer.timeout.connect(self._refresh_row_crlf_ghosts)
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._load_next_row)
        self._build_ui()

    def ensure_loaded(self) -> None:
        if self._loaded or self._loading:
            return
        self._loading = True
        self._load_index()

    def _set_loading_ui(self, loading: bool) -> None:
        """加载期间锁定配置操作，避免半成品状态被用户修改。"""
        self._cfg_combo.setEnabled(not loading)
        if loading:
            self._cfg_add_btn.setEnabled(False)
            self._cfg_del_btn.setEnabled(False)
        else:
            self._update_cfg_buttons()
        self._add_row_btn.setEnabled(not loading)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(EXT_CMD_OUTER_LEFT_MARGIN, 0, 0, 0)
        outer.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("extCmdFrame")
        v = QVBoxLayout(frame)
        v.setContentsMargins(0, 2, EXT_CMD_VIEW_RIGHT_MARGIN, 2)
        v.setSpacing(4)

        # 配置文件选择器行
        cfg_row = QHBoxLayout()
        cfg_row.setSpacing(4)
        display_font_css = get_display_font_css_stack()
        self._cfg_combo = QComboBox()
        self._cfg_combo.setEditable(True)
        self._cfg_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        configure_combo_popup(self._cfg_combo)
        self._cfg_combo.setStyleSheet(f"QComboBox {{ font-family: {display_font_css}; }}")
        self._cfg_combo.lineEdit().editingFinished.connect(self._on_combo_edit_finished)
        self._cfg_combo.currentIndexChanged.connect(self._on_combo_changed)
        cfg_row.addWidget(self._cfg_combo, stretch=1)

        self._cfg_add_btn = EmojiButton(EMOJI_BUTTON_TEXTS["cfg_add"], size=28)
        self._cfg_add_btn.setToolTip("新建配置文件")
        self._cfg_add_btn.clicked.connect(self._add_config)
        cfg_row.addWidget(self._cfg_add_btn)

        self._cfg_del_btn = EmojiButton(EMOJI_BUTTON_TEXTS["cfg_del"], size=28)
        self._cfg_del_btn.setToolTip("删除当前配置文件")
        self._cfg_del_btn.clicked.connect(self._del_config)
        cfg_row.addWidget(self._cfg_del_btn)
        v.addLayout(cfg_row)

        # 命令行滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBar(RoundedScrollBar(self._scroll))
        self._scroll.setStyleSheet("QScrollArea { border: none; }")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)

        # 添加行按钮：放在滚动内容末尾，始终跟随最后一条命令下方
        self._add_row_btn = EmojiButton(EMOJI_BUTTON_TEXTS["row_add"], size=38)
        self._add_row_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._add_row_btn.setToolTip("添加命令行")
        self._add_row_btn.clicked.connect(self.add_row)
        self._rows_layout.addWidget(self._add_row_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._rows_layout.addStretch()

        self._scroll.setWidget(self._rows_container)
        v.addWidget(self._scroll, stretch=1)

        self._drag_line = QFrame(self._rows_container)
        self._drag_line.setFrameShape(QFrame.Shape.HLine)
        self._drag_line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._drag_line.hide()

        outer.addWidget(frame)

    # ---------- 索引加载 ----------
    def _load_index(self) -> None:
        idx_path = self._cmd_dir / "index.json"
        stored_current_id = 0
        index_needs_save = not idx_path.exists()
        if idx_path.exists():
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not isinstance(data.get("files", []), list):
                    raise ValueError("invalid command index")
                self._configs = data.get("files", [])
                stored_current_id = data.get("current", 0)
                for c in self._configs:
                    if not isinstance(c, dict):
                        raise ValueError("invalid command config")
                    if "id" not in c:
                        c["id"] = 1
                        index_needs_save = True
                    if "note" not in c:
                        c["note"] = ""
                        index_needs_save = True
            except (json.JSONDecodeError, OSError, ValueError):
                self._configs = []
                index_needs_save = True
        if not self._configs:
            self._configs = [{"id": 1, "note": ""}]
            stored_current_id = 1
            self._write_config_file(1, [])
            index_needs_save = True
        # 按 stored_current_id 定位当前配置
        found_idx = -1
        if stored_current_id > 0:
            for i, c in enumerate(self._configs):
                if c["id"] == stored_current_id:
                    found_idx = i
                    break
        self._current_idx = found_idx if found_idx >= 0 else 0
        if stored_current_id != self._configs[self._current_idx]["id"]:
            index_needs_save = True
        self._refresh_combo()
        if index_needs_save:
            self._save_index()
        self._begin_async_config_load(self._current_idx)

    def _save_index(self) -> None:
        idx_path = self._cmd_dir / "index.json"
        current_id = self._configs[self._current_idx]["id"]
        data = {"current": current_id, "files": self._configs}
        self._cmd_dir.mkdir(parents=True, exist_ok=True)
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ---------- 配置切换 ----------
    def _refresh_combo(self) -> None:
        self._cfg_combo.blockSignals(True)
        self._cfg_combo.clear()
        for c in self._configs:
            label = c["note"] if c["note"] else f"配置文件{c['id']}"
            self._cfg_combo.addItem(label)
        if 0 <= self._current_idx < len(self._configs):
            self._cfg_combo.setCurrentIndex(self._current_idx)
        self._cfg_combo.blockSignals(False)
        self._update_cfg_buttons()

    def _on_combo_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._configs):
            return
        if index == self._current_idx:
            return
        self._switch_config(index)

    def _switch_config(self, combo_idx: int) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._do_save_config()
        self._current_idx = combo_idx
        self._load_config(combo_idx)
        self._save_index()

    def _on_combo_edit_finished(self) -> None:
        if 0 <= self._current_idx < len(self._configs):
            text = self._cfg_combo.lineEdit().text().strip()
            self._configs[self._current_idx]["note"] = text
            self._save_index()
            self._refresh_combo()

    def _update_cfg_buttons(self) -> None:
        self._cfg_add_btn.setEnabled(len(self._configs) < self.MAX_CONFIGS)
        if len(self._configs) <= 1:
            self._cfg_del_btn.setEnabled(True)
            self._cfg_del_btn.setToolTip("重置当前配置文件")
        else:
            self._cfg_del_btn.setEnabled(True)
            self._cfg_del_btn.setToolTip("删除当前配置文件")

    # ---------- 配置增删 ----------
    def _add_config(self) -> None:
        if len(self._configs) >= self.MAX_CONFIGS:
            return
        max_id = max((c["id"] for c in self._configs), default=0)
        new_id = max_id + 1
        self._configs.append({"id": new_id, "note": ""})
        self._save_index()
        self._do_save_config()
        self._current_idx = len(self._configs) - 1
        self._load_config(self._current_idx)
        self._refresh_combo()

    def _del_config(self) -> None:
        # 仅剩一个配置 → 删除文件，恢复默认 id=1
        if len(self._configs) <= 1:
            cid = self._configs[self._current_idx]["id"]
            cfg_path = self._cmd_dir / f"cmd_{cid}.json"
            try:
                cfg_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._configs = [{"id": 1, "note": ""}]
            self._current_idx = 0
            self._save_index()
            self._write_config_file(1, [])
            self._load_config(0)
            self._refresh_combo()
            return
        cid = self._configs[self._current_idx]["id"]
        cfg_path = self._cmd_dir / f"cmd_{cid}.json"
        try:
            cfg_path.unlink(missing_ok=True)
        except OSError:
            pass
        del self._configs[self._current_idx]
        if self._current_idx >= len(self._configs):
            self._current_idx = len(self._configs) - 1
        self._save_index()
        self._load_config(self._current_idx)
        self._refresh_combo()

    # ---------- 命令行加载/保存 ----------
    def _recycle_row(self, row: dict) -> None:
        """从布局剥离并留给下一次配置切换复用。"""
        row["row_widget"].hide()
        self._rows_layout.removeWidget(row["row_widget"])
        row["row_widget"].setParent(self)
        self._row_pool.append(row)

    def _clear_rows(self) -> None:
        for row in self._rows:
            self._recycle_row(row)
        self._rows.clear()

    def _read_config_items(self, config_idx: int) -> list[dict]:
        cid = self._configs[config_idx]["id"]
        cfg_path = self._cmd_dir / f"cmd_{cid}.json"
        if not cfg_path.exists():
            return []
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def _finish_config_load(self) -> None:
        self._renumber()
        self._update_add_row_btn()
        self._update_del_buttons()
        self._nudge_scroll()
        self._ghost_refresh_timer.start()
        if self._display_font is not None:
            self.set_display_font(*self._display_font)

    def _begin_async_config_load(self, config_idx: int) -> None:
        self._load_timer.stop()
        self._clear_rows()
        self._pending_items = self._read_config_items(config_idx)
        self._pending_item_index = 0
        self._loaded = False
        self._loading = True
        self._set_loading_ui(True)
        self._load_timer.start(0)

    def _load_next_row(self) -> None:
        if not self._loading:
            return

        if self._pending_item_index >= len(self._pending_items):
            self._pending_items = []
            self._pending_item_index = 0
            self._loading = False
            self._loaded = True
            self._set_loading_ui(False)
            self._finish_config_load()
            return

        self._rows_container.setUpdatesEnabled(False)
        try:
            item = self._pending_items[self._pending_item_index]
            self._add_row_widget(
                text=item.get("text", ""),
                hex_checked=item.get("hex", False),
                crlf_checked=item.get("crlf", True),
            )
            self._pending_item_index += 1
        finally:
            self._rows_container.setUpdatesEnabled(True)

        # 逐行加载时按当前行的实际几何位置立即刷新，避免复用旧行的幽灵文本残留。
        self._rows_layout.activate()
        self._update_row_crlf_ghost(self._rows[-1])

        if self._pending_item_index < len(self._pending_items):
            self._load_timer.start(0)
            return

        self._pending_items = []
        self._pending_item_index = 0
        self._loading = False
        self._loaded = True
        self._set_loading_ui(False)
        self._finish_config_load()

    def _load_config(self, config_idx: int) -> None:
        """切换配置时逐行异步加载，避免一次性创建全部控件。"""
        self._begin_async_config_load(config_idx)

    def _do_save_config(self) -> None:
        if self._current_idx < 0 or self._current_idx >= len(self._configs):
            return
        cid = self._configs[self._current_idx]["id"]
        data = []
        for row in self._rows:
            data.append({
                "text": row["input"].text(),
                "hex": row["hex_btn"].isChecked(),
                "crlf": row["crlf_btn"].isChecked(),
            })
        self._write_config_file(cid, data)

    def _write_config_file(self, config_id: int, data: list[dict]) -> None:
        cfg_path = self._cmd_dir / f"cmd_{config_id}.json"
        self._cmd_dir.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _schedule_save(self) -> None:
        self._save_timer.start()

    # ---------- 命令行控件 ----------
    @staticmethod
    def _hex_clean(text: str) -> str:
        return "".join(ch for ch in text.upper() if ch in "0123456789ABCDEF")

    @classmethod
    def _hex_format_pairs(cls, text: str) -> str:
        cleaned = cls._hex_clean(text)
        return " ".join(cleaned[i:i + 2] for i in range(0, len(cleaned), 2))

    @classmethod
    def _hex_to_bytes_lenient(cls, text: str) -> bytes:
        cleaned = cls._hex_clean(text)
        if not cleaned:
            return b""
        if len(cleaned) % 2:
            cleaned = "0" + cleaned
        return bytes.fromhex(cleaned)

    @classmethod
    def _hex_normalize_for_send(cls, text: str) -> tuple[str, bytes]:
        cleaned = cls._hex_clean(text)
        if not cleaned:
            return "", b""
        if len(cleaned) % 2:
            cleaned = cleaned[:-1] + "0" + cleaned[-1]
        normalized = cls._hex_format_pairs(cleaned)
        return normalized, bytes.fromhex(cleaned)

    @classmethod
    def _map_hex_cursor_pos(cls, raw_text: str, normalized_text: str, old_pos: int) -> int:
        old_pos = max(0, min(old_pos, len(raw_text)))
        nibble_count = len(cls._hex_clean(raw_text[:old_pos]))
        total_nibbles = len(cls._hex_clean(normalized_text))
        nibble_count = min(nibble_count, total_nibbles)
        if nibble_count <= 0:
            return 0
        return nibble_count + (nibble_count - 1) // 2

    def _set_row_text(self, row: dict, text: str, cursor_pos: int | None = None) -> None:
        row["syncing"] = True
        row["input"].setText(text)
        if cursor_pos is None:
            row["input"].setCursorPosition(len(text))
        else:
            row["input"].setCursorPosition(max(0, min(cursor_pos, len(text))))
        row["syncing"] = False

    def _on_row_text_changed(self, row: dict) -> None:
        if row.get("syncing", False):
            return
        if row["hex_btn"].isChecked():
            raw = row["input"].text()
            old_pos = row["input"].cursorPosition()
            normalized = self._hex_format_pairs(raw)

            if normalized != raw:
                new_pos = self._map_hex_cursor_pos(raw, normalized, old_pos)
                self._set_row_text(row, normalized, cursor_pos=new_pos)
        self._schedule_save()

    def _on_row_hex_toggled(self, row: dict, checked: bool) -> None:
        if row.get("suppress_hex_transform", False):
            self._schedule_save()
            return
        text = row["input"].text()
        if checked:
            if text:
                encoded = text.encode(config.tx_encoding, errors="replace")
                self._set_row_text(row, bytes_to_hex(encoded))
        else:
            if text:
                raw = self._hex_to_bytes_lenient(text)
                self._set_row_text(row, raw.decode(config.tx_encoding, errors="replace"))
        self._schedule_save()

    def _add_row_widget(self, text: str = "", hex_checked: bool = False, crlf_checked: bool = True) -> None:
        if self._row_pool:
            row_data = self._row_pool.pop()
            inp = row_data["input"]
            hex_btn = row_data["hex_btn"]
            crlf_btn = row_data["crlf_btn"]
            inp.blockSignals(True)
            hex_btn.blockSignals(True)
            crlf_btn.blockSignals(True)
            inp.setText(text)
            hex_btn.setChecked(hex_checked)
            crlf_btn.setChecked(crlf_checked)
            inp.blockSignals(False)
            hex_btn.blockSignals(False)
            crlf_btn.blockSignals(False)
            hex_btn._refresh_style()
            crlf_btn._refresh_style()
            # Qt 在屏蔽信号期间恢复文本时，可能不会刷新 QLineEdit 的清空按钮可见状态。
            inp.setClearButtonEnabled(False)
            inp.setClearButtonEnabled(True)
            row_data["syncing"] = False
            row_data["suppress_hex_transform"] = False
            row_data["drag_overlay"].hide()
            row_data["ghost_lbl"].hide()
            self._update_row_crlf_tooltip(row_data)
        else:
            row_data = self._create_row_widget(text, hex_checked, crlf_checked)

        insert_idx = self._rows_layout.indexOf(self._add_row_btn)
        self._rows_layout.insertWidget(insert_idx, row_data["row_widget"])
        self._rows.append(row_data)
        row_data["send_btn"].setText(str(len(self._rows)))
        row_data["row_widget"].show()
        self._sync_row_ghost_font(row_data)
        if not self._loading:
            self._ghost_refresh_timer.start()

    def _create_row_widget(self, text: str, hex_checked: bool, crlf_checked: bool) -> dict:
        row_widget = QWidget()
        row_widget.setObjectName("extCmdRow")
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(3)

        inp = QLineEdit()
        inp.setObjectName("extCmdInput")
        inp.setFrame(False)
        inp.setFixedHeight(EXT_CMD_ROW_HEIGHT)
        inp.setClearButtonEnabled(True)
        inp.setText(text)
        row_layout.addWidget(inp, stretch=1)

        send_btn = DraggableSendButton("0")
        send_btn.setFixedSize(EXT_CMD_ROW_HEIGHT, EXT_CMD_ROW_HEIGHT)
        row_layout.addWidget(send_btn)

        hex_btn = EmojiButton(EMOJI_BUTTON_TEXTS["hex_mode"], checkable=True, size=32)
        hex_btn.setChecked(hex_checked)
        hex_btn.toggled.connect(lambda v, b=hex_btn: b.setToolTip("HEX发送: 启用" if v else "HEX发送: 禁用"))
        hex_btn.setToolTip("HEX发送: 启用" if hex_checked else "HEX发送: 禁用")
        row_layout.addWidget(hex_btn)

        crlf_btn = EmojiButton(EMOJI_BUTTON_TEXTS["crlf_mode"], checkable=True, size=32)
        crlf_btn.setChecked(crlf_checked)
        row_layout.addWidget(crlf_btn)

        del_btn = EmojiButton(EMOJI_BUTTON_TEXTS["row_delete"], size=32)
        del_btn.setToolTip("删除命令行")
        del_btn.clicked.connect(lambda: self.remove_row(row_widget))
        row_layout.addWidget(del_btn)

        row_data = {
            "input": inp,
            "hex_btn": hex_btn,
            "crlf_btn": crlf_btn,
            "send_btn": send_btn,
            "del_btn": del_btn,
            "row_widget": row_widget,
            "syncing": False,
            "suppress_hex_transform": False,
        }
        send_btn.clicked.connect(lambda _checked=False, r=row_data: self._on_send_row(r))

        drag_overlay = QFrame(row_widget)
        drag_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        drag_overlay.hide()
        row_data["drag_overlay"] = drag_overlay

        send_btn.drag_started.connect(lambda pos, r=row_data: self._start_row_drag(r, pos))
        send_btn.drag_moved.connect(self._move_row_drag)
        send_btn.drag_finished.connect(self._finish_row_drag)

        ghost_lbl = QLabel("\\r\\n", inp)
        ghost_lbl.setObjectName("crlfGhost")
        ghost_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        ghost_lbl.hide()
        row_data["ghost_lbl"] = ghost_lbl

        inp.textChanged.connect(lambda _t, r=row_data: self._on_row_text_changed(r))
        inp.textChanged.connect(lambda _t, r=row_data: self._update_row_crlf_ghost(r))
        hex_btn.toggled.connect(lambda checked, r=row_data: self._on_row_hex_toggled(r, checked))
        hex_btn.toggled.connect(lambda _checked, r=row_data: self._update_row_crlf_ghost(r))
        crlf_btn.toggled.connect(lambda _checked, r=row_data: self._update_row_crlf_tooltip(r))
        crlf_btn.toggled.connect(lambda _checked, r=row_data: self._update_row_crlf_ghost(r))
        self._update_row_crlf_tooltip(row_data)

        # 连接信号更新样式和保存
        hex_btn.toggled.connect(lambda: self._schedule_save())
        crlf_btn.toggled.connect(lambda: self._schedule_save())
        return row_data

    def _refresh_row_crlf_ghosts(self) -> None:
        for row in self._rows:
            self._update_row_crlf_ghost(row)

    def add_row(self) -> None:
        if len(self._rows) >= self.MAX_ROWS:
            return
        self._add_row_widget()
        self._renumber()
        self._update_add_row_btn()
        self._update_del_buttons()
        self._schedule_save()
        self._nudge_scroll()
        QTimer.singleShot(0, self._ensure_add_btn_clickable)

    def remove_row(self, row_widget: QWidget) -> None:
        if self._drag_source_row is not None:
            self._finish_row_drag(row_widget.mapToGlobal(QPoint(0, 0)))
        for i, row in enumerate(self._rows):
            if row["row_widget"] is row_widget:
                self._recycle_row(row)
                del self._rows[i]
                break
        self._renumber()
        self._update_add_row_btn()
        self._update_del_buttons()
        self._schedule_save()

    def _drag_color(self) -> str:
        return THEME_COLORS.get(config.theme, THEME_COLORS["dark"])["highlight"]

    def _start_row_drag(self, row: dict, global_pos: QPoint) -> None:
        if row not in self._rows or len(self._rows) < 2:
            return
        self._drag_source_row = row
        overlay = row["drag_overlay"]
        overlay.setGeometry(row["row_widget"].rect())
        overlay.setStyleSheet(
            f"QFrame {{ border: 2px solid {self._drag_color()}; border-radius: 4px; background: transparent; }}"
        )
        overlay.show()
        overlay.raise_()
        self._drag_line.setStyleSheet(f"QFrame {{ border: none; background: {self._drag_color()}; min-height: 3px; max-height: 3px; }}")
        self._move_row_drag(global_pos)

    def _drag_slot_at(self, global_pos: QPoint) -> int:
        local_pos = self._rows_container.mapFromGlobal(global_pos)
        for index, row in enumerate(self._rows):
            widget = row["row_widget"]
            if local_pos.y() < widget.geometry().center().y():
                return index
        return len(self._rows)

    def _move_row_drag(self, global_pos: QPoint) -> None:
        if self._drag_source_row is None:
            return
        slot = self._drag_slot_at(global_pos)
        self._drag_target_slot = slot
        line_y = self._add_row_btn.geometry().top() - 2
        if slot < len(self._rows):
            line_y = self._rows[slot]["row_widget"].geometry().top() - 2
        self._drag_line.setGeometry(0, max(0, line_y), self._rows_container.width(), EXT_CMD_DRAG_LINE_HEIGHT)
        self._drag_line.show()
        self._drag_line.raise_()

    def _finish_row_drag(self, global_pos: QPoint) -> None:
        if self._drag_source_row is None:
            return
        self._move_row_drag(global_pos)
        source_row = self._drag_source_row
        source_index = self._rows.index(source_row)
        target_slot = self._drag_target_slot
        self._clear_row_drag()
        if target_slot is None:
            return
        target_index = target_slot - (1 if target_slot > source_index else 0)
        if target_index == source_index:
            return
        self._rows.pop(source_index)
        self._rows.insert(max(0, min(target_index, len(self._rows))), source_row)
        for row in self._rows:
            self._rows_layout.removeWidget(row["row_widget"])
        for index, row in enumerate(self._rows):
            self._rows_layout.insertWidget(index, row["row_widget"])
        self._renumber()
        self._schedule_save()
        self._nudge_scroll()

    def _clear_row_drag(self) -> None:
        if self._drag_source_row is not None:
            self._drag_source_row["drag_overlay"].hide()
        self._drag_source_row = None
        self._drag_target_slot = None
        self._drag_line.hide()

    def _renumber(self) -> None:
        for i, row in enumerate(self._rows):
            row["send_btn"].setText(str(i + 1))

    def _update_del_buttons(self) -> None:
        enabled = len(self._rows) > 0
        for row in self._rows:
            row["del_btn"].setEnabled(enabled)

    def _update_add_row_btn(self) -> None:
        self._add_row_btn.setEnabled(len(self._rows) < self.MAX_ROWS)

    # ---------- 发送 ----------
    def _on_send_row(self, row: dict) -> None:
        if row in self._rows:
            self._on_send(self._rows.index(row))

    def _on_send(self, row_index: int) -> None:
        if row_index < 0 or row_index >= len(self._rows):
            return
        row = self._rows[row_index]
        text = row["input"].text()
        original_text = text
        le_bytes = config.tx_line_ending.encode("ascii")
        if row["hex_btn"].isChecked():
            if not text.strip():
                data = le_bytes
            else:
                normalized, data = self._hex_normalize_for_send(text)
                if normalized and normalized != text:
                    self._set_row_text(row, normalized)
                    self._update_row_crlf_ghost(row)
                    QTimer.singleShot(0, lambda r=row: self._update_row_crlf_ghost(r))
                    self._schedule_save()
                if row["crlf_btn"].isChecked():
                    data += le_bytes
        else:
            if not text:
                if row["crlf_btn"].isChecked():
                    data = le_bytes
                else:
                    return
            else:
                if row["crlf_btn"].isChecked():
                    text = append_crlf(text, config.tx_line_ending)
                data = text.encode(config.tx_encoding, errors="replace")
        if data:
            self.send_requested.emit(data, row["hex_btn"].isChecked())
            tx_history.add(original_text, row["hex_btn"].isChecked(), row["crlf_btn"].isChecked())

    # ---------- 样式 ----------
    @staticmethod
    def _line_ending_text() -> str:
        return config.tx_line_ending.replace("\r", "\\r").replace("\n", "\\n")

    def _update_row_crlf_tooltip(self, row: dict) -> None:
        btn = row.get("crlf_btn")
        if btn is None:
            return
        le_display = self._line_ending_text()
        btn.setToolTip(f"追加{le_display}: 启用" if btn.isChecked() else f"追加{le_display}: 禁用")

    def refresh_line_ending_ui(self) -> None:
        for row in self._rows:
            self._update_row_crlf_tooltip(row)
            self._update_row_crlf_ghost(row)

    def _sync_row_ghost_font(self, row: dict) -> None:
        ghost = row.get("ghost_lbl")
        if ghost is not None:
            ghost.setFont(row["input"].font())

    def _update_row_crlf_ghost(self, row: dict) -> None:
        ghost = row.get("ghost_lbl")
        if ghost is None:
            return

        if not row["crlf_btn"].isChecked():
            ghost.hide()
            return

        le = config.tx_line_ending
        ghost.setText(" " + " ".join(f"{b:02X}" for b in le.encode("ascii")) if row["hex_btn"].isChecked() else le.replace("\r", "\\r").replace("\n", "\\n"))
        self._sync_row_ghost_font(row)
        ghost.adjustSize()

        inp = row["input"]
        content = inp.textMargins()
        fm = inp.fontMetrics()
        text_w = fm.horizontalAdvance(inp.displayText())

        lw = ghost.sizeHint().width()
        lh = ghost.sizeHint().height()
        x = content.left() + text_w + 5
        y = max(0, (inp.height() - lh) // 2) + 1

        # ponytail: 输入文本+幽灵文本总宽度超出可见区时，隐藏幽灵文本避免覆盖
        total_w = text_w + 5 + lw + content.right()
        if total_w > inp.width():
            ghost.hide()
            return

        ghost.setGeometry(x, y, lw, lh)
        ghost.show()
        ghost.raise_()

    def _nudge_scroll(self) -> None:
        """触发布局重算，让滚动条范围与当前命令行数量同步。"""
        self._rows_container.adjustSize()
        self._scroll.updateGeometry()
        vbar = self._scroll.verticalScrollBar()
        if vbar is not None:
            vbar.show()
            vbar.update()

    def _ensure_add_btn_clickable(self) -> None:
        """确保添加按钮在可视区域内，避免新增到末尾后落到不可点击区域。"""
        vbar = self._scroll.verticalScrollBar()
        if vbar is not None:
            vbar.setValue(vbar.maximum())
        self._scroll.ensureWidgetVisible(self._add_row_btn, 0, 6)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._nudge_scroll)
        QTimer.singleShot(0, self._update_all_row_ghosts)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_all_row_ghosts)

    def _update_all_row_ghosts(self) -> None:
        for row in self._rows:
            self._update_row_crlf_ghost(row)

    # ---------- 字体 ----------
    def set_display_font(self, family: str, size: int) -> None:
        """设置所有文本输入控件的显示字体（cfg_combo + 各行 QLineEdit）。"""
        self._display_font = (family, size)
        font = QFont()
        apply_display_fallbacks(font, family if family else None)
        font.setPointSize(size)
        self._cfg_combo.setFont(font)
        if self._cfg_combo.lineEdit():
            self._cfg_combo.lineEdit().setFont(font)
        for row in self._rows:
            row["input"].setFont(font)
            self._sync_row_ghost_font(row)
            self._update_row_crlf_ghost(row)
