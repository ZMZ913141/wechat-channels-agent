"""浏览器启动：按账号档案启动 Chromium/Chrome，复用本机浏览器。"""
from contextlib import contextmanager
from pathlib import Path
from playwright.sync_api import sync_playwright, BrowserContext, Page
from ..config import settings


@contextmanager
def launch_context(profile_dir: Path, headless: bool = False, extra_args: list[str] | None = None):
    """启动一个带持久化档案的浏览器上下文。

    - profile_dir: 该账号专属的 user_data_dir（按平台+账号隔离）
    - headless: 发布用 True；扫码登录用 False
    - extra_args: 额外启动参数（如把窗口移出屏幕做"隐形有头"探测）
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = ["--disable-blink-features=AutomationControlled"]
    if extra_args:
        args.extend(extra_args)
    with sync_playwright() as p:
        launch_kwargs = dict(
            user_data_dir=str(profile_dir),
            headless=headless,
            args=args,
        )
        if settings.BROWSER_CHANNEL:
            launch_kwargs["channel"] = settings.BROWSER_CHANNEL
        ctx = p.chromium.launch_persistent_context(**launch_kwargs)
        try:
            # 抹掉 navigator.webdriver 等自动化特征，降低平台检测概率
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            """)
            yield ctx
        finally:
            ctx.close()


def open_publish_page(ctx: BrowserContext, url: str) -> Page:
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    return page
