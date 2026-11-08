"""数据看板 API（已接入多用户）。"""
from fastapi import APIRouter, Depends
from sqlmodel import Session
from ...db import get_session
from ...models import User
from ...web.auth import get_current_user
from ...core import stats

router = APIRouter()


@router.get("")
def dashboard(session: Session = Depends(get_session),
             user: User = Depends(get_current_user)):
    return stats.dashboard(session, user.id)


@router.get("/trend")
def trend(days: int = 14, session: Session = Depends(get_session),
          user: User = Depends(get_current_user)):
    days = max(7, min(days, 30))
    return stats.trend(session, user.id, days=days)
