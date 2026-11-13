# easycom 贡献指南

## 项目结构

- `main.py` 启动 PySide6 应用，加载字体、主题和配置，并创建 `MainWindow`。
- `gui/` 存放界面组件；`gui/main_window.py` 负责跨面板流程和信号连接。
- `core/serial_worker.py` 负责通过 pyserial 访问串口；`core/data_converter.py` 和 `core/ansi_parser.py` 是不依赖界面的转换、解析模块。
- `config/app_config.py` 持久化用户设置和历史记录；`config/defaults.py`、`config/runtime.py`、`config/theme.py` 和 `config/ui.py` 分别存放默认值、运行限制、主题颜色和界面常量。`config/debug_config.py` 仅保留旧版导入路径。
- `utils/` 存放主题、字体加载和异步文件日志相关工具。
- `resources/` 和 `icon/` 存放打包时使用的应用资源。

## 常用命令

```powershell
.\.venv\Scripts\Activate.ps1
python.exe -m pip install -r requirements.txt
python.exe .\main.py
python.exe -m py_compile gui\changed_file.py
python.exe scripts\build_exe.py
```

在资源管理器中运行 `run.bat`，或在 PowerShell 中运行 `./run.ps1`，可以一键启动。两者都会调用 `scripts/setup_env.py`，在 `.venv` 不存在时创建虚拟环境并安装依赖。对应的一键清理入口是 `clean.bat` 和 `./clean.ps1`；它们也会先检查环境，再清理运行时数据。

安装依赖、运行程序或执行验证前，始终先激活仓库中的 `.venv`。辅助测试脚本已移除，代码验证仍须进行真实启动与手动验证。每次修改代码后，都必须从该虚拟环境运行 `python.exe .\main.py`，确认程序成功打开后关闭，并手动验证受影响的交互。只有成功启动，代码修改才算通过验证；还应对改动的 Python 模块执行编译检查。仅修改文档时，无需启动程序。

## 实现约束

- 界面逻辑放在所属面板中；`MainWindow` 只负责跨面板协调、连接生命周期和模式切换。
- 不要从 `SerialWorker` 直接更新 Qt 组件。它负责将收到的数据放入队列，由 UI 线程中的 `MainWindow._drain_received()` 消费。
- 修改高吞吐接收逻辑时，保留接收处理预算和队列流程；逐字节同步更新界面会导致程序卡顿。
- 用户可见的设置通过 `config.app_config.config` 或专用的历史记录辅助方法保存，不要另建临时配置文件。
- 普通接收显示与终端仿真保持分离：`ReceivePanel` 侧重按行显示和日志，`TerminalReceivePanel` 负责解释终端控制序列。
- 优先复用现有主题、字体、对话框和 `EmojiButton` 辅助组件，不要为单个组件另建样式体系或增加依赖。

## 字体规则

- 字体族、回退顺序和默认字号统一在 `config/ui.py` 配置，由 `utils/font_manager.py` 加载并提供给界面；不要在新控件中硬编码字体族或自行拼接回退链。
- 为 Qt 控件设置字体时，复用 `get_default_font()`、`get_display_font()` 或 `apply_display_fallbacks()`；在样式表中指定字体族时，使用 `get_display_font_css_stack()`。需要单独显示 emoji 的控件使用现有 emoji 字体辅助方法或 `EmojiButton`。
- 字号使用点数（`pt`）；新增默认字号放在 `config/ui.py`。接收、发送和终端文本的用户可调字号应继续通过 `config.font_size` 和各面板的 `set_display_font()` 应用，不要另设独立状态。
- `QPlainTextEdit` 和 `QTextEdit` 的字体需要显式设置；修改字体或样式表后，检查字号调整是否生效，并手动查看英文、中文和 emoji 的显示效果。
- 新增内置字体文件放在 `resources/fonts/`，同时检查字体加载、回退配置和打包后的显示效果。

## 修改规则

- 修改前先确认功能所属模块及调用关系；只改完成当前需求必需的文件，避免顺带重构、批量格式化或修改无关行为。
- 修改现有设置、默认值或历史记录格式时，同时检查读取与写入路径，确保旧配置仍能加载；新增用户设置应有明确的默认值。
- 修改串口收发或数据转换逻辑时，检查字节内容、编码、换行和十六进制模式等受影响路径；不要为了验证代码而操作真实串口设备。
- 修改界面交互时，手动检查相关面板、模式切换和状态变化；涉及接收显示时，分别检查普通接收与终端模式。
- 修改启动命令、配置行为或用户可见功能时，同步更新相关说明文档；新增运行时资源时检查打包配置。
- 验证完成后说明改动内容、执行过的检查和未验证的场景；如果程序未能成功启动，不要将修改标记为已验证。

## 运行时文件与安全

- 从源码运行时，用户生成的数据位于源码目录旁的 `auto-gen/`；打包后则位于可执行文件旁。目录中包含配置、历史记录、快捷命令和日志。
- 除非任务明确涉及运行时数据，否则不要编辑、删除或提交 `auto-gen/`。`scripts/clean.py` 会删除它以及所有 `__pycache__` 目录。
- `SerialWorker` 管理真实串口硬件。仅验证代码时，不要打开或关闭串口、切换 DTR/RTS，也不要发送数据；除非任务明确要求硬件交互。

## 打包

- `scripts/build_exe.py` 会打包 `resources/` 和 `icon/`；新增运行时资源应放在这两个目录下，或同步更新脚本中的 `--add-data` 参数。
- Windows 默认使用 `onedir` 打包；只有需要单文件发行版时才运行 `python scripts\build_exe.py --onefile`。
- `scripts/release.bat` 和 `scripts/release.ps1` 读取 `APP_VERSION`，在 x64 环境下默认生成 `easycom-v<version>-win64.zip`，程序名固定为 `easycom.exe`。
