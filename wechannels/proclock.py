"""浏览器档案独占锁 + 「手动操作」暂停窗口 + 账号级任务锁。

## 为什么需要这个模块

一个 Chromium 档案（`--user-data-dir`）**同一时刻只能被一个进程真正占用**。
视频号助手这套工具里，会用同一个档案开浏览器的有：

  * 面板「打开发表页」（`open_publish.py` → `launcher.open_publish_page`）；
  * 面板「扫码登录」（同上，`--login`）；
  * 「同步发布」（`publish_pw` / `publish.py`）。

只要有两方同时打开同一个档案，后开的那一方就会**拿不到登录态**：
页面能打开，但只剩二维码，看起来就像「明明刚登录过却显示未登录」。
两个账号同时被发布时更容易复现，所以用户会觉得「两个账号冲突了」。

本模块提供三样东西来根治它：

1. `acquire()/release()` —— 档案级独占锁（文件锁 + 持有进程存活校验）。
   谁要开档案，先抢锁；抢不到就**明确报错并告诉你是谁占着**，而不是静默开一个没登录的窗口。
2. `account_lock()/release_account()` —— 账号级任务锁。
   档案锁只保证「档案不被两个浏览器同时开」，但**同一账号**可能仍被两个发布
   任务先后进入（前者刚释放、后者立刻抢到），导致对同一个视频号重复提交。
   账号锁补上这一层，并发发布时第二个任务会明确收到「该账号正被另一个任务占用」。
3. `pause()/paused()` —— 「手动操作」暂停窗口。面板要打开某个账号的档案时，
   先给这个账号打一个暂停标记，让后台任务看到标记就**跳过该账号**，从而把档案让出来。

锁文件：`state/profile_<hash>.lock` / `state/task_<account_id>.lock`；
暂停标记：`state/manual_pause.json`。
"""
import hashlib
import json
import os
import time

from . import config


def _pid_alive(pid: int) -> bool:
    """进程存活判断。Windows 上不能用 os.kill(pid,0)，走 Win32 API。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        import ctypes

        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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


def account_id_of_profile(profile_dir: str) -> str:
    """由档案路径反查所属账号 id（用于关联暂停窗口）。查不到返回 ""。"""
    try:
        from . import accounts as _acc

        target = os.path.abspath(profile_dir).lower()
        for a in _acc.load_accounts():
            try:
                if os.path.abspath(_acc.profile_path(a)).lower() == target:
                    return a["id"]
            except Exception:
                continue
    except Exception:
        pass
    return ""


def acquire(profile_dir: str, tag: str, wait_s: float = 0.0, poll_s: float = 1.5,
            respect_pause: bool = True) -> tuple:
    """抢档案锁。返回 (True, None) 或 (False, 占用者信息 dict)。

    wait_s > 0 时最多等待这么久。

    respect_pause：自动流程（发布等）设为 True —— 若该档案所属账号正处于
    「手动操作暂停窗口」（用户正在扫码登录/打开发表页），**直接拒绝**而不是
    抢锁。原因：手动操作是用户显式发起的、优先级高于后台自动流程；
    而且手动窗口可能长达 30 分钟，等下去毫无意义。
    手动操作自己调acquire 时传 False，否则会被自己的标记挡住。
    """
    if respect_pause:
        acc_id = account_id_of_profile(profile_dir)
        if acc_id:
            p = paused(acc_id)
            if p:
                left = max(0, int(float(p.get("until", 0)) - time.time()))
                return False, {
                    "pid": 0,
                    "tag": f"手动操作中（{p.get('reason', '被打开发表页')}，剩约 {left // 60} 分钟）",
                }

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
# 账号级任务锁（防同一账号被并发发布）
# ---------------------------------------------------------------------------
def _task_lock_path(account_id: str) -> str:
    safe = "".join(c for c in str(account_id) if c.isalnum() or c in ("-", "_")) or "unknown"
    return os.path.join(config.STATE_DIR, f"task_{safe}.lock")


def acquire_account(account_id: str, tag: str = "", wait_s: float = 0.0,
                    poll_s: float = 1.5) -> tuple:
    """抢账号级锁。返回 (True, None) 或 (False, 占用者信息)。

    与档案锁的区别：档案锁保护「浏览器档案不被两个进程同时打开」，
    账号锁保护「同一个视频号不会被两个发布任务同时提交」。
    两者叠加才安全——尤其是多账号同时发布时。
    """
    deadline = time.time() + max(0.0, float(wait_s))
    path = _task_lock_path(account_id)
    while True:
        holder = read_file_lock(path)
        if holder is None:
            break
        if time.time() >= deadline:
            return False, holder
        time.sleep(poll_s)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "pid": os.getpid(),
                    "tag": tag or "发布任务",
                    "account_id": account_id,
                    "ts": time.time(),
                    "since": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                f,
                ensure_ascii=False,
            )
        return True, None
    except Exception as e:
        return False, {"pid": 0, "tag": f"写入锁失败：{e}"}


def release_account(account_id: str) -> None:
    """释放自己持有的账号锁（别人的锁不动）。"""
    _remove_if_mine(_task_lock_path(account_id))


def read_file_lock(path: str) -> dict | None:
    """通用锁文件读取：不存在、损坏或持有进程已死都视为「没有锁」，并顺手清理残留。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    pid = int(d.get("pid") or 0)
    if pid and _pid_alive(pid):
        return d
    try:
        os.remove(path)   # 残留锁：持有进程已不存在
    except Exception:
        pass
    return None


def _remove_if_mine(path: str) -> None:
    try:
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if int(d.get("pid") or 0) not in (0, os.getpid()):
            return
        os.remove(path)
    except Exception:
        pass


def clear_stale_task_locks() -> int:
    """清理所有残留的账号锁，返回清理数量。面板「清理残留锁」按钮用。"""
    n = 0
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        for name in os.listdir(config.STATE_DIR):
            if not (name.startswith("task_") or name.startswith("profile_")) or not name.endswith(".lock"):
                continue
            p = os.path.join(config.STATE_DIR, name)
            if read_file_lock(p) is None and os.path.exists(p):
                n += 1
    except Exception as e:
        print(f"[WARN] 清理残留锁失败：{str(e)[:60]}")
    return n


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
