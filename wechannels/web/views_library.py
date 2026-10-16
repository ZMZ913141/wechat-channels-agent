"""素材库维护（查重 / 孤儿清理 / 归档）与目录导入。"""
import html
import os
from urllib.parse import quote

from flask import redirect, render_template, request, url_for

from .. import accounts, materials
from .base import app


# ---------------------------------------------------------------------------
# 维护页
# ---------------------------------------------------------------------------
@app.route("/library/maintain")
def library_maintain():
    acc = accounts.current_account()
    aid = acc["id"]
    days = max(7, int(request.args.get("days", "90")))
    msg = ""
    done = request.args.get("done", "")
    if done:
        kind, _, n = done.partition(":")
        label_map = {
            "dup": "已删除重复素材",
            "orphan": "已清理文件丢失的素材",
            "archive": "已标记归档",
        }
        msg = f'<div class="msg">✓ {label_map.get(kind, "操作完成")}：{n} 条</div>'

    dups = materials.find_duplicates(aid)
    orphans = materials.find_orphans(aid)
    stales = materials.archive_stale_materials(days, aid)

    return render_template(
        "library_maintain.html",
        acc=acc,
        total=len(materials.load_materials(aid)),
        days=days,
        msg=msg,
        dups=dups,
        orphans=orphans,
        stales=stales,
        title="素材库维护",
    )


@app.route("/library/dedup", methods=["POST"])
def library_dedup():
    """删除重复组中除保留条以外的记录。"""
    mids = request.form.getlist("mids")
    keep = request.form.get("keep", "")
    n = 0
    for mid in mids:
        if mid != keep and materials.delete_material(mid):
            n += 1
    return redirect(url_for("library_maintain", done=f"dup:{n}"))


@app.route("/library/orphans", methods=["POST"])
def library_orphans():
    """清理文件已丢失的素材记录（不动用户的视频文件）。"""
    n = materials.delete_many(request.form.getlist("mids"))
    return redirect(url_for("library_maintain", done=f"orphan:{n}"))


@app.route("/library/archive", methods=["POST"])
def library_archive():
    """把「已发布且超过 N 天」的素材标记为已归档（可随时改回，不删任何东西）。"""
    days = max(7, int(request.form.get("days", "90")))
    items = materials.archive_stale_materials(days, accounts.current_account_id())
    n = materials.set_status_many([it["id"] for it in items], "已归档")
    return redirect(url_for("library_maintain", done=f"archive:{n}"))


# ---------------------------------------------------------------------------
# 目录导入
# ---------------------------------------------------------------------------
@app.route("/import")
def import_page():
    d = request.args.get("dir", "")
    msg = ""
    done = request.args.get("done", "")
    skipped = request.args.get("skip", "")
    names = request.args.get("names", "")
    if done:
        msg = f'<div class="msg">✓ 已导入 <b>{done}</b> 条素材到当前账号</div>'
        if skipped and int(skipped) > 0:
            msg += (f'<div class="muted">自动跳过 {skipped} 个已导入过的重复文件'
                    f'{("：" + html.escape(names)) if names else ""}</div>')
    files_html = ""
    if d and os.path.isdir(os.path.abspath(os.path.expanduser(d))):
        files = materials.list_dir_videos(d)
        if files:
            rows = "".join(
                f'<label class="frow"><input type="checkbox" name="files" value="{html.escape(f["name"])}" '
                f'onchange="updateBar()"><span class="fname">{html.escape(f["name"])}</span>'
                f'<span class="fsize">{f["size_mb"]} MB</span>'
                f'<span class="ftime">{f["mtime"]}</span></label>'
                for f in files
            )
            files_html = (
                '<form method="post" action="/import/do">'
                f'<input type="hidden" name="dir" value="{html.escape(d)}">'
                '<div class="filelist">'
                '<div class="fhead"><span></span><span>文件名</span>'
                '<span class="fsize">大小</span><span class="ftime">修改时间</span></div>'
                f'{rows}</div>'
                '<div class="listbar">'
                '<a class="btn ghost" href="javascript:toggleAll()">全选/全不选</a>'
                '<span class="muted" id="counter">已选 0 / {len(files)}</span>'
                '<button class="btn" type="submit">导入选中</button>'
                "</div></form>"
            )
        else:
            files_html = '<div class="empty">该目录下没有支持的视频文件</div>'
    return render_template(
        "import.html", dir=d, msg=msg, files_html=files_html,
        title="目录导入",
    )


@app.route("/import/do", methods=["POST"])
def import_do():
    d = request.form.get("dir", "")
    files = request.form.getlist("files")
    paths_raw = request.form.get("paths", "")
    skip_dup = request.form.get("skip_dup", "1") not in ("0", "false", "off")
    added = 0
    skipped = 0
    names = []
    if files and d:
        r = materials.import_from_dir(d, files, skip_duplicates=skip_dup)
        added += r["added"]; skipped += r["skipped"]; names += r["skipped_names"]
    if paths_raw.strip():
        paths = [ln.strip() for ln in paths_raw.splitlines() if ln.strip()]
        r = materials.import_paths(paths, skip_duplicates=skip_dup)
        added += r["added"]; skipped += r["skipped"]; names += r["skipped_names"]
    # 「跳过了几个重复」必须如实回报，否则用户会以为导入失败了
    return redirect(
        f"/import?dir={quote(d)}&done={added}&skip={skipped}&names={quote('、'.join(names[:5]))}"
    )