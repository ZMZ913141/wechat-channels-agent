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
# 私信监控默认【无头】运行（后台不弹窗）；首次扫码登录请用 open_publish 或 --headed
MONITOR_HEADLESS = _get("MONITOR_HEADLESS", "true").lower() == "true"
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
# 表现为「浏览器启动失败/监控一轮都跑不起来」，但面板上看不出原因。
# 关掉后功能不受影响（只是少了广告拦截），默认关。
BROWSER_DISABLE_EXTENSIONS = _get("BROWSER_DISABLE_EXTENSIONS", "true").lower() == "true"
# 同步给 browser-use：它内部 BrowserProfile 的默认工厂会读这个环境变量
if BROWSER_DISABLE_EXTENSIONS:
    os.environ.setdefault("BROWSER_USE_DISABLE_EXTENSIONS", "1")
# 单条自动回复的超时上限（秒）：AI Agent 在视频号的无界微前端里可能空转，
# 没有上限会拖死整轮监控（实测卡了 9 分钟以上）。超时后下一轮自动重试。
REPLY_TIMEOUT_S = float(_get("REPLY_TIMEOUT_S", "150"))
# 确定性提取（monitor_pw）是否用无头浏览器。
# ⚠️ 实测：无头下视频号助手会把 /platform/comment 重定向回首页，抓不到评论，
# 所以「抓评论」时本开关无效、强制有头；只抓私信时可用无头（后台不弹窗）。
PW_HEADLESS = _get("PW_HEADLESS", "false").lower() == "true"
# 是否让模型使用“视觉”（截图识别）。DeepSeek-chat 等纯文本模型不支持视觉，
# 必须设为 false；若你改用支持视觉的模型（如 gpt-4o / deepseek-vl），可设 true
USE_VISION = _get("USE_VISION", "false").lower() == "true"

# LLM
LLM_PROVIDER = _get("LLM_PROVIDER", "deepseek").lower()
LLM_API_KEY = _get("LLM_API_KEY", "")
LLM_MODEL = _get("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = _get("LLM_BASE_URL", "https://api.deepseek.com/v1")

# 通知
NOTIFY_WEBHOOK = _get("NOTIFY_WEBHOOK", "")
NOTIFY_WEBHOOK_TYPE = _get("NOTIFY_WEBHOOK_TYPE", "generic").lower()
# 通知渠道：wecom_app(企业微信应用消息，官方直推手机) / webhook(企微/钉钉群机器人) / serverchan / pushplus
NOTIFY_CHANNEL = _get("NOTIFY_CHANNEL", "webhook").lower()
SERVERCHAN_SENDKEY = _get("SERVERCHAN_SENDKEY", "")
PUSHPLUS_TOKEN = _get("PUSHPLUS_TOKEN", "")
# 企业微信「应用消息」参数（在企业微信管理后台创建自建应用后获得；
# 手机装企业微信即可收到，也可在企业微信里开启“微信插件”转发到个人微信）
WECOM_CORPID = _get("WECOM_CORPID", "")
WECOM_SECRET = _get("WECOM_SECRET", "")
WECOM_AGENTID = _get("WECOM_AGENTID", "")
WECOM_TOUSER = _get("WECOM_TOUSER", "@all")

# 视频号发表页（真实发布入口；/platform/upload 会被重定向到首页，故用 post/create）
PUBLISH_URL = _get("PUBLISH_URL", "https://channels.weixin.qq.com/platform/post/create")

# 状态
STATE_DIR = os.path.abspath(_get("STATE_DIR", os.path.join(_BASE, "state")))
SEEN_FILE = os.path.join(STATE_DIR, "seen.json")
NOTIFY_LOG = os.path.join(STATE_DIR, "notifications.log")

# 监控
MONITOR_INTERVAL = int(_get("MONITOR_INTERVAL", "300"))
# 是否同时监控「评论管理」里的视频评论（与私信在同一轮 Agent 中完成，不额外翻倍 token）
MONITOR_COMMENTS = _get("MONITOR_COMMENTS", "true").lower() == "true"
# 固定规则自动回复：私信命中关键词后自动发送预先配置的回复（无需 AI 生成内容）
AUTO_REPLY_ENABLED = _get("AUTO_REPLY_ENABLED", "false").lower() == "true"
# 评论也自动回复（与私信共用同一份规则库；评论可用 scope=comment 单独配）
AUTO_REPLY_COMMENTS = _get("AUTO_REPLY_COMMENTS", "true").lower() == "true"
# 同一个人只自动回复一次（true=首次之后不再自动回，避免连发/跨视频重复骚扰）
AUTO_REPLY_ONCE_PER_USER = _get("AUTO_REPLY_ONCE_PER_USER", "true").lower() == "true"
# 同一人两次自动回复之间的冷却秒数（0=不限制；设 3600 表示 1 小时内最多回一次）
# 仅当 AUTO_REPLY_ONCE_PER_USER=false 时生效，用于「允许多次回复但别太密」
AUTO_REPLY_COOLDOWN = int(_get("AUTO_REPLY_COOLDOWN", "0") or 0)

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

# 心跳文件：监控持续运行时周期性写入，供面板显示状态 / 看门狗判断“是否还活着”
HEARTBEAT_FILE = os.path.join(STATE_DIR, "heartbeat.json")
# 看门狗关机阈值（秒）：
#  - 心跳超过该时间未更新 → 视为进程卡死/崩溃（默认 10 分钟）
SHUTDOWN_HEARTBEAT_TIMEOUT = int(_get("SHUTDOWN_HEARTBEAT_TIMEOUT", "600"))
#  - 超过该时间没有一次“成功检查” → 视为持续断网/接口异常（默认 30 分钟）
SHUTDOWN_SUCCESS_TIMEOUT = int(_get("SHUTDOWN_SUCCESS_TIMEOUT", "1800"))
