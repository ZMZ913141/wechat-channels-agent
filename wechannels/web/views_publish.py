"""发布中心：一条素材同步发到多个视频号。"""
import html
import json

from flask import redirect, render_template, request, url_for

from .. import accounts, config, materials, media_probe
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
                size_mb = round((info.get("size") or 0) / 1048576, 1)
                extra = f'<div class="bad">⚠️ {"；".join(warns)}</div>' if warns else ""
                probe_html = (f'<div class="probe {cls}">视频规格：{res} · {dur} · {size_mb} MB'
                              f' · {info.get("fps", "?")}fps{extra}</div>')

    return render_template(
        "publish.html",
        mat=decorate(mat) if mat else None,
        valid=valid,
        accounts=[a for a in accounts.load_accounts() if a.get("enabled", True)],
        cur_account_id=acc_id,
        ai_opts=[v for v in materials.AI_DECLARE_OPTIONS if v != "非AI内容"],
        vis_opts=materials.VISIBILITY_OPTIONS,
        login_badge=login_badge_html,
        probe_html=probe_html,
        job=read_job(),
        title="同步发布",
    )


@app.route("/publish/status")
def publish_status():
    """发布任务进度（前端轮询）。"""
    job = read_job() or {}
    return json.dumps({
        "running": job.get("running", False),
        "accounts": job.get("accounts", []),
        "summary": job.get("summary", ""),
        "finished_at": job.get("finished_at", ""),
    }, ensure_ascii=False)


@app.route("/publish/start", methods=["POST"])
def publish_start():
    mids = request.form.getlist("material_ids")
    acc_ids = request.form.getlist("accounts")
    overrides = {
        "short_title": (request.form.get("short_title") or "").strip(),
        "desc": (request.form.get("desc") or "").strip(),
        "tags": (request.form.get("tags") or "").strip(),
        "ai_declare": request.form.get("ai_declare", ""),
        "original": request.form.get("original", ""),
        "visibility": request.form.get("visibility", "公开"),
        "scheduled_at": (request.form.get("scheduled_at") or "").strip(),
        "ext_link": (request.form.get("ext_link") or "").strip(),
        "cover_path": (request.form.get("cover_path") or "").strip(),
    }
    if not mids:
        return redirect(url_for("publish_page", err="请先选择要发布的素材"))
    if not acc_ids:
        return redirect(url_for("publish_page", err="请至少选择一个发布账号"))
    try:
        # 逐个素材依次发布（单任务线程内串行，避免多号并发抢带宽与风控）
        for mid in mids:
            start_publish_job(mid, acc_ids, overrides)
    except ValueError as e:
        return redirect(url_for("publish_page", err=str(e)))
    return redirect(url_for("publish_page", started="1"))


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
    return redirect(url_for("publish_page", started="1"))