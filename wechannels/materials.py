"""本地素材库：管理待发布的视频素材与文案，方便发布时取用。

每条素材记录：
  标题、短标题、描述、话题标签(tags)、**分类标签(labels)**、视频路径、封面路径、
  状态（待发布/已发布/草稿/发布中/失败）、
  发布配置（AI 内容声明 / 原创声明 / 可见范围 / 定时时间 / 允许下载评论）、
  发布历史（publish_history：每次发布到哪个账号、时间、结果）。
数据持久化在 state/materials.json，无需数据库。
支持按 account_id 过滤；视频路径会做存在性校验。

## ⚠ labels 与 tags 的区别（容易混淆，务必分清）
- `tags`  = **话题标签**，发布时会拼成 `#xxx #yyy` 进入视频描述，**观众可见**。
- `labels` = **分类标签**，纯内部管理用（"待剪辑"/"爆款"/"双十一"），**不会发布出去**。
两者都用逗号分隔、字段独立。素材一多时靠 labels 检索，发布时用 tags。
"""
import json
import os
import time
import uuid

from . import accounts, config

# 素材状态集合（用于状态管理与过滤）
# 「已归档」是维护页生成的软状态：只是从常用视图里滤掉，随时能改回，不删任何东西。
STATUSES = ["待发布", "草稿", "发布中", "已发布", "失败", "已归档"]
# 可在线预览/服务的媒体扩展名（用于缩略图渲染）
MEDIA_EXTS = {"mp4", "webm", "mov", "m4v", "jpg", "jpeg", "png", "gif", "webp"}
# 视频文件扩展名（本地目录导入用）
VIDEO_EXTS = {"mp4", "mov", "m4v", "webm", "avi", "mkv", "flv", "ts"}
# AI 内容声明选项（依据《人工智能生成合成内容标识办法》2025-09-01 施行，
# AI 生成合成内容发布时必须主动声明，否则会被平台限流/下架/处罚）
AI_DECLARE_OPTIONS = ["", "内容为AI制作", "内容为虚构剧情", "非AI内容"]
# 可见范围选项（与官方发布页一致）
VISIBILITY_OPTIONS = ["公开", "仅好友可见", "私密"]
# 官方限制提示
LIMITS = {"desc_max": 1000, "short_title": (6, 16), "video_max_gb": 4, "video_max_hours": 2}

# 新素材允许写入/更新的全部字段
_FIELDS = (
    "title", "short_title", "desc", "tags", "labels", "video_path", "cover_path", "status",
    "ai_declare", "original", "original_cat", "visibility",
    "allow_download", "allow_comment", "scheduled_at", "ext_link", "location",
)
# 分类标签的长度上限（防止误贴一长串）
LABEL_MAX_LEN = 12
LABEL_MAX_COUNT = 12


def _path() -> str:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    return os.path.join(config.STATE_DIR, "materials.json")


def load_materials(account_id: str | None = None) -> list:
    p = _path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return []
    if account_id:
        items = [it for it in items if it.get("account_id") == account_id]
    return items


def save_materials(items: list) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def add_material(data: dict, account_id: str | None = None) -> dict:
    items = load_materials()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    aid = account_id or accounts.current_account_id()
    item = {
        "id": uuid.uuid4().hex[:8],
        "account_id": aid,
        "title": data.get("title", ""),
        "short_title": data.get("short_title", ""),
        "desc": data.get("desc", ""),
        "tags": data.get("tags", ""),
        # 分类标签（内部管理用，不会发布出去）
        "labels": labels_str(data.get("labels", "")),
        "video_path": data.get("video_path", ""),
        "cover_path": data.get("cover_path", ""),
        "status": data.get("status", "待发布"),
        # 发布配置（仿官方发布页字段）
        "ai_declare": data.get("ai_declare", ""),
        # 声明原创：默认勾选（用户明确要求；未显式传参即视为 True）
        "original": str(data.get("original") if data.get("original") not in (None, "") else "true").lower()
        in ("1", "true", "on", "yes"),
        "original_cat": data.get("original_cat", ""),
        "visibility": data.get("visibility", "公开"),
        "allow_download": str(data.get("allow_download", "")).lower() in ("1", "true", "on", "yes"),
        "allow_comment": str(data.get("allow_comment", "true")).lower() in ("1", "true", "on", "yes"),
        "scheduled_at": data.get("scheduled_at", ""),
        "ext_link": data.get("ext_link", ""),
        "location": data.get("location", ""),
        # 发布历史：[{time, account_id, account_name, ok, result}]
        "publish_history": [],
        "created_at": now,
        "updated_at": now,
    }
    items.append(item)
    save_materials(items)
    return item


def get_material(mid: str):
    for it in load_materials():
        if it["id"] == mid:
            return it
    return None


def update_material(mid: str, data: dict):
    items = load_materials()
    for it in items:
        if it["id"] == mid:
            for k in _FIELDS:
                if k in data:
                    if k in ("original", "allow_download", "allow_comment"):
                        it[k] = str(data[k]).lower() in ("1", "true", "on", "yes")
                    elif k == "labels":
                        it[k] = labels_str(data[k])
                    else:
                        it[k] = data[k]
            it["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_materials(items)
            return it
    return None


def delete_material(mid: str) -> bool:
    items = load_materials()
    new = [it for it in items if it["id"] != mid]
    if len(new) == len(items):
        return False
    save_materials(new)
    return True


def delete_many(mids: list) -> int:
    """批量删除，返回实际删除条数。"""
    items = load_materials()
    s = set(mids)
    new = [it for it in items if it["id"] not in s]
    if len(new) == len(items):
        return 0
    save_materials(new)
    return len(items) - len(new)


def set_status(mid: str, status: str):
    return update_material(mid, {"status": status})


def set_status_many(mids: list, status: str) -> int:
    n = 0
    for mid in mids:
        if update_material(mid, {"status": status}):
            n += 1
    return n


def record_publish(mid: str, account: dict, ok: bool, result: str) -> None:
    """往素材的发布历史里追加一条记录（哪个账号、时间、结果）。"""
    items = load_materials()
    for it in items:
        if it["id"] == mid:
            hist = it.setdefault("publish_history", [])
            hist.append({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "account_id": account.get("id", ""),
                "account_name": account.get("name", ""),
                "ok": ok,
                "result": (result or "")[:160],
            })
            save_materials(items)
            return


def publish_history(mid: str) -> list:
    m = get_material(mid)
    return (m or {}).get("publish_history", [])


def media_dir() -> str:
    """用户上传的媒体统一存放目录（项目根下的 media/）。"""
    p = os.path.join(config._BASE, "media")
    os.makedirs(p, exist_ok=True)
    return p


def clone_material(mid: str, target_account_id: str) -> dict | None:
    """把一条素材「复用」到另一个账号：复制内容，生成新 id，状态重置为待发布。"""
    src = get_material(mid)
    if not src:
        return None
    data = {k: src.get(k, "") for k in _FIELDS if k != "status"}
    data["status"] = "待发布"
    return add_material(data, account_id=target_account_id)


def video_exists(material: dict) -> bool | None:
    """校验视频路径是否存在；路径为空返回 None。"""
    p = (material.get("video_path") or "").strip()
    if not p:
        return None
    return os.path.exists(p)


def list_dir_videos(dir_path: str) -> list:
    """列出本地目录下的视频文件（不递归），供「从目录导入」页使用。

    返回 [{path, name, size_mb, mtime}]，按修改时间倒序（最新导出的在前）。
    """
    ap = os.path.abspath(os.path.expanduser(dir_path.strip()))
    if not os.path.isdir(ap):
        return []
    out = []
    for fn in os.listdir(ap):
        fp = os.path.join(ap, fn)
        if not os.path.isfile(fp):
            continue
        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        if ext not in VIDEO_EXTS:
            continue
        try:
            st = os.stat(fp)
        except OSError:
            continue
        out.append({
            "path": fp,
            "name": fn,
            "size_mb": round(st.st_size / 1048576, 1),
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def import_from_dir(dir_path: str, filenames: list, account_id: str | None = None,
                     skip_duplicates: bool = True) -> dict:
    """把本地目录里的若干视频批量导入素材库（标题默认取文件名）。

    skip_duplicates=True 时自动跳过已导入过的文件（按文件名+大小指纹判断），
    避免反复点导入堆出一堆重复条目。
    返回 {"added": n, "skipped": m, "skipped_names": [...]}
    """
    aid = account_id or accounts.current_account_id()
    existing = set()
    if skip_duplicates:
        for it in load_materials(aid):
            vp = (it.get("video_path") or "").strip()
            if vp:
                existing.add(file_fingerprint(vp))
    n = 0
    skipped = []
    for fn in filenames:
        fp = os.path.join(os.path.abspath(dir_path), fn)
        if not os.path.isfile(fp):
            continue
        fpr = file_fingerprint(fp)
        if skip_duplicates and fpr and fpr in existing:
            skipped.append(fn)
            continue
        title = os.path.splitext(fn)[0]
        add_material({"title": title, "video_path": fp, "status": "待发布"}, account_id=aid)
        if fpr:
            existing.add(fpr)
        n += 1
    return {"added": n, "skipped": len(skipped), "skipped_names": skipped}


def import_paths(paths: list, account_id: str | None = None,
                 skip_duplicates: bool = True) -> dict:
    """按完整文件路径批量导入（每行一个路径，支持任意位置）。

    返回 {"added": n, "skipped": m, "skipped_names": [...]}
    """
    aid = account_id or accounts.current_account_id()
    existing = set()
    if skip_duplicates:
        for it in load_materials(aid):
            vp = (it.get("video_path") or "").strip()
            if vp:
                existing.add(file_fingerprint(vp))
    n = 0
    skipped = []
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p.strip().strip('"')))
        if not os.path.isfile(p):
            continue
        ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
        if ext not in VIDEO_EXTS:
            continue
        fpr = file_fingerprint(p)
        if skip_duplicates and fpr and fpr in existing:
            skipped.append(os.path.basename(p))
            continue
        title = os.path.splitext(os.path.basename(p))[0]
        add_material({"title": title, "video_path": p, "status": "待发布"}, account_id=aid)
        if fpr:
            existing.add(fpr)
        n += 1
    return {"added": n, "skipped": len(skipped), "skipped_names": skipped}


def copy_text(material: dict) -> str:
    """生成“一键复制”的文案：标题 + 描述 + #话题。"""
    parts = []
    if material.get("title"):
        parts.append(material["title"])
    if material.get("desc"):
        parts.append(material["desc"])
    tags = [t.strip() for t in (material.get("tags") or "").split(",") if t.strip()]
    if tags:
        parts.append(" ".join(f"#{t}" for t in tags))
    return "\n\n".join(parts)


# ===========================================================================
# 分类标签（labels）—— 内部检索用，与发布话题 tags 严格区分
# ===========================================================================
def norm_labels(raw) -> list:
    """把用户输入的标签串规范化成去重后的列表。

    容错设计（这些都是很容易踩的输入问题）：
    - 多种分隔符都认：逗号、中文逗号、顿号、分号、竖线、空白
    - 去掉 # 前缀和首尾空格，避免出现 "##爆款" 这种脏标签
    - 单个标签超长直接截断（防止误贴一整段描述）
    - 数量超限截断（防止把标签栏当垃圾桶用）
    """
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        s = str(raw or "")
        # 先把各类分隔符统一成逗号。注意：**空格也必须当分隔符**——
        # 用户很自然会输入 "#爆款 #待剪辑"，若不按空格切分会合并成一个标签。
        for sep in ("，", "、", "；", ";", "|", "/", "\n", "\t", " "):
            s = s.replace(sep, ",")
        parts = s.split(",")
    out, seen = [], set()
    for p in parts:
        t = p.strip().lstrip("#").strip()
        if not t:
            continue
        t = t[:LABEL_MAX_LEN]
        k = t.lower()
        if k in seen:      # 去重（大小写不敏感）
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= LABEL_MAX_COUNT:
            break
    return out


def labels_str(raw) -> str:
    """规范化后存回字符串（保持与 tags 一致的存储格式）。"""
    return ",".join(norm_labels(raw))


def get_labels(material: dict) -> list:
    """读出某条素材的分类标签列表。"""
    return norm_labels(material.get("labels") or "")


def all_labels(account_id: str | None = None) -> list:
    """当前账号下所有出现过的标签，按使用次数倒序（常用标签在前）。"""
    counts = {}
    for it in load_materials(account_id):
        for t in get_labels(it):
            counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def set_labels_many(mids: list, labels, mode: str = "add") -> int:
    """批量给素材打分类标签。

    mode：
      - add    追加（默认，不动已有标签）
      - replace 替换为这批标签
      - remove 移除指定标签
    返回被改动的条数。
    """
    items = load_materials()
    want = norm_labels(labels)
    changed = 0
    for it in items:
        if it["id"] not in set(mids):
            continue
        cur = get_labels(it)
        if mode == "replace":
            new = want
        elif mode == "remove":
            drop = {t.lower() for t in want}
            new = [t for t in cur if t.lower() not in drop]
        else:
            new = list(cur)
            for t in want:
                if t.lower() not in {x.lower() for x in new}:
                    new.append(t)
        new = new[:LABEL_MAX_COUNT]
        if new != cur:
            it["labels"] = ",".join(new)
            it["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            changed += 1
    if changed:
        save_materials(items)
    return changed


def filter_by_label(items: list, label: str) -> list:
    """按单个分类标签过滤（大小写不敏感）。"""
    if not label:
        return items
    k = label.strip().lower()
    return [it for it in items if any(t.lower() == k for t in get_labels(it))]


# ===========================================================================
# 去重：避免同一个视频被反复导入成多条素材
# ===========================================================================
def file_fingerprint(path: str) -> str:
    """文件指纹 = 文件名 + 字节大小。

    为什么不用 md5：算全文件哈希对几百MB 的视频要几十秒，
    导入时每条都算一遍会卡住。文件名+大小在实践中足够识别重复
    （同一视频改了名字仍可能被识别为不同条目，这是可接受的保守行为——
    宁可漏判也不能误判，两条不同素材被当成重复删掉才是事故）。
    """
    try:
        ap = os.path.abspath(path)
        return f"{os.path.basename(ap).lower()}|{os.path.getsize(ap)}"
    except OSError:
        return ""


def find_duplicates(account_id: str | None = None) -> list:
    """找出重复素材。

    返回 [{fingerprint, items: [{id, title, status, video_path}]}]
    只有同一指纹出现>= 2 条才算重复。
    """
    buckets = {}
    for it in load_materials(account_id):
        vp = (it.get("video_path") or "").strip()
        if not vp:
            continue
        fp = file_fingerprint(vp)
        if not fp:
            continue
        buckets.setdefault(fp, []).append(it)
    out = []
    for fp, items in buckets.items():
        if len(items) >= 2:
            out.append({
                "fingerprint": fp,
                "name": os.path.basename(items[0].get("video_path") or ""),
                "items": [{
                    "id": it["id"],
                    "title": it.get("title", ""),
                    "status": it.get("status", ""),
                    "video_path": it.get("video_path", ""),
                    "account_id": it.get("account_id", ""),
                    "updated_at": it.get("updated_at", ""),
                } for it in items],
            })
    out.sort(key=lambda g: len(g["items"]), reverse=True)
    return out


def find_orphans(account_id: str | None = None) -> list:
    """找出「素材还在、视频文件没了」的孤儿记录。

    这些记录无法发布，久了会堆积成噪音。**这里只报告不删除**，
    由用户确认后再清理。
    """
    out = []
    for it in load_materials(account_id):
        vp = (it.get("video_path") or "").strip()
        if not vp:
            # 连路径都没填：新建时忘了选文件
            out.append({**it, "reason": "未设置视频路径"})
            continue
        if not os.path.exists(vp):
            out.append({**it, "reason": "视频文件已丢失"})
    return out


def archive_stale_materials(days: int = 90, account_id: str | None = None) -> list:
    """找出「已发布且超过 N 天」的素材（待归档）。

    注意：只**报告**不改状态——归档是破坏性操作，必须由用户确认。
    """
    import datetime

    cutoff = (datetime.datetime.now() - datetime.timedelta(days=max(1, days))).strftime("%Y-%m-%d %H:%M:%S")
    out = []
    for it in load_materials(account_id):
        if it.get("status") != "已发布":
            continue
        if (it.get("updated_at") or "") < cutoff:
            out.append(it)
    return out


def library_stats(account_id: str | None = None) -> dict:
    """素材库概览：给面板顶部的统计条用。"""
    items = load_materials(account_id)
    by_status = {}
    for it in items:
        by_status[it.get("status", "待发布")] = by_status.get(it.get("status", "待发布"), 0) + 1
    missing = 0
    total_mb = 0.0
    for it in items:
        vp = (it.get("video_path") or "").strip()
        if vp and os.path.exists(vp):
            try:
                total_mb += os.path.getsize(vp) / 1048576
            except OSError:
                pass
        elif vp:
            missing += 1
    return {
        "total": len(items),
        "by_status": by_status,
        "labels": len(all_labels(account_id)),
        "missing_files": missing,
        "total_mb": round(total_mb, 1),
    }
