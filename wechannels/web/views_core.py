"""首页与素材库路由。"""
import html
import json
import os
import subprocess
import sys
import uuid

from flask import redirect, render_template, request, url_for

from .. import accounts, config, login_state, materials, media_probe, proclock
from .base import app, BASE_DIR
from .templating import decorate


def _cur():
    return accounts.current_account()


@app.route("/")
def index():
    acc = _cur()
    aid = acc["id"]
    sf = request.args.get("status", "")
    label_filter = request.args.get("label", "")
    q = request.args.get("q", "")
    sort = request.args.get("sort", "new")
    coll_f = request.args.get("collection", "")

    # 批量上传结果提示：如实说清"入库几个、跳过几个"，否则用户会以为失败
    flash = ""
    fm = request.args.get("msg", "")
    if fm.startswith("batch_upload:"):
        parts = fm.split(":")
        added = int(parts[1]) if len(parts) > 1 else 0
        skipped = int(parts[2]) if len(parts) > 2 else 0
        flash = (f'<div class="msg ok">✓ 已批量入库 <b>{added}</b> 条素材'
                 f'{"（只有一条，已直接跳到编辑页补文案）" if added == 1 else ""}</div>')
        if skipped:
            flash += (f'<div class="msg bad">⚠ 有 {skipped} 个文件被跳过'
                      f'（不是支持的视频格式，或保存失败）</div>')

    items = materials.load_materials(aid)
    if sf:
        items = [it for it in items if it.get("status") == sf]
    if label_filter:
        items = materials.filter_by_label(items, label_filter)
    if coll_f:
        items = [it for it in items if (it.get("collection") or "").strip() == coll_f]
    if q:
        items = [it for it in items if _match(it, q)]

    if sort == "updated":
        items.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    elif sort == "status":
        order = {s: i for i, s in enumerate(materials.STATUSES)}
        items.sort(key=lambda m: order.get(m.get("status", ""), 99))
    else:
        items.reverse()

    return render_template(
        "index.html",
        accounts=accounts.load_accounts(),
        cur_account_id=aid,
        cur_account_name=acc.get("name") or aid,
        account_count=len(accounts.load_accounts()),
        count=len(materials.load_materials(aid)),
        items=[decorate(m) for m in items],
        sf=sf, label_filter=label_filter, q=q, sort=sort,
        all_label_names=[n for n, _ in materials.all_labels(aid)],
        collections=materials.load_collections(),
        coll_counts=materials.collection_counts(aid),
        collection_filter=coll_f,
        flash=flash,
        title="素材库",
    )


def _match(m: dict, q: str) -> bool:
    q = (q or "").strip().lower()
    if not q:
        return True
    hay = " ".join([m.get("title", ""), m.get("short_title", ""),
                    m.get("desc", ""), m.get("tags", ""), m.get("labels", "")])
    return q in hay.lower()


@app.route("/switch_account", methods=["POST"])
def switch_account():
    aid = request.form.get("account_id")
    if accounts.get_account(aid):
        accounts.set_current_account(aid)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# 素材 CRUD
# ---------------------------------------------------------------------------
def _apply_uploads(data: dict) -> None:
    """把上传的视频/封面文件落到 media/，并回填路径。"""
    for field, prefix in (("video_file", "video"), ("cover_file", "cover")):
        f = request.files.get(field)
        if not f or not f.filename:
            continue
        name = os.path.basename(f.filename.replace("\\", "/"))
        safe = f"{prefix}_{os.path.splitext(name)[0][:40]}{os.path.splitext(name)[1]}"
        dest_dir = materials.media_dir()
        dest = os.path.join(dest_dir, safe)
        f.save(dest)
        data[field.replace("_file", "_path")] = dest


@app.route("/materials/batch_upload", methods=["POST"])
def materials_batch_upload():
    """批量拖入：一次上传多个视频，每条自动建素材（标题取文件名）。

    为什么单独开这个接口而不是复用 /materials/new：
    批量场景下用户不该被迫为每个文件填一遍标题/文案/标签。
    """
    files = request.files.getlist("video_files")
    if not files:
        return redirect(url_for("index", msg="batch_upload:0"))
    aid = accounts.current_account_id()
    # 批量拖入的默认标签，方便事后在标签云里成组筛出来
    labels = materials.labels_str(request.form.get("labels", ""))
    added, skipped = [], []
    for f in files:
        fn = os.path.basename((f.filename or "").replace("\\", "/"))
        if not fn:
            continue
        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        if ext not in materials.VIDEO_EXTS:
            skipped.append(fn)
            continue
        dest = os.path.join(materials.media_dir(), fn)
        # 同名文件已存在时不覆盖——避免把已有素材的文件悄悄换掉
        if os.path.exists(dest):
            dest = os.path.join(
                materials.media_dir(),
                f"{os.path.splitext(fn)[0][:36]}_{uuid.uuid4().hex[:4]}{ext}",
            )
        try:
            f.save(dest)
        except Exception as e:
            skipped.append(fn)
            print(f"[WARN] 保存失败 {fn}：{str(e)[:80]}")
            continue
        title = os.path.splitext(fn)[0]
        m = materials.add_material(
            {"title": title, "video_path": dest, "status": "待发布", "labels": labels},
            account_id=aid,
        )
        added.append({"id": m["id"], "title": title})
    msg = f"batch_upload:{len(added)}:{len(skipped)}"
    if len(added) == 1:
        # 只有一条时直接跳到编辑页，省得再手工填文案
        return redirect(url_for("edit_material", mid=added[0]["id"]))
    return redirect(url_for("index", msg=msg))


@app.route("/collections")
def collections_page():
    """合集管理：维护「已存在的合集名称清单」。

    ⚠️ 平台约束：视频号合集必须先在**视频号后台**创建，本工具只管理清单，
    发布时在发表页搜到并选中。名字必须与后台完全一致，否则选不中。
    """
    msg = ""
    m = request.args.get("msg", "")
    if m == "ok":
        msg = '<div class="msg ok">合集已添加</div>'
    elif m == "dup":
        msg = '<div class="msg bad">同名合集已存在，请直接选它</div>'
    elif m == "renamed":
        msg = '<div class="msg ok">已重命名，素材上的引用同步更新</div>'
    elif m == "del":
        msg = '<div class="msg ok">已删除；引用它的素材已清空合集（素材本身保留）</div>'
    elif m == "err":
        msg = '<div class="msg bad">操作失败，请检查是否重名</div>'

    return render_template(
        "collections.html",
        cols=materials.load_collections(),
        counts=materials.collection_counts(accounts.current_account_id()),
        msg=msg,
        title="合集管理",
    )


@app.route("/collections/add", methods=["POST"])
def collections_add():
    name = request.form.get("name", "")
    if not materials.add_collection(name, request.form.get("desc", "")):
        return redirect(url_for("collections_page", msg="dup" if name.strip() else "err"))
    return redirect(url_for("collections_page", msg="ok"))


@app.route("/collections/rename", methods=["POST"])
def collections_rename():
    ok = materials.update_collection(
        request.form.get("old", ""), request.form.get("name", ""),
        request.form.get("desc", ""),
    )
    return redirect(url_for("collections_page", msg="renamed" if ok else "err"))


@app.route("/collections/delete", methods=["POST"])
def collections_delete():
    materials.delete_collection(request.form.get("name", ""))
    return redirect(url_for("collections_page", msg="del"))


@app.route("/library/heal")
def library_heal():
    """路径自愈：视频文件被移动/改名后，自动把素材指回新位置。

    分两步：先预览（dry_run）让人确认，再实际修复。
    """
    if request.args.get("do") == "1":
        r = materials.heal_paths(dry_run=False)
        return redirect(url_for("library_maintain", done=f"heal:{len(r['healed'])}"))
    r = materials.heal_paths(dry_run=True)
    return render_template(
        "library_heal.html",
        r=r,
        acc=accounts.current_account(),
        title="找回被移动的视频",
    )


@app.route("/materials/new", methods=["POST"])
def new_material():
    data = request.form.to_dict()
    _apply_uploads(data)
    materials.add_material(data, account_id=accounts.current_account_id())
    return redirect(url_for("index"))


@app.route("/materials/autofill")
def materials_autofill():
    """AI 智能填写：生成文案字段。

    field 为空 → 一次返回全部字段（title/short_title/desc/tags/ai_declare），
                 供「一键生成全部」按钮使用；
    field 有值 → 只返回该字段，形如 {"value": "..."}，供单个 ✨ 按钮使用。

    内部已做兜底：未配 Key 或调用失败时自动退回本地规则，不会卡住用户。
    """
    from .. import autofill

    field = (request.args.get("field", "") or "").strip()
    base = (request.args.get("base", "") or "").strip()
    hint = (request.args.get("hint", "") or "").strip()

    # 路径输入时只取文件名喂给 AI —— 否则会生成「d:/videos/xxx.mp4」这种垃圾标题
    if base and ("/" in base or "\\" in base):
        base = base.replace("\\", "/").split("/")[-1]

    try:
        data = autofill.autofill(name=base, hint=hint, field=field) or {}
    except Exception as e:
        print(f"[WARN] AI 填写异常（{field or 'all'}）：{str(e)[:80]}")
        data = {}
    if field:
        return json.dumps({"value": data.get(field, "")}, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)


@app.route("/materials/<mid>/edit", methods=["GET", "POST"])
def edit_material(mid):
    m = materials.get_material(mid)
    if not m:
        return redirect(url_for("index"))
    if request.method == "POST":
        data = request.form.to_dict()
        _apply_uploads(data)
        materials.update_material(mid, data)
        return redirect(url_for("index"))
    from .views_accounts import login_badge_html

    return render_template(
        "material_edit.html",
        m=decorate(m),
        status_opts=materials.STATUSES,
        ai_opts=[v for v in materials.AI_DECLARE_OPTIONS if v != "非AI内容"],
        vis_opts=materials.VISIBILITY_OPTIONS,
        login_badge=login_badge_html(m.get("account_id", "")),
        collections=materials.load_collections(),
        all_collections=materials.collection_names(),
        all_label_names=[n for n, _ in materials.all_labels(m.get("account_id", ""))],
        title="编辑素材",
    )


@app.route("/materials/<mid>/cover", methods=["POST"])
def upload_cover(mid):
    f = request.files.get("cover_file")
    if f and f.filename:
        name = os.path.basename(f.filename.replace("\\", "/"))
        dest = os.path.join(materials.media_dir(),
                            "cover_" + os.path.splitext(name)[0][:40] + os.path.splitext(name)[1])
        f.save(dest)
        materials.update_material(mid, {"cover_path": dest})
    return redirect(url_for("edit_material", mid=mid))


@app.route("/materials/<mid>/delete")
def delete_material_route(mid):
    materials.delete_material(mid)
    return redirect(url_for("index"))


@app.route("/materials/batch", methods=["POST"])
def materials_batch():
    mids = request.form.getlist("mids")
    op = request.form.get("op", "")
    if not mids:
        return redirect(url_for("index"))
    if op == "delete":
        materials.delete_many(mids)
    elif op.startswith("mark_"):
        st = op[len("mark_"):]
        if st in materials.STATUSES:
            materials.set_status_many(mids, st)
    elif op in ("label_add", "label_replace", "label_remove"):
        mode = {"label_add": "add", "label_replace": "replace", "label_remove": "remove"}[op]
        materials.set_labels_many(mids, request.form.get("labels", ""), mode=mode)
    return redirect(url_for("index"))


@app.route("/materials/<mid>/status")
def status_route(mid):
    st = request.args.get("status", "")
    if st in materials.STATUSES:
        materials.set_status(mid, st)
    return redirect(url_for("index"))


@app.route("/materials/<mid>/clone", methods=["GET", "POST"])
def clone_material_route(mid):
    src = materials.get_material(mid)
    if not src:
        return redirect(url_for("index"))
    if request.method == "POST":
        target = request.form.get("target_account", "")
        if target and accounts.get_account(target):
            materials.clone_material(mid, target)
        return redirect(url_for("index"))
    return render_template(
        "material_clone.html",
        m=src,
        accounts=[a for a in accounts.load_accounts() if a.get("enabled", True)],
        title="复用素材到其他账号",
    )


# ---------------------------------------------------------------------------
# 本地文件服务（缩略图、封面预览）
# ---------------------------------------------------------------------------
def _safe_name(name: str) -> str:
    """只允许安全文件名，防止 ../ 穿越。"""
    if not name or name.startswith("/") or ".." in name:
        return ""
    return os.path.basename(name.replace("\\", "/"))


@app.route("/media/<path:name>")
def media_file(name):
    """按**绝对路径**提供本地媒体（素材可能散落在任意磁盘位置）。

    ⚠️ 这条路由等于开放了任意文件读取。之所以可接受：本工具只绑 127.0.0.1、
    单机自用。若将来要改host="0.0.0.0" 暴露到局域网，必须先加鉴权。
    """
    import mimetypes

    ap = os.path.abspath(name.replace("/", os.sep))
    if not os.path.isfile(ap):
        return ("文件不存在", 404)
    if ap.lower().endswith((".mp4", ".mov", ".webm", ".m4v")):
        return _send_video(ap)
    mime = mimetypes.guess_type(ap)[0] or "application/octet-stream"
    from flask import send_from_directory

    d, base = os.path.split(ap)
    return send_from_directory(d, base, mimetype=mime)


def _send_video(ap: str):
    """视频要支持 Range 请求，否则浏览器无法拖动进度条。"""
    from flask import Response

    size = os.path.getsize(ap)
    mime = "video/mp4" if ap.lower().endswith((".mp4", ".m4v")) else "video/mp4"
    rng = request.headers.get("Range", "")
    if not rng:
        def gen():
            with open(ap, "rb") as f:
                while True:
                    chunk = f.read(1024 * 512)
                    if not chunk:
                        break
                    yield chunk

        return Response(gen(), mimetype=mime, headers={"Accept-Ranges": "bytes"})
    try:
        s, e = rng.replace("bytes=", "").split("-")
        start = int(s) if s else 0
        end = int(e) if e else size - 1
        end = min(end, size - 1)
        length = end - start + 1
    except Exception:
        return Response(b"", status=416, mimetype=mime)

    def gen_range():
        with open(ap, "rb") as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(1024 * 256, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    return Response(
        gen_range(), 206, mimetype=mime,
        headers={"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes"},
    )