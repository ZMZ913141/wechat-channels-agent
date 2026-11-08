"""租户隔离回归：写入路径必须校验资源归属。

背景：此前只有「列表查询」做了 user_id 过滤，所有按 ID 取数的写接口
（改素材、删账号、设封面、用他人素材排期）都没有校验，
任何登录用户都能操作别人的数据——文档中「租户隔离已验证」只覆盖了读路径。
本文件锁住修复结果。
"""
import tempfile
from pathlib import Path

import wchannels.config as cfg

cfg.settings.SCHEDULER_ENABLED = False

import wchannels.db as dbmod
from wchannels.web.app import create_app
from wchannels.models import Account, Material
from fastapi.testclient import TestClient
from sqlmodel import Session


def _client() -> TestClient:
    cfg.settings.WX_DATA_DIR = Path(tempfile.mkdtemp())
    dbmod._engine = None
    dbmod.init_db()
    return TestClient(create_app())


def _register(client, username) -> int:
    r = client.post("/api/auth/register", json={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_material(session: Session, user_id: int, title: str) -> int:
    m = Material(user_id=user_id, file_path=f"{title}.mp4", title=title, status="ready")
    session.add(m)
    session.commit()
    return m.id


def _seed_account(session: Session, user_id: int, name: str) -> int:
    a = Account(user_id=user_id, name=name, platform="wechat", profile_dir=f"p_{user_id}")
    session.add(a)
    session.commit()
    return a.id


def test_cross_user_material_update_forbidden():
    client = _client()
    uid_b = _register(client, "victim_b")
    client.post("/api/auth/logout", json={})
    uid_a = _register(client, "attacker_a")

    with Session(dbmod.get_engine()) as s:
        mid_b = _seed_material(s, uid_b, "B的素材")

    # A 试图改 B 的素材 —— 必须 404，且不能被写入
    r = client.put(f"/api/materials/{mid_b}", json={"collection": "窃取合集"})
    assert r.status_code == 404, r.text
    with Session(dbmod.get_engine()) as s:
        assert s.get(Material, mid_b).collection == ""


def test_cross_user_material_delete_and_ready_forbidden():
    client = _client()
    uid_b = _register(client, "victim_b2")
    client.post("/api/auth/logout", json={})
    _register(client, "attacker_a2")

    with Session(dbmod.get_engine()) as s:
        mid_b = _seed_material(s, uid_b, "B的素材2")

    assert client.delete(f"/api/materials/{mid_b}").status_code == 404
    assert client.post(f"/api/materials/{mid_b}/to-ready").status_code == 404
    assert client.post(f"/api/materials/{mid_b}/autofill", json={"extra_hint": ""}).status_code == 404
    assert client.post(f"/api/cover/{mid_b}/extract-frame?at_sec=1").status_code == 404

    with Session(dbmod.get_engine()) as s:
        assert s.get(Material, mid_b).status != "trash"


def test_cross_user_account_delete_and_login_forbidden():
    client = _client()
    uid_b = _register(client, "victim_c")
    client.post("/api/auth/logout", json={})
    _register(client, "attacker_c")

    with Session(dbmod.get_engine()) as s:
        aid_b = _seed_account(s, uid_b, "B的号")

    # 删除他人账号：必须拒绝，否则可直接破坏别人的资产
    assert client.delete(f"/api/accounts/{aid_b}").status_code == 404
    # 对他人账号发起扫码登录：会拿到别人的登录态，属严重越权
    assert client.post(f"/api/accounts/{aid_b}/login").status_code == 404
    assert client.post(f"/api/accounts/{aid_b}/login-probe").status_code == 404

    with Session(dbmod.get_engine()) as s:
        assert s.get(Account, aid_b) is not None


def test_batch_creation_rejects_foreign_resources():
    """用他人素材/账号建批次，会被过滤；全非法则 400。"""
    client = _client()
    uid_b = _register(client, "victim_d")
    client.post("/api/auth/logout", json={})
    uid_a = _register(client, "attacker_d")

    with Session(dbmod.get_engine()) as s:
        mid_b = _seed_material(s, uid_b, "B的素材D")
        aid_b = _seed_account(s, uid_b, "B的号D")
        mid_a = _seed_material(s, uid_a, "A的素材D")
        aid_a = _seed_account(s, uid_a, "A的号D")

    # 全部属于 B —— 必须 400
    r = client.post("/api/publish", json={"material_ids": [mid_b], "account_ids": [aid_b]})
    assert r.status_code == 400, r.text

    # 混合：A 的资源合法、B 的被忽略，批次只含 A 自己的
    r2 = client.post("/api/publish",
                     json={"material_ids": [mid_a, mid_b], "account_ids": [aid_a, aid_b]})
    assert r2.status_code == 200, r2.text
    from wchannels.models import PublishTarget
    from sqlmodel import select
    with Session(dbmod.get_engine()) as s:
        targets = list(s.exec(select(PublishTarget).where(
            PublishTarget.batch_id == r2.json()["batch_id"])).all())
        assert len(targets) == 1
        assert targets[0].material_id == mid_a
        assert targets[0].account_id == aid_a

    # 智能排期同样拒绝越权资源
    r3 = client.post("/api/automation/smart-schedule",
                     json={"material_ids": [mid_b], "account_ids": [aid_b]})
    assert r3.status_code == 400, r3.text
