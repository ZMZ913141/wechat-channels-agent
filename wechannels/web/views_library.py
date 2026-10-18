"""素材库维护（查重 / 孤儿清理 / 归档）与目录导入。"""
import html
import os
from urllib.parse import quote

from flask import redirect, render_template, request, url_for

from .. import accounts, materials
from .base import app
from .templating import media_url


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
    recursive = request.args.get("recursive", "1") not in ("0", "false", "off")
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
    groups = []
    depth_hit = False
    if d and os.path.isdir(os.path.abspath(os.path.expanduser(d))):
        res = materials.scan_import_dir(d, recursive=recursive)
        groups = res["groups"]
        depth_hit = res["depth_hit"]
        if groups:
            rows = "".join(
                _import_row(g) for g in groups
            )
            files_html = (
                '<form method="post" action="/import/do" id="impForm">'
                '<input type="hidden" name="dir" value="' + html.escape(d) + '">'
                '<input type="hidden" name="recursive" value="' + ("1" if recursive else "0") + '">'
                '<div class="filelist">'
                '<div class="fhead"><span></span><span>封面</span><span>文件名</span>'
                '<span class="fsize">大小</span><span class="ftime">修改时间</span>'
                '<span>所在子目录</span></div>'
                f'{rows}</div>'
                '<div class="listbar">'
                '<a class="btn ghost" href="javascript:toggleAll()">全选/全不选</a>'
                '<span class="muted" id="counter">已选 0 / {0}</span>'.format(len(groups))
                + '<button class="btn" type="submit">导入选中（自动带封面）</button>'
                "</div></form>"
            )
            if depth_hit:
                files_html += ('<div class="warnbox" style="margin-top:8px">'
                               '⚠ 扫描深度已达上限（4 层），更深的子目录未纳入，'
                               '可把目录路径选得更靠近视频再扫。</div>')
        else:
            files_html = '<div class="empty">该目录下没有支持的视频文件</div>'
    return render_template(
        "import.html", dir=d, recursive=recursive, msg=msg, files_html=files_html,
        groups=groups, title="目录导入",
    )


def _import_row(g: dict) -> str:
    """渲染一条导入分组：封面缩略图 + 文件信息 + 封面匹配徽标。"""
    name = html.escape(g["name"])
    size = g["size_mb"]
    mtime = html.escape(g["mtime"])
    folder = html.escape(g["folder"])
    vp = html.escape(g["video_path"])
    # 重复项默认不勾选（避免重复导入）；新文件默认勾选
    checked = "" if g["dup"] else "checked"
    dup_badge = '<span class="warnbox" style="font-size:11px;padding:1px 6px">已导入</span>' if g["dup"] else ""
    if g["cover_path"]:
        thumb = (f'<img class="fcover" src="{html.escape(media_url(g["cover_path"]))}" '
                 f'alt="封面" loading="lazy">')
        cover_badge = '<span class="okbox" style="font-size:11px;padding:1px 6px">封面✓</span>'
    else:
        thumb = '<div class="fcover fcover-empty">无</div>'
        cover_badge = '<span class="muted" style="font-size:11px">封面前往编辑页抓取</span>'
    return (
        f'<label class="frow{" dup" if g["dup"] else ""}">'
        f'<input type="checkbox" name="files" value="{vp}" {checked} onchange="updateBar()">'
        f'<span class="fcover-cell">{thumb}</span>'
        f'<span class="fname">{name}<br>{cover_badge} {dup_badge}</span>'
        f'<span class="fsize">{size} MB</span>'
        f'<span class="ftime">{mtime}</span>'
        f'<span class="fdir">{folder}</span>'
        '</label>'
    )


@app.route("/import/do", methods=["POST"])
def import_do():
    d = request.form.get("dir", "")
    files = request.form.getlist("files")
    paths_raw = request.form.get("paths", "")
    skip_dup = request.form.get("skip_dup", "1") not in ("0", "false", "off")
    # 始终尝试自动匹配封面：导入时把同级封面一并写进素材
    with_cover = request.form.get("with_cover", "1") not in ("0", "false", "off")
    added = 0
    skipped = 0
    names = []
    if files:
        r = materials.import_paths(files, skip_duplicates=skip_dup, with_cover=with_cover)
        added += r["added"]; skipped += r["skipped"]; names += r["skipped_names"]
    if paths_raw.strip():
        paths = [ln.strip() for ln in paths_raw.splitlines() if ln.strip()]
        r = materials.import_paths(paths, skip_duplicates=skip_dup, with_cover=with_cover)
        added += r["added"]; skipped += r["skipped"]; names += r["skipped_names"]
    # 「跳过了几个重复」必须如实回报，否则用户会以为导入失败了
    return redirect(
        f"/import?dir={quote(d)}&recursive={request.form.get('recursive','1')}"
        f"&done={added}&skip={skipped}&names={quote('、'.join(names[:5]))}"
    )