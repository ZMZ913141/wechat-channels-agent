"""发布审计 + 新增字段 + 档案锁一致性。

审计日志、合集/封面字段、以及「手动发布与调度器共用同一把档案锁」都是本次
补齐/修复的能力，此前没有任何测试覆盖，这里建立回归保护。

注意：必须在导入 db/app 之前把 WX_DATA_DIR 指向临时目录并关掉调度器，
否则引擎会绑定到默认（真实）数据库路径。
"""
import tempfile
from pathlib import Path

import wchannels.config as cfg

cfg.settings.SCHEDULER_ENABLED = False

import wchannels.db as dbmod
from wchannels.web.app import create_app
from wchannels.core import audit as audit_svc
from wchannels.models import Account, Material, PublishAudit, AuditSource
from fastapi.testclient import TestClient
from sqlmodel import Session, select


def _fresh_client() -> TestClient:
    """每个用例换一个全新临时目录并重建引擎，保证数据库彼此隔离。"""
    tmp = Path(tempfile.mkdtemp())
    cfg.settings.WX_DATA_DIR = tmp
    dbmod._engine = None
    dbmod.init_db()
    return TestClient(create_app())


def _seed(session: Session, user_id: int, platform: str, name: str, title: str):
    acc = Account(user_id=user_id, name=name, platform=platform, profile_dir=f"p_{user_id}")
    mat = Material(user_id=user_id, file_path=f"{name}.mp4", title=title, status="ready")
    session.add(acc)
    session.add(mat)
    session.commit()
    return acc, mat


def _register_and_login(client, username):
    client.post("/api/auth/register", json={"username": username, "password": "pw123456"})
    client.post("/api/auth/login", json={"username": username, "password": "pw123456"})


def _register_and_login(client, username) -> int:
    r = client.post("/api/auth/register", json={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    client.post("/api/auth/login", json={"username": username, "password": "pw123456"})
    return uid


def test_audit_table_created_by_init_db():
    _fresh_client()
    from sqlalchemy import inspect
    tables = set(inspect(dbmod.get_engine()).get_table_names())
    assert "publishtarget" in tables
    assert "publishaudit" in tables
    with Session(dbmod.get_engine()) as s:
        assert s.exec(select(PublishAudit)).all() == []


def test_audit_record_and_query_with_tenant_isolation():
    client = _fresh_client()
    uid = _register_and_login(client, "alice")
    with Session(dbmod.get_engine()) as s:
        acc, mat = _seed(s, uid, "wechat", "A1", "素材A")
        audit_svc.record(
            s, user_id=uid, account_id=acc.id, material_id=mat.id, platform="wechat",
            ok=True, message="发布成功", source=AuditSource.SCHEDULER.value,
            duration_ms=1234,
        )

    items = client.get("/api/audit").json()
    assert len(items) == 1
    assert items[0]["ok"] is True
    assert items[0]["platform"] == "wechat"
    assert items[0]["material"] == "素材A"
    assert items[0]["duration_ms"] == 1234

    # 租户隔离：bob 看不到 alice 的审计流水
    client.post("/api/auth/logout", json={})
    _register_and_login(client, "bob")
    assert client.get("/api/audit").json() == []


def test_audit_summary_and_filters():
    client = _fresh_client()
    uid = _register_and_login(client, "carol")
    with Session(dbmod.get_engine()) as s:
        acc, mat = _seed(s, uid, "douyin", "C1", "素材C")
        for ok in (True, False, False):
            audit_svc.record(
                s, user_id=uid, account_id=acc.id, material_id=mat.id,
                platform="douyin", ok=ok, message="ok" if ok else "登录态已失效",
                source=AuditSource.MANUAL.value,
            )

    summary = client.get("/api/audit/summary").json()
    assert summary["total"] == 3
    assert summary["success"] == 1
    assert summary["failed"] == 2
    assert summary["by_platform"]["douyin"]["total"] == 3
    assert summary["recent_failures"][0]["message"] == "登录态已失效"

    assert len(client.get("/api/audit?ok=false").json()) == 2
    assert len(client.get("/api/audit?platform=wechat").json()) == 0
    assert client.get("/api/audit?platform=bilibili").status_code == 400
    assert client.get("/api/audit/summary?days=999").status_code == 400


def test_material_collection_and_cover_roundtrip():
    """「选合集」与自定义封面必须能写入并回显，否则发布时永远传不到适配器。"""
    client = _fresh_client()
    uid = _register_and_login(client, "dave")
    with Session(dbmod.get_engine()) as s:
        m = Material(user_id=uid, file_path="d.mp4", title="素材D", status="ready")
        s.add(m)
        s.commit()
        mid = m.id

    r = client.put(f"/api/materials/{mid}", json={
        "collection": "秋日穿搭", "cover_path": "D:/x/cover_9.jpg", "ai_disclosure": "AI 生成"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["collection"] == "秋日穿搭"
    assert body["cover_path"] == "D:/x/cover_9.jpg"
    assert body["ai_disclosure"] == "AI 生成"

    listed = client.get("/api/materials").json()["items"]
    assert listed[0]["collection"] == "秋日穿搭"


def test_old_db_upgrade_adds_missing_columns(tmp_path):
    """真正的旧库升级：手工建一张「缺新列」的旧表，再跑 init_db 补列且不能丢数据。

    只在空库上 create_all 测不到 _ensure_columns（那时列本来就全）。
    """
    from sqlalchemy import inspect, text
    cfg.settings.WX_DATA_DIR = tmp_path
    dbmod._engine = None

    # 模拟 v3.0 旧库：三张表都缺少 v3.1 新增的列
    eng = dbmod.get_engine()
    with eng.begin() as conn:
        conn.execute(text(
            'CREATE TABLE account (id INTEGER PRIMARY KEY, user_id INTEGER, name VARCHAR, '
            '"group" VARCHAR DEFAULT \'\', profile_dir VARCHAR DEFAULT \'\', '
            "nickname_detected VARCHAR DEFAULT '', status VARCHAR DEFAULT 'active', "
            'last_login_at DATETIME, last_publish_at DATETIME, daily_publish_count INTEGER DEFAULT 0, '
            "default_topics VARCHAR DEFAULT '', default_comment VARCHAR DEFAULT '', "
            'created_at DATETIME)'))
        conn.execute(text(
            'CREATE TABLE material (id INTEGER PRIMARY KEY, user_id INTEGER, file_path VARCHAR, '
            'file_hash VARCHAR DEFAULT \'\', file_size INTEGER DEFAULT 0, duration_sec FLOAT DEFAULT 0, '
            'width INTEGER DEFAULT 0, height INTEGER DEFAULT 0, title VARCHAR DEFAULT \'\', '
            "short_title VARCHAR DEFAULT '', description VARCHAR DEFAULT '', "
            "topics VARCHAR DEFAULT '', ai_disclosure VARCHAR DEFAULT 'AI 制作', "
            "status VARCHAR DEFAULT 'inbox', thumbnail_path VARCHAR DEFAULT '', "
            'cover_path VARCHAR DEFAULT \'\', created_at DATETIME, updated_at DATETIME)'))
        conn.execute(text(
            'CREATE TABLE publishtarget (id INTEGER PRIMARY KEY, batch_id INTEGER, '
            'material_id INTEGER, account_id INTEGER, short_title VARCHAR DEFAULT \'\', '
            "description VARCHAR DEFAULT '', topics VARCHAR DEFAULT '', "
            "ai_disclosure VARCHAR DEFAULT 'AI 制作', scheduled_at DATETIME, "
            "comment VARCHAR DEFAULT '', status VARCHAR DEFAULT 'queued', "
            "result_message VARCHAR DEFAULT '', screenshot_path VARCHAR DEFAULT '', "
            'published_at DATETIME)'))
    # 旧库里已有一条真实数据（用原生 SQL 写入——旧表结构还没有新列，ORM 插入会失败）
    with eng.begin() as conn:
        conn.execute(text(
            'INSERT INTO account (user_id, name, "group", profile_dir, status, daily_publish_count) '
            "VALUES (1, '旧账号', '', 'old_p', 'active', 2)"))
    dbmod._engine = None  # 让 init_db 重新按当前配置取引擎

    dbmod.init_db()  # 应补齐所有缺失列，且不动已有数据

    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "publishtarget" in tables and "publishaudit" in tables

    def cols(t):
        return {c["name"] for c in insp.get_columns(t)}
    assert "platform" in cols("account")
    assert "collection" in cols("material")
    assert {"collection", "cover_path"} <= cols("publishtarget")
    with Session(eng) as s:
        old = s.get(Account, 1)
        assert old is not None and old.name == "旧账号"
        assert old.daily_publish_count == 2      # 原数据未被破坏
        assert old.platform == "wechat"          # 旧行按默认值回填


def test_audit_export_csv_and_tenant_isolation():
    """CSV 导出：带 BOM、表头中文化、内容转义；且只导自己的流水。"""
    client = _fresh_client()
    uid = _register_and_login(client, "eve")
    with Session(dbmod.get_engine()) as s:
        acc, mat = _seed(s, uid, "wechat", "E1", "素材E,带逗号")
        audit_svc.record(
            s, user_id=uid, account_id=acc.id, material_id=mat.id, platform="wechat",
            ok=True, message="发布成功", source=AuditSource.SCHEDULER.value,
            duration_ms=1500,
        )

    r = client.get("/api/audit/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.content.decode("utf-8")
    assert body.startswith("\ufeff")              # BOM：Excel 直开不乱码
    lines = [ln for ln in body.splitlines() if ln]
    assert lines[0].lstrip("\ufeff") == "时间,账号,平台,素材,结果,来源,耗时(秒),说明"
    assert "视频号" in lines[1] and "素材E,带逗号" in lines[1]  # csv 模块自动加引号
    assert "1.5" in lines[1]                        # 耗时换算成秒

    # 筛选参数透传 + 租户隔离
    assert len(client.get("/api/audit/export.csv?ok=false").content.decode("utf-8").splitlines()) == 1
    assert client.get("/api/audit/export.csv?platform=bilibili").status_code == 400
    client.post("/api/auth/logout", json={})
    _register_and_login(client, "frank")
    lines = client.get("/api/audit/export.csv").content.decode("utf-8").splitlines()
    assert len([ln for ln in lines if ln]) == 1     # 只剩表头


def test_audit_purge_older_than():
    """保留期清理：老记录被删、新记录保留；days<=0 不动。"""
    from datetime import timedelta
    from wchannels.timeutils import now as utcnow
    client = _fresh_client()
    uid = _register_and_login(client, "gina")
    with Session(dbmod.get_engine()) as s:
        acc, mat = _seed(s, uid, "douyin", "G1", "素材G")
        old = audit_svc.record(
            s, user_id=uid, account_id=acc.id, material_id=mat.id,
            platform="douyin", ok=True, message="老记录")
        new = audit_svc.record(
            s, user_id=uid, account_id=acc.id, material_id=mat.id,
            platform="douyin", ok=False, message="新记录")
        # 把一条改成 100 天前
        old.created_at = utcnow() - timedelta(days=100)
        s.add(old)
        s.commit()

        assert audit_svc.purge_older_than(s, 0) == 0      # 0 = 永久保留
        assert audit_svc.purge_older_than(s, 90) == 1
        remaining = s.exec(select(PublishAudit)).all()
        assert len(remaining) == 1
        assert remaining[0].message == "新记录"


def test_profile_lock_same_key_for_relative_and_absolute(tmp_path, monkeypatch):
    """回归：调度器传相对目录名、面板传绝对路径，两者必须落到同一把锁。

    此前一个用 str(Path)、一个用 hash() 且受 PYTHONHASHSEED 影响，
    两条发布链路会拿到不同锁文件，从而同时打开同一 Chrome 档案。
    """
    from wchannels.browser.proclock import ProfileLock

    # profiles_dir 是由 WX_DATA_DIR 派生的只读属性，只需重定向数据目录
    monkeypatch.setattr(cfg.settings, "WX_DATA_DIR", tmp_path)
    rel = ProfileLock("acc_wechat_20261108")
    absolute = ProfileLock(cfg.settings.profiles_dir / "acc_wechat_20261108")
    assert rel.path == absolute.path

    other = ProfileLock(cfg.settings.profiles_dir / "acc_douyin_20261108")
    assert other.path != rel.path
