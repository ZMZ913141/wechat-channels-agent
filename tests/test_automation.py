"""回归：smart-schedule 必须写入带时区的 datetime（否则 SQLAlchemy 2.x 拒绝 naive）。

注意：必须在导入 app/db 之前重定向 WX_DATA_DIR 与关掉调度器，
否则 db.engine 在导入时已绑定默认数据库路径，monkeypatch 来不及生效。
"""
import tempfile
from datetime import datetime
from pathlib import Path

import wchannels.config as cfg

_TMP = Path(tempfile.mkdtemp())
cfg.settings.WX_DATA_DIR = _TMP
cfg.settings.SCHEDULER_ENABLED = False

from wchannels.web.app import create_app
from wchannels.db import get_engine, init_db
from sqlmodel import Session
from wchannels.models import Account, Material


def test_smart_schedule_writes_tz_aware():
    import wchannels.db as dbmod
    # 测试期（而非模块导入期）重定向数据目录：其他测试模块也会改这个全局值，
    # 只有这里现取现用才能保证引擎落在本用例独占的临时库上。
    cfg.settings.WX_DATA_DIR = Path(tempfile.mkdtemp())
    dbmod._engine = None
    init_db()
    app = create_app()
    from fastapi.testclient import TestClient
    with TestClient(app) as client:
        r = client.post("/api/auth/register", json={"username": "sz", "password": "pw123456"})
        assert r.status_code == 200, r.text
        uid = r.json()["id"]  # 不能假定是 1：库里可能已有其他用例留下的用户
        client.post("/api/auth/login", json={"username": "sz", "password": "pw123456"})
        with Session(get_engine()) as s:
            a = Account(user_id=uid, name="t", platform="wechat", profile_dir="p1")
            m = Material(user_id=uid, file_path="x.mp4", file_name="x.mp4",
                         duration_sec=2, width=720, height=1280, title="t", status="ready")
            s.add(a); s.add(m); s.commit()
            aid, mid = a.id, m.id
        r = client.post("/api/automation/smart-schedule",
                        json={"material_ids": [mid], "account_ids": [aid]})
        assert r.status_code == 200, r.text
        dt = datetime.fromisoformat(r.json()["first_slot"])
        assert dt.tzinfo is not None
