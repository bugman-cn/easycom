# Windows 发行包第三方声明

easycom 的原创代码采用随包 `LICENSE` 中的 MIT 许可证。以下文件位于解压后的 `licenses/` 目录。

## Qt、PySide6 和 Shiboken6

本版使用 The Qt Company Ltd. 及 Qt 项目贡献者提供的 Qt、PySide6 和 Shiboken6 6.11.2，按 GNU LGPL 第 3 版使用。完整条款见 `LGPL-3.0-only.txt` 和 `GPL-3.0-only.txt`。Qt 组件的第三方声明见 `Qt-third-party.txt`，Windows 软件渲染器的声明见 `Mesa-LLVM.txt`。本软件包含由 FreeType Project 开发的部分代码，并部分基于 Independent JPEG Group 的工作。

对应源码可从以下地址免费下载，包含这些版本的构建文件。

- [Qt Base 6.11.2（Core、Gui、Network、Widgets、Windows 平台插件）](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.tar.xz)
- [Qt SVG 6.11.2](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtsvg-everywhere-src-6.11.2.tar.xz)
- [Qt Image Formats 6.11.2](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtimageformats-everywhere-src-6.11.2.tar.xz)
- [Qt Translations 6.11.2](https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qttranslations-everywhere-src-6.11.2.tar.xz)
- [PySide6 和 Shiboken6 6.11.2](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz)

你可以修改或替换上述 LGPL 库，并为调试这些修改进行逆向工程。目录版在程序关闭后可替换 `_internal/PySide6/` 和 `_internal/shiboken6/` 下对应的 DLL、PYD 或插件。请保持处理器架构和二进制接口兼容。单文件版可从相同发行标签取得 easycom 源码，在其虚拟环境安装兼容的自编译 PySide6 和 Shiboken6，再运行 `python.exe scripts/build_exe.py --onefile` 重建程序。[easycom 源码和构建脚本](https://github.com/bugman-cn/easycom)

## 其他运行组件

| 组件 | 随包的许可及署名 |
| --- | --- |
| CPython 及其 Windows 发行版中的第三方库 | `Python/LICENSE.txt`，包含 Python 历史许可、libffi、bzip2 及其他第三方条款。libmpdec 和 liblzma 的说明见 `CPython-libraries.txt`。 |
| pySerial | `pySerial.txt`，Chris Liechti，BSD 三条款。 |
| OpenSSL 3.0.15（`libcrypto-3.dll`、`libssl-3.dll`） | `OpenSSL.txt`，Apache License 2.0。Copyright the OpenSSL Project Authors. |
| PyInstaller 引导程序 | `PyInstaller/COPYING.txt`，包含 GPL 及引导程序分发例外。 |
| Microsoft Visual C++ v14 和 Universal CRT | Windows 10/11 提供 UCRT。VC++ 运行库由用户通过微软安装，出现缺少运行库的提示时请安装或更新 [Microsoft Visual C++ v14 x64 Redistributable](https://aka.ms/vc14/vc_redist.x64.exe)。CPython 中 Microsoft 代码的条款见 `Python/LICENSE.txt`。 |

## 图标和字体

- VS Code Codicons 由 Microsoft 及项目贡献者提供，采用 CC BY 4.0。来源、配色和栅格化修改记录见 `LICENSE-Codicons.txt`。
- Hack 字体的版权声明及许可见 `LICENSE-Hack.md`，文渊字体的 OFL 1.1 许可见 `LICENSE-WenYuanFonts.md`。
