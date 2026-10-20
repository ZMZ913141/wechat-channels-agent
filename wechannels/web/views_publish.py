"""发布中心：一条素材同步发到多个视频号。"""
import html
import json
import time

from flask import jsonify, redirect, render_template, request, url_for

from .. import accounts, config, materials, media_probe, proclock
from ..publish import JOB_FILE, read_job, start_publish_job
from .base import app
from .templating import decorate
from .views_accounts import login_badge_html


@app.route("/publish")
def publish_page():
    acc_id = accounts.current_account_id()
    valid = [m for m in materials.load_materials(acc_id) if materials.video_exists(m) is True]
    pre = request.args.get("material", "")
    mat = materials.get_material(pre) if pre else None
    if not mat:
        mat = valid[0] if valid else None

    # 视频合规预检（ffprobe）。缺失时静默跳过——外部依赖，不该因此拦住发布。
    probe_html = ""
    if mat:
        vp = (mat.get("video_path") or "").strip()
        if not vp:
            probe_html = '<div class="probe bad">⚠️ 该素材还没绑定视频文件，请点「编辑」上传或填写本地路径。</div>'
        elif materials.video_exists(mat) is not True:
            probe_html = (f'<div class="probe bad">⚠️ 视频文件不存在或不可读：'
                          f'{html.escape(str(vp))}（发布前请先修正路径）</div>')
        else:
            info = media_probe.probe(vp)
            if info:
                res = f'{info.get("width") or "?"}×{info.get("height") or "?"}'
                dur_s = info.get("duration") or 0
                dur = f'{int(dur_s // 60)}分{int(dur_s % 60)}秒' if dur_s else "—"
                warns = media_probe.check_compliance(info)
                cls = "bad" if warns else "ok"
                size_mb = info.get("size_mb") or 0
                extra = f'<div class="bad">⚠️ {"；".join(warns)}</div>' if warns else ""
                probe_html = (f'<div class="probe {cls}">视频规格：{res} · {dur} · {size_mb} MB'
                              f' · {info.get("fps", "?")}fps{extra}</div>')

    # 发布前阻塞预检：账号被暂停 / 档案被占用 / 残留锁时**提前告知**。
    # 否则用户点完只能等任务跑完才看到失败，体验上就是"点了没反应"。
    # 返回 blocked_ids：只禁用受影响的账号勾选框，而不是一律禁用发布按钮
    # （3 个号里 1 个被占用时，另外 2 个仍应能正常发布）。
    blocked, blocked_ids = [], set()
    for a in [x for x in accounts.load_accounts() if x.get("enabled", True)]:
        aid = a["id"]
        why = ""
        p = proclock.paused(aid)
        if p:
            mins = max(1, int((p.get("until", 0) - time.time()) / 60) + 1)
            why = (f'账号「{a.get("name") or aid}」正在手动操作中'
                   f'（{p.get("reason", "被打开发表页")}），请约 {mins} 分钟后再发布')
        else:
            lock_path = proclock._task_lock_path(aid)
            holder = proclock.read_file_lock(lock_path)
            if holder and not proclock._pid_alive(holder.get("pid", 0)):
                proclock._remove_if_mine(lock_path)   # 残留锁顺手清掉
            elif holder:
                why = (f'账号「{a.get("name") or aid}」正被 '
                       f'{proclock.holder_desc(holder)} 占用，请等上一个任务结束')
        if why:
            blocked.append(why)
            blocked_ids.add(aid)

    ena = [a for a in accounts.load_accounts() if a.get("enabled", True)]
    # 按账号差异化：为每个账号预先拼好「继承素材默认 + 叠加该账号已存差异」的填充值，
    # 模板里直接把值塞进输入框；勾选某账号时才显示它的专属配置卡片。
    accvars = []
    for a in ena:
        base = {f: (mat.get(f) if mat else "") for f in materials.VARIANT_FIELDS}
        var = materials.material_variant(mat, a["id"]) if mat else {}
        base.update(var)
        accvars.append((a, base))

    return render_template(
        "publish.html",
        mat=decorate(mat) if mat else None,
        valid=valid,
        accounts=ena,
        accvars=accvars,
        accids=[a["id"] for a in ena],
        cur_account_id=acc_id,
        ai_opts=[v for v in materials.AI_DECLARE_OPTIONS if v != "非AI内容"],
        vis_opts=materials.VISIBILITY_OPTIONS,
        login_badge=login_badge_html,
        probe_html=probe_html,
        blocked=blocked,
        blocked_ids=sorted(blocked_ids),
        usable_ids=[a["id"] for a in ena if a["id"] not in blocked_ids],
        job=read_job(),
        collections=materials.load_collections(),
        all_collections=materials.collection_names(),
        title="同步发布",
    )


@app.route("/publish/status")
def publish_status():
    """发布任务进度（前端轮询）。

    必须同时返回渲染好的 html：前端 refreshJob() 直接把 d.html 塞进
    #jobwrap。之前只返回 JSON 字段，前端拿到 undefined → 任务区一片空白，
    用户看到的就是"点了没反应"。
    """
    job = read_job() or {}
    frag = render_template("partials/job_status.html", job=job or None)
    resp = jsonify({
        "running": bool(job.get("running", False)),
        "accounts": job.get("accounts", []),
        "summary": job.get("summary", ""),
        "finished_at": job.get("finished_at", ""),
        "html": frag,
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


def parse_publish_form(r) -> dict:
    """把发布页表单解析成结构化参数，便于单测（不依赖真实请求/线程）。

    返回 {mids, acc_ids, overrides(全局覆盖), variants(按账号差异), stagger_minutes}
    variants 只针对「被勾选」的账号构建，避免把未选账号的隐藏字段带进去。
    """
    # 素材：发布页表单提交的是单选 <select name="material_id">，
    # 但这里原本只读 material_ids（复数）→ 永远读不到，点了发布直接报
    # 「请先选择要发布的素材」。两种名字都兼容，避免再被字段名不一致坑一次。
    mids = (r.form.getlist("material_ids")
            or ([r.form.get("material_id")] if r.form.get("material_id") else []))
    mids = [m for m in mids if m]
    acc_ids = [a for a in r.form.getlist("accounts") if a]

    overrides = {
        "short_title": (r.form.get("short_title") or "").strip(),
        "desc": (r.form.get("desc") or "").strip(),
        "tags": (r.form.get("tags") or "").strip(),
        "ai_declare": r.form.get("ai_declare", ""),
        "original": r.form.get("original", ""),
        "visibility": r.form.get("visibility", "公开"),
        "scheduled_at": (r.form.get("scheduled_at") or "").strip(),
        "ext_link": (r.form.get("ext_link") or "").strip(),
        "cover_path": (r.form.get("cover_path") or "").strip(),
        "collection": (r.form.get("collection") or "").strip(),
    }

    # 按账号差异化：每个被勾选的账号，收集其 ov_<field>_<aid> 覆盖项
    variants: dict = {}
    for aid in acc_ids:
        sub = {}
        for f in materials.VARIANT_FIELDS:
            val = (r.form.get(f"ov_{f}_{aid}") or "").strip()
            if val:
                sub[f] = val
        if sub:
            variants[aid] = sub

    try:
        stagger_minutes = int(float(r.form.get("stagger_minutes") or 0))
    except (TypeError, ValueError):
        stagger_minutes = 0
    if stagger_minutes < 0:
        stagger_minutes = 0

    # 发布模式：unified=统一（所有账号共用上方一套内容，忽略每账号差异化与错峰）；
    # stagger=错峰（启用每账号差异化 + 间隔）。未显式传参时保持旧行为（按错峰处理），
    # 以免破坏已有调用/测试。
    mode = (r.form.get("publish_mode") or "").strip().lower()
    if mode not in ("unified", "stagger"):
        mode = "stagger"
    if mode == "unified":
        variants = {}
        stagger_minutes = 0

    return {
        "mids": mids,
        "acc_ids": acc_ids,
        "overrides": overrides,
        "variants": variants,
        "stagger_minutes": stagger_minutes,
        "mode": mode,
    }


@app.route("/publish/start", methods=["POST"])
def publish_start():
    p = parse_publish_form(request)
    mids, acc_ids = p["mids"], p["acc_ids"]
    overrides, variants, stagger_minutes = p["overrides"], p["variants"], p["stagger_minutes"]
    mode = p.get("mode", "stagger")
    if not mids:
        return redirect(url_for("publish_page", err="请先选择要发布的素材"))
    if not acc_ids:
        return redirect(url_for("publish_page", err="请至少选择一个发布账号"))
    # 保存「按账号差异化」配置（可复用：下次发同一条素材到同批账号时记得上次的分化）。
    # 统一发布模式不写 variants —— 否则会把用户之前存好的差异化配置清空。
    if mode != "unified" and variants:
        for mid in mids:
            try:
                materials.set_variants(mid, variants)
            except Exception:
                pass
    try:
        # 逐个素材依次发布（单任务线程内串行，避免多号并发抢带宽与风控）
        for mid in mids:
            start_publish_job(mid, acc_ids, overrides,
                              variants=variants, stagger_minutes=stagger_minutes)
    except ValueError as e:
        return redirect(url_for("publish_page", err=str(e)))
    # 带上 material 参数：发布后回到页面时仍停在同一条素材，不用重新找
    return redirect(url_for("publish_page", started="1",
                            material=mids[0] if mids else ""))


@app.route("/publish/retry/<mid>/<aid>")
def publish_retry(mid, aid):
    """对失败账号单独重发。"""
    mat = materials.get_material(mid)
    if not mat or not accounts.get_account(aid):
        return redirect(url_for("publish_page"))
    try:
        start_publish_job(mid, [aid], {})
    except ValueError as e:
        return redirect(url_for("publish_page", err=str(e)))
    return redirect(url_for("publish_page", started="1", material=mid))