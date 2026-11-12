"""将透明 PNG 的非透明主体移动到画布中心。

用法：
    python icon/center_image_subject.py icon/emoji-light/u26a0.png icon/emoji-dark/u26a0.png
    python icon/center_image_subject.py icon/emoji-light --recursive
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage, QPainter


def _png_files(paths: list[Path], recursive: bool) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() == ".png":
            result.append(path)
        elif path.is_dir():
            pattern = "**/*.png" if recursive else "*.png"
            result.extend(sorted(path.glob(pattern)))
    return list(dict.fromkeys(result))


def center_image_subject(path: Path) -> bool:
    image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
    if image.isNull():
        raise ValueError(f"无法读取图片: {path}")

    min_x, min_y = image.width(), image.height()
    max_x = max_y = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 0:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x < 0:
        return False

    subject_center_x = (min_x + max_x) / 2
    subject_center_y = (min_y + max_y) / 2
    canvas_center_x = (image.width() - 1) / 2
    canvas_center_y = (image.height() - 1) / 2
    shift_x = round(canvas_center_x - subject_center_x)
    shift_y = round(canvas_center_y - subject_center_y)

    if shift_x == 0 and shift_y == 0:
        return False

    output = QImage(image.size(), QImage.Format.Format_RGBA8888)
    output.fill(0)
    painter = QPainter(output)
    painter.drawImage(QPoint(shift_x, shift_y), image)
    painter.end()

    if not output.save(str(path), "PNG"):
        raise OSError(f"无法保存图片: {path}")
    print(f"{path}: shift=({shift_x}, {shift_y})")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="将透明 PNG 的主体移动到画布中心")
    parser.add_argument("paths", nargs="+", type=Path, help="PNG 文件或目录")
    parser.add_argument("--recursive", action="store_true", help="目录递归处理 PNG")
    args = parser.parse_args()

    files = _png_files(args.paths, args.recursive)
    if not files:
        parser.error("未找到 PNG 文件")

    changed = sum(center_image_subject(path) for path in files)
    print(f"processed={len(files)}, changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
