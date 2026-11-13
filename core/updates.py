"""Read public GitHub Releases and prepare a verified Windows update."""
from __future__ import annotations

import ctypes
import hashlib
import json
import re
import shutil
import stat
import struct
import sys
import sysconfig
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from config.version import APP_VERSION, UPDATE_REPO
from core.release_page import AssetDigests, ReleaseNotes
from core.update_install import is_notice_file


class UpdateError(Exception):
    pass


class RateLimited(UpdateError):
    def __init__(self, until: float):
        self.until = until
        super().__init__(f"GitHub 请求受限，请在 {datetime.fromtimestamp(until):%H:%M:%S} 后重试。")


def version_parts(version: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"[0-9]{1,5}\.[0-9]{1,5}(?:\.[0-9]{1,5})?", version):
        raise UpdateError("发行版的版本号格式不正确。")
    major, minor, *patch = map(int, version.split("."))
    return major, minor, patch[0] if patch else 0


def architecture() -> str:
    arch = {"win-amd64": "x64", "win32": "x86", "win-arm64": "arm64"}.get(sysconfig.get_platform())
    if not arch:
        raise UpdateError("当前平台不支持安装 Windows 更新。")
    return arch


def release_asset_name(version: str, arch: str, mode: str) -> str:
    platform = {"x64": "win64", "x86": "win32", "arm64": "win-arm64"}[arch]
    suffix = "-onefile" if mode == "onefile" else ""
    return f"easycom-v{version}-{platform}{suffix}.zip"


def validate_repo(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo) or repo.split("/")[1] in (".", ".."):
        raise UpdateError("更新源必须是 GitHub 的 OWNER/REPO。")
    return repo


@dataclass(frozen=True)
class Build:
    repo: str
    arch: str
    mode: str

    @classmethod
    def current(cls):
        if not getattr(sys, "frozen", False):
            return cls(UPDATE_REPO, architecture(), "source")
        data = json.loads((Path(sys._MEIPASS) / "update-build.json").read_text(encoding="utf-8"))
        if data["mode"] not in {"onedir", "onefile"} or data["arch"] != architecture():
            raise UpdateError("安装包的更新信息无效。")
        return cls(validate_repo(data["repo"]), data["arch"], data["mode"])


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str = ""
    size: int = 0
    digest: str = ""


class _GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        host = url.hostname or ""
        if url.scheme != "https" or url.username or url.password or not (
            host in {"github.com", "api.github.com"} or host.endswith(".githubusercontent.com")
        ):
            raise UpdateError("GitHub 下载重定向地址无效。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, accept: str, *, method: str = "GET"):
    req = Request(url, method=method, headers={"User-Agent": f"easycom/{APP_VERSION}", "Accept": accept})
    try:
        return build_opener(_GitHubRedirects()).open(req, timeout=15)
    except HTTPError as exc:
        if exc.code in (403, 429):
            retry = exc.headers.get("Retry-After", "")
            reset = exc.headers.get("X-RateLimit-Reset", "")
            exhausted = exc.headers.get("X-RateLimit-Remaining") == "0"
            if (retry.isdigit() or exhausted or exc.code == 429
                    or b"secondary rate limit" in exc.read(8192).lower()):
                now = time.time()
                if retry.isdigit():
                    until = now + int(retry)
                elif exhausted and reset.isdigit():
                    until = int(reset)
                else:
                    until = now + 60
                raise RateLimited(max(now + 1, until)) from exc
        if exc.code == 404:
            raise UpdateError("未找到公开的正式版本或附件，请确认仓库已发布 Release。") from exc
        raise UpdateError(f"GitHub 请求失败（HTTP {exc.code}）。") from exc


class _PageFormatError(Exception):
    """The public page did not provide the expected release metadata."""


def _page_text(stream):
    raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise _PageFormatError()
    return raw.decode("utf-8", errors="replace")


def _latest_page(build: Build) -> Release:
    repo = validate_repo(build.repo)
    base = f"https://github.com/{repo}/releases"
    with _open(base + "/latest", "text/html") as page:
        final = page.geturl().rstrip("/")
        if final.casefold() == base.casefold():
            raise UpdateError("该仓库目前没有已发布的正式版。")
        tag = final.rsplit("/", 1)[-1]
        if final.casefold() != f"{base}/tag/{tag}".casefold() or not tag.startswith("v"):
            raise _PageFormatError()
        version = tag[1:]
        parsed_version = version_parts(version)
        notes = ReleaseNotes()
        notes.feed(_page_text(page))
        notes.close()

    fragment = f"{base}/expanded_assets/{tag}"
    if not notes.found_body and notes.asset_fragment.casefold() != fragment.casefold():
        raise _PageFormatError()
    text = notes.plain_text()
    if parsed_version <= version_parts(APP_VERSION) or build.mode == "source":
        return Release(version, text)

    name = release_asset_name(version, build.arch, build.mode)
    asset_path = f"/{repo}/releases/download/{tag}/{name}"
    assets = AssetDigests(asset_path)
    with _open(fragment, "text/html") as page:
        assets.feed(_page_text(page))
        assets.close()
    if len(assets.matches) != 1 or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", assets.matches[0]):
        raise _PageFormatError()
    url = "https://github.com" + asset_path
    with _open(url, "application/octet-stream", method="HEAD") as response:
        length = response.headers.get("Content-Length", "")
    if not re.fullmatch(r"[0-9]{1,10}", length) or not 0 < int(length) <= 2**31:
        raise _PageFormatError()
    return Release(version, text, url, int(length), assets.matches[0][7:].lower())


def latest(build: Build) -> Release:
    try:
        return _latest_page(build)
    except _PageFormatError:
        return _latest_api(build)


def _latest_api(build: Build) -> Release:
    with _open(f"https://api.github.com/repos/{validate_repo(build.repo)}/releases/latest",
               "application/vnd.github+json") as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise UpdateError("GitHub 返回的版本信息过大。")
    data = json.loads(raw)
    tag = data["tag_name"]
    if data.get("draft") or data.get("prerelease") or not tag.startswith("v"):
        raise UpdateError("未找到有效的正式版本标签。")
    version = tag[1:]
    notes = str(data.get("body") or "")
    if version_parts(version) <= version_parts(APP_VERSION) or build.mode == "source":
        return Release(version, notes)
    name = release_asset_name(version, build.arch, build.mode)
    matches = [a for a in data["assets"] if a["name"] == name and a.get("state") == "uploaded"]
    if len(matches) != 1:
        raise UpdateError(f"V{version} 尚未提供当前安装方式的附件：{name}")
    asset = matches[0]
    digest = asset.get("digest") or ""
    url = f"https://github.com/{build.repo}/releases/download/{tag}/{name}"
    if str(asset.get("browser_download_url", "")).casefold() != url.casefold():
        raise UpdateError("附件下载地址与所选仓库和标签不符。")
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest) or not 0 < asset["size"] <= 2**31:
        raise UpdateError("附件缺少有效的 SHA-256 或文件大小。")
    return Release(version, notes, url, asset["size"], digest[7:].lower())


def _check_space(path: Path, needed: int):
    if shutil.disk_usage(path).free < needed:
        raise UpdateError("安装磁盘的可用空间不足。")


def _exe_version(exe: Path, arch: str):
    with exe.open("rb") as fp:
        if fp.read(2) != b"MZ":
            raise UpdateError("更新包中的程序不是 Windows EXE。")
        fp.seek(60)
        offset, = struct.unpack("<I", fp.read(4))
        fp.seek(offset)
        if fp.read(4) != b"PE\0\0" or fp.read(2) != struct.pack("<H", {"x64":0x8664,"x86":0x14c,"arm64":0xaa64}[arch]):
            raise UpdateError("更新包中的 EXE 架构不匹配。")
    lib = ctypes.windll.version
    size = lib.GetFileVersionInfoSizeW(str(exe), None)
    buf = ctypes.create_string_buffer(size)
    ptr = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not size or not lib.GetFileVersionInfoW(str(exe), 0, size, buf) or not lib.VerQueryValueW(
        buf, "\\", ctypes.byref(ptr), ctypes.byref(length)
    ) or length.value < 24:
        raise UpdateError("无法读取更新包的 Windows 版本信息。")
    signature, _, high, low = struct.unpack("<4I", ctypes.string_at(ptr, 16))
    if signature != 0xFEEF04BD:
        raise UpdateError("Windows 版本信息无效。")
    return high >> 16, high & 65535, low >> 16, low & 65535


def _unpack(archive: Path, target: Path, mode: str, cancelled):
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        total = sum(i.file_size for i in members)
        if len(members) > 30000 or total > 4 * 1024**3:
            raise UpdateError("更新包解压后超出大小限制。")
        _check_space(target, total)
        names = set()
        for item in members:
            cancelled()
            if item.orig_filename != item.filename:
                raise UpdateError("更新包包含非标准路径。")
            parts = item.filename.rstrip("/").split("/")
            path = PureWindowsPath(*parts)
            if (any(not part or part in (".","..") or part.endswith((" ", ".")) or
                    re.search(r'[\\:<>"|?*\x00-\x1f]', part) or PureWindowsPath(part).is_reserved()
                    for part in parts) or path.is_absolute() or path.drive or
                    stat.S_ISLNK(item.external_attr >> 16) or item.external_attr & 0x400):
                raise UpdateError("更新包包含无效路径或链接。")
            if not (parts == ["easycom.exe"] and not item.is_dir() or
                    mode == "onedir" and parts[0] == "_internal" or
                    item.is_dir() and parts[0] == "licenses" or
                    not item.is_dir() and is_notice_file("/".join(parts))):
                raise UpdateError("更新包布局与当前安装方式不符。")
            key = str(path).casefold()
            if key in names:
                raise UpdateError("更新包包含重复路径。")
            names.add(key)
            dest = target.joinpath(*parts)
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as source, dest.open("xb") as output:
                while chunk := source.read(1024*1024):
                    cancelled()
                    output.write(chunk)
        if not (target / "easycom.exe").is_file() or (mode == "onedir" and
                not (target / "_internal" / "update-build.json").is_file()):
            raise UpdateError("更新包缺少必需的程序文件。")


def prepare(build: Build, release: Release, job: Path, progress, cancelled):
    archive = job / "download.zip"
    target = job / "new"
    target.mkdir()
    _check_space(job, release.size)
    sha = hashlib.sha256()
    received = 0
    with _open(release.url, "application/octet-stream") as source, archive.open("xb") as output:
        while chunk := source.read(256*1024):
            cancelled()
            received += len(chunk)
            if received > release.size:
                raise UpdateError("下载大小超过 Release 附件信息。")
            sha.update(chunk)
            output.write(chunk)
            progress(received * 100 // release.size)
    cancelled()
    if received != release.size or sha.hexdigest() != release.digest:
        raise UpdateError("更新包大小或 SHA-256 校验失败。")
    _unpack(archive, target, build.mode, cancelled)
    if _exe_version(target / "easycom.exe", build.arch) != (*version_parts(release.version), 0):
        raise UpdateError("EXE 版本与 Release 标签不符。")
    if build.mode == "onedir":
        info = json.loads((target/"_internal"/"update-build.json").read_text(encoding="utf-8"))
        if info != {"repo":build.repo,"arch":build.arch,"mode":build.mode}:
            raise UpdateError("下载的程序包来自不同更新源或架构。")
    archive.unlink()
