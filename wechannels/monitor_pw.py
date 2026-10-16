"""确定性提取视频号私信 / 评论（Playwright 直取，不依赖 LLM 操作浏览器）。

为什么要有这个模块（2026-10 实测踩坑）：
  视频号助手是「无界(wujie)」微前端：外壳 /platform/xxx 只有导航栏，
  真正的内容渲染在 iframe `.../micro/interaction/...` 里，而且 micro 子应用
  **不能直接访问**（直接打开会返回 `Cannot GET /micro/...`），必须由外壳承载。

  browser-use 的 Agent 读不进这个 iframe（它会卡在 shadow DOM 里反复 evaluate
  直到超时），所以「让大模型操作浏览器去抓」这条路在视频号上走不通。

  本模块的思路是把两件事拆开：
    1. 取内容 —— 用 Playwright 穿透 iframe 拿纯文本（确定性、快、可离线）
    2. 懂内容 —— 把文本交给大模型做结构化（纯文本任务，模型很稳）
"""
import json
import os
import time

from . import accounts, config, proclock

# 外壳页面 → 内容所在的 iframe 关键词
PAGES = {
    "dm": ("https://channels.weixin.qq.com/platform/private_msg", "micro/interaction/private_msg"),
    "comment": ("https://channels.weixin.qq.com/platform/comment", "micro/interaction/comment"),
}

LOGIN_MARKS = ("登录视频号助手", "选择视频号登录", "请使用微信扫描", "qrconnect")

# ⚠️ 登录态探针专用入口。
# 实测：**登录成功后**直接开 /platform 也会被跳到 /login.html（营销落地页），
# 所以不能拿 /platform 判断登录态——会把已登录误判成「未登录」，监控于是
# 每轮都推「未登录」告警、而面板上显示的却是已登录，非常误导。
# /platform/post/create（发表页）能稳定区分：登录 → 平台外壳（含右上角昵称）；
# 未登录 → 跳 login.html。
PROBE_URL = "https://channels.weixin.qq.com/platform/post/create"


# 子应用还没渲染时 body 只有 Vue 的 <noscript> 兜底文本，不能当内容用
NOT_RENDERED = "doesn't work properly without JavaScript"


class NeedLogin(Exception):
    """账号未登录（跳到登录页）。"""


class ProfileBusy(Exception):
    """该账号的浏览器档案正被另一个进程占用（监控 / 打开发表页 / 同步发布）。"""


def _launch(p, profile: str, headless: bool, channel: str | None = None):
    """按账号偏好选内核启动（持久化档案），失败自动回退。

    返回 (context, 实际使用的内核说明)。''（内置 Chromium）时不传 channel 参数。
    """
    args = ["--disable-blink-features=AutomationControlled"]
    last_err = None
    for ch in accounts.channel_candidates(channel):
        try:
            kw = dict(user_data_dir=profile, headless=headless, no_viewport=True, args=args)
            if ch:
                kw["channel"] = ch
            return p.chromium.launch_persistent_context(**kw), (ch or "chromium(内置)")
        except Exception as e:
            last_err = e
    raise last_err if last_err else RuntimeError("浏览器启动失败")


def _frame_text(page, keyword: str, timeout_s: int = 40) -> str:
    """等目标 iframe 出现并返回其纯文本。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for f in page.frames:
            if keyword in f.url:
                try:
                    t = f.evaluate("document.body.innerText") or ""
                except Exception:
                    t = ""
                if len(t) > 30 and NOT_RENDERED not in t:
                    return t
        time.sleep(1)
    return ""


def _goto_route(page, url: str, tries: int = 3) -> bool:
    """进入子路由。实测视频号助手偶尔会把深链重定向回 /platform，需要重进。"""
    route = url.rsplit("/", 1)[-1]
    for i in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        time.sleep(6)
        try:
            if route in page.url:
                return True
        except Exception:
            pass
        print(f"[WARN] 被重定向回首页（{page.url}），重试进入 {route}（{i + 1}/{tries}）")
    return False


# ---------------------------------------------------------------------------
# 登录态探针 / 当前登录的视频号身份
# ---------------------------------------------------------------------------
# 保存「这个档案实际登录的是哪个视频号」，供面板显示，便于发现两个账号登成同一个号
IDENTITY_TPL = "login_probe_{}.json"


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


def _clean_nick(t: str) -> str:
    """去掉读取时可能带上的引号/括号等噪音字符。"""
    return (t or "").strip().strip("()（）'\"“”‘’ \t\n")


# 平台外壳右上角昵称的 DOM 选择器（实测）：<span class="name">用户0284223142</span>
_NICK_SELECTORS = ("span.name", "[class*='nickname']", "[class*='userName']", "[class*='user-name']")


def _nickname_from_page(page) -> str:
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
        return _clean_nick(_nickname_from_body(page.evaluate("document.body.innerText") or ""))
    except Exception:
        return ""


def _login_probe(page, wait_s: int = 20) -> dict:
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
            out["nickname"] = _nickname_from_page(page)
            out["detail"] = "发表页正常打开"
            return out
    # 超时也没跳登录 → 以 URL 为准（宁可认为已登录，交给后续子路由再确认）
    out["logged_in"] = "/login" not in (out["url"] or "")
    out["nickname"] = _nickname_from_page(page)
    out["detail"] = "发表页探测超时（按 URL 判定）"
    return out


def _click_videos_with_comments(page, frame, limit: int = 3) -> list[str]:
    """评论管理页默认是「视频列表 + 评论数」，要点进视频才看得到评论内容。

    判定「哪个视频有评论」必须靠 DOM 结构，**不能写死业务关键词**。
    历史教训：这里曾经硬编码过「新中式|雅太社|供货|女装」——那是当时账号的类目，
    换任何一个其它垂直的账号评论抓取就会彻底失效（把业务数据写进了基础设施）。

    现在的通用策略（按可靠性排序，逐级兜底）：
      1. 结构优先：行元素上带 comment 类名 / 存在独立「评论数」列 → 直接点；
      2. 数字线索：文本里有「N条评论」「评论 N」「(12)」这类计数 → 点它；
      3. 兜底：列表前若干个可点击卡片。
    任何一级拿不到就退下一级，绝不因为「没匹配到」就静默返回空。
    """
    # 1) 找出带评论计数的行/卡片，返回其文本片段用于后续比对
    cands: list[str] = []
    try:
        cands = frame.evaluate(
            """(() => {
              const COUNT_RE = /(评论\\s*[:：]?\\s*\\d+)|(\\d+\\s*条\\s*评论)|(评论\\s*\\d+)/;
              const out = [];
              // 优先看「行/卡片」这类容器；视频列表通常是一行一条
              const rows = Array.from(document.querySelectorAll(
                'tr,[class*=row],[class*=card],[class*=item],[class*=list]'
              ));
              for (const r of rows) {
                if (r.children.length > 12) continue;   // 排除大容器
                const t = (r.innerText || '').trim();
                if (!t || t.length < 8 || t.length > 400) continue;
                const hasCommentMark =
                  /comment/i.test(r.className || '') ||
                  !!r.querySelector('[class*=comment],[class*=Comment],[class*=reply]');
                if (hasCommentMark || COUNT_RE.test(t)) out.push(t.slice(0, 60));
                if (out.length >= 15) break;
              }
              return out;
            })()"""
        ) or []
    except Exception as e:
        print(f"[WARN] 评论候选探测失败：{str(e)[:60]}")
        cands = []

    clicked = 0
    chunks: list[str] = []
    for _ in cands:
        if clicked >= limit:
            break
        try:
            before = frame.evaluate("document.body.innerText") or ""
        except Exception:
            before = ""
        try:
            ok = frame.evaluate(
                """(() => {
                  const COUNT_RE = /(评论\\s*[:：]?\\s*\\d+)|(\\d+\\s*条\\s*评论)|(评论\\s*\\d+)/;
                  const rows = Array.from(document.querySelectorAll(
                    'tr,[class*=row],[class*=card],[class*=item],[class*=list]'
                  ));
                  for (const r of rows) {
                    if (r.children.length > 12) continue;
                    const t = (r.innerText || '').trim();
                    if (!t || t.length < 8 || t.length > 400) continue;
                    const hasCommentMark =
                      /comment/i.test(r.className || '') ||
                      !!r.querySelector('[class*=comment],[class*=Comment],[class*=reply]');
                    if (!hasCommentMark && !COUNT_RE.test(t)) continue;
                    r.scrollIntoView && r.scrollIntoView();
                    r.click && r.click();
                    return true;
                  }
                  return false;
                })()"""
            )
        except Exception:
            ok = False
        if not ok:
            break
        clicked += 1
        time.sleep(5)
        try:
            t = frame.evaluate("document.body.innerText") or ""
        except Exception:
            t = ""
        # 页面没变化说明点空了，换下一个候选，而不是把同一屏文字重复收集
        if t and t != before:
            chunks.append(t[-3000:])
    if not cands:
        print("[WARN] 评论管理页未识别到「有评论」的视频行，可能页面结构已改版或该号暂无评论")
    return chunks


def grab(acc: dict, want_comment: bool = True, headless: bool | None = None) -> dict:
    """抓取一个账号的私信 / 评论原文。

    返回 {"dm": str, "comment": str, "comment_detail": str?, "identity": dict}。

    账号未登录时抛 NeedLogin；档案被其它进程占用时抛 ProfileBusy。
    """
    from playwright.sync_api import sync_playwright

    # ⚠️ 实测：无头模式下 /platform/comment 会被重定向回首页，拿不到评论页。
    # 所以只要要抓评论就必须有头；只抓私信时可以用无头（不弹窗）。
    if headless is None:
        headless = bool(getattr(config, "PW_HEADLESS", False)) and not want_comment
    if want_comment:
        headless = False
    profile = accounts.profile_path(acc)
    res = {"dm": "", "comment": ""}

    # 档案独占：与「打开发表页 / 扫码登录 / 同步发布」互斥。
    # 抢不到就明确报错——绝不静默开一个没有登录态的窗口（那正是「明明登录过却显示未登录」的来源）。
    ok, holder = proclock.acquire(profile, f"监控（{acc.get('id')}）", wait_s=5)
    if not ok:
        raise ProfileBusy(
            f"账号「{acc.get('name') or acc['id']}」的浏览器档案正被 {proclock.holder_desc(holder)} 占用，"
            "本轮跳过（等对方关闭后自动恢复）"
        )
    try:
        with sync_playwright() as p:
            ctx, _used = _launch(p, profile, headless, accounts.channel_of(acc))
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                # ---- 登录态判定 ----
                # 1) 若 6 小时内有成功的实测记录，直接信它，省掉一次页面加载；
                # 2) 否则用「发表页」实测（比 /platform 可靠，见 PROBE_URL 注释）。
                # 之前这里拿 /platform 判登录态，会把已登录误判成未登录 —— 监控于是
                # 每轮都推「未登录」告警，而面板上却显示已登录，非常误导。
                ident = read_identity(acc["id"])
                probe = None
                ident_fresh = bool(ident.get("logged_in")) and (
                    time.time() - float(ident.get("ts") or 0) < 6 * 3600
                )
                if not ident_fresh:
                    probe = _login_probe(page)
                    res["identity"] = probe
                    write_identity(acc["id"], {**probe, "ts": time.time(),
                                               "at": time.strftime("%Y-%m-%d %H:%M:%S")})
                    if not probe.get("logged_in"):
                        raise NeedLogin(f"账号「{acc.get('name') or acc['id']}」未登录")

                keys = ["dm"] + (["comment"] if want_comment else [])
                for key in keys:
                    url, kw = PAGES[key]
                    _goto_route(page, url)
                    # 子路由疑似跳登录时：先重试一次；仍失败再用发表页复核，
                    # 只有复核也判定未登录才真的抛 NeedLogin（避免偶发重定向误报）。
                    for attempt in range(3):
                        try:
                            cur = page.url
                            body = page.evaluate("document.body.innerText") or ""
                        except Exception:
                            cur, body = "", ""
                        looks_out = ("/login" in cur) or any(m in body for m in LOGIN_MARKS)
                        if not looks_out:
                            break
                        if attempt == 0:
                            print(f"[WARN] {key} 页疑似跳登录，重试一次确认…")
                            time.sleep(5)
                            _goto_route(page, url)
                            continue
                        if attempt == 1:
                            probe = _login_probe(page)
                            res["identity"] = probe
                            write_identity(acc["id"], {**probe, "ts": time.time(),
                                                       "at": time.strftime("%Y-%m-%d %H:%M:%S")})
                            if probe.get("logged_in"):
                                # 发表页正常 → 只是该子路由偶发，重新进一次即可
                                print(f"[WARN] {key} 页偶发跳登录（发表页正常），再进一次")
                                _goto_route(page, url)
                                continue
                        # 走到这里 = 功能子路由（私信/评论）反复跳登录。
                        # 若发表页能打开，说明只是“外壳登录”、真正的视频号会话没建好
                        # （常见于扫码后没在弹窗里选/确认视频号）。写回“未登录”，
                        # 让面板徽章与真实可用性一致，否则会误导用户以为已登录。
                        write_identity(acc["id"], {
                            "logged_in": False,
                            "nickname": (probe or {}).get("nickname", ""),
                            "url": cur,
                            "detail": f"{key} 页反复跳登录（发表页能打开但功能页不可用），请重新扫码并确认已选择视频号",
                            "ts": time.time(),
                            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        raise NeedLogin(f"账号「{acc.get('name') or acc['id']}」未登录")
                    res[key] = _frame_text(page, kw)

                    # 评论：点进有评论的视频，收集展开后的文本
                    if key == "comment" and res[key]:
                        fr = None
                        for f in page.frames:
                            if kw in f.url:
                                fr = f
                        if fr is not None:
                            extra = _click_videos_with_comments(page, fr)
                            if extra:
                                # 展开后的文本里真正有价值的评论明细在末尾（前面又是视频列表），
                                # 单独存一份，避免被上层统一截断时把评论切掉
                                res["comment_detail"] = "\n".join(t[-3000:] for t in extra)
                                res[key] = res[key] + "\n---- 展开评论 ----\n" + "\n".join(extra)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        proclock.release(profile)
    return res
