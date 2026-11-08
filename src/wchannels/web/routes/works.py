"""作品数据 API：列表 + 手动刷新（刷新在后台线程跑，接口立即返回）。"""
import threading

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ...core import workstats
from ...db import get_session
from ...logutil import get_logger
from ...models import User
from ...web.auth import get_current_user

router = APIRouter()
log = get_logger("works")
_running = False


@router.get("")
def list_works(limit: int = 30, session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    return {"items": workstats.recent_works(session, user.id, limit=limit)}


@router.post("/refresh")
def refresh(session: Session = Depends(get_session),
            user: User = Depends(get_current_user)):
    """立即采集一次（后台线程，接口立刻返回；通常每个账号 20~40 秒）。"""
    global _running
    if _running:
        return {"started": False, "message": "正在采集，请稍后刷新"}
    _running = True
    uid = user.id

    def _job():
        global _running
        from ...db import get_engine
        from sqlmodel import Session as S
        try:
            with S(get_engine()) as s:
                workstats.collect_all(s, user_id=uid)
        except Exception:
            log.exception("手动数据采集失败")
        finally:
            _running = False

    threading.Thread(target=_job, daemon=True).start()
    return {"started": True, "message": "已开始采集，约 20~40 秒后刷新看数据"}
