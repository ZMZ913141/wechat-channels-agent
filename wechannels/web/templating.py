"""模板全局函数：把「渲染素材卡片」需要的计算逻辑集中在这里。

此前这些逻辑散落在 app.py 的各个 `_xxx_html()` 辅助函数里，
混着路由代码、样式字符串和业务逻辑。重构后统一放在这里，
模板里直接调用，Python 侧只负责取数据。
"""
import html
import os

from flask import render_template

from .. import materials
from .base import app

# 状态 → 卡片左边框配色class
_STATUS_CLS = {
    "待发布": "warn", "已发布": "ok", "草稿": "acc",
    "发布中": "acc", "失败": "bad", "已归档": "acc",
}


def status_class(status: str) -> str:
    return _STATUS_CLS.get(status, "acc")


def topic_list(m: dict) -> list:
    """话题（会进视频描述）。"""
    return [t.strip() for t in (m.get("tags") or "").split(",") if t.strip()]


def copy_text(m: dict) -> str:
    """一键复制的文案。注意：m 可能为 None（发布页未选素材时）。"""
    if not m:
        return ""
    return (materials.copy_text(m) or "").replace('"', "&quot;")


def thumb(m: dict) -> str:
    """缩略图 HTML：优先自定义封面，其次取视频首帧，都没有则显示占位。"""
    cover = (m.get("cover_path") or "").strip()
    video = (m.get("video_path") or "").strip()
    url = None
    if cover and os.path.exists(cover):
        url = "/media/" + cover.replace("\\", "/").lstrip("/")
    elif video and os.path.exists(video):
        url = "/media/" + video.replace("\\", "/").lstrip("/")
    if url:
        return (f'<img src="{url}" alt="" loading="lazy" '
                f'onerror="this.parentNode.classList.add(\'noimg\');this.remove()">')
    return '<div class="nothumb">无预览</div>'


def badges(m: dict) -> str:
    """卡片徽章：视频合规告警、定时未生效等提示。"""
    out = []
    sched = (m.get("scheduled_at") or "").strip()
    if sched:
        out.append(f'<span class="bdg" title="定时发表：{html.escape(sched)}">⏰ 定时</span>')
    vp = (m.get("video_path") or "").strip()
    if vp and not os.path.exists(vp):
        out.append('<span class="bdg bad" title="视频文件已丢失，无法发布">📁 文件丢失</span>')
    if m.get("ai_declare") and m.get("ai_declare") != "非AI内容":
        out.append(f'<span class="bdg" title="AI 内容声明（依法必须）">🤖 {html.escape(m["ai_declare"])}</span>')
    return "".join(out)


def decorate(m: dict) -> dict:
    """给素材字典补上模板需要的派生字段（不改原数据）。"""
    if m is None:
        return None
    d = dict(m)
    d["labels_list"] = materials.get_labels(m)
    d["topic_list"] = topic_list(m)
    return d


def label_options(account_id: str) -> str:
    """已有标签的 datalist 选项，减少手输错误。"""
    out = [f'<option value="{html.escape(n)}">' for n, _ in materials.all_labels(account_id)]
    return "".join(out)


def label_cloud(account_id: str, current: str = "") -> str:
    """标签云：点击即筛选。"""
    pairs = materials.all_labels(account_id)
    if not pairs:
        return ""
    out = ['<div class="labelbar"><span class="muted" style="font-size:12px">分类标签</span>']
    if current:
        out.append('<a class="lchip on" href="/">✕ 清除筛选</a>')
    for name, cnt in pairs[:24]:
        on = " on" if (current and name.lower() == current.lower()) else ""
        out.append(
            f'<a class="lchip{on}" href="/?label={_urlq(name)}">{html.escape(name)}'
            f'<span class="cn">{cnt}</span></a>'
        )
    out.append("</div>")
    return "".join(out)


def _urlq(s: str) -> str:
    from urllib.parse import quote

    return quote(s)


def library_summary(account_id: str) -> str:
    s = materials.library_stats(account_id)
    bits = [f"共 <b>{s['total']}</b> 条"]
    if s["total_mb"]:
        bits.append(f"视频合计 <b>{s['total_mb']}</b> MB")
    if s["labels"]:
        bits.append(f"{s['labels']} 个标签")
    if s["missing_files"]:
        bits.append(f'<span style="color:var(--bad)">{s["missing_files"]} 条文件丢失</span>')
    return '<span class="muted" style="font-size:12px">' + " · ".join(bits) + "</span>"


def accounts_channel(a: dict) -> str:
    """账号当前选择的浏览器内核。"""
    from .. import accounts as _acc

    return _acc.channel_of(a)


def login_badge(aid: str) -> str:
    """登录态徽章（含实测身份）。注册为全局后模板可直接调用。"""
    from .views_accounts import login_badge_html

    return login_badge_html(aid)


def dt_local(value) -> str:
    """把 "2026-10-17 10:00" 转成 <input type="datetime-local"> 要的 "2026-10-17T10:00"。"""
    s = str(value or "").strip().replace(" ", "T")
    return s[:16]


# ---------------------------------------------------------------------------
# 注册为 Jinja2 全局
# ---------------------------------------------------------------------------
app.jinja_env.globals.update(
    status_class=status_class,
    topic_list=topic_list,
    copy_text=copy_text,
    thumb=thumb,
    badges=badges,
    decorate=decorate,
    label_options=label_options,
    label_cloud=label_cloud,
    library_summary=library_summary,
    accounts_channel=accounts_channel,
    login_badge=login_badge,
)
app.jinja_env.filters["dt_local"] = dt_local