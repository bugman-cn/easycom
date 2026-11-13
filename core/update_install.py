"""Windows update transaction for the executable, bundled runtime and release notices."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath

WORK = ".easycom-update"
PENDING = "pending.json"
TOKEN = "EASYCOM_UPDATE_TOKEN"
MARK = "easycom updater v1\n"


TOP_NOTICES = ("LICENSE", "THIRD_PARTY_NOTICES.md")


def is_notice_file(name):
    if name in TOP_NOTICES:
        return True
    return (re.fullmatch(r"licenses/(?:[\w-]+/)*[\w-][\w.-]*\.(?:md|txt)", name, re.ASCII) is not None
            and not any(PureWindowsPath(part).is_reserved() for part in name.split("/")))


def _plain(path: Path):
    if os.path.lexists(path) and path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError(f"更新路径含有链接：{path}")
    return path


def _member(base, name):
    for part in name.split("/"):
        base = _plain(base / part)
    return base


def workspace(root: Path, *, create=False):
    work = _plain(root / WORK)
    marker = _plain(work / "owner")
    if root == Path(root.anchor):
        raise OSError('请将 easycom 解压到独立的程序目录后再安装更新。')
    if create:
        try:
            work.mkdir()
        except FileExistsError:
            pass
        else:
            with marker.open("x", encoding="utf8") as f:
                f.write(MARK)
    if marker.read_text(encoding="utf8") != MARK:
        raise OSError(f"更新目录已被其他文件占用：{work}")
    return work


def new_job(root: Path):
    work = workspace(root, create=True)
    if (work / PENDING).exists():
        raise OSError("上一次更新尚未完成，请先运行更新目录中的 recover.exe。")
    job = work / uuid.uuid4().hex
    job.mkdir()
    return job


@contextmanager
def transaction_lock(work, wait=0):
    import msvcrt
    with _plain(work / "lock").open("a+b") as lock:
        lock.seek(0)
        deadline = time.monotonic() + wait
        while True:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise OSError("另一个更新助手正在运行。") from exc
                time.sleep(0.1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _atomic(path: Path, data):
    tmp = path.with_name(uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _plain(path))
    finally:
        tmp.unlink(missing_ok=True)


def read_pending(work):
    state = json.loads(_plain(work / PENDING).read_text(encoding="utf8"))
    if (not re.fullmatch("[0-9a-f]{32}", state["id"]) or state["mode"] not in ("onefile", "onedir")
            or state["phase"] not in ("prepared", "replacing", "launching", "committed")):
        raise ValueError("更新记录损坏。")
    notices = state.get("notices", [])
    if not isinstance(notices, list) or any(not isinstance(n, str) or not is_notice_file(n) for n in notices):
        raise ValueError("更新记录中的许可文件路径无效。")
    return state


def _launch(exe, args=(), token=None):
    env = os.environ.copy()
    env.pop(TOKEN, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if token:
        env[TOKEN] = token
    return subprocess.Popen([str(exe), *args], cwd=exe.parent, env=env, close_fds=True)


def handoff(root, job, mode, version):
    work = workspace(root)
    if job.parent != work or root / "easycom.exe" != Path(sys.executable):
        raise ValueError("请将程序以 easycom.exe 的名称运行，再安装更新。")
    with transaction_lock(work):
        if (work / PENDING).exists():
            raise OSError("已有更新正在进行。")
        helper = _plain(work / "recover.exe")
        tmp = job / "helper.tmp"
        shutil.copyfile(Path(sys._MEIPASS) / "easycom-updater.exe", tmp)
        os.replace(tmp, helper)
        staged = job / "new"
        notices = [name for name in TOP_NOTICES if (staged / name).is_file()]
        notices.extend(sorted(p.relative_to(staged).as_posix() for p in (staged / "licenses").rglob("*")
                              if p.is_file()))
        if not all(is_notice_file(n) for n in notices):
            raise ValueError("更新包包含无效的许可文件。")
        _atomic(work / PENDING, {"id": job.name, "mode": mode, "version": version,
                                 "phase": "prepared", "notices": notices})
        try:
            _launch(helper, [job.name])
        except OSError:
            (work / PENDING).unlink()
            raise


def startup_allowed(root, version):
    pending = root / WORK / PENDING
    if not pending.exists():
        return True
    state = read_pending(workspace(root))
    return state["phase"] == "launching" and state["version"] == version and state["id"] == os.getenv(TOKEN)


def confirm_start(root, version):
    token = os.environ.pop(TOKEN, None)
    if token:
        work = workspace(root)
        state = read_pending(work)
        if (state["id"], state["version"], state["phase"]) == (token, version, "launching"):
            _atomic(_plain(work / token) / "ready.json", token)


def _idle(exe, timeout=30):
    # Windows denies write access to an executable mapped by any running instance,
    # including the parent process of a onefile application.
    end = time.monotonic() + timeout
    while exe.exists():
        try:
            with exe.open("r+b"):
                return True
        except OSError:
            if time.monotonic() >= end:
                return False
            time.sleep(0.25)
    return True


def _phase(work, state, phase):
    _atomic(work / PENDING, {**state, "phase": phase})
    state["phase"] = phase


def _cleanup(work, job):
    # Remove the journal last. A committed cleanup can be repeated after interruption.
    if job.exists():
        shutil.rmtree(job)
    (work / PENDING).unlink()


def _restore(root, job, names):
    backup = _plain(job / "backup")
    for name in reversed(names):
        saved = _member(backup, name)
        absent = saved.with_name(saved.name + ".absent")
        if saved.exists() or absent.is_file():
            current = _member(root, name)
            if current.exists():
                discard = _member(job / "discard", name)
                discard.parent.mkdir(parents=True, exist_ok=True)
                current.rename(discard)
            if saved.exists():
                saved.rename(current)


def _record(work, msg):
    with _plain(work / "update.log").open("a", encoding="utf-8") as out:
        out.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def run_helper(root, request=None):
    work = workspace(root)
    exe = _plain(root / "easycom.exe")
    # The GUI holds the lock until the helper process has been created.
    with transaction_lock(work, wait=2):
        if not (work / PENDING).exists():
            return
        state = read_pending(work)
        job = _plain(work / state["id"])
        names = ["easycom.exe"] + (["_internal"] if state["mode"] == "onedir" else [])
        names.extend(state.get("notices", []))
        if request and request != state["id"]:
            raise ValueError("更新任务已更改。")
        if state["phase"] == "committed":
            _cleanup(work, job)
            return
        if not _idle(exe):
            raise OSError("程序仍在运行或安装目录不可写。请退出所有 easycom 实例，再运行 recover.exe。")
        backup = _plain(job / "backup")
        if request and state["phase"] == "prepared":
            try:
                backup.mkdir()
                _phase(work, state, "replacing")
                for name in names:
                    current = _member(root, name)
                    saved = _member(backup, name)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    if name in state.get("notices", []) and not current.exists():
                        saved.with_name(saved.name + ".absent").touch(exist_ok=False)
                        current.parent.mkdir(parents=True, exist_ok=True)
                    else:
                        if name in state.get("notices", []) and not current.is_file():
                            raise OSError(f"许可文件路径已被占用：{current}")
                        current.rename(saved)
                    _member(_plain(job / "new"), name).rename(current)
                _phase(work, state, "launching")
                proc = _launch(exe, token=state["id"])
                end = time.monotonic() + 90
                ready = _plain(job / "ready.json")
                while not ready.exists() and proc.poll() is None and time.monotonic() < end:
                    time.sleep(0.2)
                if not ready.exists() or json.loads(ready.read_text()) != state["id"]:
                    raise OSError("新版未确认启动。")
                _phase(work, state, "committed")
            except Exception as exc:
                _record(work, str(exc))
        if state["phase"] == "committed":
            _cleanup(work, job)
            return
        if not _idle(exe, 3):
            raise OSError("新版尚未退出。请关闭窗口后运行 recover.exe 以恢复旧版。")
        _restore(root, job, names)
        _record(work, "已恢复更新前的程序。")
        _cleanup(work, job)
        _launch(exe)
