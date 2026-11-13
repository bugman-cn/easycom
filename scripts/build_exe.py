"""一键打包为 Windows EXE（基于 PyInstaller）。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path
from tempfile import NamedTemporaryFile
from textwrap import dedent
from zipfile import ZIP_DEFLATED, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.version import APP_NAME, APP_VERSION, UPDATE_REPO
from core.updates import architecture, release_asset_name, validate_repo, version_parts


def _write_version_file(root: Path, name: str, product_name: str, file_description: str) -> Path:
    """生成 PyInstaller 版本信息文件，用于写入 EXE 元数据。"""
    version_file = root / "version_info.txt"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    parts = APP_VERSION.split(".")
    if len(parts) == 2:
        parts.append("0")
    major, minor, patch = map(int, parts)
    file_version = f"{major}.{minor}.{patch}.0"
    content = dedent(
        f"""
        # UTF-8
        VSVersionInfo(
            ffi=FixedFileInfo(
                filevers=({major}, {minor}, {patch}, 0), prodvers=({major}, {minor}, {patch}, 0), mask=0x3F,
                flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
            ),
            kids=[
                StringFileInfo([StringTable(u'080404B0', [
                    StringStruct(u'CompanyName', u''),
                    StringStruct(u'FileDescription', u'{file_description}'),
                    StringStruct(u'FileVersion', u'{file_version}'),
                    StringStruct(u'InternalName', {APP_NAME!r}),
                    StringStruct(u'LegalCopyright', u''),
                    StringStruct(u'OriginalFilename', {name + ".exe"!r}),
                    StringStruct(u'ProductName', u'{product_name}'),
                    StringStruct(u'ProductVersion', u'{file_version}')
                ])]),
                VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
            ]
        )
        """
    ).strip() + "\n"
    version_file.write_text(content, encoding="utf-8")
    return version_file


def _license_files(root: Path) -> list[tuple[Path, Path]]:
    """Return each license source and its path relative to the release EXE."""
    installer = distribution("pyinstaller")
    copying = next(path for path in installer.files or () if path.name == "COPYING.txt")
    files = [
        (root / "LICENSE", Path("LICENSE")),
        (root / "THIRD_PARTY_NOTICES.md", Path("THIRD_PARTY_NOTICES.md")),
        (Path(sys.base_prefix) / "LICENSE.txt", Path("licenses/Python/LICENSE.txt")),
        (Path(installer.locate_file(copying)), Path("licenses/PyInstaller/COPYING.txt")),
    ]
    license_paths = [
        *sorted((root / "licenses").glob("*.txt")),
        root / "icon/LICENSE-Codicons.txt",
        root / "resources/fonts/LICENSE-Hack.md",
        root / "resources/fonts/LICENSE-WenYuanFonts.md",
    ]
    for path in license_paths:
        files.append((path, Path("licenses") / path.name))
    return files


def _release_zip(target: Path, exe: Path, onefile: bool, notices: list[tuple[Path, Path]]) -> Path:
    with NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as temp:
        output = Path(temp.name)
    try:
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            archive.write(exe, exe.name)
            for source, relative in notices:
                archive.write(source, relative)
            if not onefile:
                for source in sorted((exe.parent / "_internal").rglob("*")):
                    if source.is_file():
                        archive.write(source, source.relative_to(exe.parent))
        output.replace(target)
    finally:
        output.unlink(missing_ok=True)
    return target


def build(onefile: bool, console: bool, product_name: str, file_description: str, name: str,
          release_zip: bool = False, *, release: bool = False, repo: str = UPDATE_REPO) -> int:
    root = Path(__file__).resolve().parent.parent
    entry = root / "main.py"
    if not entry.exists():
        print(f"[ERROR] 找不到入口文件: {entry}")
        return 1

    if release_zip or release:
        from PySide6.QtCore import qVersion
        if qVersion() != "6.11.2" or distribution("PySide6").version != "6.11.2":
            print("[ERROR] 发行声明对应 Qt / PySide6 6.11.2，请使用该版本或先更新许可及源码链接。")
            return 1
    notices = _license_files(root)
    mode = "onefile" if onefile else "onedir"
    arch = architecture()
    validate_repo(repo)
    if release and name != APP_NAME:
        raise ValueError("发布版的程序名必须为 easycom。")
    if max(version_parts(APP_VERSION)) > 65535:
        raise ValueError("Windows 版本号的各段须小于 65536。")
    build_dir = root / "build" / mode
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    out = build_dir / "release" if release else dist
    _write_version_file(build_dir, name, product_name, file_description)
    (build_dir / "update-build.json").write_text(
        json.dumps({"repo": repo, "arch": arch, "mode": mode}), encoding="utf-8",
    )
    pyinstaller = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean"]
    recipe = [str(root / "scripts" / "easycom.spec"), "--", "--build-dir", str(build_dir)]
    helper_cmd = [
        *pyinstaller, "--workpath", str(build_dir / "helper-work"), "--distpath", str(build_dir / "helper"),
        *recipe, "--name", "easycom-updater", "--helper", "--onefile",
    ]
    cmd = [
        *pyinstaller, "--workpath", str(build_dir / "work"), "--distpath", str(out), *recipe, "--name", name,
    ]
    if onefile:
        cmd.append("--onefile")
    if console:
        cmd.append("--console")

    print("[INFO] 开始打包...")
    print("[INFO] 命令:", " ".join(cmd))
    env = os.environ.copy()
    if os.name == "nt":
        # 只让 PyInstaller 从 Python 和 Windows 目录查找 DLL，避免 PATH 中的其他软件库混入产物。
        windows_dir = Path(env.get("SystemRoot", r"C:\Windows"))
        env["PATH"] = os.pathsep.join(
            (str(Path(sys.executable).parent), sys.base_prefix, str(windows_dir / "System32"), str(windows_dir))
        )
    try:
        subprocess.run(helper_cmd, cwd=root, check=True, env=env)
        subprocess.run(cmd, cwd=root, check=True, env=env)
    except FileNotFoundError:
        print("[ERROR] 未找到 Python 或 PyInstaller。请先安装: pip install pyinstaller")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] 打包失败，退出码: {exc.returncode}")
        return exc.returncode

    exe_path = out / (f"{name}.exe" if onefile else f"{name}/{name}.exe")
    if not onefile:
        for source, relative in notices:
            destination = exe_path.parent / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    if release_zip or release:
        filename = release_asset_name(APP_VERSION, arch, mode) if release else name + ".zip"
        print(f"[OK] ZIP: {_release_zip(dist / filename, exe_path, onefile, notices)}")
    print("[OK] 打包完成")
    print(f"[OK] 输出目录: {root / 'dist'}")
    print(f"[OK] 可执行文件: {exe_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"打包 {APP_NAME} 为 EXE")
    parser.add_argument("--onefile", action="store_true", help="使用单文件模式")
    parser.add_argument("--console", action="store_true", help="保留控制台窗口")
    parser.add_argument("--name", default=APP_NAME, help="输出可执行文件名（不含扩展名）")
    parser.add_argument("--product-name", default=APP_NAME, help="Windows 文件属性中的产品名称")
    parser.add_argument("--file-description", default=APP_NAME, help="Windows 文件属性中的文件说明")
    parser.add_argument("--zip", action="store_true", help="生成附带许可文件的发行 ZIP")
    parser.add_argument("--release", action="store_true", help="生成带架构和安装模式的 GitHub Release ZIP")
    parser.add_argument("--update-repo", default=UPDATE_REPO, help="公开 GitHub 更新源 OWNER/REPO")
    args = parser.parse_args()
    return build(args.onefile, args.console, args.product_name, args.file_description, args.name, args.zip,
                 release=args.release, repo=args.update_repo)


if __name__ == "__main__":
    raise SystemExit(main())
