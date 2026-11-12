# 脚本说明

本目录存放构建、发布、环境部署和清理脚本。执行前请在项目根目录启用虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

## `setup_env.py`

检测 `.venv`。环境不存在时创建虚拟环境并安装 `requirements.txt`；环境已就绪时仅输出检测结果。检测到不完整的 `.venv` 时不会自动删除，而是提示手动删除后重试。

```powershell
python.exe scripts\setup_env.py
```

根目录的 `run.bat`、`run.ps1`、`clean.bat` 和 `clean.ps1` 都会先调用它。

## `build_exe.py`

使用 PyInstaller 打包应用。默认生成 Windows `onedir` 目录包，默认程序路径为 `dist/easycom/easycom.exe`。

```powershell
python.exe scripts\build_exe.py
python.exe scripts\build_exe.py --onefile
python.exe scripts\build_exe.py --name "easycom-custom"
```

可选参数：`--onefile` 生成单文件 EXE，`--console` 保留控制台，`--name` 指定产物名称，`--product-name` 和 `--file-description` 写入 Windows 文件属性。

## `release.bat` / `release.ps1`

从 `config/version.py` 读取 `APP_VERSION`。打包前会询问是否使用 `onefile`，输入 `y`、`yes` 或 `1` 使用单文件模式，直接回车或其他输入默认使用 `onedir`。发布产物以 `easycom-v<version>` 命名，包含对应的 EXE 和 ZIP。发布新版本时只需修改 `config/version.py` 中的数字版本号，支持 `1.0` 和 `1.0.1` 两种格式。

```powershell
.\scripts\release.ps1
```

或在资源管理器中双击 `scripts\release.bat`。

发行构建已用 Windows x64、Python 3.12.6、PySide6 / Shiboken6 6.11.2、pySerial 3.5 和 PyInstaller 6.22.3 验证打包。复现时可在虚拟环境执行 `python.exe -m pip install PySide6==6.11.2 pyserial==3.5 pyinstaller==6.22.3`。Windows 平台插件负责本机输入。打包钩子排除 Qt Virtual Keyboard 和 PDF 图像插件。`scripts/easycom.spec` 在生成目录版和单文件版前，从依赖清单移除 Windows API 集、UCRT 和 VC++ v14 运行库 DLL。发布页应附上[微软 x64 运行库](https://aka.ms/vc14/vc_redist.x64.exe)的下载链接。

发布到 GitHub Releases 时上传完整 ZIP，并在 Release 正文放上该标签的 `THIRD_PARTY_NOTICES.md` 链接，方便从下载页取得对应源码。维护者需保持对应源码可下载，必要时将相同版本源码包一并上传。升级 Qt 前应更新 `licenses/Qt-third-party.txt` 和第三方声明中的版本及源码链接。

## `clean.py`

删除项目内的 `__pycache__` 目录和 `auto-gen/` 运行时数据。`auto-gen/` 包含用户配置、历史记录、快捷命令和日志，删除后不可恢复。

```powershell
python.exe scripts\clean.py
```

通常请使用根目录的 `clean.bat` 或 `clean.ps1`，它们会先检查虚拟环境。
