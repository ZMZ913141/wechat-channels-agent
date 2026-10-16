"""打开视频号页面（扫码登录 / 手动发表）。

- 必须有头（用户要肉眼操作、扫码），固定 headless=False。
- 使用 launch_persistent_context + 账号独立 user_data_dir：
  扫码成功后登录态持久化保存，下次无需再扫码。
- 优先用本机已装的 Chrome/Edge 打开（页面兼容性、抗风控更好），
  本机没有时自动回退到 Playwright 自带 Chromium。
- 等待扫码登录成功并在控制台给出明确提示。
"""
import os
import time

from playwright.sync_api import sync_playwright

from . import accounts, config, loginprobe, proclock

# 视频号助手平台首页（未登录时会自动展示登录二维码）
PLATFORM_URL = "https://channels.weixin.qq.com/platform"

# 扫码最长等待时间（秒），超时只提示，不强制关闭窗口
LOGIN_WAIT_TIMEOUT = 600


def _is_logged_in_url(url: str) -> bool:
    """登录成功后 URL 会落在 /platform/xxx（登录页是 /platform/login 或跳回首页）。"""
    if "channels.weixin.qq.com" not in url:
        return False
    if "/platform" not in url:
        return False
    # 登录中间态的 URL
    if "/login" in url:
        return False
    return True


def _launch(p, profile: str, channel: str | None = None):
    """启动有头浏览器（持久化档案）。按账号偏好选内核，失败自动回退。

    - channel：账号指定的内核（''/None=自动）。'' 表示 Playwright 内置 Chromium。
    - 返回 (context, 实际使用的内核说明)。
    """
    args = ["--start-maximized", "--disable-blink-features=AutomationControlled"]
    last_err = None
    for ch in accounts.channel_candidates(channel):
        try:
            kw = dict(
                user_data_dir=profile,
                headless=False,
                no_viewport=True,
                args=args,
            )
            if ch:
                kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(**kw)
            return ctx, (ch or "chromium(内置)")
        except Exception as e:  # 本机没装该浏览器等情况 → 尝试下一个
            last_err = e
    raise last_err if last_err else RuntimeError("浏览器启动失败")


def open_publish_page(account: dict | None = None, open_url: str | None = None) -> None:
    """启动一个【有头】浏览器，打开指定页面，等待扫码登录成功，直到用户关闭窗口。

    - account：目标账号（决定用哪个独立登录档案）；None 用当前账号。
    - open_url：要打开的页面；默认发表页（未登录会自动先出二维码）。

    ⚠️ 档案独占：一个 Chromium 档案同时只能被一个进程真正占用。本函数会先抢
    「档案锁」；抢不到（通常是后台监控正在用）时**明确报错并退出**，
    绝不再开一个拿不到登录态的窗口（那会让用户误以为「刚登录过却显示未登录」）。
    """
    acc = account or accounts.current_account()
    profile = accounts.profile_path(acc)
    url = open_url or config.PUBLISH_URL
    aid = acc["id"]
    name = acc.get("name") or aid

    print(f"[INFO] 账号「{name}」({aid})")
    print(f"[INFO] 登录档案：{profile}")
    print(f"[INFO] 浏览器内核偏好：{accounts.channel_label(acc)}")

    # owner="manual"：这次打开本身就是「手动操作」，若面板已经为该账号打了
    # 暂停标记（点扫码登录时打的），要豁免自己——否则自己把自己拦在门外。
    ok, holder = proclock.acquire(
        profile, f"打开发表页（{aid}）", wait_s=45,
        respect_pause=True, owner="manual",
    )
    if not ok:
        print(f"[ERROR] 档案被占用：{proclock.holder_desc(holder)}")
        print("[HINT] 同一个浏览器档案不能被两个进程同时打开，此时硬开也拿不到登录态。")
        print("[HINT] 常见原因与处理：")
        print("[HINT]   1) 上一次打开的浏览器窗口还没关 → 直接关掉再试")
        print("[HINT]   2) 另一个账号正在发布 → 稍等它结束，或到面板看发布进度")
        print("[HINT]   3) 该账号被标记为「手动操作中」但窗口已关 → 面板点「解除暂停」")
        print("[HINT]   4) 残留锁（进程被强杀）→ 面板点「🧹 素材库维护」旁边的清理，"
              "或删除 state/profile_*.lock")
        return

    try:
        with sync_playwright() as p:
            try:
                ctx, used = _launch(p, profile, accounts.channel_of(acc))
            except Exception as e:
                print(f"[ERROR] 浏览器启动失败：{e}")
                print("[HINT] 如果提示 profile 正被占用，请先关闭残留的浏览器窗口。")
                return
            print(f"[INFO] 浏览器已启动（内核：{used}），正在打开页面：{url}")

            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"[WARN] 页面加载提示：{e}（窗口已打开，可直接手动操作）")

            print("[INFO] 若页面出现二维码，请用微信「扫一扫」扫码并在手机上确认登录。")
            print("[INFO] 登录成功后此窗口会提示；确认无误后直接关闭浏览器窗口即可。")

            # ---- 立刻回报「这个档案现在登的是哪个视频号」----
            # 这一步很关键：两个账号如果都登成了同一个视频号，面板上一眼就能看出来。
            probe = loginprobe.probe(page)
            if probe.get("logged_in"):
                print(f"[OK] ✅ 已登录，当前视频号：{probe.get('nickname') or '（读不到昵称）'}")
            else:
                print(f"[INFO] 当前未登录（{probe.get('detail')}），请扫码。")
            loginprobe.write_identity(
                aid,
                {**probe, "browser": used, "ts": time.time(),
                 "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            )

            # 等待：登录成功 或 用户关窗
            logged_notified = bool(probe.get("logged_in"))
            deadline = time.time() + LOGIN_WAIT_TIMEOUT
            while True:
                time.sleep(2)
                try:
                    pages = ctx.pages
                    if not pages:
                        print("[INFO] 浏览器窗口已关闭，结束。")
                        break
                    cur = pages[-1].url
                except Exception:
                    # 浏览器被用户直接关掉（进程已退出）
                    print("[INFO] 浏览器窗口已关闭，结束。")
                    break

                if _is_logged_in_url(cur):
                    if not logged_notified:
                        logged_notified = True
                        # 登录成功后重新读一次身份，写回面板
                        try:
                            pr = loginprobe.probe(page, wait_s=8)
                            loginprobe.write_identity(
                                aid,
                                {**pr, "browser": used, "ts": time.time(),
                                 "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                            )
                            print(f"[OK] ✅ 检测到登录成功！当前视频号：{pr.get('nickname') or '（读不到昵称）'}")
                        except Exception:
                            print("[OK] ✅ 检测到登录成功！")
                        print("[OK] 登录态已保存到该账号档案，下次无需再扫码。")
                        print("[INFO] 你可以继续在本窗口操作（上传视频等），完成后关闭窗口。")
                elif logged_notified:
                    # 登录态又失效/退出登录了
                    logged_notified = False
                    print("[WARN] 检测到退出登录，如需登录请重新扫码。")

                if time.time() > deadline:
                    print("[INFO] 已等待较长时间，脚本退出（浏览器窗口仍保留，可继续手动使用）。")
                    break

            try:
                ctx.close()
            except Exception:
                pass
    finally:
        proclock.release(profile)
        # 手动操作结束 → 立刻解除该账号的暂停标记，监控无需等到超时
        proclock.resume(aid)
