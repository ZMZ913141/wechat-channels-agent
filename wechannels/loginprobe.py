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

# 平台在「未真正登录」时显示的默认占位昵称。这些**不是**用户身份，
# 拿它们当账号标识会让多账号面板彻底失去意义（三个号都显示"微信用户"）。
PLACEHOLDER_NICKS = ("微信用户", "用户", "未登录", "视频号助手", "请登录")

# 平台外壳右上角昵称的 DOM 选择器。
# 实测教训：`span.name` 并非稳定存在（有的账号根本读不到），真正稳定的是
# `[class*=nickname]`。所以顺序很关键——先试语义明确的，再兜底。
_NICK_SELECTORS = (
    "[class*='nickname']",
    "[class*='userName']",
    "[class*='user-name']",
    "span.name",
)


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


def is_placeholder(nick: str) -> bool:
    """判断昵称是否为平台默认占位（=实际未真正登录）。"""
    return (nick or "").strip() in PLACEHOLDER_NICKS


def probe(page, wait_s: int = 20) -> dict:
    """用发表页判断登录态并读取身份。

    返回：
      logged_in  是否处于登录态（URL 未跳登录页）
      verified   昵称是否可信——**这是多账号辨识的关键**
      nickname   读到的昵称（可能是占位符）
      url/detail 诊断信息

    为什么需要 verified：实测「已登录但昵称显示『微信用户』」的情况非常普遍
    （登录态过期/ 半登录 / 视频号未设昵称）。如果面板把它当成真实身份显示，
    三个号会全都显示"微信用户"，用户完全无法分辨谁是谁——这正是我们要避免的。
    """
    out = {"logged_in": False, "verified": False, "nickname": "",
           "url": "", "detail": "", "placeholder": False}
    try:
        page.goto(PROBE_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        out["detail"] = f"发表页打开失败：{e}"
        return out
    deadline = time.time() + wait_s
    body = ""
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
            out["detail"] = "发表页跳转到登录页（该档案未登录）"
            return out
        if "post/create" in cur and ("草稿箱" in body or "内容管理" in body):
            break
    else:
        out["logged_in"] = "/login" not in (out["url"] or "")

    nick = nickname_from_page(page)
    out["nickname"] = nick
    out["placeholder"] = is_placeholder(nick)
    # 「跳到内容管理页」+「昵称不是占位符」= 可信身份
    out["verified"] = bool(out["logged_in"] and nick and not out["placeholder"])
    if out["placeholder"]:
        out["detail"] = f"昵称显示为默认占位「{nick}」，该档案很可能未真正登录"
    elif not nick:
        out["detail"] = "已登录但读不到昵称（页面结构可能已改版）"
    elif out["logged_in"]:
        out["detail"] = "已登录，昵称可信"
    return out


def _launch_headless(p, profile: str, channel: str | None = None):
    """无头启动浏览器档案（仅供登录态探测用）。

    为什么不复用 launcher._launch：
    1. launcher._launch 固定有头（headless=False），探测会弹出窗口打扰用户；
    2. 它只接受 (p, profile, channel) 三个参数，历史上曾被误传四个参数导致
       探测函数整体静默失败 —— 这里独立实现，参数顺序明确、不易再踩。

    返回 (context, 实际使用的内核说明)。
    """
    args = ["--disable-blink-features=AutomationControlled"]
    last_err = None
    for ch in _channel_candidates(channel):
        try:
            kw = dict(user_data_dir=profile, headless=True, args=args)
            if ch:
                kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(**kw)
            return ctx, (ch or "chromium(内置)")
        except Exception as e:
            last_err = e
    raise RuntimeError(f"浏览器启动失败：{last_err}")


def _channel_candidates(channel: str | None):
    """按偏好顺序列出可用内核。'' 表示 Playwright 内置 Chromium。"""
    from . import accounts as _acc

    try:
        return _acc.channel_candidates(channel)
    except Exception:
        return ["", "msedge", "chrome"]


def probe_account(acc: dict, channel: str | None = None, headless: bool = True) -> dict:
    """独立跑一次探针（面板「检测登录」按钮用），不改变浏览器档案内容。

    用无头模式，不打扰你正在做的事。
    """
    from playwright.sync_api import sync_playwright

    from . import accounts

    profile = accounts.profile_path(acc)
    with sync_playwright() as p:
        try:
            # 注意参数顺序：_launch_headless(p, profile, channel)
            ctx, used = _launch_headless(p, profile, accounts.channel_of(acc))
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
    """给面板用：账号的登录身份摘要（含是否过期、昵称是否可信）。"""
    d = read_identity(account_id)
    if not d:
        return {"known": False, "logged_in": None, "verified": False,
                "nickname": "", "detail": "尚未检测", "stale": True,
                "placeholder": False}
    ts = float(d.get("ts") or 0)
    stale = (time.time() - ts) > max_age_s
    nick = (d.get("nickname") or "").strip()
    # 老版本记录没有 verified 字段，按占位符规则补算，避免显示过期结论
    placeholder = bool(d.get("placeholder")) or is_placeholder(nick)
    verified = bool(d.get("verified")) if "verified" in d else (
        bool(d.get("logged_in")) and bool(nick) and not placeholder)
    return {
        "known": True,
        "logged_in": bool(d.get("logged_in")),
        "verified": verified,
        "placeholder": placeholder,
        "nickname": nick,
        "detail": d.get("detail", ""),
        "at": d.get("at", ""),
        "stale": stale,
    }


def display_name(account: dict, fallback: str = "") -> dict:
    """生成**辨识度最高**的账号显示信息。

    这直接回答「能不能显示对应视频号的名字」：
    - 你自己起的名字（"视频号 2"）只是标签，**不能区分真实身份**
    - 实测昵称（"雅太社"）才是真实身份
    - 但昵称可能读不到、或是"微信用户"这种占位符 → 这时必须明确告诉用户
       「该号未真正登录」，而不是显示一个会误导人的假名字

    返回 {primary, secondary, tone, verified}
      primary   主显示名（可信昵称 > 你起的名字）
      secondary 副标题（始终显示你的命名 + 档案目录，便于对号入座）
      tone      ok / warn / bad，用于着色
    """
    s = identity_summary(account["id"])
    label = (account.get("name") or account["id"]).strip()
    nick = (s.get("nickname") or "").strip()

    if s.get("verified") and nick:
        # 最理想：显示真实昵称，你的命名作为副标题
        return {"primary": nick, "secondary": label, "tone": "ok",
                "verified": True, "placeholder": False,
                "detail": s.get("detail", ""), "at": s.get("at", "")}

    if s.get("placeholder"):
        # 昵称是"微信用户"这类占位 → 明确警示，绝不把它当身份显示
        return {"primary": label, "secondary": "未真正登录（昵称显示占位符）",
                "tone": "bad", "verified": False, "placeholder": True,
                "detail": s.get("detail", ""), "at": s.get("at", "")}

    if s.get("logged_in") and not nick:
        return {"primary": label, "secondary": "已登录，昵称未读到",
                "tone": "warn", "verified": False, "placeholder": False,
                "detail": s.get("detail", ""), "at": s.get("at", "")}

    if s.get("known"):
        return {"primary": label, "secondary": "未登录",
                "tone": "bad", "verified": False, "placeholder": False,
                "detail": s.get("detail", ""), "at": s.get("at", "")}

    return {"primary": label, "secondary": "尚未检测登录态",
            "tone": "warn", "verified": False, "placeholder": False,
            "detail": "", "at": ""}