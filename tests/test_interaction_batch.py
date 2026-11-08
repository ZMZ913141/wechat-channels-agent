"""互动批量操作 + 私信发送守卫回归。

- POST /api/comments/batch/status 批量忽略（含归属校验、路由不被 /{cid} 遮蔽）
- POST /api/comments/batch/draft 批量 AI 草稿（未配置 LLM 时计 failed 不 500）
- POST /api/messages/{mid}/send 守卫：非视频号 / 缺 session_id / 空内容
"""
import tempfile
from pathlib import Path

import wchannels.config as cfg

cfg.settings.SCHEDULER_ENABLED = False

import wchannels.db as dbmod
from wchannels.web.app import create_app
from wchannels.models import Account, Comment, PrivateMessage, WorkStat
from fastapi.testclient import TestClient
from sqlmodel import Session


def _client() -> TestClient:
    cfg.settings.WX_DATA_DIR = Path(tempfile.mkdtemp())
    dbmod._engine = None
    dbmod.init_db()
    return TestClient(create_app())


def _register(client, username) -> int:
    r = client.post("/api/auth/register",
                    json={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_account(session: Session, user_id: int, platform="wechat") -> int:
    a = Account(user_id=user_id, name="测试号", platform=platform,
                profile_dir=f"p_{user_id}_{platform}")
    session.add(a)
    session.commit()
    return a.id


def _seed_comment(session: Session, user_id: int, account_id: int,
                  content: str, status: str = "new") -> int:
    c = Comment(user_id=user_id, account_id=account_id, platform="wechat",
                author="观众", content=content, work_title="作品",
                comment_key=f"k_{content}", status=status)
    session.add(c)
    session.commit()
    return c.id


def test_batch_status_ignores_only_own_comments():
    client = _client()
    uid = _register(client, "batch_u1")
    with Session(dbmod.get_engine()) as s:
        aid = _seed_account(s, uid)
        ids = [_seed_comment(s, uid, aid, f"c{i}") for i in range(3)]

    r = client.post("/api/comments/batch/status",
                    json={"ids": ids, "status": "ignored"})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 3
    with Session(dbmod.get_engine()) as s:
        for cid in ids:
            assert s.get(Comment, cid).status == "ignored"


def test_batch_status_rejects_bad_status_and_empty_ids():
    client = _client()
    _register(client, "batch_u2")
    r = client.post("/api/comments/batch/status",
                    json={"ids": [1], "status": "bogus"})
    assert r.status_code == 400
    r = client.post("/api/comments/batch/status",
                    json={"ids": [], "status": "ignored"})
    assert r.status_code == 400


def test_batch_draft_processes_each_row():
    client = _client()
    uid = _register(client, "batch_u3")
    with Session(dbmod.get_engine()) as s:
        aid = _seed_account(s, uid)
        ids = [_seed_comment(s, uid, aid, f"c{i}") for i in range(2)]
    r = client.post("/api/comments/batch/draft", json={"ids": ids})
    assert r.status_code == 200, r.text
    body = r.json()
    # 每条都要么成功要么计入失败（取决于测试环境是否配了 LLM_API_KEY），不允许 500
    assert body["drafted"] + body["failed"] == 2
    with Session(dbmod.get_engine()) as s:
        for cid in ids:
            c = s.get(Comment, cid)
            if body["drafted"]:
                assert c.status == "drafted" and c.ai_draft
            else:
                assert c.status == "new"


def test_message_send_guards():
    client = _client()
    uid = _register(client, "msg_u1")
    with Session(dbmod.get_engine()) as s:
        aid = _seed_account(s, uid)
        dy = _seed_account(s, uid, platform="douyin")
        m_no_sid = PrivateMessage(user_id=uid, account_id=aid, platform="wechat",
                                  sender="甲", content="你好", msg_key="k1")
        m_dy = PrivateMessage(user_id=uid, account_id=dy, platform="douyin",
                              sender="乙", content="在吗", msg_key="k2")
        s.add(m_no_sid)
        s.add(m_dy)
        s.commit()
        mid_wechat, mid_dy = m_no_sid.id, m_dy.id

    # 非视频号平台 → 400 提示不支持
    r = client.post(f"/api/messages/{mid_dy}/send", json={"text": "回复"})
    assert r.status_code == 400
    assert "不支持" in r.json()["detail"]

    # 视频号但缺 session_id（旧数据）→ 400 提示重新抓取
    r = client.post(f"/api/messages/{mid_wechat}/send", json={"text": "回复"})
    assert r.status_code == 400
    assert "抓取" in r.json()["detail"]

    # 空内容 → 400
    r = client.post(f"/api/messages/{mid_wechat}/send", json={"text": "  "})
    assert r.status_code == 400


def test_message_send_ownership():
    client = _client()
    uid_a = _register(client, "msg_own_a")
    client.post("/api/auth/logout", json={})
    _register(client, "msg_own_b")
    with Session(dbmod.get_engine()) as s:
        aid = _seed_account(s, uid_a)
        m = PrivateMessage(user_id=uid_a, account_id=aid, platform="wechat",
                           sender="甲", content="你好", msg_key="kx")
        s.add(m)
        s.commit()
        mid = m.id
    # B 发 A 的私信 → 404
    r = client.post(f"/api/messages/{mid}/send", json={"text": "hi"})
    assert r.status_code == 404


def test_dashboard_trend_aggregates_and_compares():
    """趋势按天加总（缺采集日补 0），账号对比同作品只取最新一天。"""
    from datetime import datetime, timedelta
    d1 = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    d2 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    client = _client()
    uid = _register(client, "trend_u1")
    with Session(dbmod.get_engine()) as s:
        aid = _seed_account(s, uid)
        for day, views in ((d1, 100), (d2, 150)):
            s.add(WorkStat(user_id=uid, account_id=aid, platform="wechat",
                           work_id="w1", work_title="作品一", stat_date=day,
                           views=views, likes=10))
        s.add(WorkStat(user_id=uid, account_id=aid, platform="wechat",
                       work_id="w2", work_title="作品二", stat_date=d2,
                       views=50, likes=5))
        s.commit()

    r = client.get("/api/dashboard/trend?days=14")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["days"]) == 14
    day_map = {d["date"]: d for d in body["days"]}
    assert day_map[d1[5:]]["views"] == 100
    assert day_map[d2[5:]]["views"] == 200       # 两作品同日加总
    assert body["accounts"][0]["views"] == 200   # w1 只取最新 150 + w2 50
    assert body["accounts"][0]["likes"] == 15


def test_dashboard_trend_empty_is_ok():
    client = _client()
    _register(client, "trend_u2")
    r = client.get("/api/dashboard/trend")
    assert r.status_code == 200
    assert len(r.json()["days"]) == 14
    assert r.json()["accounts"] == []
