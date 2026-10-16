"""登录态探针：判断「这个浏览器档案现在登的是哪个视频号」。

## 为什么单独成模块
多账号管理的**头号事故**是两个账号登成了同一个视频号——你以为自己有3 个号，
其实两个号发的是同一个视频号，矩阵完全失效。这个探针就是用来一眼看出来的：
扫码登录后立刻回报昵称，面板上每个账号旁边显示「实际登录身份」。

## 为什么用「发表页」而不是 /platform 判断登录态
实测：**登录成功后**直接开 `/platform` 也会被跳到 `/login.html`（营销落地页），
所以拿 /platform 判断会把已登录误判成未登录。
而 `/platform/post/create`（发表页）能稳定区分：
登录 → 停在 post/create（含「草稿箱/内容管理」）；未登录 → 跳 login.html。
"""
import json
import os
import time

from . import config

# 登录态探针专用入口（见模块文档：不能用 /platform）
PROBE_URL = "https://channels.weixin.qq.com/platform/post/create"

LOGIN_MARKS = ("登录视频号助手", "选择视频号登录", "请使用微信扫描", "qrconnect")

IDENTITY_TPL = "login_probe_{}.json"

# 平台外壳右上角昵称的 DOM 选择器（实测）：<span class="name">用户0284223142</span>
_NICK_SELECTORS = ("span.name", "[class*='nickname']", "[class*='userName']", "[class*='user-name']")


def _clean_nick(t: str) -> str:
    """去掉读取时可能带上的引号/括号等噪音字符。"""
    return (t or "").strip().strip("()（）'\"“”‘’ \t\n")


def identity_path(account_id: str) -> str:
    return os.path.join(config.STATE_DIR, IDENTITY_TPL.format(account_id))


def read_identity(account_id: str) -> dict:
    try:
        with open(identity_path(account_id), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write_identity(account_id: str, data: dict) -> None:
    try:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(identity_path(account_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _nickname_from_body(body: str) -> str:
    """从平台外壳页取出右上角昵称（纯文本兜底方案）。

    实测：发表页的 innerText 最后一行就是当前登录的视频号昵称
    （例：`… | 设置 | 通知中心 | 用户0284223142`）。取最后一行的启发式足够稳，
    并且加长度/关键词过滤，避免页面改版时把菜单项当成昵称。
    """
    lines = [x.strip() for x in (body or "").splitlines() if x.strip()]
    for ln in reversed(lines[-6:]):
        if 1 < len(ln) <= 24 and not any(
            k in ln for k in ("通知中心", "设置", "首页", "助手", "登录", "管理",
                              "数据中心", "收入", "草稿", "腾讯", "规范", "反馈",
                              "活动", "直播", "服务", "创作")
        ):
            return ln
    return ""


def nickname_from_page(page) -> str:
    """优先用 DOM 选择器读昵称（准），失败再退回纯文本启发式。"""
    for f in page.frames:
        for sel in _NICK_SELECTORS:
            try:
                el = f.query_selector(sel)
            except Exception:
                el = None
            if not el:
                continue
            try:
                t = _clean_nick(el.inner_text() or "")
            except Exception:
                t = ""
            if t and len(t) <= 40 and "视频号" not in t:
                return t
    try:
        return _clean_nick(nickname_from_body(page.evaluate("document.body.innerText") or ""))
    except Exception:
        return ""


def probe(page, wait_s: int = 20) -> dict:
    """用发表页判断登录态并读取身份。

    返回 {"logged_in": bool, "nickname": str, "url": str, "detail": str}
    """
    out = {"logged_in": False, "nickname": "", "url": "", "detail": ""}
    try:
        page.goto(PROBE_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        out["detail"] = f"发表页打开失败：{e}"
        return out
    # 等 SPA 完成跳转/渲染：未登录会跳 login.html，登录则停在 post/create
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(2)
        try:
            cur = page.url
            body = page.evaluate("document.body.innerText") or ""
        except Exception:
            cur, body = "", ""
        out["url"] = cur
        if "/login" in cur:
            out["logged_in"] = False
            out["detail"] = "发表页跳转到登录页"
            return out
        if "post/create" in cur and ("草稿箱" in body or "内容管理" in body):
            out["logged_in"] = True
            out["nickname"] = nickname_from_page(page)
            out["detail"] = "发表页正常打开"
            return out
    # 超时也没跳登录 → 以 URL 为准（宁可认为已登录，交给调用方再确认）
    out["logged_in"] = "/login" not in (out["url"] or "")
    out["nickname"] = nickname_from_page(page)
    out["detail"] = "发表页探测超时（按 URL 判定）"
    return out


def probe_account(acc: dict, channel: str | None = None, headless: bool = True) -> dict:
    """独立跑一次探针（面板「检测登录」按钮用），不改变浏览器档案内容。

    用无头模式，不打扰你正在做的事。
    """
    from playwright.sync_api import sync_playwright

    from . import accounts
    from .launcher import _launch

    profile = accounts.profile_path(acc)
    with sync_playwright() as p:
        try:
            ctx, used = _launch(p, profile, headless, accounts.channel_of(acc))
        except Exception as e:
            return {
                "logged_in": False, "nickname": "", "url": "",
                "detail": f"浏览器启动失败：{e}", "ts": time.time(),
                "at": time.strftime("%Y-%m-%d %H:%M:%S"), "browser": channel or "",
            }
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            res = probe(page)
            res["browser"] = used
            res["ts"] = time.time()
            res["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            write_identity(acc["id"], res)
            return res
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def identity_summary(account_id: str, max_age_s: int = 24 * 3600) -> dict:
    """给面板用：账号的登录身份摘要（含是否过期）。"""
    d = read_identity(account_id)
    if not d:
        return {"known": False, "logged_in": None, "nickname": "", "detail": "尚未检测", "stale": True}
    ts = float(d.get("ts") or 0)
    stale = (time.time() - ts) > max_age_s
    return {
        "known": True,
        "logged_in": bool(d.get("logged_in")),
        "nickname": d.get("nickname", ""),
        "detail": d.get("detail", ""),
        "at": d.get("at", ""),
        "stale": stale,
    }