"""私信 API：列表、立即采集、AI 回复草稿、一键发送（仅视频号）。"""
import threading
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ...core import messages
from ...db import get_session
from ...logutil import get_logger
from ...models import Account, PrivateMessage, User
from ...web.auth import get_current_user

router = APIRouter()
log = get_logger("messages_api")
_running = False


@router.get("")
def list_messages(account_id: int | None = None, only_unread: bool = False,
                  limit: int = 200,
                  session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    return {"items": messages.list_messages(session, user.id, account_id=account_id,
                                            only_unread=only_unread, limit=limit),
            "counts": messages.count_unread(session, user.id)}


@router.post("/refresh")
def refresh(session: Session = Depends(get_session),
            user: User = Depends(get_current_user)):
    """后台采集所有账号的新私信（每个账号约 30~60 秒）。"""
    global _running
    if _running:
        return {"started": False, "message": "正在采集私信，请稍后刷新"}
    _running = True
    uid = user.id

    def _job():
        global _running
        from sqlmodel import Session as S
        from ...db import get_engine
        try:
            with S(get_engine()) as s:
                results = messages.collect_all(s, user_id=uid)
            for r in results:
                if r["error"]:
                    log.warning("私信采集 %s：%s", r["account_name"], r["error"])
        except Exception:
            log.exception("手动私信采集失败")
        finally:
            _running = False

    threading.Thread(target=_job, daemon=True).start()
    return {"started": True, "message": "已开始采集私信，约 1 分钟后刷新查看"}


@router.post("/{mid}/draft")
def make_draft(mid: int,
               session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    """为一条私信生成 AI 回复草稿。"""
    m = session.get(PrivateMessage, mid)
    if not m or m.user_id != user.id:
        raise HTTPException(404, "私信不存在")
    from ...ai import reply as ai_reply
    acc = session.get(Account, m.account_id)
    try:
        history = json.loads(m.history) if m.history else None
    except Exception:
        history = None
    try:
        m.ai_draft = ai_reply.draft_dm(m.sender, m.content,
                                       acc.name if acc else "",
                                       history=history)
    except Exception as e:
        raise HTTPException(400, f"生成回复草稿失败：{e}")
    session.add(m)
    session.commit()
    return {"ok": True, "ai_draft": m.ai_draft}


class SendBody(BaseModel):
    text: str


@router.post("/{mid}/send")
def send(mid: int, body: SendBody,
         session: Session = Depends(get_session),
         user: User = Depends(get_current_user)):
    """一键发送：把回复直接发到平台私信会话（约 20~40 秒，仅视频号）。"""
    m = session.get(PrivateMessage, mid)
    if not m or m.user_id != user.id:
        raise HTTPException(404, "私信不存在")
    ok, err = messages.send_reply(session, mid, body.text, user_id=user.id)
    if not ok:
        raise HTTPException(400, err)
    return {"ok": True}
