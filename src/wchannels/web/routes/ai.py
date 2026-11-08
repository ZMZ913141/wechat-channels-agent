"""AI 配置自检 + 界面化 AI 供应商配置。"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session
from ...db import get_session
from ...models import User
from ..auth import get_current_user
from ...ai import client as ai_client
from ...ai import autofill
from ...config import settings

router = APIRouter()

# 界面可选的云端供应商预设（都是 OpenAI 兼容协议）
PROVIDERS = {
    "deepseek":  {"name": "DeepSeek",   "base_url": "https://api.deepseek.com/v1",                 "model": "deepseek-chat"},
    "moonshot":  {"name": "Kimi",       "base_url": "https://api.moonshot.cn/v1",                  "model": "moonshot-v1-8k"},
    "dashscope": {"name": "通义千问",    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "zhipu":     {"name": "智谱 GLM",    "base_url": "https://open.bigmodel.cn/api/paas/v4",        "model": "glm-4-flash"},
    "custom":    {"name": "自定义（OpenAI 兼容）", "base_url": "", "model": ""},
}

ENV_PATH = Path(__file__).resolve().parents[4] / ".env"


def _write_env(updates: dict[str, str]) -> None:
    """把配置写回 .env（保留其他行），并同步到运行中的 settings。"""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    for key, val in updates.items():
        setattr(settings, key, val)


@router.get("/status")
def ai_status(user: User = Depends(get_current_user)):
    return {"enabled": ai_client.is_configured()}


@router.post("/test")
def ai_test(user: User = Depends(get_current_user)):
    """用一次真实 LLM 调用验证 key 是否有效、能否返回可解析的文案。"""
    if not ai_client.is_configured():
        return {"ok": False, "reason": "未配置 LLM_API_KEY（在 .env 里填 DeepSeek/OpenAI key 后重启）"}
    try:
        copy = autofill.generate_copy("女装_秋季新款.mp4", "温柔风", platform="wechat")
        if not copy.get("ai_used"):
            return {"ok": False, "reason": "调用失败（key 无效 / 网络不通 / 返回无法解析）"}
        return {
            "ok": True,
            "sample": {
                "short_title": copy["short_title"],
                "description": copy["description"],
                "topics": copy["topics"],
            },
        }
    except Exception as e:
        return {"ok": False, "reason": f"调用异常：{e}"}


def _current_provider() -> str:
    for key, p in PROVIDERS.items():
        if key != "custom" and p["base_url"] == settings.LLM_BASE_URL:
            return key
    return "custom"


@router.get("/config")
def ai_config(user: User = Depends(get_current_user)):
    return {
        "provider": _current_provider(),
        "base_url": settings.LLM_BASE_URL,
        "model": settings.LLM_MODEL,
        "has_key": bool(settings.LLM_API_KEY),
        "presets": [{"key": k, **p} for k, p in PROVIDERS.items()],
    }


@router.get("/models")
def ai_models(user: User = Depends(get_current_user)):
    """从当前接口地址拉可用模型列表（OpenAI 兼容 /models）。"""
    import json as _json
    import urllib.request
    if not settings.LLM_API_KEY:
        return {"models": [], "reason": "未配置 API Key"}
    try:
        req = urllib.request.Request(
            f"{settings.LLM_BASE_URL.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json.loads(r.read())
        ids = sorted(m["id"] for m in data.get("data", []) if m.get("id"))
        return {"models": ids[:200]}
    except Exception as e:
        return {"models": [], "reason": f"拉取失败：{e}"}


class AiConfigIn(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""        # 留空 = 不改动现有 key
    base_url: str = ""
    model: str = ""


@router.post("/config")
def ai_config_save(body: AiConfigIn, user: User = Depends(get_current_user)):
    preset = PROVIDERS.get(body.provider)
    if not preset:
        raise HTTPException(400, "未知供应商")
    updates = {
        "LLM_BASE_URL": (body.base_url or preset["base_url"]).strip(),
        "LLM_MODEL": (body.model or preset["model"]).strip(),
    }
    if not updates["LLM_BASE_URL"] or not updates["LLM_MODEL"]:
        raise HTTPException(400, "自定义供应商必须填接口地址和模型名")
    if body.api_key.strip():
        updates["LLM_API_KEY"] = body.api_key.strip()
    elif not settings.LLM_API_KEY:
        raise HTTPException(400, "还没配置过 API Key，请填写")
    _write_env(updates)
    return {"ok": True, "provider": _current_provider(),
            "base_url": settings.LLM_BASE_URL, "model": settings.LLM_MODEL,
            "has_key": bool(settings.LLM_API_KEY)}
