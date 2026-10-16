"""公众号管理（官方 API，合规通道）：绑定服务号/订阅号后，在面板里发图文、发布、看粉丝数。

与视频号不同，公众号有成熟的官方开放接口（api.weixin.qq.com），
本模块全部走官方 API，不涉及逆向：

  - 绑定：appid + appsecret（公众平台「设置与开发 → 基本配置」获取，
          并把本机出口 IP 加入「IP 白名单」，否则获取 token 会报 40164）
  - 图文草稿：新建（草稿箱）、草稿列表、发布
  - 粉丝数：user/get 的 total
  - 封面：本地图片上传为永久素材，取 media_id 作为图文封面

说明：接收粉丝消息/留言需要公众号后台配置服务器回调（需公网地址），
本地面板做不了，属于公众号官方机制限制；本模块聚焦「内容发布」。
"""
import json
import os
import time

import requests

from . import config

MP_FILE = os.path.join(config.STATE_DIR, "mp.json")
TOKEN_FILE = os.path.join(config.STATE_DIR, "mp_token.json")
API = "https://api.weixin.qq.com"


def load_config() -> dict:
    try:
        with open(MP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"appid": "", "secret": ""}


def save_config(appid: str, secret: str) -> None:
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(MP_FILE, "w", encoding="utf-8") as f:
        json.dump({"appid": appid.strip(), "secret": secret.strip()}, f, ensure_ascii=False, indent=2)


def is_bound() -> bool:
    c = load_config()
    return bool(c.get("appid") and c.get("secret"))


def get_access_token(force: bool = False) -> str:
    """获取公众号 access_token（缓存到本地文件，有效期以官方返回为准）。"""
    cfg = load_config()
    if not force and os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, encoding="utf-8") as f:
                t = json.load(f)
            if t.get("appid") == cfg["appid"] and t.get("expire", 0) > time.time() + 60:
                return t["token"]
        except Exception:
            pass
    r = requests.get(
        f"{API}/cgi-bin/token",
        params={"grant_type": "client_credential", "appid": cfg["appid"], "secret": cfg["secret"]},
        timeout=10,
    )
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"获取公众号 access_token 失败：{data}（常见：IP 不在白名单 40164 / secret 错误 40001）")
    os.makedirs(config.STATE_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"appid": cfg["appid"], "token": data["access_token"],
                   "expire": time.time() + data.get("expires_in", 7200)}, f)
    return data["access_token"]


def _check(resp: dict) -> dict:
    if "errcode" in resp and resp.get("errcode") not in (0,):
        raise RuntimeError(f"公众号接口错误：{resp}")
    return resp


# ---------------------------------------------------------------------------
# 粉丝数
# ---------------------------------------------------------------------------
def follower_count() -> int | None:
    try:
        token = get_access_token()
        r = requests.get(f"{API}/cgi-bin/user/get", params={"access_token": token}, timeout=10).json()
        if "total" in r:
            return r["total"]
        print(f"[WARN] 获取粉丝数失败: {r}")
    except Exception as e:
        print(f"[WARN] 获取粉丝数失败: {e}")
    return None


# ---------------------------------------------------------------------------
# 封面上传（永久素材）
# ---------------------------------------------------------------------------
def upload_cover(image_path: str) -> str:
    """上传本地图片为永久素材，返回 media_id（用作图文封面 thumb_media_id）。"""
    if not os.path.exists(image_path):
        raise RuntimeError(f"封面图不存在：{image_path}")
    token = get_access_token()
    with open(image_path, "rb") as f:
        r = requests.post(
            f"{API}/cgi-bin/material/add_material",
            params={"access_token": token, "type": "image"},
            files={"media": f},
            timeout=30,
        ).json()
    _check(r)
    return r["media_id"]


# ---------------------------------------------------------------------------
# 草稿与发布
# ---------------------------------------------------------------------------
def _to_html(content: str) -> str:
    """把纯文本转成最简单的 HTML 段落（接口要求 content 为 HTML）。"""
    c = content.strip()
    if "<p" in c or "<br" in c or "<img" in c:
        return c  # 已是 HTML
    parts = [f"<p>{line.strip()}</p>" for line in c.splitlines() if line.strip()]
    return "".join(parts) or "<p></p>"


def add_draft(title: str, content: str, cover_path: str = "", author: str = "", digest: str = "") -> dict:
    """新建单图文草稿。cover_path 为本地封面图（必填，公众号图文必须有封面）。"""
    if not title.strip():
        raise RuntimeError("标题不能为空")
    thumb_id = upload_cover(cover_path) if cover_path else None
    if not thumb_id:
        raise RuntimeError("公众号图文必须封面图，请填写本地封面图路径")
    article = {
        "title": title.strip(),
        "author": author.strip(),
        "digest": (digest or title).strip()[:120],
        "content": _to_html(content),
        "thumb_media_id": thumb_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    token = get_access_token()
    r = requests.post(
        f"{API}/cgi-bin/draft/add",
        params={"access_token": token},
        json={"articles": [article]},
        timeout=15,
    ).json()
    _check(r)
    return r  # {"media_id": ...}


def list_drafts(offset: int = 0, count: int = 10) -> list:
    """草稿列表（新的在前）。返回 [{media_id, title, update_time}]。"""
    try:
        token = get_access_token()
        r = requests.post(
            f"{API}/cgi-bin/draft/batchget",
            params={"access_token": token},
            json={"no_content": 1, "offset": offset, "count": count},
            timeout=15,
        ).json()
        _check(r)
    except Exception as e:
        print(f"[WARN] 获取草稿列表失败: {e}")
        return []
    out = []
    for item in r.get("item", []):
        for art in item.get("content", {}).get("news_item", []):
            out.append({
                "media_id": item.get("media_id", ""),
                "title": art.get("title", ""),
                "update_time": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(item.get("update_time", 0))
                ),
            })
            break  # 只取每篇草稿的第一条图文
    return out


def publish_draft(media_id: str) -> dict:
    """发布草稿（freepublish）。发布后可在公众号后台「发表记录」查看。"""
    token = get_access_token()
    r = requests.post(
        f"{API}/cgi-bin/freepublish/submit",
        params={"access_token": token},
        json={"media_id": media_id},
        timeout=15,
    ).json()
    _check(r)
    return r
