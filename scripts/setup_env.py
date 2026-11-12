"""创建项目虚拟环境并安装运行依赖。"""
from __future__ import annotations

import subprocess
import sys
import time
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"

SEPARATOR = "=" * 64


def _section(title: str) -> float:
    print(f"\n{SEPARATOR}")
    print(f"[阶段] {title}")
    print(SEPARATOR)
    return time.perf_counter()


def _complete(title: str, started: float) -> None:
    print(f"[完成] {title}，耗时 {time.perf_counter() - started:.2f}s")


def main() -> int:
    total_started = time.perf_counter()
    result = 1
    try:
        started = _section("检测 Python 虚拟环境")
        if VENV_PYTHON.exists():
            print(f"[就绪] 虚拟环境已存在: {VENV_DIR}")
            _complete("检测 Python 虚拟环境", started)
            result = 0
            return result
        if VENV_DIR.exists():
            print(f"[失败] 虚拟环境不完整: {VENV_DIR}")
            print("[操作] 请删除该 .venv 目录后重新运行此脚本。")
            _complete("检测 Python 虚拟环境", started)
            return result
        print("[信息] 未找到虚拟环境，将开始部署。")
        _complete("检测 Python 虚拟环境", started)

        started = _section("创建 Python 虚拟环境")
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        _complete("创建 Python 虚拟环境", started)

        started = _section("安装项目依赖")
        subprocess.run(
            [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
            check=True,
        )
        _complete("安装项目依赖", started)
        print(f"[成功] 虚拟环境部署完成: {VENV_DIR}")
        result = 0
        return result
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[失败] 环境部署失败: {exc}")
        return result
    finally:
        print(f"\n{SEPARATOR}")
        print(f"[总耗时] {time.perf_counter() - total_started:.2f}s")
        print(SEPARATOR)


if __name__ == "__main__":
    raise SystemExit(main())
