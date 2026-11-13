"""Standalone entry point for the Windows recovery executable."""
import ctypes
import sys
from pathlib import Path

from core.update_install import run_helper

if __name__ == "__main__":
    try:
        run_helper(Path(sys.executable).resolve().parent.parent, *sys.argv[1:])
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(None, str(exc), "easycom 更新助手", 0x10)
        sys.exit(1)
