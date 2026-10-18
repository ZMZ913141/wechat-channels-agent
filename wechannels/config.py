"""配置加载：从 .env 读取，全部带默认值，方便直接跑。"""
import os
import json
import time

from dotenv import load_dotenv

load_dotenv()

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get(name: str, default=None):
    return os.getenv(name, default)


# 视频号助手地址（browser-use 操作的站点）
CHANNELS_URL = "https://channels.weixin.qq.com"

# 浏览器
USER_DATA_DIR = os.path.abspath(_get("USER_DATA_DIR", os.path.join(_BASE, "browser_profile")))
HEADLESS = _get("HEADLESS", "false").lower() == "true"
# 抗检测 / 拟人（来自 browser-use 生产可靠性实践）：
# 浏览器渠道：留空=用 Playwright 自带的 Chromium；设为 chrome/edge 可复用本机已装浏览器，指纹更一致、更不易被识别为自动化
BROWSER_CHANNEL = _get("BROWSER_CHANNEL", "")
# 每次操作之间的随机拟人延时（秒），降低被风控的概率；0 表示不设置
WAIT_BETWEEN_ACTIONS = float(_get("WAIT_BETWEEN_ACTIONS", "1.0"))
# 可选：自定义 User-Agent（留空则用浏览器默认）
USER_AGENT = _get("USER_AGENT", "")
# 是否禁用 browser-use 的默认扩展（uBlock Origin Lite / cookie 拦截 / ClearURLs）。
# ⚠️ 关键坑：这些扩展是**启动时联网下载**的。若机器挂了代理或网络受限，
# 下载会卡住 → 超过 browser-use 内部 30s 事件超时 → BrowserStartEvent 被取消，
# 表现为「浏览器启动失败但面板上看不出原因」。关掉后功能不受影响，默认关。
BROWSER_DISABLE_EXTENSIONS = _get("BROWSER_DISABLE_EXTENSIONS", "true").lower() == "true"
# 同步给 browser-use：它内部 BrowserProfile 的默认工厂会读这个环境变量
if BROWSER_DISABLE_EXTENSIONS:
    os.environ.setdefault("BROWSER_USE_DISABLE_EXTENSIONS", "1")
# 是否让模型使用“视觉”（截图识别）。DeepSeek-chat 等纯文本模型不支持视觉，
# 必须设为 false；若你改用支持视觉的模型（如 gpt-4o / deepseek-vl），可设 true
USE_VISION = _get("USE_VISION", "false").lower() == "true"

# LLM
LLM_PROVIDER = _get("LLM_PROVIDER", "deepseek").lower()
LLM_API_KEY = _get("LLM_API_KEY", "")
LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.deepseek.com/v1")

# 视频号发表页（真实发布入口；/platform/upload 会被重定向到首页，故用 post/create）
PUBLISH_URL = _get("PUBLISH_URL", "https://channels.weixin.qq.com/platform/post/create")

# 状态
STATE_DIR = os.path.abspath(_get("STATE_DIR", os.path.join(_BASE, "state")))

# 同步发布
# 同步发布是否**无头**运行（不弹浏览器窗口）。
# 默认 true：发布是自动流程，没必要打断你；也避免多账号串行时反复弹窗。
# ⚠️ 首次扫码登录 / 需要肉眼确认上传过程时才设 false —— 那时用面板的
# 「扫码登录 / 打开发表页」按钮（有头），与此开关无关。
PUBLISH_HEADLESS = _get("PUBLISH_HEADLESS", "true").lower() == "true"
# 监控/后台 Agent 发布是否无头运行（有头只在用户主动「打开发表页」时出现）。
# 默认 true：后台任务不该打断你。
MONITOR_HEADLESS = _get("MONITOR_HEADLESS", "true").lower() == "true"
# 发布方式：
#   deterministic = 确定性填写（Playwright 直填表单，无需 LLM，快且稳；推荐）
#   agent         = browser-use Agent「看懂页面」操作（抗大幅改版，但依赖 LLM）
PUBLISH_METHOD = _get("PUBLISH_METHOD", "deterministic").lower()
# 确定性方式失败时是否自动回退 Agent
PUBLISH_AUTO_FALLBACK = _get("PUBLISH_AUTO_FALLBACK", "true").lower() == "true"
# 视频上传的硬超时（秒）。实测 172MB / 上行 0.5Mbps 时会传到一半失败，
# 若不设上限就会无限重传、整个任务永远挂着。默认 1 小时。
PUBLISH_UPLOAD_TIMEOUT_S = int(_get("PUBLISH_UPLOAD_TIMEOUT_S", "3600"))
# 同账号并发发布时的账号锁等待秒数。0 = 不等，直接报错让用户稍后重试。
# 调大有意义的情况：你自己点了发布又立刻再点一次，第二个任务等第一个跑完。
PUBLISH_TASK_LOCK_WAIT_S = float(_get("PUBLISH_TASK_LOCK_WAIT_S", "0"))


# ===========================================================================
# 原子写JSON —— 所有状态文件的落盘都必须走这里
# ===========================================================================
# os.replace 在 Windows 上遇文件占用会失败，重试参数
_REPLACE_RETRIES = 6
_REPLACE_BACKOFF = 0.02


def save_json_atomic(path: str, data, *, ensure_ascii: bool = False,
                     indent: int = 2) -> None:
    """把数据原子地写入 path。

    ## 为什么必须原子写
    `open(path, "w")` 会**先清空文件再逐字写入**。并发场景下（批量操作与
    后台任务同时进行、定时发布撞上手工编辑），其他线程正好在"已清空"
    与"尚未写完"之间读文件，就会读到**空内容或半个 JSON**：
      - json.load 直接抛 "Expecting value: line 1 column 1"
      - 更糟的是：读到空列表的调用方可能当成"素材全没了"再保存回去，
        形成真实的数据丢失（实测踩过）

    ## 为什么用 os.replace
    同文件系统内 `os.replace` 是原子操作——读方永远只能看到
    "完整的旧内容"或"完整的新内容"，不存在中间态。

    ## 临时文件名为什么要带线程 id
    只带 PID 是不够的：**同一进程内的多线程 PID 相同**，并发写会共用同一个
    tmp 文件 → 一个线程 os.replace 走了、另一个线程再 replace 就报
    "WinError 32 文件被占用"，甚至写出交错损坏的内容（实测）。
    所以必须带线程 id + 随机串，保证每个写入有独占的临时文件。
    """
    import threading
    import uuid as _uuid

    # ⚠️ 必须确保目标目录存在：部分调用方（如首次落盘、或没显式建目录的入口）
    # 未必先建好 STATE_DIR，否则 os.replace 前的临时文件 open 会直接抛
    # FileNotFoundError。这里兜底建一次，彻底消除这个脆弱点。
    _dir = os.path.dirname(path)
    if _dir:
        os.makedirs(_dir, exist_ok=True)

    tmp = f"{path}.tmp{os.getpid()}.{threading.get_ident():x}.{_uuid.uuid4().hex[:6]}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent)
            f.flush()
            os.fsync(f.fileno())

        # Windows 上 os.replace 可能因目标文件被其他进程/线程短暂占用而
        # 抛 PermissionError(WinError 5)。这是平台特性不是代码错误，
        # 退避重试几次即可——占用通常是毫秒级的（另一个读句柄）。
        last = None
        for i in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, path)
                last = None
                break
            except PermissionError as e:
                last = e
                time.sleep(_REPLACE_BACKOFF * (i + 1))
            except OSError:
                last = None
                break
        if last is not None:
            raise last
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise
