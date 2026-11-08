"""鉴权路由：注册 / 登录 / 登出 / 当前用户。"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import Session, select
from ...db import get_session
from ...models import User
from ..auth import get_current_user, set_session_cookie, clear_session_cookie
from ...ai import client as ai_client

router = APIRouter()


class RegisterBody(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/register")
def register(body: RegisterBody, response: Response, session: Session = Depends(get_session)):
    if session.exec(select(User).where(User.username == body.username)).first():
        raise HTTPException(400, "用户名已存在")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    u = User(
        username=body.username,
        password_hash=User.hash_password(body.password),
        display_name=body.display_name or body.username,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    set_session_cookie(response, u.id)
    return {"id": u.id, "username": u.username, "display_name": u.display_name}


@router.post("/login")
def login(body: LoginBody, response: Response, session: Session = Depends(get_session)):
    u = session.exec(select(User).where(User.username == body.username)).first()
    if not u or not u.verify_password(body.password):
        raise HTTPException(401, "用户名或密码错误")
    set_session_cookie(response, u.id)
    return {"id": u.id, "username": u.username, "display_name": u.display_name}


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "ai_enabled": ai_client.is_configured(),
    }
