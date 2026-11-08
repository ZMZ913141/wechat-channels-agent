"""登录态相关：快速探测 + 交互式扫码登录（按平台）。

- probe_login：有头但移出屏幕的快速探测（用于「检测登录」按钮）。
- wait_login：打开【可见】浏览器，等用户扫码，轮询到登录成功或超时（用于「登录/扫码」）。

两个函数都返回 (logged_in, nickname)。昵称抓不到不等于掉线：各平台 DOM 差异大，
以「页面已离开登录页且没有扫码登录提示」作为登录判据，避免把已登录账号误标成掉线。
"""
import time
from pathlib import Path
from .launcher import launch_context
from .platforms import get_publisher

LOGIN_WAIT_S = 150  # 给用户扫码留足时间


def _read_nick(page) -> str:
    try:
        return page.evaluate(
            """() => {
                const sels = ['.nickname', '.user-name', '[class*="nickname"]',
                              '[class*="user-name"]', '.account-name', '.name'];
                for (const s of sels) {
                    try {
                        const el = document.querySelector(s);
                        const t = el && el.textContent.trim();
                        if (t && t.length <= 30) return t;
                    } catch (e) {}
                }
                return '';
            }"""
        ) or ""
    except Exception:
        return ""


def _page_logged_in(page) -> bool:
    """URL 不在登录页、页面也没有「扫码登录」提示 → 认为已登录。"""
    try:
        if "login" in (page.url or "").lower():
            return False
        txt = page.inner_text("body")[:400]
        for mark in ("扫码登录", "手机号登录", "微信扫一扫登录", "登录后即可"):
            if mark in txt:
                return False
        return True
    except Exception:
        return False


def probe_login(profile_dir: Path, platform: str = "wechat") -> tuple[bool, str]:
    """快速探测：返回 (是否已登录, 昵称)。

    视频号对无头模式有反爬（会主动让登录态失效，表现为「刚登录成功就掉线」），
    所以这里用【有头但移出屏幕】的窗口探测，既不被检测，也不打扰用户。
    """
    url = get_publisher(platform).publish_url
    hidden = ["--window-position=-32000,-32000", "--start-minimized"]
    try:
        with launch_context(profile_dir, headless=False, extra_args=hidden) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                return False, ""
            page.wait_for_timeout(3000)
            if not _page_logged_in(page):
                return False, ""
            return True, _read_nick(page)
    except Exception:
        return False, ""


def wait_login(profile_dir: Path, platform: str = "wechat", timeout_s: int = LOGIN_WAIT_S) -> tuple[bool, str]:
    """打开【可见】浏览器，等用户扫码登录。返回 (是否登录成功, 昵称)。

    期间浏览器窗口一直可见，用户扫码后页面跳转即被识别。
    窗口固定在屏幕中央打开，避免用户找不到。
    """
    url = get_publisher(platform).publish_url
    args = ["--window-size=1000,720"]
    try:
        import ctypes
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        args.append(f"--window-position={max(0, (sw - 1000) // 2)},"
                    f"{max(0, (sh - 720) // 2)}")
    except Exception:
        pass
    with launch_context(profile_dir, headless=False, extra_args=args) as ctx:
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            if _page_logged_in(page):
                nick = _read_nick(page)
                # 登录态 cookie 异步落盘，多等几秒再关，避免「成功了一会就掉线」
                page.wait_for_timeout(5000)
                return True, nick
        return False, ""
