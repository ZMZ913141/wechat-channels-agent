"""OpenAI 兼容 LLM 客户端（DeepSeek / OpenAI / 其他）。"""
import httpx
from ..config import settings


def chat(prompt: str, system: str = "", timeout: int = 60, retries: int = 3) -> str:
    """调一次 chat completions，返回文本。未配置 key 抛 RuntimeError。

    对网关瞬时抖动做有限重试（聚合路由偶发 5xx / 错误 JSON）。
    """
    if not settings.LLM_API_KEY:
        raise RuntimeError("未配置 LLM_API_KEY")
    url = f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.LLM_API_KEY}"}
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.7,
    }
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if "error" in data:  # 网关返回 200 但带 error 字段
                raise RuntimeError(str(data["error"])[:200])
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("LLM 调用失败")


def is_configured() -> bool:
    return bool(settings.LLM_API_KEY)
