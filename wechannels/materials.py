"""本地素材库：管理待发布的视频素材与文案，方便发布时取用。

每条素材记录：
  标题、短标题、描述、话题标签、视频路径、封面路径、状态（待发布/已发布/草稿/发布中/失败）、
  发布配置（AI 内容声明 / 原创声明 / 可见范围 / 定时时间 / 允许下载评论）、
  发布历史（publish_history：每次发布到哪个账号、时间、结果）。
数据持久化在 state/materials.json，无需数据库。
支持按 account_id 过滤；视频路径会做存在性校验。
"""
import json
import os
import time
import uuid

from . import accounts, config

# 素材状态集合（用于状态管理与过滤）
STATUSES = ["待发布", "草稿", "发布中", "已发布", "失败"]
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
    "title", "short_title", "desc", "tags", "video_path", "cover_path", "status",
    "ai_declare", "original", "original_cat", "visibility",
    "allow_download", "allow_comment", "scheduled_at", "ext_link", "location",
)


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


def import_from_dir(dir_path: str, filenames: list, account_id: str | None = None) -> int:
    """把本地目录里的若干视频批量导入素材库（标题默认取文件名）。"""
    n = 0
    for fn in filenames:
        fp = os.path.join(os.path.abspath(dir_path), fn)
        if not os.path.isfile(fp):
            continue
        title = os.path.splitext(fn)[0]
        add_material(
            {"title": title, "video_path": fp, "status": "待发布"},
            account_id=account_id,
        )
        n += 1
    return n


def import_paths(paths: list, account_id: str | None = None) -> int:
    """按完整文件路径批量导入（每行一个路径，支持任意位置）。"""
    n = 0
    for p in paths:
        p = os.path.abspath(os.path.expanduser(p.strip().strip('"')))
        if not os.path.isfile(p):
            continue
        ext = p.rsplit(".", 1)[-1].lower() if "." in p else ""
        if ext not in VIDEO_EXTS:
            continue
        title = os.path.splitext(os.path.basename(p))[0]
        add_material(
            {"title": title, "video_path": p, "status": "待发布"},
            account_id=account_id,
        )
        n += 1
    return n


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
