"""浏览器与 LLM 工厂：统一创建可复用的 Browser 和模型实例。"""
import os

from browser_use import Browser

from . import accounts, config


def detect_custom_browser() -> str | None:
    """探测一个「Playwright 兼容」的浏览器可执行文件路径。

    返回绝对路径或 None。用途：BROWSER_BACKEND=custom 时作为启动内核。

    ⚠️ ego-lite 是 Electron 应用（不是 Playwright 内核），其主程序不能被 Playwright
    直接驱动，所以这里不会返回 ego 的主程序；此项只接受真正兼容 Playwright 的
    内核（例如用户自己装的 Chromium / Chrome for Testing）。
    若 CUSTOM_BROWSER_PATH 指向一个存在的文件就直接用；否则在常见安装位置里找。
    """
    cand = (config.CUSTOM_BROWSER_PATH or "").strip()
    if cand and os.path.isfile(cand):
        return cand
    # 常见安装位置（按需补充）。ego-lite 主程序不在此列，因为无法被 Playwright 驱动。
    roots = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Chromium\Application\chrome.exe",
    ]
    for p in roots:
        if os.path.isfile(p):
            return p
    return None


def get_browser(account: dict | None = None, headless: bool | None = None) -> Browser:
    """创建一个带登录态持久化的浏览器实例。

    browser-use 0.13+ 的 Browser 直接支持 user_data_dir / headless 参数，
    用 user_data_dir 持久化 Cookie，首次扫码登录后，后续运行无需重复扫码。

    - account：多账号时传入对应账号（使用其独立 profile 目录）；为 None 用默认账号。
    - headless：为 None 时，监控用 MONITOR_HEADLESS，手动发表页固定有头（见 launcher）。
    - 抗检测：可选复用本机 Chrome/Edge（channel）、拟人操作延时、自定义 UA。
    """
    acc = account or accounts.current_account()
    profile = accounts.profile_path(acc)
    if headless is None:
        headless = config.MONITOR_HEADLESS
    kwargs = {
        "user_data_dir": profile,
        "headless": headless,
        # 默认关掉扩展下载：联网下载扩展在受限网络/代理下会卡死导致浏览器启动超时
        "enable_default_extensions": not config.BROWSER_DISABLE_EXTENSIONS,
    }
    # 自定义内核：BROWSER_BACKEND=custom 且探测到一个 Playwright 兼容内核时，
    # 用 executable_path 指定它（此时不走 channel，避免冲突）。
    backend = (config.BROWSER_BACKEND or "auto").strip().lower()
    custom = detect_custom_browser() if backend in ("custom", "ego") else None
    if custom:
        kwargs["executable_path"] = custom
    else:
        # 复用本机已装浏览器，指纹更一致（默认仍用自带 Chromium，保证开箱即用）
        # 支持「账号级内核」：账号指定了 channel 就用它的，否则沿用全局 BROWSER_CHANNEL。
        _ch = accounts.channel_of(acc) or (config.BROWSER_CHANNEL or "").strip().lower()
        if _ch and _ch != "chromium":
            kwargs["channel"] = _ch
    # 拟人操作延时，降低风控
    if config.WAIT_BETWEEN_ACTIONS and config.WAIT_BETWEEN_ACTIONS > 0:
        kwargs["wait_between_actions"] = config.WAIT_BETWEEN_ACTIONS
    # 自定义 User-Agent（留空则用浏览器默认）
    if config.USER_AGENT:
        kwargs["user_agent"] = config.USER_AGENT

    # Browser 只接受 browser_profile 参数，其余配置要放进 BrowserProfile
    try:
        from browser_use import BrowserProfile

        return Browser(browser_profile=BrowserProfile(**kwargs))
    except Exception:
        # 老版本没有 BrowserProfile / 不接受该参数 → 回退为直接传参（此时依赖
        # BROWSER_USE_DISABLE_EXTENSIONS 环境变量生效）
        kwargs.pop("enable_default_extensions", None)
        return Browser(**kwargs)


def get_llm():
    """按 LLM_PROVIDER 返回 browser-use 能直接使用的聊天模型实例。

    ⚠️ 关键坑：browser-use 0.13+ 的 `Agent.__init__` 会读取 `llm.provider`
    （见 browser_use/agent/service.py: `if llm.provider == 'browser-use'`），
    所以**必须**用它自带的 LLM 封装（`browser_use.llm.*`）——这些类自带
    `provider` 属性。如果传 langchain 的 `ChatOpenAI`，会直接抛
    `AttributeError: 'ChatOpenAI' object has no attribute 'provider'`，
    Agent 根本创建不出来，监控/发布整条链路全废（报错只在后台日志里，
    面板上看不出来）。

    因此这里的优先级是：browser_use 原生类 > langchain（老版本兜底）。
    """
    provider = (config.LLM_PROVIDER or "").strip().lower()
    model = config.LLM_MODEL
    key = config.LLM_API_KEY
    base = config.LLM_BASE_URL or None

    # ---- 1) browser_use 原生封装（推荐路径）----
    try:
        if provider == "browseruse":
            from browser_use.llm.browser_use.chat import ChatBrowserUse

            return ChatBrowserUse()

        if provider == "deepseek":
            from browser_use.llm.deepseek.chat import ChatDeepSeek

            return ChatDeepSeek(
                model=model or "deepseek-chat",
                api_key=key,
                base_url=base or "https://api.deepseek.com/v1",
            )

        if provider in ("openai", "openai_compatible", "custom"):
            from browser_use.llm.openai.chat import ChatOpenAI as BUChatOpenAI

            return BUChatOpenAI(model=model, api_key=key, base_url=base)

        if provider == "google":
            from browser_use.llm.google.chat import ChatGoogle

            return ChatGoogle(model=model, api_key=key)

        if provider == "anthropic":
            from browser_use.llm.anthropic.chat import ChatAnthropic

            return ChatAnthropic(model=model, api_key=key)

        if provider == "ollama":
            from browser_use.llm.ollama.chat import ChatOllama

            return ChatOllama(model=model, base_url=base or "http://localhost:11434")

        if provider == "groq":
            from browser_use.llm.groq.chat import ChatGroq

            return ChatGroq(model=model, api_key=key)

        if provider == "openrouter":
            from browser_use.llm.openrouter.chat import ChatOpenRouter

            return ChatOpenRouter(model=model, api_key=key)
    except ImportError:
        # 老版本 browser-use 没有 browser_use.llm.* → 落到下面的 langchain 兜底
        pass

    # ---- 2) langchain 兜底（仅老版本 browser-use 需要）----
    if provider in ("openai", "deepseek", "openai_compatible"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=key,
            base_url=base,
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, google_api_key=key)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=key)

    raise ValueError(f"未知 LLM_PROVIDER: {provider}，请检查 .env")
