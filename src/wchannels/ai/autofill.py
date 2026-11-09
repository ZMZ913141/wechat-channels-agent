"""AI 智能填写：根据文件名和素材信息生成标题/描述/话题（按平台）。

质量防线（确保不发出烂文案）：
1. 禁词过滤：口水词删除，广告法高风险词替换为温和表达；
2. 长度收敛：标题/描述按平台上限截断，且在标点处断开不截半句；
3. 空字段回退：标题为空时用文件名兜底；
4. JSON 解析失败重试一次（附「只输出 JSON」提示）；
5. 校验不通过重试一次（附上具体问题让 AI 重写）。
未配置 LLM 时回退本地规则，不卡住流程。
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

# 各平台标题/描述上限（与 prompt 一致，最后一道防线）
PLATFORM_LIMITS: dict[str, dict[str, int]] = {
    "wechat": {"title": 15, "desc": 100},
    "douyin": {"title": 30, "desc": 200},
    "xiaohongshu": {"title": 20, "desc": 300},
}

# 空泛口水词 → 直接删除（这些词没有信息量）
FILLER_WORDS = [
    "绝绝子", "爱了爱了", "冲冲冲", "太好看了", "yyds", "YYDS",
    "绝了", "天花板", "封神", "炸了", "疯了", "yyds！", "绝绝",
    "姐妹们冲", "家人们谁懂",
]

# 广告法/平台高风险词 → 替换为温和表达（避免夸大宣传违规）
# 注意：长词必须排在短词前面，否则「全网最低价」会被「全网最低」先匹配，
# 剩下一个「价」字。这里按 key 长度倒序遍历替换。
ADLAW_REPLACEMENTS: dict[str, str] = {
    "全网最低价": "性价比很高",
    "全网最低": "性价比很高",
    "最低价": "实惠价",
    "最便宜": "很实惠",
    "国家级": "专业",
    "唯一": "少有的",
    "顶级": "优质",
    "极品": "精品",
    "完美": "很好",
    "100%": "高",
    "百分之百": "高",
    "最棒": "很棒",
    "最强": "很强",
    "最好": "很好",
    "最佳": "优选",
    "绝对": "非常",
    "永久": "长久",
}
_ADLAW_ORDERED = sorted(ADLAW_REPLACEMENTS.items(), key=lambda kv: -len(kv[0]))


def _sanitize(text: str) -> str:
    """删除口水词 + 替换广告法高风险词，再合并多余空白。"""
    if not text:
        return ""
    t = text
    for w in FILLER_WORDS:
        t = t.replace(w, "")
    for k, v in _ADLAW_ORDERED:
        t = t.replace(k, v)
    # 合并连续空白
    return " ".join(t.split())


def _truncate_at_punct(text: str, max_len: int) -> str:
    """截断到 max_len 字，尽量在标点处断开，避免截半句。"""
    s = (text or "").strip()
    if not s or len(s) <= max_len:
        return s
    s = s[:max_len]
    # 回退到最后一个句末标点（至少保留一半内容才回退）
    best = -1
    for p in "。！？!?；;":
        idx = s.rfind(p)
        if idx > best:
            best = idx
    if best >= max_len // 2:
        return s[:best + 1]
    # 没有句末标点，尝试在逗号处断开
    for p in "，,":
        idx = s.rfind(p)
        if idx >= max_len // 2:
            return s[:idx + 1]
    return s


def _validate_copy(data: dict, filename: str, platform: str,
                   transcript: str) -> tuple[dict, bool, str]:
    """校验并清洗 AI 输出。返回 (结果dict, 是否合格, 不合格原因)。

    不合格原因供重试时反馈给 AI。
    """
    limits = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["wechat"])
    title = _sanitize(str(data.get("title", "")).strip())
    desc = _sanitize(str(data.get("description", "")).strip())
    topics_raw = data.get("topics", [])

    issue = ""
    if not title:
        issue = "标题为空"
    elif len(title) < 4:
        issue = f"标题过短（{len(title)}字），信息量不足"
    if len(title) > limits["title"]:
        title = _truncate_at_punct(title, limits["title"])
    if len(desc) > limits["desc"]:
        desc = _truncate_at_punct(desc, limits["desc"])

    # 话题归一化（与发布链路同一套）
    if isinstance(topics_raw, list):
        topics_joined = ",".join(str(t) for t in topics_raw)
    else:
        topics_joined = str(topics_raw)
    topics_norm = normalize_topics(topics_joined)
    topics_str = ",".join(topics_norm)

    # 标题为空兜底：用文件名或口播稿开头
    if not title:
        src = (transcript or "").strip().replace("\n", " ")
        name = Path(filename).stem.replace("_", " ").replace("-", " ")
        title = (src or name)[:limits["title"]]

    result = {
        "short_title": title,
        "description": desc,
        "topics": topics_str,
        "ai_used": True,
    }
    return result, (not issue), issue


def generate_copy(filename: str, extra_hint: str = "", platform: str = "wechat",
                  title_hint: str = "", description_hint: str = "",
                  transcript: str = "", examples: list[dict] | None = None) -> dict:
    """返回 {short_title, description, topics, ai_used, ai_draft}。

    ai_used=True 表示真调了 LLM；False 表示走了本地兜底（没配 key / 调用失败）。
    examples：该平台近期已发布的文案样例（含用户编辑过的），用于 few-shot 学风格。
    ai_draft：本次 AI 输出的原始文案 JSON（存库，下一轮判断用户是否编辑过）。
    """
    import json as _json
    examples = examples or []
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
    if examples:
        ex_lines = []
        for i, ex in enumerate(examples[:3], 1):
            ex_lines.append(
                f"{i}) 标题：{ex.get('title', '')}\n"
                f"描述：{ex.get('description', '')}\n"
                f"话题：{ex.get('topics', '')}"
            )
        parts.append(
            "以下是该平台近期已发布、经人工确认的文案样例，请保持类似的卖点结构和"
            "语言风格（不要照抄，要贴合本条视频内容）：\n" + "\n\n".join(ex_lines)
        )
    if extra_hint:
        parts.append(f"补充要求：{extra_hint}")
    parts.append("只输出 JSON，不要解释、不要前后缀。")
    base = "\n".join(parts)
    sys = PLATFORM_PROMPTS.get(platform, PLATFORM_PROMPTS["wechat"])

    def _call(hint: str = "") -> dict | None:
        prompt = base if not hint else base + f"\n{hint}"
        try:
            raw = client.chat(prompt, system=sys, json_mode=True)
        except Exception:
            return None
        return _extract_json(raw)

    data = _call()
    if data is None:
        # JSON 解析失败：重试一次，强调只输出 JSON
        data = _call("注意：你上一次输出不是合法 JSON。请严格只输出 {\"title\":...,\"description\":...,\"topics\":[...]} 这一个对象，不要任何其他文字。")

    if data is None:
        return _local_fallback(filename, platform, transcript)

    result, ok, issue = _validate_copy(data, filename, platform, transcript)
    if not ok:
        # 质量不达标：重试一次，把具体问题告诉 AI
        data2 = _call(f"上一版不合格：{issue}。请重新生成，确保标题有具体卖点、不使用空泛口水词。")
        if data2 is not None:
            result2, _, _ = _validate_copy(data2, filename, platform, transcript)
            if len(result2["short_title"]) > len(result["short_title"]):
                result = result2
                data = data2
    # 存本次 AI 输出的原始文案（清洗后），用于下一轮判断用户是否编辑过
    result["ai_draft"] = _json.dumps(
        {"title": result["short_title"], "description": result["description"],
         "topics": result["topics"]},
        ensure_ascii=False,
    )
    return result


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
        title = _truncate_at_punct(src, max_title)
        desc = _truncate_at_punct(src, 200)
    else:
        title = name[:max_title] if len(name) >= 7 else name + " 穿搭分享"
        desc = f"做女装多年，{name}。点赞收藏不迷路。"
    return {
        "short_title": title,
        "description": desc,
        "topics": "女装,穿搭,显瘦",
        "ai_used": False,
        "ai_draft": "",
    }
