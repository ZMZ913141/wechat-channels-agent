"""v3.3 代码质量项的回归测试：话题归一化、统一错误格式、配额即时显示。

注意：必须在导入 db/app 之前把 WX_DATA_DIR 指向临时目录并关掉调度器，
否则引擎会绑定到默认（真实）数据库路径。
"""
import tempfile
from datetime import timedelta
from pathlib import Path

import wchannels.config as cfg

cfg.settings.SCHEDULER_ENABLED = False

import wchannels.db as dbmod
from wchannels.web.app import create_app
from wchannels.models import Account
from wchannels.timeutils import now as utcnow
from fastapi.testclient import TestClient
from sqlmodel import Session


def _fresh_client() -> TestClient:
    tmp = Path(tempfile.mkdtemp())
    cfg.settings.WX_DATA_DIR = tmp
    dbmod._engine = None
    dbmod.init_db()
    return TestClient(create_app(), raise_server_exceptions=False)


def _register_and_login(client, username) -> int:
    r = client.post("/api/auth/register", json={"username": username, "password": "pw123456"})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    client.post("/api/auth/login", json={"username": username, "password": "pw123456"})
    return uid


# ---------- 话题归一化 ----------

def test_normalize_topics_formats():
    from wchannels.browser.platforms._base import normalize_topics as nt
    assert nt("") == []
    assert nt("女装,穿搭") == ["女装", "穿搭"]
    assert nt("女装，穿搭") == ["女装", "穿搭"]           # 中文逗号
    assert nt("#女装 #穿搭") == ["女装", "穿搭"]           # 井号+空格分隔
    assert nt(" #女装 , 穿搭 ,") == ["女装", "穿搭"]       # 空格/空项
    assert nt("女装,女装,#女装") == ["女装"]               # 去重
    assert nt(",,,") == []


def test_publish_normalizes_topics_before_fill(monkeypatch):
    """Publisher.publish 必须把脏话题收敛后再交给适配器（走完整流程验证）。"""
    import wchannels.browser.platforms._base as base

    captured = {}

    class FakeElement:
        def set_input_files(self, path): pass

    class FakePage:
        frames = []
        url = "http://x"
        def goto(self, *a, **k): pass
        def wait_for_timeout(self, ms): pass
        def screenshot(self, **k): pass

    class FakeCtx:
        pages = [FakePage()]

    class Probe(base.Publisher):
        platform = "probe"
        publish_url = "http://x"
        def fill_form(self, page, inp, title): captured["topics"] = inp.topics
        def click_publish(self, page): pass
        def verify(self, page): return True, "ok"

    monkeypatch.setattr(base, "validate_video", lambda p: None)
    monkeypatch.setattr(base, "find_file_input", lambda page: FakeElement())

    out = Probe().publish(FakeCtx(), base.PublishInput(
        video_path="v.mp4", short_title="标题", topics="#女装 #穿搭,女装"))
    assert out.ok
    assert captured["topics"] == "女装,穿搭"


def test_ai_autofill_strips_hash_from_llm_topics(monkeypatch):
    from wchannels.ai import autofill
    monkeypatch.setattr(autofill.client, "chat",
                        lambda *a, **k: '{"title":"秋日上新","description":"d","topics":["#女装","#穿搭 技巧"]}')
    out = autofill.generate_copy("x.mp4", platform="douyin")
    assert out["ai_used"] is True
    assert out["topics"] == "女装,穿搭,技巧"


# ---------- 发布链路健壮性 ----------

def test_retry_succeeds_on_second_attempt():
    from wchannels.browser.platforms._base import retry
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("网络抖了一下")
        return "ok"

    assert retry(flaky, times=2, backoff_ms=0) == "ok"
    assert calls["n"] == 2


def test_retry_raises_last_error_after_exhausted():
    from wchannels.browser.platforms._base import retry
    calls = {"n": 0}

    def always_bad():
        calls["n"] += 1
        raise RuntimeError(f"第{calls['n']}次失败")

    try:
        retry(always_bad, times=3, backoff_ms=0)
        assert False, "应当抛异常"
    except RuntimeError as e:
        assert "第3次失败" in str(e)
    assert calls["n"] == 3


def test_page_digest_includes_url_and_title():
    from wchannels.browser.platforms._base import page_digest

    class P:
        url = "https://channels.weixin.qq.com/platform/post/create"
        def title(self): return "视频号助手"

    d = page_digest(P())
    assert "channels.weixin.qq.com" in d and "视频号助手" in d

    class Broken:  # url/title 都抛异常时也不能拖垮诊断
        @property
        def url(self): raise RuntimeError("x")
        def title(self): raise RuntimeError("x")

    assert page_digest(Broken()) == ""


def test_first_accepts_candidate_list_and_skips_bad_selector():
    from wchannels.browser.platforms._base import Publisher

    class FakeFrame:
        def query_selector(self, sel):
            if sel == "bad:::selector":
                raise RuntimeError("非法选择器")
            if sel == ".good":
                return "found-el"
            return None

    class FakePage:
        frames = [FakeFrame()]

    assert Publisher.first(FakePage(), ["bad:::selector", ".good"]) == "found-el"
    assert Publisher.first(FakePage(), ".good") == "found-el"
    assert Publisher.first(FakePage(), [".none1", ".none2"]) is None


def test_publish_retries_goto_then_succeeds(monkeypatch):
    """打开发表页第一次超时、第二次成功：发布流程应继续而不是直接判死。"""
    import wchannels.browser.platforms._base as base

    class FakeElement:
        def set_input_files(self, path): pass

    class FlakyPage:
        frames = []
        url = "http://x"
        goto_calls = 0
        def goto(self, *a, **k):
            self.goto_calls += 1
            if self.goto_calls == 1:
                raise RuntimeError("加载超时")
        def wait_for_timeout(self, ms): pass
        def title(self): return "t"
        def screenshot(self, **k): pass

    page = FlakyPage()

    class FakeCtx:
        pages = [page]

    class Probe(base.Publisher):
        platform = "probe"
        publish_url = "http://x"
        def fill_form(self, page, inp, title): pass
        def click_publish(self, page): pass
        def verify(self, page): return True, "ok"

    monkeypatch.setattr(base, "validate_video", lambda p: None)
    monkeypatch.setattr(base, "find_file_input", lambda page: FakeElement())

    out = Probe().publish(FakeCtx(), base.PublishInput(video_path="v.mp4", short_title="标题"))
    assert out.ok, out.message
    assert page.goto_calls == 2


def test_publish_failure_message_has_page_digest(monkeypatch):
    """找不到上传控件时，失败消息要带页面地址/标题，方便判断掉登录还是改版。"""
    import wchannels.browser.platforms._base as base

    class FakePage:
        frames = []
        url = "https://example.com/login"
        def goto(self, *a, **k): pass
        def wait_for_timeout(self, ms): pass
        def title(self): return "请先登录"
        def screenshot(self, **k): pass

    class FakeCtx:
        pages = [FakePage()]

    class Probe(base.Publisher):
        platform = "probe"
        publish_url = "http://x"
        def fill_form(self, page, inp, title): pass
        def click_publish(self, page): pass
        def verify(self, page): return True, "ok"

    monkeypatch.setattr(base, "validate_video", lambda p: None)
    monkeypatch.setattr(base, "find_file_input", lambda page: None)

    out = Probe().publish(FakeCtx(), base.PublishInput(video_path="v.mp4", short_title="标题"))
    assert not out.ok
    assert "example.com/login" in out.message and "请先登录" in out.message


# ---------- 上传素材 ----------

def test_upload_keeps_original_name_and_distinct_errors():
    """浏览器上传必须用原始文件名当标题；重复/格式错误要给出不同的人话提示。"""
    client = _fresh_client()
    _register_and_login(client, "up")

    r = client.post("/api/materials/upload",
                    files={"file": ("我的短视频.mp4", b"fake-mp4-bytes", "video/mp4")})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "我的短视频"          # 不能是 tmpXXXX 随机名

    r2 = client.post("/api/materials/upload",
                     files={"file": ("改个名字.mp4", b"fake-mp4-bytes", "video/mp4")})
    assert r2.status_code == 400
    assert "已经在素材库" in r2.json()["detail"]

    r3 = client.post("/api/materials/upload",
                     files={"file": ("笔记.txt", b"text", "text/plain")})
    assert r3.status_code == 400
    assert "不支持" in r3.json()["detail"]


def test_inbox_dir_follows_data_dir():
    """数据目录挪走后，收件箱必须跟着走（此前是硬编码的第二份路径）。"""
    from wchannels.config import settings
    old = settings.WX_DATA_DIR
    try:
        settings.WX_DATA_DIR = Path(tempfile.mkdtemp())
        assert settings.inbox_dir == settings.WX_DATA_DIR / "inbox"
        settings.ensure_dirs()
        assert settings.inbox_dir.is_dir()
    finally:
        settings.WX_DATA_DIR = old


# ---------- 统一错误格式 ----------

def test_validation_error_returns_chinese_detail():
    client = _fresh_client()
    _register_and_login(client, "val")
    # 缺必填字段 name -> 422，且 detail 是小白能看懂的中文
    r = client.post("/api/accounts", json={})
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body.get("detail"), str)
    assert "不完整" in body["detail"] or "格式" in body["detail"]


def test_unhandled_exception_returns_safe_detail():
    client = _fresh_client()
    _register_and_login(client, "boom")

    from fastapi import FastAPI
    app = client.app

    @app.get("/api/__test_boom")
    def _boom():
        raise RuntimeError("敏感堆栈信息不该外泄")

    r = client.get("/api/__test_boom")
    assert r.status_code == 500
    assert r.json()["detail"].startswith("服务器开小差了")
    assert "敏感堆栈" not in r.text


# ---------- 配额即时显示 ----------

def test_accounts_list_resets_daily_count_and_exposes_limit():
    client = _fresh_client()
    uid = _register_and_login(client, "quota")
    with Session(dbmod.get_engine()) as s:
        acc = Account(user_id=uid, name="Q1", platform="wechat", profile_dir="p_quota",
                      daily_publish_count=3,
                      last_publish_at=utcnow() - timedelta(days=1))  # 昨天发的
        s.add(acc)
        s.commit()

    items = client.get("/api/accounts").json()
    assert len(items) == 1
    # 跨天即时归零：界面上不该再显示「今日已发 3 条」
    assert items[0]["daily_publish_count"] == 0
    # 带上每日上限，前端才能显示「今日 0/3 条」
    assert items[0]["daily_limit"] == 3
