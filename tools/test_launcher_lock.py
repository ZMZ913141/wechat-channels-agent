"""验证 launcher 的浏览器锁清理与活占用检测（真实 chromium/msedge）。

覆盖：
  A) 残留的「死进程」SingletonLock 会被安全删除（exitCode=21 的根因之一）
  B) 真有浏览器窗口占用档案时，能识别 PID 且不误删锁（避免双重打开损坏登录态）
"""
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright
from wechannels.launcher import (
    _profile_holder_pid,
    _clear_stale_singleton_lock,
    _cmd_uses_profile,
)

REAL_PROFILE = r"D:/github/wechat-channels-agent/profiles/1204215e"

passed = 0
failed = 0


def ck(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ---------- 边界匹配：acc2 不能被 acc22 误中 ----------
print("== 边界匹配（防 acc2 误中 acc22） ==")
T = r"d:\proj\profiles\acc2".lower()
ck("精确匹配算占用", _cmd_uses_profile(
    r'msedge.exe --user-data-dir=d:\proj\profiles\acc2', T))
ck("引号包裹算占用", _cmd_uses_profile(
    r'msedge.exe --user-data-dir="d:\proj\profiles\acc2"', T))
ck("acc22 不算占用", not _cmd_uses_profile(
    r'msedge.exe --user-data-dir=d:\proj\profiles\acc22', T))
ck("acc2x 不算占用", not _cmd_uses_profile(
    r'msedge.exe --user-data-dir=d:\proj\profiles\acc2x', T))

# ---------- A) 死锁清理（用临时档案，不碰真实登录态） ----------
print("== A) 残留死锁清理 ==")
tmp = tempfile.mkdtemp(prefix="wca_locktest_")
try:
    lockp = os.path.join(tmp, "SingletonLock")
    with open(lockp, "w") as f:
        f.write("DESKTOP-XYZ 999999 SomeMutexName")  # 999999 基本是死 PID
    ok, live = _clear_stale_singleton_lock(tmp)
    ck("识别为可清理(死锁)", ok is True and live is False, f"({ok},{live})")
    ck("SingletonLock 已被删除", not os.path.exists(lockp))
finally:
    for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(tmp, n))
        except OSError:
            pass
    try:
        os.rmdir(tmp)
    except OSError:
        pass

# ---------- B) 活占用检测（真实档案，只检测不删除） ----------
print("== B) 活窗口占用检测 ==")


def _hold():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=REAL_PROFILE, headless=True, channel="msedge",
            args=["--disable-gpu"],
        )
        ctx.new_page()
        time.sleep(10)
        ctx.close()


import threading
t = threading.Thread(target=_hold, daemon=True)
t.start()
time.sleep(3.5)

pid = _profile_holder_pid(REAL_PROFILE)
ck("检测到占用进程的 PID", isinstance(pid, int) and pid > 0, f"pid={pid}")

ok2, live2 = _clear_stale_singleton_lock(REAL_PROFILE)
ck("判定为活占用(live=True)", live2 is True, f"({ok2},{live2})")
ck("活占用时不清理(ok=False)", ok2 is False, f"({ok2},{live2})")
t.join(timeout=15)

print(f"\n结果：{passed} 通过 / {failed} 失败")
sys.exit(1 if failed else 0)
