"""AI 评论/私信回复草稿：客服口吻、短句、不承诺不确定信息。

输出约束（程序强制，不只靠 prompt）：
- 评论 30 字内、私信 60 字内，超了在标点处截断不截半句；
- 过滤口水词与广告法高风险词；
- 去除包裹的引号。
"""
from . import client
from .autofill import _sanitize, _truncate_at_punct

SYSTEM = (
    "你是一个中文短视频账号的客服，负责回复观众评论。"
    "要求：1) 只输出回复正文，不要解释、不要引号；2) 30 字以内，口语化、真诚；"
    "3) 价格/库存/发货/尺码等无法确认的信息，只引导观众私信或看橱窗，不要编造；"
    "4) 不承诺退款、赔付、赠品；5) 不提及 AI，不加话题标签。"
)

DM_SYSTEM = (
    "你是一个中文短视频账号的客服，负责回复私信咨询。"
    "要求：1) 只输出回复正文，不要解释、不要引号；2) 60 字以内，口语化、真诚；"
    "3) 价格/库存/发货/尺码等无法确认的信息，只引导对方看橱窗或留下联系方式，不要编造；"
    "4) 不承诺退款、赔付、赠品；5) 不提及 AI，不加话题标签。"
)


def _clean_reply(text: str, max_len: int) -> str:
    """清洗 AI 回复：去引号、过禁词、按长度在标点处截断。"""
    if not text:
        return ""
    t = text.strip().strip('"“”\'‘’')
    t = _sanitize(t)
    t = _truncate_at_punct(t, max_len)
    return t


def draft(comment_text: str, work_title: str = "", account_name: str = "") -> str:
    """生成一条评论回复草稿。未配置 LLM 时抛 RuntimeError。"""
    if not client.is_configured():
        raise RuntimeError("AI 未配置（.env 里没有 LLM_API_KEY）")
    prompt = (f"账号：{account_name or '本账号'}\n"
              f"作品：{work_title or '（无标题）'}\n"
              f"观众评论：{comment_text}\n"
              "请给出一条回复。")
    text = client.chat(prompt, system=SYSTEM)
    return _clean_reply(text, 30)


def draft_dm(sender: str, content: str, account_name: str = "",
             history: list | None = None) -> str:
    """生成一条私信回复草稿。未配置 LLM 时抛 RuntimeError。"""
    if not client.is_configured():
        raise RuntimeError("AI 未配置（.env 里没有 LLM_API_KEY）")
    ctx = ""
    if history:
        lines = []
        for h in history[-10:]:
            who = "对方" if h.get("from_peer") else "我"
            text = str(h.get("content") or "").strip()
            if text:
                lines.append(f"{who}：{text}")
        if lines:
            ctx = "最近对话：\n" + "\n".join(lines) + "\n"
    prompt = (f"账号：{account_name or '本账号'}\n"
              f"{ctx}"
              f"「{sender or '对方'}」最新私信：{content}\n"
              "请给出一条回复。")
    text = client.chat(prompt, system=DM_SYSTEM)
    return _clean_reply(text, 60)
