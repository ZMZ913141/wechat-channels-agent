"""素材库导出：CSV（给人看/给Excel）与 JSON（给迁移/备份）。

## 为什么需要
1. **资产不能困在工具里**：素材库是你积累的文案资产，
   万一以后换工具、想批量喂给别的 AI、或者只想看看自己写了什么，
   不该只能在这个面板里读。
2. **CSV 用 utf-8-sig BOM**：不加 BOM，Excel 打开中文全是乱码（实测踩过）。
3. **JSON 用于迁移**：带完整字段，可被其他程序直接消费。

## 导出的内容边界
- **包含**：标题/短标题/描述/话题/分类标签/合集/状态/发布次数/时间等元数据
- **不包含**：视频文件本体（动辄上百 MB，导出无意义）
- 视频路径原样导出，方便你据此定位文件
"""
import csv
import io
import json
import os
import time

from flask import Response

from .. import materials


# CSV 列顺序（按「发布时最常看的」排）
CSV_COLUMNS = [
    ("title", "标题"),
    ("short_title", "短标题"),
    ("desc", "描述文案"),
    ("tags", "话题标签"),
    ("labels", "分类标签"),
    ("collection", "合集"),
    ("status", "状态"),
    ("ai_declare", "AI标注"),
    ("original", "声明原创"),
    ("visibility", "可见范围"),
    ("scheduled_at", "定时发表"),
    ("video_path", "视频路径"),
    ("cover_path", "封面路径"),
    ("file_size", "文件大小"),
    ("duration", "时长"),
    ("publish_count", "发布次数"),
    ("last_published", "最近发布"),
    ("created_at", "创建时间"),
    ("updated_at", "更新时间"),
    ("id", "素材ID"),
]


def _rows(account_id: str | None, keyword: str = "", label: str = "",
          collection: str = "") -> list:
    items = materials.load_materials(account_id)
    if label:
        items = materials.filter_by_label(items, label)
    if collection:
        items = [it for it in items
                 if (it.get("collection") or "").strip() == collection]
    if keyword:
        kw = keyword.lower()
        items = [it for it in items if _match(it, keyword)]
    return items


def _match(m: dict, q: str) -> bool:
    """与首页搜索保持一致的匹配逻辑，避免「搜得到却导不出」。"""
    q = (q or "").strip().lower()
    if not q:
        return True
    hay = " ".join(str(m.get(k, "") or "") for k in
                   ("title", "short_title", "desc", "tags", "labels",
                    "collection", "video_path")).lower()
    return q in hay


def _flat(m: dict) -> dict:
    """把素材摊平成一行（列表字段转成逗号串，发布历史转成摘要）。"""
    hist = m.get("publish_history") or []
    last = ""
    if hist:
        last = str(hist[-1].get("time", ""))
    return {
        "labels": ",".join(materials.get_labels(m)),
        "tags": ",".join(m.get("tags", "").split(",")) if m.get("tags") else "",
        "publish_count": len(hist),
        "last_published": last,
        "file_size": _fmt_size(m.get("video_path")),
        "duration": str(m.get("duration") or m.get("video_duration") or ""),
    }


def _fmt_size(path: str) -> str:
    try:
        n = os.path.getsize(path)
        return f"{n / 1048576:.1f}MB" if n else ""
    except Exception:
        return ""


def export_csv(account_id: str | None = None, keyword: str = "", label: str = "",
               collection: str = "") -> Response:
    items = _rows(account_id, keyword, label, collection)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([cn for _, cn in CSV_COLUMNS])
    for m in items:
        extra = _flat(m)
        row = []
        for key, _cn in CSV_COLUMNS:
            v = extra.get(key, m.get(key, ""))
            row.append(str(v if v is not None else ""))
        w.writerow(row)

    fn = f"素材库_{time.strftime('%Y%m%d_%H%M')}.csv"
    # ⚠️ BOM 必须有：Excel 不带 BOM 打开中文会乱码
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f"attachment; filename*=UTF-8''{fn}",
        },
    )


def export_json(account_id: str | None = None, keyword: str = "", label: str = "",
                collection: str = "") -> Response:
    items = _rows(account_id, keyword, label, collection)
    payload = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": "视频号助手",
        "count": len(items),
        "items": items,
    }
    fn = f"素材库_{time.strftime('%Y%m%d_%H%M')}.json"
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Disposition": f"attachment; filename*=UTF-8''{fn}",
        },
    )


def export_text(account_id: str | None = None, keyword: str = "",
                collection: str = "") -> Response:
    """纯文本文案（标题+描述+话题），方便直接喂给 AI 改写或复制粘贴。"""
    items = _rows(account_id, keyword, collection=collection)
    parts = []
    for i, m in enumerate(items, 1):
        parts.append(f"--- {i}. {m.get('title') or '(无标题)'} ---")
        if m.get("short_title"):
            parts.append(f"[短标题] {m['short_title']}")
        parts.append(m.get("desc") or "")
        if m.get("tags"):
            parts.append(" ".join("#" + t.strip() for t in m["tags"].split(",") if t.strip()))
        parts.append("")
    fn = f"文案_{time.strftime('%Y%m%d_%H%M')}.txt"
    return Response(
        "\n".join(parts),
        mimetype="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{fn}",
        },
    )