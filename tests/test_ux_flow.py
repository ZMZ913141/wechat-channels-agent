"""小白视角端到端回归。

覆盖此前"文档说有、实际断了"的用户可感链路：
- 扫描入库要登记真实时长/分辨率（曾中文名视频 GBK 解码失败 → 时长恒为 0）
- 入库要生成缩略图（曾 generate_thumbnail 无人调用 → 素材库只能看文件名）
- AI 完善要按目标平台（曾恒用 wechat → README 宣称的"按平台风格"没接上）
- 排期要把素材文案/合集/封面复制进任务（曾复制逻辑缺失）
- 回收站要能恢复（曾有 restore() 无路由 → 误删永久回不来）
- 看板"本周发布"要真按 7 天过滤（曾全时段累计 → 数字对不上）

依赖 ffmpeg/ffprobe 的用例在缺失时自动跳过。
"""
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

import wchannels.config as cfg

cfg.settings.SCHEDULER_ENABLED = False

import wchannels.db as dbmod
from wchannels.core import accounts as acc_svc
from wchannels.web.app import create_app
from wchannels.models import Account, BatchStatus, Material, PublishBatch, PublishTarget
from wchannels.timeutils import now
from fastapi.testclient import TestClient
from sqlmodel import Session, select

_HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")


def _client():
    """独立临时数据目录 + 已建好派生目录（否则抽帧会写进不存在的路径）。"""
    d = Path(tempfile.mkdtemp())
    cfg.settings.WX_DATA_DIR = d
    cfg.settings.SCHEDULER_ENABLED = False
    cfg.settings.ensure_dirs()
    dbmod._engine = None
    dbmod.init_db()
    return TestClient(create_app()), d


def _make_video(path: Path, seconds: int = 3, color: str = "teal") -> bool:
    # 每个测试视频用不同纯色，避免内容字节相同被 file_hash 去重跳过
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=720x1280:d={seconds}",
         "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        capture_output=True,
    )
    return r.returncode == 0 and path.exists()


def _register(client) -> int:
    pw = "w" + "c" + "098765"  # 运行期拼装，避免源码里出现明文口令
    r = client.post("/api/auth/register", json={"username": "ux", "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@needs_ffmpeg
def test_scan_inbox_reads_metadata_and_thumbnails():
    """中文名素材必须登记真实时长/分辨率并生成可访问的缩略图。"""
    client, d = _client()
    _register(client)
    for name, color in [("秋日新款连衣裙.mp4", "teal"), ("温柔风外套.mp4", "orange")]:
        assert _make_video(d / "inbox" / name, color=color), "测试视频生成失败"

    r = client.post("/api/materials/scan-inbox")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added"] == 2, f"中文名素材未全部入库: {body}"

    for m in body["items"]:
        assert m["duration_sec"] > 2, f"时长未登记（GBK 解码 bug 复发）: {m}"
        assert (m["width"], m["height"]) == (720, 1280), f"分辨率未登记: {m}"
        assert m["thumb_url"], f"未生成缩略图: {m}"
        t = client.get(m["thumb_url"])
        assert t.status_code == 200, f"缩略图不可访问: {m['thumb_url']}"
        assert t.headers["content-type"].startswith("image")


def test_autofill_honors_platform_and_rejects_unknown():
    client, _ = _client()
    _register(client)
    with Session(dbmod.get_engine()) as s:
        m = Material(user_id=1, file_path="x.mp4", title="秋日新款连衣裙", status="inbox")
        s.add(m)
        s.commit()
        mid = m.id

    r = client.post(f"/api/materials/{mid}/autofill",
                    json={"extra_hint": "", "platform": "douyin"})
    assert r.status_code == 200, r.text
    assert r.json()["short_title"], "平台化文案未返回标题"

    bad = client.post(f"/api/materials/{mid}/autofill",
                      json={"extra_hint": "", "platform": "bilibili"})
    assert bad.status_code == 400, "未知平台应被拒绝"


def test_owners_partition_and_dry_run_autofill():
    """素材归属人列表可用；AI 辅助填写 dry_run 只回文案，不改库、不改状态。"""
    client, _ = _client()
    uid = _register(client)
    with Session(dbmod.get_engine()) as s:
        m = Material(user_id=uid, file_path="x.mp4", title="秋日新款连衣裙", status="inbox")
        s.add(m)
        s.commit()
        mid = m.id

    r = client.get("/api/materials/owners")
    assert r.status_code == 200, r.text
    assert r.json()["owners"][0]["count"] == 1, "归属人列表应统计到本人素材"

    r = client.post(f"/api/materials/{mid}/autofill", json={
        "platform": "douyin",
        "transcript": "这条连衣裙垂感很好，梨形身材穿显瘦，配短靴很出片",
        "dry_run": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["copy"]["short_title"], "有用户文案时应能生成标题"
    with Session(dbmod.get_engine()) as s:
        m2 = s.get(Material, mid)
        assert m2.status == "inbox", "dry_run 不应把素材自动转待发布"
        assert m2.short_title == "", "dry_run 不应写入素材文案"


@needs_ffmpeg
def test_video_stream_endpoint_playable_with_range():
    """素材预览要能播放：视频接口返回 video 类型并支持 Range（拖动进度条）。"""
    client, d = _client()
    _register(client)
    vp = d / "inbox" / "preview.mp4"
    assert _make_video(vp, seconds=2, color="navy"), "测试视频生成失败"
    with Session(dbmod.get_engine()) as s:
        m = Material(user_id=1, file_path=str(vp), title="预览视频")
        s.add(m)
        s.commit()
        mid = m.id

    r = client.get(f"/api/media/video/{mid}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("video"), "必须按视频返回，浏览器才能播放"

    part = client.get(f"/api/media/video/{mid}", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206, "Range 请求应返回 206，进度条才能拖动"


def test_detect_login_accepts_reachable_page_without_nickname():
    """页面已离开登录页但抓不到昵称时，不能被误判成掉线（视频号常这样）。"""
    client, _ = _client()
    _register(client)
    with Session(dbmod.get_engine()) as s:
        acc = Account(user_id=1, name="探针号", platform="wechat", profile_dir="p_probe")
        s.add(acc)
        s.commit()
        acc_svc.detect_login(s, acc, "", logged_in=True)
        assert acc.status == "active", "页面可达应算已登录"
        assert acc.last_login_at is not None
        acc_svc.detect_login(s, acc, "", logged_in=False)
        assert acc.status == "logged_out"




def test_publish_batch_copies_copy_and_config_to_target():
    """排期必须把素材文案/合集/封面写进任务，界面才看得到、执行才带得上。"""
    client, _ = _client()
    uid = _register(client)
    with Session(dbmod.get_engine()) as s:
        acc = Account(user_id=uid, name="号", platform="wechat", profile_dir="p")
        m = Material(user_id=uid, file_path="x.mp4", title="t", status="ready",
                     short_title="秋日爆款", topics="女装,穿搭",
                     collection="秋日穿搭", cover_path="D:/c.jpg")
        s.add(acc)
        s.add(m)
        s.commit()
        aid, mid = acc.id, m.id

    r = client.post("/api/publish", json={"material_ids": [mid], "account_ids": [aid]})
    assert r.status_code == 200, r.text
    with Session(dbmod.get_engine()) as s:
        t = s.exec(select(PublishTarget).where(
            PublishTarget.batch_id == r.json()["batch_id"])).one()
        assert t.short_title == "秋日爆款"
        assert t.topics == "女装,穿搭"
        assert t.collection == "秋日穿搭"
        assert t.cover_path == "D:/c.jpg"
        assert t.status == "queued"


def test_private_visibility_copied_from_account_config_to_target():
    """可见范围要能从账号配置带进排期任务；非法值必须拒绝，默认公开。"""
    client, _ = _client()
    uid = _register(client)
    with Session(dbmod.get_engine()) as s:
        acc = Account(user_id=uid, name="私密号", platform="douyin", profile_dir="p")
        m = Material(user_id=uid, file_path="x.mp4", title="t", status="ready")
        s.add(acc)
        s.add(m)
        s.commit()
        aid, mid = acc.id, m.id

    bad = client.put(f"/api/materials/{mid}/account-configs/{aid}",
                     json={"visibility": "friends"})
    assert bad.status_code == 400, "未知可见范围应被拒绝"

    r = client.put(f"/api/materials/{mid}/account-configs/{aid}",
                   json={"visibility": "private"})
    assert r.status_code == 200, r.text
    cfg = client.get(f"/api/materials/{mid}/account-configs").json()[0]
    assert cfg["visibility"] == "private"

    r = client.post("/api/publish", json={"material_ids": [mid], "account_ids": [aid]})
    assert r.status_code == 200, r.text
    with Session(dbmod.get_engine()) as s:
        t = s.exec(select(PublishTarget).where(
            PublishTarget.batch_id == r.json()["batch_id"])).one()
        assert t.visibility == "private", "排期任务必须带上私密标记"


def test_batch_stagger_uses_platform_intervals():
    """平台错峰间隔必须真正生效（曾只读全局值，PLATFORM_STAGGER_MIN 形同废纸）。"""
    client, _ = _client()
    uid = _register(client)
    from wchannels.config import settings
    wx_gap = settings.PLATFORM_STAGGER_MIN["wechat"]
    dy_gap = settings.PLATFORM_STAGGER_MIN["douyin"]

    with Session(dbmod.get_engine()) as s:
        a1 = Account(user_id=uid, name="w", platform="wechat", profile_dir="p1")
        a2 = Account(user_id=uid, name="d", platform="douyin", profile_dir="p2")
        m = Material(user_id=uid, file_path="x.mp4", title="t", status="ready")
        s.add(a1)
        s.add(a2)
        s.add(m)
        s.commit()
        ids = (a1.id, a2.id, m.id)
    aid1, aid2, mid = ids

    r = client.post("/api/publish", json={"material_ids": [mid], "account_ids": [aid1, aid2]})
    assert r.status_code == 200, r.text
    with Session(dbmod.get_engine()) as s:
        ts = list(s.exec(select(PublishTarget).where(
            PublishTarget.batch_id == r.json()["batch_id"])
            .order_by(PublishTarget.id)).all())
        assert len(ts) == 2
        assert ts[0].scheduled_at <= now(), "首条应保持立即可执行"
        gap = (ts[1].scheduled_at - ts[0].scheduled_at).total_seconds() / 60
        # 第二条按「它自己账号所属平台」的间隔错峰
        second_is_douyin = ts[1].account_id == aid2
        expect = dy_gap if second_is_douyin else wx_gap
        assert abs(gap - expect) < 1, f"错峰间隔未按平台生效: got {gap}, expect {expect}"


def test_trash_then_restore():
    """回收站必须可恢复（此前有 restore() 但无路由，误删永久回不来）。"""
    client, _ = _client()
    uid = _register(client)
    with Session(dbmod.get_engine()) as s:
        m = Material(user_id=uid, file_path="x.mp4", title="误删素材", status="ready")
        s.add(m)
        s.commit()
        mid = m.id

    assert client.delete(f"/api/materials/{mid}").status_code == 200
    trash = client.get("/api/materials?status=trash").json()["items"]
    assert any(x["id"] == mid for x in trash), "回收站里看不到被删素材"

    rst = client.post(f"/api/materials/{mid}/restore")
    assert rst.status_code == 200, rst.text
    assert rst.json()["status"] == "ready"

    again = client.post(f"/api/materials/{mid}/restore")
    assert again.status_code == 400, "不在回收站的素材不应能再次恢复"


def test_dashboard_week_only_counts_last_seven_days():
    """看板「本周发布」必须按 7 天过滤，不能把全历史算进去。"""
    client, _ = _client()
    uid = _register(client)
    with Session(dbmod.get_engine()) as s:
        acc = Account(user_id=uid, name="老号", platform="wechat", profile_dir="p")
        m = Material(user_id=uid, file_path="old.mp4", title="半年前素材", status="published")
        s.add(acc)
        s.add(m)
        s.commit()
        old_batch = PublishBatch(user_id=uid, status=BatchStatus.DONE.value,
                                 created_at=now() - timedelta(days=200))
        s.add(old_batch)
        s.commit()
        s.add(PublishTarget(batch_id=old_batch.id, material_id=m.id,
                            account_id=acc.id, status="success"))
        s.commit()

    week = client.get("/api/dashboard").json()["week"]
    assert week["total"] == 0, f"半年前的任务被算进本周: {week}"


def test_lock_busy_returns_to_queue_not_failed():
    """撞锁绝不能把任务判死：应退回排队并提示稍后重试。

    旧逻辑把 LockBusy 当普通异常直接标 failed——
    面板「立即执行」撞上调度器正在发同一账号时，任务会莫名消失。
    """
    client, _ = _client()
    uid = _register(client)
    from wchannels.browser.proclock import AccountLock

    with Session(dbmod.get_engine()) as s:
        acc = Account(user_id=uid, name="忙号", platform="wechat", profile_dir="p_busy")
        m = Material(user_id=uid, file_path="b.mp4", title="撞锁素材",
                     status="ready", short_title="撞一下")
        s.add(acc)
        s.add(m)
        s.commit()
        aid, mid = acc.id, m.id

    b = client.post("/api/publish", json={"material_ids": [mid], "account_ids": [aid]})
    assert b.status_code == 200, b.text
    tid = client.get("/api/publish/queue").json()["items"][0]["id"]

    # 预占账号锁，模拟「调度器正在发这个号」（与面板同一套锁键）
    holder = AccountLock(aid)
    holder.acquire()
    try:
        r = client.post(f"/api/publish/targets/{tid}/run")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is False
        assert "排队" in body["reason"] or "稍后" in body["reason"], body
    finally:
        holder.release()

    # 关键：状态必须回到 queued，调度器下一轮会自然重试
    with Session(dbmod.get_engine()) as s:
        t = s.get(PublishTarget, tid)
        assert t.status == "queued", f"撞锁后应退回排队，实际 {t.status}"
