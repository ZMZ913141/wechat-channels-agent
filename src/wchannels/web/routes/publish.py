"""发布 API：创建批次、查看队列、手动执行（已接入多用户 + 平台分发）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from ...db import get_session
from ...models import PublishTarget, TargetStatus, Material, Account, User, AuditSource
from ...web.auth import get_current_user
from ...core import queue as svc
from ...core import accounts as acc_svc
from ...core import audit as audit_svc
from ...config import settings
from ...browser.launcher import launch_context
from ...browser.platforms import publish_for_account
from ...browser.proclock import AccountLock, ProfileLock, LockBusy

router = APIRouter()


class PublishBody(BaseModel):
    material_ids: list[int]
    account_ids: list[int]
    stagger_min: int | None = None


@router.post("")
def create_publish(body: PublishBody, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    try:
        b = svc.create_batch(session, user.id, body.material_ids, body.account_ids, body.stagger_min)
    except svc.OwnershipError as e:
        raise HTTPException(400, str(e))
    return {"batch_id": b.id, "status": b.status}


@router.get("/queue")
def queue(status: str | None = None, page: int = 1, size: int = 30,
          session: Session = Depends(get_session),
          user: User = Depends(get_current_user)):
    # 历史任务会越积越多，这里分页返回，避免一次性把几百上千条塞给前端
    from sqlalchemy import func
    cond = Account.user_id == user.id
    if status:
        cond = cond & (PublishTarget.status == status)
    total = session.exec(
        select(func.count()).select_from(PublishTarget)
        .join(Account, PublishTarget.account_id == Account.id).where(cond)
    ).one()
    targets = session.exec(
        select(PublishTarget)
        .join(Account, PublishTarget.account_id == Account.id)
        .where(cond)
        .order_by(PublishTarget.scheduled_at.desc())
        .offset((page - 1) * size).limit(size)
    ).all()
    out = []
    for t in targets:
        m = session.get(Material, t.material_id)
        a = session.get(Account, t.account_id)
        out.append({
            "id": t.id,
            "material": m.title if m else "（素材已删除）",
            "account": a.name if a else "",
            "platform": a.platform if a else "",
            "status": t.status,
            "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None,
            "result_message": t.result_message,
        })
    return {"items": out, "total": int(total), "page": page, "size": size}


@router.post("/targets/{tid}/run")
def run_target(tid: int, session: Session = Depends(get_session),
              user: User = Depends(get_current_user)):
    """手动执行一个到点的目标（实际浏览器操作）。"""
    import time
    t = session.get(PublishTarget, tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    a = session.get(Account, t.account_id)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "任务不存在")
    m = session.get(Material, t.material_id)
    if not m or not a:
        raise HTTPException(400, "素材或账号不存在")

    # 素材已移入回收站/彻底删除时不允许再发
    if m.status == "trash":
        svc.mark_failed(session, t, "跳过：素材已删除（在回收站）")
        return {"success": False, "reason": "素材已删除（在回收站），不能发布"}

    # 防重复发布：已成功的再点一次就是发第二条；running 说明调度器正在发。
    # 账号锁只能挡「同时」，挡不住「事后重放」，所以状态校验必须在这里做。
    if t.status == TargetStatus.SUCCESS.value:
        return {"success": False, "reason": "该任务已发布成功，不能重复执行"}
    if t.status == TargetStatus.RUNNING.value:
        return {"success": False, "reason": "该任务正在发布中，请稍候"}

    ok, reason = acc_svc.can_publish(session, a)
    if not ok:
        svc.mark_failed(session, t, reason)
        audit_svc.record(
            session, user_id=user.id, account_id=a.id, material_id=m.id,
            platform=a.platform, ok=False, message=reason,
            source=AuditSource.SKIPPED.value, target_id=t.id, batch_id=t.batch_id,
        )
        return {"success": False, "reason": reason}

    profile = settings.profiles_dir / a.profile_dir
    a_lock = AccountLock(a.id)
    p_lock = ProfileLock(profile)
    started = time.monotonic()
    try:
        a_lock.acquire()
        p_lock.acquire()
        svc.mark_running(session, t)
        with launch_context(profile, headless=True) as ctx:
            ok, msg, shot = publish_for_account(
                ctx, a,
                video_path=m.file_path,
                short_title=t.short_title or m.short_title,
                description=t.description or m.description,
                topics=t.topics or m.topics,
                ai_disclosure=t.ai_disclosure or m.ai_disclosure,
                collection=t.collection or m.collection,
                cover_path=t.cover_path or m.cover_path,
                original_work=bool(t.original_work),
                visibility=t.visibility or "public",
                # 注意：这里刻意不传 scheduled_at。到点即发由本项目调度器负责，
                # 若再把 scheduled_at 交给平台，等于让平台二次延迟发布。
            )
        cost = int((time.monotonic() - started) * 1000)
        if ok:
            svc.mark_success(session, t, a, m)
        else:
            svc.mark_failed(session, t, msg, shot)
        audit_svc.record(
            session, user_id=user.id, account_id=a.id, material_id=m.id,
            platform=a.platform, ok=ok, message=msg,
            source=AuditSource.MANUAL.value,
            target_id=t.id, batch_id=t.batch_id, screenshot=shot, duration_ms=cost,
        )
        return {"success": ok, "reason": msg}
    except LockBusy:
        # 撞锁不判失败：状态可能已被 mark_running 改写，回退成 queued 让调度器稍后重试
        t.status = TargetStatus.QUEUED.value
        session.add(t)
        session.commit()
        return {"success": False, "reason": "该账号正被其他发布占用，已退回排队，稍后自动重试"}
    except Exception as e:
        svc.mark_failed(session, t, str(e))
        audit_svc.record(
            session, user_id=user.id, account_id=a.id, material_id=m.id,
            platform=a.platform, ok=False, message=f"异常：{e}",
            source=AuditSource.MANUAL.value,
            target_id=t.id, batch_id=t.batch_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return {"success": False, "reason": str(e)}
    finally:
        a_lock.release()
        p_lock.release()
