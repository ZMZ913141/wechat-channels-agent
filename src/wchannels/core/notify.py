"""飞书群机器人通知（webhook，无需扫码授权）。没配地址时静默跳过，不影响发布。"""
import json
import urllib.request

from ..config import settings
from ..logutil import get_logger

log = get_logger("notify")

EMOJI = {"info": "📣", "success": "✅", "fail": "⚠️", "data": "📊"}
# 飞书卡片标题栏配色
TEMPLATE = {"info": "blue", "success": "green", "fail": "red", "data": "turquoise"}

WORKBENCH_URL = settings.WORKBENCH_URL


def _workbench_url() -> str:
    """优先用隧道守卫写下的最新公网地址，其次 .env 配置，最后本地地址。"""
    try:
        p = settings.WX_DATA_DIR / "tunnel_url.txt"
        if p.exists():
            u = p.read_text(encoding="utf-8").strip()
            if u:
                return u
    except Exception:
        pass
    return settings.WORKBENCH_URL


def _card(title: str, lines: list[str] | None, level: str,
          link: str | None) -> dict:
    elements: list[dict] = []
    if lines:
        elements.append({"tag": "div",
                         "text": {"tag": "lark_md", "content": "\n".join(lines)}})
    if link:
        elements.append({"tag": "action", "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "打开工作台"},
            "type": "primary",
            "url": link,
        }]})
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": TEMPLATE.get(level, "blue"),
                "title": {"tag": "plain_text",
                          "content": f"{EMOJI.get(level, '📣')} {title}"},
            },
            "elements": elements,
        },
    }


def send(title: str, lines: list[str] | None = None, level: str = "info",
         link: str | None = None) -> bool:
    """发一条交互卡片到飞书群（带「打开工作台」按钮）。失败只记日志，不抛异常。"""
    url = (settings.FEISHU_WEBHOOK_URL or "").strip()
    if not url:
        return False
    payload = _card(title, lines, level, link or _workbench_url())
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", "ignore")
        # 飞书返回 code!=0 表示被限流/签名校验失败，这里只记日志
        if '"code":0' not in body.replace(" ", ""):
            log.warning("飞书通知返回异常：%s", body[:200])
        return True
    except Exception as e:
        log.warning("飞书通知发送失败：%s", e)
        return False
