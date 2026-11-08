"""AI 智能填写：根据文件名和素材信息生成标题/描述/话题（按平台）。

未配置 LLM 时回退本地规则，不卡住流程。标题长度按平台收敛：
视频号短标题 7-15 字由发布适配器再校验，这里给通用上限。
"""
from pathlib import Path
from . import client
from ..browser.platforms._base import normalize_topics

PLATFORM_PROMPTS: dict[str, str] = {
    "wechat": (
        "你是微信视频号女装带货文案专家。请为下方视频生成发布文案，要求：\n"
        "1. 短标题 7-15 字，要有具体卖点（材质/版型/显瘦/显高/面料/价格/风格），禁用「绝绝子、爱了爱了、冲冲冲、太好看了」这类空词；\n"
        "2. 描述 100 字内，口语化，点明适合谁穿、什么场景穿，带一点行动号召；\n"
        "3. 话题 3-5 个，贴合视频内容与女装垂类，不要写泛泛的「女装」一个就完事。\n"
        '输出严格 JSON：{"title":"短标题","description":"描述","topics":["话题1","话题2"]}'
    ),
    "douyin": (
        "你是抖音女装短视频爆款文案专家。请为下方视频生成发布文案，要求：\n"
        "1. 标题 ≤30 字，钩子前置（痛点/反差/数字），让人想点开；\n"
        "2. 描述 200 字内，有信息量（版型/面料/显瘦效果/适合身材/穿搭思路），可适度加表情；\n"
        "3. 话题 4-6 个，混垂直词和流量词。\n"
        '输出严格 JSON：{"title":"标题","description":"描述","topics":["话题1","话题2"]}'
    ),
    "xiaohongshu": (
        "你是小红书女装种草笔记文案专家。请为下方视频生成发布文案，要求：\n"
        "1. 标题 ≤20 字，有记忆点（数字/痛点/对比）；\n"
        "2. 正文 300 字内，像真人分享：先说问题→给出答案→细节描述→适合人群，段落短；\n"
        "3. 话题 5-8 个，精准到面料/版型/风格/身材/场景。\n"
        '输出严格 JSON：{"title":"标题","description":"正文","topics":["话题1","话题2"]}'
    ),
}


def generate_copy(filename: str, extra_hint: str = "", platform: str = "wechat",
                  title_hint: str = "", description_hint: str = "",
                  transcript: str = "") -> dict:
    """返回 {short_title, description, topics, ai_used}。

    ai_used=True 表示真调了 LLM；False 表示走了本地兜底（没配 key / 调用失败）。
    platform 决定文案风格与长度。title_hint/description_hint 为素材已有信息，
    让 AI 在已有基础上优化而不是凭空重写。transcript 为用户粘贴的口播稿/文案，
    有值时以它为核心依据提炼卖点。
    """
    parts = [f"视频文件名：{filename}"]
    if title_hint:
        parts.append(f"已有标题：{title_hint}")
    if description_hint:
        parts.append(f"已有描述：{description_hint}")
    if transcript:
        parts.append(
            "视频文案/口播稿（这是这条视频的真实内容，请以它为核心提炼卖点，"
            "不要编造文案里没有的信息）：\n" + transcript[:2000]
        )
    if extra_hint:
        parts.append(f"补充要求：{extra_hint}")
    parts.append("只输出 JSON，不要解释、不要前后缀。")
    base = "\n".join(parts)
    sys = PLATFORM_PROMPTS.get(platform, PLATFORM_PROMPTS["wechat"])
    try:
        import json
        raw = client.chat(base, system=sys)
        data = _extract_json(raw)
        if not data:
            return _local_fallback(filename, platform, transcript)
        topics = data.get("topics", [])
        # LLM 可能返回 "#女装" 或中文逗号串，统一走发布链路的同一套归一化
        topics_str = ",".join(normalize_topics(",".join(topics) if isinstance(topics, list) else str(topics)))
        return {
            "short_title": data.get("title", ""),
            "description": data.get("description", ""),
            "topics": topics_str,
            "ai_used": True,
        }
    except Exception:
        return _local_fallback(filename, platform, transcript)


def _extract_json(text: str) -> dict | None:
    """从 LLM 返回里尽可能稳地抽出 JSON 对象。

    小模型常在 JSON 前后加废话或包 ```json 代码块，这里兼容这些情况。
    """
    import json
    if not text:
        return None
    t = text.strip()
    # 去掉常见代码块围栏
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
        t = t.strip()
    # 截取第一个 { 到最后一个 } 之间的内容
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e != -1 and e > s:
        t = t[s:e + 1]
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _local_fallback(filename: str, platform: str = "wechat", transcript: str = "") -> dict:
    """本地规则：从文件名（或用户粘贴的文案）抽词（未配置 LLM 时的兜底，非真 AI）。"""
    src = (transcript or "").strip().replace("\n", " ")
    name = Path(filename).stem.replace("_", " ").replace("-", " ")
    max_title = {"wechat": 12, "douyin": 28, "xiaohongshu": 18}.get(platform, 12)
    if src:
        # 有用户文案时优先用它开头，避免兜底标题和真实内容完全不搭
        title = src[:max_title]
        desc = src[:200]
    else:
        title = name[:max_title] if len(name) >= 7 else name + " 避坑指南"
        desc = f"做女装十年，{name}。点赞收藏不迷路。"
    return {
        "short_title": title,
        "description": desc,
        "topics": "女装,创业,实体店",
        "ai_used": False,
    }
