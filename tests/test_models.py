"""模型与能力层基本契约。"""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import wchannels.config as cfg

cfg.settings.SCHEDULER_ENABLED = False

from wchannels.models.account import Account
from wchannels.models import AccountStatus


def test_account_default_platform():
    a = Account(name="x")
    assert a.platform == "wechat"


def _quota_session():
    import wchannels.db as dbmod
    from sqlmodel import Session
    cfg.settings.WX_DATA_DIR = Path(tempfile.mkdtemp())
    dbmod._engine = None
    dbmod.init_db()
    return Session(dbmod.get_engine())


def test_daily_limit_resets_across_days():
    """回归：每日发布数必须跨天归零，否则号发满上限后永久瘫掉。"""
    from wchannels.core import accounts as acc_svc

    with _quota_session() as s:
        limit = cfg.settings.PLATFORM_DAILY_LIMITS["wechat"]
        a = Account(user_id=1, name="A", platform="wechat", profile_dir="p",
                    status=AccountStatus.ACTIVE.value,
                    daily_publish_count=limit,
                    last_publish_at=datetime.now(timezone.utc))
        s.add(a)
        s.commit()

        # 今天已达上限 —— 拒绝
        ok, reason = acc_svc.can_publish(s, a)
        assert ok is False
        assert "上限" in reason

        # 把上次发布时间挪到昨天 —— 应自动归零并放行
        a.last_publish_at = datetime.now(timezone.utc) - timedelta(days=1)
        s.add(a)
        s.commit()
        ok2, _ = acc_svc.can_publish(s, a)
        assert ok2 is True
        assert a.daily_publish_count == 0


def test_paused_and_logged_out_blocked():
    from wchannels.core import accounts as acc_svc
    with _quota_session() as s:
        a = Account(user_id=1, name="B", platform="wechat", profile_dir="p",
                    status=AccountStatus.LOGGED_OUT.value)
        s.add(a)
        s.commit()
        ok, reason = acc_svc.can_publish(s, a)
        assert ok is False and "扫码" in reason
