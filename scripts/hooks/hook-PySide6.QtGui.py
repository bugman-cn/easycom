"""Collect Qt GUI dependencies used by the Windows desktop application."""
from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
# Filter plugin entry points before PyInstaller follows their DLL dependencies.
skipped = {"qpdf.dll", "qtvirtualkeyboardplugin.dll"}
binaries = [(src, dest) for src, dest in binaries if Path(src).name.lower() not in skipped]
