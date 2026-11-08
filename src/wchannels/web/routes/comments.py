"""评论 API：列表、立即采集、AI 回复草稿、标记状态。"""
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ...core import comments
from ...db import get_session
from ...logutil import get_logger
from ...models import Comment, User
from ...timeutils import now
from ...web.auth import get_current_user

router = APIRouter()
log = get_logger("comments_api")
_running = False
STATUS = ("new", "drafted", "replied", "ignored")


@router.get("")
def list_comments(status: str = "", account_id: int | None = None, limit: int = 50,
                  session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    return {"items": comments.list_comments(session, user.id, status=status,
                                            account_id=account_id, limit=limit),
            "counts": comments.count_by_status(session, user.id)}


@router.post("/refresh")
def refresh(session: Session = Depends(get_session),
            user: User = Depends(get_current_user)):
    """后台采集所有账号的新评论（每个账号约 30~60 秒）。"""
    global _running
    if _running:
        return {"started": False, "message": "正在采集评论，请稍后刷新"}
    _running = True
    uid = user.id

    def _job():
        global _running
        from sqlmodel import Session as S
        from ...db import get_engine
        try:
            with S(get_engine()) as s:
                results = comments.collect_all(s, user_id=uid)
            for r in results:
                if r["error"]:
                    log.warning("评论采集 %s：%s", r["account_name"], r["error"])
        except Exception:
            log.exception("手动评论采集失败")
        finally:
            _running = False

    threading.Thread(target=_job, daemon=True).start()
    return {"started": True, "message": "已开始采集评论，约 1 分钟后刷新查看"}


class BatchStatusBody(BaseModel):
    ids: list[int]
    status: str


# 注意：/batch/* 必须注册在 /{cid}/* 之前，否则 "batch" 会被 {cid} 捕获变 422。
@router.post("/batch/status")
def batch_status(body: BatchStatusBody,
                 session: Session = Depends(get_session),
                 user: User = Depends(get_current_user)):
    """批量改状态（目前前端用于批量忽略）。"""
    if body.status not in STATUS:
        raise HTTPException(400, "状态只能是 new / drafted / replied / ignored")
    if not body.ids:
        raise HTTPException(400, "请先勾选评论")
    rows = [c for cid in body.ids[:500]
            if (c := session.get(Comment, cid)) and c.user_id == user.id]
    for c in rows:
        c.status = body.status
        if body.status == "replied" and not c.replied_at:
            c.replied_at = now()
        session.add(c)
    session.commit()
    return {"ok": True, "updated": len(rows)}


class BatchDraftBody(BaseModel):
    ids: list[int]


@router.post("/batch/draft")
def batch_draft(body: BatchDraftBody,
                session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    """批量生成 AI 回复草稿（每条一次 LLM 调用，上限 50 条防超时）。"""
    if not body.ids:
        raise HTTPException(400, "请先勾选评论")
    from ...ai import reply as ai_reply
    from ...models import Account
    rows = [c for cid in body.ids[:50]
            if (c := session.get(Comment, cid)) and c.user_id == user.id
            and c.status in ("new", "drafted")]
    names: dict[int, str] = {}
    done, failed = 0, 0
    for c in rows:
        if c.account_id not in names:
            acc = session.get(Account, c.account_id)
            names[c.account_id] = acc.name if acc else ""
        try:
            c.ai_draft = ai_reply.draft(c.content, c.work_title,
                                        names[c.account_id])
            c.status = "drafted"
            done += 1
        except Exception:
            failed += 1
        session.add(c)
    session.commit()
    return {"ok": True, "drafted": done, "failed": failed,
            "capped": len(body.ids) > 50}


class DraftBody(BaseModel):
    account_id: int | None = None   # 预留给「多账号同一评论」的场景


@router.post("/{cid}/draft")
def make_draft(cid: int, body: DraftBody | None = None,
               session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    c = session.get(Comment, cid)
    if not c or c.user_id != user.id:
        raise HTTPException(404, "评论不存在")
    from ...ai import reply as ai_reply
    from ...models import Account
    acc = session.get(Account, c.account_id)
    try:
        c.ai_draft = ai_reply.draft(c.content, c.work_title, acc.name if acc else "")
    except Exception as e:
        raise HTTPException(400, f"生成回复草稿失败：{e}")
    c.status = "drafted"
    session.add(c)
    session.commit()
    return {"ok": True, "ai_draft": c.ai_draft, "status": c.status}


class StatusBody(BaseModel):
    status: str


class SendBody(BaseModel):
    text: str


@router.post("/{cid}/send")
def send_reply(cid: int, body: SendBody,
               session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    """一键回复：直接把内容发到平台上对应的评论下（约 20~40 秒）。"""
    c = session.get(Comment, cid)
    if not c or c.user_id != user.id:
        raise HTTPException(404, "评论不存在")
    ok, err = comments.send_reply(session, cid, body.text, user_id=user.id)
    if not ok:
        raise HTTPException(400, err)
    return {"ok": True, "status": "replied"}


@router.post("/{cid}/status")
def set_status(cid: int, body: StatusBody,
               session: Session = Depends(get_session),
               user: User = Depends(get_current_user)):
    c = session.get(Comment, cid)
    if not c or c.user_id != user.id:
        raise HTTPException(404, "评论不存在")
    if body.status not in STATUS:
        raise HTTPException(400, "状态只能是 new / drafted / replied / ignored")
    c.status = body.status
    if body.status == "replied" and not c.replied_at:
        c.replied_at = now()
    session.add(c)
    session.commit()
    return {"ok": True, "status": c.status}
