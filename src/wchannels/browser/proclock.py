"""两层锁：账号锁（同账号不并发）+ 档案锁（同浏览器档案不并发）。

含残留锁自愈：进程被强杀后，若锁文件里记录的 PID 已不在，自动清除，
避免账号永久卡死（此前 ARCHITECTURE.md 声称有自愈，代码其实没实现）。
"""
import os
import time
from pathlib import Path
from ..config import settings


class LockBusy(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    """跨平台判断进程是否还活着。Windows 用 ctypes 查句柄，POSIX 用 kill(pid,0)。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                return code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
    except Exception:
        return False


class _FileLock:
    """基于文件的互斥锁，带残留锁自愈。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._held = False

    def _break_if_stale(self) -> None:
        """若持有者进程已死，清掉残留锁。"""
        try:
            if not self.path.exists():
                return
            txt = self.path.read_text(encoding="utf-8", errors="ignore")
            pid = -1
            if "pid=" in txt:
                pid = int(txt.split("pid=", 1)[1].split()[0])
            if not _pid_alive(pid):
                self.path.unlink(missing_ok=True)
        except Exception:
            # 读不到就不动，交给 acquire 的下一次循环
            pass

    def acquire(self, timeout: int = 0) -> None:
        start = time.time()
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"pid={os.getpid()} start={time.time()}".encode())
                os.close(fd)
                self._held = True
                return
            except FileExistsError:
                self._break_if_stale()
                if self.path.exists():
                    # 锁仍存在，说明被活进程持有
                    if time.time() - start > timeout:
                        raise LockBusy(f"锁被占用：{self.path}")
                    time.sleep(1)
                # 否则残留锁已被自愈清除，立即进入下一轮重试

    def release(self) -> None:
        if self._held and self.path.exists():
            try:
                self.path.unlink()
            except Exception:
                pass
        self._held = False


class AccountLock(_FileLock):
    """账号锁：同一账号同时只允许一个发布任务。"""

    def __init__(self, account_id: int):
        super().__init__(settings.WX_DATA_DIR / f"lock_acc_{account_id}.lock")


class ProfileLock(_FileLock):
    """档案锁：同一 Chrome user_data_dir 不被两个进程打开。

    关键点：
    1. 路径先归一化成绝对路径——否则调用方一处传相对、一处传绝对，
       会得到两把不同的锁，手动发布与后台调度器就能同时打开同一档案。
       相对名按 profiles_dir 解析（调用方通常只存目录名）。
    2. 摘要用 md5 而不是内置 hash()——CPython 的 str 哈希受 PYTHONHASHSEED
       影响，跨进程随机，同样会算出不同的锁文件。
    """

    def __init__(self, profile_dir: Path):
        import hashlib

        p = Path(profile_dir)
        if not p.is_absolute():
            p = settings.profiles_dir / p
        norm = str(p.resolve()).replace("\\", "/").lower()
        h = hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]
        super().__init__(settings.WX_DATA_DIR / f"lock_profile_{h}.lock")
