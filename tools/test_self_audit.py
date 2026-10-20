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
# 13.5) 安全/健壮性加固回归：/media 防穿越 + 500 处理器返回 JSON
# ===========================================================================
def test_hardening():
    print("[安全/健壮性加固]")
    from app import app as _app
    # 注意：不能设 TESTING=True —— 否则测试客户端会直接把异常抛出，
    # 500 处理器就不会被触发，无法验证「返回 JSON」这一行为。
    _app.config["TESTING"] = False
    c = _app.test_client()

    # 必须在首次请求前注册探针路由（Flask 首次请求后禁止再改路由）
    @_app.route("/__probe_500_harden")
    def _p500():
        raise RuntimeError("探针错误")

    # 1) /media 拒绝父目录穿越（任意文件读取防护）
    rv = c.get("/media/../../Windows/win.ini")
    ck("/media 拒绝 .. 穿越 → 400", rv.status_code == 400, f"status={rv.status_code}")
    rv = c.get("/media/..%2f..%2fetc%2fpasswd")
    ck("/media 拒绝编码穿越 → 400", rv.status_code == 400, f"status={rv.status_code}")

    # 2) 500 处理器：API 请求返回 JSON（避免前端 fetch().json() 拿到 HTML 崩）
    rv = c.get("/__probe_500_harden", headers={"Accept": "application/json"})
    ck("500 处理器(API) 返回 JSON", rv.status_code == 500 and rv.is_json,
       f"status={rv.status_code} ct={rv.content_type}")
    try:
        d = rv.get_json()
        ck("500 JSON 含 ok=False", bool(d) and d.get("ok") is False)
    except Exception:
        ck("500 JSON 可解析", False)

    # 3) 500 处理器：页面请求返回可读文案而非裸堆栈
    rv = c.get("/__probe_500_harden", headers={"Accept": "text/html"})
    ck("500 处理器(页面) 返回文本", rv.status_code == 500
       and "服务器内部错误" in rv.get_data(as_text=True),
       f"status={rv.status_code}")


# ===========================================================================
# 15) 筛选链接组合保留（filter_qs）
# ===========================================================================
def test_filter_qs():
    print("[筛选链接组合保留]")
    from app import app as _app
    from wechannels.web.templating import filter_qs
    with _app.test_request_context("/?q=篮球&collection=我的合集"):
        qs = filter_qs(status="待发布")
        ck("状态芯片保留 q 与 collection",
           "q=" in qs and "collection=" in qs and "status=" in qs, qs)
        qs2 = filter_qs(collection="")
        ck("清空合集仍保留 q",
           "q=" in qs2 and "collection=" not in qs2, qs2)
    with _app.test_request_context("/"):
        qs3 = filter_qs(status="已发布")
        ck("无筛选时只带 status", "status=" in qs3 and "q=" not in qs3, qs3)


# ===========================================================================
# 16) 发布页复选框尊重素材设置（修复 not m → not mat）
# ===========================================================================
def test_publish_checkboxes():
    print("[发布页复选框尊重素材设置]")
    if not accounts.current_account_id():
        accounts.add_account({"id": "chk_acc", "name": "chk"})
        accounts.set_current_account("chk_acc")
    aid = accounts.current_account_id()
    import re
    def _mk(**kw):
        vid = os.path.join(tempfile.mkdtemp(), "v.mp4")
        open(vid, "wb").write(b"x")
        kw.setdefault("video_path", vid)
        kw.setdefault("ai_declare", "非AI内容")
        kw.setdefault("visibility", "公开")
        return materials.add_material(kw, account_id=aid)
    m_f = _mk(title="t_false", original=False, allow_comment=False, allow_download=False)
    m_t = _mk(title="t_true", original=True, allow_comment=True, allow_download=True)
    from app import app as _app
    c = _app.test_client()
    def _chk(mid, expect):
        h = c.get(f"/publish?material={mid}").get_data(as_text=True)
        for n in ("original", "allow_comment", "allow_download"):
            mm = re.search(r'name="' + n + r'" value="true"([^>]*)>', h)
            got = bool(mm and "checked" in mm.group(1))
            ck(f"复选框 {n} = {expect}（素材 {mid[:4]}）", got == expect)
    _chk(m_f["id"], False)
    _chk(m_t["id"], True)


# ===========================================================================
# 14) 扫码登录：档案被占用时要「立刻说清楚」，不能静默等待
# ===========================================================================
def test_login_busy():
    print("[扫码登录 · 档案占用]")
    import contextlib
    import io

    from wechannels import launcher

    acc = accounts.add_account({"id": "loginbusy", "name": "占用测试"})
    prof = accounts.profile_path(acc)

    # 造一把「活占用」锁（占用者标签不是登录窗口 → 应进入等待并持续提示）
    lp = proclock._lock_path(prof)
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    with open(lp, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "tag": "发布任务",
                   "since": "2026-01-01 00:00:00"}, f)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok, holder = launcher._acquire_profile(prof, "打开发表页（loginbusy）", wait_s=3)
    out = buf.getvalue()
    ck("锁被占用 → 抢锁失败", ok is False and bool(holder), f"ok={ok}")
    ck("等待期间有进度提示（不再静默）", "[WAIT]" in out)
    ck("提示里写明占用者", "发布任务" in out)

    # 面板路由：已有占用 → 直接提示 busy，不再拉起一个白等 45 秒的控制台
    from app import app as _app
    c = _app.test_client()
    rv = c.get("/accounts/loginbusy/login")
    ck("面板点扫码登录 → 重定向", rv.status_code in (302, 303), f"status={rv.status_code}")
    loc = rv.headers.get("Location", "")
    ck("重定向带 msg=busy（明确告知，不白开窗口）", "msg=busy" in loc, loc)
    ck("被占用时不打暂停标记", not proclock.paused("loginbusy"))

    # 抢锁失败提前退出 → 必须解除暂停标记，否则该账号最长 30 分钟内发布全被拒
    proclock.pause("loginbusy", reason="扫码登录", owner="manual")
    ck("暂停标记已打上", proclock.paused("loginbusy"))
    from wechannels.web.templating import pause_badge
    ck("面板显示「手动操作中」徽标", "剩" in pause_badge("loginbusy"))
    launcher._resume_unless_window_open("loginbusy", prof)
    ck("未开窗口时自动解除暂停（发布不被阻塞）", not proclock.paused("loginbusy"))
    ck("解除后徽标消失", pause_badge("loginbusy") == "")

    # 残留锁（持有进程已死）不应被当成占用：read_holder 会顺手清掉
    with open(lp, "w", encoding="utf-8") as f:
        json.dump({"pid": 999999, "tag": "打开发表页（loginbusy）"}, f)
    ck("残留锁（进程已死）自动清理", proclock.read_holder(prof) is None)
    ck("清理后锁文件已删除", not os.path.exists(lp))


# ===========================================================================
# 15) media 服务：Range 解析（含开放区间 bytes=0-）
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


# ===========================================================================
# 16) 按账号差异化：variants 存储 + 合并 + 冗余剔除
# ===========================================================================
def test_materials_variants():
    print("[按账号差异化 · variants 存储/合并]")
    aid = "default"
    tmp = tempfile.mkdtemp()
    try:
        vid = os.path.join(tmp, "v.mp4")
        open(vid, "wb").write(b"x")
        m = materials.add_material(
            {"title": "pa", "video_path": vid, "desc": "全局描述", "original": True},
            account_id=aid,
        )
        accA = accounts.add_account({"id": "pa_a", "name": "A"})
        accB = accounts.add_account({"id": "pa_b", "name": "B"})

        # 保存差异化：A 改描述+封面，B 改描述
        materials.set_variants(m["id"], {
            accA["id"]: {"desc": "A专属描述", "cover_path": "C:\\a.png"},
            accB["id"]: {"desc": "B专属描述"},
        })
        # 注意：set_variants 写盘，不在内存的 m 上就地更新，必须重新读取
        mf = materials.get_material(m["id"])
        ck("material_variant(A) 命中描述", materials.material_variant(mf, accA["id"]).get("desc") == "A专属描述")
        ck("material_variant(B) 命中描述", materials.material_variant(mf, accB["id"]).get("desc") == "B专属描述")

        # 与素材默认相同的字段不应冗余保存
        materials.set_variants(m["id"], {accA["id"]: {"desc": "全局描述"}})
        mf = materials.get_material(m["id"])
        ck("与默认相同的字段被剔除", "desc" not in materials.material_variant(mf, accA["id"]),
           str(materials.material_variant(mf, accA["id"])))

        # 还原并验证合并：全局覆盖 + 账号差异，账号级优先
        materials.set_variants(m["id"], {
            accA["id"]: {"desc": "A专属描述", "cover_path": "C:\\a.png"},
            accB["id"]: {"desc": "B专属描述"},
        })
        from wechannels import publish as P
        merged = P.merge_publish_overrides({"desc": "全局覆盖"}, {"desc": "账号覆盖"})
        ck("合并：账号差异覆盖全局", merged.get("desc") == "账号覆盖")
        merged2 = P.merge_publish_overrides({"desc": "全局覆盖"}, {})
        ck("合并：无差异时沿用全局", merged2.get("desc") == "全局覆盖")
    finally:
        shutil.rmtree(tmp)
        items = materials.load_materials(aid)
        materials.save_materials([x for x in items if x.get("account_id") != aid])
        for _a in ("pa_a", "pa_b"):
            if accounts.get_account(_a):
                accounts.delete_account(_a)


# ===========================================================================
# 17) 发布表单解析：按账号差异化字段 + 错峰
# ===========================================================================
def test_publish_form_parse():
    print("[发布表单解析 · 按账号差异化/错峰]")
    from app import app as _app
    from wechannels.web import views_publish as VP
    from flask import request

    data = {
        "material_id": "m1", "accounts": ["pa_a", "pa_b"],
        "desc": "全局描述", "original": "true", "visibility": "公开",
        "ov_desc_pa_a": "A描述", "ov_cover_path_pa_a": "C:\\a.png",
        "ov_desc_pa_b": "B描述", "ov_original_pa_b": "true",
        "stagger_minutes": "5",
    }
    with _app.test_request_context("/publish/start", method="POST", data=data):
        p = VP.parse_publish_form(request)
    ck("解析出素材 id", p["mids"] == ["m1"], str(p["mids"]))
    ck("解析出两个账号", set(p["acc_ids"]) == {"pa_a", "pa_b"}, str(p["acc_ids"]))
    ck("全局 original 解析", p["overrides"].get("original") == "true")
    ck("按账号 A 描述差异化", p["variants"].get("pa_a", {}).get("desc") == "A描述")
    ck("按账号 A 封面差异化", p["variants"].get("pa_a", {}).get("cover_path") == "C:\\a.png")
    ck("按账号 B 原创差异化", p["variants"].get("pa_b", {}).get("original") == "true")
    ck("错峰 5 分钟解析", p["stagger_minutes"] == 5, str(p["stagger_minutes"]))

    # 未被勾选的账号不应进入 variants
    data2 = dict(data)
    data2["accounts"] = ["pa_a"]
    with _app.test_request_context("/publish/start", method="POST", data=data2):
        p2 = VP.parse_publish_form(request)
    ck("未勾选账号不进 variants", "pa_b" not in p2["variants"])

    # 发布页模板能渲染（含按账号区块，且不抛 Jinja 错误）
    c = _app.test_client()
    rv = c.get("/publish")
    ck("GET /publish 200 且含按账号区块", rv.status_code == 200
       and "按账号差异化" in rv.get_data(as_text=True), f"status={rv.status_code}")


# ===========================================================================
# 18) 真实跑 _run_job：按账号覆盖生效 + 错峰 sleep + 单原创保护
# ===========================================================================
def test_publish_per_account():
    print("[_run_job · 按账号覆盖/错峰/单原创]")
    import unittest.mock as mock
    import time as _time
    from wechannels import publish as P

    tmp = tempfile.mkdtemp()
    try:
        vid = os.path.join(tmp, "v.mp4")
        open(vid, "wb").write(b"x")
        m = materials.add_material(
            {"title": "pa", "video_path": vid, "desc": "全局描述", "original": True},
            account_id="default",
        )
        accA = accounts.add_account({"id": "pa_a", "name": "A"})
        accB = accounts.add_account({"id": "pa_b", "name": "B"})
        materials.set_variants(m["id"], {
            accA["id"]: {"desc": "A专属描述", "cover_path": "C:\\a.png"},
            accB["id"]: {"desc": "B专属描述"},
        })

        recorded = []
        def fake_pub(acc, mat, overrides=None):
            recorded.append((acc["id"], dict(overrides or {})))
            return (True, "发布成功")

        sleeps = []
        with mock.patch.object(P, "publish_one_account", side_effect=fake_pub), \
             mock.patch.object(_time, "sleep", side_effect=lambda s: sleeps.append(s)):
            P._run_job(m, [accA, accB], overrides={}, batch_id="",
                       variants=materials.get_material(m["id"])["variants"],
                       stagger_minutes=1)
        ck("A 使用差异化描述", any(a == "pa_a" and v.get("desc") == "A专属描述" for a, v in recorded))
        ck("B 使用差异化描述", any(a == "pa_b" and v.get("desc") == "B专属描述" for a, v in recorded))
        ck("错峰 sleep 调用 1 次 × 60s", len(sleeps) == 1 and sleeps[0] == 60, str(sleeps))

        # 单原创保护：全局 original=true，两账号都无差异化 → 第二个自动取消
        recorded2 = []
        def fake_pub2(acc, mat, overrides=None):
            recorded2.append((acc["id"], dict(overrides or {})))
            return (True, "发布成功")
        with mock.patch.object(P, "publish_one_account", side_effect=fake_pub2):
            P._run_job(m, [accA, accB], overrides={"original": "true"}, batch_id="",
                       variants={}, stagger_minutes=0)
        ck("首账号保留原创", any(a == "pa_a" and str(v.get("original", "")).lower() in ("1", "true")
                                for a, v in recorded2))
        ck("次账号原创被取消(单原创保护)",
           any(a == "pa_b" and str(v.get("original", "")).lower() in ("false", "off", "0", "")
               for a, v in recorded2), str(recorded2))
    finally:
        shutil.rmtree(tmp)
        materials.delete_material(m["id"])
        for _a in ("pa_a", "pa_b"):
            if accounts.get_account(_a):
                accounts.delete_account(_a)


# ===========================================================================
# 19) 浏览器后端探测（ego / 自定义内核）：不抛异常、不存在返回 None
# ===========================================================================
def test_browser_custom_backend():
    print("[浏览器后端探测 · 安全降级]")
    from wechannels import browser as B
    cfg_backup = config.BROWSER_BACKEND
    path_backup = config.CUSTOM_BROWSER_PATH
    try:
        config.BROWSER_BACKEND = "auto"
        config.CUSTOM_BROWSER_PATH = ""
        r = B.detect_custom_browser()
        ck("detect_custom_browser 不抛异常", True)

        # 不存在的自定义路径：绝不能误用该路径本身，必须安全降级（系统内核或 None）
        config.CUSTOM_BROWSER_PATH = "C:\\no\\such\\browser.exe"
        nb = B.detect_custom_browser()
        ck("不存在的自定义路径不被采用", nb != "C:\\no\\such\\browser.exe", f"got={nb}")

        # 真实存在的文件：必须被直接采用（不做任何探测覆盖）
        sentinel = os.path.join(_TMP, "fake_edge.exe")
        open(sentinel, "wb").write(b"x")
        config.CUSTOM_BROWSER_PATH = sentinel
        got = B.detect_custom_browser()
        ck("真实存在的自定义路径被采用", got == sentinel, f"got={got}")
    finally:
        config.BROWSER_BACKEND = cfg_backup
        config.CUSTOM_BROWSER_PATH = path_backup


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
    test_hardening()
    test_login_busy()
    test_filter_qs()
    test_publish_checkboxes()
    test_views_smoke()
    test_media_service()
    test_materials_variants()
    test_publish_form_parse()
    test_publish_per_account()
    test_browser_custom_backend()

    print(f"\n==== 自我审计结果：PASS={PASS}  FAIL={FAIL} ====")
    shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)
