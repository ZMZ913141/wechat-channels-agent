"""首页与素材库路由。"""
import html
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import uuid

from flask import jsonify, redirect, render_template, request, url_for

from .. import (accounts, backup, config, ledger, login_state, materials,
                 media_probe, proclock)
from . import exporters
from .base import app, BASE_DIR
from .templating import decorate

_log = logging.getLogger("wechannels")


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

    # 批量上传/批量操作结果提示：如实说清"入库几个、跳过几个"，否则用户会以为失败
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
    elif fm.startswith("batch:"):
        parts = fm.split(":", 2)
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        text = parts[2] if len(parts) > 2 else ""
        cls = "bad" if n == 0 else "ok"
        flash = f'<div class="msg {cls}">{"✗" if n == 0 else "✓"} {html.escape(text)}</div>'

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

    # ---- 分页 ----
    # 为什么必须分页：素材上千时全量渲染会产出 ~2MB HTML，
    # 且每张卡片的 <video preload=metadata> 都是一次 HTTP 请求
    # （1000 条 = 1000 并发请求，浏览器直接卡死）。
    total = len(items)
    try:
        per_page = min(200, max(20, int(request.args.get("size", "60") or 60)))
    except (TypeError, ValueError):
        per_page = 60
    try:
        page = max(1, int(request.args.get("page", "1") or 1))
    except (TypeError, ValueError):
        page = 1
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages
    start = (page - 1) * per_page
    page_items = items[start:start + per_page]

    # ---- 为当前页批量抽首帧图 ----
    # 只处理本页（不是全库）：1000 条素材时全量抽帧要几分钟，
    # 而用户翻到第 1 页根本不需要后面的图。抽不出来的自动回退 <video>。
    try:
        from .. import thumbs as _thumbs
        _t0 = time.time()
        n_ok = 0
        for _m in page_items:
            vp = (_m.get("video_path") or "").strip()
            # 有自定义封面的不需要抽
            if not vp or not os.path.exists(vp) or (_m.get("cover_path") or "").strip():
                continue
            t = _thumbs.extract(vp)
            if t:
                _m["_thumb"] = t
                n_ok += 1
        # 只在真的新抽了帧时才打印，避免每次翻页都刷屏
        if n_ok > 0 and time.time() - _t0 > 0.5:
            print(f"[INFO] 首帧图就绪 {n_ok}/{len(page_items)}"
                  f"（{time.time() - _t0:.1f}s）")
    except Exception as e:
        print(f"[WARN] 首帧图生成跳过（不影响使用）：{str(e)[:60]}")

    return render_template(
        "index.html",
        accounts=accounts.load_accounts(),
        cur_account_id=aid,
        cur_account_name=acc.get("name") or aid,
        account_count=len(accounts.load_accounts()),
        count=len(materials.load_materials(aid)),
        items=[decorate(m) for m in page_items],
        sf=sf, label_filter=label_filter, q=q, sort=sort,
        # 分页上下文（切换筛选时靠它拼链接）
        statuses=materials.STATUSES,
        page=page, pages=pages, per_page=per_page,
        total_shown=total, offset=start,
        # 导出链接要带上当前筛选条件，做到「所见即所得」
        qs_all=urllib.parse.urlencode({
            k: v for k, v in (
                ("q", q), ("label", label_filter),
                ("collection", coll_f), ("status", sf),
                ("account", aid),
            ) if v
        }),
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


@app.route("/media/thumbs")
def media_thumbs():
    """按路径抽帧返回预览图（供新建素材表单填路径时预览画面）。

    ⚠️ 安全：path 是用户传来的本地路径，**只允许读视频文件**，
    且限定扩展名，避免被当成任意文件读取接口。
    """
    from .. import thumbs as _th
    path = (request.args.get("path", "") or "").strip()
    if not path:
        return jsonify(ok=False, reason="未提供路径")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in materials.VIDEO_EXTS:
        return jsonify(ok=False, reason=f"不是视频文件（.{ext or '无扩展名'}）")
    if not os.path.exists(path):
        return jsonify(ok=False, reason="文件不存在，请检查路径")
    try:
        dur = _th.probe_duration(path)
        # 预览条：帧数与时间窗都可调（用户要能只看开头 2-4 秒的密集帧）
        count = _int_arg("count", 6, 1, 24)
        start = _float_arg("start", None)
        end = _float_arg("end", None)
        # 尾帧档位：默认整段；前端可传 end=4 得到"前 4 秒"密集帧
        main_t = _float_arg("main_t", None)
        if main_t is None:
            main_t = (dur * 0.3 if dur > 0 else 0.5)
        main = _th.extract(path, at_second=main_t, width=480)
        strip = _th.preview_strip(path, count, start=start, end=end)
        # 把实际时间点一并返回：前端不再自己复算取点规则，避免两边漂移
        pts = _th.strip_points(count, dur, start, end)[:len(strip)]
        if not main:
            return jsonify(ok=False, reason="无法解码该视频（可能是不支持的编码）")
        try:
            st = os.stat(path)
            size_mb = round(st.st_size / 1048576, 1)
            w, h = _video_size(path)
        except OSError:
            size_mb, w, h = 0, 0, 0
        return jsonify(
            ok=True,
            main=_media_url(main),
            frames=[_media_url(f) for f in strip],
            times=[round(t, 2) for t in pts],
            main_t=round(main_t, 2),
            duration=round(dur, 2), width=w, height=h, size_mb=size_mb,
        )
    except Exception as e:
        print(f"[WARN] 抽帧预览失败：{str(e)[:70]}")
        return jsonify(ok=False, reason="读取失败")


def _int_arg(name, default, lo, hi):
    try:
        return max(lo, min(int(request.args.get(name, default)), hi))
    except (TypeError, ValueError):
        return default


def _float_arg(name, default):
    v = request.args.get(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@app.route("/media/frame")
def media_frame():
    """抽**单帧**预览图（不落正式封面）。

    与 /media/thumbs 的区别：这是"点某帧 → 大图换成那一帧"的即时反馈，
    走 thumbs 缓存目录（可被定期清理），不会污染 media/ 里的正式封面。
    """
    from .. import thumbs as _th
    path = (request.args.get("path", "") or "").strip()
    if not path:
        return jsonify(ok=False, reason="未提供路径")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in materials.VIDEO_EXTS:
        return jsonify(ok=False, reason=f"不是视频文件（.{ext or '无扩展名'}）")
    if not os.path.exists(path):
        return jsonify(ok=False, reason="文件不存在，请检查路径")
    t = _float_arg("t", 1.0)
    dur = _th.probe_duration(path)
    if dur > 0:
        t = max(0.05, min(t, dur - 0.05))
    out = _th.extract(path, at_second=t, width=720)
    if not out:
        return jsonify(ok=False, reason="该时间点抽帧失败")
    return jsonify(ok=True, url=_media_url(out), at=round(t, 2))


def _video_size(path: str):
    """取视频宽高（读不出返回 0,0）。不额外依赖 ffprobe 的第二次调用。"""
    from .. import thumbs as _th
    exe = _th._ffprobe_exe()
    if not exe:
        return 0, 0
    try:
        import subprocess as _sp, json as _js
        p = _sp.run([exe, "-v", "quiet", "-select_streams", "v:0",
                     "-show_entries", "stream=width,height",
                     "-print_format", "json", path],
                    capture_output=True, timeout=30)
        if p.returncode != 0:
            return 0, 0
        st = (_js.loads(p.stdout.decode("utf-8", "ignore") or "{}")
              .get("streams") or [{}])[0]
        return int(st.get("width") or 0), int(st.get("height") or 0)
    except Exception:
        return 0, 0


def _media_url(path: str) -> str:
    """本地绝对路径 → /media URL。复用 templating 里的实现（已处理 URL 编码与盘符）。"""
    from .templating import media_url

    return media_url(path)


@app.route("/media/grab_cover", methods=["GET", "POST"])
def media_grab_cover():
    """从视频指定时间点抓一帧存为封面。"""
    from .. import thumbs as _th
    path = (request.args.get("path", "") or "").strip()
    if not path:
        return jsonify(ok=False, reason="未提供路径")
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext not in materials.VIDEO_EXTS:
        return jsonify(ok=False, reason=f"不是视频文件（.{ext or '无扩展名'}）")
    if not os.path.exists(path):
        return jsonify(ok=False, reason="文件不存在，请检查路径")
    try:
        t = float(request.args.get("t", 1) or 1)
    except (TypeError, ValueError):
        t = 1.0
    dur = _th.probe_duration(path)
    if dur > 0:
        t = max(0.1, min(t, dur - 0.1))
    out = _th.grab_cover(path, at_second=t, width=1080)
    if not out:
        return jsonify(ok=False, reason="抓帧失败（视频可能已损坏或编码不支持）")
    return jsonify(ok=True, cover=out, url=_media_url(out), at=t)


@app.route("/library/export/<fmt>")
def library_export(fmt):
    """导出素材库。fmt = csv / json / txt

    支持按当前筛选条件导出（关键词/标签/合集），所见即所得。
    """
    from . import exporters as _ex

    aid = request.args.get("account", "") or None
    kw = request.args.get("q", "")
    lb = request.args.get("label", "")
    cl = request.args.get("collection", "")
    f = (fmt or "csv").lower()
    if f == "json":
        return _ex.export_json(aid, kw, lb, cl)
    if f == "txt":
        return _ex.export_text(aid, kw, cl)
    return _ex.export_csv(aid, kw, lb, cl)


@app.route("/backup")
def backup_page():
    return render_template(
        "backup.html",
        backups=backup.list_backups(),
        items=materials.load_materials(),
        cols=materials.load_collections(),
        batches=len(ledger.load_batches()),
        accs=len(accounts.load_accounts()),
        backup_path=backup.backup_dir(),
        msg=request.args.get("msg", ""),
        msg_cls=request.args.get("cls", "ok"),
        title="数据备份",
    )


@app.route("/backup/create", methods=["POST"])
def backup_create_route():
    r = backup.create(label="手动备份")
    if not r.get("ok"):
        return redirect(url_for("backup_page", msg=r.get("reason", "备份失败"),
                                cls="bad"))
    return redirect(url_for("backup_page", msg=f"✓ 已备份（{len(r['files'])} 个文件）"))


@app.route("/backup/restore", methods=["POST"])
def backup_restore_route():
    r = backup.restore(request.form.get("name", ""))
    if r.get("ok"):
        extra = f"，还原前快照：{r['snapshot']}" if r.get("snapshot") else ""
        return redirect(url_for(
            "backup_page",
            msg=f"✓ 已还原 {len(r['restored'])} 个文件{extra}（页面请手动刷新）"))
    return redirect(url_for("backup_page", msg="✗ " + r.get("reason", "还原失败"),
                            cls="bad"))


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
    # ⚠️ 必须用 jsonify：直接 json.dumps 字符串会让响应 Content-Type 变成
    # text/html，前端 fetch().json() 无法解析（这正是一个导致"一键生成无反应"的 bug）。
    if field:
        return jsonify(value=data.get(field, ""))
    return jsonify(data)


@app.route("/materials/<mid>/edit", methods=["GET", "POST"])
def edit_material(mid):
    m = materials.get_material(mid)
    if not m:
        # 静默跳首页会让用户以为"编辑成功了"——必须明确告知
        return redirect(url_for("index", msg=f"batch:0:素材 {mid[:8]} 不存在，可能已被删除"))
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


@app.route("/materials/<mid>/delete", methods=["POST"])
def delete_material_route(mid):
    """删除素材。

    ⚠️ 必须用 POST：删除是破坏性操作，若用 GET 则——
      - 浏览器预取/预加载可能误触发
      - 爬虫或聊天软件里的链接预览会顺手删掉你的素材
      - URL 被缓存后刷新页面会再次删除
    前端已改为表单提交 + confirm 二次确认。
    """
    if not materials.get_material(mid):
        return redirect(url_for("index", msg=f"batch:0:素材 {mid[:8]} 不存在，可能已被删除"))
    materials.delete_material(mid)
    return redirect(url_for("index", msg="batch:1:已删除素材记录（视频文件未动）"))


@app.route("/materials/batch", methods=["POST"])
def materials_batch():
    mids = request.form.getlist("mids")
    op = request.form.get("op", "")
    if not mids:
        return redirect(url_for("index"))
    msg = ""
    if op == "delete":
        materials.delete_many(mids)
        msg = f"batch:{len(mids)}:已删除素材记录（视频文件未动）"
    elif op.startswith("mark_"):
        st = op[len("mark_"):]
        if st in materials.STATUSES:
            materials.set_status_many(mids, st)
            msg = f"batch:{len(mids)}:已标记为「{st}」"
    elif op in ("label_add", "label_replace", "label_remove"):
        mode = {"label_add": "add", "label_replace": "replace", "label_remove": "remove"}[op]
        materials.set_labels_many(mids, request.form.get("labels", ""), mode=mode)
        verb = {"add": "追加", "replace": "替换", "remove": "移除"}[mode]
        msg = f"batch:{len(mids)}:已{verb}标签"
    elif op in ("collection_set", "collection_clear"):
        # 合集是"发布时要归入"的关键字段，批量设置能省掉逐条进编辑页
        if op == "collection_clear":
            materials.set_collection_many(mids, "")
            msg = f"batch:{len(mids)}:已清空合集"
        else:
            name = (request.form.get("collection") or "").strip()
            if name:
                known = materials.collection_names()
                if name not in known:
                    # 如实回报，不静默写一个不存在的合集名（发布时会选不中）
                    return redirect(url_for(
                        "index", msg=f"batch:{0}:合集「{name}」不在合集清单里，请先到「合集管理」登记"))
                materials.set_collection_many(mids, name)
                msg = f"batch:{len(mids)}:已归入合集「{name}」"
            else:
                return redirect(url_for("index", msg="batch:0:请先选择合集"))
    if msg:
        return redirect(url_for("index", msg=msg))
    return redirect(url_for("index"))


@app.route("/materials/<mid>/label", methods=["POST"])
def material_label_route(mid):
    """卡片内就地增删分类标签（省得进编辑页）。

    只影响 labels，不动标题/文案/发布历史。
    """
    op = (request.form.get("op") or "").strip()
    label = (request.form.get("label") or "").strip()
    if not materials.get_material(mid):
        return redirect(url_for("index", msg=f"batch:0:素材 {mid[:8]} 不存在"))
    if op == "add":
        if not label:
            return redirect(url_for("index", msg="batch:0:标签不能为空"))
        cur = materials.get_labels(materials.get_material(mid))
        if label.lower() in {x.lower() for x in cur}:
            return redirect(url_for("index", msg=f'batch:0:标签「{html.escape(label)}」已经有了'))
        materials.set_labels_many([mid], label, mode="add")
        return redirect(url_for("index", msg=f"batch:1:已添加标签「{html.escape(label)}」"))
    if op == "remove":
        materials.set_labels_many([mid], label, mode="remove")
        return redirect(url_for("index", msg=f"batch:1:已移除标签「{html.escape(label)}」"))
    return redirect(url_for("index"))


@app.route("/materials/<mid>/status", methods=["POST"])
def status_route(mid):
    """改状态（POST：状态变更影响发布筛选，不该被预取误触发）。"""
    st = (request.form.get("status") or request.args.get("status") or "").strip()
    if not materials.get_material(mid):
        return redirect(url_for("index", msg=f"batch:0:素材 {mid[:8]} 不存在，可能已被删除"))
    if st in materials.STATUSES:
        m = materials.get_material(mid) or {}
        old = m.get("status", "")
        materials.set_status(mid, st)
        # 直接告诉用户"可以撤销"，并给出撤销入口 —— 误点后不用再找
        tip = f'已改为「{html.escape(st)}」' if old != st else ""
        if old != st:
            tip += (f'&nbsp;<a href="/materials/{mid}/undo_status" '
                    f'style="color:var(--acc);text-decoration:underline">撤销</a>')
        if tip:
            return redirect(url_for("index", msg=f"batch:1:{tip}"))
    return redirect(url_for("index"))


@app.route("/materials/<mid>/undo_status")
def undo_status_route(mid):
    """撤销最近一次状态变更。"""
    ok, text = materials.undo_status(mid)
    return redirect(url_for("index", msg=f"batch:{1 if ok else 0}:{text}"))


@app.route("/materials/<mid>/clone", methods=["GET", "POST"])
def clone_material_route(mid):
    src = materials.get_material(mid)
    if not src:
        return redirect(url_for("index", msg=f"batch:0:素材 {mid[:8]} 不存在，可能已被删除"))
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

    安全基线：拒绝任何「父目录穿越」（`..`）与异常前缀，避免被利用去读
    素材目录之外的文件。合法媒体 URL 由 templating.media_url 生成，
    只会是「盘符:绝对路径」且不含 `..`，不受影响。
    """
    import mimetypes

    if not name or ".." in name.split("/") or name.startswith("/") or name.startswith("\\"):
        _log.warning("拒绝可疑的 /media 请求（疑似路径穿越）: %r", name)
        return ("非法路径", 400)

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
    ext = ap.lower().rsplit(".", 1)[-1]
    mime = {
        "mp4": "video/mp4",
        "m4v": "video/mp4",
        "mov": "video/quicktime",
        "webm": "video/webm",
    }.get(ext, "video/mp4")
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
        unit, _, spec = rng.partition("=")
        if unit.strip().lower() != "bytes":
            return Response(b"", status=416, mimetype=mime)
        s, _sep, e = spec.strip().partition("-")
        start = int(s) if s else 0
        end = int(e) if e else size - 1   # 开放区间（bytes=0-）也要支持
        end = min(end, size - 1)
        if start > end:
            return Response(b"", status=416, mimetype=mime)
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