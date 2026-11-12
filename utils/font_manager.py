"""字体管理：全局字体加载与应用。

启动时从 resources/fonts/ 加载内置 Hack 等宽字体,
确保在任意 PC 上均可使用设计字体。缓存默认 QFont 对象供全局复用。

UI 显示字体统一使用 Hack，中文回退 WenYuan Serif SC。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from config.ui import (
    CJK_PRIMARY_FONT_FILE,
    DEFAULT_DISPLAY_FONT_SIZE_PT,
    DISPLAY_FONT_FILE,
    FONT_SIZE_MAX_PT,
    FONT_SIZE_MIN_PT,
    FONT_FALLBACK_DEFAULT_FAMILIES,
    FONT_FALLBACK_FAMILIES,
)
from config.layout import FONT_DIALOG_MIN_WIDTH
from gui.themed_dialog import ThemedDialog

# 回退链默认值（当 ui 配置为空时兜底）
_DEFAULT_FALLBACKS = [name.strip() for name in FONT_FALLBACK_DEFAULT_FAMILIES if isinstance(name, str) and name.strip()]
if not _DEFAULT_FALLBACKS:
    _DEFAULT_FALLBACKS = ["Hack", "WenYuan Serif SC"]
_CONFIGURED_FALLBACKS = [name.strip() for name in FONT_FALLBACK_FAMILIES if isinstance(name, str) and name.strip()]
_FONT_FALLBACKS = _CONFIGURED_FALLBACKS or _DEFAULT_FALLBACKS
_INCLUDE_EMOJI_IN_DISPLAY_FALLBACK = len(_FONT_FALLBACKS) > 2

# 统一显示字体
DISPLAY_FAMILY = _FONT_FALLBACKS[0]
# 中文回退字体（主回退：resources/fonts/WenYuanSerifSC-Regular.ttf）
CJK_FAMILY = _FONT_FALLBACKS[1] if len(_FONT_FALLBACKS) > 1 else (DISPLAY_FAMILY if not _DEFAULT_FALLBACKS else _DEFAULT_FALLBACKS[min(1, len(_DEFAULT_FALLBACKS) - 1)])
# emoji 显示字体（对应 resources/fonts/NotoColorEmoji-Regular.ttf）
EMOJI_FAMILY = _FONT_FALLBACKS[2] if len(_FONT_FALLBACKS) > 2 else "Noto Color Emoji"
EXTRA_FALLBACK_FAMILIES = _FONT_FALLBACKS[3:]
# 默认字体大小（点大小，而非像素）
DEFAULT_FONT_SIZE = DEFAULT_DISPLAY_FONT_SIZE_PT
# 内置字体目录相对于项目根
_FONTS_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"
_DISPLAY_FONT_FILE = DISPLAY_FONT_FILE.upper()
_CJK_PRIMARY_FONT_FILE = CJK_PRIMARY_FONT_FILE.upper()

# 缓存默认 QFont，避免反复构造
_DEFAULT_FONT: QFont | None = None
_RESOLVED_DISPLAY_FAMILY = DISPLAY_FAMILY
_RESOLVED_CJK_FAMILY = CJK_FAMILY
_RESOLVED_EMOJI_FAMILY = EMOJI_FAMILY


def _display_fallback_families(primary_family: str | None = None) -> list[str]:
    """显示字体族优先级：主字体 -> 中文 -> emoji -> 额外回退。"""
    primary = primary_family or _RESOLVED_DISPLAY_FAMILY
    if primary and primary.lower() == DISPLAY_FAMILY.lower():
        primary = _RESOLVED_DISPLAY_FAMILY
    families: list[str] = []
    known = {name.lower(): name for name in QFontDatabase.families()}
    for name in (
        primary,
        _RESOLVED_CJK_FAMILY,
        _RESOLVED_EMOJI_FAMILY if _INCLUDE_EMOJI_IN_DISPLAY_FALLBACK else "",
        *EXTRA_FALLBACK_FAMILIES,
    ):
        if not name:
            continue
        hit = known.get(name.lower(), name)
        if hit not in families:
            families.append(hit)
    return families


def get_display_font_css_stack(primary_family: str | None = None) -> str:
    """返回可直接用于 stylesheet 的字体回退链字符串。"""

    def _css_quote(name: str) -> str:
        return f'"{name}"' if any(ch in name for ch in (" ", "-")) else name

    return ", ".join(_css_quote(name) for name in _display_fallback_families(primary_family))


def get_display_font_families(primary_family: str | None = None) -> list[str]:
    """返回运行时可用的显示字体族回退链。"""
    return _display_fallback_families(primary_family)


def get_emoji_family_name() -> str:
    """返回当前运行时可用的 emoji 字体族名。"""
    known = {name.lower(): name for name in QFontDatabase.families()}
    return known.get(_RESOLVED_EMOJI_FAMILY.lower(), _RESOLVED_EMOJI_FAMILY)


def apply_display_fallbacks(font: QFont, primary_family: str | None = None) -> None:
    """为 QFont 注入主字体/中文/emoji 回退链。"""
    families = _display_fallback_families(primary_family)
    # Qt6 提供 setFamilies，可显式指定回退链；旧接口则退化到 setFamily。
    if hasattr(font, "setFamilies"):
        font.setFamilies(families)
    elif families:
        font.setFamily(families[0])


def _build_default_font() -> QFont:
    """构造默认显示字号的 QFont。"""
    font = QFont()
    apply_display_fallbacks(font, DISPLAY_FAMILY)
    font.setPointSize(DEFAULT_FONT_SIZE)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def get_default_font() -> QFont:
    """返回缓存的默认 QFont（主显示字体 + 默认字号）。"""
    global _DEFAULT_FONT
    if _DEFAULT_FONT is None:
        _DEFAULT_FONT = _build_default_font()
    return _DEFAULT_FONT


def get_display_font(size: int) -> QFont:
    """返回指定字号的主显示字体 QFont。"""
    font = QFont()
    apply_display_fallbacks(font, DISPLAY_FAMILY)
    font.setPointSize(size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def get_emoji_font(size: int | None = None) -> QFont:
    """返回 emoji 专用字体。"""
    font = QFont()
    families = {name.lower(): name for name in QFontDatabase.families()}
    hit = families.get(EMOJI_FAMILY.lower())
    if hit:
        font.setFamily(hit)
    if size is not None and size > 0:
        font.setPointSize(size)
    return font


def apply_emoji_font(widget: QWidget, size: int | None = None) -> None:
    """将 emoji 专用字体应用到指定控件。"""
    base_size = widget.font().pointSize()
    point_size = size if size is not None else (base_size if base_size > 0 else None)
    widget.setFont(get_emoji_font(point_size))


def load_bundled_fonts() -> list[str]:
    """加载 resources/fonts/ 下所有 .ttf/.otf 文件到 Qt 字体数据库。

    DISPLAY_FONT_FILE 对应字体优先加载以保证主显示字体可用。返回加载成功的字体 family 列表。
    必须在 QApplication 构造后调用。
    """
    global _RESOLVED_DISPLAY_FAMILY, _RESOLVED_CJK_FAMILY, _RESOLVED_EMOJI_FAMILY, _DEFAULT_FONT
    loaded: list[str] = []
    if not _FONTS_DIR.is_dir():
        return loaded
    files = [fp for fp in _FONTS_DIR.iterdir() if fp.suffix.lower() in (".ttf", ".otf", ".ttc")]
    # 优先加载 display/cjk 两类配置字体，再加载其余字体。
    files.sort(key=lambda p: (
        0 if p.name.upper() == _DISPLAY_FONT_FILE else 1 if p.name.upper() == _CJK_PRIMARY_FONT_FILE else 2,
        p.name.lower(),
    ))
    for fp in files:
        if fp.suffix.lower() in (".ttf", ".otf", ".ttc"):
            fid = QFontDatabase.addApplicationFont(str(fp))
            if fid >= 0:
                families = QFontDatabase.applicationFontFamilies(fid)
                loaded.extend(families)
                if families:
                    if fp.name.upper() == _DISPLAY_FONT_FILE:
                        _RESOLVED_DISPLAY_FAMILY = next((name for name in families if name.lower() == DISPLAY_FAMILY.lower()), families[0])

    # ponytail: 按 FONT_FALLBACK_FAMILIES 配置解析 CJK 族，不从 CJK_PRIMARY_FONT_FILE 推导。
    known_families = {name.lower(): name for name in QFontDatabase.families()}
    _RESOLVED_CJK_FAMILY = known_families.get(CJK_FAMILY.lower(), CJK_FAMILY)
    if len(_FONT_FALLBACKS) > 2:
        _RESOLVED_EMOJI_FAMILY = known_families.get(EMOJI_FAMILY.lower(), EMOJI_FAMILY)

    # 重新构造默认字体，避免缓存里保留旧回退链。
    _DEFAULT_FONT = None
    return loaded


def choose_font_size(current: int, parent=None) -> int | None:
    """弹出字体大小选择对话框，返回 point_size 或 None（取消）。"""
    dlg = ThemedDialog(parent, "字体大小")
    dlg.setMinimumWidth(FONT_DIALOG_MIN_WIDTH)

    row = QHBoxLayout()
    row.addWidget(QLabel("字号:"))
    spin = QSpinBox()
    spin.setRange(FONT_SIZE_MIN_PT, FONT_SIZE_MAX_PT)
    spin.setValue(current)
    spin.setSuffix(" pt")
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

    if dlg.exec() == ThemedDialog.DialogCode.Accepted:
        return spin.value()
    return None
