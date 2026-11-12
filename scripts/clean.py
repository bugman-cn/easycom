"""清除项目中所有 __pycache__ 目录和 auto-gen/ 持久化数据。"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

count = 0
for d in ROOT.rglob("__pycache__"):
    if d.is_dir():
        shutil.rmtree(d)
        print(f"  已删除: {d.relative_to(ROOT)}")
        count += 1

gen_dir = ROOT / "auto-gen"
if gen_dir.exists():
    shutil.rmtree(gen_dir)
    print(f"  已删除: {gen_dir.relative_to(ROOT)}")
    count += 1

print(f"\n完成: 删除了 {count} 个目标")
