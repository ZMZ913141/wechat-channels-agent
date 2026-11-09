"""账号管理 API（已接入多用户，按 user_id 隔离）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from pydantic import BaseModel
from ...db import get_session
from ...models import User
from ...web.auth import get_current_user
from ...core import accounts as svc
from ...config import settings
from ...browser.loginprobe import probe_login, wait_login
from ...browser.launcher import launch_context
from ...browser.proclock import AccountLock, ProfileLock, LockBusy
from ...browser.platforms import get_publisher

router = APIRouter()


@router.get("")
def list_accounts(session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    accounts = svc.list_accounts(session, user.id)
    for a in accounts:
        svc.refresh_daily_count(session, a)  # 跨天即时归零，界面不再显示昨天的计数
    return [_serialize(a) for a in accounts]


class CreateBody(BaseModel):
    name: str
    group: str = ""
    platform: str = "wechat"


@router.post("")
def create_account(body: CreateBody, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    a = svc.create_account(session, user.id, body.name, body.group, body.platform)
    return _serialize(a)


@router.delete("/{aid}")
def delete_account(aid: int, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    a = svc.get_account(session, aid, user.id)
    if not a:
        raise HTTPException(404, "账号不存在")
    svc.delete_account(session, a)
    return {"ok": True}


class UpdateBody(BaseModel):
    name: str | None = None
    group: str | None = None


@router.patch("/{aid}")
def update_account(aid: int, body: UpdateBody,
                   session: Session = Depends(get_session),
                   user: User = Depends(get_current_user)):
    """改名 / 改分组（只动工作台里的展示信息，不影响平台账号）。"""
    a = svc.get_account(session, aid, user.id)
    if not a:
        raise HTTPException(404, "账号不存在")
    if body.name is not None and body.name.strip():
        a.name = body.name.strip()[:40]
    if body.group is not None:
        a.group = body.group.strip()[:20]
    session.add(a)
    session.commit()
    return _serialize(a)


@router.post("/{aid}/login-probe")
def login_probe(aid: int, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    a = svc.get_account(session, aid, user.id)
    if not a:
        raise HTTPException(404, "账号不存在")
    profile = settings.profiles_dir / a.profile_dir
    # 与发布/巡检共用同一把档案锁：账号正在发布时不能同时打开该档案
    a_lock, p_lock = AccountLock(a.id), ProfileLock(profile)
    try:
        a_lock.acquire()
        p_lock.acquire()
    except LockBusy:
        return {"nickname": "", "logged_in": None, "busy": True,
                "platform": a.platform}
    try:
        logged_in, nick = probe_login(profile, a.platform)
        svc.detect_login(session, a, nick, logged_in)
    finally:
        a_lock.release()
        p_lock.release()
    return {"nickname": nick, "logged_in": logged_in, "platform": a.platform}


@router.post("/{aid}/login")
def login_account(aid: int, session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    """交互式登录：打开可见浏览器，等用户扫码，最长 150s。返回登录昵称。"""
    a = svc.get_account(session, aid, user.id)
    if not a:
        raise HTTPException(404, "账号不存在")
    if a.user_id != user.id:
        raise HTTPException(404, "账号不存在")
    profile = settings.profiles_dir / a.profile_dir
    logged_in, nick = wait_login(profile, a.platform)
    svc.detect_login(session, a, nick, logged_in)
    return {"nickname": nick, "logged_in": logged_in, "platform": a.platform}


@router.get("/{aid}/collections")
def fetch_collections(aid: int, session: Session = Depends(get_session),
                      user: User = Depends(get_current_user)):
    """用该账号的登录态抓取平台已有合集列表。登录失效或改版时返回空列表。"""
    a = svc.get_account(session, aid, user.id)
    if not a:
        raise HTTPException(404, "账号不存在")
    profile = settings.profiles_dir / a.profile_dir
    pub = get_publisher(a.platform)
    try:
        with launch_context(profile, headless=True) as ctx:
            items = pub.fetch_collections(ctx)
    except Exception:
        items = []
    return {"collections": items}


def _serialize(a) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "group": a.group,
        "platform": a.platform,
        "nickname_detected": a.nickname_detected,
        "status": a.status,
        "last_login_at": a.last_login_at.isoformat() if a.last_login_at else None,
        "last_publish_at": a.last_publish_at.isoformat() if a.last_publish_at else None,
        "daily_publish_count": a.daily_publish_count,
        "daily_limit": svc.daily_limit_of(a),
        "default_topics": a.default_topics,
        "default_comment": a.default_comment,
    }
