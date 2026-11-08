"""自动化方案：AI 智能排期、一键批量发布（已接入多用户）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from datetime import datetime, timedelta
from ...db import get_session
from ...models import User
from ...web.auth import get_current_user
from ...core import materials as mat_svc, accounts as acc_svc, queue as queue_svc
from ...models import PublishTarget

router = APIRouter()


class SmartScheduleBody(BaseModel):
    material_ids: list[int]
    account_ids: list[int]
    # 黄金时段：默认 12:00、18:00、20:00
    golden_hours: list[int] = [12, 18, 20]


@router.post("/smart-schedule")
def smart_schedule(body: SmartScheduleBody, session: Session = Depends(get_session),
                  user: User = Depends(get_current_user)):
    """方案 B：AI 智能排期。选 N 个 ready 素材 × M 个账号，分配到未来 3 天黄金时段，错峰发布。"""
    now = datetime.now().astimezone()  # 本地时区（带 tz），保证黄金时段是用户本地时间
    slots = []
    for day in range(3):
        day_date = now + timedelta(days=day)
        for h in body.golden_hours:
            slot = day_date.replace(hour=h, minute=0, second=0, microsecond=0)
            if slot > now:
                slots.append(slot)

    try:
        batch = queue_svc.create_batch(session, user.id, body.material_ids, body.account_ids)
    except queue_svc.OwnershipError as e:
        raise HTTPException(400, str(e))
    targets = list(session.exec(select(PublishTarget).where(PublishTarget.batch_id == batch.id)).all())
    for i, t in enumerate(targets):
        if i < len(slots):
            t.scheduled_at = slots[i]
        else:
            t.scheduled_at = slots[-1] + timedelta(minutes=30 * (i - len(slots) + 1))
        session.add(t)
    session.commit()
    return {"batch_id": batch.id, "slots_used": len(targets),
            "first_slot": targets[0].scheduled_at if targets else None}


class AutoPublishBody(BaseModel):
    material_ids: list[int]
    # 可选：指定账号；不指定则自动选当前用户所有 active 账号
    account_ids: list[int] | None = None


@router.post("/auto-publish")
def auto_publish(body: AutoPublishBody, session: Session = Depends(get_session),
                user: User = Depends(get_current_user)):
    """方案 C：一键智能发布。选素材，自动选所有可用账号，自动错峰排期。"""
    if body.account_ids:
        account_ids = body.account_ids
    else:
        accs = acc_svc.list_accounts(session, user.id)
        account_ids = [a.id for a in accs if a.status == "active"]
    if not account_ids:
        raise HTTPException(400, "没有可用账号，请先添加账号并登录")
    try:
        b = queue_svc.create_batch(session, user.id, body.material_ids, account_ids)
    except queue_svc.OwnershipError as e:
        raise HTTPException(400, str(e))
    return {"batch_id": b.id, "accounts": len(account_ids), "materials": len(body.material_ids)}
