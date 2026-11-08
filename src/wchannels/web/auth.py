"""本地多用户鉴权：注册 / 登录 / 登出 + 会话 Cookie 依赖。

不引入 SSO 或第三方依赖，用 HMAC 签名 Cookie 实现会话；所有受保护路由通过
get_current_user 拿到当前用户，进而按 user_id 做行级租户隔离。
"""
import base64
import hashlib
import hmac
import json
import time
from fastapi import Request, HTTPException, Response
from sqlmodel import Session
from ..config import settings
from ..db import get_engine
from ..models import User


def _sign(payload: str) -> str:
    b = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(settings.SECRET_KEY.encode(), b.encode(), hashlib.sha256).hexdigest()
    return f"{b}.{sig}"


def _unsign(token: str) -> str | None:
    try:
        b, sig = token.rsplit(".", 1)
        exp = hmac.new(settings.SECRET_KEY.encode(), b.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(exp, sig):
            return base64.urlsafe_b64decode(b).decode()
    except Exception:
        pass
    return None


def create_session_token(user_id: int) -> str:
    payload = json.dumps({"uid": user_id, "exp": int(time.time()) + settings.SESSION_EXPIRE_DAYS * 86400})
    return _sign(payload)


def parse_session(token: str) -> int | None:
    raw = _unsign(token)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if data.get("exp", 0) < int(time.time()):
            return None
        return int(data["uid"])
    except Exception:
        return None


def get_current_user(request: Request) -> User:
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    uid = parse_session(token)
    if uid is None:
        raise HTTPException(status_code=401, detail="会话无效或已过期")
    with Session(get_engine()) as s:
        user = s.get(User, uid)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key="session",
        value=create_session_token(user_id),
        httponly=True,
        max_age=settings.SESSION_EXPIRE_DAYS * 86400,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie("session")
