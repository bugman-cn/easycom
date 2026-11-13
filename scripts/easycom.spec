"""PyInstaller recipe for the Windows desktop release."""
import argparse
import sys
from fnmatch import fnmatchcase
from pathlib import Path

scripts = Path(SPECPATH)
root = scripts.parent
sys.path.insert(0, str(scripts))
from build_exe import _license_files

parser = argparse.ArgumentParser()
parser.add_argument("--name", required=True)
parser.add_argument("--onefile", action="store_true")
parser.add_argument("--console", action="store_true")
parser.add_argument("--helper", action="store_true")
parser.add_argument("--build-dir", required=True, type=Path)
args = parser.parse_args()

if args.helper:
    entry = scripts / "update_helper.py"
    datas = []
else:
    entry = root / "main.py"
    datas = [(str(root / name), name) for name in ("resources", "icon")]
    datas.extend([(str(args.build_dir / "update-build.json"), "."),
                  (str(args.build_dir / "helper" / "easycom-updater.exe"), ".")])
if args.onefile:
    datas.extend((str(source), str(relative.parent)) for source, relative in _license_files(root))

app = Analysis([str(entry)], pathex=[str(root)], datas=datas, hookspath=[str(scripts / "hooks")])
if sys.platform == "win32":
    # Windows 10/11 supplies the API sets and UCRT. Microsoft's x64 redistributable supplies VC++.
    system_dlls = (
        "api-ms-win-*.dll", "ext-ms-win-*.dll", "ucrtbase.dll",
        "msvcp140*.dll", "vcruntime140*.dll", "concrt140.dll",
        "vccorlib140.dll", "vcamp140.dll", "vcomp140.dll",
    )
    app.binaries = [
        entry for entry in app.binaries
        if not any(fnmatchcase(Path(entry[0]).name.lower(), pattern) for pattern in system_dlls)
    ]

icon = root / "icon" / "icon.ico"
embedded = [app.binaries, app.datas] if args.onefile else []
exe = EXE(
    PYZ(app.pure), app.scripts, *embedded,
    exclude_binaries=not args.onefile, name=args.name, console=args.console, upx=False,
    icon=str(icon) if icon.is_file() else None, version=None if args.helper else str(args.build_dir / "version_info.txt"),
)
if not args.onefile:
    COLLECT(exe, app.binaries, app.datas, name=args.name, upx=False)
