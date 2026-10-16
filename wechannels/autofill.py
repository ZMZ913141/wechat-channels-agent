"""素材智能填写：根据视频文件名（+用户补充提示）用已配置的 LLM 生成建议字段。

输出：标题 / 短标题(6-16字) / 描述文案(含话题) / 话题标签 / AI标注建议。
- LLM 未配置或调用失败时，回退为纯本地规则（从文件名提取标题）。
- 使用 config 里现有的 LLM_PROVIDER/LLM_API_KEY/LLM_BASE_URL/LLM_MODEL。
"""
import json
import os
import random
import re

import requests

from . import config

SYSTEM_PROMPT = """你是短视频运营助手，为微信视频号素材生成发布信息。根据用户给出的视频文件名和补充提示，
输出严格的 JSON（不要 markdown 围栏、不要多余文字），字段：
{
  "title": "标题，<=30字，吸引人",
  "short_title": "短标题，6-16个字，用于搜索/话题场景",
  "desc": "发布文案，60-150字，口语化、有钩子，末尾自然带 3-5 个 #话题",
  "tags": "话题标签，逗号分隔，4-6个，不带#号",
  "ai_declare": "AI标注建议：如果内容大概率是AI生成/虚拟数字人/虚构剧情，填「内容为AI制作」或「内容为虚构剧情」，否则填空字符串"
}"""

USER_TMPL = "视频文件名：{name}\n补充提示：{hint}\n请输出 JSON。"


def _fallback(name: str, hint: str) -> dict:
    """无 LLM 时的本地兜底：清洗文件名作为标题。"""
    base = os.path.splitext(os.path.basename(name))[0]
    base = re.sub(r"[\d_\-\s]+$", "", base).strip() or "未命名素材"
    # 文件名太机器化（含大量英文+下划线）时不用作标题
    looks_machine = len(re.findall(r"[a-zA-Z_0-9]", base)) > len(base) * 0.6
    out = {
        "title": base[:30],
        "short_title": base[:16],
        "desc": base + " #干货分享",
        "tags": "干货分享",
        "ai_declare": "",
    }
    if hint:
        out["title"] = hint[:30]
        out["short_title"] = hint[:16]
        out["desc"] = hint + "\n" + out["desc"] if not looks_machine else hint + " #干货分享"
    elif looks_machine:
        out["title"] = "新素材｜" + base[:24]
    if "ai" in name.lower() or "ai" in hint.lower():
        out["ai_declare"] = "内容为AI制作"
    return out


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            import ast

            return ast.literal_eval(m.group(0))
        except Exception:
            return None


def autofill(name: str, hint: str = "", field: str = "") -> dict:
    """返回建议字段 dict（保证含 title/short_title/desc/tags/ai_declare 键）。

    field 为空=生成全部；指定某字段时，提示词聚焦该字段并加入随机扰动，
    便于用户反复点「生成」得到不同版本。
    """
    name = (name or "").strip()
    hint = (hint or "").strip()
    field = (field or "").strip()
    if not name and not hint:
        return _fallback("未命名素材", "")
    if not config.LLM_API_KEY:
        return _fallback(name, hint)

    provider = (config.LLM_PROVIDER or "").lower()
    focus = f"\n本次只需重点优化字段：{field}（其它字段可简略）。" if field else ""
    variation = f"\n随机种子：{random.randint(1, 99999)}，请换一个角度/措辞，避免与上次重复。"
    try:
        if provider in ("openai", "deepseek", "openai_compatible"):
            resp = requests.post(
                config.LLM_BASE_URL.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                json={
                    "model": config.LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_TMPL.format(name=name or "（无文件名）", hint=hint or "无") + focus + variation},
                    ],
                    "temperature": 1.0,
                    "max_tokens": 500,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        else:
            return _fallback(name, hint)
    except Exception as e:
        out = _fallback(name, hint)
        out["_note"] = f"AI 调用失败，已用本地规则：{str(e)[:80]}"
        return out

    data = _extract_json(content)
    if not isinstance(data, dict):
        return _fallback(name, hint)
    out = {
        "title": str(data.get("title") or "")[:40],
        "short_title": str(data.get("short_title") or "")[:16],
        "desc": str(data.get("desc") or ""),
        "tags": str(data.get("tags") or "").replace("#", "").strip(),
        "ai_declare": str(data.get("ai_declare") or "").strip(),
    }
    if hint:
        out["_hint_used"] = hint
    return out
