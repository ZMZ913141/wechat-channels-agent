"""两层锁：acquire/release 与残留锁自愈。"""
import time

import wchannels.config as cfg
from wchannels.browser.proclock import AccountLock, _pid_alive


def test_pid_alive_false_for_garbage():
    assert _pid_alive(9_999_999) is False


def test_account_lock_acquire_release(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.settings, "WX_DATA_DIR", tmp_path)
    lock = AccountLock(123)
    lock.acquire()
    assert lock._held and lock.path.exists()
    lock.release()
    assert not lock.path.exists()


def test_stale_lock_self_heal(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.settings, "WX_DATA_DIR", tmp_path)
    lock = AccountLock(456)
    # 模拟“被强杀进程”留下的残留锁（PID 不存在）
    lock.path.write_text("pid=9999999 start=0")
    lock.acquire()  # 应自动识别并清除残留锁
    assert lock._held
    lock.release()
