"""自我审计回归测试：覆盖 wechannels 各子系统的纯逻辑 + Flask 端到端冒烟。

设计原则（对齐项目既有测试规范）：
  - 纯 Python，不启动浏览器、不依赖 ffmpeg；外部命令缺失时优雅降级。
  - 全程使用临时 STATE_DIR / USER_DATA_DIR 与临时账号，绝不污染真实 state。
  - 每个子系统一个 test_* 函数，断言式 ck()；最后汇总 PASS/FAIL。

目标：把「自我优化迭代」发现的潜在 bug 全部固化成回归测试，确保最终无 bug。
"""
import os
import sys
import json
import shutil
import tempfile

# ⚠️ 必须在 import wechannels 之前设置 STATE_DIR / USER_DATA_DIR，
# 否则 config 在 import 时就固化了真实 state 路径，测试会污染用户数据。
_TMP = tempfile.mkdtemp(prefix="wcagent_audit_")
os.environ["STATE_DIR"] = os.path.join(_TMP, "state")
os.environ["USER_DATA_DIR"] = os.path.join(_TMP, "profiles")
os.environ.setdefault("LLM_API_KEY", "")  # 关掉 LLM，autofill 走本地兜底

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wechannels import config, accounts, materials, ledger, stats, backup  # noqa: E402
from wechannels import autofill, media_probe, migrate, proclock  # noqa: E402
from wechannels.web import exporters  # noqa: E402  (exporters 在 web 包下)
from wechannels.web import base  # noqa: E402,F401  (注册路由前先有 app)
from wechannels.web import (views_core, views_accounts,  # noqa: E402,F401
                            views_publish, views_dashboard, views_library)

PASS = 0
FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2713 {name}")
    else:
        FAIL += 1
        print(f"  \u2717 {name}  {extra}")


# ===========================================================================
# 1) config: 原子写 + Windows PermissionError 重试
# ===========================================================================
def test_config_atomic_retry():
    print("[config.save_json_atomic 重试]")
    import wechannels.config as C
    path = os.path.join(config.STATE_DIR, "_audit_atomic.json")
    if os.path.exists(path):
        os.remove(path)
    calls = {"n": 0}

    real_replace = os.replace

    def fake_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("simulated Windows busy")
        return real_replace(src, dst)

    import unittest.mock as mock
    with mock.patch.object(C.os, "replace", side_effect=fake_replace):
        C.save_json_atomic(path, {"k": "v"}, ensure_ascii=False)
    ck("PermissionError 重试后写入成功", calls["n"] == 3, f"calls={calls['n']}")
    try:
        with open(path, encoding="utf-8") as f:
            ck("落盘内容正确", json.load(f) == {"k": "v"})
    except Exception as e:
        ck("落盘内容正确", False, str(e))
    if os.path.exists(path):
        os.remove(path)


# ===========================================================================
# 2) accounts: CRUD + 当前账号 + profile 路径
# ===========================================================================
def test_accounts_crud():
    print("[accounts CRUD]")
    # load_accounts 在首次会生成 default 并落盘
    accs = accounts.load_accounts()
    ck("至少一个账号", len(accs) >= 1)
    before = len(accs)

    a = accounts.add_account({"id": "acc_audit", "name": "审计号", "profile_dir": "profiles/audit"})
    ck("新增账号", a["id"] == "acc_audit")
    ck("重复 id 报错", _raises(lambda: accounts.add_account({"id": "acc_audit"})))
    got = accounts.get_account("acc_audit")
    ck("按 id 取回", got is not None and got["name"] == "审计号")

    accounts.update_account("acc_audit", {"name": "审计号改名"})
    ck("更新账号名", accounts.get_account("acc_audit")["name"] == "审计号改名")

    pp = accounts.profile_path(a)
    ck("profile 路径为绝对路径", os.path.isabs(pp))
    ck("profile 目录已创建", os.path.isdir(pp))

    # 当前账号切换
    accounts.set_current_account("acc_audit")
    ck("当前账号切换生效", accounts.current_account_id() == "acc_audit")

    # 删除：default 受保护；普通账号可删
    ck("default 不可删", accounts.delete_account("default") is False)
    ck("普通账号可删", accounts.delete_account("acc_audit") is True)
    ck("删除后取回为 None", accounts.get_account("acc_audit") is None)
    ck("删除后回到 default 为当前", accounts.current_account_id() == "default")
    # 还原
    accounts.set_current_account("default")
    _ = before  # 占位：账号数可能因 default 自动创建而变化，不强校验


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


# ===========================================================================
# 3) materials: 增删改查 + 标签 + 封面 + 集合 + 去重 + 自愈
# ===========================================================================
def test_materials_core():
    print("[materials 核心]")
    aid = "acc_audit"
    # 清空该账号
    items = materials.load_materials(aid)
    materials.save_materials([m for m in items if m.get("account_id") != aid])

    m = materials.add_material(
        {"title": "t1", "video_path": "/tmp/x.mp4", "labels": "爆款, 待剪, 爆款"},
        account_id=aid,
    )
    got = materials.get_material(m["id"])
    ck("新增素材", got is not None)
    ck("标签去重+规范化", materials.get_labels(got) == ["爆款", "待剪"],
       str(materials.get_labels(got)))

    # 更新字段（original 字符串 → 布尔）
    materials.update_material(m["id"], {"desc": "hi", "original": "true", "status": "已发布"})
    got = materials.get_material(m["id"])
    ck("更新文本字段", got["desc"] == "hi")
    ck("original 转 bool", got["original"] is True)
    ck("status 生效", got["status"] == "已发布")

    # 状态撤销
    materials.set_status(m["id"], "待发布")
    ok, text = materials.undo_status(m["id"])
    ck("撤销状态成功", ok and materials.get_material(m["id"])["status"] == "已发布", text)

    # 克隆到其它账号
    c = materials.clone_material(m["id"], "default")
    ck("克隆生成新 id", c and c["id"] != m["id"] and c["account_id"] == "default")
    ck("克隆重置状态为待发布", c["status"] == "待发布")

    # 删除
    ck("删除素材", materials.delete_material(m["id"]))

    # 集合：新增/重命名同步素材引用/删除清理引用
    materials.add_collection("集合A", "desc")
    m2 = materials.add_material(
        {"title": "t2", "video_path": "/tmp/y.mp4", "collection": "集合A"}, account_id=aid)
    ck("合集存在", "集合A" in materials.collection_names())
    ck("集合重命名成功", materials.update_collection("集合A", "集合B"))
    ck("素材引用同步更新", materials.get_material(m2["id"])["collection"] == "集合B")
    ck("删除合集清理引用", materials.delete_collection("集合B")
       and materials.get_material(m2["id"])["collection"] == "")
    materials.delete_material(m2["id"])

    # 指纹与去重
    tmp = tempfile.mkdtemp()
    try:
        f1 = os.path.join(tmp, "dup.mp4")
        with open(f1, "wb") as f:
            f.write(b"abcdef")
        ck("指纹稳定", materials.file_fingerprint(f1) == materials.file_fingerprint(f1))
        ck("不同文件指纹不同", materials.file_fingerprint(f1) !=
           materials.file_fingerprint(os.path.join(tmp, "dup2.mp4")))
    finally:
        shutil.rmtree(tmp)

    # 孤儿 / 缺失 / 统计
    orphan = materials.add_material({"title": "orphan", "video_path": "/no/such.mp4"}, account_id=aid)
    ck("孤儿检测", any(o["id"] == orphan["id"] for o in materials.find_orphans(aid)))
    st = materials.library_stats(aid)
    ck("统计含总数", st["total"] >= 1)
    materials.delete_material(orphan["id"])

    # 自愈：移动文件后被找回（用真实临时文件）
    tmp2 = tempfile.mkdtemp()
    try:
        src = os.path.join(tmp2, "mov.mp4")
        with open(src, "wb") as f:
            f.write(b"12345678")
        mat = materials.add_material({"title": "mov", "video_path": src}, account_id=aid)
        newdir = os.path.join(tmp2, "sub")
        os.makedirs(newdir)
        dst = os.path.join(newdir, "mov.mp4")
        os.rename(src, dst)  # 文件被移动
        r = materials.heal_paths(dry_run=True, account_id=aid)
        ck("dry_run 预测到缺失", r["missing"] >= 1)
        r2 = materials.heal_paths(dry_run=False, account_id=aid)
        ck("自愈找回到新位置", any(h["new"] == dst for h in r2["healed"]), str(r2))
        materials.delete_material(mat["id"])
    finally:
        shutil.rmtree(tmp2)

    # 清理临时账号
    items = materials.load_materials(aid)
    materials.save_materials([m for m in items if m.get("account_id") != aid])


# ===========================================================================
# 4) materials: 递归扫描 + 封面匹配（已有 test_import_recursive 覆盖，这里补正向）
# ===========================================================================
def test_match_cover_and_scan():
    print("[match_cover + scan]")
    tmp = tempfile.mkdtemp()
    try:
        d = os.path.join(tmp, "sub"); os.makedirs(d)
        vp = os.path.join(d, "A.mp4"); open(vp, "wb").write(b"x")
        cp = os.path.join(d, "A-封面.jpg"); open(cp, "wb").write(b"x")
        ck("同名-封面优先", os.path.basename(materials.match_cover_for_video(vp)) == "A-封面.jpg")
        res = materials.scan_import_dir(tmp, recursive=True, skip_duplicates=False)
        ck("递归扫描命中视频", res["total"] == 1 and res["groups"][0]["cover_path"] == cp)
    finally:
        shutil.rmtree(tmp)


# ===========================================================================
# 5) ledger: 三态 + 批次生命周期 + 错误分类 + 迭代 + 清理
# ===========================================================================
def test_ledger():
    print("[ledger]")
    mat = {"id": "m_audit", "title": "m"}
    b = ledger.new_batch(mat, ["a1", "a2"], {})
    ck("new_batch 生成两个 target", len(b["targets"]) == 2)
    bid = b["batch_id"]

    ledger.start_target(bid, "a1", "a1")
    ledger.finish_target(bid, "a1", "success", duration_ms=1234)
    ledger.finish_target(bid, "a2", "failed", error_code="UPLOAD_TIMEOUT",
                         error_stage="upload", duration_ms=500)
    # 三态之一不可越界
    ck("finish 三态校验", _raises(lambda: ledger.finish_target(bid, "a2", "weird")))
    ledger.close_batch(bid)
    done = [x for x in ledger.load_batches() if x["batch_id"] == bid][0]
    ck("close_batch=部分成功", done["status"] == "partial_success", done["status"])

    # 错误分类归一化
    code, stage = ledger.classify_error("视频上传超时了，未在时限内传完")
    ck("classify UPLOAD_TIMEOUT", code == "UPLOAD_TIMEOUT" and stage == "upload",
       f"{code}/{stage}")
    code2, _ = ledger.classify_error("账号未登录请扫码")
    ck("classify NOT_LOGGED_IN", code2 == "NOT_LOGGED_IN")

    # 迭代
    rows = list(ledger.iter_targets(days=0))
    ck("iter_targets 含全部", len(rows) >= 2)

    # 清理（保留很久的）
    n = ledger.prune(keep_days=-1)  # -1 天 → 全删
    ck("prune 删除测试批次", n >= 1)
    ck("prune 后为空", len([x for x in ledger.load_batches() if x["batch_id"] == bid]) == 0)


# ===========================================================================
# 6) stats.build：聚合不崩溃 + 口径
# ===========================================================================
def test_stats_build():
    print("[stats.build]")
    # 造两条：一条成功一条失败（已发布 + 上传超时）
    mat = {"id": "m_st", "title": "st"}
    b = ledger.new_batch(mat, ["a1"], {})
    ledger.finish_target(b["batch_id"], "a1", "success", duration_ms=1000)
    ledger.close_batch(b["batch_id"])
    b2 = ledger.new_batch(mat, ["a1"], {})
    ledger.finish_target(b2["batch_id"], "a1", "failed",
                         error_code="UPLOAD_TIMEOUT", error_stage="upload", duration_ms=2000)
    ledger.close_batch(b2["batch_id"])

    d = stats.build(days=30, account_id="")
    ck("build 返回 has_data", d["has_data"] is True)
    kpi = d["kpi"]
    ck("KPI 成功数=1", kpi["success"] == 1, str(kpi))
    ck("KPI 失败数=1", kpi["failed"] == 1)
    ck("成功率分母=suc+fail", kpi["success_rate"] == 50.0, str(kpi["success_rate"]))
    ck("p50 时长存在", kpi["p50_ms"] > 0, str(kpi))
    ck("错误分布含 UPLOAD_TIMEOUT", any(e["code"] == "UPLOAD_TIMEOUT" for e in d["error_dist"]))
    ck("趋势非空", len(d["trend"]) >= 1)
    # 清理
    ledger.prune(keep_days=-1)


# ===========================================================================
# 7) backup: 创建 / 还原（写后再读校验）
# ===========================================================================
def test_backup():
    print("[backup]")
    # 先制造一点状态数据
    materials.save_materials([{"id": "b1", "title": "b1", "account_id": "default"}])
    r = backup.create(label="审计")
    ck("备份创建成功", r.get("ok") is True, str(r))
    name = r["name"]
    # 改掉数据再还原
    materials.save_materials([])
    res = backup.restore(name, auto_backup_first=False)
    ck("还原成功", res.get("ok") is True, str(res))
    ck("还原后数据回来", any(m["id"] == "b1" for m in materials.load_materials()))
    # 还原不存在的包 → 失败
    res2 = backup.restore("nope.zip", auto_backup_first=False)
    ck("还原不存在的包报错", res2.get("ok") is False)
    # 自动备份：首次只建立指纹不备份；无变化不重复；数据变化才新增
    n_before = len(backup.list_backups())
    backup.auto_backup_if_changed()      # 首次：建立指纹，不新增
    backup.auto_backup_if_changed()      # 无变化：不新增
    ck("auto_backup 无变化时幂等", len(backup.list_backups()) == n_before,
       f"{n_before}->{len(backup.list_backups())}")
    materials.save_materials([{"id": "ab1", "title": "ab", "account_id": "default"}])
    backup.auto_backup_if_changed()      # 数据变化 → 应新增 1 份
    ck("auto_backup 数据变化后新增", len(backup.list_backups()) == n_before + 1,
       f"{n_before}->{len(backup.list_backups())}")
    materials.save_materials([])


# ===========================================================================
# 8) exporters: BOM + 字段
# ===========================================================================
def test_exporters():
    print("[exporters]")
    materials.save_materials([{
        "id": "e1", "title": "e1", "desc": "d", "tags": "x,y", "account_id": "default",
    }])
    csv_r = exporters.export_csv(account_id="default")
    body = csv_r.get_data(as_text=True)
    ck("CSV 带 BOM", body.startswith("\ufeff"), repr(body[:4]))
    ck("CSV 含标题行", "标题" in body)
    json_r = exporters.export_json(account_id="default")
    jb = json.loads(json_r.get_data(as_text=True))
    ck("JSON 含 items", jb["count"] >= 1 and "items" in jb)
    txt_r = exporters.export_text(account_id="default")
    tb = txt_r.get_data(as_text=True)
    ck("TXT 含标题", "e1" in tb)
    materials.save_materials([])


# ===========================================================================
# 9) autofill: 无 LLM 走本地兜底，字段齐全
# ===========================================================================
def test_autofill():
    print("[autofill]")
    out = autofill.autofill(name="我的爆款视频.mp4", hint="", field="")
    for k in ("title", "short_title", "desc", "tags", "ai_declare"):
        ck(f"兜底含 {k}", k in out, str(out))
    ck("文件名清洗标题", out["title"] != "我的爆款视频.mp4")
    single = autofill.autofill(name="x.mp4", hint="", field="title")
    ck("单字段仍返回完整字段（field 在视图层包装成 value）", "title" in single)


# ===========================================================================
# 10) media_probe: 合规校验（不依赖 ffprobe，直接喂合成 info）
# ===========================================================================
def test_media_probe():
    print("[media_probe]")
    ck("合规无警告", media_probe.check_compliance(
        {"height": 1080, "size_mb": 10, "duration": 30}) == [])
    warns = media_probe.check_compliance({"height": 480, "size_mb": 5000, "duration": 100000})
    ck("低分辨率被警告", any("分辨率" in w for w in warns), str(warns))
    ck("过大被警告", any("过大" in w for w in warns))
    # 探针对不存在文件返回 None（不抛异常）
    ck("不存在文件 probe=None", media_probe.probe("/no/such.mp4") is None)


# ===========================================================================
# 11) migrate: 旧 publish_history → ledger（幂等）
# ===========================================================================
def test_migrate():
    print("[migrate_legacy]")
    materials.save_materials([{
        "id": "old1", "title": "old", "account_id": "default",
        "publish_history": [
            {"time": "2025-01-01 10:00:00", "account_id": "default", "ok": True, "result": "成功"},
            {"time": "2025-01-01 10:00:02", "account_id": "default", "ok": False, "result": "未登录"},
        ],
    }])
    r1 = migrate.migrate_legacy()
    ck("首次迁移生成批次", r1["batches_created"] == 1, str(r1))
    r2 = migrate.migrate_legacy()
    ck("重复迁移幂等（不重复）", r2["skipped"] >= 1 and r2["batches_created"] == 0, str(r2))
    # 清理
    ledger.prune(keep_days=-1)
    materials.save_materials([])


# ===========================================================================
# 12) proclock: 锁获取/释放/残留清理
# ===========================================================================
def test_proclock():
    print("[proclock]")
    prof = os.path.join(_TMP, "state", "profile_audit.lock")
    ok, holder = proclock.acquire(prof, "测试", wait_s=0)
    ck("获取档案锁", ok is True)
    ok2, _ = proclock.acquire(prof, "第二次", wait_s=0)
    ck("重复获取被拒", ok2 is False)
    proclock.release(prof)
    ok3, _ = proclock.acquire(prof, "释放后可再获取", wait_s=0)
    ck("释放后重新获取", ok3 is True)
    proclock.release(prof)

    # 账号锁
    p = os.getpid()
    acc = "acc_lock"
    ao, _ = proclock.acquire_account(acc, "t", wait_s=0)
    ck("账号锁获取", ao is True)
    ao2, _ = proclock.acquire_account(acc, "t2", wait_s=0)
    ck("账号锁重复被拒", ao2 is False)
    proclock.release_account(acc)
    # 暂停窗口
    proclock.pause(acc, seconds=1800, reason="测试", owner="x")
    ck("暂停生效", proclock.paused(acc) is not None)
    proclock.resume(acc)
    ck("解除暂停", proclock.paused(acc) is None)


# ===========================================================================
# 13) views: Flask 端到端冒烟（test client，不启动真实服务器/浏览器）
# ===========================================================================
def test_views_smoke():
    print("[Flask 端到端冒烟]")
    from app import app
    app.config["TESTING"] = True
    c = app.test_client()

    # 各页面 GET 不报错
    for url in ("/", "/accounts", "/dashboard", "/import", "/collections",
                "/backup", "/dashboard/export.csv", "/library/export/csv"):
        rv = c.get(url)
        ck(f"GET {url} 200", rv.status_code == 200, f"status={rv.status_code}")

    # 视频合规预检 JSON（ffprobe 缺失时仍要返回 ok=False 不崩）
    rv = c.get("/materials/autofill?base=test.mp4")
    ck("autofill 接口返回 JSON", rv.status_code == 200 and rv.is_json,
       f"status={rv.status_code}")
    rv = c.get("/materials/autofill?base=x.mp4&field=title")
    ck("autofill 单字段返回 value 且 Content-Type 为 JSON",
       rv.status_code == 200 and rv.is_json and "value" in (rv.get_json() or {}),
       f"status={rv.status_code}")

    # 新建账号（POST）→ 应重定向
    rv = c.post("/accounts/new", data={"id": "acc_view", "name": "视图号"})
    ck("新建账号重定向", rv.status_code in (301, 302))
    ck("账号确实写入", accounts.get_account("acc_view") is not None)

    # 新增合集（POST）
    rv = c.post("/collections/add", data={"name": "视图集合", "desc": "d"})
    ck("新增合集重定向", rv.status_code in (301, 302))
    ck("合集确实写入", "视图集合" in materials.collection_names())

    # 新建素材（POST）
    rv = c.post("/materials/new", data={"title": "视图素材", "video_path": "/tmp/v.mp4"})
    ck("新建素材重定向", rv.status_code in (301, 302))
    mats = [m for m in materials.load_materials("default") if m["title"] == "视图素材"]
    ck("素材确实写入", len(mats) == 1)
    if mats:
        mid = mats[0]["id"]
        # 改状态 POST
        rv = c.post(f"/materials/{mid}/status", data={"status": "已发布"})
        ck("改状态重定向", rv.status_code in (301, 302))
        ck("状态已改", materials.get_material(mid)["status"] == "已发布")
        # 删除素材 POST
        rv = c.post(f"/materials/{mid}/delete")
        ck("删除素材重定向", rv.status_code in (301, 302))
        ck("素材已删", materials.get_material(mid) is None)

    # 清理视图测试数据
    if accounts.get_account("acc_view"):
        accounts.delete_account("acc_view")
    materials.delete_collection("视图集合")


# ===========================================================================
# 14) media 服务：Range 解析（含开放区间 bytes=0-）
# ===========================================================================
def test_media_service():
    print("[media /_send_video Range]")
    from wechannels.web.views_core import _send_video
    tmp = tempfile.mkdtemp()
    try:
        fp = os.path.join(tmp, "v.mp4")
        with open(fp, "wb") as f:
            f.write(b"\x00" * 1000)
        from app import app
        with app.test_request_context("/media/x", headers={"Range": "bytes=0-99"}):
            r = _send_video(fp)
            ck("带 Range 返回 206", r.status_code == 206, f"status={r.status_code}")
        with app.test_request_context("/media/x", headers={"Range": "bytes=0-"}):
            r2 = _send_video(fp)
            ck("开放区间 Range 也返回 206", r2.status_code == 206, f"status={r2.status_code}")
        with app.test_request_context("/media/x"):
            r3 = _send_video(fp)
            ck("无 Range 返回 200", r3.status_code == 200, f"status={r3.status_code}")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    test_config_atomic_retry()
    test_accounts_crud()
    test_materials_core()
    test_match_cover_and_scan()
    test_ledger()
    test_stats_build()
    test_backup()
    test_exporters()
    test_autofill()
    test_media_probe()
    test_migrate()
    test_proclock()
    test_views_smoke()
    test_media_service()

    print(f"\n==== 自我审计结果：PASS={PASS}  FAIL={FAIL} ====")
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)
