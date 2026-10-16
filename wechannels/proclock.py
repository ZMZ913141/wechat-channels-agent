"""浏览器档案独占锁 + 「手动操作」暂停窗口。

## 为什么需要这个模块

一个 Chromium 档案（`--user-data-dir`）**同一时刻只能被一个进程真正占用**。
视频号助手这套工具里，会用同一个档案开浏览器的有三方：

  * 后台监控（`monitor_dm.py` → `monitor_pw.grab`）——每 `MONITOR_INTERVAL` 秒一轮；
  * 面板「打开发表页」（`open_publish.py` → `launcher.open_publish_page`）；
  * 面板「扫码登录」（同上，`--login`）与「同步发布」（`publish_pw`）。

只要有两方同时打开同一个档案，后开的那一方就会**拿不到登录态**：
页面能打开，但只剩二维码，看起来就像「明明刚登录过却显示未登录」。
两个账号同时被监控时更容易复现，所以用户会觉得「两个账号冲突了」。

本模块提供两个东西来根治它：

1. `acquire()/release()` —— 档案级独占锁（文件锁 + 持有进程存活校验）。
   谁要开档案，先抢锁；抢不到就**明确报错并告诉你是谁占着**，而不是静默开一个没登录的窗口。
2. `pause()/paused()` —— 「手动操作」暂停窗口。面板要打开某个账号的档案时，
   先给这个账号打一个暂停标记，后台监控看到标记就**跳过该账号**，从而把档案让出来。

锁文件：`state/profile_<hash>.lock`；暂停标记：`state/manual_pause.json`。
"""
import hashlib
import json
import os
import time

from . import config


def _pid_alive(pid: int) -> bool:
    """进程存活判断。复用 monitor 里的实现（Windows 上不能用 os.kill(pid,0)）。"""
    try:
        from .monitor import pid_alive

        return pid_alive(pid)
    except Exception:  # monitor 尚在导入 / 不可用时的兜底
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        try:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == 259  # STILL_ACTIVE
                return True
            finally:
                k.CloseHandle(h)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# 档案独占锁
# ---------------------------------------------------------------------------
def _lock_path(profile_dir: str) -> str:
    ap = os.path.abspath(profile_dir).lower().replace("\\", "/")
    h = hashlib.sha1(ap.encode("utf-8")).hexdigest()[:10]
    return os.path.join(config.STATE_DIR, f"profile_{h}.lock")


def read_holder(profile_dir: str) -> dict | None:
    """返回当前持锁者信息；锁不存在或持有进程已死（残留）则返回 None 并顺手清理。"""
    p = _lock_path(profile_dir)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    pid = int(d.get("pid") or 0)
    if pid and _pid_alive(pid):
        return d
    # 残留锁：持有进程已不存在 → 清掉
    try:
        os.remove(p)
    except Exception:
        pass
    return None


def acquire(profile_dir: str, tag: str, wait_s: float = 0.0, poll_s: float = 1.5) -> tuple:
    """抢档案锁。返回 (True, None) 或 (False, 占用者信息 dict)。

    wait_s > 0 时最多等待这么久（用于「先暂停监控 → 等它把档案放出来」）。
    """
    deadline = time.time() + max(0.0, float(wait_s))
    while True:
        holder = read_holder(profile_dir)
        if holder is None:
            break
        if time.time() >= deadline:
            return False, holder
        time.sleep(poll_s)
    p = _lock_path(profile_dir)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pid": os.getpid(),
                    "tag": tag,
                    "profile": os.path.abspath(profile_dir),
                    "ts": time.time(),
                    "since": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                f,
                ensure_ascii=False,
            )
        return True, None
    except Exception as e:
        return False, {"pid": 0, "tag": f"写入锁失败：{e}"}


def release(profile_dir: str) -> None:
    """释放自己持有的锁（别人的锁不动）。"""
    p = _lock_path(profile_dir)
    try:
        if not os.path.exists(p):
            return
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if int(d.get("pid") or 0) not in (0, os.getpid()):
            return
        os.remove(p)
    except Exception:
        pass


def holder_desc(holder: dict | None) -> str:
    if not holder:
        return "未知进程"
    tag = holder.get("tag") or "?"
    pid = holder.get("pid") or "?"
    since = holder.get("since") or "?"
    return f"{tag}（PID {pid}，自 {since}）"


# ---------------------------------------------------------------------------
# 「手动操作」暂停窗口：让后台监控把手动账号的档案让出来
# ---------------------------------------------------------------------------
_PAUSE_FILE = os.path.join(config.STATE_DIR, "manual_pause.json")
# 默认暂停时长：足够扫码/发表操作（到点自动恢复，不会把监控永久停掉）
DEFAULT_PAUSE_S = 1800


def _read_pause() -> dict:
    try:
        with open(_PAUSE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_pause(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_PAUSE_FILE), exist_ok=True)
        with open(_PAUSE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def pause(account_id: str, seconds: int = DEFAULT_PAUSE_S, reason: str = "") -> dict:
    """给某账号打暂停标记（后台监控会跳过它，把浏览器档案让给手动操作）。"""
    d = _read_pause()
    now = time.time()
    d[account_id] = {
        "until": now + max(60, int(seconds)),
        "reason": reason or "手动操作（打开发表页 / 扫码登录）",
        "since": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_pause(d)
    return d[account_id]


def resume(account_id: str) -> None:
    d = _read_pause()
    if account_id in d:
        d.pop(account_id, None)
        _write_pause(d)


def paused(account_id: str) -> dict | None:
    """该账号是否处于暂停窗口内（已过期的自动清理）。"""
    d = _read_pause()
    it = d.get(account_id)
    if not it:
        return None
    if float(it.get("until", 0)) <= time.time():
        d.pop(account_id, None)
        _write_pause(d)
        return None
    return it


def paused_all() -> dict:
    """返回所有仍在暂停窗口内的账号标记（顺手清理过期项）。"""
    d = _read_pause()
    now = time.time()
    keep = {k: v for k, v in d.items() if float(v.get("until", 0)) > now}
    if len(keep) != len(d):
        _write_pause(keep)
    return keep


def paused_desc(account_id: str) -> str:
    it = paused(account_id)
    if not it:
        return ""
    left = max(0, int(float(it.get("until", 0)) - time.time()))
    return f"已为「手动操作」暂停（剩约 {left // 60} 分钟）：{it.get('reason','')}"
