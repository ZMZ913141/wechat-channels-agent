"""配置加载：从 .env 读取，全部带默认值，方便直接跑。"""
import os

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
# 发布过程默认【有头】运行，方便你肉眼确认上传与发表；稳定后可设 true 后台跑
PUBLISH_HEADLESS = _get("PUBLISH_HEADLESS", "false").lower() == "true"
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
