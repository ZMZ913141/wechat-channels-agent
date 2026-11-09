"""登录态保活：首轮延迟、禁用不动、到点重排并起任务。"""
import threading
import time

import wchannels.config as cfg
from wchannels.scheduler import Scheduler


def _make(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setattr(cfg.settings, k, v)
    return Scheduler()


def test_disabled_does_nothing(monkeypatch):
    s = _make(monkeypatch, LOGIN_KEEPALIVE_ENABLED=False)
    s._keepalive_tick()
    assert s._next_keepalive is None


def test_first_tick_schedules_after_start_delay(monkeypatch):
    s = _make(monkeypatch, LOGIN_KEEPALIVE_ENABLED=True,
              LOGIN_KEEPALIVE_START_DELAY_S=600)
    before = time.time()
    s._keepalive_tick()
    assert s._next_keepalive is not None
    assert before + 590 <= s._next_keepalive <= time.time() + 610


def test_due_tick_reschedules_and_triggers_job(monkeypatch):
    s = _make(monkeypatch, LOGIN_KEEPALIVE_ENABLED=True,
              LOGIN_KEEPALIVE_INTERVAL_S=10800, LOGIN_KEEPALIVE_STAGGER_S=0)
    fired = threading.Event()
    monkeypatch.setattr(s, "_keepalive_job", fired.set)
    s._next_keepalive = time.time() - 1
    before = time.time()
    s._keepalive_tick()
    # 下一轮已预约（3 小时 ±10%）
    assert s._next_keepalive >= before + 10800 * 0.9 - 5
    assert fired.wait(5), "到点未启动保活任务"
